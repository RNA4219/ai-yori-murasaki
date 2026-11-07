"""Read-only product HTTP server for ai-yori-murasaki monitor.

Exposes a fixed, allowlisted projection of the Runtime/Store state over
loopback HTTP.  No raw ledger, CAS object, filesystem path, token, prompt or
log content is ever served.  All responses are bounded and carry strict
security headers.
"""
from __future__ import annotations

import hashlib
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from .jsonio import DataError, canonical, loads
from .runtime import Runtime
from .storage import Store
from . import lab_metrics

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_HTML_BYTES = 256 * 1024
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SEQ_RE = re.compile(r"^[0-9]{1,19}$")
LIMIT_RE = re.compile(r"^[0-9]{1,4}$")

ALLOWED_HOSTS = ("127.0.0.1", "localhost")

# Fixed endpoint patterns.  No arbitrary path segments are accepted.
ROUTES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("index", re.compile(r"^/$")),
    ("health", re.compile(r"^/api/health$")),
    ("campaigns", re.compile(r"^/api/campaigns$")),
    ("campaign", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})$")),
    ("campaign_runs", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})/runs$")),
    ("campaign_rsi", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})/rsi$")),
    ("campaign_assessment", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})/assessment$")),
    ("run", re.compile(r"^/api/runs/([a-z0-9][a-z0-9-]{0,63})$")),
    ("run_events", re.compile(r"^/api/runs/([a-z0-9][a-z0-9-]{0,63})/events$")),
    ("verification", re.compile(r"^/api/verification/([a-z0-9][a-z0-9-]{0,63})$")),
    ("approvals", re.compile(r"^/api/approvals$")),
    ("models", re.compile(r"^/api/models$")),
    ("adapters", re.compile(r"^/api/adapters$")),
)

_AGENT_RECORD_TYPES = ("agent_turn", "agent_tool", "agent_tool_denied",
                       "agent_stopped", "agent_hypothesis", "patch_drafted",
                       "remediation_drafted")


# --------------------------------------------------------------------------- #
# projection helpers
# --------------------------------------------------------------------------- #
_SAFE_EVENT_FIELDS = ("event_id", "sequence", "timestamp", "phase", "role",
                      "event_type", "source", "finding", "title", "severity")


def _safe_str(value: Any, limit: int = 256) -> str | None:
    if not isinstance(value, str):
        return None
    if len(value) > limit:
        return value[:limit]
    return value


def _safe_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def project_event(event: dict, view: str = "operator") -> dict:
    """Return an allowlisted projection of a single ledger event.

    Only the fields in ``_SAFE_EVENT_FIELDS`` plus ``evidence_count`` are
    emitted.  ``extensions``, ``evidence_refs``, ``target``, ``actor`` and
    any other nested structure are dropped.  In ``blue`` view the finding
    title is redacted and no finding identifiers or counts are exposed.
    """
    if not isinstance(event, dict):
        return {}
    out: dict[str, Any] = {}
    for key in _SAFE_EVENT_FIELDS:
        if key in ("finding", "title", "severity"):
            continue
        value = event.get(key)
        if key in ("sequence",):
            iv = _safe_int(value)
            if iv is not None:
                out[key] = iv
        else:
            sv = _safe_str(value)
            if sv is not None:
                out[key] = sv
    finding = event.get("finding")
    if isinstance(finding, dict):
        title = _safe_str(finding.get("title"))
        severity = _safe_str(finding.get("severity"))
        if view == "blue":
            out["title"] = "[redacted]"
            if severity is not None:
                out["severity"] = severity
        else:
            if title is not None:
                out["title"] = title
            if severity is not None:
                out["severity"] = severity
    refs = event.get("evidence_refs")
    if view == "blue":
        out["evidence_count"] = 0
    elif isinstance(refs, list):
        out["evidence_count"] = len(refs)
    else:
        out["evidence_count"] = 0
    extensions = event.get("extensions")
    if isinstance(extensions, dict):
        record_type = extensions.get("record_type")
        if isinstance(record_type, str) and record_type in _AGENT_RECORD_TYPES:
            agent: dict[str, Any] = {}
            for key in ("record_type", "model", "tool", "llm_status",
                        "generated_by"):
                sv = _safe_str(extensions.get(key), 128)
                if sv is not None:
                    agent[key] = sv
            turn = _safe_int(extensions.get("turn"))
            if turn is not None:
                agent["turn"] = turn
            for key in ("parse_error", "unit_passed"):
                value = extensions.get(key)
                if isinstance(value, bool):
                    agent[key] = value
            if view != "blue":
                for key in ("reason", "category", "path", "role"):
                    sv = _safe_str(extensions.get(key), 128)
                    if sv is not None:
                        agent[key] = sv
            out["agent"] = agent
    return out


def _project_run(run: dict, store=None) -> dict:
    last_sequence = _safe_int(run.get("last_sequence"))
    last_event_hash = _safe_str(run.get("last_event_hash"))
    sealed_at = _safe_str(run.get("sealed_at"))
    if store is not None:
        run_id = run.get("run_id")
        chain = store.verify_chain(run_id)
        if chain.get("result") == "PASS":
            last_sequence = _safe_int(chain.get("last_sequence"))
            last_event_hash = _safe_str(chain.get("last_event_hash"))
        events = store.read_events(run_id) if chain.get("result") == "PASS" else []
        if events and events[-1].get("event_type") == "chain_sealed":
            sealed_at = _safe_str(events[-1].get("timestamp"))
        else:
            sealed_at = None
    return {
        "run_id": _safe_str(run.get("run_id")),
        "campaign_id": _safe_str(run.get("campaign_id")),
        "phase": _safe_str(run.get("phase")),
        "status": _safe_str(run.get("status")),
        "completion": _safe_str(run.get("completion")),
        "cause": _safe_str(run.get("cause")),
        "started_at": _safe_str(run.get("started_at")),
        "ended_at": _safe_str(run.get("ended_at")),
        "sealed_at": sealed_at,
        "last_sequence": last_sequence,
        "last_event_hash": last_event_hash,
    }


def _project_campaign(campaign: dict, runtime=None) -> dict:
    last_run_at = _safe_str(campaign.get("last_run_at"))
    finding_count = _safe_int(campaign.get("finding_count"))
    if runtime is not None:
        cid = campaign.get("campaign_id")
        runs = runtime.list_runs(cid)
        started = [r.get("started_at") for r in runs if r.get("started_at")]
        last_run_at = _safe_str(max(started)) if started else None
        finding_count = len(set(runtime.list_records(cid, "finding")))
    return {
        "campaign_id": _safe_str(campaign.get("campaign_id")),
        "intent_id": _safe_str(campaign.get("intent_id")),
        "status": _safe_str(campaign.get("status")),
        "created_at": _safe_str(campaign.get("created_at")),
        "last_run_at": last_run_at,
        "finding_count": finding_count,
    }


def _project_lab_metrics(runtime: Runtime, cid: str) -> dict | None:
    """Return aggregated lab metrics for a campaign, or None if absent.

    Scans every actual run of the campaign, reads its events, and selects
    the latest ``lab_measurement`` state_capture event by event timestamp
    across runs.  A run is only eligible when both chain and checkpoint
    verification PASS.  The referenced measurement object is then handed
    to ``lab_metrics.measure`` which independently re-verifies all runs
    and CAS integrity.  Only the aggregate dict is returned; source refs,
    raw objects and expected manifests are never exposed.
    """
    store = runtime.store
    try:
        runs = runtime.list_runs(cid)
    except DataError:
        return None
    latest: tuple[str, dict] | None = None
    for run in runs:
        rid = run.get("run_id")
        if not isinstance(rid, str):
            continue
        try:
            chain = store.verify_chain(rid)
        except DataError:
            return {"status": "HOLD", "reason_code": "evidence_unverified",
                    "metrics": None, "limitations": []}
        if chain.get("result") != "PASS":
            return {"status": "HOLD", "reason_code": "evidence_unverified",
                    "metrics": None, "limitations": []}
        try:
            events = store.read_events(rid)
        except DataError:
            return {"status": "HOLD", "reason_code": "evidence_unverified",
                    "metrics": None, "limitations": []}
        for event in events:
            if not isinstance(event, dict):
                continue
            if event.get("event_type") != "state_captured":
                continue
            extensions = event.get("extensions")
            if not isinstance(extensions, dict):
                continue
            if extensions.get("record_type") != "lab_measurement":
                continue
            if store.verify_checkpoint(cid, rid).get("result") != "PASS":
                return {"status": "HOLD", "reason_code": "evidence_unverified",
                        "metrics": None, "limitations": []}
            ts = event.get("timestamp")
            if not isinstance(ts, str):
                continue
            if latest is None or ts > latest[0]:
                latest = (ts, event)
    if latest is None:
        return None
    event = latest[1]
    refs = event.get("evidence_refs")
    if not isinstance(refs, list) or not refs or not isinstance(refs[0], str):
        return {"status": "HOLD", "reason_code": "evidence_unverified",
                "metrics": None, "limitations": []}
    try:
        measurement = store.get_object(refs[0])
    except (DataError, FileNotFoundError):
        return {"status": "HOLD", "reason_code": "evidence_unverified",
                "metrics": None, "limitations": []}
    if not isinstance(measurement, dict):
        return {"status": "HOLD", "reason_code": "evidence_unverified",
                "metrics": None, "limitations": []}
    try:
        result = lab_metrics.measure(runtime, cid, measurement)
    except DataError:
        return {"status": "HOLD", "reason_code": "evidence_unverified",
                "metrics": None, "limitations": []}
    if not isinstance(result, dict):
        return {"status": "HOLD", "reason_code": "evidence_unverified",
                "metrics": None, "limitations": []}
    status = result.get("status")
    if status == "HOLD":
        reason = _safe_str(result.get("reason_code"), "evidence_unverified")
        return {"status": "HOLD", "reason_code": reason,
                "metrics": None, "limitations": []}
    metrics = result.get("metrics")
    limitations = result.get("limitations")
    if not isinstance(limitations, list):
        limitations = []
    return {"status": status, "metrics": metrics, "limitations": limitations}


def _project_approval(approval: dict) -> dict:
    applied = approval.get("applied")
    return {
        "approval_id": _safe_str(approval.get("approval_id")),
        "remediation_id": _safe_str(approval.get("remediation_id")),
        "decision": _safe_str(approval.get("decision")),
        "decided_at": _safe_str(approval.get("decided_at")),
        "expires_at": _safe_str(approval.get("expires_at")),
        "applied": applied if isinstance(applied, bool) else None,
    }


# --------------------------------------------------------------------------- #
# workspace status files
# --------------------------------------------------------------------------- #
def _read_workspace_json(store: Store, relative: str) -> dict | None:
    """Read a workspace-relative JSON file, returning None if absent/invalid."""
    try:
        path = store.workspace / relative
        if not path.exists() or path.is_symlink():
            return None
        raw = path.read_bytes()
        if len(raw) > MAX_RESPONSE_BYTES:
            return None
        value = loads(raw)
        if not isinstance(value, dict):
            return None
        return value
    except (DataError, OSError):
        return None


def _project_models(store: Store) -> list[dict]:
    data = _read_workspace_json(store, "model-status.json")
    if data is None:
        return []
    models = data.get("models")
    if not isinstance(models, list):
        return []
    out: list[dict] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        reg = item.get("registration") if isinstance(item.get("registration"), dict) else {}
        dev = item.get("development_call") if isinstance(item.get("development_call"), dict) else {}
        prod = item.get("product_connection") if isinstance(item.get("product_connection"), dict) else {}
        pin = item.get("pin") if isinstance(item.get("pin"), dict) else {}
        out.append({
            "role": _safe_str(item.get("role")),
            "requested_role_model": _safe_str(item.get("requested_role_model")),
            "registration": {
                "status": _safe_str(reg.get("status")) or "unknown",
                "checked_at": _safe_str(reg.get("checked_at")),
                "evidence_ref": _safe_str(reg.get("evidence_ref")),
            },
            "development_call": {
                "status": _safe_str(dev.get("status")) or "unknown",
                "evidence_refs": [r for r in (dev.get("evidence_refs") or []) if isinstance(r, str)][:16],
            },
            "product_connection": {
                "status": _safe_str(prod.get("status")) or "unknown",
                "checked_at": _safe_str(prod.get("checked_at")),
                "evidence_ref": _safe_str(prod.get("evidence_ref")),
            },
            "pin": {
                "status": _safe_str(pin.get("status")) or "unknown",
                "version": _safe_str(pin.get("version")),
            },
        })
    return out


def _project_adapters(store: Store) -> list[dict]:
    data = _read_workspace_json(store, "adapter-status.json")
    if data is None:
        return []
    adapters = data.get("adapters")
    if not isinstance(adapters, list):
        return []
    out: list[dict] = []
    for item in adapters:
        if not isinstance(item, dict):
            continue
        status = _safe_str(item.get("status")) or "not_tested"
        # Contract tests never promote an adapter to live.
        if status == "live":
            status = "not_tested"
        out.append({
            "name": _safe_str(item.get("name")),
            "status": status,
            "mode": _safe_str(item.get("mode")),
            "checked_at": _safe_str(item.get("checked_at")),
        })
    return out


def _project_rsi(store: Store, campaign_id: str) -> list[dict]:
    from ai_yori_murasaki.rsi import RSI
    from .rsi_compare import _validate_comparison, _evaluate_pairs

    data = _read_workspace_json(store, "rsi/state.json")
    if data is None:
        return []
    candidates = data.get("candidates")
    if not isinstance(candidates, dict):
        return []
    out: list[dict] = []
    for item in candidates.values():
        if not isinstance(item, dict):
            continue
        if item.get("campaign_id") != campaign_id:
            continue
        freeze_id = _safe_str(item.get("freeze_id"))
        if freeze_id is None:
            continue
        try:
            freeze = RSI(store).load_freeze(freeze_id)
        except (DataError, Exception):
            continue
        if not isinstance(freeze, dict):
            continue
        status = _safe_str(item.get("status"))
        if status in ("ready", "current"):
            continue
        comparison = None
        comparison_ref = item.get("comparison_ref")
        if isinstance(comparison_ref, str):
            try:
                raw_cmp = store.get_object(comparison_ref)
                if raw_cmp is not None:
                    validated = _validate_comparison(raw_cmp, freeze)
                    evaluated = _evaluate_pairs(validated["pairs"], freeze)
                    comparison = {
                        "pairs": _safe_int(evaluated.get("pairs")),
                        "gain": float(evaluated["gain"]) if isinstance(evaluated.get("gain"), (int, float)) and not isinstance(evaluated.get("gain"), bool) else None,
                        "baseline": _safe_str(freeze.get("baseline_ref")),
                        "candidate": _safe_str(freeze.get("candidate_ref")),
                    }
            except (DataError, Exception):
                comparison = None
        applied = None
        stored_applied = item.get("applied")
        if isinstance(stored_applied, bool):
            if stored_applied is False:
                applied = False
            else:
                registry = _read_workspace_json(store, "rsi/registry.json")
                if registry is not None:
                    current = registry.get("current")
                    applied = isinstance(current, str) and current == freeze.get("candidate_ref")
        out.append({
            "candidate_id": _safe_str(item.get("candidate_id")),
            "freeze_id": freeze_id,
            "content_digest": _safe_str(item.get("content_digest")),
            "status": status,
            "applied": applied,
            "comparison": comparison,
        })
    return out


def _project_agent_rsi(store: Store, campaign_id: str) -> list[dict]:
    """Project agent-RSI candidates (policy-bundle improvement) safely.

    Reads the agent RSI state file only; digests and summaries are
    reported as recorded, never recomputed into claims of improvement.
    """
    data = _read_workspace_json(store, "agent_rsi/state.json")
    if data is None:
        return []
    candidates = data.get("candidates")
    if not isinstance(candidates, dict):
        return []
    out: list[dict] = []
    for item in candidates.values():
        if not isinstance(item, dict):
            continue
        if item.get("campaign_id") != campaign_id:
            continue
        summary = item.get("summary")
        summary_out = None
        if isinstance(summary, dict):
            gain = summary.get("gain")
            summary_out = {
                "pairs": _safe_int(summary.get("pairs")),
                "gain": float(gain) if isinstance(gain, (int, float))
                and not isinstance(gain, bool) else None,
                "holdout_baseline": _safe_int(
                    summary.get("holdout_baseline")),
                "holdout_candidate": _safe_int(
                    summary.get("holdout_candidate")),
                "validation_baseline": _safe_int(
                    summary.get("validation_baseline")),
                "validation_candidate": _safe_int(
                    summary.get("validation_candidate")),
                "invalid_pairs": _safe_int(summary.get("invalid_pairs")),
                "losses": _safe_int(summary.get("losses")),
                "complete": summary.get("complete") is True,
                "benign_ok": summary.get("benign_ok") is True,
                "model_pinned": summary.get("model_pinned") is True,
                "exploration_grade": summary.get("exploration_grade") is True,
                "ready": summary.get("ready") is True,
            }
        rules = item.get("rules_applied")
        rules_out = None
        if isinstance(rules, list):
            rules_out = [_safe_str(r, 64) for r in rules[:16]]
        out.append({
            "candidate_id": _safe_str(item.get("candidate_id")),
            "freeze_id": _safe_str(item.get("freeze_id")),
            "bundle_digest": _safe_str(item.get("bundle_digest")),
            "baseline_digest": _safe_str(item.get("baseline_digest")),
            "rules_applied": rules_out,
            "compared": item.get("compared") is True,
            "applied": item.get("applied") is True,
            "summary": summary_out,
        })
    return out


# --------------------------------------------------------------------------- #
# verification journal
# --------------------------------------------------------------------------- #
def _read_verification(store: Store, run_id: str) -> list[dict]:
    """Read verification journal entries for a run, strictly parsed."""
    try:
        path = store.workspace / "verification" / (run_id + ".jsonl")
        if not path.exists() or path.is_symlink():
            return []
        raw = path.read_bytes()
        if len(raw) > MAX_RESPONSE_BYTES:
            return []
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        if not line.strip():
            continue
        try:
            value = loads(line)
        except DataError:
            continue
        if not isinstance(value, dict):
            continue
        out.append({
            "verify_id": _safe_str(value.get("verify_id")),
            "run_id": _safe_str(value.get("run_id")),
            "result": _safe_str(value.get("result")),
            "reason_code": _safe_str(value.get("reason_code")),
            "checked_at": _safe_str(value.get("checked_at")),
        })
    return out


# --------------------------------------------------------------------------- #
# request handling
# --------------------------------------------------------------------------- #
def _json_response(status: int, payload: dict) -> tuple[int, dict, bytes]:
    body = canonical(payload)
    if len(body) > MAX_RESPONSE_BYTES:
        body = canonical({"error": "response_too_large"})
        status = 500
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Content-Length": str(len(body)),
    }
    return status, headers, body


def _error(status: int, code: str) -> tuple[int, dict, bytes]:
    return _json_response(status, {"error": code})


def _parse_query(raw_query: str) -> dict[str, str] | None:
    if not raw_query:
        return {}
    pairs = parse_qsl(raw_query, keep_blank_values=True, strict_parsing=False)
    out: dict[str, str] = {}
    for key, value in pairs:
        if key in out:
            return None
        out[key] = value
    return out


def _match_route(path: str) -> tuple[str, tuple[str, ...]] | None:
    for name, pattern in ROUTES:
        m = pattern.match(path)
        if m:
            return name, m.groups()
    return None


def _check_host(host_header: str | None, port: int) -> bool:
    if not isinstance(host_header, str):
        return False
    host, _, port_str = host_header.partition(":")
    if host not in ALLOWED_HOSTS:
        return False
    if port_str:
        try:
            if int(port_str) != port:
                return False
        except ValueError:
            return False
    return True


def _check_origin(origin: str | None, port: int) -> bool:
    if origin is None:
        return True
    allowed = {f"http://127.0.0.1:{port}", f"http://localhost:{port}"}
    return origin in allowed


def _load_index_html() -> bytes:
    try:
        data = resources.files("ai_yori_murasaki").joinpath("web/index.html").read_bytes()
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return b""
    if len(data) > MAX_HTML_BYTES:
        return b""
    return data


def _normalize_csp_source(body: str) -> str:
    """Normalize inline source to the browser's CSP hash input.

    Browsers normalize CRLF and lone CR to LF before hashing inline
    script/style bodies.  Apply the same normalization so the emitted
    hash matches what the browser computes.
    """
    return body.replace("\r\n", "\n").replace("\r", "\n")


def _inline_script_hashes(html: bytes) -> list[str]:
    import base64
    hashes: list[str] = []
    text = html.decode("utf-8", errors="replace")
    for m in re.finditer(r"<script[^>]*>(.*?)</script>", text, re.DOTALL | re.IGNORECASE):
        body = m.group(1)
        if not body.strip():
            continue
        normalized = _normalize_csp_source(body)
        digest = hashlib.sha256(normalized.encode("utf-8")).digest()
        hashes.append("sha256-" + base64.b64encode(digest).decode("ascii"))
    return hashes


def _inline_style_hashes(html: bytes) -> list[str]:
    import base64
    hashes: list[str] = []
    text = html.decode("utf-8", errors="replace")
    for m in re.finditer(r"<style[^>]*>(.*?)</style>", text, re.DOTALL | re.IGNORECASE):
        body = m.group(1)
        if not body.strip():
            continue
        normalized = _normalize_csp_source(body)
        digest = hashlib.sha256(normalized.encode("utf-8")).digest()
        hashes.append("sha256-" + base64.b64encode(digest).decode("ascii"))
    return hashes


def _security_headers(html: bytes | None = None) -> dict[str, str]:
    script_hashes = _inline_script_hashes(html) if html else []
    style_hashes = _inline_style_hashes(html) if html else []
    script_src = "'self'"
    if script_hashes:
        script_src += " " + " ".join("'" + h + "'" for h in script_hashes)
    style_src = "'self'"
    if style_hashes:
        style_src += " " + " ".join("'" + h + "'" for h in style_hashes)
    csp = (
        "default-src 'none'; "
        f"script-src {script_src}; "
        f"style-src {style_src}; "
        "img-src 'self' data:; "
        "connect-src 'self'; "
        "frame-ancestors 'none'; "
        "base-uri 'none'; "
        "form-action 'none'"
    )
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": csp,
        "X-Frame-Options": "DENY",
        "Referrer-Policy": "no-referrer",
    }


def _handle(runtime: Runtime, method: str, path: str, query: dict[str, str],
            headers: dict[str, str], port: int) -> tuple[int, dict, bytes]:
    if method not in ("GET", "HEAD"):
        return _error(405, "method_not_allowed")
    if not _check_host(headers.get("Host"), port):
        return _error(400, "bad_host")
    if not _check_origin(headers.get("Origin"), port):
        return _error(403, "bad_origin")
    if "?" in path or "#" in path:
        return _error(400, "bad_path")
    if "%2f" in path.lower() or "%5c" in path.lower():
        return _error(400, "bad_path")
    if ".." in path:
        return _error(400, "bad_path")
    matched = _match_route(path)
    if matched is None:
        return _error(404, "not_found")
    name, groups = matched
    store = runtime.store
    view = query.get("view", "operator")
    if view not in ("operator", "blue"):
        return _error(400, "bad_view")

    try:
        if name == "index":
            html = _load_index_html()
            if not html:
                return _error(500, "index_unavailable")
            hdrs = _security_headers(html)
            hdrs["Content-Type"] = "text/html; charset=utf-8"
            hdrs["Content-Length"] = str(len(html))
            return 200, hdrs, html

        if name == "health":
            return _json_response(200, {"status": "ok"})

        if name == "campaigns":
            campaigns = [_project_campaign(c, runtime) for c in runtime.list_campaigns()]
            return _json_response(200, {"campaigns": campaigns})

        if name == "campaign_assessment":
            cid = groups[0]
            if view == "blue":
                return _json_response(200, {
                    "report": {
                        "status": "withheld",
                        "reason": "評価情報はBlueに非公開",
                    },
                })
            from .assessment import load_report
            try:
                report = load_report(runtime, cid)
            except DataError:
                return _json_response(200, {
                    "report": None,
                    "status": "hold",
                    "reason": "評価記録を確認できません",
                })
            except Exception:
                return _json_response(200, {
                    "report": None,
                    "status": "hold",
                    "reason": "評価記録を確認できません",
                })
            if report is None:
                return _error(404, "not_found")
            return _json_response(200, {"report": report})

        if name == "campaign":
            cid = groups[0]
            if view == "blue":
                return _json_response(200, {
                    "campaign": {
                        "status": "withheld",
                        "reason": "評価情報はBlueに非公開",
                    },
                })
            try:
                campaign = runtime.get_campaign(cid)
            except DataError:
                return _error(404, "not_found")
            findings = []
            seen_finding_ids = set()
            for ref in runtime.list_records(cid, "finding"):
                try:
                    obj = store.get_object(ref)
                except (DataError, FileNotFoundError):
                    continue
                fid = _safe_str(obj.get("finding_id"))
                if fid in seen_finding_ids:
                    continue
                seen_finding_ids.add(fid)
                findings.append({
                    "finding_id": fid,
                    "severity": _safe_str(obj.get("severity")),
                    "title": _safe_str(obj.get("title")),
                    "source": _safe_str(obj.get("source")),
                })
            remediations = []
            for ref in runtime.list_records(cid, "remediation"):
                try:
                    obj = store.get_object(ref)
                except (DataError, FileNotFoundError):
                    continue
                remediations.append({
                    "remediation_id": _safe_str(obj.get("remediation_id")),
                    "status": _safe_str(obj.get("status")),
                })
            return _json_response(200, {
                "campaign": _project_campaign(campaign, runtime),
                "findings": findings,
                "remediations": remediations,
                "metrics": _project_lab_metrics(runtime, cid),
            })

        if name == "campaign_runs":
            cid = groups[0]
            try:
                runtime.get_campaign(cid)
            except DataError:
                return _error(404, "not_found")
            runs = [_project_run(r, store) for r in runtime.list_runs(cid)]
            return _json_response(200, {"runs": runs})

        if name == "campaign_rsi":
            cid = groups[0]
            try:
                runtime.get_campaign(cid)
            except DataError:
                return _error(404, "not_found")
            return _json_response(200, {
                "rsi": _project_rsi(store, cid),
                "agent_rsi": _project_agent_rsi(store, cid),
            })

        if name == "run":
            rid = groups[0]
            try:
                run = runtime.get_run(rid)
            except DataError:
                return _error(404, "not_found")
            return _json_response(200, {"run": _project_run(run, store)})

        if name == "run_events":
            rid = groups[0]
            # Query validation precedes existence lookup so malformed
            # pagination is rejected with 400 regardless of run presence.
            after_raw = query.get("after", "0")
            limit_raw = query.get("limit", str(DEFAULT_LIMIT))
            if not SEQ_RE.match(after_raw):
                return _error(400, "bad_after")
            if not LIMIT_RE.match(limit_raw):
                return _error(400, "bad_limit")
            after = int(after_raw)
            limit = int(limit_raw)
            if limit < 1 or limit > MAX_LIMIT:
                return _error(400, "bad_limit")
            try:
                runtime.get_run(rid)
            except DataError:
                return _error(404, "not_found")
            try:
                events = store.read_events(rid)
            except DataError:
                return _error(500, "ledger_unreadable")
            selected = [e for e in events if isinstance(e.get("sequence"), int) and e["sequence"] > after]
            selected = selected[:limit]
            projected = [project_event(e, view) for e in selected]
            return _json_response(200, {"events": projected})

        if name == "verification":
            rid = groups[0]
            try:
                runtime.get_run(rid)
            except DataError:
                return _error(404, "not_found")
            return _json_response(200, {"verification": _read_verification(store, rid)})

        if name == "approvals":
            approvals: list[dict] = []
            for campaign in runtime.list_campaigns():
                cid = campaign.get("campaign_id")
                if not isinstance(cid, str):
                    continue
                for ref in runtime.list_records(cid, "approval"):
                    try:
                        obj = store.get_object(ref)
                    except (DataError, FileNotFoundError):
                        continue
                    approvals.append(_project_approval(obj))
            return _json_response(200, {"approvals": approvals})

        if name == "models":
            return _json_response(200, {"models": _project_models(store)})

        if name == "adapters":
            return _json_response(200, {"adapters": _project_adapters(store)})
    except DataError:
        return _error(500, "internal_error")
    except Exception:  # noqa: BLE001 - never leak exception data
        return _error(500, "internal_error")
    return _error(404, "not_found")


def handle_request(runtime: Runtime, method: str, path: str,
                   query: dict[str, str] | None = None,
                   headers: dict[str, str] | None = None,
                   port: int = 8765) -> tuple[int, dict, bytes]:
    """Test-friendly request handler.

    ``path`` may include a query string; if ``query`` is provided it is used
    as-is.  ``headers`` keys are matched case-insensitively for Host/Origin.
    """
    if query is None:
        if "?" in path:
            path, _, raw_query = path.partition("?")
            parsed = _parse_query(raw_query)
            if parsed is None:
                return _error(400, "duplicate_query")
            query = parsed
        else:
            query = {}
    if headers is None:
        headers = {}
    norm_headers: dict[str, str] = {}
    for key, value in headers.items():
        if isinstance(key, str) and isinstance(value, str):
            norm_headers[key.lower()] = value
    host = norm_headers.get("host")
    origin = norm_headers.get("origin")
    if host is None:
        host = f"127.0.0.1:{port}"
    return _handle(runtime, method, path, query,
                   {"Host": host, "Origin": origin} if origin is not None else {"Host": host},
                   port)


# --------------------------------------------------------------------------- #
# HTTP server
# --------------------------------------------------------------------------- #
class _Handler(BaseHTTPRequestHandler):
    server_version = "aym-monitor/1.0"
    sys_version = ""

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        return

    def _dispatch(self, method: str) -> None:
        runtime: Runtime = self.server.runtime  # type: ignore[attr-defined]
        port: int = self.server.server_address[1]  # type: ignore[attr-defined]
        split = urlsplit(self.path)
        path = split.path
        query = _parse_query(split.query)
        if query is None:
            status, headers, body = _error(400, "duplicate_query")
        else:
            req_headers = {
                "Host": self.headers.get("Host", ""),
                "Origin": self.headers.get("Origin"),
            }
            status, headers, body = _handle(runtime, method, path, query,
                                            req_headers, port)
        # Merge security headers with route headers so each header is
        # emitted exactly once.  Route-specific headers (e.g. the HTML CSP
        # with inline hashes) take precedence over the default CSP.
        merged = _security_headers()
        merged.update(headers)
        self.send_response(status)
        for key, value in merged.items():
            self.send_header(key, value)
        self.end_headers()
        if method != "HEAD" and body:
            self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        self._dispatch("GET")

    def do_HEAD(self) -> None:  # noqa: N802
        self._dispatch("HEAD")

    def do_POST(self) -> None:  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self) -> None:  # noqa: N802
        self._dispatch("PUT")

    def do_DELETE(self) -> None:  # noqa: N802
        self._dispatch("DELETE")

    def do_PATCH(self) -> None:  # noqa: N802
        self._dispatch("PATCH")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._dispatch("OPTIONS")


class _Server(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr: tuple[str, int], runtime: Runtime) -> None:
        self.runtime = runtime
        super().__init__(addr, _Handler)


def _parse_bind(bind: str) -> tuple[str, int]:
    if not isinstance(bind, str) or not bind:
        raise DataError("invalid bind", "input_invalid")
    host, sep, port_str = bind.rpartition(":")
    if not sep or not host:
        raise DataError("invalid bind", "input_invalid")
    if host != "127.0.0.1":
        raise DataError("bind must be loopback 127.0.0.1", "bind_not_loopback")
    try:
        port = int(port_str)
    except ValueError as exc:
        raise DataError("invalid bind port", "input_invalid") from exc
    if port < 0 or port > 65535:
        raise DataError("invalid bind port", "input_invalid")
    return host, port


def create_server(runtime: Runtime, bind: str = "127.0.0.1:8765") -> ThreadingHTTPServer:
    host, port = _parse_bind(bind)
    return _Server((host, port), runtime)


def serve(bind: str = "127.0.0.1:8765", runtime: Runtime | None = None) -> None:
    if runtime is None:
        runtime = Runtime(Store.from_env())
    server = create_server(runtime, bind)
    try:
        server.serve_forever()
    finally:
        server.server_close()
