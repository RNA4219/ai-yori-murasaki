"""Assessment import tests: idempotency, dedup, coverage, context guards."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_yori_murasaki import assessment
from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


class AssessmentImportTest(unittest.TestCase):
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
        from ai_yori_murasaki.jsonio import digest
        auth = {"operator_id": "op1",
                "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope_digest": digest(scope)}
        c = self.rt.create_campaign("INT-TEST-1", scope, auth, "k1")
        self.cid = c["campaign_id"]
        run = self.rt.start_run(self.cid, "verify", 1, "k2")
        self.rid = run["run_id"]
        self.ctx = {
            "target": "t1",
            "target_version": "v1",
            "actor": run["actor"]["role"],
            "previous_report_ref": None,
        }

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _trivy(self, results: list) -> bytes:
        return json.dumps({"Results": results}).encode()

    def _vuln(self, cve: str, pkg: str = "libfoo") -> dict:
        return {"VulnerabilityID": cve, "PkgName": pkg}

    def _import(self, raw: bytes, ctx: dict | None = None) -> dict:
        return assessment.import_artifact(
            self.rt, self.cid, self.rid, "trivy", raw,
            ctx if ctx is not None else self.ctx)

    def _report(self, report_ref: str) -> dict:
        obj = self.store.get_object(report_ref)
        return obj

    def test_single_import_cve_candidate_only(self) -> None:
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-0001")]}])
        result = self._import(raw)
        report = self._report(result["report_ref"])
        findings = report["findings"]
        self.assertEqual(len(findings), 1)
        finding = findings[0]
        cve = finding["classification"]["cve"]
        self.assertEqual(cve["status"], "candidate")
        self.assertEqual(cve["ids"], ["CVE-2024-0001"])
        self.assertTrue(finding["claims"])
        for claim in finding["claims"]:
            self.assertEqual(claim["status"], "hypothesis")

    def test_repeated_same_entries_single_artifact_attempts(self) -> None:
        vulns = [self._vuln("CVE-2024-0001") for _ in range(3)]
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": vulns}])
        result = self._import(raw)
        report = self._report(result["report_ref"])
        self.assertEqual(len(report["findings"]), 1)
        self.assertEqual(report["findings"][0]["attempts"], 3)

    def test_same_cve_different_pkg_two_findings(self) -> None:
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-0001", "libfoo"),
            self._vuln("CVE-2024-0001", "libbar")]}])
        result = self._import(raw)
        report = self._report(result["report_ref"])
        self.assertEqual(len(report["findings"]), 2)

    def test_different_trivy_target_two_findings(self) -> None:
        raw = self._trivy([
            {"Target": "t1", "Vulnerabilities": [self._vuln("CVE-2024-0001")]},
            {"Target": "t2", "Vulnerabilities": [self._vuln("CVE-2024-0001")]},
        ])
        result = self._import(raw)
        report = self._report(result["report_ref"])
        self.assertEqual(len(report["findings"]), 2)

    def test_empty_results_all_not_executed(self) -> None:
        raw = self._trivy([])
        result = self._import(raw)
        report = self._report(result["report_ref"])
        self.assertEqual(report["findings"], [])
        coverage = report["coverage"]
        self.assertIsInstance(coverage, dict)
        self.assertIn("created_at", coverage)
        categories = coverage["categories"]
        self.assertEqual(len(categories), 5)
        for entry in categories:
            self.assertEqual(entry["status"], "not_executed")

    def test_external_context_injection_raises(self) -> None:
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-0001")]}])
        bad_ctx = {
            **self.ctx,
            "classification": {
                "cve": {
                    "status": "candidate",
                    "ids": ["CVE-2024-0001"],
                    "applicability": {},
                }
            },
        }
        with self.assertRaises(DataError):
            self._import(raw, bad_ctx)


if __name__ == "__main__":
    unittest.main()
