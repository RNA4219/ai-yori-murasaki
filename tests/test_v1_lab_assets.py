#!/usr/bin/env python3
"""Local unit tests for the finite lab assets.

These tests run entirely on loopback with an ephemeral port using the
stdlib HTTPServer in a background thread. No Docker is required for the
asset-level tests. Docker integration is exercised by the supervisor.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parents[1] / "ai_yori_murasaki" / "lab_assets"
TARGET_PATH = HERE / "target.py"
WORKER_PATH = HERE / "worker.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _LoopbackServer:
    def __init__(self, handler_cls):
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler_cls)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self.server

    def __exit__(self, exc_type, exc, tb):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class TargetAuthorizeTests(unittest.TestCase):
    def test_baseline_authorize_allows_all(self):
        target = _load_module("aym_target_baseline", TARGET_PATH)
        self.assertFalse(target.REQUIRE_ADMIN)
        self.assertTrue(target.authorize("guest"))
        self.assertTrue(target.authorize("admin"))
        self.assertTrue(target.authorize(None))

    def test_patched_authorize_requires_admin(self):
        with tempfile.TemporaryDirectory() as tmp:
            patched = Path(tmp) / "target.py"
            src = TARGET_PATH.read_text(encoding="utf-8")
            patched_src = src.replace("REQUIRE_ADMIN: bool = False", "REQUIRE_ADMIN: bool = True", 1)
            self.assertNotEqual(src, patched_src)
            patched.write_text(patched_src, encoding="utf-8")
            target = _load_module("aym_target_patched", patched)
            self.assertTrue(target.REQUIRE_ADMIN)
            self.assertTrue(target.authorize("admin"))
            self.assertFalse(target.authorize("guest"))
            self.assertFalse(target.authorize(None))

    def test_handler_uses_authorize(self):
        target = _load_module("aym_target_handler", TARGET_PATH)
        with _LoopbackServer(target.Handler) as server:
            host, port = server.server_address
            import urllib.request
            url = "http://{}:{}/record?role=guest".format(host, port)
            with urllib.request.urlopen(url, timeout=5) as resp:
                self.assertEqual(resp.getcode(), 200)
                body = json.loads(resp.read().decode("utf-8"))
                self.assertEqual(body, {"record": "lab-protected"})


class WorkerModeTests(unittest.TestCase):
    def setUp(self):
        self.worker = _load_module("aym_worker_under_test", WORKER_PATH)
        self.target = _load_module("aym_target_for_worker", TARGET_PATH)

    def test_validate_host_rejects_arbitrary(self):
        with self.assertRaises(ValueError):
            self.worker._validate_host("example.com")
        with self.assertRaises(ValueError):
            self.worker._validate_host("aym-")
        self.assertEqual(self.worker._validate_host("aym-lab-1"), "aym-lab-1")

    def test_benign_five_checks_against_loopback(self):
        with _LoopbackServer(self.target.Handler) as server:
            host, port = server.server_address
            with mock.patch.object(self.worker, "_validate_host", return_value=host), \
                    mock.patch.object(self.worker, "FIXED_PORT", port):
                result = self.worker.mode_benign(host)
            self.assertEqual(result["mode"], "benign")
            self.assertEqual(len(result["checks"]), 5)
            self.assertTrue(all(c["passed"] for c in result["checks"]), result["checks"])

    def test_red_three_guest_cases(self):
        with _LoopbackServer(self.target.Handler) as server:
            host, port = server.server_address
            with mock.patch.object(self.worker, "_validate_host", return_value=host), \
                    mock.patch.object(self.worker, "FIXED_PORT", port):
                result = self.worker.mode_red(host)
            self.assertEqual(result["mode"], "red")
            self.assertEqual(len(result["checks"]), 3)
            self.assertEqual([c["name"] for c in result["checks"]], list(self.worker.RED_CASES))
            self.assertTrue(all(c["passed"] for c in result["checks"]))

    def test_blue_reads_only_telemetry(self):
        with tempfile.TemporaryDirectory() as tmp:
            telemetry = Path(tmp) / "telemetry.json"
            rows = [
                {"method": "GET", "path": "/record", "status": 200, "role": "guest", "timestamp": 1.0},
                {"method": "GET", "path": "/health", "status": 200, "role": "", "timestamp": 2.0},
                {"method": "GET", "path": "/record", "status": 200, "role": "admin", "timestamp": 3.0},
            ]
            telemetry.write_text("\n".join(json.dumps(r) for r in rows) + "\n", encoding="utf-8")
            result = self.worker.mode_blue(str(telemetry))
            self.assertEqual(result["mode"], "blue")
            self.assertEqual(result["source_rows"], 3)
            self.assertEqual(result["detection_count"], 1)
            self.assertEqual(result["observations"][0]["role"], "guest")

    def test_unit_mode_baseline_and_patched(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmpdir = Path(tmp)
            shutil.copy(TARGET_PATH, tmpdir / "target.py")
            baseline = self.worker.mode_unit(str(tmpdir))
            self.assertEqual(baseline["status"], "ok", baseline)
            patched_src = (tmpdir / "target.py").read_text(encoding="utf-8").replace(
                "REQUIRE_ADMIN: bool = False", "REQUIRE_ADMIN: bool = True", 1
            )
            (tmpdir / "target.py").write_text(patched_src, encoding="utf-8")
            patched = self.worker.mode_unit(str(tmpdir))
            self.assertEqual(patched["status"], "ok", patched)
            self.assertTrue(patched["observations"][0]["require_admin"])

    def test_unit_mode_missing_target(self):
        with tempfile.TemporaryDirectory() as tmp:
            result = self.worker.mode_unit(tmp)
            self.assertEqual(result["status"], "fail")
            self.assertFalse(result["checks"][0]["passed"])

    def test_worker_script_is_stdlib_executable(self):
        self.assertTrue(WORKER_PATH.is_file())
        text = WORKER_PATH.read_text(encoding="utf-8")
        self.assertIn("if __name__ == \"__main__\"", text)
        self.assertNotIn("import docker", text)
        self.assertNotIn("import requests", text)


if __name__ == "__main__":
    unittest.main()
