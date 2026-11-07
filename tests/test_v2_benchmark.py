"""benchmark.py Phase 1 の単体テスト（Docker 不要）。"""

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki import benchmark
from ai_yori_murasaki.benchmark import (
    ALLOWED_IMAGE_IDS,
    _decide_status,
    _validate_manifest,
    run_assessment_lab,
)
from ai_yori_murasaki.benchmark_evaluation import (
    CATEGORIES,
    confusion,
    evaluate_observations,
)


IMAGE_ID = next(iter(ALLOWED_IMAGE_IDS))


def _valid_manifest(**overrides):
    base = {
        "schema_version": "1.0",
        "image_id": IMAGE_ID,
        "seed": 7,
        "deps": "old",
        "campaign_id": "camp-1",
        "run_id": "run-1",
        "dependency_version": "2.31.0",
        "formal": False,
    }
    base.update(overrides)
    return base


def _oracle_output(seed, dependency_version, observed_map):
    """observed_map: cat -> bool で、その観測を成立させる oracle 出力を作る。"""
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
        obs["path_attack"] = {
            "status": 200,
            "body": {"text": "SECRET-MARKER-%d\n" % seed},
            "error": None,
        }
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


def _blue_output(detected_map):
    findings = []
    for cat in CATEGORIES:
        findings.append({"category": cat, "detected": bool(detected_map.get(cat))})
    return {"output": {"findings": findings}}


def _all_true():
    return {cat: True for cat in CATEGORIES}


def _all_false():
    return {cat: False for cat in CATEGORIES}


class ValidateManifestTests(unittest.TestCase):
    def _assert_invalid(self, manifest):
        with self.assertRaises(DataError) as ctx:
            _validate_manifest(manifest)
        self.assertEqual(ctx.exception.reason_code, "input_invalid")

    def test_valid_manifest_normalized(self):
        params = _validate_manifest(_valid_manifest())
        self.assertEqual(params["image_id"], IMAGE_ID)
        self.assertEqual(params["seed"], 7)
        self.assertEqual(params["deps"], "old")
        self.assertEqual(params["campaign_id"], "camp-1")
        self.assertEqual(params["run_id"], "run-1")
        self.assertEqual(params["dependency_version"], "2.31.0")

    def test_not_a_dict(self):
        self._assert_invalid([])

    def test_bad_schema_version(self):
        self._assert_invalid(_valid_manifest(schema_version="2.0"))

    def test_unknown_image_id(self):
        self._assert_invalid(_valid_manifest(image_id="sha256:" + "0" * 64))

    def test_allowed_image_id_pinned_to_built_digest(self):
        self.assertEqual(len(ALLOWED_IMAGE_IDS), 1)
        self.assertEqual(
            next(iter(ALLOWED_IMAGE_IDS)),
            "sha256:991b10cf0619c626209f5c47159a8f614c8aa31f738e8c2e4e5528362eeee9e8",
        )
        params = _validate_manifest(_valid_manifest())
        self.assertEqual(
            params["image_id"],
            "sha256:991b10cf0619c626209f5c47159a8f614c8aa31f738e8c2e4e5528362eeee9e8",
        )
        self._assert_invalid(_valid_manifest(image_id="sha256:" + "0" * 64))

    def test_seed_out_of_range(self):
        self._assert_invalid(_valid_manifest(seed=-1))
        self._assert_invalid(_valid_manifest(seed=2147483648))

    def test_seed_bool_rejected(self):
        self._assert_invalid(_valid_manifest(seed=True))

    def test_bad_deps(self):
        self._assert_invalid(_valid_manifest(deps="latest"))

    def test_bad_dependency_version(self):
        self._assert_invalid(_valid_manifest(dependency_version="1.0.0"))

    def test_empty_campaign_id(self):
        self._assert_invalid(_valid_manifest(campaign_id=""))

    def test_empty_run_id(self):
        self._assert_invalid(_valid_manifest(run_id=""))

    def test_missing_key(self):
        m = _valid_manifest()
        del m["seed"]
        self._assert_invalid(m)


class DecideStatusTests(unittest.TestCase):
    def _observed(self, benign_map=None):
        benign_map = benign_map or {}
        return {
            cat: {"observed": True, "benign": benign_map.get(cat, True), "reason": None}
            for cat in CATEGORIES
        }

    def _conf(self, tp=0, fp=0, fn=0, tn=0, invalid=0):
        totals = {"TP": tp, "FP": fp, "FN": fn, "TN": tn, "invalid": invalid}
        return {"categories": {}, "totals": totals}

    def test_all_tp_tn_benign_true_pass(self):
        observed = self._observed()
        conf = self._conf(tp=3, tn=2)
        self.assertEqual(_decide_status(observed, conf), "pass")

    def test_fn_fail(self):
        observed = self._observed()
        conf = self._conf(tp=4, tn=0, fn=1)
        self.assertEqual(_decide_status(observed, conf), "fail")

    def test_fp_fail(self):
        observed = self._observed()
        conf = self._conf(tp=4, tn=0, fp=1)
        self.assertEqual(_decide_status(observed, conf), "fail")

    def test_invalid_hold(self):
        observed = self._observed()
        conf = self._conf(tp=4, tn=0, invalid=1)
        self.assertEqual(_decide_status(observed, conf), "hold")

    def test_benign_false_hold(self):
        observed = self._observed(benign_map={"sqli": False})
        conf = self._conf(tp=5, tn=0)
        self.assertEqual(_decide_status(observed, conf), "hold")


class EvaluationIntegrationTests(unittest.TestCase):
    """evaluate_observations / confusion は実物を呼ぶ。"""

    def test_all_observed_all_detected_pass(self):
        seed = 7
        dep = "2.31.0"
        oracle = _oracle_output(seed, dep, _all_true())
        observed = evaluate_observations(oracle, seed, dep)
        for cat in CATEGORIES:
            self.assertTrue(observed[cat]["observed"], cat)
            self.assertTrue(observed[cat]["benign"], cat)
        blue = _blue_output(_all_true())
        conf = confusion(observed, blue)
        self.assertEqual(conf["totals"]["TP"], 5)
        self.assertEqual(conf["totals"]["invalid"], 0)
        self.assertEqual(_decide_status(observed, conf), "pass")

    def test_missed_detection_fn_fail(self):
        seed = 7
        dep = "2.31.0"
        oracle = _oracle_output(seed, dep, _all_true())
        observed = evaluate_observations(oracle, seed, dep)
        detected = _all_true()
        detected["sqli"] = False
        conf = confusion(observed, _blue_output(detected))
        self.assertEqual(conf["totals"]["FN"], 1)
        self.assertEqual(_decide_status(observed, conf), "fail")

    def test_false_positive_fail(self):
        seed = 7
        dep = "2.31.0"
        oracle = _oracle_output(seed, dep, _all_false())
        observed = evaluate_observations(oracle, seed, dep)
        detected = _all_false()
        detected["xss"] = True
        conf = confusion(observed, _blue_output(detected))
        self.assertEqual(conf["totals"]["FP"], 1)
        self.assertEqual(_decide_status(observed, conf), "fail")


class RunAssessmentLabTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        (self.root / "blue").mkdir()
        (self.root / "oracle").mkdir()
        self.manifest_path = self.root / "manifest.json"
        self.manifest_path.write_text(
            json.dumps(_valid_manifest()), encoding="utf-8"
        )

    def test_run_fixture_dataerror_becomes_hold(self):
        def fake_run_fixture(stage, image_id, mode, seed=1, deps="old"):
            raise DataError("docker unavailable")

        with mock.patch.object(benchmark, "run_fixture", side_effect=fake_run_fixture):
            with self.assertRaises(DataError) as ctx:
                run_assessment_lab(None, self.root, self.manifest_path)
        self.assertEqual(ctx.exception.reason_code, "hold")

    def test_integration_pass_with_rt_none(self):
        seed = 7
        dep = "2.31.0"
        oracle = _oracle_output(seed, dep, _all_true())
        blue = _blue_output(_all_true())

        def fake_run_fixture(stage, image_id, mode, seed=1, deps="old"):
            if mode == "blue":
                return blue
            return oracle

        with mock.patch.object(benchmark, "run_fixture", side_effect=fake_run_fixture):
            result = run_assessment_lab(None, self.root, self.manifest_path)

        self.assertEqual(result["status"], "hold")
        self.assertEqual(result["reason"], "non_formal_run")
        self.assertNotIn("formal_verified", result)
        self.assertEqual(result["campaign_id"], "camp-1")
        self.assertEqual(result["run_id"], "run-1")
        self.assertEqual(result["image_id"], IMAGE_ID)
        self.assertEqual(result["seed"], seed)
        self.assertEqual(result["deps"], "old")
        self.assertEqual(result["dependency_version"], dep)
        self.assertIn("observed", result)
        self.assertIn("confusion", result)
        self.assertIn("blue_result", result)
        self.assertIn("oracle_result", result)
        self.assertNotIn("lab_result_ref", result)

    def test_integration_missing_stage_hold(self):
        (self.root / "oracle").rmdir()
        with self.assertRaises(DataError) as ctx:
            run_assessment_lab(None, self.root, self.manifest_path)
        self.assertEqual(ctx.exception.reason_code, "hold")

    def test_integration_invalid_manifest(self):
        self.manifest_path.write_text(
            json.dumps(_valid_manifest(schema_version="9.9")), encoding="utf-8"
        )
        with self.assertRaises(DataError) as ctx:
            run_assessment_lab(None, self.root, self.manifest_path)
        self.assertEqual(ctx.exception.reason_code, "input_invalid")


if __name__ == "__main__":
    unittest.main()
