"""CSV の課題件数を Blue に公開せず、診断検知の件数と集計状態を維持する。"""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from ai_yori_murasaki.monitor_index import _index_path, build_index
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import handle_request
from ai_yori_murasaki.storage import Store
from tests.test_tracker_drift_e2e import prepare_csv_fixture, run_cli


class BlueCampaignCountsTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        ids = prepare_csv_fixture(root)
        self.cid, self.rid = ids["campaign_id"], ids["run_id"]
        self.store = Store(root / "workspace", root / "trusted")
        self.runtime = Runtime(self.store)
        run_cli(root, "assess", "import", "--format", "notion", "--input", root / "input" / "notion.csv",
                "--context", root / "input" / "context.json", "--campaign", self.cid, "--run", self.rid)

    def row(self, suffix):
        status, _, body = handle_request(self.runtime, "GET", "/api/campaigns" + suffix)
        self.assertEqual(status, 200)
        return next(row for row in json.loads(body)["campaigns"] if row["campaign_id"] == self.cid)

    def assert_counts(self, count, operator_count, coverage):
        for query in ("?view=blue", "?page=1&view=blue"):
            row = self.row(query)
            self.assertEqual((row["finding_count"], row["finding_count_status"]), (count, coverage))
            for private in ("blue_counts", "severity_counts", "finding_excerpt", "unverified_run_ids"):
                self.assertNotIn(private, row)
        for query in ("", "?page=1"):
            row = self.row(query)
            self.assertEqual((row["finding_count"], row["finding_count_status"]), (operator_count, coverage))
            self.assertNotIn("blue_counts", row)
            self.assertEqual(row["severity_counts"]["unknown"], 8)
            self.assertEqual(sum(row["severity_counts"].values()), operator_count)

    def test_zero_and_partial_counts_before_and_after_index_and_migration(self):
        self.assert_counts(0, 8, "complete")
        build_index(self.store)
        self.assert_counts(0, 8, "complete")
        finding = {"schema_version": "1.0.0", "campaign_id": self.cid, "run_id": self.rid,
                   "finding_id": "ordinary", "source": "wazuh", "severity": "high", "title": "合成した診断",
                   "references": [], "location": {"path": "ordinary", "line": None, "column": None}}
        self.runtime.register_record("finding", finding, self.rid)
        self.store.update_catalog("missing-run", {"campaign_id": self.cid})
        build_index(self.store)
        self.assert_counts(1, 9, "partial")
        with closing(sqlite3.connect(_index_path(self.store))) as db, db:
            for cid, data in db.execute("SELECT id, row_json FROM cases").fetchall():
                row = json.loads(data)
                del row["blue_counts"]
                db.execute("UPDATE cases SET row_json=? WHERE id=?", (json.dumps(row), cid))
            db.execute("PRAGMA user_version = 3")
        with patch.object(self.store, "read_verified_events", side_effect=AssertionError("台帳の再読")):
            self.assertEqual(self.row("?page=1&view=blue")["finding_count"], 1)
            self.assertEqual(build_index(self.store)["rechecked_runs"], 0)
        self.assert_counts(1, 9, "partial")

    def test_missing_private_batch_does_not_change_blue_coverage(self):
        events = self.store.read_verified_events(self.rid)
        ref = next(e["extensions"]["notion_csv_ref"] for e in events if "notion_csv_ref" in e["extensions"])
        self.store._object_path(ref).unlink()
        for indexed in (False, True):
            if indexed:
                build_index(self.store)
            for query in ("?view=blue", "?page=1&view=blue"):
                with patch("ai_yori_murasaki.notion_import.batch_findings", side_effect=AssertionError("CSV 読取")):
                    row = self.row(query)
                self.assertEqual((row["finding_count"], row["finding_count_status"]), (0, "complete"))
                self.assertEqual(row["unverified_run_count"], 0)
            self.assertNotEqual(self.row("?page=1")["finding_count_status"], "complete")

    def test_damaged_blue_counts_do_not_fall_back_to_the_operator_total(self):
        build_index(self.store)
        with closing(sqlite3.connect(_index_path(self.store))) as db:
            original = json.loads(db.execute("SELECT row_json FROM cases WHERE id=?", (self.cid,)).fetchone()[0])
            for counts in (None, {**original["blue_counts"], "finding_count": "8"},
                           {**original["blue_counts"], "finding_count_status": "unknown"}):
                with self.subTest(counts=counts):
                    row = {**original, "blue_counts": counts}
                    if counts is None:
                        del row["blue_counts"]
                    db.execute("UPDATE cases SET row_json=? WHERE id=?", (json.dumps(row), self.cid))
                    db.commit()
                    status, _, body = handle_request(self.runtime, "GET", "/api/campaigns?page=1&view=blue")
                    self.assertEqual(status, 409)
                    self.assertEqual(json.loads(body), {"error": "monitor_index_required"})
