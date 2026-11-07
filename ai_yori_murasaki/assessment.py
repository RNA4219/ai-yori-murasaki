"""Assessment orchestration: import, build, load, project (SPEC-ASSESSMENT).

Public API:
    build_report(runtime, campaign_id, run_id, observations, context) -> report_ref
    import_artifact(runtime, campaign_id, run_id, format, raw, context) -> dict
    load_report(runtime, campaign_id, view='operator') -> dict | None
    project_report(report, view, phase) -> dict

Trust boundary:
- ``observations`` passed to ``build_report`` are *trusted normalized
  assessment entries* produced by an in-process diagnostic producer
  (``FindingAssessment`` objects). They are NOT raw scanner output and are
  NOT accepted from external callers. External artifacts must go through
  ``import_artifact`` which normalizes via the adapter normalizers and
  downgrades all scanner claims to hypotheses.
- ``context`` for ``import_artifact`` is restricted to
  ``{target, target_version, actor, previous_report_ref, connection_state,
  enrichment_refs}``. It may NOT supply confirmations, validations,
  classifications, or diagnostics.

Legacy schema (``contracts.KINDS`` / ``schemas/v1/*.schema.json``) is never
modified. Assessment data lives in a single ``assessment_report`` object
validated by ``assessment_contracts``.
"""
from __future__ import annotations

import hashlib
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from . import advisories, jsonio, severity
from .assessment_contracts import (
    AssessmentContractError,
    validate_assessment,
    semantic_validate_assessment,
)
from . import assessment_enrichment
from .adapters import normalizers
from .storage import sanitize


ASSESSMENT_VERSION = "1.0.0"
MAX_ARTIFACT_BYTES = 16 * 1024 * 1024
MAX_TITLE = 200
MAX_REASON = 500
MAX_SUMMARY = 1000

_SAFE_STR_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Japanese category descriptions used for normalized titles.
_CATEGORY_TITLES = {
    "authz": "認可制御の不備",
    "sqli": "SQLインジェクション",
    "xss": "クロスサイトスクリプティング",
    "path_traversal": "パストラバーサル",
    "dependency_cve": "依存関係の既知脆弱性",
    "unknown": "分類未確定の検出事項",
}

# Explicit, reliable source-rule -> category mapping. Anything not listed
# stays ``unknown``; we never infer category from keywords.
_RULE_CATEGORY = {
    ("trivy", "vulnerability"): "dependency_cve",
    ("trivy", "misconfiguration"): "unknown",
    ("trivy", "secret"): "unknown",
    ("trivy", "license"): "unknown",
    ("codeql", "sarif_result"): "unknown",
}

_CONNECTION_STATES = {"contract", "live", "not_tested", "failed"}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _safe_str(value: Any, limit: int) -> str:
    if not isinstance(value, str):
        return ""
    cleaned = _SAFE_STR_RE.sub("", value)
    return cleaned[:limit]


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def _require(cond: bool, msg: str, code: str = "input_invalid") -> None:
    if not cond:
        raise jsonio.DataError(msg, code)


def _active_run(runtime: Any, campaign_id: str, run_id: str) -> dict:
    run = runtime.get_run(run_id)
    _require(run.get("campaign_id") == campaign_id,
             "run does not belong to campaign", "policy_denied")
    _require(run.get("status") == "running",
             "run is not active", "policy_denied")
    manifest = runtime.store.load_manifest(run_id)
    _require(bool(manifest) and "run" in manifest,
             "run manifest missing", "hold")
    _require(not manifest.get("sealed"), "run chain is sealed", "policy_denied")
    return run


def _campaign_target(campaign: dict) -> str:
    scope = campaign.get("scope") or {}
    targets = scope.get("targets") or []
    _require(isinstance(targets, list) and len(targets) > 0,
             "campaign has no targets", "input_invalid")
    first = targets[0]
    if isinstance(first, dict):
        ident = first.get("identifier")
    else:
        ident = first
    _require(isinstance(ident, str) and ident,
             "campaign target identifier missing", "input_invalid")
    return ident


def _validate_context(runtime: Any, campaign: dict, run: dict,
                      context: dict, *, external: bool) -> dict:
    _require(isinstance(context, dict), "context must be object")
    allowed = {"target", "target_version", "actor", "previous_report_ref"}
    if external:
        allowed |= {"connection_state", "enrichment_refs"}
    else:
        allowed |= {"coverage", "cvss", "business_impact", "manifest_ref",
                    "enrichment"}
    extra = set(context) - allowed
    _require(not extra, f"context has disallowed keys: {sorted(extra)}")

    target = context.get("target")
    _require(target == _campaign_target(campaign),
             "context.target must match campaign scope target", "policy_denied")
    run_actor = run.get("actor")
    _require(isinstance(run_actor, dict), "run.actor must be object",
             "input_invalid")
    run_role = run_actor.get("role")
    _require(isinstance(run_role, str) and run_role,
             "run.actor.role required", "input_invalid")
    actor = context.get("actor", run_role)
    _require(actor == run_role,
             "context.actor must equal run.actor.role", "policy_denied")
    target_version = context.get("target_version")
    _require(isinstance(target_version, str) and target_version,
             "context.target_version required", "input_invalid")
    prev = context.get("previous_report_ref")
    if prev is not None:
        _require(isinstance(prev, str) and re.fullmatch(r"[0-9a-f]{64}", prev),
                 "previous_report_ref must be sha256 hex", "input_invalid")
        try:
            runtime.store.get_object(prev)
        except Exception as exc:  # noqa: BLE001
            raise jsonio.DataError("previous_report_ref unresolved",
                                   "input_invalid") from exc
    out = {
        "target": target,
        "target_version": target_version,
        "actor": actor,
        "previous_report_ref": prev,
    }
    if not external:
        for key in ("coverage", "cvss", "business_impact", "manifest_ref",
                    "enrichment"):
            if key in context:
                out[key] = context[key]
    if external:
        cs = context.get("connection_state", "not_tested")
        _require(cs in _CONNECTION_STATES,
                 "connection_state invalid", "input_invalid")
        out["connection_state"] = cs
        refs = context.get("enrichment_refs") or []
        _require(isinstance(refs, list) and len(refs) <= 16,
                 "enrichment_refs must be list<=16", "input_invalid")
        for r in refs:
            _require(isinstance(r, dict), "enrichment ref must be object",
                     "input_invalid")
        out["enrichment_refs"] = refs
    return out


# ---------------------------------------------------------------------------
# build_report
# ---------------------------------------------------------------------------


def build_report(runtime: Any, campaign_id: str, run_id: str,
                  observations: list, context: dict) -> str:
    """Build and persist an assessment report from trusted observations.

    ``observations`` is a list of ``FindingAssessment`` objects produced by
    an in-process diagnostic producer. Each entry is already normalized and
    validated; this function does not accept raw scanner output here.
    """
    _require(isinstance(observations, list), "observations must be list")
    campaign = runtime.get_campaign(campaign_id)
    run = _active_run(runtime, campaign_id, run_id)
    ctx = _validate_context(runtime, campaign, run, context, external=False)

    findings: list[dict] = []
    cvss_records: list[dict] = []
    business_impact: list[dict] = []
    coverage_categories: list[dict] = []
    seen_cause: dict[str, int] = {}
    by_cause: dict[str, dict] = {}

    for obs in observations:
        _require(isinstance(obs, dict), "observation must be object")
        cause_id = obs.get("cause_id")
        if not (isinstance(cause_id, str) and cause_id):
            cause_key = obs.get("cause_key")
            _require(isinstance(cause_key, str) and cause_key,
                     "observation.cause_key required", "input_invalid")
            cause_id = _sha256("\x1f".join([
                str(obs.get("source", "")), str(obs.get("kind", "")),
                ctx["target"], cause_key,
            ]))

        attempt = obs.get("attempt", 1)
        if not isinstance(attempt, int) or isinstance(attempt, bool) or attempt < 1:
            attempt = 1
        seen_cause[cause_id] = seen_cause.get(cause_id, 0) + attempt

        existing = by_cause.get(cause_id)
        if existing is not None:
            existing["attempts"] = seen_cause[cause_id]
            obs_list = obs.get("observations", [])
            if isinstance(obs_list, list):
                existing["observations"].extend(obs_list)
            claims_list = obs.get("claims", [])
            if isinstance(claims_list, list):
                existing["claims"].extend(claims_list)
            if isinstance(obs.get("cvss"), dict):
                cvss_records.append(obs["cvss"])
            if isinstance(obs.get("business_impact"), dict):
                business_impact.append(obs["business_impact"])
            cov = obs.get("coverage")
            if isinstance(cov, dict) and isinstance(cov.get("categories"), list):
                coverage_categories.extend(cov["categories"])
            continue

        finding_id = obs.get("finding_id")
        _require(isinstance(finding_id, str) and finding_id,
                 "observation.finding_id required", "input_invalid")
        finding_ref = obs.get("finding_ref")
        _require(isinstance(finding_ref, str) and
                 re.fullmatch(r"[0-9a-f]{64}", finding_ref),
                 "observation.finding_ref must be sha256 hex", "input_invalid")

        category = obs.get("category", "unknown")
        if category not in _CATEGORY_TITLES:
            category = "unknown"
        title = _CATEGORY_TITLES[category]

        obs_list = obs.get("observations", [])
        claims_list = obs.get("claims", [])
        entry = {
            "finding_id": finding_id,
            "finding_ref": finding_ref,
            "cause_id": cause_id,
            "category": category,
            "title": title,
            "attempts": seen_cause[cause_id],
            "observations": list(obs_list) if isinstance(obs_list, list) else [],
            "claims": list(claims_list) if isinstance(claims_list, list) else [],
            "classification": obs.get("classification"),
            "remediation": obs.get("remediation"),
            "validation": obs.get("validation"),
        }
        by_cause[cause_id] = entry
        findings.append(entry)
        if isinstance(obs.get("cvss"), dict):
            cvss_records.append(obs["cvss"])
        if isinstance(obs.get("business_impact"), dict):
            business_impact.append(obs["business_impact"])
        cov = obs.get("coverage")
        if isinstance(cov, dict) and isinstance(cov.get("categories"), list):
            coverage_categories.extend(cov["categories"])

    ctx_coverage = context.get("coverage") if isinstance(context, dict) else None
    if isinstance(ctx_coverage, dict) and isinstance(ctx_coverage.get("categories"), list):
        coverage_categories = list(ctx_coverage["categories"])
    elif not coverage_categories:
        coverage_categories = [
            {"category": cat, "status": "not_executed",
             "reason": "調査範囲の証拠がありません",
             "evidence_refs": []}
            for cat in ("authz", "sqli", "xss", "path_traversal", "dependency_cve")
        ]

    ctx_cvss = context.get("cvss") if isinstance(context, dict) else None
    if isinstance(ctx_cvss, list):
        cvss_records = list(ctx_cvss)
    ctx_bi = context.get("business_impact") if isinstance(context, dict) else None
    if isinstance(ctx_bi, list):
        business_impact = list(ctx_bi)

    manifest_ref = None
    if isinstance(context, dict) and isinstance(context.get("manifest_ref"), str):
        manifest_ref = context["manifest_ref"]
    if manifest_ref is None:
        manifest_ref = obs_manifest(observations)

    report = {
        "schema_version": ASSESSMENT_VERSION,
        "assessment_version": ASSESSMENT_VERSION,
        "report_id": _new_id("asc"),
        "campaign_id": campaign_id,
        "run_id": run_id,
        "target": ctx["target"],
        "target_version": ctx["target_version"],
        "previous_report_ref": ctx["previous_report_ref"],
        "created_at": _now(),
        "actor": ctx["actor"],
        "coverage": {
            "categories": coverage_categories,
            "created_at": _now(),
        },
        "findings": findings,
        "cvss": cvss_records,
        "business_impact": business_impact,
        "manifest_ref": manifest_ref,
        "extensions": {},
    }

    ctx_enrichment = context.get("enrichment") if isinstance(context, dict) else None
    if isinstance(ctx_enrichment, list):
        finding_ids = {f["finding_id"] for f in findings}
        try:
            assessment_enrichment.validate_enrichment_records(
                ctx_enrichment, finding_ids)
        except assessment_enrichment.EnrichmentError as exc:
            raise jsonio.DataError(
                f"enrichment invalid: {exc}", "input_invalid") from exc
        report["enrichment"] = list(ctx_enrichment)

    with runtime.store.lock():
        validate_assessment("assessment_report", report)
        semantic_validate_assessment("assessment_report", report, runtime.store)
        report_ref = runtime.store.put_object(report)
        runtime.record_event(
            run_id, "state_captured", "orchestrator",
            evidence_refs=(report_ref,),
            extensions={
                "assessment_version": ASSESSMENT_VERSION,
                "assessment_ref": report_ref,
                "assessment_target_version": ctx["target_version"],
            },
        )
    return report_ref


def obs_manifest(observations: list) -> str | None:
    for obs in observations:
        if isinstance(obs, dict) and isinstance(obs.get("manifest_ref"), str):
            return obs["manifest_ref"]
    return None


# ---------------------------------------------------------------------------
# import_artifact
# ---------------------------------------------------------------------------


def _load_artifact(format: str, raw: Any) -> dict:
    _require(format in ("trivy", "sarif"),
             "format must be trivy|sarif", "input_invalid")
    if isinstance(raw, dict):
        data = jsonio.canonical(raw)
    elif isinstance(raw, (bytes, bytearray)):
        data = bytes(raw)
    else:
        raise jsonio.DataError("raw must be bytes or dict", "input_invalid")
    _require(len(data) <= MAX_ARTIFACT_BYTES,
             f"artifact exceeds {MAX_ARTIFACT_BYTES} bytes", "input_invalid")
    if format == "trivy":
        return normalizers.normalize_trivy(data)
    return normalizers.normalize_sarif(data)


def _scanner_severity(data: dict) -> str:
    raw = data.get("Severity") or data.get("level") or "info"
    if not isinstance(raw, str):
        return "info"
    key = raw.strip().lower()
    return {
        "critical": "critical", "high": "high", "medium": "medium",
        "moderate": "medium", "low": "low", "info": "info",
        "informational": "info", "note": "info", "none": "info",
    }.get(key, "info")


def _source_title(finding: dict) -> str:
    data = finding.get("data") or {}
    for key in ("Title", "title", "message"):
        val = data.get(key) if isinstance(data, dict) else None
        if isinstance(val, str) and val:
            return _safe_str(val, MAX_TITLE)
    return ""


def _cwe_candidates(finding: dict) -> list[str]:
    data = finding.get("data") or {}
    out: list[str] = []
    if isinstance(data, dict):
        for key in ("CweIDs", "cwe_ids", "CWE"):
            val = data.get(key)
            if isinstance(val, list):
                for c in val:
                    if isinstance(c, str) and re.fullmatch(r"CWE-[0-9]+", c):
                        out.append(c)
    return out[:16]


def _cve_candidates(finding: dict) -> list[str]:
    data = finding.get("data") or {}
    out: list[str] = []
    if isinstance(data, dict):
        for key in ("VulnerabilityID", "cve", "CVE"):
            val = data.get(key)
            if isinstance(val, str) and re.fullmatch(r"CVE-[0-9]{4}-[0-9]{4,19}", val):
                out.append(val)
    return out[:16]


def _location_key(finding: dict) -> str:
    target = finding.get("target")
    if isinstance(target, dict):
        return "\x1f".join([
            str(target.get("path", "")), str(target.get("line", "")),
            str(target.get("column", "")),
        ])
    locations = finding.get("locations")
    if isinstance(locations, list) and locations:
        loc = locations[0] if isinstance(locations[0], dict) else {}
        return "\x1f".join([
            str(loc.get("uri", "")), str(loc.get("startLine", "")),
            str(loc.get("startColumn", "")),
        ])
    return ""


def _rule_key(finding: dict) -> str:
    rule_id = finding.get("rule_id")
    if isinstance(rule_id, str) and rule_id:
        return rule_id
    data = finding.get("data") or {}
    if isinstance(data, dict):
        for key in ("ID", "ruleId", "RuleID", "VulnerabilityID"):
            val = data.get(key)
            if isinstance(val, str) and val:
                return val
    return str(finding.get("kind", ""))


def import_artifact(runtime: Any, campaign_id: str, run_id: str,
                    format: str, raw: Any, context: dict) -> dict:
    """Import a scanner artifact, downgrading all claims to hypotheses."""
    campaign = runtime.get_campaign(campaign_id)
    run = _active_run(runtime, campaign_id, run_id)
    ctx = _validate_context(runtime, campaign, run, context, external=True)

    normalized = _load_artifact(format, raw)
    source = "trivy" if format == "trivy" else "codeql"
    raw_findings = normalized.get("findings") or []

    finding_ids: list[str] = []
    coverage_categories: list[dict] = []
    seen_cause: dict[str, int] = {}
    observations: list[dict] = []
    finding_ref_by_id: dict[str, str] = {}

    for f in raw_findings:
        if not isinstance(f, dict):
            continue
        kind = f.get("kind", "")
        category = _RULE_CATEGORY.get((source, kind), "unknown")
        rule = _rule_key(f)
        loc = _location_key(f)
        stable_id = f.get("stable_id")
        if isinstance(stable_id, str) and stable_id:
            cause_key = stable_id
        else:
            cause_key = "\x1f".join([rule, loc])
        cause_id = _sha256("\x1f".join([source, kind, ctx["target"], cause_key]))
        attempt = seen_cause.get(cause_id, 0) + 1
        seen_cause[cause_id] = attempt

        finding_id = "f" + _sha256("\x1f".join([
            campaign_id, run_id, source, cause_key,
        ]))[:31]
        if finding_id not in finding_ref_by_id:
            finding_ids.append(finding_id)

        sev = _scanner_severity(f.get("data") or {})
        title = _CATEGORY_TITLES[category]
        source_title = _source_title(f)
        cwe = _cwe_candidates(f)
        cve = _cve_candidates(f)

        # Persist a legacy finding record (schema unchanged).
        legacy = {
            "schema_version": "1.0.0",
            "finding_id": finding_id,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "source": source,
            "severity": sev,
            "title": title,
            "location": {
                "path": _safe_str((f.get("location") or {}).get("path", "unknown"), 1024) or "unknown",
                "line": (f.get("location") or {}).get("line"),
                "column": (f.get("location") or {}).get("column"),
            },
            "references": [],
            "extensions": {
                "kind": kind,
                "source_title": source_title,
                "scanner_severity": sev,
            },
        }

        # Diagnostic observation: origin=scanner, claim=hypothesis.
        diag = {
            "diagnostic_version": "1.0.0",
            "campaign_id": campaign_id,
            "run_id": run_id,
            "target": ctx["target"],
            "target_version": ctx["target_version"],
            "actor": ctx["actor"],
            "captured_at": _now(),
            "kind": "observation",
            "details": {
                "summary": _safe_str(source_title or title, MAX_SUMMARY),
                "source": source,
                "origin": "scanner",
                "data": {
                    "rule": _safe_str(rule, 200),
                    "category": category,
                    "scanner_severity": sev,
                    "cwe_candidates": cwe,
                    "cve_candidates": cve,
                    "connection_state": ctx["connection_state"],
                },
            },
        }
        diag_ref = runtime.store.put_object(diag)

        legacy["references"] = [diag_ref]
        if finding_id in finding_ref_by_id:
            finding_ref = finding_ref_by_id[finding_id]
        else:
            finding_ref = runtime.register_record("finding", legacy, run_id=run_id)
            finding_ref_by_id[finding_id] = finding_ref

        observations.append({
            "finding_id": finding_id,
            "finding_ref": finding_ref,
            "cause_key": cause_key,
            "source": source,
            "kind": kind,
            "category": category,
            "observations": [{
                "observation_id": _new_id("obs"),
                "finding_id": finding_id,
                "kind": "scanner_output",
                "payload_ref": diag_ref,
                "captured_at": _now(),
            }],
            "claims": [{
                "claim_id": _new_id("clm"),
                "finding_id": finding_id,
                "cause_statement": _safe_str(source_title or title, MAX_SUMMARY),
                "confidence_axis": "cause",
                "status": "hypothesis",
                "basis_refs": [diag_ref],
                "refuted_by": None,
            }],
            "classification": {
                "classification_id": _new_id("cls"),
                "finding_id": finding_id,
                "cwe": {
                    "status": "candidate" if cwe else "unknown",
                    "ids": cwe,
                    "basis_refs": [diag_ref] if cwe else [],
                },
                "cve": {
                    "status": "candidate" if cve else "unknown",
                    "ids": cve,
                    "applicability": {
                        "product": None, "version": None, "condition": None,
                        "basis_refs": [],
                    },
                },
                "owasp": {"ids": [], "basis_refs": []},
                "attack": {"status": "unknown", "techniques": [],
                           "observation_refs": []},
            },
            "remediation": None,
            "validation": None,
        })

    # Coverage: only categories the scanner explicitly reported as executed.
    executed = normalized.get("coverage")
    if isinstance(executed, dict) and isinstance(executed.get("categories"), list):
        for c in executed["categories"]:
            if isinstance(c, dict) and c.get("status") == "executed":
                coverage_categories.append({
                    "category": _safe_str(c.get("category", "unknown"), 64),
                    "status": "executed",
                    "reason": None,
                    "evidence_refs": [],
                })
    if not coverage_categories:
        for _category in ("authz", "sqli", "xss", "path_traversal", "dependency_cve"):
            coverage_categories.append({
                "category": _category,
                "status": "not_executed",
                "reason": "スキャナがカバレッジのメタデータを報告しませんでした",
                "evidence_refs": [],
            })

    coverage = {
        "categories": coverage_categories,
        "created_at": _now(),
    }
    coverage_ref = runtime.store.put_object(coverage)

    report_ref = build_report(
        runtime, campaign_id, run_id, observations,
        {
            "target": ctx["target"],
            "target_version": ctx["target_version"],
            "actor": ctx["actor"],
            "previous_report_ref": ctx["previous_report_ref"],
            "coverage": coverage,
        },
    )
    return {
        "finding_ids": finding_ids,
        "coverage_ref": coverage_ref,
        "report_ref": report_ref,
    }


# ---------------------------------------------------------------------------
# load_report
# ---------------------------------------------------------------------------


def _iter_assessment_events(runtime: Any, campaign_id: str):
    for run in runtime.list_runs(campaign_id):
        chain = runtime.store.verify_chain(run["run_id"])
        if chain.get("result") != "PASS":
            raise jsonio.DataError(
                f"ledger verification failed for run {run['run_id']}", "hold")
        for event in runtime.store.read_events(run["run_id"]):
            ext = event.get("extensions") or {}
            if not isinstance(ext, dict):
                continue
            if ext.get("assessment_version") and ext.get("assessment_ref"):
                yield event, ext


def load_report(runtime: Any, campaign_id: str, view: str = "operator") -> dict | None:
    """Return the latest assessment report for the campaign, or None."""
    if view == "blue":
        return {"status": "withheld", "reason": "評価情報はBlueに非公開"}

    campaign = runtime.get_campaign(campaign_id)
    current_version = None
    current_version_ts = ""

    latest: tuple[str, str, dict] | None = None
    for event, ext in _iter_assessment_events(runtime, campaign_id):
        tv = ext.get("assessment_target_version")
        if isinstance(tv, str) and tv:
            ts = event.get("timestamp", "")
            if current_version is None or ts >= current_version_ts:
                current_version = tv
                current_version_ts = ts
        ref = ext.get("assessment_ref")
        if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref):
            raise jsonio.DataError("malformed assessment event", "hold")
        try:
            report = runtime.store.get_object(ref)
        except Exception as exc:  # noqa: BLE001
            raise jsonio.DataError("assessment report unresolved", "hold") from exc
        if not isinstance(report, dict):
            raise jsonio.DataError("assessment report not object", "hold")
        try:
            validate_assessment("assessment_report", report)
            semantic_validate_assessment("assessment_report", report, runtime.store)
        except AssessmentContractError as exc:
            raise jsonio.DataError("malformed assessment report", "hold") from exc
        if report.get("campaign_id") != campaign_id or report.get("run_id") != event.get("run_id"):
            raise jsonio.DataError("assessment report campaign/run mismatch", "hold")
        ts = event.get("timestamp", "")
        if latest is None or ts >= latest[0]:
            latest = (ts, ref, report)

    if latest is None:
        return None
    report = latest[2]
    if current_version and report.get("target_version") != current_version:
        report = dict(report)
        report["_historical"] = True
    return project_report(sanitize(report, runtime.store.secrets), view, "operator")


# ---------------------------------------------------------------------------
# project_report
# ---------------------------------------------------------------------------


def _project_cve_applicability(a) -> dict:
    if not isinstance(a, dict):
        return {"product": None, "version": None, "condition": None, "basis_refs": []}
    return {
        "product": _safe_str(a.get("product") or "", 128) or None,
        "version": _safe_str(a.get("version") or "", 128) or None,
        "condition": _safe_str(a.get("condition") or "", MAX_REASON) or None,
        "basis_refs": [r for r in (a.get("basis_refs") or []) if isinstance(r, str)],
    }


def _project_finding(f: dict) -> dict:
    classification = f.get("classification") or {}
    cwe = classification.get("cwe") or {}
    cve = classification.get("cve") or {}
    claims = f.get("claims") or []
    claim_summaries = []
    for c in claims:
        if not isinstance(c, dict):
            continue
        claim_summaries.append({
            "status": c.get("status"),
            "axis": c.get("confidence_axis"),
            "statement": _safe_str(c.get("cause_statement", ""), MAX_SUMMARY),
            "basis_refs": [r for r in (c.get("basis_refs") or []) if isinstance(r, str)],
        })
    remediation = f.get("remediation")
    validation = f.get("validation")
    return {
        "finding_id": f.get("finding_id"),
        "finding_ref": f.get("finding_ref"),
        "cause_id": f.get("cause_id"),
        "category": f.get("category"),
        "title": _safe_str(f.get("title", ""), MAX_TITLE),
        "attempts": f.get("attempts"),
        "claims": claim_summaries,
        "classification": {
            "cwe": {
                "status": cwe.get("status"),
                "ids": cwe.get("ids", []),
                "basis_refs": [r for r in (cwe.get("basis_refs") or []) if isinstance(r, str)],
            },
            "cve": {
                "status": cve.get("status"),
                "ids": cve.get("ids", []),
                "applicability": _project_cve_applicability(cve.get("applicability")),
            },
            "owasp": {
                "ids": (classification.get("owasp") or {}).get("ids", []),
                "basis_refs": [r for r in ((classification.get("owasp") or {}).get("basis_refs") or []) if isinstance(r, str)],
            },
            "attack": {
                "status": (classification.get("attack") or {}).get("status"),
                "techniques": (classification.get("attack") or {}).get("techniques", []),
                "observation_refs": [r for r in ((classification.get("attack") or {}).get("observation_refs") or []) if isinstance(r, str)],
            },
        },
        "remediation": None if not isinstance(remediation, dict) else {
            "proposal_ref": remediation.get("proposal_ref"),
            "applied": remediation.get("applied"),
            "applied_env": remediation.get("applied_env"),
            "applied_version": remediation.get("applied_version"),
            "diff_ref": remediation.get("diff_ref"),
            "side_effects": [s for s in (remediation.get("side_effects") or []) if isinstance(s, str)][:32],
        },
        "validation": None if not isinstance(validation, dict) else {
            "result": validation.get("result"),
            "oracle_verdict": validation.get("oracle_verdict"),
            "checks": {
                k: (validation.get("checks") or {}).get(k)
                for k in ("attack_blocked", "benign_continued", "related_tests_passed",
                          "oracle_evidence_ref", "version_recorded", "unverified_scope_recorded")
            },
        },
    }


def project_report(report: dict, view: str, phase: str) -> dict:
    """Pure projection of an assessment report for a view/phase."""
    if view == "blue":
        return {"status": "withheld", "reason": "評価情報はBlueに非公開"}
    if not isinstance(report, dict):
        return {"status": "none"}

    findings = [_project_finding(f) for f in (report.get("findings") or [])
                if isinstance(f, dict)]
    coverage = report.get("coverage") or {}
    categories = []
    for c in (coverage.get("categories") or []):
        if not isinstance(c, dict):
            continue
        categories.append({
            "category": _safe_str(c.get("category", "unknown"), 64),
            "status": c.get("status"),
            "reason": _safe_str(c.get("reason") or "", MAX_REASON) or None,
            "evidence_refs": [r for r in (c.get("evidence_refs") or []) if isinstance(r, str)],
        })

    cvss = []
    for r in (report.get("cvss") or []):
        if not isinstance(r, dict):
            continue
        cvss.append({
            "finding_id": r.get("finding_id"),
            "status": r.get("status"),
            "score": r.get("score"),
            "nomenclature": r.get("nomenclature"),
            "vector": _safe_str(r.get("vector") or "", MAX_REASON) or None,
            "calculator": _safe_str(r.get("calculator") or "", 128) or None,
            "metric_basis": {
                str(k): [h for h in (v or []) if isinstance(h, str)][:32]
                for k, v in (r.get("metric_basis") or {}).items()
            } if isinstance(r.get("metric_basis"), dict) else None,
            "reason": _safe_str(r.get("reason") or "", MAX_REASON),
        })

    business = []
    for r in (report.get("business_impact") or []):
        if not isinstance(r, dict):
            continue
        business.append({
            "finding_id": r.get("finding_id"),
            "data_importance": r.get("data_importance"),
            "external_exposure": r.get("external_exposure"),
            "business_stop_impact": r.get("business_stop_impact"),
            "reasons": _safe_str(r.get("reasons") or "", MAX_REASON),
            "unverified_axes": r.get("unverified_axes", []),
        })

    out = {
        "report_id": report.get("report_id"),
        "campaign_id": report.get("campaign_id"),
        "run_id": report.get("run_id"),
        "target": report.get("target"),
        "target_version": report.get("target_version"),
        "created_at": report.get("created_at"),
        "historical": bool(report.get("_historical")),
        "previous_report_ref": report.get("previous_report_ref"),
        "status": "ready",
        "coverage": {"categories": categories},
        "findings": findings,
        "cvss": cvss,
        "business_impact": business,
    }
    enrichment = report.get("enrichment")
    if isinstance(enrichment, list):
        try:
            out["enrichment"] = assessment_enrichment.project_enrichment(enrichment)
        except assessment_enrichment.EnrichmentError as exc:
            raise jsonio.DataError(
                f"enrichment projection failed: {exc}", "hold") from exc
    if phase == "blind":
        # Red/Oracle-derived info is withheld during blind phase.
        for f in out["findings"]:
            f["claims"] = []
            f["validation"] = None
    return out
