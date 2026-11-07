"""KEV/EPSS enrichment record builder tests (assessment_enrichment)."""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import jsonschema

from ai_yori_murasaki import assessment_enrichment as ae
from ai_yori_murasaki.enrichment import (
    EnrichmentError as EnrichmentImportError,
    import_snapshot,
    query_snapshot,
)
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

FETCHED_AT = AS_OF = (datetime.now(timezone.utc)
                      - timedelta(hours=1)).isoformat()

FINDINGS = [
    {"finding_id": "f1",
     "classification": {"cve": {"status": "candidate",
                                "ids": ["CVE-2024-35195"]}}},
    {"finding_id": "f2",
     "classification": {"cve": {"status": "unknown", "ids": []}}},
    {"finding_id": "f3",
     "classification": {"cve": {"status": "candidate",
                                "ids": ["CVE-2024-9999-0001"]}}},
]

FINDING_IDS = {"f1", "f2", "f3"}

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

SCHEMA_PATH = (Path(__file__).resolve().parent.parent
               / "ai_yori_murasaki" / "schemas" / "v1"
               / "assessment.schema.json")


def _minimal_report() -> dict:
    return {
        "schema_version": "1.0.0",
        "assessment_version": "1.0.0",
        "report_id": "asc-" + "0" * 32,
        "campaign_id": "c",
        "run_id": "r",
        "target": "t",
        "target_version": "v",
        "previous_report_ref": None,
        "created_at": "2026-01-01T00:00:00Z",
        "actor": "a",
        "coverage": {"categories": [],
                     "created_at": "2026-01-01T00:00:00Z"},
        "findings": [],
        "cvss": [],
        "business_impact": [],
        "manifest_ref": None,
        "extensions": {},
    }


def _by_key(records, finding_id, kind):
    for record in records:
        if record["finding_id"] == finding_id and record["kind"] == kind:
            return record
    raise AssertionError(f"record not found: {finding_id}/{kind}")


class AssessmentEnrichmentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "ws"
        self.tr = root / "tr"
        self.ws.mkdir()
        self.tr.mkdir()
        self.store = Store(self.ws, self.tr)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _import_kev(self, ttl_seconds=None) -> dict:
        return import_snapshot(
            self.store, "kev", KEV_JSON,
            source_url="https://example.test/kev.json",
            fetched_at=FETCHED_AT,
            source_version="v1",
            as_of=AS_OF,
            ttl_seconds=ttl_seconds,
        )

    def _import_epss(self, ttl_seconds=None) -> dict:
        return import_snapshot(
            self.store, "epss", EPSS_CSV,
            source_url="https://example.test/epss.csv",
            fetched_at=FETCHED_AT,
            source_version="v1",
            as_of=AS_OF,
            ttl_seconds=ttl_seconds,
        )

    def test_listed_and_scored(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta)

        f1kev = _by_key(records, "f1", "kev")
        self.assertEqual(f1kev["status"], "listed")
        self.assertEqual(f1kev["reason"], "")
        self.assertIsNone(f1kev["epss"])
        self.assertIsNone(f1kev["percentile"])
        for field in ("source_url", "source_version", "fetched_at", "as_of",
                      "expires_at", "raw_sha256", "content_digest"):
            self.assertIsNotNone(f1kev[field], field)

        f1epss = _by_key(records, "f1", "epss")
        self.assertEqual(f1epss["status"], "scored")
        self.assertAlmostEqual(f1epss["epss"], 0.05)
        self.assertAlmostEqual(f1epss["percentile"], 0.94)

        ae.validate_enrichment_records(records, FINDING_IDS)
        ae.verify_against_meta(records, [kev_meta, epss_meta])

    def test_not_listed(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta)

        f3kev = _by_key(records, "f3", "kev")
        self.assertEqual(f3kev["status"], "not_listed")
        self.assertIsNone(f3kev["epss"])
        self.assertIsNone(f3kev["percentile"])
        self.assertTrue(f3kev["reason"])

    def test_expired(self) -> None:
        kev_meta = self._import_kev(ttl_seconds=1)
        epss_meta = self._import_epss(ttl_seconds=1)
        now = datetime(2027, 1, 1, tzinfo=timezone.utc)
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta,
            now=now)

        f1kev = _by_key(records, "f1", "kev")
        self.assertEqual(f1kev["status"], "expired")
        self.assertIsNone(f1kev["epss"])
        self.assertIsNone(f1kev["percentile"])
        self.assertTrue(f1kev["reason"])

        f1epss = _by_key(records, "f1", "epss")
        self.assertEqual(f1epss["status"], "expired")
        self.assertIsNone(f1epss["epss"])
        self.assertIsNone(f1epss["percentile"])
        self.assertTrue(f1epss["reason"])

    def test_missing(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()

        bad_kev = dict(kev_meta)
        bad_kev["raw_sha256"] = "0" * 64
        # content_digest stays the real one.
        self.assertEqual(bad_kev["content_digest"], kev_meta["content_digest"])

        result = query_snapshot(self.store, bad_kev, cve="CVE-2024-35195")
        self.assertEqual(result["status"], "missing")

        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=bad_kev, epss_ref=epss_meta)
        f1kev = _by_key(records, "f1", "kev")
        self.assertEqual(f1kev["status"], "missing")

    def test_no_cve_not_applicable(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta)

        for kind in ("kev", "epss"):
            rec = _by_key(records, "f2", kind)
            self.assertEqual(rec["status"], "not_applicable")
            self.assertIsNone(rec["cve"])
            self.assertIsNone(rec["epss"])
            self.assertIsNone(rec["percentile"])
            self.assertTrue(rec["reason"])

    def test_not_tested(self) -> None:
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=None, epss_ref=None)

        for finding_id in ("f1", "f3"):
            for kind in ("kev", "epss"):
                rec = _by_key(records, finding_id, kind)
                self.assertEqual(rec["status"], "not_tested")
                self.assertTrue(rec["reason"])
                for field in ("source_url", "source_version", "fetched_at",
                              "as_of", "expires_at", "raw_sha256",
                              "content_digest"):
                    self.assertIsNone(rec[field], field)

    def test_duplicate_rejected(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta)
        duplicated = list(records) + [dict(records[0])]
        with self.assertRaises(ae.EnrichmentError):
            ae.validate_enrichment_records(duplicated, FINDING_IDS)

    def test_cve_mismatch_rejected(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta)
        bad = [dict(records[0])]
        bad[0]["finding_id"] = "f-unknown"
        with self.assertRaises(ae.EnrichmentError):
            ae.validate_enrichment_records(bad, FINDING_IDS)

    def test_projection_allowlisted(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta)
        projected = ae.project_enrichment(records)
        self.assertEqual(len(projected), len(records))
        for item in projected:
            self.assertEqual(set(item.keys()), PROJECTION_KEYS)
            self.assertEqual(len(item), 12)

    def test_schema_backward_compat(self) -> None:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        self.assertNotIn("enrichment", schema["required"])
        jsonschema.validate(_minimal_report(), schema)

    def test_schema_enrichment_record_valid(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta)
        f1_records = [r for r in records if r["finding_id"] == "f1"]
        self.assertEqual(len(f1_records), 2)

        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
        report = _minimal_report()
        report["enrichment"] = f1_records
        jsonschema.validate(report, schema)

    def test_verify_against_meta_mismatch(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()
        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=kev_meta, epss_ref=epss_meta)
        tampered = [dict(r) for r in records]
        tampered[0]["content_digest"] = "0" * 64
        with self.assertRaises(ae.EnrichmentError):
            ae.verify_against_meta(tampered, [kev_meta, epss_meta])

    def test_forged_ref_expires_at_rejected(self) -> None:
        kev_meta = self._import_kev(ttl_seconds=1)
        epss_meta = self._import_epss(ttl_seconds=1)
        now = datetime(2027, 1, 1, tzinfo=timezone.utc)

        forged_kev = dict(kev_meta)
        forged_kev["expires_at"] = now.timestamp() + 10 * 365 * 86400
        forged_epss = dict(epss_meta)
        forged_epss["expires_at"] = now.timestamp() + 10 * 365 * 86400

        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=forged_kev, epss_ref=forged_epss,
            now=now)

        f1kev = _by_key(records, "f1", "kev")
        self.assertEqual(f1kev["status"], "corrupt")
        self.assertEqual(f1kev["expires_at"], kev_meta["expires_at"])
        self.assertIsNone(f1kev["epss"])
        self.assertIsNone(f1kev["percentile"])
        self.assertTrue(f1kev["reason"])

        f1epss = _by_key(records, "f1", "epss")
        self.assertEqual(f1epss["status"], "corrupt")
        self.assertEqual(f1epss["expires_at"], epss_meta["expires_at"])
        self.assertIsNone(f1epss["epss"])
        self.assertIsNone(f1epss["percentile"])
        self.assertTrue(f1epss["reason"])

        # Without a ref expires_at, the stored meta is the sole source of
        # truth and TTL expiry fires correctly.
        no_exp_kev = dict(kev_meta)
        no_exp_kev.pop("expires_at")
        no_exp_epss = dict(epss_meta)
        no_exp_epss.pop("expires_at")
        records2 = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=no_exp_kev, epss_ref=no_exp_epss,
            now=now)
        self.assertEqual(_by_key(records2, "f1", "kev")["status"], "expired")
        self.assertEqual(_by_key(records2, "f1", "epss")["status"], "expired")

    def test_ref_expires_at_mismatch_corrupt(self) -> None:
        kev_meta = self._import_kev()
        epss_meta = self._import_epss()

        forged_kev = dict(kev_meta)
        forged_kev["expires_at"] = kev_meta["expires_at"] + 10 * 365 * 86400

        result = query_snapshot(self.store, forged_kev, cve="CVE-2024-35195")
        self.assertEqual(result["status"], "corrupt")
        self.assertEqual(result["reason"], "meta_expires_at_mismatch")
        self.assertIsNone(result["score"])
        self.assertEqual(result["expires_at"], kev_meta["expires_at"])

        records = ae.build_enrichment_records(
            self.store, FINDINGS, kev_ref=forged_kev, epss_ref=epss_meta)
        f1kev = _by_key(records, "f1", "kev")
        self.assertEqual(f1kev["status"], "corrupt")
        self.assertEqual(f1kev["expires_at"], kev_meta["expires_at"])

    def test_import_rejects_dangerous_source_url(self) -> None:
        for bad_url in ("javascript:alert(1)", "data:text/html,x"):
            with self.assertRaises(EnrichmentImportError):
                import_snapshot(
                    self.store, "kev", KEV_JSON,
                    source_url=bad_url,
                    fetched_at=FETCHED_AT,
                    source_version="v1",
                    as_of=AS_OF,
                )
        ok = import_snapshot(
            self.store, "kev", KEV_JSON,
            source_url="https://example.test/kev.json",
            fetched_at=FETCHED_AT,
            source_version="v1",
            as_of=AS_OF,
        )
        self.assertEqual(ok["source_url"], "https://example.test/kev.json")


if __name__ == "__main__":
    unittest.main()
