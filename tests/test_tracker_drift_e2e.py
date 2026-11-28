"""ローカルの CSV 原本から取り込んだ課題を CLI と判断画面の API で照合する。"""
import csv
import io
import json
import tempfile
import threading
import unittest
from unittest.mock import patch
from contextlib import redirect_stdout
from http.client import HTTPConnection
from pathlib import Path

from ai_yori_murasaki.cli import main
from ai_yori_murasaki.decision_view import main as export_decision_view
from ai_yori_murasaki.jsonio import DataError, digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import create_server
from ai_yori_murasaki.storage import Store
from tests.test_tracker_drift_cli import (
    COLLECTED,
    PRIVATE_VALUE,
    offline_tracker_access,
    snapshot,
    tracker_cases,
    write_json,
    write_observation_inputs,
)


PRIVATE_COLUMN = "synthetic-review-note"


def run_cli(root, *args):
    output = io.StringIO()
    with offline_tracker_access(), redirect_stdout(output):
        code = main(["--workspace", str(root / "workspace"), "--trusted-root", str(root / "trusted"),
                     *map(str, args)])
    if code:
        raise AssertionError(f"{args[0]} {args[1]} returned {code}: {output.getvalue()}")
    return json.loads(output.getvalue())


def prepare_csv_fixture(root: Path) -> dict:
    """空のディレクトリに、案件・実行・未取り込みの CSV と観測ファイルを作る。"""
    inputs = root / "input"
    mapping_dir = root / "workspace" / "decision-view"
    inputs.mkdir()
    mapping_dir.mkdir(parents=True)
    (root / "trusted").mkdir()
    write_json(mapping_dir / "mapping.json", {
        "fields": {"key": "棚卸キー", "title": "件名", "status": "進捗", "scope": "環境",
                   "summary": "概要", "updated_at": "最終編集"},
        "status_values": {"Open": "open", "Closed": "done"},
    })
    scope = {"targets": [{"kind": "fixture", "identifier": "t1"}], "max_targets": 1}
    write_json(inputs / "scope.json", scope)
    write_json(inputs / "authorization.json", {
        "operator_id": "fixture", "granted_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope),
    })
    campaign = run_cli(root, "campaign", "create", "--intent-id", "INT-CSV-FIXTURE",
                       "--scope-file", inputs / "scope.json", "--authorization-file",
                       inputs / "authorization.json", "--idempotency-key", "csv-campaign")
    cid = campaign["campaign_id"]
    if run_cli(root, "assess", "drift", "--campaign", cid)["items"]:
        raise AssertionError("新規案件に課題があります")
    rid = run_cli(root, "run", "start", "--campaign-id", cid, "--phase", "verify",
                  "--idempotency-key", "csv-run")["run_id"]
    write_json(inputs / "context.json", {"target": "t1", "target_version": "v1"})
    with (inputs / "notion.csv").open("w", encoding="utf-8-sig", newline="") as source:
        writer = csv.writer(source)
        writer.writerow(["棚卸キー", "件名", "進捗", "環境", "概要", "最終編集", "備考"])
        for key, state, account, summary in tracker_cases():
            updated = "2026-11-21" if key == "case-active" else "2026-11-19"
            writer.writerow([key, "Issue " + key, state, account, '確認, "対象"\n' + summary,
                             updated + "T00:00:00Z", PRIVATE_COLUMN])
    write_observation_inputs(inputs)
    return {"campaign_id": cid, "run_id": rid}


class TrackerCsvEndToEndTests(unittest.TestCase):
    def test_legacy_export_uses_current_settings_and_protects_store(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids = prepare_csv_fixture(root)
            workspace, trusted = root / "workspace", root / "trusted"
            run_cli(root, "assess", "import", "--format", "notion", "--input", root / "input/notion.csv",
                    "--context", root / "input/context.json", "--campaign", ids["campaign_id"],
                    "--run", ids["run_id"])
            out = root / "published.json"
            marker, key = "Issue case-active", "fixture-export-key"
            args = ["--workspace", str(workspace), "--trusted", str(trusted), "--campaign", ids["campaign_id"],
                    "--previous", "none", "--name", key]
            mapping = str(workspace / "decision-view/mapping.json")
            with patch.dict("os.environ", {"AYM_SECRETS": marker, "AYM_LLM_API_KEY": "Bearer " + key}):
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(export_decision_view([*args, "--map", mapping, "--out", str(out)]), 0)
                published = json.loads(out.read_text(encoding="utf-8"))
                self.assertEqual(published["campaign"]["name"], "[REDACTED]")
                self.assertIn("[REDACTED]", [item["title"] for item in published["items"]])
                self.assertNotIn(marker, out.read_text(encoding="utf-8"))
                before = out.read_bytes()
                invalid_mapping = root / "empty-mapping.json"
                invalid_mapping.write_text("{}", encoding="utf-8")
                with self.assertRaises(DataError):
                    export_decision_view([*args, "--map", str(invalid_mapping), "--out", str(out)])
                self.assertEqual(out.read_bytes(), before)
                for protected in (workspace, trusted):
                    destination = protected / "example-export.json"
                    with self.assertRaises(DataError):
                        export_decision_view([*args, "--map", mapping, "--out", str(destination)])
                    self.assertFalse(destination.exists())

    def test_exported_csv_to_drift_cli_and_monitor(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ids = prepare_csv_fixture(root)
            cid, rid = ids["campaign_id"], ids["run_id"]
            inputs = root / "input"
            untouched = snapshot(inputs)
            common = ("--context", inputs / "context.json", "--campaign", cid, "--run", rid)

            notion = ("assess", "import", "--format", "notion", "--input", inputs / "notion.csv", *common)
            run_cli(root, *notion)
            imported = run_cli(root, "assess", "drift", "--campaign", cid)["items"]
            self.assertEqual(len(imported), 8)
            run_cli(root, *notion)
            self.assertEqual(run_cli(root, "assess", "drift", "--campaign", cid)["items"], imported)

            for name in ("inventory", "detections", "inventory_only"):
                run_cli(root, "assess", "import", "--format", "observation",
                        "--input", inputs / (name + ".json"), *common)
            cli_report = run_cli(root, "assess", "drift", "--campaign", cid)
            self.assertEqual(cli_report["counts"], {"open_absent": 1, "open_resolved": 1,
                             "closed_active": 1, "aligned": 1, "uncomparable": 4})
            self.assertEqual(cli_report["reasons"], {"out_of_scope": 1, "no_resource": 1,
                             "inferred_only": 1, "no_detections": 1})
            items = {item["title"]: item for item in cli_report["items"]}
            active = items["Issue case-active"]
            self.assertTrue(active["closed"])
            self.assertTrue(active["stale"])
            self.assertEqual(active["classification"], "closed_active")
            self.assertEqual(active["collected_at"], [COLLECTED])
            self.assertEqual(active["matches"][0]["detections"][0]["id"], "det-asset-active")
            self.assertEqual(items["Issue case-resolved"]["matches"][0]["method"], "name")

            store = Store(root / "workspace", root / "trusted")
            before = snapshot(store.workspace)
            with create_server(Runtime(store), "127.0.0.1:0") as server:
                thread = threading.Thread(target=server.serve_forever, daemon=True)
                thread.start()
                connection = HTTPConnection(*server.server_address[:2], timeout=10)
                try:
                    def get(path):
                        connection.request("GET", path)
                        response = connection.getresponse()
                        body = response.read().decode("utf-8")
                        self.assertEqual(response.status, 200)
                        self.assertNotIn(PRIVATE_COLUMN, body)
                        self.assertNotIn(PRIVATE_VALUE, body)
                        return body

                    self.assertIn('id="sec-drift"', get("/decision?campaign=" + cid))
                    path = "/api/campaigns/" + cid
                    decision = json.loads(get(path + "/decision-view"))["decision_view"]
                    self.assertEqual({row["title"] for row in decision["items"]}, set(items))
                    report = json.loads(get(path + "/drift"))
                    self.assertEqual(report["items"], cli_report["items"])
                    self.assertEqual(report["counts"], cli_report["counts"])
                    self.assertEqual(json.loads(get(path + "/drift?view=blue")), {"status": "withheld"})
                finally:
                    connection.close()
                    server.shutdown()
                    thread.join()
            self.assertEqual(snapshot(store.workspace), before)
            self.assertEqual(snapshot(inputs), untouched)
