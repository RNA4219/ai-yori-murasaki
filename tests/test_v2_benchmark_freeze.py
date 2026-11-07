"""benchmark_freeze の単体テスト（Docker不要、evaluation_identity は実物）。"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from ai_yori_murasaki import evaluation_identity
from ai_yori_murasaki.benchmark_freeze import (
    LAB_METRICS,
    compute_metrics,
    finalize_formal,
    verify_freeze,
)
from ai_yori_murasaki.jsonio import DataError


BINDING_KEYS = (
    "legacy_source",
    "candidate_source",
    "legacy_input",
    "candidate_input",
    "runner",
    "dataset",
)


def _sha(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _write(path: Path, data: bytes) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return _sha(data)


def _build_fixture(root: Path, campaign_id: str = "camp-1", run_id: str = "run-1"):
    """trusted_root に identity manifest と bindings と freeze_digest を作る。"""
    legacy_source = "src/legacy.py"
    candidate_source = "src/candidate.py"
    legacy_input = "input/legacy.json"
    candidate_input = "input/candidate.json"
    runner = "runner/run.py"
    dataset = "data/dataset.json"

    legacy_source_sha = _write(root / legacy_source, b"legacy source")
    candidate_source_sha = _write(root / candidate_source, b"candidate source")
    legacy_input_sha = _write(root / legacy_input, b"legacy input")
    candidate_input_sha = _write(root / candidate_input, b"candidate input")
    runner_sha = _write(root / runner, b"runner")
    dataset_sha = _write(root / dataset, b"dataset")

    identity_manifest = {
        "schema_version": "1.1",
        "profile": "workflow",
        "manifest_id": "m-1",
        "task_id": "task-1",
        "owner": "owner-1",
        "purpose": "comparison",
        "freeze_date": "2026-01-01",
        "hypothesis": "h",
        "action_delta": "d",
        "negative_control": "n",
        "campaign_id": campaign_id,
        "run_id": run_id,
        "identity": {
            "legacy": {
                "label": "legacy",
                "source_path": legacy_source,
                "source_revision": "r1",
                "source_sha256": legacy_source_sha,
                "input_sha256": legacy_input_sha,
            },
            "candidate": {
                "label": "candidate",
                "source_path": candidate_source,
                "source_revision": "r2",
                "source_sha256": candidate_source_sha,
                "input_sha256": candidate_input_sha,
            },
            "runner": {"id": "runner-1", "sha256": runner_sha},
        },
        "evaluation": {
            "dataset_id": "ds-1",
            "dataset_sha256": dataset_sha,
            "measurement_unit": "task",
            "primary_metric": "accuracy",
            "model_version": "immutable:m1",
            "policy_version": "p1",
            "configuration": {"k": "v"},
        },
        "external_mutations": {"registry_frozen": True, "submission_frozen": True},
        "status": "frozen",
    }

    bindings = {
        "legacy_source": legacy_source,
        "candidate_source": candidate_source,
        "legacy_input": legacy_input,
        "candidate_input": candidate_input,
        "runner": runner,
        "dataset": dataset,
    }

    identity_path = root / "identity.json"
    identity_path.write_bytes(json.dumps(identity_manifest).encode("utf-8"))

    preflight_result = evaluation_identity.preflight(identity_manifest, root, bindings)
    freeze_digest_path = root / "freeze_digest.json"
    freeze_digest_path.write_bytes(
        json.dumps({"manifest_digest": preflight_result["manifest_digest"]}).encode("utf-8")
    )

    lab_manifest = {
        "formal": True,
        "trusted_root": str(root),
        "identity_manifest": "identity.json",
        "bindings": bindings,
        "freeze_digest": "freeze_digest.json",
        "campaign_id": campaign_id,
        "run_id": run_id,
    }

    return lab_manifest, identity_manifest, bindings


def _result():
    return {
        "status": "pass",
        "confusion": {
            "totals": {"TP": 3, "TN": 2, "FP": 0, "FN": 0, "invalid": 0}
        },
    }


class ComputeMetricsTest(unittest.TestCase):
    def test_accuracy_one(self):
        metrics = compute_metrics(
            {"totals": {"TP": 3, "TN": 2, "FP": 0, "FN": 0, "invalid": 0}}
        )
        self.assertEqual(metrics["accuracy"], 1.0)
        self.assertEqual(metrics["tp"], 3)
        self.assertEqual(metrics["tn"], 2)

    def test_all_zero_accuracy_none(self):
        metrics = compute_metrics(
            {"totals": {"TP": 0, "TN": 0, "FP": 0, "FN": 0, "invalid": 0}}
        )
        self.assertIsNone(metrics["accuracy"])

    def test_bool_not_counted(self):
        metrics = compute_metrics(
            {"totals": {"TP": True, "TN": 2, "FP": 0, "FN": 0, "invalid": 0}}
        )
        self.assertEqual(metrics["tp"], 0)
        self.assertEqual(metrics["accuracy"], 1.0)

    def test_lab_metrics_constant(self):
        self.assertEqual(
            set(LAB_METRICS), {"tp", "tn", "fp", "fn", "invalid", "accuracy"}
        )


class VerifyFreezeTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_success(self):
        lab_manifest, _, _ = _build_fixture(self.root)
        info = verify_freeze(lab_manifest, self.root)
        self.assertTrue(info["formal"])
        self.assertEqual(info["primary_metric"], "accuracy")

    def test_freeze_digest_modified(self):
        lab_manifest, _, _ = _build_fixture(self.root)
        path = self.root / "freeze_digest.json"
        path.write_bytes(
            json.dumps({"manifest_digest": "sha256:" + "0" * 64}).encode("utf-8")
        )
        with self.assertRaises(DataError) as ctx:
            verify_freeze(lab_manifest, self.root)
        self.assertEqual(ctx.exception.reason_code, "freeze_modified")

    def test_binding_file_modified(self):
        lab_manifest, _, _ = _build_fixture(self.root)
        (self.root / "runner" / "run.py").write_bytes(b"tampered")
        with self.assertRaises(DataError) as ctx:
            verify_freeze(lab_manifest, self.root)
        self.assertEqual(ctx.exception.reason_code, "freeze_modified")

    def test_identity_status_completed(self):
        lab_manifest, identity_manifest, _ = _build_fixture(self.root)
        identity_manifest["status"] = "completed"
        (self.root / "identity.json").write_bytes(
            json.dumps(identity_manifest).encode("utf-8")
        )
        with self.assertRaises(DataError) as ctx:
            verify_freeze(lab_manifest, self.root)
        self.assertEqual(ctx.exception.reason_code, "input_invalid")

    def test_campaign_id_mismatch(self):
        lab_manifest, _, _ = _build_fixture(self.root)
        lab_manifest["campaign_id"] = "other"
        with self.assertRaises(DataError) as ctx:
            verify_freeze(lab_manifest, self.root)
        self.assertEqual(ctx.exception.reason_code, "input_invalid")

    def test_bindings_missing(self):
        lab_manifest, _, _ = _build_fixture(self.root)
        del lab_manifest["bindings"]["runner"]
        with self.assertRaises(DataError) as ctx:
            verify_freeze(lab_manifest, self.root)
        self.assertEqual(ctx.exception.reason_code, "input_invalid")

    def test_formal_false(self):
        lab_manifest, _, _ = _build_fixture(self.root)
        lab_manifest["formal"] = False
        info = verify_freeze(lab_manifest, self.root)
        self.assertEqual(info, {"formal": False})


class FinalizeFormalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_success(self):
        lab_manifest, _, _ = _build_fixture(self.root)
        info = verify_freeze(lab_manifest, self.root)
        result = finalize_formal(lab_manifest, info, _result(), self.root)
        self.assertTrue(result["formal_verified"])
        self.assertIn("postrun", result)
        self.assertEqual(result["postrun"]["status"], "pass")
        outcome_path = self.root / "oracle" / "m-1" / "outcome-run-1.json"
        self.assertTrue(outcome_path.is_file())

    def test_formal_false_passthrough(self):
        lab_manifest, _, _ = _build_fixture(self.root)
        lab_manifest["formal"] = False
        info = verify_freeze(lab_manifest, self.root)
        result = _result()
        out = finalize_formal(lab_manifest, info, result, self.root)
        self.assertIs(out, result)
        self.assertNotIn("formal_verified", out)

    def test_primary_metric_outside_lab_metrics(self):
        lab_manifest, identity_manifest, bindings = _build_fixture(self.root)
        identity_manifest["evaluation"]["primary_metric"] = "recall"
        (self.root / "identity.json").write_bytes(
            json.dumps(identity_manifest).encode("utf-8")
        )
        preflight_result = evaluation_identity.preflight(
            identity_manifest, self.root, bindings
        )
        (self.root / "freeze_digest.json").write_bytes(
            json.dumps(
                {"manifest_digest": preflight_result["manifest_digest"]}
            ).encode("utf-8")
        )
        info = verify_freeze(lab_manifest, self.root)
        with self.assertRaises(DataError) as ctx:
            finalize_formal(lab_manifest, info, _result(), self.root)
        self.assertEqual(ctx.exception.reason_code, "hold")


if __name__ == "__main__":
    unittest.main()
