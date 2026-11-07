"""Non-LLM Oracle: predicate evaluation and FinalVerdict construction.

No LLM, no network, no subprocess.  Actual values are read from CAS;
expected and actual are compared canonically.  Bad CAS / communication
failure / incomplete -> HOLD.  FinalVerdict is derived from real CAS
reports, never from agent self-claims.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any

from .contracts import (
    ContractError,
    derive_remediation_success,
    semantic_validate,
    validate,
)
from .jsonio import DataError, canonical, digest
from .storage import Store

KINDS = ("http_status", "exit_code", "file_state", "db_state",
         "network_state", "alert_fired", "test_pass", "evidence_hash")

# expected/actual field type contracts per kind
KIND_FIELDS: dict[str, dict[str, type]] = {
    "http_status": {"method": str, "path": str, "status": int},
    "exit_code": {"code": int},
    "file_state": {"path": str, "sha256": (str, type(None)), "exists": bool},
    "db_state": {"query_id": str, "row_count": (int, type(None))},
    "network_state": {"host": str, "port": int, "reachable": bool},
    "alert_fired": {"rule_id": str, "fired": bool},
    "test_pass": {"suite_id": str, "passed": bool},
    "evidence_hash": {"ref": str},
}

_SHA_RE = re.compile(r"^[0-9a-f]{64}$")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _check_kind_fields(kind: str, value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ContractError(f"{label} must be an object", "input_invalid")
    spec = KIND_FIELDS[kind]
    if set(value.keys()) != set(spec.keys()):
        raise ContractError(f"{label} fields mismatch for {kind}", "input_invalid")
    for field, expected_type in spec.items():
        item = value[field]
        if isinstance(expected_type, tuple):
            if not isinstance(item, expected_type):
                raise ContractError(f"{label}.{field} type mismatch", "input_invalid")
        elif not isinstance(item, expected_type) or (isinstance(item, bool) and expected_type is int):
            raise ContractError(f"{label}.{field} type mismatch", "input_invalid")
    # range / format checks
    if kind == "http_status":
        status = value["status"]
        if not (100 <= status <= 599):
            raise ContractError(f"{label}.status out of range", "input_invalid")
    elif kind == "network_state":
        port = value["port"]
        if not (1 <= port <= 65535):
            raise ContractError(f"{label}.port out of range", "input_invalid")
    elif kind == "file_state":
        sha = value["sha256"]
        if sha is not None and not (isinstance(sha, str) and _SHA_RE.fullmatch(sha)):
            raise ContractError(f"{label}.sha256 must be 64 lowercase hex",
                                "input_invalid")
    elif kind == "evidence_hash":
        ref = value["ref"]
        if not (isinstance(ref, str) and _SHA_RE.fullmatch(ref)):
            raise ContractError(f"{label}.ref must be 64 lowercase hex",
                                "input_invalid")


class Oracle:
    def make_predicate(self, predicate_id: str, kind: str, input_digest: str,
                       expected: dict) -> dict:
        if kind not in KINDS:
            raise ContractError("invalid predicate kind", "input_invalid")
        _check_kind_fields(kind, expected, "expected")
        predicate = {
            "schema_version": "1.0.0",
            "predicate_id": predicate_id,
            "kind": kind,
            "input_digest": input_digest,
            "expected": expected,
            "actual": None,
            "outcome": None,
            "verdict": "HOLD",
            "reason_code": "not_executed",
            "evaluated_at": _now(),
        }
        validate("oracle_predicate", predicate)
        return predicate

    def evaluate(self, predicate: dict, store: Store,
                 completion: str = "completed") -> dict:
        validate("oracle_predicate", predicate)
        kind = predicate["kind"]
        if kind not in KINDS:
            raise ContractError("invalid predicate kind", "input_invalid")
        _check_kind_fields(kind, predicate["expected"], "expected")
        result = dict(predicate)
        result["evaluated_at"] = _now()
        if completion != "completed":
            result.update({"actual": None, "outcome": None, "verdict": "HOLD",
                           "reason_code": "not_executed"})
            return result
        try:
            actual = store.get_object(predicate["input_digest"])
        except (DataError, FileNotFoundError, KeyError):
            result.update({"actual": None, "outcome": None, "verdict": "HOLD",
                           "reason_code": "evidence_missing"})
            return result
        try:
            _check_kind_fields(kind, actual, "actual")
        except ContractError:
            result.update({"actual": None, "outcome": None, "verdict": "HOLD",
                           "reason_code": "evidence_mismatch"})
            return result
        expected = predicate["expected"]
        matched = canonical(expected) == canonical(actual)
        result["actual"] = actual
        if matched:
            result.update({"outcome": True, "verdict": "PASS",
                           "reason_code": "exploit_succeeded"})
        else:
            result.update({"outcome": False, "verdict": "FAIL",
                           "reason_code": "exploit_not_succeeded"})
        return result

    def blind_detection_check(self, telemetry_rows: list,
                              blue_findings: list,
                              store: Store) -> dict:
        """Deterministic detection-matching for the agent-driven blind phase.

        ``telemetry_rows`` is a list of ``{"ref": sha256, "data": {...}}``
        rows actually recorded during the red phase. ``blue_findings`` is
        the list of Finding records the blue agent submitted. A row
        requires detection when it shows a successful access to the
        protected record by a role other than admin. Every such row must
        be covered by at least one finding that cites the row's evidence
        reference. No LLM, no self-claims: coverage is recomputed from
        the CAS objects themselves.
        """
        if not isinstance(telemetry_rows, list):
            raise ContractError("telemetry rows must be list", "input_invalid")
        if not isinstance(blue_findings, list):
            raise ContractError("findings must be list", "input_invalid")

        def _is_unauthorized_success(row: dict) -> bool:
            data = row.get("data") if isinstance(row, dict) else None
            if not isinstance(data, dict):
                return False
            if data.get("status") != 200 or data.get("method") != "GET":
                return False
            path = data.get("path")
            role = data.get("role")
            if not isinstance(path, str):
                return False
            base = path.split("?", 1)[0]
            return (base == "/record" and isinstance(role, str)
                    and role != "admin")

        requiring = [row for row in telemetry_rows
                     if _is_unauthorized_success(row)]
        uncovered: list = []
        for row in requiring:
            ref = row.get("ref")
            data = row.get("data", {})
            covered = False
            for finding in blue_findings:
                if not isinstance(finding, dict):
                    continue
                location = finding.get("location")
                if not isinstance(location, dict):
                    continue
                if location.get("path") != "/record":
                    continue
                refs = finding.get("references")
                if isinstance(refs, list) and ref in refs:
                    covered = True
                    break
            if not covered:
                uncovered.append({
                    "ref": ref,
                    "path": data.get("path"),
                    "role": data.get("role"),
                })

        checks: list[dict] = []
        checks.append({"name": "rows_requiring_detection",
                       "passed": len(requiring) > 0})
        checks.append({"name": "all_rows_covered",
                       "passed": len(uncovered) == 0})
        passed = bool(requiring) and not uncovered
        report = {
            "rows_checked": len(telemetry_rows),
            "rows_requiring_detection": len(requiring),
            "uncovered": uncovered,
            "checks": checks,
            "passed": passed,
        }
        report_ref = store.put_object(report)
        actual = {"suite_id": "blue-agent-detection", "passed": passed}
        actual_ref = store.put_object(actual)
        predicate = self.make_predicate(
            "blue-agent-detection", "test_pass", actual_ref,
            {"suite_id": "blue-agent-detection", "passed": True})
        return {"predicate": predicate, "report_ref": report_ref,
                "report": report}

    def blind_predicate(self, predicate_id: str, red_ref: str, blue_ref: str,
                        store: Store) -> dict:
        """Independent non-LLM evaluator for blue detection.

        Reads raw CAS red/blue objects directly and derives checks from
        their contents.  Never trusts lab flags or agent self-claims.
        """
        red_obj = store.get_object(red_ref)
        if digest(red_obj) != red_ref:
            raise ContractError("red ref hash mismatch", "policy_denied")
        blue_obj = store.get_object(blue_ref)
        if digest(blue_obj) != blue_ref:
            raise ContractError("blue ref hash mismatch", "policy_denied")

        red_obs = red_obj.get("observations")
        if not isinstance(red_obs, list):
            raise ContractError("red observations must be list", "policy_denied")
        blue_obs = blue_obj.get("observations")
        if not isinstance(blue_obs, list):
            raise ContractError("blue observations must be list", "policy_denied")

        checks: list[dict] = []

        red_ok = (len(red_obs) == 3 and
                  all(isinstance(o, dict) and o.get("status") == 200
                      for o in red_obs))
        checks.append({"name": "red_baseline", "passed": bool(red_ok)})

        blue_ok = (len(blue_obs) == 3 and
                   all(isinstance(o, dict) and
                       o.get("method") == "GET" and
                       o.get("path") == "/record" and
                       o.get("status") == 200 and
                       o.get("role") == "guest"
                       for o in blue_obs))
        checks.append({"name": "blue_observations", "passed": bool(blue_ok)})

        detection_count = blue_obj.get("detection_count")
        count_ok = (isinstance(detection_count, int) and
                    not isinstance(detection_count, bool) and
                    detection_count == len(blue_obs))
        checks.append({"name": "detection_count", "passed": bool(count_ok)})

        cardinality_ok = len(red_obs) == len(blue_obs)
        checks.append({"name": "cardinality", "passed": bool(cardinality_ok)})

        raw_ok = True
        for idx in range(min(len(red_obs), len(blue_obs))):
            r = red_obs[idx]
            b = blue_obs[idx]
            if not isinstance(r, dict) or not isinstance(b, dict):
                raw_ok = False
                break
            if r.get("status") != b.get("status"):
                raw_ok = False
                break
            if r.get("method") != b.get("method"):
                raw_ok = False
                break
            rpath = r.get("path")
            bpath = b.get("path")
            if not isinstance(rpath, str) or not isinstance(bpath, str):
                raw_ok = False
                break
            if rpath.split("?", 1)[0] != bpath:
                raw_ok = False
                break
        checks.append({"name": "raw_match", "passed": bool(raw_ok)})

        passed = all(c["passed"] is True for c in checks)
        report = {
            "red_ref": red_ref,
            "blue_ref": blue_ref,
            "checks": checks,
            "passed": passed,
        }
        report_ref = store.put_object(report)
        actual = {"suite_id": "blue-detection", "passed": passed}
        actual_ref = store.put_object(actual)
        predicate = self.make_predicate(
            predicate_id, "test_pass", actual_ref,
            {"suite_id": "blue-detection", "passed": True})
        return {"predicate": predicate, "report_ref": report_ref}


def build_final_verdict(campaign_id: str, run_id: str, oracle_result: dict,
                        completion: str, store: Store,
                        remediation_id: str | None = None,
                        test_report_refs: tuple = (),
                        adapter_status: tuple = ()) -> dict:
    """Derive FinalVerdict from real CAS reports; agent self-claims rejected."""
    validate("oracle_predicate", oracle_result)
    reports = []
    for ref in test_report_refs:
        try:
            obj = store.get_object(ref)
        except (DataError, FileNotFoundError):
            raise ContractError("test report ref missing in CAS", "policy_denied")
        if digest(obj) != ref:
            raise ContractError("test report ref hash mismatch", "policy_denied")
        if obj.get("campaign_id") != campaign_id:
            raise ContractError("test report campaign mismatch", "policy_denied")
        if obj.get("run_id") != run_id:
            raise ContractError("test report run mismatch", "policy_denied")
        checks = obj.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ContractError("test report checks must be non-empty list",
                                "policy_denied")
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("passed"), bool):
                raise ContractError("test report check passed must be boolean",
                                    "policy_denied")
        if not isinstance(obj.get("passed"), bool):
            raise ContractError("test report passed must be boolean", "policy_denied")
        if obj["passed"] != all(c["passed"] for c in checks):
            raise ContractError("test report passed does not match checks",
                                "policy_denied")
        reports.append(obj)
    benign = None
    if reports:
        benign = all(r.get("passed") is True for r in reports)
    verdict = {
        "schema_version": "1.0.0",
        "campaign_id": campaign_id,
        "run_id": run_id,
        "oracle_verdict": oracle_result["verdict"],
        "oracle_reason_code": oracle_result["reason_code"],
        "run_completion": completion,
        "adapter_status": list(adapter_status),
        "cancellation": None,
        "remediation_success": False,
        "benign_regression_passed": benign,
        "remediation_id": remediation_id,
        "test_report_refs": list(test_report_refs),
        "decided_at": _now(),
    }
    verdict["remediation_success"] = derive_remediation_success(verdict)
    validate("final_verdict", verdict)
    return verdict
