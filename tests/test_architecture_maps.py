"""作業場所の構成図を、実際のサーバーと同じ読み取り口で確認する。"""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from ai_yori_murasaki.architecture_maps import MAX_MAP_BYTES
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import handle_request
from ai_yori_murasaki.storage import Store


class ArchitectureMapsTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        root = Path(folder.name)
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.runtime = Runtime(Store(self.workspace, root / "trusted"))
        self.directory = self.workspace / "architecture"
        self.route = "/api/architecture/maps"

    def request(self, path="", method="GET"):
        return handle_request(self.runtime, method, self.route + path)

    def write(self, filename, value):
        self.directory.mkdir(exist_ok=True)
        target = self.directory / filename
        data = value if isinstance(value, bytes) else json.dumps(value, ensure_ascii=False).encode()
        target.write_bytes(data)
        return data

    def test_empty_list_does_not_create_a_directory(self):
        status, _, body = self.request()
        self.assertEqual((status, json.loads(body)), (200, {"maps": []}))
        self.assertFalse(self.directory.exists())
        self.directory.mkdir()
        self.assertEqual(json.loads(self.request()[2]), {"maps": []})

    def test_sorted_valid_files_and_exact_id(self):
        original = self.write("b-detail.json", {"name": " 詳細図 ", "nodes": [], "extra": {"source": "fixture"}})
        self.write("a-first.json", {"nodes": []})
        self.write("Mixed.json", {"nodes": []})
        self.write("ignored.txt", {"nodes": []})
        self.write("broken.json", b"{")
        self.write("no-nodes.json", {"name": "未作成"})
        (self.directory / "folder.json").mkdir()
        status, headers, body = self.request()
        self.assertEqual((status, int(headers["Content-Length"])), (200, len(body)))
        self.assertEqual(json.loads(body)["maps"], [
            {"id": "a-first", "name": "a-first"}, {"id": "b-detail", "name": "詳細図"}])
        status, headers, body = self.request("/b-detail")
        self.assertEqual((status, headers["Content-Type"], body), (200, "application/json; charset=utf-8", original))
        for suffix in ("/missing", "/Mixed", "/mixed", "/folder"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.request(suffix)[0], 404)
        for suffix in ("/broken", "/no-nodes"):
            with self.subTest(suffix=suffix):
                self.assertEqual(self.request(suffix)[0], 409)

    def test_list_limit_and_display_name_limit(self):
        for index in reversed(range(101)):
            self.write(f"map-{index:03d}.json", {"name": "図" * 205, "nodes": []})
        self.write("a-invalid.json", {"name": "未作成"})
        status, _, body = self.request()
        self.assertEqual(status, 200)
        maps = json.loads(body)["maps"]
        self.assertEqual([m["id"] for m in maps], [f"map-{i:03d}" for i in range(100)])
        self.assertEqual(maps[0]["name"], "図" * 200)

    def test_file_size_boundary_and_json_validity(self):
        raw = b'{"nodes":[]}'.ljust(MAX_MAP_BYTES, b" ")
        self.write("at-limit.json", raw)
        self.write("too-large.json", raw + b" ")
        self.write("partial.json", b'{"nodes":')
        status, _, body = self.request()
        self.assertEqual((status, json.loads(body)["maps"]), (200, [{"id": "at-limit", "name": "at-limit"}]))
        status, headers, body = self.request("/at-limit")
        self.assertEqual((status, int(headers["Content-Length"]), body), (200, MAX_MAP_BYTES, raw))
        for path in ("/too-large", "/partial"):
            self.assertEqual(self.request(path)[0], 409)

    def test_blue_is_withheld_before_file_access(self):
        self.write("example.json", {"nodes": []})
        with patch("ai_yori_murasaki.architecture_maps.list_maps") as listing, \
             patch("ai_yori_murasaki.architecture_maps.read_map") as reader:
            for path in ("", "/example", "/missing"):
                status, _, body = self.request(path + "?view=blue")
                self.assertEqual((status, json.loads(body)), (200, {"status": "withheld"}))
            listing.assert_not_called()
            reader.assert_not_called()

    def test_requests_preserve_workspace_contents(self):
        data = self.write("example.json", {"name": "確認用", "nodes": []})

        def snapshot():
            return {p.relative_to(self.workspace): p.stat().st_mtime_ns for p in self.workspace.rglob("*")}

        initial = snapshot()
        for path in ("", "/example"):
            self.assertEqual(self.request(path)[0], 200)
            self.assertEqual(self.request(path, "HEAD")[0], 200)
            for method in ("POST", "PUT", "PATCH", "DELETE"):
                self.assertEqual(self.request(path, method)[0], 405)
        self.assertEqual(snapshot(), initial)
        self.assertEqual((self.directory / "example.json").read_bytes(), data)

    def test_only_publishable_originals_are_listed_and_served(self):
        register = "fixture-private-label"
        self.runtime.store.secrets = (register,)
        public = self.write("public.json", {"nodes": [], "label": "token", "token": "[REDACTED]"})
        self.write("notes.json", {"nodes": [{"note": "draft " + register}]})
        self.write("config.json", {"nodes": [], "extra": {"api-key": "fixture-only"}})
        self.write(register + ".json", {"nodes": []})
        status, _, listing = self.request()
        self.assertEqual((status, json.loads(listing)), (200, {"maps": [{"id": "public", "name": "public"}]}))
        self.assertEqual(self.request("/public")[2], public)
        for map_id in ("notes", "config", register):
            with self.subTest(map_id=map_id):
                status, _, body = self.request("/" + map_id)
                self.assertEqual((status, json.loads(body)), (409, {"error": "map_unavailable"}))
