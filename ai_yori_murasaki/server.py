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
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib import resources
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import parse_qsl, urlencode, urlsplit

from .jsonio import DataError, canonical, loads
from .runtime import Runtime
from .monitor_summary import CampaignSummary, campaign_summaries
from .monitor_detail import IndexUpdateRequired, read_selection
from .storage import Store
from . import lab_metrics
from . import monitor_auth

if TYPE_CHECKING:
    from .google_sso import GoogleSSO

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
    ("intake", re.compile(r"^/intake$")),
    ("receipts", re.compile(r"^/api/intake$")),
    ("decision", re.compile(r"^/decision$")),
    ("architecture", re.compile(r"^/architecture$")),
    ("architecture_asset", re.compile(r"^/architecture/(map\.mjs|model\.mjs|js-yaml\.mjs|sample\.json|product\.json)$")),
    ("architecture_maps", re.compile(r"^/api/architecture/maps\Z")),
    ("architecture_map", re.compile(r"^/api/architecture/maps/([a-z0-9][a-z0-9-]{0,63})\Z")),
    ("health", re.compile(r"^/api/health$")),
    ("sources", re.compile(r"^/api/sources$")),
    ("source", re.compile(r"^/api/sources/(src-[0-9a-f]{32})$")),
    ("campaigns", re.compile(r"^/api/campaigns$")),
    ("campaign_relations", re.compile(r"^/api/campaign-relations$")),
    ("campaign", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})$")),
    ("campaign_runs", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})/runs$")),
    ("campaign_rsi", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})/rsi$")),
    ("campaign_assessment", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})/assessment$")),
    ("campaign_decision_view", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})/decision-view$")),
    ("campaign_drift", re.compile(r"^/api/campaigns/([a-z0-9][a-z0-9-]{0,63})/drift$")),
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


def _project_run(run: dict, store=None, *, verified_events=None) -> dict:
    last_sequence = _safe_int(run.get("last_sequence"))
    last_event_hash = _safe_str(run.get("last_event_hash"))
    sealed_at = _safe_str(run.get("sealed_at"))
    if verified_events is not None:
        last = verified_events[-1] if verified_events else {}
        last_sequence = _safe_int(last.get("sequence"))
        last_event_hash = _safe_str(last.get("event_hash"))
        sealed_at = _safe_str(last.get("timestamp")) if last.get("event_type") == "chain_sealed" else None
    elif store is not None:
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


def _project_campaign(cid: str, summary: CampaignSummary, view: str = "operator") -> dict:
    campaign = summary.campaign
    return {
        "campaign_id": cid,
        "intent_id": _safe_str(campaign.get("intent_id")),
        "status": _safe_str(campaign.get("status")),
        "created_at": _safe_str(campaign.get("created_at")),
        "last_run_at": _safe_str(summary.last_run_at) or None,
        "finding_count": len(summary.findings) if summary.status != "unavailable" else None,
        "finding_count_status": summary.status,
        "verified_run_count": summary.verified_runs,
        "unverified_run_count": len(summary.pending_runs),
        **({"unverified_run_ids": summary.pending_runs, "severity_counts": summary.severity_counts()}
           if view == "operator" else {}),
    }


def _project_finding(obj: dict) -> dict:
    return {key: _safe_str(obj.get(key)) for key in ("finding_id", "severity", "title", "source")}


def _project_lab_metrics(runtime, cid: str, *, verified_events=None) -> dict | None:
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
    if verified_events is None:
        try:
            run_ids = [run.get("run_id") for run in runtime.list_runs(cid)]
        except DataError:
            return None
    else:
        run_ids = verified_events.keys()
    latest: tuple[str, dict] | None = None
    for rid in run_ids:
        if not isinstance(rid, str):
            continue
        if verified_events is not None:
            events = verified_events[rid]
        else:
            try:
                chain = store.verify_chain(rid)
                if chain.get("result") != "PASS":
                    raise DataError("unverified")
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
    if verified_events is not None and {r["run_id"] for r in runtime.list_runs(cid)} != set(verified_events):
        return {"status": "HOLD", "reason_code": "evidence_unverified", "metrics": None, "limitations": []}
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
        reason = _safe_str(result.get("reason_code")) or "evidence_unverified"
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


def _drift_response(report: dict) -> tuple[int, dict, bytes]:
    # 複数の CAS オブジェクトから作るレポートは、応答上限に達した時点で符号化を止める。
    encoder = json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    body = bytearray()
    for chunk in encoder.iterencode(report):
        if len(chunk) > MAX_RESPONSE_BYTES - len(body):
            return _error(500, "response_too_large")
        data = chunk.encode("utf-8")
        if len(data) > MAX_RESPONSE_BYTES - len(body):
            return _error(500, "response_too_large")
        body.extend(data)
    return 200, {"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(body))}, bytes(body)


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


def _matches_authority(value: str | None, base: str, port: int, scheme: str) -> bool:
    default_port = 443 if scheme == "https" else 80
    return value == f"{base}:{port}" or (port == default_port and value == base)


def _load_page(filename: str, *, blue: bool = False) -> bytes:
    try:
        web = resources.files("ai_yori_murasaki").joinpath("web")
        data = web.joinpath(filename).read_bytes()
        theme = web.joinpath("workspace.css").read_bytes()
        data = data.replace(b"<!-- workspace-style -->", b"<style>\n" + theme + b"\n</style>")
        if blue:
            # The server-rendered links must keep the current view before any script runs.
            markup, scripts, tail = data.partition(b"<script")
            for route in (b"/", b"/decision", b"/architecture", b"/intake"):
                prefix = b'href="' + route
                markup = markup.replace(prefix + b"?", prefix + b"?view=blue&amp;")
                for end in (b'"', b"#"):
                    markup = markup.replace(prefix + end, prefix + b"?view=blue" + end)
            data = markup + scripts + tail
    except (FileNotFoundError, ModuleNotFoundError, OSError):
        return b""
    return data if len(data) <= MAX_HTML_BYTES else b""


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


def _security_headers(html: bytes | None = None, *, logout_form: bool = False) -> dict[str, str]:
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
        + ("form-action 'self'" if logout_form else "form-action 'none'")
    )
    return {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": csp,
        "X-Frame-Options": "DENY",
        # ログアウトフォームは同一オリジンの POST に Origin が必要。
        "Referrer-Policy": "same-origin" if logout_form else "no-referrer",
    }


def _html_response(status: int, html: bytes, *, logout_form: bool = False) -> tuple[int, dict, bytes]:
    headers = _security_headers(html, logout_form=logout_form)
    headers.update({"Content-Type": "text/html; charset=utf-8", "Content-Length": str(len(html))})
    return status, headers, html


def _redirect(location: str, *cookies: str) -> tuple[int, dict, bytes]:
    headers = {"Location": location, "Content-Length": "0"}
    if cookies:
        headers["Set-Cookie"] = cookies
    return 303, headers, b""


def _auth_gate(auth: monitor_auth.MonitorAuth, method: str, path: str, query: dict[str, str],
               headers: dict, port: int, read_csrf: Callable[[], str | None] | None
               ) -> tuple[int, dict, bytes] | str:
    """認証済みならセッション、それ以外は公開可能な応答のみを返す。"""
    from .google_sso import GoogleLoginError, PENDING_TTL, SESSION_TTL
    from .monitor_auth_ui import render_auth_page

    canonical_host = f"127.0.0.1:{port}"
    origin = "https://" + canonical_host
    canonical_request = _matches_authority(headers["Host"], "127.0.0.1", port, "https")
    session_name = f"aym_session_{port}"
    pending_name = f"aym_login_{port}"
    jar = headers.get("Cookie", "") if canonical_request else ""
    session = monitor_auth.read_cookie(jar, session_name)
    if path == "/auth/google/start":
        if method != "GET":
            return _error(405, "method_not_allowed")
        target = monitor_auth.return_target(query.get("next", "/"))
        if not canonical_request:
            return _redirect(origin + monitor_auth.login_path(target))
        try:
            url, correlation = auth.provider.begin(port, target)
        except GoogleLoginError:
            return _html_response(503, render_auth_page("unavailable", monitor_auth.login_path(target)))
        return _redirect(url, monitor_auth.cookie(pending_name, correlation, "/auth/google", PENDING_TTL))
    if path == "/auth/google/callback":
        if method != "GET":
            return _error(405, "method_not_allowed")
        if not canonical_request:
            return _error(400, "bad_host")
        correlation = monitor_auth.read_cookie(jar, pending_name)
        # state は照合時に消費される。並行して始めた次の試行の Cookie を
        # 古い応答で消さず、5分の期限または次回開始時の上書きに任せる。
        try:
            issued, target = auth.provider.complete(port, query, correlation)
        except GoogleLoginError as exc:
            result = urlencode({"state": exc.state, "next": monitor_auth.return_target(exc.return_to)})
            return _redirect("/auth/result?" + result)
        if session:
            auth.provider.logout(session)
        return _redirect(monitor_auth.return_target(target),
                         monitor_auth.cookie(session_name, issued, "/", SESSION_TTL))
    if path == "/auth/result":
        state = query.get("state", "")
        codes = {"cancelled": 401, "denied": 403, "unavailable": 503}
        if state not in codes:
            return _error(404, "not_found")
        retry = monitor_auth.login_path(monitor_auth.return_target(query.get("next", "/")))
        return _html_response(codes[state], render_auth_page(state, retry))
    if path == "/auth/logout":
        if method != "POST":
            return _error(405, "method_not_allowed")
        if not canonical_request or not _matches_authority(headers.get("Origin"), "https://127.0.0.1", port, "https"):
            return _error(403, "bad_origin")
        if not session or not auth.provider.authenticated(session):
            return _error(401, "authentication_required")
        if not read_csrf or not auth.accepts(session, read_csrf()):
            return _error(403, "invalid_csrf")
        auth.provider.logout(session)
        return _redirect("/", monitor_auth.cookie(session_name, "", "/", 0))
    if session and auth.provider.authenticated(session):
        return session
    if path in monitor_auth.PAGES:
        target = monitor_auth.page_target(path, query)
        return _html_response(401, render_auth_page("login", monitor_auth.login_path(target)))
    return _error(401, "authentication_required")


def _handle(runtime: Runtime, method: str, path: str, query: dict[str, str],
            headers: dict, port: int, auth: monitor_auth.MonitorAuth | None = None,
            read_csrf: Callable[[], str | None] | None = None, *, scheme: str = "http") -> tuple[int, dict, bytes]:
    if method not in ("GET", "HEAD") and not (auth and method == "POST" and path == "/auth/logout"):
        return _error(405, "method_not_allowed")
    host = next((name for name in ALLOWED_HOSTS if _matches_authority(headers.get("Host"), name, port, scheme)), None)
    if host is None:
        return _error(400, "bad_host")
    origin = headers.get("Origin")
    if origin is not None and not _matches_authority(origin, f"{scheme}://{host}", port, scheme):
        return _error(403, "bad_origin")
    if "?" in path or "#" in path:
        return _error(400, "bad_path")
    if "%2f" in path.lower() or "%5c" in path.lower():
        return _error(400, "bad_path")
    if ".." in path:
        return _error(400, "bad_path")
    if auth is not None:
        if scheme != "https":
            return _error(403, "monitor_tls_required")
        access = _auth_gate(auth, method, path, query, headers, port, read_csrf)
        if isinstance(access, tuple):
            return access
    matched = _match_route(path)
    if matched is None:
        return _error(404, "not_found")
    name, groups = matched
    store = runtime.store
    view = query.get("view", "operator")
    if view not in ("operator", "blue"):
        return _error(400, "bad_view")

    try:
        if name in ("index", "decision", "architecture", "intake"):
            html = _load_page(name + ".html", blue=view == "blue")
            if not html:
                return _error(500, "index_unavailable")
            if auth is not None:
                from .monitor_auth_ui import add_logout
                html = add_logout(html, auth.for_session(access))
            return _html_response(200, html, logout_form=auth is not None)

        if name == "architecture_asset":
            asset = groups[0]
            data = resources.files("ai_yori_murasaki").joinpath("web", "architecture", asset).read_bytes()
            if len(data) > MAX_RESPONSE_BYTES:
                return _error(500, "asset_too_large")
            hdrs = _security_headers()
            media = "application/json" if asset.endswith(".json") else "text/javascript"
            hdrs.update({"Content-Type": media + "; charset=utf-8", "Content-Length": str(len(data))})
            return 200, hdrs, data

        if name in ("architecture_maps", "architecture_map"):
            # 作業場所に置いた構成図。読み取りだけで、Blue には出さない。
            if view == "blue":
                return _json_response(200, {"status": "withheld"})
            from .architecture_maps import MapUnavailable, list_maps, read_map
            if name == "architecture_maps":
                return _json_response(200, {"maps": list_maps(store.workspace)})
            try:
                data = read_map(store.workspace, groups[0])
            except (MapUnavailable, OSError):
                return _error(409, "map_unavailable")
            if data is None:
                return _error(404, "not_found")
            hdrs = _security_headers()
            hdrs.update({"Content-Type": "application/json; charset=utf-8", "Content-Length": str(len(data))})
            return 200, hdrs, data

        if name == "health":
            return _json_response(200, {"status": "ok"})

        if name == "receipts":
            if view == "blue":
                return _json_response(200, {"status": "withheld"})
            from .intake import search
            try:
                return _json_response(200, search(store, query.get("q", "")))
            except DataError as exc:
                if exc.reason_code == "invalid_query":
                    return _error(400, "invalid_query")
                raise

        if name in ("sources", "source"):
            if view == "blue":
                return _json_response(200, {"status": "withheld", "reason": "資料はBlueに非公開"})
            from .sources import Sources
            sources = Sources(store)
            try:
                if name == "sources":
                    return _json_response(200, {"sources": sources.list()})
                return _json_response(200, {"source": sources.get(groups[0])})
            except (DataError, OSError, KeyError):
                return _error(409, "source_unavailable")

        if name == "campaigns":
            if "page" in query:
                from .monitor_index import PAGE_SIZE, needs_index, read_page

                value = query["page"]
                if not re.fullmatch(r"[1-9][0-9]{0,8}", value):
                    return _error(400, "invalid_page")
                page = int(value)
                result = read_page(store, page, view)
                if result is not None:
                    return _json_response(200, result)
                if needs_index(store):
                    return _error(409, "monitor_index_required")
            campaigns = [_project_campaign(cid, summary, view)
                          for cid, summary in campaign_summaries(store, include_csv=view == "operator").items()]
            campaigns.sort(key=lambda c: not c["created_at"])
            if "page" in query:
                total = len(campaigns)
                pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
                page = min(page, pages)
                return _json_response(200, {"campaigns": campaigns[(page-1)*PAGE_SIZE:page*PAGE_SIZE],
                                            "total": total, "page": page, "pages": pages, "checked_at": None})
            return _json_response(200, {"campaigns": campaigns})

        if name == "campaign_relations":
            if view == "blue":
                return _json_response(200, {"status": "withheld"})
            from .decision_view import previous_candidates
            from .storage import sanitize
            campaigns = runtime.list_campaigns()
            previous = previous_candidates(campaigns)
            rows = [{"campaign_id": c["campaign_id"], "intent_id": _safe_str(c.get("intent_id")),
                     "created_at": _safe_str(c.get("created_at")), "previous_id": ids[0] if len(ids) == 1 else None,
                     "previous_candidates": list(ids) if len(ids) > 1 else []}
                    for c in campaigns for ids in [previous.get(c["campaign_id"], ())]]
            return _json_response(200, {"campaigns": sanitize(rows, store.secrets)})

        if name == "campaign_drift":
            if view == "blue":
                return _json_response(200, {"status": "withheld"})
            from .drift_report import read_drift

            try:
                report = read_drift(runtime, groups[0])
            except DataError as exc:
                if exc.reason_code == "not_found":
                    return _error(404, "not_found")
                if exc.reason_code == "hold":
                    return _json_response(200, {"status": "hold"})
                raise
            return _drift_response(report)

        indexed = None
        if name in ("campaign", "campaign_decision_view", "campaign_runs", "campaign_rsi", "campaign_assessment"):
            withheld = view == "blue" and name in ("campaign", "campaign_decision_view", "campaign_assessment")
            if not withheld:
                try:
                    indexed = read_selection(store, groups[0], compare=name == "campaign_decision_view",
                                             selected=query.get("previous"))
                except KeyError:
                    return _error(404, "not_found")

        if name == "campaign_decision_view":
            if view == "blue":
                return _json_response(200, {"decision_view": {"status": "withheld", "reason": "評価情報はBlueに非公開"}})
            cid = groups[0]
            summaries = {cid: indexed.current.summary} if indexed else campaign_summaries(store)
            if cid not in summaries:
                return _error(404, "not_found")
            if summaries[cid].status != "complete" or not summaries[cid].campaign:
                return _json_response(200, {"decision_view": None, "status": "hold",
                                            "reason": "検知の未集計があるため、判断と前回比較を保留しています。"})
            from .decision_view import read_decision_view
            if indexed and indexed.previous:
                summaries[indexed.previous.cid] = indexed.previous.summary
            try:
                projected = read_decision_view(runtime, cid, query.get("previous"), summaries,
                                               options=indexed.candidates if indexed else None,
                                               report_loader=indexed.current.report if indexed else None)
            except (DataError, OSError, ValueError, KeyError, TypeError):
                return _json_response(200, {"decision_view": None, "status": "hold", "reason": "記録を確認できません"})
            return _json_response(200, {"decision_view": projected})

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
                report = indexed.current.report() if indexed else load_report(runtime, cid)
            except IndexUpdateRequired:
                raise
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
            summary = indexed.current.summary if indexed else campaign_summaries(store).get(cid)
            if summary is None:
                return _error(404, "not_found")
            findings = [_project_finding(obj) for obj in summary.findings.values()]
            remediations = []
            for ref in summary.remediation_refs:
                try:
                    obj = store.get_object(ref)
                except (DataError, FileNotFoundError):
                    continue
                remediations.append({
                    "remediation_id": _safe_str(obj.get("remediation_id")),
                    "status": _safe_str(obj.get("status")),
                })
            if summary.pending_runs:
                metrics = {"status": "HOLD", "reason_code": "evidence_unverified", "metrics": None, "limitations": []}
            elif indexed:
                metrics = _project_lab_metrics(indexed.current, cid, verified_events=indexed.current.events)
            else:
                metrics = _project_lab_metrics(runtime, cid)
            return _json_response(200, {
                "campaign": _project_campaign(cid, summary),
                "findings": findings,
                "remediations": remediations,
                "metrics": metrics,
            })

        if name == "campaign_runs":
            cid = groups[0]
            if indexed:
                case = indexed.current
                if not case.summary.campaign:
                    return _error(404, "not_found")
                return _json_response(200, {"runs": [
                    _project_run(run, verified_events=case.events.get(run["run_id"], []))
                    for run in case.list_runs(cid)]})
            try:
                runtime.get_campaign(cid)
            except DataError:
                return _error(404, "not_found")
            runs = [_project_run(r, store) for r in runtime.list_runs(cid)]
            return _json_response(200, {"runs": runs})

        if name == "campaign_rsi":
            cid = groups[0]
            if indexed:
                if not indexed.current.summary.campaign:
                    return _error(404, "not_found")
            else:
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
                manifest = store.load_manifest(rid)
            except (DataError, OSError):
                return _error(409, "evidence_unverified")
            if manifest is None:
                return _error(404, "not_found")
            run = manifest.get("run")
            cid = run.get("campaign_id") if isinstance(run, dict) else None
            if not isinstance(cid, str) or not ID_RE.fullmatch(cid) or run.get("run_id") != rid:
                return _error(409, "evidence_unverified")
            try:
                events = store.read_verified_events(rid, campaign_id=cid, allow_missing=run.get("status") == "running")
            except (DataError, OSError):
                return _error(409, "evidence_unverified")
            if manifest.get("sealed") and (not events or events[-1]["event_type"] != "chain_sealed"):
                return _error(409, "evidence_unverified")
            selected = [e for e in events if isinstance(e.get("sequence"), int) and e["sequence"] > after]
            selected = selected[:limit]
            projected = [project_event(e, view) for e in selected]
            return _json_response(200, {"events": projected})

        if name == "verification":
            from .monitor_verification import MAX_PAGE, read_verification
            rid = groups[0]
            try:
                runtime.get_run(rid)
            except DataError:
                return _error(404, "not_found")
            limit, before = query.get("limit"), query.get("before")
            if limit is not None and (not re.fullmatch(r"[1-9][0-9]{0,2}", limit) or int(limit) > MAX_PAGE):
                return _error(400, "bad_limit")
            if before is not None and (limit is None or not re.fullmatch(r"[1-9][0-9]{0,18}", before)):
                return _error(400, "invalid_before")
            try:
                page = read_verification(store, rid, limit=int(limit) if limit else None,
                                         before=int(before) if before else None, operator=view == "operator")
            except DataError as exc:
                if exc.reason_code == "invalid_before":
                    return _error(400, "invalid_before")
                code = exc.reason_code if exc.reason_code.startswith("verification_") else "verification_unavailable"
                return _error(409, code)
            return _json_response(200, page)

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
    except IndexUpdateRequired:
        return _error(409, "monitor_index_required")
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

    def _read_logout_csrf(self) -> str | None:
        length = self.headers.get("Content-Length", "")
        if (self.headers.get("Transfer-Encoding") is not None
                or len(self.headers.get_all("Content-Length", [])) != 1
                or self.headers.get_content_type() != "application/x-www-form-urlencoded"
                or length != "69"):
            return None
        body = self.rfile.read(69)
        match = re.fullmatch(rb"csrf=([a-f0-9]{64})", body)
        return match[1].decode("ascii") if match else None

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
                "Cookie": "; ".join(self.headers.get_all("Cookie", [])),
            }
            status, headers, body = _handle(runtime, method, path, query,
                                            req_headers, port, self.server.auth, self._read_logout_csrf,
                                            scheme=self.server.scheme)
        # Merge security headers with route headers so each header is
        # emitted exactly once.  Route-specific headers (e.g. the HTML CSP
        # with inline hashes) take precedence over the default CSP.
        merged = _security_headers()
        merged.update(headers)
        self.send_response(status)
        for key, value in merged.items():
            if key == "Set-Cookie":
                for item in value:
                    self.send_header(key, item)
            else:
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

    def __init__(self, addr: tuple[str, int], runtime: Runtime, google_sso: GoogleSSO | None,
                 tls_context: ssl.SSLContext | None) -> None:
        if google_sso is not None and tls_context is None:
            raise DataError("Google SSOにはHTTPSが必要です", "monitor_tls_required")
        if tls_context is not None and (tls_context.protocol != ssl.PROTOCOL_TLS_SERVER
                                        or tls_context.minimum_version < ssl.TLSVersion.TLSv1_2):
            raise DataError("TLS 1.2以上のサーバー設定が必要です", "monitor_tls_configuration")
        self.tls_context = tls_context
        self.scheme = "https" if tls_context is not None else "http"
        self.runtime = runtime
        self.auth = monitor_auth.MonitorAuth(google_sso) if google_sso is not None else None
        super().__init__(addr, _Handler)

    def get_request(self):
        connection, address = super().get_request()
        if self.tls_context is not None:
            # accept スレッドではネットワークとのハンドシェイクを開始しない。
            connection = self.tls_context.wrap_socket(connection, server_side=True, do_handshake_on_connect=False)
        return connection, address

    def finish_request(self, request, client_address) -> None:
        if self.tls_context is not None:
            request.settimeout(5)
            try:
                request.do_handshake()
            except OSError:
                return
            request.settimeout(30)
        # 応答後の送信終了と close は HTTPServer が同じ接続に対して行う。
        super().finish_request(request, client_address)


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


def create_server(runtime: Runtime, bind: str = "127.0.0.1:8765", *,
                  google_sso: GoogleSSO | None = None,
                  tls_context: ssl.SSLContext | None = None) -> _Server:
    """ローカル組み込み用。非SSOの既存fixtureはHTTPを利用できる。"""
    host, port = _parse_bind(bind)
    return _Server((host, port), runtime, google_sso, tls_context)


def configured_server(runtime: Runtime, bind: str, *, cert: str | Path | None = None,
                      key: str | Path | None = None, allow_http_loopback: bool = False,
                      google_sso: bool = False) -> _Server:
    from .monitor_tls import server_context

    address = _parse_bind(bind)
    if google_sso and allow_http_loopback:
        raise DataError("Google SSOにはHTTPSが必要です", "monitor_tls_required")
    provider = None
    if google_sso:
        from .google_sso import GoogleSSO
        provider = GoogleSSO.from_env()
    return _Server(address, runtime, provider, server_context(cert, key, allow_http_loopback=allow_http_loopback))


def serve(bind: str = "127.0.0.1:8765", runtime: Runtime | None = None, *,
          tls_cert: str | Path | None = None, tls_key: str | Path | None = None,
          allow_http_loopback: bool = False) -> None:
    if runtime is None:
        runtime = Runtime(Store.from_env())
    server = configured_server(runtime, bind, cert=tls_cert, key=tls_key, allow_http_loopback=allow_http_loopback)
    try:
        server.serve_forever()
    finally:
        server.server_close()
