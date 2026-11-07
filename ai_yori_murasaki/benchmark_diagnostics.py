"""Benchmark lab diagnostics: build assessment reports from oracle lab results."""
from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone

from . import assessment, jsonio
from .benchmark_evaluation import CATEGORIES

_CWE_BY_CATEGORY = {
    "authz": ["CWE-862"],
    "sqli": ["CWE-89"],
    "xss": ["CWE-79"],
    "path_traversal": ["CWE-22"],
    "dependency_cve": [],
}

_TITLES = {
    "authz": "認可制御の不備",
    "sqli": "SQLインジェクション",
    "xss": "クロスサイトスクリプティング",
    "path_traversal": "パストラバーサル",
    "dependency_cve": "依存関係の既知脆弱性",
}

_OBS_KEYS = {
    "authz": ["auth_guest", "auth_admin", "public"],
    "sqli": ["sql_attack", "sql_normal"],
    "path_traversal": ["path_attack", "path_normal"],
    "xss": ["xss_attack", "xss_normal"],
    "dependency_cve": ["dependency"],
}

_ATTACK_KEYS = ["auth_guest", "sql_attack", "path_attack", "xss_attack", "dependency"]
_BENIGN_KEYS = ["auth_admin", "public", "sql_normal", "path_normal", "xss_normal"]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex}"


def verify_auditor_exception(rt, run_id, lab_result, freeze_info) -> dict:
    checks = {}
    try:
        run = rt.get_run(run_id)
    except Exception:  # noqa: BLE001
        run = None
    checks["phase_verify"] = isinstance(run, dict) and run.get("phase") == "verify"
    checks["freeze_formal"] = (
        isinstance(freeze_info, dict)
        and freeze_info.get("formal") is True
        and "preflight" in freeze_info
    )
    checks["formal_verified"] = (
        isinstance(lab_result, dict) and lab_result.get("formal_verified") is True
    )
    blue = lab_result.get("blue_result") if isinstance(lab_result, dict) else None
    iso = blue.get("isolation") if isinstance(blue, dict) else None
    checks["isolation"] = (
        isinstance(iso, dict)
        and iso.get("network") == "none"
        and iso.get("readonly") is True
        and iso.get("mounts_verified") is True
    )
    observed = lab_result.get("observed") if isinstance(lab_result, dict) else None
    obs_ok = isinstance(observed, dict)
    if obs_ok:
        for cat in CATEGORIES:
            entry = observed.get(cat)
            if not isinstance(entry, dict) or not isinstance(entry.get("observed"), bool):
                obs_ok = False
                break
    checks["observed_complete"] = obs_ok
    checks["campaign_match"] = (
        isinstance(run, dict)
        and isinstance(lab_result, dict)
        and run.get("campaign_id") == lab_result.get("campaign_id")
    )
    eval_ref = None
    eval_id = None
    try:
        if isinstance(run, dict):
            manifest_ref = run.get("manifest_ref")
            if isinstance(manifest_ref, str) and manifest_ref:
                run_manifest = rt.store.get_object(manifest_ref)
                if isinstance(run_manifest, dict):
                    ref = run_manifest.get("evaluation_manifest_ref")
                    mid = run_manifest.get("evaluation_manifest_id")
                    if isinstance(ref, str) and ref:
                        eval_ref = ref
                    if isinstance(mid, str) and mid:
                        eval_id = mid
    except Exception:  # noqa: BLE001
        eval_ref = None
        eval_id = None
    checks["eval_context_recorded"] = eval_ref is not None and eval_id is not None
    frozen_ok = False
    if eval_ref is not None and eval_id is not None:
        try:
            eval_manifest = rt.store.get_object(eval_ref)
            manifest_ok = (
                isinstance(eval_manifest, dict)
                and eval_manifest.get("status") == "frozen"
                and eval_manifest.get("manifest_id") == eval_id
            )
            identity = (
                freeze_info.get("identity_manifest")
                if isinstance(freeze_info, dict) else None
            )
            identity_id = (
                identity.get("manifest_id") if isinstance(identity, dict) else None
            )
            frozen_ok = manifest_ok and identity_id == eval_id
        except Exception:  # noqa: BLE001
            frozen_ok = False
    checks["manifest_frozen_id_match"] = frozen_ok
    allowed = all(checks.values())
    return {"allowed": allowed, "checks": checks}


def compute_validation_verdict(lab_result) -> str:
    conf = lab_result.get("confusion") if isinstance(lab_result, dict) else None
    totals = conf.get("totals") if isinstance(conf, dict) else {}
    fn = totals.get("FN", 0)
    fp = totals.get("FP", 0)
    invalid = totals.get("invalid", 0)
    if (isinstance(fn, int) and fn > 0) or (isinstance(fp, int) and fp > 0):
        return "FAIL"
    if isinstance(invalid, int) and invalid > 0:
        return "undetermined"
    observed = lab_result.get("observed") if isinstance(lab_result, dict) else None
    if isinstance(observed, dict):
        for cat in CATEGORIES:
            entry = observed.get(cat)
            if not isinstance(entry, dict):
                return "undetermined"
            if not isinstance(entry.get("observed"), bool):
                return "undetermined"
            if not isinstance(entry.get("benign"), bool):
                return "undetermined"
            if entry.get("benign") is False:
                return "undetermined"
    return "PASS"


def _diag(cid, rid, target, target_version, actor, captured_at, kind, details) -> dict:
    return {
        "diagnostic_version": "1.0.0",
        "campaign_id": cid,
        "run_id": rid,
        "target": target,
        "target_version": target_version,
        "actor": actor,
        "captured_at": captured_at,
        "kind": kind,
        "details": details,
    }


def build_lab_report(rt, lab_result, freeze_info, target, target_version) -> dict:
    run = rt.get_run(lab_result["run_id"])
    if run.get("status") != "running":
        raise jsonio.DataError("run is not running", "hold")
    cid = run["campaign_id"]
    rid = lab_result["run_id"]
    actor = run["actor"]["role"]

    exc = None
    if actor == "auditor":
        exc = verify_auditor_exception(rt, rid, lab_result, freeze_info)
        if not exc["allowed"]:
            raise jsonio.DataError("auditor exception not allowed", "policy_violation")
    elif actor == "oracle":
        pass
    else:
        raise jsonio.DataError("actor not permitted", "policy_violation")

    oracle_result = lab_result.get("oracle_result")
    output = oracle_result.get("output") if isinstance(oracle_result, dict) else None
    obs_out = output.get("observations") if isinstance(output, dict) else None
    if not isinstance(obs_out, dict):
        raise jsonio.DataError("observations missing", "hold")

    captured_at = _now()
    obs_refs: dict[str, list[str]] = {}
    for cat in CATEGORIES:
        refs = []
        for key in _OBS_KEYS[cat]:
            if key not in obs_out:
                raise jsonio.DataError(f"observation {key} missing", "hold")
            diag = _diag(cid, rid, target, target_version, actor, captured_at,
                         "observation", {
                             "summary": key,
                             "source": "benchmark_lab",
                             "origin": "oracle",
                             "data": obs_out[key],
                         })
            refs.append(rt.store.put_object(diag))
        obs_refs[cat] = refs

    observed = lab_result.get("observed") or {}
    root_refs: dict[str, str] = {}
    for cat in CATEGORIES:
        entry = observed.get(cat)
        if isinstance(entry, dict) and entry.get("observed") is True:
            cause = entry.get("reason") or "observed in lab"
            diag = _diag(cid, rid, target, target_version, actor, captured_at,
                         "root_cause", {
                             "category": cat,
                             "cwe_ids": _CWE_BY_CATEGORY[cat],
                             "cause": cause,
                             "observation_refs": obs_refs[cat],
                         })
            root_refs[cat] = rt.store.put_object(diag)

    remediation_ref = rt.store.put_object(_diag(
        cid, rid, target, target_version, actor, captured_at,
        "remediation_diff", {
            "before_version": lab_result["dependency_version"],
            "after_version": "not_applied",
            "diff": "",
            "environment": "lab",
        }))

    attack_refs = [obs_refs[cat][_OBS_KEYS[cat].index(key)]
                   for cat in CATEGORIES for key in _ATTACK_KEYS
                   if key in _OBS_KEYS[cat]]
    benign_refs = [obs_refs[cat][_OBS_KEYS[cat].index(key)]
                   for cat in CATEGORIES for key in _BENIGN_KEYS
                   if key in _OBS_KEYS[cat]]
    unverified_scope = [cat for cat in CATEGORIES
                        if not (isinstance(observed.get(cat), dict)
                                and isinstance(observed[cat].get("observed"), bool))]
    verdict = compute_validation_verdict(lab_result)
    validation_ref = rt.store.put_object(_diag(
        cid, rid, target, target_version, actor, captured_at,
        "oracle_validation", {
            "remediation_ref": remediation_ref,
            "attack_observation_refs": attack_refs,
            "benign_observation_refs": benign_refs,
            "related_observation_refs": [],
            "unverified_scope": unverified_scope,
            "verdict": verdict,
        }))

    observations = []
    coverage = []
    for cat in CATEGORIES:
        entry = observed.get(cat) if isinstance(observed.get(cat), dict) else {}
        is_observed = entry.get("observed") is True
        finding_id = "f" + _sha256("\x1f".join([cid, rid, cat]))[:31]
        cause_id = _sha256("\x1f".join(["benchmark_lab", "oracle", target, cat]))
        title = _TITLES[cat]
        cwe_ids = _CWE_BY_CATEGORY[cat]
        obs_kind = "dependency_version" if cat == "dependency_cve" else "http_response"
        legacy = {
            "schema_version": "1.0.0",
            "finding_id": finding_id,
            "campaign_id": cid,
            "run_id": rid,
            "source": "benchmark_lab",
            "severity": "high",
            "title": title,
            "location": {"path": "lab", "line": None, "column": None},
            "references": [root_refs[cat]] if is_observed else [obs_refs[cat][0]],
            "extensions": {"category": cat},
        }
        finding_ref = rt.register_record("finding", legacy, run_id=rid)
        observations.append({
            "finding_id": finding_id,
            "finding_ref": finding_ref,
            "cause_id": cause_id,
            "category": cat,
            "title": title,
            "attempts": 1,
            "observations": [{
                "observation_id": _new_id("obs"),
                "finding_id": finding_id,
                "kind": obs_kind,
                "payload_ref": obs_refs[cat][0],
                "captured_at": captured_at,
            }],
            "claims": [{
                "claim_id": _new_id("clm"),
                "finding_id": finding_id,
                "cause_statement": title,
                "confidence_axis": "cause",
                "status": "confirmed" if is_observed else "hypothesis",
                "basis_refs": [root_refs[cat]] if is_observed else [obs_refs[cat][0]],
                "refuted_by": None,
            }],
            "classification": {
                "classification_id": _new_id("cls"),
                "finding_id": finding_id,
                "cwe": {
                    "status": "confirmed" if (is_observed and cwe_ids) else "unknown",
                    "ids": cwe_ids,
                    "basis_refs": [root_refs[cat]] if is_observed else [],
                },
                "cve": {
                    "status": "unknown",
                    "ids": [],
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
        if isinstance(entry.get("observed"), bool):
            coverage.append({
                "category": cat,
                "status": "executed",
                "reason": None,
                "evidence_refs": obs_refs[cat],
            })
        else:
            coverage.append({
                "category": cat,
                "status": "failed",
                "reason": entry.get("reason") or "invalid observation",
                "evidence_refs": obs_refs[cat],
            })

    context = {
        "target": target,
        "target_version": target_version,
        "actor": actor,
        "previous_report_ref": None,
        "coverage": {"categories": coverage, "created_at": captured_at},
    }
    if actor == "auditor" and exc is not None and exc.get("allowed"):
        try:
            run_manifest = rt.store.get_object(run["manifest_ref"])
        except Exception:  # noqa: BLE001
            run_manifest = None
        if isinstance(run_manifest, dict):
            eval_ref = run_manifest.get("evaluation_manifest_ref")
            if isinstance(eval_ref, str) and eval_ref:
                context["manifest_ref"] = eval_ref
    report_ref = assessment.build_report(rt, cid, rid, observations, context)

    return {
        "report_ref": report_ref,
        "diagnostic_refs": {
            "observations": obs_refs,
            "root_causes": root_refs,
            "remediation": remediation_ref,
            "validation": validation_ref,
        },
        "auditor_exception": exc if actor == "auditor" else None,
    }
