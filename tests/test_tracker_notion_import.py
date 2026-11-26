"""合成した CSV で課題スナップショットの永続化と再試行を確認する。"""
import csv
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_yori_murasaki.decision_view import read_source_records
from ai_yori_murasaki.drift_report import read_drift
from ai_yori_murasaki.jsonio import DataError, digest
from ai_yori_murasaki.monitor_summary import summarize_run
from ai_yori_murasaki.notion_import import campaign_issues, import_notion, load_csv_row
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


class OfflineNotionCsvTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.store = Store(root / "workspace", root / "trusted")
        self.runtime = Runtime(self.store)
        scope = {"targets": [{"kind": "fixture", "identifier": "synth"}], "max_targets": 1}
        auth = {"operator_id": "reader", "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope)}
        self.cid = self.runtime.create_campaign("INT-CSV", scope, auth, "fixture")["campaign_id"]
        self.rid = self.runtime.start_run(self.cid, "verify", 1, "fixture-run")["run_id"]
        self.path, self.context = root / "issues.csv", root / "ctx.json"
        self.context.write_text('{"target":"synth","target_version":"v1"}', encoding="utf-8")

    def write(self, rows, header=("名前", "棚卸キー", "ステータス", "概要")):
        with self.path.open("w", newline="", encoding="utf-8-sig") as stream:
            out = csv.writer(stream)
            out.writerow(header)
            out.writerows(rows)

    def ingest(self):
        return import_notion(self.runtime, self.cid, self.rid, self.path, self.context)

    def test_concurrent_retries_original_rows_and_provenance(self):
        summary = '前半,後半\r\n"追記"'
        self.write([("資料A", "t-1", "未対応", summary), ("資料B", "t-2", "修正済", "")])
        with ThreadPoolExecutor(2) as executor:
            results = list(executor.map(lambda _: self.ingest(), range(2)))
        self.assertEqual(sorted(row["imported_count"] for row in results), [0, 2])
        self.assertEqual(sorted(row["existing_count"] for row in results), [0, 2])
        events = [event for event in self.store.read_verified_events(self.rid)
                  if "notion_csv_ref" in event.get("extensions", {})]
        self.assertEqual(len(events), 1)
        batch = self.store.get_object(events[0]["extensions"]["notion_csv_ref"])
        self.assertEqual(batch["context"]["target"], "synth")
        self.assertIn("timestamp", events[0])
        issues = list(campaign_issues(self.store, self.cid).values())
        rows = read_source_records(self.store, issues)
        first = next(issue for issue in issues if issue["title"] == "資料A")
        self.assertEqual(rows[first["finding_id"]]["概要"], summary)
        self.assertEqual(first["source"], "notion")
        self.assertNotIn("severity", first)
        self.assertEqual(self.runtime.list_records(self.cid, "finding"), [])

    def test_conflicting_row_rejects_entire_append(self):
        original = ("A", "one", "未対応", "first")
        self.write([original])
        self.ingest()
        before = self.store.read_verified_events(self.rid)
        self.write([("B", "two", "未対応", ""), ("new title", "one", "修正済", "")])
        with self.assertRaises(DataError):
            self.ingest()
        self.assertEqual(self.store.read_verified_events(self.rid), before)
        self.write([("B", "two", "未対応", ""), original])
        self.assertEqual(self.ingest(), {"imported_count": 1, "existing_count": 1})

    def test_interrupted_batch_remains_invisible_and_can_be_retried(self):
        self.write([("A", "1", "", ""), ("B", "2", "", "")])
        with patch.object(self.runtime, "record_event", side_effect=OSError("synthetic interruption")):
            with self.assertRaises(OSError):
                self.ingest()
        self.assertEqual(campaign_issues(self.store, self.cid), {})
        self.assertEqual(self.ingest()["imported_count"], 2)

    def test_bad_headers_width_utf8_and_duplicate_keys(self):
        for raw in (b"title,title\na,b\n", b"title,status\na,b,c\n", b"title\n\xff\n",
                    b'title,status\n"unfinished\n', b"title\n \n", b"title\na\na\n"):
            with self.subTest(raw=raw):
                self.path.write_bytes(raw)
                with self.assertRaises(DataError):
                    self.ingest()
        self.assertEqual(campaign_issues(self.store, self.cid), {})

    def test_mapped_title_and_title_key_fallback(self):
        folder = self.store.workspace / "decision-view"
        folder.mkdir()
        mapping = {"fields": {"title": "題名", "status": "状態"}, "status_values": {"済": "done"}}
        (folder / "mapping.json").write_text(json.dumps(mapping), encoding="utf-8")
        self.write([("済", "課題B"), ("済", "課題A")], ("状態", "題名"))
        self.assertEqual(self.ingest()["imported_count"], 2)
        result = read_drift(self.runtime, self.cid)
        self.assertEqual({item["title"] for item in result["items"]}, {"課題A", "課題B"})
        self.assertTrue(all(item["closed"] for item in result["items"]))
        self.write([("済", "課題A"), ("済", "課題B")], ("状態", "題名"))
        self.assertEqual(self.ingest()["existing_count"], 2)
        self.write([("missing mapped title",)], ("other",))
        with self.assertRaises(DataError):
            self.ingest()

    def test_damaged_source_holds_and_legacy_inline_fallback_remains_available(self):
        self.write([("A", "one", "", "")])
        self.ingest()
        issue = next(iter(campaign_issues(self.store, self.cid).values()))
        ref = issue["extensions"]["notion"]["source_record_ref"]
        tracked = set()
        _, summary = summarize_run(self.store, self.rid, self.cid, tracked)
        self.assertEqual(summary.status, "complete")
        self.assertIn(ref, tracked)
        self.store._object_path(ref).unlink()
        with self.assertRaises(DataError) as caught:
            read_drift(self.runtime, self.cid)
        self.assertEqual(caught.exception.reason_code, "hold")
        _, incomplete = summarize_run(self.store, self.rid, self.cid, set())
        self.assertNotEqual(incomplete.status, "complete")
        legacy = {"finding_id": "old", "extensions": {"notion": {
            "source_record_ref": ref, "properties": {"ステータス": "未対応"}}}}
        self.assertEqual(read_source_records(self.store, [legacy]), {})
        with self.assertRaises(DataError) as malformed:
            load_csv_row(self.store, self.store.put_object({"record": []}))
        self.assertEqual(malformed.exception.reason_code, "hold")
