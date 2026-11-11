"""Tests for ai_yori_murasaki.policy and adapters.base (v1)."""
from __future__ import annotations

import json
import os
import socket
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from unittest import mock

from ai_yori_murasaki import policy
from ai_yori_murasaki.adapters import base as adapter_base


REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
POLICY_PATH = os.path.join(REPO_ROOT, "policies", "policy.json")


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        if self.path == "/redirect":
            self.send_response(302)
            self.send_header("Location", "http://localhost:1/")
            self.end_headers()
            return
        if self.path == "/big":
            body = b"x" * 100000
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/slow":
            time.sleep(2.0)
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.end_headers()
            self.wfile.write(b"ok")
            return
        body = b"hello"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


class _Server:
    def __init__(self):
        self.httpd = HTTPServer(("127.0.0.1", 0), _Handler)
        self.port = self.httpd.server_address[1]
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.httpd.shutdown()
        self.httpd.server_close()


def _allowlist_for(port: int, prefix: str = "/") -> list:
    return [{
        "host": "localhost",
        "ip": "127.0.0.1",
        "port": port,
        "path_prefix": prefix,
        "verbs": ["GET", "POST"],
    }]


class PolicyLoadTests(unittest.TestCase):
    def test_load_valid_policy(self):
        doc = policy.load(POLICY_PATH)
        self.assertEqual(doc["schema_version"], "1.0.0")
        self.assertEqual(doc["auto_apply"]["production"], "deny")

    def test_unknown_key_fail_closed(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump({"schema_version": "1.0.0", "bogus": 1}, fh)
            path = fh.name
        try:
            with self.assertRaises(policy.PolicyDenied):
                policy.load(path)
        finally:
            os.unlink(path)

    def test_parse_error_fail_closed(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write("{not json")
            path = fh.name
        try:
            with self.assertRaises(policy.PolicyDenied):
                policy.load(path)
        finally:
            os.unlink(path)

    def test_duplicate_key_fail_closed(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            fh.write('{"schema_version":"1.0.0","schema_version":"1.0.0"}')
            path = fh.name
        try:
            with self.assertRaises(policy.PolicyDenied):
                policy.load(path)
        finally:
            os.unlink(path)

    def test_production_auto_apply_must_deny(self):
        doc = policy.load(POLICY_PATH)
        doc["auto_apply"]["production"] = "approval_required"
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as fh:
            json.dump(doc, fh)
            path = fh.name
        try:
            with self.assertRaises(policy.PolicyDenied):
                policy.load(path)
        finally:
            os.unlink(path)

    def test_policy_denied_is_data_error_exit4(self):
        exc = policy.PolicyDenied("x")
        self.assertIsInstance(exc, policy.DataError)
        self.assertEqual(exc.exit_code, 4)
        self.assertEqual(exc.reason_code, "policy_denied")


class AllowlistTests(unittest.TestCase):
    def test_localhost_allowed(self):
        with _Server() as srv:
            allow = _allowlist_for(srv.port)
            self.assertTrue(policy.check_allowlist(
                {"url": f"http://localhost:{srv.port}/x", "method": "GET"}, allow))

    def test_userinfo_rejected(self):
        allow = _allowlist_for(80)
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://user:pass@localhost/x", "method": "GET"}, allow)

    def test_fragment_rejected(self):
        allow = _allowlist_for(80)
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://localhost/x#frag", "method": "GET"}, allow)

    def test_host_not_in_allowlist(self):
        allow = _allowlist_for(80)
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://example.com/x", "method": "GET"}, allow)

    def test_port_mismatch(self):
        allow = _allowlist_for(80)
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://localhost:9999/x", "method": "GET"}, allow)

    def test_path_segment_boundary(self):
        allow = _allowlist_for(80, prefix="/api")
        self.assertTrue(policy.check_allowlist(
            {"url": "http://localhost/api", "method": "GET"}, allow))
        self.assertTrue(policy.check_allowlist(
            {"url": "http://localhost/api/v1", "method": "GET"}, allow))
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://localhost/apix", "method": "GET"}, allow)

    def test_verb_mismatch(self):
        allow = _allowlist_for(80)
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://localhost/x", "method": "DELETE"}, allow)

    def test_dns_rebind_fake(self):
        allow = _allowlist_for(80)
        with mock.patch.object(policy, "_resolve_ips", return_value=["10.0.0.1"]):
            with self.assertRaises(policy.PolicyDenied):
                policy.check_allowlist(
                    {"url": "http://localhost/x", "method": "GET"}, allow)

    def test_dns_resolution_failure(self):
        allow = _allowlist_for(80)
        with mock.patch.object(policy, "_resolve_ips",
                               side_effect=policy.PolicyDenied("dns")):
            with self.assertRaises(policy.PolicyDenied):
                policy.check_allowlist(
                    {"url": "http://localhost/x", "method": "GET"}, allow)

    def test_url_crlf_rejected(self):
        allow = _allowlist_for(80)
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://localhost/x\r\nInjected: 1", "method": "GET"}, allow)

    def test_url_dot_segment_rejected(self):
        allow = _allowlist_for(80)
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://localhost/a/../b", "method": "GET"}, allow)

    def test_url_encoded_slash_rejected(self):
        allow = _allowlist_for(80)
        with self.assertRaises(policy.PolicyDenied):
            policy.check_allowlist(
                {"url": "http://localhost/a%2fb", "method": "GET"}, allow)

    def test_resolve_and_pin_single_resolution(self):
        allow = _allowlist_for(80)
        with mock.patch.object(policy, "_resolve_ips",
                               return_value=["127.0.0.1"]) as m:
            pin = policy.resolve_and_pin("http://localhost/x", allow, "GET")
            self.assertEqual(pin["ip"], "127.0.0.1")
            self.assertEqual(m.call_count, 1)


class ActorTests(unittest.TestCase):
    def test_resolve_actor_from_policy(self):
        doc = policy.load(POLICY_PATH)
        actor = policy.resolve_actor({"role": "orchestrator"}, doc)
        self.assertEqual(actor["role"], "orchestrator")
        self.assertEqual(actor["agent_id"], "orchestrator")

    def test_client_agent_id_rejected(self):
        doc = policy.load(POLICY_PATH)
        with self.assertRaises(policy.PolicyDenied):
            policy.resolve_actor({"role": "orchestrator", "agent_id": "evil"}, doc)

    def test_client_model_rejected(self):
        doc = policy.load(POLICY_PATH)
        with self.assertRaises(policy.PolicyDenied):
            policy.resolve_actor({"role": "orchestrator", "model": "evil"}, doc)

    def test_unknown_role_denied(self):
        doc = policy.load(POLICY_PATH)
        with self.assertRaises(policy.PolicyDenied):
            policy.resolve_actor({"role": "hacker"}, doc)

    def test_role_not_configured_denied(self):
        doc = policy.load(POLICY_PATH)
        doc["role_permissions"].pop("oracle")
        with self.assertRaises(policy.PolicyDenied):
            policy.resolve_actor({"role": "oracle"}, doc)


class ProductionWriteTests(unittest.TestCase):
    def test_production_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            policy.check_production_write("production", "fixture_reset")

    def test_lab_finite_allowed(self):
        policy.check_production_write("lab", "fixture_reset")

    def test_lab_unknown_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            policy.check_production_write("lab", "arbitrary")

    def test_dev_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            policy.check_production_write("dev", "fixture_reset")


class HttpBrokerTests(unittest.TestCase):
    def _broker(self, port: int, prefix: str = "/") -> adapter_base.HttpBroker:
        return adapter_base.HttpBroker({"broker_allowlist": _allowlist_for(port, prefix)})

    def test_basic_get(self):
        with _Server() as srv:
            broker = self._broker(srv.port)
            resp = broker.request("GET", f"http://localhost:{srv.port}/x")
            self.assertEqual(resp["status"], 200)
            self.assertEqual(resp["body"], b"hello")

    def test_redirect_denied(self):
        with _Server() as srv:
            broker = self._broker(srv.port)
            with self.assertRaises(policy.PolicyDenied):
                broker.request("GET", f"http://localhost:{srv.port}/redirect")

    def test_oversize_denied(self):
        with _Server() as srv:
            broker = self._broker(srv.port)
            with self.assertRaises(policy.PolicyDenied):
                broker.request("GET", f"http://localhost:{srv.port}/big",
                               max_bytes=1024)

    def test_timeout(self):
        with _Server() as srv:
            broker = self._broker(srv.port)
            with self.assertRaises((socket.timeout, TimeoutError, OSError)):
                broker.request("GET", f"http://localhost:{srv.port}/slow",
                               timeout=0.2)

    def test_header_crlf_rejected(self):
        with _Server() as srv:
            broker = self._broker(srv.port)
            with self.assertRaises(policy.PolicyDenied):
                broker.request("GET", f"http://localhost:{srv.port}/x",
                               headers={"X-Test": "a\r\nInjected: 1"})

    def test_ollama_loopback_allowed(self):
        doc = policy.load(POLICY_PATH)
        broker = adapter_base.HttpBroker(doc)
        # Only validation; no live Ollama required.
        pin = policy.resolve_and_pin(
            "http://localhost:11434/api/tags", doc["broker_allowlist"], "GET")
        self.assertEqual(pin["ip"], "127.0.0.1")
        self.assertEqual(pin["port"], 11434)

    def test_other_host_denied(self):
        doc = policy.load(POLICY_PATH)
        broker = adapter_base.HttpBroker(doc)
        with self.assertRaises(policy.PolicyDenied):
            broker.request("GET", "http://example.com/api/tags")


class ContractTests(unittest.TestCase):
    def test_load_contract_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            with mock.patch.object(adapter_base, "_contracts_dir", return_value=directory):
                with self.assertRaises(adapter_base.ContractError):
                    adapter_base.load_contract("trivy")

    def test_load_contract_unknown_name(self):
        with self.assertRaises(adapter_base.ContractError):
            adapter_base.load_contract("nope")

    def test_contract_schema_validation(self):
        from ai_yori_murasaki.adapters.schema_contracts import SCHEMAS
        from ai_yori_murasaki.jsonio import digest
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trivy.json")
            with open(path, "w") as fh:
                json.dump({
                    "name": "trivy",
                    "version": "1.0.0",
                    "input_ref": digest(SCHEMAS["trivy"]["input"]),
                    "output_ref": digest(SCHEMAS["trivy"]["output"]),
                    "timeout_ms": 1000,
                    "retry_cap": 1,
                    "response_cap": 1024,
                }, fh)
            with mock.patch.object(adapter_base, "_contracts_dir", return_value=td):
                doc = adapter_base.load_contract("trivy")
                self.assertEqual(doc["name"], "trivy")

    def test_contract_bad_timeout(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trivy.json")
            with open(path, "w") as fh:
                json.dump({
                    "name": "trivy",
                    "version": "1.0.0",
                    "input_ref": "0" * 64,
                    "output_ref": "1" * 64,
                    "timeout_ms": 0,
                    "retry_cap": 1,
                    "response_cap": 1024,
                }, fh)
            with mock.patch.object(adapter_base, "_contracts_dir", return_value=td):
                with self.assertRaises(adapter_base.ContractError):
                    adapter_base.load_contract("trivy")


class DockerRunnerTests(unittest.TestCase):
    def test_non_docker_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_docker(["bash", "-c", "echo hi"])

    def test_unknown_subcommand_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_docker(["docker", "rmi", "x"])

    def test_exec_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_docker(["docker", "exec", "aym-1", "sh"])

    def test_privileged_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_docker([
                "docker", "run", "--privileged", "img", "python", "/app/x.py"])

    def test_host_network_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_docker([
                "docker", "run", "--network", "host", "img", "python", "/app/x.py"])

    def test_env_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_docker([
                "docker", "run", "-e", "X=1", "img", "python", "/app/x.py"])

    def test_shell_command_denied(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_docker([
                "docker", "run", "img", "sh", "-c", "echo hi"])

    def test_container_op_requires_aym_name(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_docker(["docker", "inspect", "other"])

    def test_run_adapter_rejects_untrusted(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_adapter("trivy", {"plan": {}})

    def test_run_adapter_rejects_docker_argv_from_request(self):
        with self.assertRaises(policy.PolicyDenied):
            adapter_base.run_adapter("trivy", {
                "trusted": True,
                "argv": ["docker", "run", "img", "python", "/app/x.py"],
            })

    def test_build_argv_roundtrip_validator(self):
        # macOS /tmp is a symlink; keep the guard by using a resolved path.
        with tempfile.TemporaryDirectory() as tmp:
            stage = os.path.realpath(tmp)
            plan = {
                "id": "abc123",
                "image": "img",
                "network": "none",
                "staged_source": stage,
                "script": "/app/run.py",
                "args": ["--x", "1"],
            }
            argv = adapter_base._build_docker_argv_from_plan(plan)
            adapter_base._validate_docker_argv(
                argv, staging_root=stage, allowed_images=["img"])

    def test_build_argv_mount_readonly_true(self):
        plan = {
            "id": "abc123",
            "image": "img",
            "network": "none",
            "staged_source": "/tmp/stage",
            "script": "/app/run.py",
            "args": [],
        }
        argv = adapter_base._build_docker_argv_from_plan(plan)
        mount = argv[argv.index("--mount") + 1]
        self.assertIn("readonly=true", mount)

    def test_shell_false(self):
        with mock.patch.object(adapter_base.subprocess, "Popen") as popen:
            popen.side_effect = OSError("no docker")
            with self.assertRaises(adapter_base.AdapterUnavailable):
                adapter_base.run_docker(
                    ["docker", "inspect", "aym-1"],
                    allowed_names=["aym-1"])
            _, kwargs = popen.call_args
            self.assertFalse(kwargs.get("shell", False))

    def test_timeout_terminates(self):
        class _FakeProc:
            def __init__(self):
                self.returncode = None
                self.stdout = mock.MagicMock()
                self.stderr = mock.MagicMock()
                self.stdout.read = lambda n: b""
                self.stderr.read = lambda n: b""
                self.terminated = False

            def poll(self):
                return None

            def terminate(self):
                self.terminated = True

            def wait(self, timeout=None):
                self.returncode = -15
                return -15

            def kill(self):
                pass

        fake = _FakeProc()
        with mock.patch.object(adapter_base.subprocess, "Popen", return_value=fake):
            with self.assertRaises(adapter_base.AdapterUnavailable):
                adapter_base.run_docker(
                    ["docker", "inspect", "aym-1"], timeout=0.05,
                    allowed_names=["aym-1"])
        self.assertTrue(fake.terminated)


if __name__ == "__main__":
    unittest.main()
