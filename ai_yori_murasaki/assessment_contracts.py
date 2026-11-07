"""Assessment contract validation (SPEC-ASSESSMENT).

Independent of contracts.validate; only assessment_report is registered here.
"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone

from .jsonio import DataError
from .advisories import validate_advisory, match_version
from .assessment_enrichment import EnrichmentError as _EnrichmentError


class AssessmentContractError(DataError):
    """Raised when an assessment object violates its contract."""


ASSESSMENT_KINDS = {"assessment_report"}
_MAX_DEPTH = 12

_SCHEMA_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "schemas", "v1", "assessment.schema.json",
)

_CAS_RE = re.compile(r"^[0-9a-f]{64}$")
_REPORT_ID_RE = re.compile(r"^asc-[0-9a-f]{32}$")
_ISO_RE = re.compile(
    r"^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}"
    r"(\.[0-9]+)?(Z|[+-][0-9]{2}:[0-9]{2})$"
)
_CWE_RE = re.compile(r"^CWE-[0-9]+$")
_CVE_RE = re.compile(r"^CVE-[0-9]{4}-[0-9]{4,}$")
_ATTACK_RE = re.compile(r"^T[0-9]{4}(\.[0-9]{3})?$")

_DIAGNOSTIC_KINDS = {
    "observation",
    "root_cause",
    "cve_applicability",
    "attack_observation",
    "remediation_diff",
    "oracle_validation",
}

_DIAGNOSTIC_KEYS = {
    "diagnostic_version",
    "campaign_id",
    "run_id",
    "target",
    "target_version",
    "actor",
    "captured_at",
    "kind",
    "details",
}

_DETAIL_KEYS = {
    "observation": {"summary", "source", "origin", "data"},
    "root_cause": {"category", "cwe_ids", "cause", "observation_refs"},
    "cve_applicability": {
        "advisory_ref", "package", "version", "condition", "observation_refs"
    },
    "attack_observation": {"technique_id", "behavior", "observation_refs"},
    "remediation_diff": {
        "before_version", "after_version", "diff", "environment"
    },
    "oracle_validation": {
        "remediation_ref",
        "attack_observation_refs",
        "benign_observation_refs",
        "related_observation_refs",
        "unverified_scope",
        "verdict",
    },
}

_ORIGINS = {"scanner", "blue", "red", "oracle", "operator"}
_VERDICTS = {"PASS", "FAIL", "undetermined"}

_schema_cache = None


def _load_schema():
    global _schema_cache
    if _schema_cache is None:
        try:
            with open(_SCHEMA_PATH, "r", encoding="utf-8") as fh:
                _schema_cache = json.load(fh)
        except (OSError, ValueError) as exc:
            raise AssessmentContractError(
                f"assessment schema unavailable: {exc}"
            ) from exc
    return _schema_cache


def _fail(msg):
    raise AssessmentContractError(msg)


def _is_str(v):
    return isinstance(v, str) and v != ""


def _is_cas(v):
    return isinstance(v, str) and bool(_CAS_RE.match(v))


def _is_iso(v):
    if not isinstance(v, str) or not _ISO_RE.match(v):
        return False
    try:
        datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return False
    return True


def _is_int(v):
    return isinstance(v, int) and not isinstance(v, bool)


def _is_num(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _check_keys(obj, required, where):
    if not isinstance(obj, dict):
        _fail(f"{where}: expected object")
    missing = required - set(obj)
    if missing:
        _fail(f"{where}: missing keys {sorted(missing)}")
    extra = set(obj) - required
    if extra:
        _fail(f"{where}: unknown keys {sorted(extra)}")


def _check_ref_list(v, where, allow_empty=True):
    if not isinstance(v, list):
        _fail(f"{where}: expected list")
    if not allow_empty and not v:
        _fail(f"{where}: must not be empty")
    for i, ref in enumerate(v):
        if not _is_cas(ref):
            _fail(f"{where}[{i}]: invalid CAS ref")


def _check_str_list(v, where, pattern=None, allow_empty=True):
    if not isinstance(v, list):
        _fail(f"{where}: expected list")
    if not allow_empty and not v:
        _fail(f"{where}: must not be empty")
    for i, item in enumerate(v):
        if not _is_str(item):
            _fail(f"{where}[{i}]: expected non-empty string")
        if pattern is not None and not pattern.match(item):
            _fail(f"{where}[{i}]: invalid format {item!r}")


def _check_opt_str(v, where):
    if v is not None and not _is_str(v):
        _fail(f"{where}: expected non-empty string or null")


def _check_opt_cas(v, where):
    if v is not None and not _is_cas(v):
        _fail(f"{where}: expected CAS ref or null")


def _check_opt_enum(v, allowed, where):
    if v is not None and v not in allowed:
        _fail(f"{where}: invalid value {v!r}")


def _check_enum(v, allowed, where):
    if v not in allowed:
        _fail(f"{where}: invalid value {v!r}")


def _check_bool(v, where):
    if not isinstance(v, bool):
        _fail(f"{where}: expected boolean")


def _check_observation(obj, where):
    _check_keys(
        obj,
        {"observation_id", "finding_id", "kind", "payload_ref", "captured_at"},
        where,
    )
    if not _is_str(obj["observation_id"]):
        _fail(f"{where}.observation_id: invalid")
    if not _is_str(obj["finding_id"]):
        _fail(f"{where}.finding_id: invalid")
    _check_enum(
        obj["kind"],
        {"http_response", "file_state", "scanner_output", "dependency_version"},
        f"{where}.kind",
    )
    if not _is_cas(obj["payload_ref"]):
        _fail(f"{where}.payload_ref: invalid CAS ref")
    if not _is_iso(obj["captured_at"]):
        _fail(f"{where}.captured_at: invalid ISO8601")


def _check_claim(obj, where):
    _check_keys(
        obj,
        {
            "claim_id", "finding_id", "cause_statement", "confidence_axis",
            "status", "basis_refs", "refuted_by",
        },
        where,
    )
    for k in ("claim_id", "finding_id", "cause_statement"):
        if not _is_str(obj[k]):
            _fail(f"{where}.{k}: invalid")
    _check_enum(
        obj["confidence_axis"],
        {"detection", "cause", "impact", "severity"},
        f"{where}.confidence_axis",
    )
    status = obj["status"]
    _check_enum(status, {"confirmed", "hypothesis", "refuted"}, f"{where}.status")
    _check_ref_list(obj["basis_refs"], f"{where}.basis_refs")
    _check_opt_cas(obj["refuted_by"], f"{where}.refuted_by")
    if status == "confirmed":
        if not obj["basis_refs"]:
            _fail(f"{where}: confirmed claim requires basis_refs")
        if obj["refuted_by"] is not None:
            _fail(f"{where}: confirmed claim cannot be refuted")
    if status == "refuted" and obj["refuted_by"] is None:
        _fail(f"{where}: refuted claim requires refuted_by")


def _check_classification(obj, where):
    _check_keys(
        obj,
        {"classification_id", "finding_id", "cwe", "cve", "owasp", "attack"},
        where,
    )
    for k in ("classification_id", "finding_id"):
        if not _is_str(obj[k]):
            _fail(f"{where}.{k}: invalid")

    cwe = obj["cwe"]
    _check_keys(cwe, {"status", "ids", "basis_refs"}, f"{where}.cwe")
    _check_enum(cwe["status"], {"confirmed", "candidate", "unknown"}, f"{where}.cwe.status")
    _check_str_list(cwe["ids"], f"{where}.cwe.ids", _CWE_RE)
    _check_ref_list(cwe["basis_refs"], f"{where}.cwe.basis_refs")
    if cwe["status"] == "confirmed":
        if not cwe["ids"] or not cwe["basis_refs"]:
            _fail(f"{where}.cwe: confirmed requires ids and basis_refs")

    cve = obj["cve"]
    _check_keys(cve, {"status", "ids", "applicability"}, f"{where}.cve")
    _check_enum(
        cve["status"],
        {"confirmed", "candidate", "not_applicable", "unknown"},
        f"{where}.cve.status",
    )
    _check_str_list(cve["ids"], f"{where}.cve.ids", _CVE_RE)
    app = cve["applicability"]
    _check_keys(app, {"product", "version", "condition", "basis_refs"}, f"{where}.cve.applicability")
    for k in ("product", "version", "condition"):
        _check_opt_str(app[k], f"{where}.cve.applicability.{k}")
    _check_ref_list(app["basis_refs"], f"{where}.cve.applicability.basis_refs")
    if cve["status"] == "confirmed":
        if not cve["ids"]:
            _fail(f"{where}.cve: confirmed requires ids")
        for k in ("product", "version", "condition"):
            if app[k] is None:
                _fail(f"{where}.cve: confirmed requires applicability.{k}")
        if not app["basis_refs"]:
            _fail(f"{where}.cve: confirmed requires applicability.basis_refs")

    owasp = obj["owasp"]
    _check_keys(owasp, {"ids", "basis_refs"}, f"{where}.owasp")
    _check_str_list(owasp["ids"], f"{where}.owasp.ids")
    _check_ref_list(owasp["basis_refs"], f"{where}.owasp.basis_refs")

    attack = obj["attack"]
    _check_keys(attack, {"status", "techniques", "observation_refs"}, f"{where}.attack")
    _check_enum(attack["status"], {"observed", "unknown"}, f"{where}.attack.status")
    _check_str_list(attack["techniques"], f"{where}.attack.techniques", _ATTACK_RE)
    _check_ref_list(attack["observation_refs"], f"{where}.attack.observation_refs")
    if attack["status"] == "observed":
        if not attack["techniques"] or not attack["observation_refs"]:
            _fail(f"{where}.attack: observed requires techniques and observation_refs")


def _check_remediation(obj, where):
    _check_keys(
        obj,
        {
            "remediation_id", "finding_id", "proposal_ref", "applied",
            "applied_env", "applied_version", "diff_ref", "side_effects",
        },
        where,
    )
    for k in ("remediation_id", "finding_id"):
        if not _is_str(obj[k]):
            _fail(f"{where}.{k}: invalid")
    if not _is_cas(obj["proposal_ref"]):
        _fail(f"{where}.proposal_ref: invalid CAS ref")
    _check_bool(obj["applied"], f"{where}.applied")
    _check_opt_str(obj["applied_env"], f"{where}.applied_env")
    _check_opt_str(obj["applied_version"], f"{where}.applied_version")
    _check_opt_cas(obj["diff_ref"], f"{where}.diff_ref")
    _check_str_list(obj["side_effects"], f"{where}.side_effects")
    if obj["applied"]:
        if obj["applied_env"] is None or obj["applied_version"] is None:
            _fail(f"{where}: applied requires applied_env and applied_version")
        if obj["diff_ref"] is None:
            _fail(f"{where}: applied requires diff_ref")


def _check_validation(obj, where):
    _check_keys(
        obj,
        {"validation_id", "remediation_id", "checks", "oracle_verdict", "result"},
        where,
    )
    for k in ("validation_id", "remediation_id"):
        if not _is_str(obj[k]):
            _fail(f"{where}.{k}: invalid")
    checks = obj["checks"]
    _check_keys(
        checks,
        {
            "attack_blocked", "benign_continued", "related_tests_passed",
            "oracle_evidence_ref", "version_recorded", "unverified_scope_recorded",
        },
        f"{where}.checks",
    )
    for k in (
        "attack_blocked", "benign_continued", "related_tests_passed",
        "version_recorded", "unverified_scope_recorded",
    ):
        _check_bool(checks[k], f"{where}.checks.{k}")
    if not _is_cas(checks["oracle_evidence_ref"]):
        _fail(f"{where}.checks.oracle_evidence_ref: invalid CAS ref")
    verdict = obj["oracle_verdict"]
    _check_enum(verdict, _VERDICTS, f"{where}.oracle_verdict")
    result = obj["result"]
    _check_enum(result, {"success", "failed", "undetermined"}, f"{where}.result")
    if verdict == "undetermined" and result != "undetermined":
        _fail(f"{where}: undetermined verdict requires undetermined result")
    if result == "success":
        if verdict != "PASS":
            _fail(f"{where}: success requires PASS verdict")
        for k in (
            "attack_blocked", "benign_continued", "related_tests_passed",
            "version_recorded", "unverified_scope_recorded",
        ):
            if checks[k] is not True:
                _fail(f"{where}: success requires checks.{k}=true")


def _check_finding_assessment(obj, where):
    _check_keys(
        obj,
        {
            "finding_id", "finding_ref", "cause_id", "category", "title",
            "attempts", "observations", "claims", "classification",
            "remediation", "validation",
        },
        where,
    )
    for k in ("finding_id", "title"):
        if not _is_str(obj[k]):
            _fail(f"{where}.{k}: invalid")
    if not _is_cas(obj["finding_ref"]):
        _fail(f"{where}.finding_ref: invalid CAS ref")
    if not _is_cas(obj["cause_id"]):
        _fail(f"{where}.cause_id: invalid cause id")
    _check_enum(
        obj["category"],
        {"authz", "sqli", "xss", "path_traversal", "dependency_cve", "unknown"},
        f"{where}.category",
    )
    if not _is_int(obj["attempts"]) or obj["attempts"] < 1:
        _fail(f"{where}.attempts: must be integer >= 1")
    if not isinstance(obj["observations"], list):
        _fail(f"{where}.observations: expected list")
    for i, o in enumerate(obj["observations"]):
        _check_observation(o, f"{where}.observations[{i}]")
    if not isinstance(obj["claims"], list):
        _fail(f"{where}.claims: expected list")
    for i, c in enumerate(obj["claims"]):
        _check_claim(c, f"{where}.claims[{i}]")
    _check_classification(obj["classification"], f"{where}.classification")
    if obj["remediation"] is not None:
        _check_remediation(obj["remediation"], f"{where}.remediation")
    if obj["validation"] is not None:
        _check_validation(obj["validation"], f"{where}.validation")


def _check_cvss(obj, where):
    _check_keys(
        obj,
        {
            "finding_id", "status", "vector", "score", "nomenclature",
            "calculator", "metric_basis", "reason",
        },
        where,
    )
    if not _is_str(obj["finding_id"]):
        _fail(f"{where}.finding_id: invalid")
    status = obj["status"]
    _check_enum(status, {"scored", "invalid", "unevaluated"}, f"{where}.status")
    if obj["calculator"] != "cvss/3.6":
        _fail(f"{where}.calculator: must be 'cvss/3.6'")
    mb = obj["metric_basis"]
    if not isinstance(mb, dict):
        _fail(f"{where}.metric_basis: expected object")
    for k, refs in mb.items():
        if not _is_str(k):
            _fail(f"{where}.metric_basis: invalid metric key")
        _check_ref_list(refs, f"{where}.metric_basis.{k}")
    if not isinstance(obj["reason"], str):
        _fail(f"{where}.reason: expected string")
    if status == "scored":
        if not _is_str(obj["vector"]):
            _fail(f"{where}.vector: required for scored")
        if not _is_num(obj["score"]) or not (0.0 <= float(obj["score"]) <= 10.0):
            _fail(f"{where}.score: must be 0.0..10.0")
        _check_enum(
            obj["nomenclature"],
            {"CVSS-B", "CVSS-BT", "CVSS-BE", "CVSS-BTE"},
            f"{where}.nomenclature",
        )
        if obj["reason"] != "":
            _fail(f"{where}.reason: must be empty for scored")
    else:
        if obj["vector"] is not None:
            _fail(f"{where}.vector: must be null for {status}")
        if obj["score"] is not None:
            _fail(f"{where}.score: must be null for {status}")
        if obj["nomenclature"] is not None:
            _fail(f"{where}.nomenclature: must be null for {status}")
        if not obj["reason"]:
            _fail(f"{where}.reason: required for {status}")


def _check_business_impact(obj, where):
    _check_keys(
        obj,
        {
            "finding_id", "data_importance", "external_exposure",
            "business_stop_impact", "reasons", "author", "recorded_at",
            "unverified_axes",
        },
        where,
    )
    if not _is_str(obj["finding_id"]):
        _fail(f"{where}.finding_id: invalid")
    levels = {"high", "medium", "low", "unknown"}
    for k in ("data_importance", "external_exposure", "business_stop_impact"):
        _check_enum(obj[k], levels, f"{where}.{k}")
    if not isinstance(obj["reasons"], str):
        _fail(f"{where}.reasons: expected string")
    if not _is_str(obj["author"]):
        _fail(f"{where}.author: invalid")
    if not _is_iso(obj["recorded_at"]):
        _fail(f"{where}.recorded_at: invalid ISO8601")
    axes = obj["unverified_axes"]
    if not isinstance(axes, list):
        _fail(f"{where}.unverified_axes: expected list")
    seen = set()
    for i, a in enumerate(axes):
        _check_enum(
            a,
            {"data_importance", "external_exposure", "business_stop_impact"},
            f"{where}.unverified_axes[{i}]",
        )
        if a in seen:
            _fail(f"{where}.unverified_axes: duplicate {a}")
        seen.add(a)


def _check_coverage(obj, where):
    _check_keys(obj, {"categories", "created_at"}, where)
    if not _is_iso(obj["created_at"]):
        _fail(f"{where}.created_at: invalid ISO8601")
    cats = obj["categories"]
    if not isinstance(cats, list):
        _fail(f"{where}.categories: expected list")
    for i, c in enumerate(cats):
        cw = f"{where}.categories[{i}]"
        _check_keys(c, {"category", "status", "reason", "evidence_refs"}, cw)
        if not _is_str(c["category"]):
            _fail(f"{cw}.category: invalid")
        _check_enum(c["status"], {"executed", "not_executed", "failed"}, f"{cw}.status")
        if c["reason"] is not None and not isinstance(c["reason"], str):
            _fail(f"{cw}.reason: expected string or null")
        if c["status"] in ("not_executed", "failed"):
            if not c["reason"]:
                _fail(f"{cw}.reason: required for {c['status']}")
        _check_ref_list(c["evidence_refs"], f"{cw}.evidence_refs")


def validate_assessment(kind, obj):
    """Structural validation of an assessment object against its schema."""
    if kind not in ASSESSMENT_KINDS:
        _fail(f"unsupported assessment kind: {kind!r}")
    if not isinstance(obj, dict):
        _fail("assessment_report: expected object")
    try:
        import jsonschema
    except ImportError as exc:  # pragma: no cover
        raise AssessmentContractError("jsonschema is required") from exc
    schema = _load_schema()
    try:
        jsonschema.validate(instance=obj, schema=schema)
    except jsonschema.ValidationError as exc:
        path = "/".join(str(p) for p in exc.absolute_path)
        _fail(f"assessment_report: {path or '<root>'}: {exc.message}")
    except jsonschema.SchemaError as exc:  # pragma: no cover
        raise AssessmentContractError(f"assessment schema invalid: {exc}") from exc
    return True


def _resolve(store, ref, where):
    if not _is_cas(ref):
        _fail(f"{where}: invalid CAS ref")
    try:
        return store.get_object(ref)
    except Exception as exc:
        raise AssessmentContractError(f"{where}: unresolved ref {ref}") from exc


def _resolve_all(store, refs, where):
    for i, ref in enumerate(refs):
        _resolve(store, ref, f"{where}[{i}]")


def _resolve_diag(store, ref, where, _depth=0, _seen=None):
    """Resolve a ref and require it to be a diagnostic record.

    Structural validation is performed first (without store access) to avoid
    recursion cycles. Observation refs are then resolved recursively, bounded
    by _MAX_DEPTH, requiring same campaign/target/target_version scope.
    """
    if _depth > _MAX_DEPTH:
        _fail(f"{where}: diagnostic reference depth exceeded")
    if _seen is None:
        _seen = set()
    obj = _resolve(store, ref, where)
    if not isinstance(obj, dict) or obj.get("kind") not in _DIAGNOSTIC_KINDS:
        _fail(f"{where}: resolved object is not a diagnostic")
    if "diagnostic_version" not in obj:
        _fail(f"{where}: resolved object is not a diagnostic")
    # Structural validation first, without store, to avoid cycles.
    validate_diagnostic(obj, None)
    # Verify real run scope.
    _validate_run_scope(obj, store)
    # Recursively resolve observation refs.
    for field in (
        "observation_refs",
        "attack_observation_refs",
        "benign_observation_refs",
        "related_observation_refs",
    ):
        refs = obj.get(field)
        if not refs:
            continue
        if not isinstance(refs, list):
            _fail(f"{where}: {field} must be a list")
        for i, sub_ref in enumerate(refs):
            sub_where = f"{where}.{field}[{i}]"
            if sub_ref in _seen:
                continue
            _seen.add(sub_ref)
            sub_obj = _resolve(store, sub_ref, sub_where)
            if not isinstance(sub_obj, dict) or sub_obj.get("kind") != "observation":
                _fail(f"{sub_where}: resolved object is not an observation")
            if sub_obj.get("campaign_id") != obj.get("campaign_id"):
                _fail(f"{sub_where}: campaign scope mismatch")
            if sub_obj.get("target") != obj.get("target"):
                _fail(f"{sub_where}: target scope mismatch")
            if sub_obj.get("target_version") != obj.get("target_version"):
                _fail(f"{sub_where}: target_version scope mismatch")
            _seen.discard(sub_ref)
    return obj


def _check_diag_scope(diag, report, where):
    if diag.get("campaign_id") != report["campaign_id"]:
        _fail(f"{where}: campaign scope mismatch")
    if diag.get("target") != report["target"]:
        _fail(f"{where}: target scope mismatch")
    if diag.get("target_version") != report["target_version"]:
        _fail(f"{where}: target_version scope mismatch")


def _check_not_future(iso, where):
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        _fail(f"{where}: invalid ISO8601")
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    if dt > datetime.now(timezone.utc):
        _fail(f"{where}: timestamp is in the future")


def _diag_scope(obj, where):
    for k in ("campaign_id", "run_id", "target", "target_version", "actor"):
        if not _is_str(obj.get(k)):
            _fail(f"{where}.{k}: invalid")
    if not _is_iso(obj.get("captured_at")):
        _fail(f"{where}.captured_at: invalid ISO8601")


def _check_diag_details(kind, details, where):
    if not isinstance(details, dict):
        _fail(f"{where}: expected object")
    expected = _DETAIL_KEYS[kind]
    missing = expected - set(details)
    if missing:
        _fail(f"{where}: missing keys {sorted(missing)}")
    extra = set(details) - expected
    if extra:
        _fail(f"{where}: unknown keys {sorted(extra)}")
    if kind == "observation":
        if not _is_str(details["summary"]):
            _fail(f"{where}.summary: invalid")
        if not _is_str(details["source"]):
            _fail(f"{where}.source: invalid")
        _check_enum(details["origin"], _ORIGINS, f"{where}.origin")
        if not isinstance(details["data"], (dict, list, str, int, float, bool)) and details["data"] is not None:
            _fail(f"{where}.data: unsupported type")
    elif kind == "root_cause":
        _check_enum(
            details["category"],
            {"authz", "sqli", "xss", "path_traversal", "dependency_cve", "unknown"},
            f"{where}.category",
        )
        _check_str_list(details["cwe_ids"], f"{where}.cwe_ids", _CWE_RE)
        if not _is_str(details["cause"]):
            _fail(f"{where}.cause: invalid")
        _check_ref_list(details["observation_refs"], f"{where}.observation_refs", allow_empty=False)
    elif kind == "cve_applicability":
        if not _is_cas(details["advisory_ref"]):
            _fail(f"{where}.advisory_ref: invalid CAS ref")
        for k in ("package", "version", "condition"):
            if not _is_str(details[k]):
                _fail(f"{where}.{k}: invalid")
        _check_ref_list(details["observation_refs"], f"{where}.observation_refs", allow_empty=False)
    elif kind == "attack_observation":
        if not _is_str(details["technique_id"]) or not _ATTACK_RE.match(details["technique_id"]):
            _fail(f"{where}.technique_id: invalid")
        if not _is_str(details["behavior"]):
            _fail(f"{where}.behavior: invalid")
        _check_ref_list(details["observation_refs"], f"{where}.observation_refs", allow_empty=False)
    elif kind == "remediation_diff":
        for k in ("before_version", "after_version", "diff", "environment"):
            if not _is_str(details[k]):
                _fail(f"{where}.{k}: invalid")
    elif kind == "oracle_validation":
        if not _is_cas(details["remediation_ref"]):
            _fail(f"{where}.remediation_ref: invalid CAS ref")
        for k in ("attack_observation_refs", "benign_observation_refs", "related_observation_refs"):
            _check_ref_list(details[k], f"{where}.{k}", allow_empty=False)
        if not isinstance(details["unverified_scope"], list):
            _fail(f"{where}.unverified_scope: expected list")
        for i, s in enumerate(details["unverified_scope"]):
            if not _is_str(s):
                _fail(f"{where}.unverified_scope[{i}]: invalid")
        _check_enum(details["verdict"], _VERDICTS, f"{where}.verdict")


def validate_diagnostic(obj, store=None):
    """Validate a diagnostic record (closed shape) and optionally resolve refs."""
    if not isinstance(obj, dict):
        _fail("diagnostic: expected object")
    missing = _DIAGNOSTIC_KEYS - set(obj)
    if missing:
        _fail(f"diagnostic: missing keys {sorted(missing)}")
    extra = set(obj) - _DIAGNOSTIC_KEYS
    if extra:
        _fail(f"diagnostic: unknown keys {sorted(extra)}")
    if obj["diagnostic_version"] != "1.0.0":
        _fail("diagnostic.diagnostic_version: must be '1.0.0'")
    _diag_scope(obj, "diagnostic")
    kind = obj["kind"]
    _check_enum(kind, _DIAGNOSTIC_KINDS, "diagnostic.kind")
    _check_diag_details(kind, obj["details"], "diagnostic.details")
    if store is not None:
        _validate_run_scope(obj, store)
        details = obj["details"]
        if kind == "root_cause":
            _resolve_all(store, details["observation_refs"], "diagnostic.details.observation_refs")
        elif kind == "cve_applicability":
            _resolve(store, details["advisory_ref"], "diagnostic.details.advisory_ref")
            _resolve_all(store, details["observation_refs"], "diagnostic.details.observation_refs")
        elif kind == "attack_observation":
            _resolve_all(store, details["observation_refs"], "diagnostic.details.observation_refs")
        elif kind == "oracle_validation":
            _resolve(store, details["remediation_ref"], "diagnostic.details.remediation_ref")
            for k in ("attack_observation_refs", "benign_observation_refs", "related_observation_refs"):
                _resolve_all(store, details[k], f"diagnostic.details.{k}")
    return True


def _check_scope_consistency(obj):
    for k in ("campaign_id", "run_id", "target", "target_version", "actor"):
        if not _is_str(obj.get(k)):
            _fail(f"assessment_report.{k}: invalid")
    if not _is_iso(obj.get("created_at")):
        _fail("assessment_report.created_at: invalid ISO8601")
    if not _is_iso(obj["coverage"]["created_at"]):
        _fail("assessment_report.coverage.created_at: invalid ISO8601")


def _check_finding_ref(store, fa, report):
    where = f"assessment_report.findings[{fa['finding_id']}]"
    finding = _resolve(store, fa["finding_ref"], f"{where}.finding_ref")
    if not isinstance(finding, dict):
        _fail(f"{where}.finding_ref: resolved object is not a finding")
    if finding.get("finding_id") != fa["finding_id"]:
        _fail(f"{where}: finding_id mismatch with referenced finding")
    if finding.get("campaign_id") != report["campaign_id"]:
        _fail(f"{where}: campaign_id mismatch with referenced finding")
    if finding.get("run_id") != report["run_id"]:
        _fail(f"{where}: run_id mismatch with referenced finding")


_AXIS_KINDS = {
    "detection": {"observation"},
    "cause": {"root_cause"},
    "impact": {"cve_applicability", "oracle_validation"},
    "severity": set(),
}


def _check_claim_refs(store, fa, report):
    for i, claim in enumerate(fa["claims"]):
        where = f"assessment_report.findings[{fa['finding_id']}].claims[{i}]"
        axis = claim["confidence_axis"]
        status = claim["status"]
        basis = []
        for j, ref in enumerate(claim["basis_refs"]):
            diag = _resolve_diag(store, ref, f"{where}.basis_refs[{j}]")
            _check_diag_scope(diag, report, f"{where}.basis_refs[{j}]")
            basis.append(diag)
        if status == "confirmed":
            allowed = _AXIS_KINDS[axis]
            if not allowed:
                _fail(f"{where}: severity cannot be confirmed via diagnostic")
            for j, diag in enumerate(basis):
                if diag["kind"] not in allowed:
                    _fail(
                        f"{where}.basis_refs[{j}]: kind {diag['kind']!r} "
                        f"not valid for axis {axis!r}"
                    )
            if axis == "detection":
                for j, diag in enumerate(basis):
                    origin = diag["details"].get("origin")
                    if origin == "scanner":
                        _fail(
                            f"{where}.basis_refs[{j}]: scanner origin "
                            "cannot confirm detection"
                        )
        if claim["refuted_by"] is not None:
            refuted = _resolve_diag(store, claim["refuted_by"], f"{where}.refuted_by")
            _check_diag_scope(refuted, report, f"{where}.refuted_by")


def _check_classification_refs(store, fa, report):
    cls = fa["classification"]
    where = f"assessment_report.findings[{fa['finding_id']}].classification"
    cwe = cls["cwe"]
    cwe_diags = []
    for j, ref in enumerate(cwe["basis_refs"]):
        diag = _resolve_diag(store, ref, f"{where}.cwe.basis_refs[{j}]")
        _check_diag_scope(diag, report, f"{where}.cwe.basis_refs[{j}]")
        cwe_diags.append(diag)
    if cwe["status"] == "confirmed":
        for j, diag in enumerate(cwe_diags):
            if diag["kind"] != "root_cause":
                _fail(f"{where}.cwe.basis_refs[{j}]: confirmed cwe requires root_cause")
            d = diag["details"]
            if set(d["cwe_ids"]) != set(cwe["ids"]):
                _fail(f"{where}.cwe.basis_refs[{j}]: cwe_ids mismatch")
            if d["category"] != fa["category"]:
                _fail(f"{where}.cwe.basis_refs[{j}]: category mismatch")
            if not d["observation_refs"]:
                _fail(f"{where}.cwe.basis_refs[{j}]: observation_refs required")

    cve = cls["cve"]
    app = cve["applicability"]
    cve_diags = []
    for j, ref in enumerate(app["basis_refs"]):
        diag = _resolve_diag(store, ref, f"{where}.cve.applicability.basis_refs[{j}]")
        _check_diag_scope(diag, report, f"{where}.cve.applicability.basis_refs[{j}]")
        cve_diags.append(diag)
    if cve["status"] == "confirmed":
        for j, diag in enumerate(cve_diags):
            if diag["kind"] != "cve_applicability":
                _fail(f"{where}.cve.applicability.basis_refs[{j}]: confirmed cve requires cve_applicability")
            d = diag["details"]
            if d["package"] != app["product"]:
                _fail(f"{where}.cve.applicability.basis_refs[{j}]: package mismatch")
            if d["version"] != app["version"]:
                _fail(f"{where}.cve.applicability.basis_refs[{j}]: version mismatch")
            if d["condition"] != app["condition"]:
                _fail(f"{where}.cve.applicability.basis_refs[{j}]: condition mismatch")
            if not d["observation_refs"]:
                _fail(f"{where}.cve.applicability.basis_refs[{j}]: observation_refs required")
            advisory = _resolve(store, d["advisory_ref"], f"{where}.cve.applicability.basis_refs[{j}]")
            validate_advisory(advisory, store)
            if advisory["cve_id"] not in cve["ids"]:
                _fail(f"{where}.cve.applicability.basis_refs[{j}]: advisory cve_id mismatch")
            if match_version(store, d["advisory_ref"], d["package"], d["version"])["version_status"] != "affected":
                _fail(f"{where}.cve.applicability.basis_refs[{j}]: version not affected")

    for j, ref in enumerate(cls["owasp"]["basis_refs"]):
        diag = _resolve_diag(store, ref, f"{where}.owasp.basis_refs[{j}]")
        _check_diag_scope(diag, report, f"{where}.owasp.basis_refs[{j}]")

    attack = cls["attack"]
    attack_diags = []
    for j, ref in enumerate(attack["observation_refs"]):
        diag = _resolve_diag(store, ref, f"{where}.attack.observation_refs[{j}]")
        _check_diag_scope(diag, report, f"{where}.attack.observation_refs[{j}]")
        attack_diags.append(diag)
    if attack["status"] == "observed":
        for j, diag in enumerate(attack_diags):
            if diag["kind"] != "attack_observation":
                _fail(f"{where}.attack.observation_refs[{j}]: observed requires attack_observation")
            d = diag["details"]
            if d["technique_id"] not in attack["techniques"]:
                _fail(f"{where}.attack.observation_refs[{j}]: technique mismatch")
            if not d["behavior"]:
                _fail(f"{where}.attack.observation_refs[{j}]: behavior required")
            if not d["observation_refs"]:
                _fail(f"{where}.attack.observation_refs[{j}]: observation_refs required")


def _check_remediation_validation(store, fa, report):
    where = f"assessment_report.findings[{fa['finding_id']}]"
    rem = fa["remediation"]
    val = fa["validation"]
    diff_diag = None
    if rem is not None:
        _resolve(store, rem["proposal_ref"], f"{where}.remediation.proposal_ref")
        if rem["applied"]:
            if rem["diff_ref"] is None:
                _fail(f"{where}.remediation: applied requires diff_ref")
            diff_diag = _resolve_diag(store, rem["diff_ref"], f"{where}.remediation.diff_ref")
            _check_diag_scope(diff_diag, report, f"{where}.remediation.diff_ref")
            if diff_diag["kind"] != "remediation_diff":
                _fail(f"{where}.remediation.diff_ref: must be remediation_diff")
            d = diff_diag["details"]
            if d["after_version"] != rem["applied_version"]:
                _fail(f"{where}.remediation: applied_version mismatch with diff")
            if d["environment"] != rem["applied_env"]:
                _fail(f"{where}.remediation: applied_env mismatch with diff")
    if val is not None:
        oracle = _resolve_diag(store, val["checks"]["oracle_evidence_ref"], f"{where}.validation.checks.oracle_evidence_ref")
        _check_diag_scope(oracle, report, f"{where}.validation.checks.oracle_evidence_ref")
        if oracle["kind"] != "oracle_validation":
            _fail(f"{where}.validation.checks.oracle_evidence_ref: must be oracle_validation")
        if rem is None:
            _fail(f"{where}.validation: requires remediation")
        if val["remediation_id"] != rem["remediation_id"]:
            _fail(f"{where}.validation: remediation_id mismatch")
        od = oracle["details"]
        if rem["diff_ref"] is not None and od["remediation_ref"] != rem["diff_ref"]:
            _fail(f"{where}.validation: oracle remediation_ref mismatch with diff_ref")
        if val["result"] == "success":
            if not rem["applied"]:
                _fail(f"{where}.validation: success requires applied remediation")
            if rem["side_effects"]:
                _fail(f"{where}.validation: success requires empty side_effects")
            if val["oracle_verdict"] != "PASS":
                _fail(f"{where}.validation: success requires PASS verdict")
            if od["verdict"] != "PASS":
                _fail(f"{where}.validation: success requires oracle PASS")
            if oracle.get("actor") != "oracle":
                _fail(f"{where}.validation: success requires oracle actor")
            if oracle.get("run_id") != report["run_id"]:
                _fail(f"{where}.validation: oracle run scope mismatch")
            if diff_diag is not None and od["remediation_ref"] != rem["diff_ref"]:
                _fail(f"{where}.validation: oracle remediation_ref mismatch")
            for k in ("attack_observation_refs", "benign_observation_refs", "related_observation_refs"):
                for j, ref in enumerate(od[k]):
                    obs = _resolve_diag(store, ref, f"{where}.validation.oracle.{k}[{j}]")
                    _check_diag_scope(obs, report, f"{where}.validation.oracle.{k}[{j}]")
                    if obs["kind"] != "observation":
                        _fail(f"{where}.validation.oracle.{k}[{j}]: must be observation")
                    if obs["target_version"] != rem["applied_version"]:
                        _fail(f"{where}.validation.oracle.{k}[{j}]: version mismatch")


def _check_cvss_refs(store, rec, report):
    where = f"assessment_report.cvss[{rec['finding_id']}]"
    for metric, refs in rec["metric_basis"].items():
        for j, ref in enumerate(refs):
            diag = _resolve_diag(store, ref, f"{where}.metric_basis.{metric}[{j}]")
            _check_diag_scope(diag, report, f"{where}.metric_basis.{metric}[{j}]")
    if rec["status"] == "scored":
        from .severity import score_cvss4
        try:
            computed = score_cvss4(rec["vector"], rec["metric_basis"], store)
        except Exception as exc:
            raise AssessmentContractError(f"{where}: invalid vector: {exc}") from exc
        if isinstance(computed, dict):
            c_score = computed.get("score")
            c_nom = computed.get("nomenclature")
            c_status = computed.get("status")
            c_vector = computed.get("vector")
        else:
            c_score = computed
            c_nom = None
            c_status = None
            c_vector = None
        if c_status is not None and c_status != rec["status"]:
            _fail(f"{where}: status mismatch with recomputed CVSS")
        if c_score is not None and abs(float(c_score) - float(rec["score"])) > 1e-9:
            _fail(f"{where}: score mismatch with recomputed CVSS")
        if c_nom is not None and c_nom != rec["nomenclature"]:
            _fail(f"{where}: nomenclature mismatch with recomputed CVSS")
        if c_vector is not None and c_vector != rec["vector"]:
            _fail(f"{where}: vector mismatch with recomputed CVSS")


def _validate_run_scope(obj, store):
    manifest = store.load_manifest(obj["run_id"])
    run = manifest["run"]
    if run.get("campaign_id") != obj["campaign_id"]:
        _fail("run campaign_id mismatch")
    if run.get("actor", {}).get("role") != obj["actor"]:
        _fail("run actor role mismatch")


def semantic_validate_assessment(kind, obj, store):
    """Cross-reference and semantic validation of an assessment report."""
    if kind not in ASSESSMENT_KINDS:
        _fail(f"unsupported assessment kind: {kind!r}")
    if store is None:
        _fail("assessment_report: store is required for semantic validation")
    if not isinstance(obj, dict):
        _fail("assessment_report: expected object")
    _check_scope_consistency(obj)
    _validate_run_scope(obj, store)

    _check_not_future(obj["created_at"], "assessment_report.created_at")
    _check_not_future(obj["coverage"]["created_at"], "assessment_report.coverage.created_at")

    if obj["previous_report_ref"] is not None:
        prev = _resolve(store, obj["previous_report_ref"], "assessment_report.previous_report_ref")
        if not isinstance(prev, dict):
            _fail("assessment_report.previous_report_ref: resolved object is not a report")
        if prev.get("campaign_id") != obj["campaign_id"]:
            _fail("assessment_report.previous_report_ref: campaign mismatch")
        if prev.get("target") != obj["target"]:
            _fail("assessment_report.previous_report_ref: target mismatch")
        prev_created = prev.get("created_at")
        if not _is_iso(prev_created):
            _fail("assessment_report.previous_report_ref: invalid created_at")
        try:
            prev_dt = datetime.fromisoformat(prev_created.replace("Z", "+00:00"))
            cur_dt = datetime.fromisoformat(obj["created_at"].replace("Z", "+00:00"))
            if prev_dt.tzinfo is None:
                prev_dt = prev_dt.replace(tzinfo=timezone.utc)
            if cur_dt.tzinfo is None:
                cur_dt = cur_dt.replace(tzinfo=timezone.utc)
            if prev_dt >= cur_dt:
                _fail("assessment_report.previous_report_ref: must be earlier than current")
        except ValueError:
            _fail("assessment_report.previous_report_ref: invalid created_at")

    if obj["manifest_ref"] is not None:
        _resolve(store, obj["manifest_ref"], "assessment_report.manifest_ref")

    for i, cat in enumerate(obj["coverage"]["categories"]):
        where = f"assessment_report.coverage.categories[{i}].evidence_refs"
        if cat["status"] == "executed" and not cat["evidence_refs"]:
            _fail(f"{where}: executed requires non-empty evidence_refs")
        for j, ref in enumerate(cat["evidence_refs"]):
            diag = _resolve_diag(store, ref, f"{where}[{j}]")
            _check_diag_scope(diag, obj, f"{where}[{j}]")

    finding_ids = set()
    cause_ids = set()
    for fa in obj["findings"]:
        if fa["finding_id"] in finding_ids:
            _fail(f"assessment_report.findings: duplicate finding_id {fa['finding_id']}")
        finding_ids.add(fa["finding_id"])
        if fa["cause_id"] in cause_ids:
            _fail(f"assessment_report.findings: duplicate cause_id {fa['cause_id']}")
        cause_ids.add(fa["cause_id"])
        _check_finding_ref(store, fa, obj)
        for j, obs in enumerate(fa["observations"]):
            if obs["finding_id"] != fa["finding_id"]:
                _fail(f"assessment_report.findings[{fa['finding_id']}].observations[{j}]: finding_id mismatch")
            payload = _resolve(store, obs["payload_ref"], f"assessment_report.findings[{fa['finding_id']}].observations[{j}].payload_ref")
            if not isinstance(payload, dict) or payload.get("kind") != "observation":
                _fail(f"assessment_report.findings[{fa['finding_id']}].observations[{j}].payload_ref: must be observation diagnostic")
            validate_diagnostic(payload, store)
            _check_diag_scope(payload, obj, f"assessment_report.findings[{fa['finding_id']}].observations[{j}].payload_ref")
        _check_claim_refs(store, fa, obj)
        _check_classification_refs(store, fa, obj)
        _check_remediation_validation(store, fa, obj)

    for rec in obj["cvss"]:
        _check_cvss_refs(store, rec, obj)

    enrichment = obj.get("enrichment")
    if isinstance(enrichment, list):
        try:
            from .assessment_enrichment import validate_enrichment_records
            validate_enrichment_records(enrichment, finding_ids)
        except _EnrichmentError as exc:
            _fail(f"assessment_report.enrichment: {exc}")
        cve_ids_by_finding = {}
        for fa in obj["findings"]:
            cve_block = (fa.get("classification") or {}).get("cve") or {}
            cve_ids_by_finding[fa["finding_id"]] = set(cve_block.get("ids") or [])
        for i, rec in enumerate(enrichment):
            cve = rec.get("cve")
            if cve is None:
                continue
            allowed_ids = cve_ids_by_finding.get(rec.get("finding_id"), set())
            if cve not in allowed_ids:
                _fail(
                    f"assessment_report.enrichment[{i}].cve: {cve!r} not in "
                    f"finding classification.cve.ids")

    return True
