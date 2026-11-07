"""End-to-end enrichment integration: build_report -> semantic validation ->
persist -> load_report/project_report -> CLI path -> Blue withholding.

This test wires the real code paths together (no mocks):
- assessment.build_report with context["enrichment"]
- assessment_enrichment.build_enrichment_records / validate_enrichment_records
- assessment.load_report / project_report (operator projection, 12 keys)
- cli_assessment._do_import with --kev-meta/--epss-meta
- Blue withholding fixed response
"""
from __future__ import annotations

import argparse
import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

from ai_yori_murasaki import assessment
from ai_yori_murasaki import assessment_enrichment as ae
from ai_yori_murasaki import cli_assessment
from ai_yori_murasaki.assessment_contracts import AssessmentContractError
from ai_yori_murasaki.enrichment import import_snapshot
from ai_yori_murasaki.jsonio import DataError, digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


KEV_JSON = json.dumps({
    "catalogVersion": "v1",
    "dateReleased": "2026-01-01",
    "count": 1,
    "vulnerabilities": [
        {
            "cveID": "CVE-2024-35195",
            "vendorProject": "psf",
            "product": "requests",
            "vulnerabilityName": "x",
            "dateAdded": "2024-01-01",
            "shortDescription": "x",
            "requiredAction": "x",
            "dueDate": "2024-06-01",
        }
    ],
}).encode("utf-8")

EPSS_CSV = "cve,epss,percentile\nCVE-2024-35195,0.05,0.94\n".encode("utf-8")

PROJECTION_KEYS = {
    "finding_id",
    "kind",
    "cve",
    "status",
    "epss",
    "percentile",
    "source_url",
    "source_version",
    "fetched_at",
    "as_of",
    "expires_at",
    "reason",
}


def _now_iso() -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()


def _by_key(records, finding_id, kind):
    for record in records:
        if record["finding_id"] == finding_id and record["kind"] == kind:
            return record
    raise AssertionError(f"record not found: {finding_id}/{kind}")


class EnrichmentIntegrationTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.root = root
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
        c = self.rt.create_campaign("INT-ENRICH-1", scope, auth, "k1")
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

    # -- fixtures ---------------------------------------------------------

    def _import_kev(self, ttl_seconds=None) -> dict:
        return import_snapshot(
            self.store, "kev", KEV_JSON,
            source_url="https://example.test/kev.json",
            fetched_at=_now_iso(),
            source_version="v1",
            as_of=_now_iso(),
            ttl_seconds=ttl_seconds,
        )

    def _import_epss(self, ttl_seconds=None) -> dict:
        return import_snapshot(
            self.store, "epss", EPSS_CSV,
            source_url="https://example.test/epss.csv",
            fetched_at=_now_iso(),
            source_version="v1",
            as_of=_now_iso(),
            ttl_seconds=ttl_seconds,
        )

    def _trivy(self, results: list) -> bytes:
        return json.dumps({"Results": results}).encode()

    def _vuln(self, cve: str, pkg: str = "libfoo") -> dict:
        return {"VulnerabilityID": cve, "PkgName": pkg}

    def _write(self, name: str, data) -> str:
        p = self.root / name
        if isinstance(data, (bytes, bytearray)):
            p.write_bytes(bytes(data))
        else:
            p.write_text(json.dumps(data), encoding="utf-8")
        return str(p)

    def _cli_args(self, *, input_path, context_path, kev_meta_path=None,
                  epss_meta_path=None, fmt="trivy") -> argparse.Namespace:
        return argparse.Namespace(
            format=fmt,
            input_path=input_path,
            context_path=context_path,
            campaign_id=self.cid,
            run_id=self.rid,
            kev_meta_path=kev_meta_path,
            epss_meta_path=epss_meta_path,
        )

    def _run_cli_import(self, raw: bytes, ctx: dict, kev_meta=None,
                        epss_meta=None) -> dict:
        input_path = self._write("trivy.json", raw)
        context_path = self._write("context.json", ctx)
        kev_path = self._write("kev_meta.json", kev_meta) if kev_meta else None
        epss_path = (self._write("epss_meta.json", epss_meta)
                     if epss_meta else None)
        args = self._cli_args(
            input_path=input_path, context_path=context_path,
            kev_meta_path=kev_path, epss_meta_path=epss_path)
        _cid, _rid, result, code = cli_assessment._do_import(args, self.rt)
        self.assertEqual(code, 0)
        return result

    # -- (A) build_report path -------------------------------------------

    def test_build_report_with_enrichment_persists_and_projects(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()

        # Build a real report via import_artifact to obtain trusted
        # observations (finding_id/finding_ref/classification) that satisfy
        # semantic validation (CAS resolution, run scope, diagnostic refs).
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-35195")]}])
        imported = assessment.import_artifact(
            self.rt, self.cid, self.rid, "trivy", raw, self.ctx)
        base_report = self.store.get_object(imported["report_ref"])
        findings = base_report["findings"]
        self.assertEqual(len(findings), 1)
        finding_id = findings[0]["finding_id"]

        records = ae.build_enrichment_records(
            self.store,
            [{"finding_id": finding_id,
              "classification": findings[0]["classification"]}],
            kev_ref=kev_meta, epss_ref=epss_meta)
        ae.validate_enrichment_records(records, {finding_id})

        # Rebuild the report through build_report with context enrichment.
        observations = [{
            "finding_id": findings[0]["finding_id"],
            "finding_ref": findings[0]["finding_ref"],
            "cause_id": findings[0]["cause_id"],
            "category": findings[0]["category"],
            "attempts": findings[0]["attempts"],
            "observations": findings[0]["observations"],
            "claims": findings[0]["claims"],
            "classification": findings[0]["classification"],
            "remediation": findings[0]["remediation"],
            "validation": findings[0]["validation"],
        }]
        ctx = dict(self.ctx)
        ctx["enrichment"] = records
        report_ref = assessment.build_report(
            self.rt, self.cid, self.rid, observations, ctx)

        stored = self.store.get_object(report_ref)
        self.assertIn("enrichment", stored)
        self.assertEqual(len(stored["enrichment"]), len(records))

        loaded = assessment.load_report(self.rt, self.cid, view="operator")
        self.assertIsNotNone(loaded)
        self.assertIn("enrichment", loaded)
        projected = loaded["enrichment"]
        self.assertEqual(len(projected), len(records))
        for item in projected:
            self.assertEqual(set(item.keys()), PROJECTION_KEYS)
            self.assertEqual(len(item), 12)

        f1kev = _by_key(projected, finding_id, "kev")
        self.assertEqual(f1kev["status"], "listed")
        f1epss = _by_key(projected, finding_id, "epss")
        self.assertEqual(f1epss["status"], "scored")
        self.assertAlmostEqual(f1epss["epss"], 0.05)

    # -- (B) CLI append path ---------------------------------------------

    def test_cli_import_appends_enrichment(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-35195")]}])

        result = self._run_cli_import(raw, self.ctx, kev_meta, epss_meta)
        self.assertIn("enrichment_ref", result)

        loaded = assessment.load_report(self.rt, self.cid, view="operator")
        self.assertIsNotNone(loaded)
        self.assertIn("enrichment", loaded)
        enrichment = loaded["enrichment"]
        self.assertEqual(len(enrichment), 2)
        finding_id = loaded["findings"][0]["finding_id"]
        f1kev = _by_key(enrichment, finding_id, "kev")
        self.assertEqual(f1kev["status"], "listed")
        f1epss = _by_key(enrichment, finding_id, "epss")
        self.assertEqual(f1epss["status"], "scored")
        self.assertAlmostEqual(f1epss["epss"], 0.05)

    # -- (C) rejection / withholding -------------------------------------

    def test_external_context_enrichment_rejected(self) -> None:
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-35195")]}])
        bad_ctx = dict(self.ctx)
        bad_ctx["enrichment"] = []
        with self.assertRaises(DataError) as cm:
            assessment.import_artifact(
                self.rt, self.cid, self.rid, "trivy", raw, bad_ctx)
        self.assertEqual(cm.exception.reason_code, "input_invalid")

    def test_selfdeclared_status_rejected(self) -> None:
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-35195")]}])
        imported = assessment.import_artifact(
            self.rt, self.cid, self.rid, "trivy", raw, self.ctx)
        base_report = self.store.get_object(imported["report_ref"])
        findings = base_report["findings"]
        finding_id = findings[0]["finding_id"]

        # Self-declared listed record with null meta fields.
        fake = {
            "finding_id": finding_id,
            "kind": "kev",
            "cve": "CVE-2024-35195",
            "status": "listed",
            "epss": None,
            "percentile": None,
            "source_url": None,
            "source_version": None,
            "fetched_at": None,
            "as_of": None,
            "expires_at": None,
            "raw_sha256": None,
            "content_digest": None,
            "reason": "",
        }
        observations = [{
            "finding_id": findings[0]["finding_id"],
            "finding_ref": findings[0]["finding_ref"],
            "cause_id": findings[0]["cause_id"],
            "category": findings[0]["category"],
            "attempts": findings[0]["attempts"],
            "observations": findings[0]["observations"],
            "claims": findings[0]["claims"],
            "classification": findings[0]["classification"],
            "remediation": findings[0]["remediation"],
            "validation": findings[0]["validation"],
        }]
        ctx = dict(self.ctx)
        ctx["enrichment"] = [fake]
        with self.assertRaises(DataError) as cm:
            assessment.build_report(
                self.rt, self.cid, self.rid, observations, ctx)
        self.assertEqual(cm.exception.reason_code, "input_invalid")

    def test_cve_mismatch_rejected(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-35195")]}])
        imported = assessment.import_artifact(
            self.rt, self.cid, self.rid, "trivy", raw, self.ctx)
        base_report = self.store.get_object(imported["report_ref"])
        findings = base_report["findings"]
        finding_id = findings[0]["finding_id"]

        records = ae.build_enrichment_records(
            self.store,
            [{"finding_id": finding_id,
              "classification": findings[0]["classification"]}],
            kev_ref=kev_meta, epss_ref=epss_meta)
        # Tamper: point the record at a CVE not in classification.cve.ids.
        tampered = [dict(r) for r in records]
        tampered[0]["cve"] = "CVE-2024-9999-0001"

        observations = [{
            "finding_id": findings[0]["finding_id"],
            "finding_ref": findings[0]["finding_ref"],
            "cause_id": findings[0]["cause_id"],
            "category": findings[0]["category"],
            "attempts": findings[0]["attempts"],
            "observations": findings[0]["observations"],
            "claims": findings[0]["claims"],
            "classification": findings[0]["classification"],
            "remediation": findings[0]["remediation"],
            "validation": findings[0]["validation"],
        }]
        ctx = dict(self.ctx)
        ctx["enrichment"] = tampered
        with self.assertRaises((AssessmentContractError, DataError)):
            assessment.build_report(
                self.rt, self.cid, self.rid, observations, ctx)

    def test_blue_withheld(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-35195")]}])
        self._run_cli_import(raw, self.ctx, kev_meta, epss_meta)

        blue = assessment.load_report(self.rt, self.cid, view="blue")
        self.assertEqual(blue, {"status": "withheld",
                                "reason": "評価情報はBlueに非公開"})
        self.assertNotIn("enrichment", blue)
        self.assertNotIn("findings", blue)

    def test_ttl_expired_record(self) -> None:
        kev_meta = self._import_kev(ttl_seconds=1)
        epss_meta = self._import_epss(ttl_seconds=1)
        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-35195")]}])

        result = self._run_cli_import(raw, self.ctx, kev_meta, epss_meta)
        self.assertIn("enrichment_ref", result)

        loaded = assessment.load_report(self.rt, self.cid, view="operator")
        self.assertIsNotNone(loaded)
        enrichment = loaded["enrichment"]
        finding_id = loaded["findings"][0]["finding_id"]
        f1kev = _by_key(enrichment, finding_id, "kev")
        self.assertEqual(f1kev["status"], "expired")
        self.assertIsNone(f1kev["epss"])
        self.assertIsNone(f1kev["percentile"])
        self.assertTrue(f1kev["reason"])
        f1epss = _by_key(enrichment, finding_id, "epss")
        self.assertEqual(f1epss["status"], "expired")
        self.assertIsNone(f1epss["epss"])
        self.assertIsNone(f1epss["percentile"])
        self.assertTrue(f1epss["reason"])

    def test_cli_forged_expires_at_not_listed(self) -> None:
        kev_meta = self._import_kev(ttl_seconds=1)
        epss_meta = self._import_epss(ttl_seconds=1)
        forged_kev = dict(kev_meta)
        forged_kev["expires_at"] = (
            datetime.now(timezone.utc).timestamp() + 10 * 365 * 86400)
        forged_epss = dict(epss_meta)
        forged_epss["expires_at"] = (
            datetime.now(timezone.utc).timestamp() + 10 * 365 * 86400)

        raw = self._trivy([{"Target": "t1", "Vulnerabilities": [
            self._vuln("CVE-2024-35195")]}])
        result = self._run_cli_import(raw, self.ctx, forged_kev, forged_epss)
        self.assertIn("enrichment_ref", result)

        loaded = assessment.load_report(self.rt, self.cid, view="operator")
        self.assertIsNotNone(loaded)
        enrichment = loaded["enrichment"]
        finding_id = loaded["findings"][0]["finding_id"]
        f1kev = _by_key(enrichment, finding_id, "kev")
        self.assertIn(f1kev["status"], ("expired", "corrupt"))
        self.assertNotEqual(f1kev["status"], "listed")
        self.assertEqual(f1kev["expires_at"], kev_meta["expires_at"])
        f1epss = _by_key(enrichment, finding_id, "epss")
        self.assertIn(f1epss["status"], ("expired", "corrupt"))
        self.assertNotEqual(f1epss["status"], "scored")
        self.assertEqual(f1epss["expires_at"], epss_meta["expires_at"])

    def test_no_cve_not_applicable(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        # Misconfiguration finding: no VulnerabilityID -> no CVE.
        raw = self._trivy([{
            "Target": "t1",
            "Misconfigurations": [
                {"ID": "AVD-TEST-0001", "Title": "misconfig",
                 "Severity": "LOW"}
            ],
        }])

        result = self._run_cli_import(raw, self.ctx, kev_meta, epss_meta)
        self.assertIn("enrichment_ref", result)

        loaded = assessment.load_report(self.rt, self.cid, view="operator")
        self.assertIsNotNone(loaded)
        enrichment = loaded["enrichment"]
        self.assertTrue(enrichment)
        for item in enrichment:
            self.assertEqual(item["status"], "not_applicable")
            self.assertIsNone(item["cve"])
            self.assertIsNone(item["epss"])
            self.assertIsNone(item["percentile"])
            self.assertTrue(item["reason"])


if __name__ == "__main__":
    unittest.main()
