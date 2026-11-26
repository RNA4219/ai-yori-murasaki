"""合成した収集ファイルで観測の受け入れ境界と台帳の整合性を確かめる。"""
import copy
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_yori_murasaki.jsonio import MAX_JSON_BYTES, DataError, digest
from ai_yori_murasaki.observations import import_observation, load_observations, local_path
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


class ObservationImports(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        self.runtime = Runtime(Store(root / "workspace", root / "trusted"))
        scope = {"targets": [{"kind": "fixture", "identifier": "synthetic"}], "max_targets": 1}
        auth = {"operator_id": "op", "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope)}
        self.cid = self.runtime.create_campaign("INT-TEST-1", scope, auth, "create")["campaign_id"]
        self.rid = self.runtime.start_run(self.cid, "verify", 1, "start")["run_id"]
        self.input_path = root / "inventory.json"
        self.context_path = root / "context.json"
        self.context_path.write_text(json.dumps({"target": "synthetic", "target_version": "1"}), encoding="utf-8")
        self.doc = {
            "schema_version": "1.0.0", "kind": "inventory", "collected_at": "2026-11-26T10:00:00+09:00",
            "scope": {"provider": "example", "account": "fixture", "regions": ["region-1"]},
            "method": "offline fixture",
            "resources": [{"id": "res-1", "type": "example", "name": "", "state": "stopped"}],
            "source_note": "Synthetic private provenance",
        }
        self.write_doc(self.doc)

    def write_doc(self, doc):
        self.input_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

    def do_import(self):
        return import_observation(self.runtime, self.cid, self.rid, self.input_path, self.context_path)

    def test_concurrent_retries_record_once_and_read_without_mutation(self):
        events_before = self.runtime.store.read_events(self.rid)
        input_before = self.input_path.read_bytes(), self.context_path.read_bytes()
        with patch("socket.socket.connect", side_effect=AssertionError("outbound connection")):
            with ThreadPoolExecutor(max_workers=2) as pool:
                a, b = [future.result() for future in [pool.submit(self.do_import) for _ in range(2)]]
            self.assertEqual(a, b)
            events = self.runtime.store.read_verified_events(self.rid)
            self.assertEqual(len(events), len(events_before) + 1)
            self.assertNotIn("Synthetic private provenance", json.dumps(events))
            self.assertEqual(load_observations(self.runtime, self.cid), [self.doc])
            self.assertEqual(load_observations(self.runtime, self.cid, verified_events={self.rid: events}),
                             [self.doc])
        self.assertEqual(events, self.runtime.store.read_events(self.rid))
        self.assertEqual(input_before, (self.input_path.read_bytes(), self.context_path.read_bytes()))

    def test_invalid_files_do_not_partially_commit(self):
        cases = []
        for key in ("collected_at", "method", "scope", "resources"):
            bad = copy.deepcopy(self.doc)
            del bad[key]
            cases.append(bad)
        cases.extend([
            {**self.doc, "collected_at": "2026-01-01T12:00:00"},
            {**self.doc, "scope": {"provider": "example", "account": "x", "regions": "r"}},
            {**self.doc, "resources": self.doc["resources"] * 2},
            {**self.doc, "kind": "detections", "detections": [
                {"id": "det-1", "resource_id": "", "status": "active", "rule": "r",
                 "updated_at": self.doc["collected_at"]}]},
            {**self.doc, "kind": "detections", "detections": [
                {"id": "det-2", "resource_id": "r1", "status": "pending", "rule": "r",
                 "updated_at": self.doc["collected_at"]}]},
        ])
        start = self.runtime.store.read_events(self.rid)
        for i, invalid in enumerate(cases):
            with self.subTest(i=i):
                self.write_doc(invalid)
                with self.assertRaises(DataError):
                    self.do_import()
        for raw in (b'{"kind": "inventory", "kind": "detections"}', json.dumps(self.doc).encode("utf-16"),
                    b" " * (MAX_JSON_BYTES + 1)):
            with self.subTest(size=len(raw)):
                self.input_path.write_bytes(raw)
                with self.assertRaises(DataError):
                    self.do_import()
        self.assertEqual(start, self.runtime.store.read_events(self.rid))
        self.assertEqual(load_observations(self.runtime, self.cid), [])

    def test_local_paths_only_and_no_read_through_a_link(self):
        for value in ("https://example.invalid/input", "//example.invalid/test/input", r"\\example.invalid\x"):
            with self.subTest(path=value), self.assertRaises(DataError):
                local_path(value)
        with patch("ai_yori_murasaki.observations._is_reparse",
                   side_effect=lambda p: p == self.input_path.parent) as check:
            with self.assertRaises(DataError):
                local_path(self.input_path)
            self.assertNotIn((self.input_path,), [call.args for call in check.call_args_list])

    def test_rejects_mismatched_context_and_campaign(self):
        self.context_path.write_text('{"target":"other","target_version":"1"}', encoding="utf-8")
        with self.assertRaises(DataError):
            self.do_import()
        with self.assertRaises(DataError):
            import_observation(self.runtime, "other", self.rid, self.input_path, self.context_path)
        self.assertEqual(load_observations(self.runtime, self.cid), [])

    def test_empty_collections_and_unavailable_evidence(self):
        for kind, key in (("inventory", "resources"), ("detections", "detections")):
            doc = {k: v for k, v in self.doc.items() if k != "resources"}
            doc.update({"kind": kind, key: []})
            self.write_doc(doc)
            ref = self.do_import()["observation_ref"]
        self.assertEqual(len(load_observations(self.runtime, self.cid)), 2)
        with patch.object(self.runtime.store, "get_object", side_effect=FileNotFoundError(ref)):
            with self.assertRaises(DataError) as error:
                load_observations(self.runtime, self.cid)
        self.assertEqual(error.exception.reason_code, "hold")
