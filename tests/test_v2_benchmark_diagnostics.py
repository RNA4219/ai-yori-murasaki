"""benchmark_diagnostics の単体テスト（Docker 不要）。"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_yori_murasaki import benchmark, benchmark_diagnostics, cli_assessment
from ai_yori_murasaki.benchmark import ALLOWED_IMAGE_IDS, run_assessment_lab
from ai_yori_murasaki.benchmark_diagnostics import (
    build_lab_report,
    compute_validation_verdict,
    verify_auditor_exception,
)
from ai_yori_murasaki.benchmark_evaluation import CATEGORIES
from ai_yori_murasaki.jsonio import ContractError, DataError, digest
from ai_yori_murasaki.oracle import Oracle
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


_OBS_KEYS = [
    "auth_guest", "auth_admin", "public",
    "sql_attack", "sql_normal",
    "path_attack", "path_normal",
    "xss_attack", "xss_normal",
    "dependency",
]


def _raw_obs() -> dict:
    return {key: {"status": 200, "body": {}, "error": None} for key in _OBS_KEYS}


def _lab_result(cid, rid, **overrides) -> dict:
    observed = {
        cat: {"observed": True, "benign": True, "reason": None}
        for cat in CATEGORIES
    }
    base = {
        "observed": observed,
        "confusion": {"totals": {"TP": 5, "TN": 0, "FP": 0, "FN": 0, "invalid": 0}},
        "blue_result": {
            "isolation": {
                "network": "none",
                "readonly": True,
                "mounts_verified": True,
                "image_id": "sha256:" + "a" * 64,
            },
            "output": {},
            "execution": {},
        },
        "oracle_result": {
            "output": {"observations": _raw_obs()},
            "isolation": {},
            "execution": {},
        },
        "formal_verified": True,
        "postrun": {"status": "pass"},
        "campaign_id": cid,
        "run_id": rid,
        "dependency_version": "2.31.0",
    }
    base.update(overrides)
    return base


def _freeze_info(manifest_id: str = "m-1") -> dict:
    return {
        "formal": True,
        "preflight": {"status": "pass", "manifest_digest": "sha256:" + "b" * 64},
        "identity_manifest": {"manifest_id": manifest_id},
    }


class BenchmarkDiagnosticsTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "ws"
        self.tr = root / "tr"
        self.ws.mkdir()
        self.tr.mkdir()
        self.store = Store(self.ws, self.tr)
        self.rt = Runtime(self.store)
        scope = {"targets": [{"kind": "fixture", "identifier": "t1"}],
                 "max_targets": 1}
        auth = {"operator_id": "op1",
                "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope_digest": digest(scope)}
        c = self.rt.create_campaign("INT-TEST-1", scope, auth, "k1")
        self.cid = c["campaign_id"]
        self.freeze_info = _freeze_info()
        self._key_seq = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _key(self) -> str:
        self._key_seq += 1
        return f"k-{self._key_seq}"

    def _auditor_run(self) -> dict:
        ref = self.store.put_object({
            "manifest_id": "m-1",
            "status": "frozen",
            "schema_version": "1.1",
        })
        return self.rt.start_run(
            self.cid, "verify", 1, self._key(),
            evaluation_context={
                "evaluation_manifest_ref": ref,
                "evaluation_manifest_id": "m-1",
            })

    def _oracle_run(self) -> dict:
        self.rt.transition(self.cid, "running")
        red_run = self.rt.start_run(self.cid, "red", 1, self._key())
        red_rid = red_run["run_id"]
        self.rt.finish_run(red_rid, "completed")
        self.rt.seal_run(self.cid, red_rid)
        blue_run = self.rt.start_run(self.cid, "blue_blind", 1, self._key())
        blue_rid = blue_run["run_id"]
        self.rt.finish_run(blue_rid, "completed")
        self.rt.seal_run(self.cid, blue_rid)
        red_ref = self.store.put_object({
            "observations": [
                {"method": "GET", "path": "/record?role=guest", "status": 200,
                 "role": "guest"} for _ in range(3)]})
        blue_ref = self.store.put_object({
            "observations": [
                {"method": "GET", "path": "/record", "status": 200,
                 "role": "guest"} for _ in range(3)],
            "detection_count": 3})
        blind = Oracle().blind_predicate("blue-detection", red_ref, blue_ref,
                                         self.store)
        verify_run = self.rt.start_run(self.cid, "verify", 1, self._key())
        verify_rid = verify_run["run_id"]
        evaluated = self.rt.record_oracle(verify_rid, blind["predicate"],
                                          evaluated_run_id=blue_rid)
        self.assertEqual(evaluated.get("verdict"), "PASS")
        self.rt.finish_run(verify_rid, "completed")
        self.rt.seal_run(self.cid, verify_rid)
        self.rt.transition(self.cid, "blind_completed")
        self.rt.transition(self.cid, "purple_open")
        purple_run = self.rt.start_run(self.cid, "purple", 1, self._key())
        purple_rid = purple_run["run_id"]
        patch_ref = self.store.put_object(
            {"path": "target.py", "before": "a", "after": "b"})
        self.rt.record_event(purple_rid, "patch_drafted", "orchestrator",
                             evidence_refs=(patch_ref,))
        self.rt.finish_run(purple_rid, "completed")
        self.rt.seal_run(self.cid, purple_rid)
        self.rt.transition(self.cid, "remediation_in_progress")
        rem_run = self.rt.start_run(self.cid, "remediation", 1, self._key())
        rem_rid = rem_run["run_id"]
        self.rt.finish_run(rem_rid, "completed")
        self.rt.seal_run(self.cid, rem_rid)
        return self.rt.start_run(self.cid, "reevaluation", 1, self._key())

    def test_build_report_success_oracle(self) -> None:
        oracle_run = self._oracle_run()
        self.assertEqual(oracle_run["actor"]["role"], "oracle")
        self.assertEqual(oracle_run["status"], "running")
        lab = _lab_result(self.cid, oracle_run["run_id"])
        result = build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertIn("report_ref", result)
        report = self.store.get_object(result["report_ref"])
        self.assertEqual(report["schema_version"], "1.0.0")
        findings = report["findings"]
        self.assertEqual(len(findings), 5)
        for finding in findings:
            self.assertEqual(finding["claims"][0]["status"], "confirmed")
        coverage = report["coverage"]["categories"]
        self.assertEqual(len(coverage), 5)
        for entry in coverage:
            self.assertEqual(entry["status"], "executed")
        refs = result["diagnostic_refs"]
        for cat in CATEGORIES:
            for ref in refs["observations"][cat]:
                self.assertIsInstance(self.store.get_object(ref), dict)
        for cat in CATEGORIES:
            self.assertIsInstance(self.store.get_object(refs["root_causes"][cat]), dict)
        self.assertIsInstance(self.store.get_object(refs["validation"]), dict)

    def test_auditor_allowed(self) -> None:
        run = self._auditor_run()
        self.assertEqual(run["actor"]["role"], "auditor")
        self.assertEqual(run["status"], "running")
        lab = _lab_result(self.cid, run["run_id"])
        result = build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertIsNotNone(result["auditor_exception"])
        self.assertIs(result["auditor_exception"]["allowed"], True)
        report = self.store.get_object(result["report_ref"])
        run_manifest = self.store.get_object(run["manifest_ref"])
        self.assertEqual(report["manifest_ref"],
                         run_manifest["evaluation_manifest_ref"])

    def test_finding_registered_via_contract(self) -> None:
        run = self._auditor_run()
        rid = run["run_id"]
        lab = _lab_result(self.cid, rid)
        result = build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        report = self.store.get_object(result["report_ref"])
        refs = self.rt.list_records(self.cid, "finding")
        self.assertEqual(len(refs), 5)
        stored_ids = set()
        for ref in refs:
            obj = self.store.get_object(ref)
            self.assertIsInstance(obj, dict)
            self.assertEqual(obj["campaign_id"], self.cid)
            self.assertEqual(obj["run_id"], rid)
            self.assertEqual(obj["source"], "benchmark_lab")
            stored_ids.add(obj["finding_id"])
        report_ids = {f["finding_id"] for f in report["findings"]}
        self.assertEqual(stored_ids, report_ids)
        bad = {
            "schema_version": "1.0.0",
            "finding_id": "f" + "0" * 31,
            "campaign_id": self.cid,
            "run_id": rid,
            "source": "invalid_source",
            "severity": "high",
            "title": "bad",
            "location": {"path": "lab", "line": None, "column": None},
            "references": [],
        }
        with self.assertRaises((ContractError, DataError)):
            self.rt.register_record("finding", bad, run_id=rid)

    def test_auditor_rejected_formal_verified_false(self) -> None:
        run = self._auditor_run()
        lab = _lab_result(self.cid, run["run_id"], formal_verified=False)
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "policy_violation")

    def test_auditor_rejected_isolation(self) -> None:
        run = self._auditor_run()
        lab = _lab_result(self.cid, run["run_id"])
        del lab["blue_result"]["isolation"]["mounts_verified"]
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "policy_violation")
        checks = verify_auditor_exception(self.rt, run["run_id"], lab, self.freeze_info)["checks"]
        self.assertIs(checks["isolation"], False)

    def test_auditor_rejected_invalid_observed(self) -> None:
        run = self._auditor_run()
        lab = _lab_result(self.cid, run["run_id"])
        lab["observed"]["xss"] = {"observed": None, "benign": None, "reason": "xss: invalid"}
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "policy_violation")
        checks = verify_auditor_exception(self.rt, run["run_id"], lab, self.freeze_info)["checks"]
        self.assertIs(checks["observed_complete"], False)

    def test_auditor_rejected_phase(self) -> None:
        self.rt.transition(self.cid, "running")
        red_run = self.rt.start_run(self.cid, "red", 1, self._key())
        red_rid = red_run["run_id"]
        self.assertEqual(red_run["phase"], "red")
        lab = _lab_result(self.cid, red_rid)
        checks = verify_auditor_exception(self.rt, red_rid, lab, self.freeze_info)["checks"]
        self.assertIs(checks["phase_verify"], False)
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "policy_violation")

    def test_other_role_rejected(self) -> None:
        self.rt.transition(self.cid, "running")
        red_run = self.rt.start_run(self.cid, "red", 1, self._key())
        red_rid = red_run["run_id"]
        self.assertNotEqual(red_run["actor"]["role"], "auditor")
        lab = _lab_result(self.cid, red_rid)
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "policy_violation")

    def test_auditor_rejected_no_evaluation_context(self) -> None:
        run = self.rt.start_run(self.cid, "verify", 1, self._key())
        self.assertEqual(run["actor"]["role"], "auditor")
        lab = _lab_result(self.cid, run["run_id"])
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "policy_violation")
        checks = verify_auditor_exception(self.rt, run["run_id"], lab, self.freeze_info)["checks"]
        self.assertIs(checks["eval_context_recorded"], False)

    def test_auditor_rejected_manifest_id_mismatch(self) -> None:
        run = self._auditor_run()
        lab = _lab_result(self.cid, run["run_id"])
        mismatched = _freeze_info("m-2")
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, mismatched, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "policy_violation")
        checks = verify_auditor_exception(self.rt, run["run_id"], lab, mismatched)["checks"]
        self.assertIs(checks["manifest_frozen_id_match"], False)

    def test_invalid_observation_coverage_failed(self) -> None:
        oracle_run = self._oracle_run()
        lab = _lab_result(self.cid, oracle_run["run_id"])
        lab["observed"]["xss"] = {"observed": None, "benign": None, "reason": "xss: invalid"}
        result = build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        report = self.store.get_object(result["report_ref"])
        coverage = {e["category"]: e for e in report["coverage"]["categories"]}
        self.assertEqual(coverage["xss"]["status"], "failed")
        findings = {f["category"]: f for f in report["findings"]}
        self.assertEqual(findings["xss"]["claims"][0]["status"], "hypothesis")
        self.assertNotIn("xss", result["diagnostic_refs"]["root_causes"])
        self.assertEqual(compute_validation_verdict(lab), "undetermined")

    def test_fn_verdict_fail(self) -> None:
        lab = _lab_result(self.cid, "r-fn")
        lab["confusion"]["totals"]["FN"] = 1
        self.assertEqual(compute_validation_verdict(lab), "FAIL")

    def test_run_not_running(self) -> None:
        oracle_run = self._oracle_run()
        rid = oracle_run["run_id"]
        self.rt.finish_run(rid, "completed")
        lab = _lab_result(self.cid, rid)
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "hold")

    def test_missing_observation_key(self) -> None:
        oracle_run = self._oracle_run()
        lab = _lab_result(self.cid, oracle_run["run_id"])
        del lab["oracle_result"]["output"]["observations"]["dependency"]
        with self.assertRaises(DataError) as ctx:
            build_lab_report(self.rt, lab, self.freeze_info, "t1", "v1")
        self.assertEqual(ctx.exception.reason_code, "hold")


_BINDING_KEYS = (
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


def _oracle_output(seed, dependency_version, observed_map):
    marker = "record-marker-%d" % seed
    obs = {}
    if observed_map.get("authz"):
        obs["auth_guest"] = {"status": 200, "body": {"record": marker}, "error": None}
    else:
        obs["auth_guest"] = {"status": 403, "body": {"error": "forbidden"}, "error": None}
    obs["auth_admin"] = {"status": 200, "body": {"role": "admin", "record": marker}, "error": None}
    obs["public"] = {"status": 200, "body": {"message": "public-ok"}, "error": None}
    if observed_map.get("sqli"):
        rows = ["alice", "bob", "seed%d" % seed]
    else:
        rows = []
    obs["sql_attack"] = {"status": 200, "body": {"rows": rows}, "error": None}
    obs["sql_normal"] = {"status": 200, "body": {"rows": ["alice"]}, "error": None}
    if observed_map.get("path_traversal"):
        obs["path_attack"] = {"status": 200, "body": {"text": "SECRET-MARKER-%d\n" % seed}, "error": None}
    else:
        obs["path_attack"] = {"status": 403, "body": {"error": "forbidden"}, "error": None}
    obs["path_normal"] = {"status": 200, "body": {"text": "hello-public\n"}, "error": None}
    probe_marker = "seed-%d" % seed
    if observed_map.get("xss"):
        attack_dom = '<html data-aym-probe="%s"><div id="out">x</div></html>' % probe_marker
    else:
        attack_dom = '<html><div id="out">hello</div></html>'
    normal_dom = '<html><div id="out">hello</div></html>'
    obs["xss_attack"] = {"returncode": 0, "error": None, "dom": attack_dom}
    obs["xss_normal"] = {"returncode": 0, "error": None, "dom": normal_dom}
    if observed_map.get("dependency_cve"):
        second_status, second_error = 200, None
    else:
        second_status, second_error = None, "SSLError"
    obs["dependency"] = {
        "returncode": 0,
        "parsed": {
            "library_version": dependency_version,
            "initial_status": 200,
            "fresh_status": None,
            "fresh_error": "SSLError",
            "benign_status": 200,
            "benign_error": None,
            "endpoint": "loopback_tls",
            "second_status": second_status,
            "second_error": second_error,
        },
    }
    return {"output": {"observations": obs}}


def _blue_output(detected_map, image_id):
    findings = []
    for cat in CATEGORIES:
        findings.append({"category": cat, "detected": bool(detected_map.get(cat))})
    return {
        "output": {"findings": findings},
        "isolation": {
            "network": "none",
            "readonly": True,
            "mounts_verified": True,
            "image_id": image_id,
        },
    }


def _all_true():
    return {cat: True for cat in CATEGORIES}


class BenchmarkProducerIntegrationTest(unittest.TestCase):
    """benchmark.run_assessment_lab -> build_lab_report -> assessment.build_report の実連結。"""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "ws"
        self.tr = root / "tr"
        self.ws.mkdir()
        self.tr.mkdir()
        self.store = Store(self.ws, self.tr)
        self.rt = Runtime(self.store)
        scope = {"targets": [{"kind": "fixture", "identifier": "t1"}],
                 "max_targets": 1}
        auth = {"operator_id": "op1",
                "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope_digest": digest(scope)}
        c = self.rt.create_campaign("INT-TEST-1", scope, auth, "k1")
        self.cid = c["campaign_id"]
        self._key_seq = 0

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _key(self) -> str:
        self._key_seq += 1
        return f"k-{self._key_seq}"

    def _auditor_run(self, cid):
        ref = self.store.put_object({
            "manifest_id": "m-1",
            "status": "frozen",
            "schema_version": "1.1",
        })
        run = self.rt.start_run(
            cid, "verify", 1, self._key(),
            evaluation_context={
                "evaluation_manifest_ref": ref,
                "evaluation_manifest_id": "m-1",
            })
        return run, ref

    def _formal_trusted_root(self, tmp_path: Path, campaign_id: str, run_id: str):
        root = Path(tmp_path)
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

        (root / "identity.json").write_bytes(
            json.dumps(identity_manifest).encode("utf-8"))

        from ai_yori_murasaki import evaluation_identity
        preflight_result = evaluation_identity.preflight(
            identity_manifest, root, bindings)
        (root / "freeze_digest.json").write_bytes(
            json.dumps({"manifest_digest": preflight_result["manifest_digest"]}).encode("utf-8"))

        return root, bindings

    def _write_manifest(self, path: Path, **overrides) -> Path:
        base = {
            "schema_version": "1.0",
            "image_id": next(iter(ALLOWED_IMAGE_IDS)),
            "seed": 7,
            "deps": "old",
            "dependency_version": "2.31.0",
            "formal": True,
            "campaign_id": self.cid,
        }
        base.update(overrides)
        path.write_text(json.dumps(base), encoding="utf-8")
        return path

    def _fake_run_fixture(self, image_id):
        oracle = _oracle_output(7, "2.31.0", _all_true())
        blue = _blue_output(_all_true(), image_id)

        def fake(stage, image_id_arg, mode, seed=1, deps="old"):
            if mode == "blue":
                return blue
            return oracle

        return fake

    def test_producer_connected_end_to_end(self) -> None:
        run, eval_ref = self._auditor_run(self.cid)
        rid = run["run_id"]
        root = Path(self.tmp.name) / "trusted"
        root.mkdir()
        trusted_root, bindings = self._formal_trusted_root(root, self.cid, rid)

        staging = Path(self.tmp.name) / "staging"
        (staging / "blue").mkdir(parents=True)
        (staging / "oracle").mkdir(parents=True)

        manifest_path = Path(self.tmp.name) / "lab_manifest.json"
        self._write_manifest(
            manifest_path,
            trusted_root=str(trusted_root),
            identity_manifest="identity.json",
            bindings=bindings,
            freeze_digest="freeze_digest.json",
            run_id=rid,
            target_version="v1",
        )

        image_id = next(iter(ALLOWED_IMAGE_IDS))
        with mock.patch.object(benchmark, "run_fixture",
                               side_effect=self._fake_run_fixture(image_id)):
            result = run_assessment_lab(self.rt, staging, manifest_path)

        self.assertEqual(result["status"], "pass")
        self.assertIs(result.get("formal_verified"), True)
        self.assertEqual(result["diagnostics"]["status"], "built")

        report = self.store.get_object(result["diagnostics"]["report_ref"])
        self.assertEqual(report["schema_version"], "1.0.0")
        self.assertEqual(report["run_id"], rid)
        self.assertEqual(report["actor"], "auditor")
        self.assertEqual(len(report["findings"]), 5)
        self.assertEqual(report["manifest_ref"], eval_ref)
        self.assertIs(result["diagnostics"]["auditor_exception"]["allowed"], True)

    def test_producer_skipped_when_run_not_active(self) -> None:
        run, _ = self._auditor_run(self.cid)
        rid = run["run_id"]
        self.rt.finish_run(rid, "completed")

        staging = Path(self.tmp.name) / "staging"
        (staging / "blue").mkdir(parents=True)
        (staging / "oracle").mkdir(parents=True)

        manifest_path = Path(self.tmp.name) / "lab_manifest.json"
        self._write_manifest(
            manifest_path,
            formal=False,
            run_id=rid,
            target_version="v1",
        )

        image_id = next(iter(ALLOWED_IMAGE_IDS))
        with mock.patch.object(benchmark, "run_fixture",
                               side_effect=self._fake_run_fixture(image_id)):
            result = run_assessment_lab(self.rt, staging, manifest_path)

        self.assertEqual(result["diagnostics"]["status"], "skipped")

    def test_cli_lab_calls_producer(self) -> None:
        run, _ = self._auditor_run(self.cid)
        rid = run["run_id"]
        root = Path(self.tmp.name) / "trusted"
        root.mkdir()
        trusted_root, bindings = self._formal_trusted_root(root, self.cid, rid)

        staging = Path(self.tmp.name) / "staging"
        (staging / "blue").mkdir(parents=True)
        (staging / "oracle").mkdir(parents=True)

        manifest_path = Path(self.tmp.name) / "lab_manifest.json"
        self._write_manifest(
            manifest_path,
            trusted_root=str(trusted_root),
            identity_manifest="identity.json",
            bindings=bindings,
            freeze_digest="freeze_digest.json",
            run_id=rid,
            target_version="v1",
        )

        args = mock.Mock()
        args.staging_root = str(staging)
        args.manifest = str(manifest_path)
        args.campaign_id = None
        args.run_id = None

        image_id = next(iter(ALLOWED_IMAGE_IDS))
        with mock.patch.object(benchmark, "run_fixture",
                               side_effect=self._fake_run_fixture(image_id)):
            cid_out, rid_out, result, code = cli_assessment._do_lab(args, self.rt)

        self.assertEqual(cid_out, self.cid)
        self.assertEqual(rid_out, rid)
        self.assertEqual(result["diagnostics"]["status"], "built")
        self.assertEqual(code, 0)


if __name__ == "__main__":
    unittest.main()
