"""評価identity契約の実装。

preflight/postrunの2段階でmanifest・source・input・runner・datasetの同一性を検証する。
宣言されたhashを信頼せず、実ファイルを読み取って照合する。
"""
from __future__ import annotations

import datetime
import hashlib
import json
import math
import os
import re
from pathlib import Path
from typing import Any, Mapping


class DataError(Exception):
    """入力不正・凍結破壊を表す。"""

    def __init__(self, code: str, message: str = "") -> None:
        self.code = code
        super().__init__(message or code)


_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_MAX_BYTES = 16 * 1024 * 1024

_KNOWN_PROFILES = {"game", "document_retrieval", "performance", "workflow"}
_PROFILE_MEASUREMENT_UNIT = {
    "workflow": "task",
    "game": "game",
    "document_retrieval": "query",
    "performance": "operation",
}
_ALLOWED_PURPOSES = {"comparison", "experiment"}
_BINDING_KEYS = (
    "legacy_source",
    "candidate_source",
    "legacy_input",
    "candidate_input",
    "runner",
    "dataset",
)


def _fail(code: str, message: str = "") -> None:
    raise DataError(code, message)


def _is_bool(value: Any) -> bool:
    return isinstance(value, bool)


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        _fail("input_invalid", f"{name} は非空文字列が必要")
    return value


def _require_sha(value: Any, name: str) -> str:
    text = _require_str(value, name)
    if not _SHA256_RE.match(text):
        _fail("input_invalid", f"{name} は sha256:<64 lowercase hex> 形式が必要")
    return text


def _require_positive_int(value: Any, name: str) -> int:
    if _is_bool(value) or not isinstance(value, int) or value <= 0:
        _fail("input_invalid", f"{name} は正の整数が必要")
    return value


def _require_zero_int(value: Any, name: str) -> int:
    if _is_bool(value) or not isinstance(value, int) or value != 0:
        _fail("input_invalid", f"{name} は0が必要")
    return value


def _require_finite_number(value: Any, name: str) -> float:
    if _is_bool(value) or not isinstance(value, (int, float)):
        _fail("input_invalid", f"{name} は有限数値が必要")
    number = float(value)
    if not math.isfinite(number):
        _fail("input_invalid", f"{name} は有限数値が必要")
    return number


def _canonical_json_bytes(obj: Any) -> bytes:
    try:
        return json.dumps(
            obj,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (ValueError, TypeError) as exc:
        _fail("input_invalid", f"canonical JSON 化に失敗: {exc}")


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _resolve_within(root: Path, candidate: str, name: str) -> Path:
    """artifact_root内に収まる実在の通常ファイルを返す。"""
    if not isinstance(candidate, str) or not candidate.strip():
        _fail("input_invalid", f"{name} のパスが空")
    root_abs = Path(os.path.abspath(str(root)))
    raw = Path(candidate)
    if raw.is_absolute():
        target = Path(os.path.abspath(str(raw)))
    else:
        target = Path(os.path.abspath(str(root_abs / raw)))
    try:
        target.relative_to(root_abs)
    except ValueError:
        _fail("input_invalid", f"{name} が artifact_root 外を指す")
    _assert_no_symlink_ancestors(root_abs, target, name)
    if not target.exists():
        _fail("input_invalid", f"{name} のパスが存在しない")
    if target.is_symlink():
        _fail("input_invalid", f"{name} がシンボリックリンク")
    if not target.is_file():
        _fail("input_invalid", f"{name} が通常ファイルでない")
    return target


def _assert_no_symlink_ancestors(root: Path, target: Path, name: str) -> None:
    try:
        rel = target.relative_to(root)
    except ValueError:
        _fail("input_invalid", f"{name} が artifact_root 外を指す")
    current = root
    for part in rel.parts:
        current = current / part
        if current.is_symlink():
            _fail("input_invalid", f"{name} の経路にシンボリックリンクがある")


def _read_bounded(path: Path, name: str) -> bytes:
    try:
        size = path.stat().st_size
    except OSError as exc:
        _fail("input_invalid", f"{name} のstatに失敗: {exc}")
    if size > _MAX_BYTES:
        _fail("input_invalid", f"{name} が16MiBを超える")
    try:
        with open(path, "rb") as handle:
            data = handle.read(_MAX_BYTES + 1)
    except OSError as exc:
        _fail("input_invalid", f"{name} の読み取りに失敗: {exc}")
    if len(data) > _MAX_BYTES:
        _fail("input_invalid", f"{name} が16MiBを超える")
    return data


def _file_sha256(path: Path, name: str) -> str:
    return _sha256_bytes(_read_bounded(path, name))


def _normalize_bindings(bindings: Mapping[str, Any]) -> dict[str, str]:
    if not isinstance(bindings, Mapping):
        _fail("input_invalid", "bindings はマッピングが必要")
    keys = set(bindings.keys())
    expected = set(_BINDING_KEYS)
    if keys != expected:
        missing = expected - keys
        extra = keys - expected
        _fail(
            "input_invalid",
            f"bindings のキー不一致 missing={sorted(missing)} extra={sorted(extra)}",
        )
    result: dict[str, str] = {}
    for key in _BINDING_KEYS:
        value = bindings[key]
        if not isinstance(value, str) or not value.strip():
            _fail("input_invalid", f"bindings[{key}] は非空文字列が必要")
        result[key] = value
    return result


def _validate_manifest_schema(manifest: Mapping[str, Any]) -> None:
    if not isinstance(manifest, Mapping):
        _fail("input_invalid", "manifest はマッピングが必要")
    version = manifest.get("schema_version")
    if version != "1.1":
        _fail("input_invalid", "schema_version は 1.1 が必要")
    profile = manifest.get("profile")
    if profile not in _KNOWN_PROFILES:
        _fail("input_invalid", f"未知の profile: {profile!r}")
    _require_str(manifest.get("manifest_id"), "manifest_id")
    _require_str(manifest.get("task_id"), "task_id")
    _require_str(manifest.get("owner"), "owner")
    purpose = manifest.get("purpose")
    if purpose not in _ALLOWED_PURPOSES:
        _fail("input_invalid", f"purpose は {sorted(_ALLOWED_PURPOSES)} のいずれか")
    freeze_date = _require_str(manifest.get("freeze_date"), "freeze_date")
    if not _DATE_RE.match(freeze_date):
        _fail("input_invalid", "freeze_date は ISO 日付が必要")
    try:
        datetime.date.fromisoformat(freeze_date)
    except ValueError:
        _fail("input_invalid", "freeze_date は実在する ISO 日付が必要")
    _require_str(manifest.get("hypothesis"), "hypothesis")
    _require_str(manifest.get("action_delta"), "action_delta")
    _require_str(manifest.get("negative_control"), "negative_control")

    identity = manifest.get("identity")
    if not isinstance(identity, Mapping):
        _fail("input_invalid", "identity はマッピングが必要")
    for side in ("legacy", "candidate"):
        block = identity.get(side)
        if not isinstance(block, Mapping):
            _fail("input_invalid", f"identity.{side} はマッピングが必要")
        _require_str(block.get("label"), f"identity.{side}.label")
        _require_str(block.get("source_path"), f"identity.{side}.source_path")
        _require_str(block.get("source_revision"), f"identity.{side}.source_revision")
        _require_sha(block.get("source_sha256"), f"identity.{side}.source_sha256")
        _require_sha(block.get("input_sha256"), f"identity.{side}.input_sha256")
    runner = identity.get("runner")
    if not isinstance(runner, Mapping):
        _fail("input_invalid", "identity.runner はマッピングが必要")
    _require_str(runner.get("id"), "identity.runner.id")
    _require_sha(runner.get("sha256"), "identity.runner.sha256")

    evaluation = manifest.get("evaluation")
    if not isinstance(evaluation, Mapping):
        _fail("input_invalid", "evaluation はマッピングが必要")
    _require_str(evaluation.get("dataset_id"), "evaluation.dataset_id")
    _require_sha(evaluation.get("dataset_sha256"), "evaluation.dataset_sha256")
    measurement_unit = _require_str(
        evaluation.get("measurement_unit"), "evaluation.measurement_unit"
    )
    expected_unit = _PROFILE_MEASUREMENT_UNIT[profile]
    if measurement_unit != expected_unit:
        _fail(
            "input_invalid",
            f"profile={profile} の measurement_unit は {expected_unit} が必要",
        )
    _require_str(evaluation.get("primary_metric"), "evaluation.primary_metric")
    _require_str(evaluation.get("model_version"), "evaluation.model_version")
    _require_str(evaluation.get("policy_version"), "evaluation.policy_version")
    configuration = evaluation.get("configuration")
    if not isinstance(configuration, Mapping) or not configuration:
        _fail("input_invalid", "evaluation.configuration は非空マッピングが必要")

    mutations = manifest.get("external_mutations")
    if not isinstance(mutations, Mapping):
        _fail("input_invalid", "external_mutations はマッピングが必要")
    for key in ("registry_frozen", "submission_frozen"):
        if mutations.get(key) is not True:
            _fail("input_invalid", f"external_mutations.{key} は true が必要")


def _verify_bindings(
    manifest: Mapping[str, Any],
    artifact_root: Path,
    bindings: Mapping[str, Any],
) -> dict[str, str]:
    normalized = _normalize_bindings(bindings)
    identity = manifest["identity"]
    evaluation = manifest["evaluation"]

    checks = (
        ("legacy_source", identity["legacy"]["source_sha256"], "legacy_source"),
        ("candidate_source", identity["candidate"]["source_sha256"], "candidate_source"),
        ("legacy_input", identity["legacy"]["input_sha256"], "legacy_input"),
        ("candidate_input", identity["candidate"]["input_sha256"], "candidate_input"),
        ("runner", identity["runner"]["sha256"], "runner"),
        ("dataset", evaluation["dataset_sha256"], "dataset"),
    )
    for binding_key, declared, label in checks:
        path = _resolve_within(artifact_root, normalized[binding_key], binding_key)
        actual = _file_sha256(path, binding_key)
        if actual != declared:
            _fail("freeze_modified", f"{label} の実hashが宣言と不一致")

    for side in ("legacy", "candidate"):
        binding_key = f"{side}_source"
        declared_path = identity[side]["source_path"]
        expected = _resolve_within(artifact_root, declared_path, f"identity.{side}.source_path")
        actual = _resolve_within(artifact_root, normalized[binding_key], binding_key)
        if expected != actual:
            _fail(
                "freeze_modified",
                f"identity.{side}.source_path と {binding_key} の実パスが不一致",
            )
    return normalized


def _identity_digest(manifest: Mapping[str, Any]) -> str:
    stripped = {k: v for k, v in manifest.items() if k not in ("status", "outcome")}
    return _sha256_bytes(_canonical_json_bytes(stripped))


def _manifest_digest(manifest: Mapping[str, Any]) -> str:
    return _sha256_bytes(_canonical_json_bytes(manifest))


def is_immutable_model_version(value: Any) -> bool:
    """model_version が不変識別子として明示されているか。"""
    if not isinstance(value, str) or not value.strip():
        return False
    text = value.strip()
    if text.startswith("not_used:"):
        return bool(text[len("not_used:"):].strip())
    if text.startswith("immutable:"):
        return bool(text[len("immutable:"):].strip())
    return False


def preflight(
    manifest: dict,
    artifact_root: Path,
    bindings: dict,
) -> dict:
    """実行前検証。成功時のみ評価を開始できる。"""
    _validate_manifest_schema(manifest)
    if manifest.get("status") != "frozen":
        _fail("input_invalid", "preflight は status=frozen が必要")
    root = Path(artifact_root)
    if not root.exists() or not root.is_dir():
        _fail("input_invalid", "artifact_root が実在のディレクトリでない")
    _verify_bindings(manifest, root, bindings)
    return {
        "status": "pass",
        "identity_digest": _identity_digest(manifest),
        "manifest_digest": _manifest_digest(manifest),
    }


def _validate_outcome(
    outcome: Any,
    artifact_root: Path,
    profile: str,
    primary_metric: str,
) -> None:
    if not isinstance(outcome, Mapping):
        _fail("input_invalid", "outcome はマッピングが必要")
    artifact_path = _require_str(outcome.get("artifact_path"), "outcome.artifact_path")
    declared_sha = _require_sha(outcome.get("artifact_sha256"), "outcome.artifact_sha256")
    resolved = _resolve_within(artifact_root, artifact_path, "outcome.artifact_path")
    actual_sha = _file_sha256(resolved, "outcome.artifact_path")
    if actual_sha != declared_sha:
        _fail("freeze_modified", "outcome.artifact_sha256 が実ファイルと不一致")

    _require_positive_int(outcome.get("records"), "outcome.records")

    metrics = outcome.get("metrics")
    if not isinstance(metrics, Mapping):
        _fail("input_invalid", "outcome.metrics はマッピングが必要")
    primary = metrics.get(primary_metric)
    _require_finite_number(primary, f"outcome.metrics.{primary_metric}")

    _require_zero_int(outcome.get("error_count"), "outcome.error_count")
    _require_zero_int(outcome.get("crash_count"), "outcome.crash_count")
    _require_zero_int(outcome.get("timeout_count"), "outcome.timeout_count")

    if profile == "game":
        total = outcome.get("total_games")
        _require_positive_int(total, "outcome.total_games")
        _require_zero_int(outcome.get("fallback_count"), "outcome.fallback_count")
        _require_zero_int(
            outcome.get("illegal_action_count"), "outcome.illegal_action_count"
        )


def postrun(
    completed_manifest: dict,
    frozen_manifest: dict,
    artifact_root: Path,
    bindings: dict,
) -> dict:
    """実行後検証。frozen manifest との同一性と結果artifactを照合する。"""
    _validate_manifest_schema(completed_manifest)
    _validate_manifest_schema(frozen_manifest)

    if completed_manifest.get("status") != "completed":
        _fail("input_invalid", "postrun は status=completed が必要")
    if frozen_manifest.get("status") != "frozen":
        _fail("input_invalid", "frozen_manifest は status=frozen が必要")

    if _identity_digest(completed_manifest) != _identity_digest(frozen_manifest):
        _fail("freeze_modified", "identity digest が preflight と不一致")

    root = Path(artifact_root)
    if not root.exists() or not root.is_dir():
        _fail("input_invalid", "artifact_root が実在のディレクトリでない")

    _verify_bindings(frozen_manifest, root, bindings)

    profile = frozen_manifest["profile"]
    primary_metric = frozen_manifest["evaluation"]["primary_metric"]
    _validate_outcome(
        completed_manifest.get("outcome"), root, profile, primary_metric
    )

    return {
        "status": "pass",
        "identity_digest": _identity_digest(completed_manifest),
        "manifest_digest": _manifest_digest(completed_manifest),
        "result_digest": _sha256_bytes(
            _canonical_json_bytes(completed_manifest.get("outcome"))
        ),
    }
