"""実際のループバック接続で、通常の起動入口と TLS の契約を確認する。"""
from contextlib import redirect_stdout
from http.client import HTTPSConnection
import io
import json
import os
from pathlib import Path
from queue import Queue
import socket
import ssl
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit

from ai_yori_murasaki import cli
from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import configured_server, create_server, serve
from ai_yori_murasaki.storage import Store
from tests.fixtures.loopback_tls import LoopbackTLS


class MonitorTLSTransportTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        workspace, trusted = self.root / "workspace", self.root / "trusted"
        workspace.mkdir()
        trusted.mkdir()
        self.runtime = Runtime(Store(workspace, trusted))
        self.args = ["--workspace", str(workspace), "--trusted-root", str(trusted),
                     "monitor", "serve", "--bind", "127.0.0.1:0"]
        environment = patch.dict(os.environ)
        environment.start()
        self.addCleanup(environment.stop)
        for name in ("AYM_MONITOR_TLS_CERT", "AYM_MONITOR_TLS_KEY"):
            os.environ.pop(name, None)
        self.tls = LoopbackTLS(self.root)

    def test_cli_https_serves_while_another_connection_is_starting(self):
        proc = subprocess.Popen(
            [sys.executable, "-u", "-m", "ai_yori_murasaki", *self.args,
             "--tls-cert", str(self.tls.cert), "--tls-key", str(self.tls.key)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, encoding="utf-8",
            env=dict(os.environ, PYTHONUTF8="1"),
        )
        try:
            lines = Queue()
            Thread(target=lambda: lines.put(proc.stdout.readline()), daemon=True).start()
            envelope = json.loads(lines.get(timeout=20))
            self.assertEqual(envelope["status"], "ok")
            url = urlsplit(envelope["data"]["url"])
            self.assertEqual((url.scheme, url.hostname), ("https", "127.0.0.1"))
            ctx = self.tls.client_context()
            with socket.create_connection((url.hostname, url.port), timeout=3) as raw:
                with ctx.wrap_socket(raw, server_hostname=url.hostname, do_handshake_on_connect=False) as first:
                    conn = HTTPSConnection(url.hostname, url.port, context=ctx, timeout=3)
                    try:
                        conn.request("GET", "/api/health", headers={"Origin": url.geturl()})
                        response = conn.getresponse()
                        self.assertEqual(response.status, 200)
                        self.assertEqual(json.loads(response.read()), {"status": "ok"})
                    finally:
                        conn.close()
                    first.do_handshake()
                    self.assertIn(first.version(), ("TLSv1.2", "TLSv1.3"))
        finally:
            proc.terminate()
            proc.communicate(timeout=10)

    def test_environment_pair_and_matching_origin(self):
        os.environ.update(AYM_MONITOR_TLS_CERT=str(self.tls.cert), AYM_MONITOR_TLS_KEY=str(self.tls.key))
        with configured_server(self.runtime, "127.0.0.1:0") as server:
            Thread(target=server.serve_forever, daemon=True).start()
            try:
                self.assertGreaterEqual(server.tls_context.minimum_version, ssl.TLSVersion.TLSv1_2)
                for origin, expected in (("https://localhost", 200), ("http://localhost", 403),
                                         ("https://127.0.0.1", 403)):
                    conn = HTTPSConnection("localhost", server.server_port, context=self.tls.client_context(), timeout=3)
                    try:
                        conn.request("GET", "/", headers={"Origin": f"{origin}:{server.server_port}"})
                        response = conn.getresponse()
                        self.assertEqual(response.status, expected)
                        response.read()
                    finally:
                        conn.close()
            finally:
                server.shutdown()

    def test_missing_unusable_and_conflicting_settings_stop_before_listening(self):
        other = self.root / "other"
        other.mkdir()
        wrong_key = LoopbackTLS(other).key
        for options, reason in (
            ([], "monitor_tls_required"),
            (["--tls-cert", str(self.tls.cert)], "monitor_tls_configuration"),
            (["--tls-key", str(self.tls.key)], "monitor_tls_configuration"),
            (["--tls-cert", str(self.tls.cert), "--tls-key", str(wrong_key)], "monitor_tls_configuration"),
            (["--tls-cert", str(other / "missing.pem"), "--tls-key", str(self.tls.key)], "monitor_tls_configuration"),
            (["--tls-key", str(self.tls.key), "--allow-http-loopback"], "monitor_tls_configuration"),
            (["--allow-http-loopback", "--google-sso"], "monitor_tls_required"),
        ):
            with self.subTest(options=options), redirect_stdout(io.StringIO()) as capture:
                self.assertEqual(cli.main([*self.args, *options]), 2)
                output = capture.getvalue()
                self.assertEqual(json.loads(output)["reason_code"], reason)
                self.assertNotIn(str(self.root), output)
                self.assertNotIn("PRIVATE KEY", output)

    def test_embedded_and_explicit_plaintext_entry_points(self):
        with self.assertRaises(DataError) as ctx:
            serve("127.0.0.1:0", self.runtime)
        self.assertEqual(ctx.exception.reason_code, "monitor_tls_required")
        with self.assertRaises(DataError) as ctx:
            create_server(self.runtime, "127.0.0.1:0", google_sso=object())
        self.assertEqual(ctx.exception.reason_code, "monitor_tls_required")
        with configured_server(self.runtime, "127.0.0.1:0", allow_http_loopback=True) as server:
            self.assertEqual(server.scheme, "http")
        with create_server(self.runtime, "127.0.0.1:0") as fixture:
            self.assertEqual(fixture.scheme, "http")
