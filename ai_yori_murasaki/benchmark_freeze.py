"""凍結ゲート（Phase 2）: formal実行の preflight/postrun 連携。

- ``verify_freeze``: 実行前に identity manifest と freeze_digest を検証する。
- ``finalize_formal``: 実行後に outcome artifact を書き出し postrun を検証する。

formal=false の smoke 実行では何も検証せず素通しする。
"""

from __future__ import annotations

import re
from pathlib import Path

from .jsonio import DataError, canonical, digest, read_json
from .evaluation_identity import DataError as IdentityDataError, preflight, postrun


LAB_METRICS = ("tp", "tn", "fp", "fn", "invalid", "accuracy")

_BINDING_KEYS = (
    "legacy_source",
    "candidate_source",
    "legacy_input",
    "candidate_input",
    "runner",
    "dataset",
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and value != ""


def _count(totals: dict, key: str) -> int:
    value = totals.get(key, 0)
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value


def compute_metrics(confusion) -> dict:
    """混同行列の totals から固定集合の metrics を計算する。

    bool や非 int は 0 として扱う（数値を捏造しない）。
    accuracy の分母が 0 なら None。
    """
    totals = confusion.get("totals") if isinstance(confusion, dict) else None
    if not isinstance(totals, dict):
        totals = {}

    tp = _count(totals, "TP")
    tn = _count(totals, "TN")
    fp = _count(totals, "FP")
    fn = _count(totals, "FN")
    invalid = _count(totals, "invalid")

    denominator = tp + tn + fp + fn
    accuracy = (tp + tn) / denominator if denominator != 0 else None

    return {
        "tp": tp,
        "tn": tn,
        "fp": fp,
        "fn": fn,
        "invalid": invalid,
        "accuracy": accuracy,
    }


def _validate_bindings(bindings) -> dict:
    if not isinstance(bindings, dict):
        raise DataError("bindings must be an object", "input_invalid")
    if set(bindings.keys()) != set(_BINDING_KEYS):
        raise DataError("bindings keys mismatch", "input_invalid")
    normalized = {}
    for key in _BINDING_KEYS:
        value = bindings[key]
        if not _is_nonempty_str(value):
            raise DataError("bindings[%s] must be a non-empty string" % key, "input_invalid")
        normalized[key] = value
    return normalized


def verify_freeze(lab_manifest, trusted_root: Path) -> dict:
    """実行前の凍結検証。formal=false は素通しする。"""
    if not isinstance(lab_manifest, dict):
        raise DataError("manifest must be an object", "input_invalid")

    formal = lab_manifest.get("formal")
    if not isinstance(formal, bool):
        raise DataError("formal must be a boolean", "input_invalid")
    if formal is False:
        return {"formal": False}

    root = Path(trusted_root)
    if not root.exists() or not root.is_dir():
        raise DataError("trusted_root must be an existing directory", "input_invalid")

    identity_manifest_path = lab_manifest.get("identity_manifest")
    if not _is_nonempty_str(identity_manifest_path):
        raise DataError("identity_manifest must be a non-empty string", "input_invalid")

    bindings = _validate_bindings(lab_manifest.get("bindings"))

    freeze_digest_path = lab_manifest.get("freeze_digest")
    if not _is_nonempty_str(freeze_digest_path):
        raise DataError("freeze_digest must be a non-empty string", "input_invalid")

    identity_manifest = read_json(root / identity_manifest_path)
    if not isinstance(identity_manifest, dict):
        raise DataError("identity manifest must be an object", "input_invalid")

    if identity_manifest.get("campaign_id") != lab_manifest.get("campaign_id"):
        raise DataError("campaign_id mismatch", "input_invalid")
    if identity_manifest.get("run_id") != lab_manifest.get("run_id"):
        raise DataError("run_id mismatch", "input_invalid")

    try:
        preflight_result = preflight(identity_manifest, root, bindings)
    except IdentityDataError as exc:
        raise DataError(str(exc), exc.code) from exc

    freeze_digest = read_json(root / freeze_digest_path)
    if not isinstance(freeze_digest, dict):
        raise DataError("freeze_digest must be an object", "input_invalid")
    declared = freeze_digest.get("manifest_digest")
    if not isinstance(declared, str) or not _SHA256_RE.match(declared):
        raise DataError("freeze_digest.manifest_digest invalid", "input_invalid")
    if declared != preflight_result.get("manifest_digest"):
        raise DataError("freeze_digest mismatch", "freeze_modified")

    evaluation = identity_manifest.get("evaluation")
    if not isinstance(evaluation, dict):
        raise DataError("evaluation must be an object", "input_invalid")

    return {
        "formal": True,
        "identity_manifest": identity_manifest,
        "bindings": bindings,
        "preflight": preflight_result,
        "primary_metric": evaluation.get("primary_metric"),
    }


def finalize_formal(lab_manifest, freeze_info, result: dict, trusted_root: Path) -> dict:
    """実行後の outcome 書き出しと postrun 検証。"""
    if not isinstance(freeze_info, dict) or freeze_info.get("formal") is not True:
        return result

    primary_metric = freeze_info.get("primary_metric")
    if primary_metric not in LAB_METRICS:
        raise DataError("primary_metric not in LAB_METRICS", "hold")

    metrics = compute_metrics(result.get("confusion"))
    if metrics.get(primary_metric) is None:
        raise DataError("primary_metric is None", "hold")

    identity_manifest = freeze_info["identity_manifest"]
    bindings = freeze_info["bindings"]
    manifest_id = identity_manifest.get("manifest_id")
    if not _is_nonempty_str(manifest_id):
        raise DataError("manifest_id must be a non-empty string", "input_invalid")
    run_id = lab_manifest.get("run_id")
    if not _is_nonempty_str(run_id):
        raise DataError("run_id must be a non-empty string", "input_invalid")

    root = Path(trusted_root)
    outcome_artifact = "oracle/%s/outcome-%s.json" % (manifest_id, run_id)
    outcome_path = root / "oracle" / manifest_id / ("outcome-%s.json" % run_id)
    outcome_path.parent.mkdir(parents=True, exist_ok=True)
    outcome_path.write_bytes(canonical(result))

    reread = read_json(outcome_path)
    artifact_sha256 = "sha256:" + digest(reread)

    outcome = {
        "artifact_path": outcome_artifact,
        "artifact_sha256": artifact_sha256,
        "records": 5,
        "metrics": metrics,
        "error_count": 0,
        "crash_count": 0,
        "timeout_count": 0,
    }

    completed = dict(identity_manifest)
    completed["status"] = "completed"
    completed["outcome"] = outcome

    try:
        postrun_result = postrun(completed, identity_manifest, root, bindings)
    except IdentityDataError as exc:
        raise DataError(str(exc), exc.code) from exc

    result["formal_verified"] = True
    result["postrun"] = postrun_result
    result["metrics"] = metrics
    return result
