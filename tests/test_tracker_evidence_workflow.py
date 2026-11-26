"""T49–T52 を合成 CSV・観測の取り込み、レポートの取得・書き出しから確認する。"""

import csv
import io
import json
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from http.client import HTTPConnection
from pathlib import Path

from ai_yori_murasaki.cli import main
from ai_yori_murasaki.jsonio import digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import create_server
from ai_yori_murasaki.storage import Store
from tests.test_tracker_drift_cli import COLLECTED, offline_tracker_access, write_json
from tests.test_tracker_drift_e2e import run_cli


SCOPE = {"account": "sandbox-one", "provider": "sample", "regions": ["north"]}
FIELDS = {"key": "ticket", "title": "title", "status": "state", "scope": "account",
          "provider": "provider", "region": "region", "summary": "details"}


def issue(title, details, *, state="Open", category="", account="sandbox-one",
          provider="sample", region="north"):
    return (title, title, state, account, provider, region, details, category)


def resource(rid, kind, name=""):
    return {"id": rid, "name": name, "type": kind, "state": "ready"}


def detection(did, rid, rule, status, kind=None):
    row = {"id": did, "resource_id": rid, "rule": rule, "status": status, "updated_at": COLLECTED}
    if kind is not None:
        row["resource_type"] = kind
    return row


class TrackerEvidenceWorkflowTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.root = Path(folder.name)
        inputs = self.root / "input"
        inputs.mkdir()
        (self.root / "workspace" / "decision-view").mkdir(parents=True)
        (self.root / "trusted").mkdir()
        scope = {"targets": [{"kind": "fixture", "identifier": "example"}], "max_targets": 1}
        write_json(inputs / "scope.json", scope)
        write_json(inputs / "authorization.json", {"operator_id": "reader", "scope_digest": digest(scope),
                   "granted_at": "2026-01-01T00:00:00Z", "expires_at": "2099-01-01T00:00:00Z"})
        campaign = run_cli(self.root, "campaign", "create", "--intent-id", "INT-EVIDENCE-TEST",
                           "--scope-file", inputs / "scope.json", "--authorization-file",
                           inputs / "authorization.json", "--idempotency-key", "evidence-campaign")
        self.cid = campaign["campaign_id"]
        run = run_cli(self.root, "run", "start", "--campaign-id", self.cid, "--phase", "verify",
                      "--idempotency-key", "evidence-run")
        write_json(inputs / "context.json", {"target": "example", "target_version": "v1"})
        self.common = ("--context", inputs / "context.json", "--campaign", self.cid, "--run", run["run_id"])
        self.write_mapping()

    def write_mapping(self, **drift):
        config = {"fields": FIELDS, "status_values": {"Open": "open", "Closed": "done"}}
        if drift:
            config["drift"] = drift
        write_json(self.root / "workspace" / "decision-view" / "mapping.json", config)

    def import_issues(self, *rows):
        source = self.root / "input" / "tickets.csv"
        with source.open("w", newline="", encoding="utf-8-sig") as stream:
            writer = csv.writer(stream)
            writer.writerow(["ticket", "title", "state", "account", "provider", "region", "details", "category"])
            writer.writerows(rows)
        run_cli(self.root, "assess", "import", "--format", "notion", "--input", source, *self.common)

    def observe(self, kind, rows, **metadata):
        source = self.root / "input" / (kind + ".json")
        records = "resources" if kind == "inventory" else "detections"
        write_json(source, {"schema_version": "1.0.0", "method": "offline sample", "scope": SCOPE,
                            "collected_at": COLLECTED, "kind": kind, records: rows, **metadata})
        run_cli(self.root, "assess", "import", "--format", "observation", "--input", source, *self.common)

    def report_items(self):
        report = run_cli(self.root, "assess", "drift", "--campaign", self.cid)
        return {item["title"]: item for item in report["items"]}

    def export(self, fmt):
        destination = self.root / ("report." + fmt)
        output = io.StringIO()
        with offline_tracker_access(), redirect_stdout(output):
            code = main(["--workspace", str(self.root / "workspace"), "--trusted-root", str(self.root / "trusted"),
                         "assess", "drift", "--campaign", self.cid, "--format", fmt, "--output", str(destination)])
        self.assertEqual(code, 0, output.getvalue())
        return destination.read_text(encoding="utf-8")

    def test_t49_rule_filter_and_legacy_file_reach_api_and_exports(self):
        self.import_issues(issue("mapped", "volume-a", state="Closed", category="storage"),
                           issue("unmapped", "volume-a", state="Closed", category="other"))
        self.observe("inventory", [resource("volume-a", "volume")])
        self.observe("detections", [detection("check-a", "volume-a", "replicas", "resolved"),
                                    detection("check-b", "volume-a", "labels", "active")])
        old = self.report_items()["mapped"]
        self.assertEqual(old["classification"], "closed_active")
        self.assertTrue(old["rules_unfiltered"])
        self.assertCountEqual(old["rules_used"], ["replicas", "labels"])

        self.write_mapping(rule_field="category", rules_by_value={"storage": ["replicas"]})
        items = self.report_items()
        self.assertEqual(items["mapped"]["classification"], "aligned")
        self.assertIs(items["mapped"]["rules_unfiltered"], False)
        self.assertEqual(items["mapped"]["rules_used"], ["replicas"])
        self.assertTrue(items["unmapped"]["rules_unfiltered"])
        self.assertEqual(items["unmapped"]["classification"], "closed_active")
        self.assertCountEqual(items["unmapped"]["rules_used"], ["labels", "replicas"])

        store = Store(self.root / "workspace", self.root / "trusted")
        with create_server(Runtime(store), "127.0.0.1:0") as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            connection = HTTPConnection(*server.server_address[:2], timeout=10)
            try:
                path = "/api/campaigns/" + self.cid + "/drift"
                connection.request("GET", path)
                response = connection.getresponse()
                self.assertEqual(response.status, 200)
                api = json.loads(response.read())
                self.assertEqual({row["title"]: row for row in api["items"]}, items)
                connection.request("GET", path + "?view=blue")
                response = connection.getresponse()
                self.assertEqual((response.status, json.loads(response.read())), (200, {"status": "withheld"}))
            finally:
                connection.close()
                server.shutdown()
                thread.join()

        exported = {row["title"]: row for row in json.loads(self.export("json"))["items"]}
        self.assertEqual(exported, items)
        csv_rows = {row["title"]: row for row in csv.DictReader(io.StringIO(self.export("csv")))}
        self.assertEqual(csv_rows["mapped"]["rules_used"], "replicas")
        self.assertEqual(csv_rows["mapped"]["rules_unfiltered"], "false")
        self.assertEqual(csv_rows["unmapped"]["rules_unfiltered"], "true")
        markdown = self.export("md").splitlines()
        mapped = next(line for line in markdown if "| mapped |" in line)
        self.assertIn("replicas", mapped)
        self.assertNotIn("labels", mapped)

    def test_t50_omitted_account_and_explicit_terms(self):
        self.import_issues(issue("mentions", "sandbox-one spare prod"))
        self.observe("inventory", [resource("sandbox-one", "account"), resource("prod", "disk"),
                                   resource("spare", "disk")])
        self.write_mapping(excluded_terms=["spare"])
        row = self.report_items()["mentions"]
        self.assertEqual([match["id"] for match in row["matches"]], ["prod"])
        self.assertEqual(row["excluded_resource_count"], 2)
        exported = next(csv.DictReader(io.StringIO(self.export("csv"))))
        self.assertEqual(exported["excluded_resource_count"], "2")

    def test_t51_type_disambiguates_detections_without_guessing_legacy_types(self):
        self.import_issues(issue("typed-disk", "QuartzDrive", state="Closed"),
                           issue("typed-bucket", "AmberVault", state="Closed"),
                           issue("named-no-type", "SecondDisk", state="Closed"),
                           issue("no-type", "shared-b", state="Closed"))
        self.observe("inventory", [
            resource("shared-a", "disk", "QuartzDrive"), resource("shared-a", "bucket", "AmberVault"),
            resource("shared-b", "disk", "SecondDisk"), resource("shared-b", "bucket", "SecondBucket"),
        ])
        self.observe("detections", [detection("disk-done", "shared-a", "durability", "resolved", "disk"),
                                    detection("bucket-still", "shared-a", "durability", "active", "bucket"),
                                    detection("unknown-type", "shared-b", "durability", "active")])
        items = self.report_items()
        for title, kind, state, did in [("typed-disk", "disk", "aligned", "disk-done"),
                                        ("typed-bucket", "bucket", "closed_active", "bucket-still")]:
            with self.subTest(title=title):
                row = items[title]
                self.assertEqual(row["classification"], state)
                self.assertEqual([match["type"] for match in row["matches"]], [kind])
                self.assertEqual([det["id"] for det in row["matches"][0]["detections"]], [did])
        for title in ("no-type", "named-no-type"):
            with self.subTest(title=title):
                ambiguous = items[title]
                self.assertEqual((ambiguous["classification"], ambiguous["reason"]), ("uncomparable", "inferred_only"))
                self.assertTrue(ambiguous["matches"])
                self.assertEqual({m["method"] for m in ambiguous["matches"]}, {"inferred"})

    def test_t52_absence_requires_declared_coverage_and_issue_scope(self):
        self.import_issues(issue("covered", "disk-2034"), issue("uncovered", "queue-a"),
                           issue("wrong-account", "disk-2035", account="sandbox-two"),
                           issue("wrong-provider", "disk-2036", provider="another"),
                           issue("wrong-region", "disk-2037", region="east"),
                           issue("unknown-account", "disk-2038", account=""),
                           issue("unknown-region", "disk-2039", region=""),
                           issue("unknown-provider", "disk-2040", provider=""),
                           issue("detected-no-region", "disk-2999", region=""),
                           issue("foreign-reference", "arn:sample:store:north:sandbox-two:disk/disk-2041"))
        self.observe("inventory", [], types=["disk"])
        self.observe("detections", [detection("late", "queue-a", "backlog", "active", "queue"),
                                    detection("seen-earlier", "disk-2999", "capacity", "active", "disk")])
        self.assertEqual(self.report_items()["covered"]["classification"], "uncomparable")
        self.observe("inventory", [], types=["disk"], id_patterns={"disk": ["disk-[0-9]{4}"]},
                     collected_at="2026-11-21T12:00:00Z")
        items = self.report_items()
        self.assertEqual(items["covered"]["classification"], "open_absent")
        self.assertEqual([(m["id"], m["type"], m["in_inventory"]) for m in items["covered"]["matches"]],
                         [("disk-2034", "disk", False)])
        self.assertEqual([m["in_inventory"] for m in items["uncovered"]["matches"]], [None])
        self.assertNotEqual(items["uncovered"]["classification"], "open_absent")
        partial = items["detected-no-region"]
        self.assertNotEqual(partial["classification"], "open_absent")
        self.assertFalse(any(m["in_inventory"] is False for m in partial["matches"]))
        for title in ("wrong-account", "wrong-provider", "wrong-region", "unknown-account",
                      "unknown-region", "unknown-provider", "foreign-reference"):
            with self.subTest(title=title):
                self.assertEqual(items[title]["classification"], "uncomparable")
                self.assertFalse(any(m["in_inventory"] is False for m in items[title]["matches"]))
        csv_rows = {row["title"]: row for row in csv.DictReader(io.StringIO(self.export("csv")))}
        self.assertEqual(csv_rows["covered"]["in_inventory"], "false")
        self.assertEqual(csv_rows["uncovered"]["in_inventory"], "unknown")
