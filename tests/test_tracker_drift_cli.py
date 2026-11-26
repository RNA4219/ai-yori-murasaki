"""T43–T48 の CLI 検収。取り込み済みの課題を既存の Store 契約から合成する。"""

import csv
import hashlib
import io
import json
import os
import socket
import subprocess
import tempfile
import unittest
from contextlib import ExitStack, contextmanager, redirect_stdout
from pathlib import Path
from unittest.mock import patch

from ai_yori_murasaki.cli import main
from ai_yori_murasaki.jsonio import digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


COLLECTED = "2026-11-20T12:00:00Z"
ACCOUNT = "fixture-a"
PRIVATE_VALUE = "synthetic-private-address"


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


def resource(identifier, name):
    return {"id": identifier, "name": name, "type": "fixture", "state": "ready"}


def tracker_cases():
    # 同名の資源、未知の状態、範囲外、検知一覧のない別アカウントを含む。
    return [
        ("case-absent", "Open", ACCOUNT, "inspect asset-removed"),
        ("case-resolved", "Open", ACCOUNT, "inspect QuartzName"),
        ("case-active", "Closed", ACCOUNT, "対象asset-activeの通知設定"),
        ("case-aligned", "UnknownLabel", ACCOUNT, "inspect asset-active"),
        ("case-outside", "Open", "unobserved", "inspect asset-active"),
        ("case-unlinked", "Open", ACCOUNT, "awaiting reference"),
        ("case-inferred", "Open", ACCOUNT, "inspect AliasShared"),
        ("case-missing-detections", "Open", "fixture-b", "inspect asset-only"),
    ]


def prepare_fixture(root: Path) -> dict:
    """空のローカルディレクトリに課題8件と、CLI に渡す観測3ファイルを用意する。"""
    workspace, trusted, inputs = root / "workspace", root / "trusted", root / "input"
    for folder in (workspace, trusted, inputs):
        folder.mkdir()
    mapping = {
        "fields": {"status": "state", "scope": "account", "summary": "summary",
                   "updated_at": "edited"},
        "status_values": {"Open": "open", "Closed": "done"},
    }
    mapping_folder = workspace / "decision-view"
    mapping_folder.mkdir()
    write_json(mapping_folder / "mapping.json", mapping)
    store = Store(workspace, trusted)
    runtime = Runtime(store)
    scope = {"targets": [{"kind": "fixture", "identifier": "t1"}], "max_targets": 1}
    auth = {"operator_id": "fixture", "granted_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope)}
    campaign_id = runtime.create_campaign("INT-DRIFT-TEST", scope, auth, "fixture-campaign")["campaign_id"]
    run = runtime.start_run(campaign_id, "verify", 1, "fixture-run")
    run_id = run["run_id"]
    write_json(inputs / "context.json", {
        "target": "t1", "target_version": "v1", "actor": run["actor"]["role"],
        "previous_report_ref": None,
    })
    tracker_refs = []
    for finding_id, status, account, summary in tracker_cases():
        row = {"state": status, "account": account, "summary": summary,
               "edited": "2026-11-21T00:00:00Z" if finding_id == "case-active" else "2026-11-19T00:00:00Z"}
        row_ref = store.put_object({"record": row})
        finding = {
            "schema_version": "1.0.0", "campaign_id": campaign_id, "run_id": run_id,
            "finding_id": finding_id, "source": "benchmark_lab", "title": "Issue " + finding_id,
            "severity": "info", "references": [], "location": {"path": finding_id, "line": None, "column": None},
            "extensions": {"notion": {"source_record_ref": row_ref}},
        }
        tracker_refs.extend((row_ref, runtime.register_record("finding", finding, run_id)))
    write_observation_inputs(inputs)
    return {"campaign_id": campaign_id, "run_id": run_id, "tracker_refs": tracker_refs}


def write_observation_inputs(inputs):
    common = {
        "schema_version": "1.0.0", "collected_at": COLLECTED,
        "scope": {"provider": "fixture", "account": ACCOUNT, "regions": ["r1"]},
        "method": "local fixture export",
    }
    inventory = {
        **common, "kind": "inventory",
        "resources": [
            resource("asset-fixed", "QuartzName"), resource("asset-active", "ActiveName"),
            resource("asset-first", "AliasShared"), resource("asset-second", "AliasShared"),
        ],
    }
    inventory["resources"][0]["address"] = PRIVATE_VALUE
    write_json(inputs / "inventory.json", inventory)
    statuses = [("asset-fixed", "resolved"), ("asset-active", "active"), ("asset-removed", "resolved")]
    write_json(inputs / "detections.json", {
        **common, "kind": "detections",
        "detections": [{"id": "det-" + rid, "resource_id": rid, "rule": "fixture-check", "status": state,
                        "updated_at": "2026-11-20T11:00:00Z"} for rid, state in statuses],
    })
    write_json(inputs / "inventory_only.json", {
        **common, "scope": {**common["scope"], "account": "fixture-b"}, "kind": "inventory",
        "resources": [resource("asset-only", "UnusedName")],
    })


@contextmanager
def offline_tracker_access():
    """外向き通信、サブプロセス、tracker用の認証情報の読み出しを捕捉する。"""
    attempts = []

    def deny(*_args, **_kwargs):
        attempts.append("external access")
        raise AssertionError("外部接続または認証情報へのアクセス")

    env_type = type(os.environ)
    original_getitem = env_type.__getitem__

    def checked_env(env, key):
        if key.upper().startswith(("NOTION_", "GITHUB_", "GH_TOKEN", "GH_CONFIG_")):
            deny()
        return original_getitem(env, key)

    def guard_open(open_file):
        def checked(path, *args, **kwargs):
            if not isinstance(path, int):
                name = os.fsdecode(path).replace("\\", "/").casefold()
                if name.endswith(("/.git-credentials", "/gh/hosts.yml", "/.netrc")) or "/.notion/" in name:
                    deny()
            return open_file(path, *args, **kwargs)
        return checked

    with ExitStack() as stack:
        stack.enter_context(patch.object(env_type, "__getitem__", checked_env))
        for owner, name in ((socket, "getaddrinfo"), (socket, "create_connection"),
                            (socket.socket, "connect"), (socket.socket, "connect_ex"),
                            (socket.socket, "sendto"), (subprocess, "Popen")):
            stack.enter_context(patch.object(owner, name, deny))
        for owner, name in ((io, "open"), (os, "open")):
            stack.enter_context(patch.object(owner, name, guard_open(getattr(owner, name))))
        stack.enter_context(patch("builtins.open", guard_open(open)))
        yield
    if attempts:
        raise AssertionError("外部アクセスを検出")


def snapshot(path):
    return {entry.relative_to(path): hashlib.sha256(entry.read_bytes()).digest()
            for entry in path.rglob("*") if entry.is_file()}


class TrackerDriftCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.fixture = prepare_fixture(self.root)
        self.cid = self.fixture["campaign_id"]
        self.rid = self.fixture["run_id"]
        self.inputs = self.root / "input"
        env = patch.dict(os.environ, {"AYM_WORKSPACE": str(self.root / "workspace"),
                                      "AYM_TRUSTED_ROOT": str(self.root / "trusted"), "AYM_SECRETS": ""})
        env.start()
        self.addCleanup(env.stop)

    def cli(self, *args, success=True):
        output = io.StringIO()
        with offline_tracker_access(), redirect_stdout(output):
            code = main(["assess", *args])
        if success:
            self.assertEqual(code, 0, output.getvalue())
        else:
            self.assertNotEqual(code, 0, output.getvalue())
        return output.getvalue()

    def import_file(self, name, success=True):
        return self.cli("import", "--format", "observation", "--input", str(self.inputs / name),
                        "--context", str(self.inputs / "context.json"), "--campaign", self.cid, "--run", self.rid,
                        success=success)

    def test_classification_evidence_and_exports_are_read_only(self):
        store = Store(self.root / "workspace", self.root / "trusted")
        original_records = {ref: store.get_object_bytes(ref) for ref in self.fixture["tracker_refs"]}
        original_inputs = snapshot(self.inputs)
        for name in ("inventory.json", "detections.json", "inventory_only.json"):
            self.import_file(name)
        initial_workspace = snapshot(self.root / "workspace")
        initial_trusted = snapshot(self.root / "trusted")
        report_text = self.cli("drift", "--campaign", self.cid)
        self.assertNotIn(PRIVATE_VALUE, report_text)
        report = json.loads(report_text)
        self.assertEqual(report["campaign_id"], self.cid)
        self.assertEqual(report["schema_version"], "1.0.0")
        self.assertEqual(
            {(obs["kind"], obs["scope"]["account"], obs["collected_at"], obs["method"])
             for obs in report["observations"]},
            {("inventory", ACCOUNT, COLLECTED, "local fixture export"),
             ("detections", ACCOUNT, COLLECTED, "local fixture export"),
             ("inventory", "fixture-b", COLLECTED, "local fixture export")},
        )
        json_path = self.root / "out.json"
        self.cli("drift", "--campaign", self.cid, "--format", "json", "--output", str(json_path))
        exported_json = json.loads(json_path.read_text(encoding="utf-8"))
        self.assertEqual({k: v for k, v in exported_json.items() if k != "generated_at"},
                         {k: v for k, v in report.items() if k != "generated_at"})
        self.assertEqual(report["counts"], {"open_absent": 1, "open_resolved": 1, "closed_active": 1,
                                           "aligned": 1, "uncomparable": 4})
        self.assertEqual(report["reasons"], {"out_of_scope": 1, "no_resource": 1,
                                            "inferred_only": 1, "no_detections": 1})
        items = {row["finding_id"]: row for row in report["items"]}
        expected = {"case-absent": ("open_absent", None), "case-resolved": ("open_resolved", None),
                    "case-active": ("closed_active", None), "case-aligned": ("aligned", None),
                    "case-outside": ("uncomparable", "out_of_scope"), "case-unlinked": ("uncomparable", "no_resource"),
                    "case-inferred": ("uncomparable", "inferred_only"),
                    "case-missing-detections": ("uncomparable", "no_detections")}
        self.assertEqual({key: (row["classification"], row.get("reason")) for key, row in items.items()}, expected)
        self.assertEqual(len(items), len(report["items"]))
        for key, method in (("case-absent", "id"), ("case-resolved", "name"), ("case-active", "id")):
            self.assertIn(COLLECTED, items[key]["collected_at"])
            self.assertEqual(items[key]["matches"][0]["method"], method)
            self.assertTrue(items[key]["matches"][0]["detections"])
        self.assertTrue(items["case-active"]["stale"])
        self.assertFalse(items["case-resolved"]["stale"])
        self.assertFalse(items["case-aligned"]["closed"])
        self.assertEqual(items["case-aligned"]["source_status"], "UnknownLabel")
        self.assertFalse(items["case-absent"]["matches"][0]["in_inventory"])
        self.assertEqual({m["method"] for m in items["case-inferred"]["matches"]}, {"inferred"})
        for fmt in ("md", "csv"):
            with self.subTest(format=fmt):
                rendered = self.cli("drift", "--campaign", self.cid, "--format", fmt)
                self.assertNotIn(PRIVATE_VALUE, rendered)
                self.assertNotIn("%", rendered)
                for value in ("case-active", COLLECTED):
                    self.assertIn(value, rendered)
                destination = self.root / ("out." + fmt)
                self.cli("drift", "--campaign", self.cid, "--format", fmt, "--output", str(destination))
                self.assertEqual(destination.read_text(encoding="utf-8").splitlines(), rendered.splitlines())
                if fmt == "csv":
                    rows = list(csv.DictReader(io.StringIO(rendered)))
                else:
                    header, _, *lines = rendered.splitlines()
                    columns = header.strip("|").split(" | ")
                    columns = [column.strip() for column in columns]
                    rows = []
                    for line in lines:
                        values = [cell.strip().replace(r"\_", "_") for cell in line.strip("|").split(" | ")]
                        rows.append(dict(zip(columns, values, strict=True)))
                self.assertEqual(len(rows), len(items))
                exported = {row["finding_id"]: row for row in rows}
                for key, item in items.items():
                    self.assertEqual(exported[key]["classification"], item["classification"])
                    self.assertEqual(exported[key]["match_method"], ", ".join(m["method"] for m in item["matches"]))
                    self.assertEqual(exported[key]["collected_at"], ", ".join(item["collected_at"]))
        self.assertEqual(snapshot(self.root / "workspace"), initial_workspace)
        self.assertEqual(snapshot(self.root / "trusted"), initial_trusted)
        self.assertEqual(snapshot(self.inputs), original_inputs)
        for ref, value in original_records.items():
            self.assertEqual(store.get_object_bytes(ref), value)

    def test_missing_provenance_rejects_entire_observation(self):
        original = json.loads((self.inputs / "inventory.json").read_text(encoding="utf-8"))
        baseline = snapshot(self.root / "workspace")
        for field in ("collected_at", "scope", "method"):
            with self.subTest(field=field):
                invalid = dict(original)
                del invalid[field]
                write_json(self.inputs / "invalid.json", invalid)
                self.import_file("invalid.json", success=False)
                self.assertEqual(snapshot(self.root / "workspace"), baseline)
        report = json.loads(self.cli("drift", "--campaign", self.cid))
        self.assertEqual(report["observations"], [])

    def test_export_cannot_overwrite_campaign_records(self):
        target = self.root / "workspace" / "decision-view" / "mapping.json"
        initial = target.read_bytes()
        self.cli("drift", "--campaign", self.cid, "--output", str(target), success=False)
        self.assertEqual(target.read_bytes(), initial)

    def test_indexed_api_does_not_ignore_a_new_run(self):
        from ai_yori_murasaki.monitor_index import build_index
        from ai_yori_murasaki.server import handle_request

        runtime = Runtime(Store(self.root / "workspace", self.root / "trusted"))
        self.import_file("inventory.json")
        runtime.finish_run(self.rid, "completed")
        build_index(runtime.store)
        self.rid = runtime.start_run(self.cid, "verify", 2, "second-fixture-run")["run_id"]
        self.import_file("detections.json")
        path = "/api/campaigns/" + self.cid + "/drift"
        status, _, raw = handle_request(runtime, "GET", path)
        self.assertEqual((status, json.loads(raw)), (200, {"status": "hold"}))
        build_index(runtime.store)
        status, _, raw = handle_request(runtime, "GET", path)
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(raw)["counts"]["closed_active"], 1)

    def test_remote_storage_and_policy_rejected_before_runtime_creation(self):
        for flag in ("--workspace", "--trusted-root", "--policy"):
            with self.subTest(flag=flag), patch("ai_yori_murasaki.cli._make_runtime") as make_runtime:
                with redirect_stdout(io.StringIO()):
                    code = main([flag, "//fixture.invalid/share", "assess", "drift", "--campaign", self.cid])
                self.assertEqual(code, 2)
                make_runtime.assert_not_called()
