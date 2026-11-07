"""evaluation_identity の契約テスト。実ファイル指紋を用いる。"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from ai_yori_murasaki.evaluation_identity import (
    DataError,
    is_immutable_model_version,
    postrun,
    preflight,
)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha(data)


def _base_manifest() -> dict:
    return {
        "schema_version": "1.1",
        "profile": "workflow",
        "manifest_id": "eval:20240101-01",
        "task_id": "20240101-01",
        "owner": "tester",
        "purpose": "comparison",
        "status": "frozen",
        "freeze_date": "2024-01-01",
        "hypothesis": "単一仮説",
        "action_delta": "変更差分",
        "negative_control": "負の対照",
        "identity": {
            "legacy": {
                "label": "legacy",
                "source_path": "legacy/src.py",
                "source_revision": "rev-legacy",
                "source_sha256": "sha256:" + "0" * 64,
                "input_sha256": "sha256:" + "0" * 64,
            },
            "candidate": {
                "label": "candidate",
                "source_path": "candidate/src.py",
                "source_revision": "rev-candidate",
                "source_sha256": "sha256:" + "0" * 64,
                "input_sha256": "sha256:" + "0" * 64,
            },
            "runner": {"id": "runner-1", "sha256": "sha256:" + "0" * 64},
        },
        "evaluation": {
            "dataset_id": "ds",
            "dataset_sha256": "sha256:" + "0" * 64,
            "measurement_unit": "task",
            "primary_metric": "accuracy",
            "model_version": "not_used:rule",
            "policy_version": "policy-1",
            "configuration": {"top_k": 3},
        },
        "external_mutations": {
            "registry_frozen": True,
            "submission_frozen": True,
        },
    }


def _setup(tmp_path: Path):
    root = tmp_path / "artifacts"
    root.mkdir()
    files = {
        "legacy_source": (root / "legacy" / "src.py", b"legacy source"),
        "candidate_source": (root / "candidate" / "src.py", b"candidate source"),
        "legacy_input": (root / "inputs" / "legacy.json", b'{"a":1}'),
        "candidate_input": (root / "inputs" / "candidate.json", b'{"a":2}'),
        "runner": (root / "runner.py", b"runner code"),
        "dataset": (root / "dataset.jsonl", b'{"q":"x"}\n'),
    }
    bindings = {}
    hashes = {}
    for key, (path, data) in files.items():
        hashes[key] = _write(path, data)
        bindings[key] = str(path.relative_to(root))
    manifest = _base_manifest()
    manifest["identity"]["legacy"]["source_sha256"] = hashes["legacy_source"]
    manifest["identity"]["candidate"]["source_sha256"] = hashes["candidate_source"]
    manifest["identity"]["legacy"]["input_sha256"] = hashes["legacy_input"]
    manifest["identity"]["candidate"]["input_sha256"] = hashes["candidate_input"]
    manifest["identity"]["runner"]["sha256"] = hashes["runner"]
    manifest["evaluation"]["dataset_sha256"] = hashes["dataset"]
    return root, bindings, manifest


def _completed(root: Path, frozen: dict) -> dict:
    artifact = root / "results" / "outcome.json"
    artifact_sha = _write(artifact, b'{"ok":true}')
    completed = json.loads(json.dumps(frozen))
    completed["status"] = "completed"
    completed["outcome"] = {
        "artifact_path": "results/outcome.json",
        "artifact_sha256": artifact_sha,
        "records": 5,
        "metrics": {"accuracy": 0.9},
        "error_count": 0,
        "crash_count": 0,
        "timeout_count": 0,
    }
    return completed


def test_preflight_and_postrun_valid(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    pre = preflight(manifest, root, bindings)
    assert pre["status"] == "pass"
    assert pre["identity_digest"].startswith("sha256:")
    completed = _completed(root, manifest)
    post = postrun(completed, manifest, root, bindings)
    assert post["status"] == "pass"
    assert post["identity_digest"] == pre["identity_digest"]
    assert post["result_digest"].startswith("sha256:")


def test_changed_source_detected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    (root / bindings["legacy_source"]).write_bytes(b"tampered")
    with pytest.raises(DataError) as exc:
        preflight(manifest, root, bindings)
    assert exc.value.code == "freeze_modified"


def test_changed_input_detected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    (root / bindings["candidate_input"]).write_bytes(b"changed")
    with pytest.raises(DataError) as exc:
        preflight(manifest, root, bindings)
    assert exc.value.code == "freeze_modified"


def test_changed_manifest_identity_detected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = _completed(root, manifest)
    completed["hypothesis"] = "別の仮説"
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "freeze_modified"


def test_missing_binding_key(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    del bindings["runner"]
    with pytest.raises(DataError) as exc:
        preflight(manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_missing_outcome(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = json.loads(json.dumps(manifest))
    completed["status"] = "completed"
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_zero_records_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = _completed(root, manifest)
    completed["outcome"]["records"] = 0
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_bool_records_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = _completed(root, manifest)
    completed["outcome"]["records"] = True
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_nan_metric_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = _completed(root, manifest)
    completed["outcome"]["metrics"]["accuracy"] = float("nan")
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_bool_metric_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = _completed(root, manifest)
    completed["outcome"]["metrics"]["accuracy"] = True
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_bad_profile_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    manifest["profile"] = "unknown_profile"
    with pytest.raises(DataError) as exc:
        preflight(manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_path_escape_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    outside = tmp_path / "outside.txt"
    outside.write_bytes(b"outside")
    bindings["runner"] = str(outside)
    with pytest.raises(DataError) as exc:
        preflight(manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_error_count_nonzero_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = _completed(root, manifest)
    completed["outcome"]["error_count"] = 1
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_artifact_sha_mismatch_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = _completed(root, manifest)
    (root / "results" / "outcome.json").write_bytes(b"tampered")
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "freeze_modified"


@pytest.mark.parametrize(
    ("mutate", "reason"),
    [
        (lambda m, b: m.__setitem__("freeze_date", "2024-02-30"), "input_invalid"),
        (
            lambda m, b: m["evaluation"].__setitem__("measurement_unit", "game"),
            "input_invalid",
        ),
        (
            lambda m, b: m["identity"]["legacy"].__setitem__(
                "source_path", "candidate/src.py"
            ),
            "freeze_modified",
        ),
    ],
)
def test_invalid_freeze_contract_rejected(
    tmp_path: Path, mutate, reason: str
) -> None:
    root, bindings, manifest = _setup(tmp_path)
    mutate(manifest, bindings)
    with pytest.raises(DataError) as exc:
        preflight(manifest, root, bindings)
    assert exc.value.code == reason


def test_missing_named_metric_rejected(tmp_path: Path) -> None:
    root, bindings, manifest = _setup(tmp_path)
    completed = _completed(root, manifest)
    del completed["outcome"]["metrics"]["accuracy"]
    with pytest.raises(DataError) as exc:
        postrun(completed, manifest, root, bindings)
    assert exc.value.code == "input_invalid"


def test_is_immutable_model_version() -> None:
    assert is_immutable_model_version("not_used:lexical")
    assert is_immutable_model_version("immutable:abc123")
    assert not is_immutable_model_version("gpt-4o")
    assert not is_immutable_model_version("model:cloud")
    assert not is_immutable_model_version("not_used:")
    assert not is_immutable_model_version("")
