"""schema を同梱してローカルだけで検証する。"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib.resources import files
import re
from typing import Any, Callable

from jsonschema import Draft202012Validator, FormatChecker
from referencing import Registry, Resource
from referencing.exceptions import NoSuchResource

from .jsonio import ContractError, DataError, canonical, digest, loads

KINDS = ("campaign", "run", "evidence_event", "oracle_predicate",
         "finding", "remediation", "approval", "final_verdict")
FORMAT = FormatChecker()

_TIMESTAMP_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T[0-2]\d:[0-5]\d:[0-5]\d(?:\.\d{1,9})?(?:Z|[+-][0-2]\d:[0-5]\d)")


@FORMAT.checks("date-time", raises=(ValueError, TypeError))
@FORMAT.checks("aym-timestamp", raises=(ValueError, TypeError))
def _aware_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    if not _TIMESTAMP_RE.fullmatch(value):
        return False
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed.tzinfo is not None


def _no_retrieval(uri: str) -> Resource:
    raise NoSuchResource(ref=uri)


_DOCUMENTS: dict[str, dict] = {}
for _name in ("common", *KINDS):
    _raw = files("ai_yori_murasaki").joinpath(
        "schemas", "v1", f"{_name}.schema.json").read_bytes()
    _schema = loads(_raw)
    Draft202012Validator.check_schema(_schema)
    _DOCUMENTS[_name] = _schema
_REGISTRY = Registry(retrieve=_no_retrieval).with_resources(
    (doc["$id"], Resource.from_contents(doc)) for doc in _DOCUMENTS.values()
)


def load_schema(name: str) -> dict:
    if name not in _DOCUMENTS:
        raise ContractError("unknown contract", "input_invalid")
    return _DOCUMENTS[name]


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _now(ctx: dict) -> datetime:
    now = ctx.get("now")
    if now is None:
        return datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise ContractError("ctx.now must be timezone-aware datetime", "input_invalid")
    return now


def _get_object(ctx: dict) -> Callable[[str], dict]:
    getter = ctx.get("get_object")
    if getter is None:
        raise ContractError("ctx.get_object is required", "input_invalid")
    if not callable(getter):
        raise ContractError("ctx.get_object must be callable", "input_invalid")
    return getter


def _require_cas(ctx: dict, ref: str, label: str) -> dict:
    getter = _get_object(ctx)
    try:
        obj = getter(ref)
    except (KeyError, FileNotFoundError, DataError) as exc:
        raise ContractError(f"{label}: stale hash", "policy_denied") from exc
    if not isinstance(obj, dict):
        raise ContractError(f"{label}: CAS object is not an object", "policy_denied")
    if digest(obj) != ref:
        raise ContractError(f"{label}: stale hash", "policy_denied")
    return obj


def validate(kind: str, value: Any) -> None:
    if kind not in KINDS and kind != "seal":
        raise ContractError("unknown contract", "input_invalid")
    canonical(value)
    if kind == "seal":
        schema = {**_DOCUMENTS["common"], "$ref": "#/$defs/seal"}
    else:
        schema = _DOCUMENTS[kind]
    error = next(Draft202012Validator(schema, registry=_REGISTRY,
                                     format_checker=FORMAT).iter_errors(value), None)
    if error is not None:
        raise ContractError(f"{kind}: {error.validator} validation failed",
                            "input_invalid")
    if kind == "approval" and "expires_at" in value and "issued_at" in value:
        issued = _parse_timestamp(value["issued_at"])
        expires = _parse_timestamp(value["expires_at"])
        if expires <= issued:
            raise ContractError("approval expiry must follow issue time",
                                "input_invalid")


def _index_by(items: list[dict], key: str) -> dict[str, dict]:
    result: dict[str, dict] = {}
    for item in items:
        result[item[key]] = item
    return result


def _semantic_campaign(instance: dict, ctx: dict) -> None:
    now = _now(ctx)
    authorization = instance["authorization"]
    if _parse_timestamp(authorization["expires_at"]) <= now:
        raise ContractError("campaign authorization expired", "policy_denied")
    if authorization["scope_digest"] != digest(instance["scope"]):
        raise ContractError("campaign scope digest mismatch", "policy_denied")
    if _parse_timestamp(authorization["expires_at"]) <= _parse_timestamp(
            instance["created_at"]):
        raise ContractError("authorization expiry must follow creation",
                            "input_invalid")


def _semantic_run(instance: dict, ctx: dict) -> None:
    campaign = ctx.get("campaign")
    if campaign is not None and instance["campaign_id"] != campaign["campaign_id"]:
        raise ContractError("run is outside campaign", "policy_denied")
    if instance["ended_at"] is not None:
        if _parse_timestamp(instance["ended_at"]) < _parse_timestamp(
                instance["started_at"]):
            raise ContractError("run ended before start", "input_invalid")
    status = instance["status"]
    completion = instance["completion"]
    cause = instance["cause"]
    ended = instance["ended_at"]
    if status in ("pending", "running"):
        if completion is not None or cause is not None or ended is not None:
            raise ContractError("non-terminal run has terminal fields", "input_invalid")
    elif status == "completed":
        if completion != "completed" or cause != "none" or ended is None:
            raise ContractError("completed run fields inconsistent", "input_invalid")
    elif status == "cancelled":
        if completion != "cancelled" or cause != "none" or ended is None:
            raise ContractError("cancelled run fields inconsistent", "input_invalid")
    elif status == "adapter_unavailable":
        if (completion != "adapter_unavailable" or cause != "adapter_unavailable"
                or ended is None):
            raise ContractError("adapter_unavailable run fields inconsistent",
                                "input_invalid")
    elif status == "incomplete":
        if completion != "incomplete" or cause is None or ended is None:
            raise ContractError("incomplete run fields inconsistent", "input_invalid")
    _require_cas(ctx, instance["manifest_ref"], "manifest_ref")


def _event_hash(instance: dict) -> str:
    body = {k: v for k, v in instance.items() if k != "event_hash"}
    return hashlib.sha256(canonical(body) + instance["previous_event_hash"].encode("ascii")).hexdigest()


def _semantic_evidence_event(instance: dict, ctx: dict) -> None:
    if instance["sequence"] == 1:
        if instance["previous_event_hash"] != "0" * 64:
            raise ContractError("first event previous hash must be zero",
                                "policy_denied")
    if instance["event_hash"] != _event_hash(instance):
        raise ContractError("event_hash mismatch", "policy_denied")
    events = ctx.get("events")
    if events is not None:
        ordered = sorted(events, key=lambda e: e["sequence"])
        prev_hash = "0" * 64
        prev_seq = 0
        matched = False
        for event in ordered:
            if event["sequence"] != prev_seq + 1:
                raise ContractError("event sequence gap", "policy_denied")
            if event["previous_event_hash"] != prev_hash:
                raise ContractError("event chain broken", "policy_denied")
            if event["event_hash"] != _event_hash(event):
                raise ContractError("event_hash mismatch", "policy_denied")
            if (event["event_hash"] == instance["event_hash"]
                    and event["sequence"] == instance["sequence"]
                    and event["previous_event_hash"] == instance["previous_event_hash"]):
                matched = True
            prev_hash = event["event_hash"]
            prev_seq = event["sequence"]
        if not matched:
            raise ContractError("event not present in ctx.events", "policy_denied")
    for ref in instance["evidence_refs"]:
        _require_cas(ctx, ref, "evidence_refs")
    finding = instance.get("finding")
    if finding is not None:
        findings = ctx.get("findings")
        if findings is None:
            raise ContractError("ctx.findings is required", "input_invalid")
        match = next((f for f in findings
                      if f["finding_id"] == finding["finding_id"]
                      and f["campaign_id"] == instance["campaign_id"]
                      and f["run_id"] == instance["run_id"]), None)
        if match is None:
            raise ContractError("embedded finding not found", "policy_denied")
        if match["severity"] != finding["severity"] or match["title"] != finding["title"]:
            raise ContractError("embedded finding mismatch", "policy_denied")


def _semantic_oracle_predicate(instance: dict, ctx: dict) -> None:
    verdict = instance["verdict"]
    outcome = instance["outcome"]
    reason = instance["reason_code"]
    if verdict == "PASS":
        if outcome is not True:
            raise ContractError("PASS requires outcome true", "input_invalid")
    elif verdict == "FAIL":
        if outcome is not False:
            raise ContractError("FAIL requires outcome false", "input_invalid")
    elif verdict == "HOLD":
        if outcome is not None:
            raise ContractError("HOLD requires outcome null", "input_invalid")
        if reason not in ("execution_error", "evidence_missing", "evidence_mismatch",
                          "not_executed", "adapter_unavailable", "cancelled"):
            raise ContractError("HOLD reason_code invalid", "input_invalid")


def _semantic_finding(instance: dict, ctx: dict) -> None:
    campaign = ctx.get("campaign")
    if campaign is not None and instance["campaign_id"] != campaign["campaign_id"]:
        raise ContractError("finding is outside campaign", "policy_denied")


def _semantic_remediation(instance: dict, ctx: dict) -> None:
    findings = ctx.get("findings")
    if findings is None:
        raise ContractError("ctx.findings is required", "input_invalid")
    match = next((f for f in findings
                  if f["finding_id"] == instance["finding_id"]
                  and f["campaign_id"] == instance["campaign_id"]
                  and f["run_id"] == instance["run_id"]), None)
    if match is None:
        raise ContractError("remediation finding not found", "policy_denied")
    _require_cas(ctx, instance["patch_bundle_ref"], "patch_bundle_ref")
    for field in ("static_scan_result", "unit_result", "integration_result",
                  "oracle_replay_result", "benign_regression_result"):
        _require_cas(ctx, instance[field]["ref"], f"{field}.ref")


def _semantic_approval(instance: dict, ctx: dict) -> None:
    now = _now(ctx)
    remediations = ctx.get("remediations")
    if remediations is None:
        raise ContractError("ctx.remediations is required", "input_invalid")
    match = next((r for r in remediations
                  if r["remediation_id"] == instance["remediation_id"]), None)
    if match is None:
        raise ContractError("approval remediation not found", "policy_denied")
    if _parse_timestamp(instance["expires_at"]) <= now:
        raise ContractError("approval expired", "policy_denied")
    if instance["decision"] == "approved":
        if instance["decided_at"] is None:
            raise ContractError("approved requires decided_at", "input_invalid")
        if _parse_timestamp(instance["expires_at"]) <= _parse_timestamp(
                instance["decided_at"]):
            raise ContractError("approval expiry must follow decision", "input_invalid")
    required = {"unit-tests", "integration-tests", "trivy", "codeql",
                "evidence-verify", "oracle-regression", "red-replay", "human-review"}
    names = {check["name"] for check in instance["required_checks"]}
    if not required.issubset(names):
        raise ContractError("approval missing required checks", "input_invalid")
    for check in instance["required_checks"]:
        obj = _require_cas(ctx, check["ref"], f"required_checks[{check['name']}].ref")
        if "passed" in obj and obj["passed"] != check["passed"]:
            raise ContractError(
                f"required_checks[{check['name']}]: passed mismatch",
                "policy_denied")
    if instance["decision"] == "approved":
        if not all(check["passed"] for check in instance["required_checks"]):
            raise ContractError("approved requires all checks passed", "policy_denied")


def derive_remediation_success(instance: dict) -> bool:
    """FinalVerdict の remediation_success を独立に導出する。"""
    return bool(
        instance["oracle_verdict"] == "FAIL"
        and instance["oracle_reason_code"] == "exploit_not_succeeded"
        and instance["run_completion"] == "completed"
        and instance["benign_regression_passed"] is True
        and instance["remediation_id"] is not None
        and len(instance["test_report_refs"]) > 0
    )


def _semantic_final_verdict(instance: dict, ctx: dict) -> None:
    remediations = ctx.get("remediations")
    if instance["remediation_id"] is not None:
        if remediations is None:
            raise ContractError("ctx.remediations is required", "input_invalid")
        match = next((r for r in remediations
                      if r["remediation_id"] == instance["remediation_id"]
                      and r["campaign_id"] == instance["campaign_id"]), None)
        if match is None:
            raise ContractError("final verdict remediation not found", "policy_denied")
    for ref in instance["test_report_refs"]:
        obj = _require_cas(ctx, ref, "test_report_refs")
        if obj.get("campaign_id") != instance["campaign_id"]:
            raise ContractError("test report campaign mismatch", "policy_denied")
        if obj.get("run_id") != instance["run_id"]:
            raise ContractError("test report run mismatch", "policy_denied")
        if not isinstance(obj.get("passed"), bool):
            raise ContractError("test report passed must be boolean", "policy_denied")
        checks = obj.get("checks")
        if not isinstance(checks, list) or not checks:
            raise ContractError("test report checks must be non-empty list",
                                "policy_denied")
        for check in checks:
            if not isinstance(check, dict) or not isinstance(check.get("passed"), bool):
                raise ContractError("test report check passed must be boolean",
                                    "policy_denied")
        if obj["passed"] != all(check["passed"] for check in checks):
            raise ContractError("test report passed does not match checks",
                                "policy_denied")
    if instance["benign_regression_passed"] is not None:
        reports = [_require_cas(ctx, ref, "test_report_refs")
                   for ref in instance["test_report_refs"]]
        if instance["benign_regression_passed"] != all(
                r.get("passed") is True for r in reports):
            raise ContractError("benign_regression_passed does not match reports",
                                "policy_denied")
    expected = derive_remediation_success(instance)
    if instance["remediation_success"] != expected:
        raise ContractError("remediation_success does not match derivation",
                            "policy_denied")
    if instance["benign_regression_passed"] is True:
        oracle = ctx.get("oracle_evaluation")
        if oracle is None:
            raise ContractError("ctx.oracle_evaluation is required", "input_invalid")
        if not isinstance(oracle, dict):
            raise ContractError("oracle evaluation must be an object", "policy_denied")
        validate("oracle_predicate", oracle)
        _semantic_oracle_predicate(oracle, ctx)
        input_ref = oracle["input_digest"]
        cas_obj = _require_cas(ctx, input_ref, "oracle input_digest")
        if canonical(cas_obj) != canonical(oracle["actual"]):
            raise ContractError("oracle actual does not match input digest",
                                "policy_denied")
        expected = oracle["expected"]
        actual = oracle["actual"]
        recomputed = canonical(expected) == canonical(actual)
        if oracle["outcome"] is not recomputed:
            raise ContractError("oracle outcome does not match expected/actual",
                                "policy_denied")
        if recomputed:
            if oracle["verdict"] != "PASS":
                raise ContractError("oracle verdict inconsistent with outcome",
                                    "policy_denied")
        else:
            if oracle["verdict"] != "FAIL":
                raise ContractError("oracle verdict inconsistent with outcome",
                                    "policy_denied")
        if instance["oracle_verdict"] != oracle["verdict"]:
            raise ContractError("final verdict oracle_verdict mismatch",
                                "policy_denied")
        if instance["oracle_reason_code"] != oracle["reason_code"]:
            raise ContractError("final verdict oracle_reason_code mismatch",
                                "policy_denied")


_SEMANTIC = {
    "campaign": _semantic_campaign,
    "run": _semantic_run,
    "evidence_event": _semantic_evidence_event,
    "oracle_predicate": _semantic_oracle_predicate,
    "finding": _semantic_finding,
    "remediation": _semantic_remediation,
    "approval": _semantic_approval,
    "final_verdict": _semantic_final_verdict,
}


def semantic_validate(name: str, instance: dict, ctx: dict) -> None:
    """cross-campaign / stale hash / expiry を検査する。

    ctx: {campaign, runs, findings, remediations, events, oracle_evaluation,
          get_object: callable(ref)->dict, now?: datetime}
    """
    if name not in _SEMANTIC:
        raise ContractError("unknown contract", "input_invalid")
    if not isinstance(ctx, dict):
        raise ContractError("ctx must be a dict", "input_invalid")
    validate(name, instance)
    _SEMANTIC[name](instance, ctx)


def validate_campaign_runs(campaign: dict, runs: list[dict]) -> None:
    validate("campaign", campaign)
    targets = [t["identifier"] for t in campaign["scope"]["targets"]]
    if len(set(targets)) != len(targets):
        raise ContractError("duplicate target identifier", "input_invalid")
    ids: set[str] = set()
    workers: dict[str, str] = {}
    for run in runs:
        validate("run", run)
        if run["campaign_id"] != campaign["campaign_id"]:
            raise ContractError("run is outside campaign", "policy_denied")
        if run["run_id"] in ids:
            raise ContractError("duplicate run identifier", "input_invalid")
        ids.add(run["run_id"])
        role = run["actor"]["role"]
        worker_id = run["identity"]["worker_id"]
        if worker_id is not None:
            if worker_id in workers and workers[worker_id] != role:
                raise ContractError("role namespaces overlap", "policy_denied")
            workers[worker_id] = role
