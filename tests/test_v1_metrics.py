"""Unit tests for KPI, enrichment, resources (SPEC A-4/A-5/A-6)."""
import hashlib
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from ai_yori_murasaki import kpi, enrichment, resources  # noqa: E402
from ai_yori_murasaki.storage import Store  # noqa: E402


class _TempStoreMixin:
    def _make_store(self):
        base = Path(tempfile.mkdtemp(prefix="aym-test-"))
        ws = base / "workspace"
        tr = base / "trusted"
        ws.mkdir()
        tr.mkdir()
        self._tmp_base = base
        return Store(ws, tr)


class TestKPI(unittest.TestCase):
    def _rec(self, **kw):
        base = {"run_id": "r1", "completion": "completed"}
        base.update(kw)
        return base

    def test_zero_denominator_null(self):
        out = kpi.compute([], {"run_ids": [], "event_counts": {},
                               "attack_ids": [], "technique_ids": []})
        self.assertIsNone(out["blue_recall"]["value"])
        self.assertIsNone(out["blue_precision"]["value"])
        self.assertIsNone(out["evidence_completeness"]["value"])

    def test_pending_excluded(self):
        recs = [self._rec(run_id="a", completion=None),
                self._rec(run_id="b", completion=None)]
        out = kpi.compute(recs, {"run_ids": ["a", "b"], "event_counts": {},
                                "attack_ids": [], "technique_ids": []})
        self.assertIsNone(out["blue_recall"]["value"])

    def test_legacy_completion_values_rejected(self):
        for bad in ("pending", "running", "failed"):
            with self.assertRaises(kpi.RecordError):
                kpi.compute([self._rec(run_id="a", completion=bad)],
                            {"run_ids": ["a"], "event_counts": {},
                             "attack_ids": [], "technique_ids": []})

    def test_candidate_loss_and_pair_fail(self):
        recs = [self._rec(run_id="a", completion="incomplete", cause="candidate"),
                self._rec(run_id="b", completion="incomplete", cause="external",
                          external_pair_invalid=True)]
        out = kpi.compute(recs, {"run_ids": ["a", "b"], "event_counts": {},
                                "attack_ids": [], "technique_ids": []})
        self.assertEqual(out["_aux"]["candidate_loss"], 1)
        self.assertEqual(out["_aux"]["pair_fail"], 1)

    def test_failure_attribution_overrides_cause(self):
        recs = [self._rec(run_id="a", completion="incomplete",
                          cause="timeout", failure_attribution="candidate")]
        out = kpi.compute(recs, {"run_ids": ["a"], "event_counts": {},
                                "attack_ids": [], "technique_ids": []})
        self.assertEqual(out["_aux"]["candidate_loss"], 1)

    def test_cancelled_and_adapter_unavailable_excluded(self):
        recs = [self._rec(run_id="a", completion="cancelled"),
                self._rec(run_id="b", completion="adapter_unavailable")]
        out = kpi.compute(recs, {"run_ids": ["a", "b"], "event_counts": {},
                                "attack_ids": [], "technique_ids": []})
        self.assertIsNone(out["blue_recall"]["value"])
        self.assertEqual(out["oracle_replay_match_rate"]["invalid"], 1)

    def test_duplicate_tp_fp_dedup(self):
        recs = [self._rec(run_id="a", findings=[
                    {"finding_id": "f1", "label": "tp"},
                    {"finding_id": "f1", "label": "tp"},
                    {"finding_id": "f2", "label": "fp"}])]
        out = kpi.compute(recs, {"run_ids": ["a"], "event_counts": {},
                                "attack_ids": [], "technique_ids": []})
        self.assertEqual(out["blue_precision"]["numerator"], 1)
        self.assertEqual(out["blue_precision"]["denominator"], 2)

    def test_conflicting_label_rejected(self):
        recs = [self._rec(run_id="a", findings=[
                    {"finding_id": "f1", "label": "tp"},
                    {"finding_id": "f1", "label": "fp"}])]
        with self.assertRaises(kpi.RecordError):
            kpi.compute(recs, {"run_ids": ["a"], "event_counts": {},
                               "attack_ids": [], "technique_ids": []})

    def test_missing_events_from_manifest(self):
        recs = [self._rec(run_id="a", events_verified=3)]
        out = kpi.compute(recs, {"run_ids": ["a", "b"],
                                 "event_counts": {"a": 5, "b": 4},
                                 "attack_ids": [], "technique_ids": []})
        self.assertEqual(out["evidence_completeness"]["numerator"], 3)
        self.assertEqual(out["evidence_completeness"]["denominator"], 9)
        self.assertEqual(out["evidence_missing_rate"]["numerator"], 6)
        self.assertIn("b", out["_aux"]["missing_expected_runs"])

    def test_time_order_diagnostic(self):
        recs = [self._rec(run_id="a", detected=True, red_at=100.0, detected_at=90.0),
                self._rec(run_id="b", detected=True, red_at=100.0, detected_at=110.0)]
        out = kpi.compute(recs, {"run_ids": ["a", "b"], "event_counts": {},
                                "attack_ids": [], "technique_ids": []})
        self.assertEqual(out["red_to_detection_time"]["invalid"], 1)
        self.assertEqual(out["red_to_detection_time"]["value"], 10.0)

    def test_nan_detected_at_rejected(self):
        recs = [self._rec(run_id="a", detected=True, red_at=1.0,
                          detected_at=float("nan"))]
        with self.assertRaises(kpi.RecordError):
            kpi.compute(recs, {"run_ids": ["a"], "event_counts": {},
                               "attack_ids": [], "technique_ids": []})

    def test_inf_cost_rejected(self):
        recs = [self._rec(run_id="a", cost=float("inf"))]
        with self.assertRaises(kpi.RecordError):
            kpi.compute(recs, {"run_ids": ["a"], "event_counts": {},
                               "attack_ids": [], "technique_ids": []})

    def test_undetected_in_recall_denominator(self):
        recs = [self._rec(run_id="a", attack_id="A1", detected=True,
                          red_at=1.0, detected_at=2.0),
                self._rec(run_id="b", attack_id="A2", detected=False)]
        out = kpi.compute(recs, {"run_ids": ["a", "b"], "event_counts": {},
                                "attack_ids": ["A1", "A2"], "technique_ids": []})
        self.assertEqual(out["blue_recall"]["numerator"], 1)
        self.assertEqual(out["blue_recall"]["denominator"], 2)
        self.assertEqual(out["blue_recall"]["invalid"], 1)

    def test_over_remediation_denominator_uses_applied(self):
        recs = [self._rec(run_id="a", remediation_attempted=True,
                          remediation_applied=True, remediation_success=None,
                          benign_passed=False),
                self._rec(run_id="b", remediation_attempted=True,
                          remediation_applied=True, remediation_success=True,
                          benign_passed=True),
                self._rec(run_id="c", remediation_attempted=True,
                          remediation_applied=False, remediation_success=True,
                          benign_passed=False)]
        out = kpi.compute(recs, {"run_ids": ["a", "b", "c"], "event_counts": {},
                                "attack_ids": [], "technique_ids": []})
        self.assertEqual(out["over_remediation_rate"]["denominator"], 2)
        self.assertEqual(out["over_remediation_rate"]["numerator"], 1)

    def test_audit_not_fixed_zero(self):
        recs = [self._rec(run_id="a", production_applies=2,
                          unauthorized_privileged_actions=1)]
        out = kpi.compute(recs, {"run_ids": ["a"], "event_counts": {},
                                "attack_ids": [], "technique_ids": []})
        self.assertEqual(out["production_auto_apply_count"]["value"], 2)
        self.assertEqual(out["unauthorized_privileged_actions"]["value"], 1)

    def test_undetected_techniques(self):
        recs = [self._rec(run_id="a", technique_id="T1001", detected=True,
                          red_at=1.0, detected_at=2.0)]
        out = kpi.compute(recs, {"run_ids": ["a"], "event_counts": {},
                                "attack_ids": [],
                                "technique_ids": ["T1001", "T1002"]})
        self.assertEqual(out["undetected_attack_techniques"]["undetected_ids"],
                         ["T1002"])


class TestEnrichment(_TempStoreMixin, unittest.TestCase):
    def setUp(self):
        self.store = self._make_store()
        self.now = datetime.now(timezone.utc)
        self.fetched = (self.now - timedelta(hours=1)).isoformat()
        self.as_of = (self.now - timedelta(days=1)).strftime("%Y-%m-%d")

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp_base, ignore_errors=True)

    def test_attack_import_and_query(self):
        bundle = {
            "type": "bundle", "id": "bundle--x", "spec_version": "2.1",
            "objects": [{
                "type": "attack-pattern", "id": "attack-pattern--1",
                "name": "Test",
                "external_references": [
                    {"source_name": "mitre-attack", "external_id": "T1059.001"},
                    {"source_name": "other", "external_id": "X"},
                ],
            }],
        }
        raw = json.dumps(bundle).encode()
        meta = enrichment.import_snapshot(
            self.store, "attack", raw, "https://example/attack.json",
            self.fetched, "v15", self.as_of)
        self.assertEqual(meta["technique_count"], 1)
        self.assertEqual(meta["raw_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertNotEqual(meta["content_digest"], meta["raw_sha256"])
        q = enrichment.query_snapshot(self.store, meta, technique_id="T1059.001")
        self.assertEqual(q["status"], "ok")
        self.assertEqual(q["technique"]["technique_id"], "T1059.001")

    def test_kev_import(self):
        doc = {
            "catalogVersion": "2026.11.01",
            "dateReleased": "2026-11-01T00:00:00.000Z",
            "count": 1,
            "vulnerabilities": [{
                "cveID": "CVE-2021-44228", "vendorProject": "Apache",
                "product": "Log4j", "vulnerabilityName": "Log4Shell",
                "dateAdded": "2021-12-10", "shortDescription": "x",
                "requiredAction": "patch", "dueDate": "2021-12-24",
            }],
        }
        raw = json.dumps(doc).encode()
        meta = enrichment.import_snapshot(
            self.store, "kev", raw, "https://example/kev.json",
            self.fetched, "2026.11.01", self.as_of)
        q = enrichment.query_snapshot(self.store, meta, cve="CVE-2021-44228")
        self.assertEqual(q["status"], "ok")
        self.assertEqual(q["vulnerability"]["cveID"], "CVE-2021-44228")

    def test_epss_csv_import_stores_json_wrapper(self):
        csv_text = (
            "#model_version:v2025.03.14,score_date:2026-11-05T00:00:00Z\n"
            "cve,epss,percentile\n"
            "CVE-2021-44228,0.97,0.99\n"
            "CVE-2019-16759,0.5,0.6\n"
        )
        raw = csv_text.encode()
        meta = enrichment.import_snapshot(
            self.store, "epss", raw, "https://example/epss.csv",
            self.fetched, "v2025.03.14", self.as_of)
        # CAS object must be a JSON raw_text wrapper, not raw CSV.
        stored = self.store.get_object(meta["content_digest"])
        self.assertEqual(stored["type"], "raw_text")
        self.assertEqual(stored["encoding"], "utf-8")
        self.assertEqual(stored["raw_sha256"], hashlib.sha256(raw).hexdigest())
        self.assertEqual(stored["text"], csv_text)
        q = enrichment.query_snapshot(self.store, meta, cve="CVE-2021-44228")
        self.assertEqual(q["status"], "ok")
        self.assertAlmostEqual(q["score"], 0.97)

    def test_epss_json_import(self):
        doc = {"status": "OK", "data": [
            {"cve": "CVE-2021-44228", "epss": "0.97", "percentile": "0.99"}]}
        raw = json.dumps(doc).encode()
        meta = enrichment.import_snapshot(
            self.store, "epss", raw, "https://example/epss.json",
            self.fetched, "v1", self.as_of)
        q = enrichment.query_snapshot(self.store, meta, cve="CVE-2021-44228")
        self.assertEqual(q["status"], "ok")

    def test_expected_sha256_checked_before_write(self):
        raw = b'{"type":"bundle","objects":[]}'
        with self.assertRaises(enrichment.EnrichmentError):
            enrichment.import_snapshot(
                self.store, "attack", raw, "u", self.fetched, "v", self.as_of,
                expected_sha256="0" * 64)
        # No object should have been written for the mismatched raw.
        objects_dir = Path(self.store.workspace) / "objects"
        if objects_dir.exists():
            self.assertEqual(list(objects_dir.iterdir()), [])

    def test_expected_sha256_matches_raw(self):
        raw = b'{"type":"bundle","objects":[]}'
        expected = hashlib.sha256(raw).hexdigest()
        meta = enrichment.import_snapshot(
            self.store, "attack", raw, "https://example.test/source", self.fetched, "v", self.as_of,
            expected_sha256=expected)
        self.assertEqual(meta["raw_sha256"], expected)

    def test_ttl_expiry(self):
        raw = b'{"type":"bundle","objects":[]}'
        meta = enrichment.import_snapshot(
            self.store, "attack", raw, "https://example.test/source", self.fetched, "v", self.as_of,
            ttl_seconds=1)
        later = self.now + timedelta(seconds=10)
        q = enrichment.query_snapshot(self.store, meta, now=later)
        self.assertEqual(q["status"], "expired")
        self.assertIsNone(q["score"])

    def test_future_date_rejected(self):
        future = (self.now + timedelta(days=1)).isoformat()
        with self.assertRaises(enrichment.EnrichmentError):
            enrichment.import_snapshot(
                self.store, "attack", b'{"type":"bundle","objects":[]}',
                "u", future, "v", self.as_of)

    def test_naive_date_rejected(self):
        naive = (self.now - timedelta(hours=1)).replace(tzinfo=None).isoformat()
        with self.assertRaises(enrichment.EnrichmentError):
            enrichment.import_snapshot(
                self.store, "attack", b'{"type":"bundle","objects":[]}',
                "u", naive, "v", self.as_of)

    def test_nan_score_rejected(self):
        csv_text = "cve,epss,percentile\nCVE-2021-44228,nan,0.5\n"
        with self.assertRaises(enrichment.EnrichmentError):
            enrichment.import_snapshot(
                self.store, "epss", csv_text.encode(), "u",
                self.fetched, "v", self.as_of)

    def test_oversize_rejected(self):
        big = b"x" * (enrichment.MAX_INPUT_BYTES + 1)
        with self.assertRaises(enrichment.EnrichmentError):
            enrichment.import_snapshot(
                self.store, "attack", big, "u", self.fetched, "v", self.as_of)

    def test_raw_secret_sanitizer_mutation_detected(self):
        # If the Store sanitizer mutates the wrapper text, readback must fail.
        secret = "SUPERSECRET-TOKEN-123"
        base = Path(tempfile.mkdtemp(prefix="aym-secret-"))
        try:
            ws = base / "workspace"
            tr = base / "trusted"
            ws.mkdir()
            tr.mkdir()
            store = Store(ws, tr, secrets=(secret,))
            csv_text = (
                "cve,epss,percentile\n"
                f"CVE-2021-44228,0.97,0.99\n"
            )
            raw = csv_text.encode()
            # Sanitizer will not touch this raw (no secret), so import succeeds.
            meta = enrichment.import_snapshot(
                store, "epss", raw, "https://example.test/source", self.fetched, "v", self.as_of)
            self.assertEqual(meta["raw_sha256"], hashlib.sha256(raw).hexdigest())
            # Now craft a wrapper whose text contains the secret; Store will
            # redact it, so raw_sha256 will no longer match on readback.
            tampered = {
                "type": "raw_text", "encoding": "utf-8",
                "text": f"cve,epss,percentile\nCVE-2021-44228,0.97,0.99\n{secret}\n",
                "raw_sha256": hashlib.sha256(
                    f"cve,epss,percentile\nCVE-2021-44228,0.97,0.99\n{secret}\n".encode()
                ).hexdigest(),
            }
            digest = store.put_object(tampered)
            stored = store.get_object(digest)
            self.assertNotIn(secret, stored["text"])
            self.assertNotEqual(
                hashlib.sha256(stored["text"].encode()).hexdigest(),
                stored["raw_sha256"])
        finally:
            import shutil
            shutil.rmtree(base, ignore_errors=True)


class TestResources(unittest.TestCase):
    def test_measure_returns_fields(self):
        r = resources.measure_resources()
        self.assertIn("rss_bytes", r)
        self.assertIn("peak_rss_bytes", r)
        self.assertIn("platform", r)
        self.assertIn("source", r)
        if r["rss_bytes"] is not None:
            self.assertGreater(r["rss_bytes"], 0)


if __name__ == "__main__":
    unittest.main()