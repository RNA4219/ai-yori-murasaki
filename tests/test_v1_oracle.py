"""Oracle predicate evaluation and FinalVerdict derivation tests."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_yori_murasaki.jsonio import ContractError, DataError
from ai_yori_murasaki.oracle import Oracle, build_final_verdict
from ai_yori_murasaki.storage import Store


class OracleTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "ws"
        self.tr = root / "tr"
        self.ws.mkdir()
        self.tr.mkdir()
        self.store = Store(self.ws, self.tr)
        self.oracle = Oracle()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _predicate(self, expected: dict, actual: dict) -> dict:
        ref = self.store.put_object(actual)
        return self.oracle.make_predicate("p1", "exit_code", ref, expected)

    def test_pass_when_actual_matches(self) -> None:
        pred = self._predicate({"code": 0}, {"code": 0})
        result = self.oracle.evaluate(pred, self.store)
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["outcome"])
        self.assertEqual(result["reason_code"], "exploit_succeeded")

    def test_fail_when_actual_differs(self) -> None:
        pred = self._predicate({"code": 0}, {"code": 1})
        result = self.oracle.evaluate(pred, self.store)
        self.assertEqual(result["verdict"], "FAIL")
        self.assertFalse(result["outcome"])
        self.assertEqual(result["reason_code"], "exploit_not_succeeded")

    def test_hold_on_missing_cas(self) -> None:
        pred = self.oracle.make_predicate("p1", "exit_code", "0" * 64,
                                          {"code": 0})
        result = self.oracle.evaluate(pred, self.store)
        self.assertEqual(result["verdict"], "HOLD")
        self.assertIsNone(result["outcome"])
        self.assertEqual(result["reason_code"], "evidence_missing")

    def test_hold_on_incomplete(self) -> None:
        pred = self._predicate({"code": 0}, {"code": 0})
        result = self.oracle.evaluate(pred, self.store, completion="incomplete")
        self.assertEqual(result["verdict"], "HOLD")
        self.assertEqual(result["reason_code"], "not_executed")

    def test_hold_on_bad_actual_type(self) -> None:
        ref = self.store.put_object({"code": "zero"})
        pred = self.oracle.make_predicate("p1", "exit_code", ref, {"code": 0})
        result = self.oracle.evaluate(pred, self.store)
        self.assertEqual(result["verdict"], "HOLD")
        self.assertEqual(result["reason_code"], "evidence_mismatch")

    def test_invalid_kind_rejected(self) -> None:
        with self.assertRaises(ContractError):
            self.oracle.make_predicate("p1", "bogus", "0" * 64, {})

    def test_bool_rejected_for_int(self) -> None:
        with self.assertRaises(ContractError):
            self.oracle.make_predicate("p1", "exit_code", "0" * 64,
                                       {"code": True})

    def test_http_status_range(self) -> None:
        with self.assertRaises(ContractError):
            self.oracle.make_predicate("p1", "http_status", "0" * 64,
                                       {"method": "GET", "path": "/",
                                        "status": 99})

    def test_network_port_range(self) -> None:
        with self.assertRaises(ContractError):
            self.oracle.make_predicate("p1", "network_state", "0" * 64,
                                       {"host": "h", "port": 0,
                                        "reachable": True})

    def test_evidence_hash_format(self) -> None:
        with self.assertRaises(ContractError):
            self.oracle.make_predicate("p1", "evidence_hash", "0" * 64,
                                       {"ref": "NOTHEX"})

    def test_final_verdict_remediation_success(self) -> None:
        report = self.store.put_object({
            "campaign_id": "c1", "run_id": "r1", "suite_id": "s1",
            "passed": True, "checks": [{"name": "unit", "passed": True}]})
        pred = self._predicate({"code": 0}, {"code": 1})
        result = self.oracle.evaluate(pred, self.store)
        verdict = build_final_verdict("c1", "r1", result, "completed",
                                      self.store, remediation_id="rem1",
                                      test_report_refs=(report,))
        self.assertTrue(verdict["remediation_success"])
        self.assertTrue(verdict["benign_regression_passed"])

    def test_final_verdict_failed_report(self) -> None:
        report = self.store.put_object({
            "campaign_id": "c1", "run_id": "r1", "suite_id": "s1",
            "passed": False, "checks": [{"name": "unit", "passed": False}]})
        pred = self._predicate({"code": 0}, {"code": 1})
        result = self.oracle.evaluate(pred, self.store)
        verdict = build_final_verdict("c1", "r1", result, "completed",
                                      self.store, remediation_id="rem1",
                                      test_report_refs=(report,))
        self.assertFalse(verdict["remediation_success"])
        self.assertFalse(verdict["benign_regression_passed"])

    def test_final_verdict_invalid_hash(self) -> None:
        pred = self._predicate({"code": 0}, {"code": 1})
        result = self.oracle.evaluate(pred, self.store)
        with self.assertRaises(ContractError):
            build_final_verdict("c1", "r1", result, "completed", self.store,
                                remediation_id="rem1",
                                test_report_refs=("0" * 64,))

    def test_final_verdict_incomplete_no_success(self) -> None:
        pred = self._predicate({"code": 0}, {"code": 1})
        result = self.oracle.evaluate(pred, self.store, completion="incomplete")
        verdict = build_final_verdict("c1", "r1", result, "incomplete",
                                      self.store)
        self.assertFalse(verdict["remediation_success"])
        self.assertIsNone(verdict["benign_regression_passed"])

    def test_final_verdict_fake_flags_rejected(self) -> None:
        report = self.store.put_object({
            "campaign_id": "c1", "run_id": "r1", "suite_id": "s1",
            "passed": False, "checks": [{"name": "unit", "passed": False}]})
        pred = self._predicate({"code": 0}, {"code": 1})
        result = self.oracle.evaluate(pred, self.store)
        verdict = build_final_verdict("c1", "r1", result, "completed",
                                      self.store, remediation_id="rem1",
                                      test_report_refs=(report,))
        self.assertFalse(verdict["remediation_success"])
        self.assertFalse(verdict["benign_regression_passed"])

    def _blind_objects(self, red_obs, blue_obs, detection_count=None):
        red = {"observations": red_obs}
        blue = {"observations": blue_obs}
        if detection_count is not None:
            blue["detection_count"] = detection_count
        return self.store.put_object(red), self.store.put_object(blue)

    def _valid_obs(self):
        red = [{"method": "GET", "path": "/record?id=x", "status": 200}
               for _ in range(3)]
        blue = [{"method": "GET", "path": "/record", "status": 200,
                 "role": "guest"} for _ in range(3)]
        return red, blue

    def test_blind_predicate_valid_passes(self) -> None:
        red, blue = self._valid_obs()
        red_ref, blue_ref = self._blind_objects(red, blue, detection_count=3)
        out = self.oracle.blind_predicate("bp1", red_ref, blue_ref, self.store)
        pred = out["predicate"]
        self.assertEqual(pred["kind"], "test_pass")
        self.assertEqual(pred["expected"],
                         {"suite_id": "blue-detection", "passed": True})
        result = self.oracle.evaluate(pred, self.store)
        self.assertEqual(result["verdict"], "PASS")
        self.assertTrue(result["outcome"])
        report = self.store.get_object(out["report_ref"])
        self.assertTrue(report["passed"])
        self.assertTrue(all(c["passed"] for c in report["checks"]))

    def test_blind_predicate_fake_flags_rejected(self) -> None:
        red, blue = self._valid_obs()
        cases = {
            "missing_red_obs": ({"observations": [], "passed": True},
                                {"observations": blue, "detection_count": 3,
                                 "passed": True}),
            "missing_blue_obs": ({"observations": red, "passed": True},
                                 {"observations": [], "detection_count": 0,
                                  "passed": True}),
        }
        for name, (red_obj, blue_obj) in cases.items():
            with self.subTest(name=name):
                red_ref = self.store.put_object(red_obj)
                blue_ref = self.store.put_object(blue_obj)
                out = self.oracle.blind_predicate("bp1", red_ref, blue_ref,
                                                  self.store)
                result = self.oracle.evaluate(out["predicate"], self.store)
                self.assertEqual(result["verdict"], "FAIL")
                self.assertFalse(result["outcome"])

    def test_blind_predicate_count_mismatch_fails(self) -> None:
        red, blue = self._valid_obs()
        for bad_count in (2, 4, True, "3"):
            with self.subTest(bad_count=bad_count):
                red_ref, blue_ref = self._blind_objects(
                    red, blue, detection_count=bad_count)
                out = self.oracle.blind_predicate("bp1", red_ref, blue_ref,
                                                  self.store)
                result = self.oracle.evaluate(out["predicate"], self.store)
                self.assertEqual(result["verdict"], "FAIL")
                report = self.store.get_object(out["report_ref"])
                checks = {c["name"]: c["passed"] for c in report["checks"]}
                self.assertFalse(checks["detection_count"])

    def test_blind_predicate_raw_method_mismatch_fails(self) -> None:
        red, blue = self._valid_obs()
        blue[0] = dict(blue[0], method="POST")
        red_ref, blue_ref = self._blind_objects(red, blue, detection_count=3)
        out = self.oracle.blind_predicate("bp1", red_ref, blue_ref, self.store)
        result = self.oracle.evaluate(out["predicate"], self.store)
        self.assertEqual(result["verdict"], "FAIL")
        report = self.store.get_object(out["report_ref"])
        checks = {c["name"]: c["passed"] for c in report["checks"]}
        self.assertFalse(checks["raw_match"])
        self.assertFalse(checks["blue_observations"])

    def test_no_llm_import(self) -> None:
        import ai_yori_murasaki.oracle as mod
        source = Path(mod.__file__).read_text()
        for banned in ("import openai", "import requests", "import subprocess",
                       "import socket", "urllib"):
            self.assertNotIn(banned, source)


if __name__ == "__main__":
    unittest.main()
