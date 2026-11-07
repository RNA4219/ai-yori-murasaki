"""RSI evaluation contract tests using real artifacts and the real evaluator."""
from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest

from ai_yori_murasaki import jsonio
from ai_yori_murasaki import rsi_eval
from ai_yori_murasaki.rsi import RSI
from ai_yori_murasaki.storage import Store
from ai_yori_murasaki.evaluation_identity import DataError as IdentityError

REJECTED = (jsonio.DataError, IdentityError)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha(data)


def _canonical(obj) -> bytes:
    return jsonio.canonical(obj)


def _config() -> dict:
    return {
        "objective": "maximize_success_gain",
        "seed_pairs": [1, 2],
        "order_policy": "alternating",
        "budget": {"max_pairs": 16, "max_calls": 128},
        "thresholds": {
            "success_gain_min": 0.0,
            "no_benign_regression": True,
            "max_safety_violations": 0,
        },
        "stop_conditions": {"max_iterations": 2},
        "memory_refs": [],
    }


def _dataset() -> dict:
    return {
        "tasks": [
            {
                "task_id": "t-train",
                "split": "train",
                "cases": [
                    {"role": "guest", "resource": "record", "expected_allowed": False},
                    {"role": "admin", "resource": "record", "expected_allowed": True},
                ],
            },
            {
                "task_id": "t-val",
                "split": "validation",
                "cases": [
                    {"role": "guest", "resource": "record", "expected_allowed": False},
                    {"role": "admin", "resource": "record", "expected_allowed": True},
                    {"role": "guest", "resource": "public", "expected_allowed": True},
                ],
            },
            {
                "task_id": "t-hold",
                "split": "holdout",
                "cases": [
                    {"role": "visitor", "resource": "record", "expected_allowed": False},
                    {"role": "admin", "resource": "record", "expected_allowed": True},
                    {"role": "guest", "resource": "public", "expected_allowed": True},
                ],
            },
        ]
    }


def _manifest(hashes: dict, model_version: str) -> dict:
    return {
        "schema_version": "1.1",
        "profile": "workflow",
        "manifest_id": "eval:rsi-01",
        "task_id": "rsi-01",
        "owner": "tester",
        "purpose": "comparison",
        "status": "frozen",
        "freeze_date": "2024-01-01",
        "hypothesis": "rsi hypothesis",
        "action_delta": "delta",
        "negative_control": "control",
        "identity": {
            "legacy": {
                "label": "legacy",
                "source_path": "baseline/policy.json",
                "source_revision": "rev-baseline",
                "source_sha256": hashes["legacy_source"],
                "input_sha256": hashes["legacy_input"],
            },
            "candidate": {
                "label": "candidate",
                "source_path": "candidate/policy.json",
                "source_revision": "rev-candidate",
                "source_sha256": hashes["candidate_source"],
                "input_sha256": hashes["candidate_input"],
            },
            "runner": {"id": "rsi_eval", "sha256": hashes["runner"]},
        },
        "evaluation": {
            "dataset_id": "ds-rsi",
            "dataset_sha256": hashes["dataset"],
            "measurement_unit": "task",
            "primary_metric": "success_gain",
            "model_version": model_version,
            "policy_version": "policy-1",
            "configuration": _config(),
        },
        "external_mutations": {
            "registry_frozen": True,
            "submission_frozen": True,
        },
    }


def setup_case(
    tmp_path: Path,
    model_version: str = "not_used:deterministic-local-policy",
    candidate: bool = True,
):
    ws = tmp_path / "ws"
    tr = tmp_path / "tr"
    ws.mkdir()
    tr.mkdir()
    root = tmp_path / "artifacts"
    root.mkdir()

    config = _config()
    config_bytes = _canonical(config)

    baseline_policy = {"require_admin": False}
    candidate_policy = {"require_admin": candidate}

    files = {
        "legacy_source": (root / "baseline" / "policy.json", _canonical(baseline_policy)),
        "candidate_source": (root / "candidate" / "policy.json", _canonical(candidate_policy)),
        "legacy_input": (root / "inputs" / "legacy.json", config_bytes),
        "candidate_input": (root / "inputs" / "candidate.json", config_bytes),
        "runner": (root / "runner-bundle.json", rsi_eval.runner_snapshot()),
        "dataset": (root / "dataset.json", _canonical(_dataset())),
    }
    hashes = {}
    bindings = {}
    for key, (path, data) in files.items():
        hashes[key] = _write(path, data)
        bindings[key] = str(path.relative_to(root))

    manifest = _manifest(hashes, model_version)

    store = Store(ws, tr)
    rsi = RSI(store)
    frozen = rsi.freeze("campaign-1", config, manifest, bindings, root)
    created = rsi.create_candidate(frozen["freeze_id"])
    return rsi, created, root, manifest, bindings


def test_freeze_compare_ready(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    result = rsi_eval.compare(rsi, candidate["candidate_id"])
    assert result["summary"]["gain"] == 1.0
    ready = rsi.ready(candidate["candidate_id"])
    assert ready["status"] == "promotion-ready"
    assert ready["summary"]["gain"] == 1.0


def test_registry_absent_until_initialize(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    rsi_eval.compare(rsi, candidate["candidate_id"])
    rsi.ready(candidate["candidate_id"])
    registry = rsi._registry_path
    assert not registry.exists()
    frozen = rsi.load_freeze(candidate["freeze_id"])
    rsi.initialize_registry(frozen["baseline_ref"])
    assert registry.exists()


def test_source_change_after_freeze_rejected(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    (root / bindings["candidate_source"]).write_bytes(b'{"policy":"tampered"}')
    with pytest.raises(REJECTED):
        rsi_eval.compare(rsi, candidate["candidate_id"])


def test_baseline_change_after_freeze_rejected(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    (root / bindings["legacy_source"]).write_bytes(b'{"policy":"tampered"}')
    with pytest.raises(REJECTED):
        rsi_eval.compare(rsi, candidate["candidate_id"])


def test_threshold_change_does_not_affect_freeze(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    frozen_before = rsi.load_freeze(candidate["freeze_id"])
    changed = _config()
    changed["thresholds"]["success_gain_min"] = 0.9
    rsi._store.put_object(changed)
    frozen_after = rsi.load_freeze(candidate["freeze_id"])
    assert frozen_before["config"] == frozen_after["config"]
    assert frozen_after["config"]["thresholds"]["success_gain_min"] == 0.0


def test_generated_artifact_tamper_blocked(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    result = rsi_eval.compare(rsi, candidate["candidate_id"])
    artifact = Path(result["completed_manifest_path"]).parent / (
        candidate["candidate_id"] + ".json"
    )
    artifact.write_bytes(b'{"tampered":true}')
    with pytest.raises(REJECTED):
        rsi.ready(candidate["candidate_id"])


def test_unpinned_cloud_comparison_allowed_ready_denied(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(
        tmp_path, model_version="glm-5.3:cloud"
    )
    result = rsi_eval.compare(rsi, candidate["candidate_id"])
    assert result["summary"]["gain"] == 1.0
    with pytest.raises(REJECTED):
        rsi.ready(candidate["candidate_id"])


def test_claimed_immutable_alias_not_ready(tmp_path):
    rsi, candidate, *_ = setup_case(tmp_path, model_version='immutable:glm-5.3:cloud')
    rsi_eval.compare(rsi, candidate['candidate_id'])
    with pytest.raises(REJECTED):
        rsi.ready(candidate['candidate_id'])


def test_apply_retry_is_idempotent(tmp_path):
    rsi, candidate, *_ = setup_case(tmp_path)
    rsi_eval.compare(rsi, candidate['candidate_id'])
    rsi.ready(candidate['candidate_id'])
    f = rsi.load_freeze(candidate['freeze_id'])
    review = rsi.record_review(candidate['candidate_id'], 'tester', 'approved')
    rsi.initialize_registry(f['baseline_ref'])
    args = (review['review_id'], f['baseline_ref'], f['candidate_ref'], f['baseline_ref'])
    assert rsi.apply(*args) == {'applied': True}
    assert rsi.apply(*args) == {'applied': True}
    assert rsi.get_candidate(candidate['candidate_id'])['status'] == 'applied'


def test_candidate_false_no_improvement_not_ready(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path, candidate=False)
    result = rsi_eval.compare(rsi, candidate["candidate_id"])
    assert result["summary"]["gain"] == 0.0
    with pytest.raises(REJECTED):
        rsi.ready(candidate["candidate_id"])


def test_add_experience_rejects_validation_and_extra(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    card = {"split": "train", "completion": "completed", "failure_class": "search",
            "outcome": "refuted", "action": "change_search", "evidence_count": 1}
    with pytest.raises(REJECTED):
        rsi.add_experience({**card, "split": "validation"})
    with pytest.raises(REJECTED):
        rsi.add_experience({**card, "credential": "test-canary-not-a-real-secret"})
    with pytest.raises(REJECTED):
        rsi.add_experience(card)
    from ai_yori_murasaki import rsi_train
    f = rsi.load_freeze(candidate['freeze_id'])
    dataset_ref = rsi._store.put_object(_dataset())
    trained = rsi_train.train(rsi, f['baseline_ref'], dataset_ref)
    card = rsi_train.propose(rsi, trained['train_ref'])['experience_card']
    ref = rsi.add_experience(card, train_ref=trained['train_ref'])["memory_ref"]
    assert rsi._verify_memory([ref]) == [card]
    with pytest.raises(REJECTED):
        rsi.record_holdout_access(candidate["candidate_id"], ["t-val"])
    with pytest.raises(REJECTED):
        rsi.record_holdout_access(candidate["candidate_id"], ["t-hold", "t-extra"])
    out = rsi.record_holdout_access(candidate["candidate_id"], ["t-hold"])
    assert len(out["audit_ref"]) == 64


def test_holdout_rejects_different_candidate(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    rsi.record_holdout_access(candidate["candidate_id"], ["t-hold"])

    root2 = tmp_path / "artifacts2"
    root2.mkdir()
    config = _config()
    config_bytes = _canonical(config)
    files = {
        "legacy_source": (root2 / "baseline" / "policy.json", _canonical({"require_admin": False})),
        "candidate_source": (root2 / "candidate" / "policy.json", _canonical({"require_admin": False})),
        "legacy_input": (root2 / "inputs" / "legacy.json", config_bytes),
        "candidate_input": (root2 / "inputs" / "candidate.json", config_bytes),
        "runner": (root2 / "runner-bundle.json", rsi_eval.runner_snapshot()),
        "dataset": (root2 / "dataset.json", _canonical(_dataset())),
    }
    hashes = {}
    bindings2 = {}
    for key, (path, data) in files.items():
        hashes[key] = _write(path, data)
        bindings2[key] = str(path.relative_to(root2))
    manifest2 = _manifest(hashes, "not_used:deterministic-local-policy")
    manifest2["manifest_id"] = "eval:rsi-02"
    manifest2["task_id"] = "rsi-02"
    frozen2 = rsi.freeze("campaign-2", config, manifest2, bindings2, root2)
    created2 = rsi.create_candidate(frozen2["freeze_id"])
    with pytest.raises(REJECTED):
        rsi.record_holdout_access(created2["candidate_id"], ["t-hold"])


def test_forged_comparison_success_flag_cannot_validate(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    rsi_eval.compare(rsi, candidate["candidate_id"])
    state = rsi._load_state()
    record = state["candidates"][candidate["candidate_id"]]
    record["comparison_ref"] = "sha256:" + "0" * 64
    rsi._save_state(state)
    with pytest.raises(REJECTED):
        rsi.ready(candidate["candidate_id"])


def test_omit_one_pair_rejected(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    result = rsi_eval.compare(rsi, candidate["candidate_id"])
    artifact = Path(result["completed_manifest_path"]).parent / (
        candidate["candidate_id"] + ".json"
    )
    data = json.loads(artifact.read_text(encoding="utf-8"))
    data["pairs"] = data["pairs"][:-1]
    artifact.write_bytes(_canonical(data))
    with pytest.raises(REJECTED):
        rsi.ready(candidate["candidate_id"])


def test_missing_postrun_ready_denied(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    with pytest.raises(REJECTED):
        rsi.ready(candidate["candidate_id"])


def test_review_apply_rollback(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    rsi_eval.compare(rsi, candidate["candidate_id"])
    rsi.ready(candidate["candidate_id"])
    frozen = rsi.load_freeze(candidate["freeze_id"])
    baseline_ref = frozen["baseline_ref"]
    candidate_ref = frozen["candidate_ref"]
    rsi.initialize_registry(baseline_ref)
    review = rsi.record_review(candidate["candidate_id"], "glm-reviewer", "approved")
    with pytest.raises(REJECTED):
        rsi.apply(review["review_id"], "sha256:" + "1" * 64, candidate_ref, "sha256:" + "1" * 64)
    out = rsi.apply(review["review_id"], baseline_ref, candidate_ref, baseline_ref)
    assert out["applied"] is True
    assert rsi.get_candidate(candidate["candidate_id"])["applied"] is True
    registry = json.loads(rsi._registry_path.read_text(encoding="utf-8"))
    assert registry["current"] == candidate_ref
    assert registry["previous"] == baseline_ref


def test_trusted_review_rejects_workspace_forgery(tmp_path: Path) -> None:
    rsi, candidate, *_ = setup_case(tmp_path)
    cid = candidate["candidate_id"]
    rsi_eval.compare(rsi, cid)
    frozen = rsi.load_freeze(candidate["freeze_id"])
    rsi.initialize_registry(frozen["baseline_ref"])
    review = rsi.record_review(cid, "reviewer", "rejected")
    state = rsi._load_state()
    state["reviews"][review["review_id"]]["decision"] = "approved"
    rsi._save_state(state)
    with pytest.raises(REJECTED):
        rsi.apply(review["review_id"], frozen["baseline_ref"], frozen["candidate_ref"], frozen["baseline_ref"])


def test_holdout_audit_missing_denies_ready(tmp_path: Path) -> None:
    rsi, candidate, *_ = setup_case(tmp_path)
    rsi_eval.compare(rsi, candidate["candidate_id"])
    (rsi._trusted / "rsi" / "holdout.json").unlink()
    with pytest.raises(REJECTED):
        rsi.ready(candidate["candidate_id"])


def test_real_comparison_projection_is_campaign_scoped(tmp_path: Path) -> None:
    from ai_yori_murasaki.server import _project_rsi
    rsi, candidate, *_ = setup_case(tmp_path)
    rsi_eval.compare(rsi, candidate["candidate_id"])
    rsi.ready(candidate["candidate_id"])
    rows = _project_rsi(rsi._store, "campaign-1")
    assert len(rows) == 1
    assert rows[0]["comparison"]["gain"] == 1.0
    assert rows[0]["status"] == "promotion-ready"
    assert rows[0]["applied"] is False
    assert _project_rsi(rsi._store, "other-campaign") == []


def test_comparison_recomputes_reported_success(tmp_path: Path) -> None:
    from ai_yori_murasaki.rsi_compare import _validate_comparison
    rsi, candidate, *_ = setup_case(tmp_path)
    result = rsi_eval.compare(rsi, candidate["candidate_id"])
    comparison = rsi._store.get_object(result["comparison_ref"])
    comparison["pairs"][0]["baseline"]["success"] = True
    with pytest.raises(REJECTED):
        _validate_comparison(comparison, rsi.load_freeze(candidate["freeze_id"]))


def test_freeze_pin_only_crash_recovery(tmp_path: Path) -> None:
    rsi, candidate, root, manifest, bindings = setup_case(tmp_path)
    state = rsi._load_state()
    state["freezes"].clear()
    rsi._save_state(state)
    recovered = rsi.freeze("campaign-1", _config(), manifest, bindings, root)
    assert recovered["freeze_id"] == candidate["freeze_id"]
    assert candidate["freeze_id"] in rsi._load_state()["freezes"]


def test_completed_comparison_retry_preserves_evidence(tmp_path: Path) -> None:
    rsi, candidate, *_ = setup_case(tmp_path)
    first = rsi_eval.compare(rsi, candidate["candidate_id"])
    second = rsi_eval.compare(rsi, candidate["candidate_id"])
    assert first["comparison_ref"] == second["comparison_ref"]
    assert first["completed_manifest_ref"] == second["completed_manifest_ref"]
