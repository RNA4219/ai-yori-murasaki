"""Adapter tests: lossless / timeout / secret / cache isolation / unsupported."""
from __future__ import annotations

import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ai_yori_murasaki import jsonio  # noqa: E402
from ai_yori_murasaki.adapters import base, normalizers  # noqa: E402


class TrivyLosslessTest(unittest.TestCase):
    def test_vuln_lossless(self):
        raw = json.dumps({"Results": [{
            "Target": "app", "Class": "os-pkgs",
            "Vulnerabilities": [{"VulnerabilityID": "CVE-1", "PkgName": "x",
                                 "Severity": "HIGH", "Title": "t"}],
        }]}).encode()
        out = normalizers.normalize_trivy(raw)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["findings"][0]["data"]["VulnerabilityID"], "CVE-1")

    def test_secret_redacted(self):
        raw = json.dumps({"Results": [{
            "Target": "app", "Class": "secret",
            "Secrets": [{"RuleID": "aws-key", "Category": "AWS",
                         "StartLine": 3, "Match": "AKIA...", "Code": {"Lines": []}}],
        }]}).encode()
        out = normalizers.normalize_trivy(raw)
        data = out["findings"][0]["data"]
        self.assertNotIn("Match", data)
        self.assertNotIn("Code", data)
        self.assertEqual(data["RuleID"], "aws-key")


class SarifTest(unittest.TestCase):
    def test_count(self):
        raw = json.dumps({"version": "2.1.0", "runs": [{
            "tool": {"driver": {"name": "codeql", "rules": [{"id": "r1"}]}},
            "results": [{"ruleId": "r1", "message": {"text": "m"},
                         "locations": [{"physicalLocation": {
                             "artifactLocation": {"uri": "a.py"},
                             "region": {"startLine": 1}}}]}],
        }]}).encode()
        out = normalizers.normalize_sarif(raw)
        self.assertEqual(out["count"], 1)
        self.assertEqual(out["findings"][0]["rule_id"], "r1")


class SigmaTest(unittest.TestCase):
    def _rule(self, detection):
        return json.dumps({"schema_version": "1.0.0", "rule": {
            "title": "t", "id": "11111111-1111-1111-1111-111111111111",
            "status": "experimental", "logsource": {"category": "webserver"},
            "detection": detection,
        }}).encode()

    def test_equals_attack(self):
        raw = self._rule({"selection": {"status": 403}, "condition": "selection"})
        out = normalizers.normalize_sigma(raw, events=[{"status": 403}, {"status": 200}])
        self.assertEqual(out["fired_indices"], [0])

    def test_contains_attack(self):
        raw = self._rule({"selection": {"path|contains": "/record"},
                          "condition": "selection"})
        out = normalizers.normalize_sigma(raw, events=[{"path": "/x/record/1"},
                                                       {"path": "/public"}])
        self.assertEqual(out["fired_indices"], [0])

    def test_and_not_benign(self):
        raw = self._rule({"selection": {"status": 403},
                          "benign": {"path": "/health"},
                          "condition": "selection and not benign"})
        out = normalizers.normalize_sigma(raw, events=[
            {"status": 403, "path": "/record"},
            {"status": 403, "path": "/health"},
        ])
        self.assertEqual(out["fired_indices"], [0])

    def test_unsupported_wildcard_hold(self):
        raw = self._rule({"selection": {"path|startswith": "/x"},
                          "condition": "selection"})
        with self.assertRaises(normalizers.SigmaUnsupported):
            normalizers.normalize_sigma(raw)

    def test_malformed_unknown_selection_fail(self):
        raw = self._rule({"selection": {"status": 403},
                          "condition": "selection and missing"})
        with self.assertRaises(normalizers.SigmaMalformed):
            normalizers.normalize_sigma(raw)

    def test_malformed_paren_fail(self):
        raw = self._rule({"selection": {"status": 403},
                          "condition": "(selection"})
        with self.assertRaises(normalizers.SigmaMalformed):
            normalizers.normalize_sigma(raw)


class RustXNGTest(unittest.TestCase):
    def test_url_hashed_and_query_redacted(self):
        raw = json.dumps({"results": [{"url": "https://x/y", "title": "t"}]}).encode()
        out = normalizers.normalize_rustxng(raw, query="q=1&token=abc")
        self.assertNotIn("https://x/y", json.dumps(out))
        self.assertIn("token=REDACTED", out["query_redacted"])


class LLMTest(unittest.TestCase):
    def test_token_count_none_preserved(self):
        raw = json.dumps({"message": {"content": "hi"}, "model": "m"}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertIsNone(out["token_count"])
        self.assertEqual(out["effective_model"], "m")

    def test_native_token_sum(self):
        raw = json.dumps({"message": {"content": "hi"}, "model": "m",
                          "done": True, "done_reason": "stop",
                          "prompt_eval_count": 7, "eval_count": 5}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertEqual(out["token_count"], 12)
        self.assertTrue(out["complete"])

    def test_native_missing_counts_none(self):
        raw = json.dumps({"message": {"content": "hi"}, "model": "m",
                          "done": True, "done_reason": "stop",
                          "prompt_eval_count": 7}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertIsNone(out["token_count"])

    def test_native_incomplete_done_false(self):
        raw = json.dumps({"message": {"content": "hi"}, "model": "m",
                          "done": False, "done_reason": "length"}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertFalse(out["complete"])

    def test_native_incomplete_missing_metadata(self):
        raw = json.dumps({"message": {"content": "hi"}, "model": "m"}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertFalse(out["complete"])

    def test_native_empty_content_no_toolcalls_incomplete(self):
        raw = json.dumps({"message": {"content": ""}, "model": "m",
                          "done": True, "done_reason": "stop"}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertFalse(out["complete"])

    def test_native_tool_calls_in_message(self):
        raw = json.dumps({"message": {"content": "",
                          "tool_calls": [{"function": {"name": "f"}}]},
                          "model": "m", "done": True,
                          "done_reason": "tool_calls"}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertEqual(len(out["tool_calls"]), 1)
        self.assertTrue(out["complete"])

    def test_oai_tool_calls_in_message(self):
        raw = json.dumps({"choices": [{"message": {"content": None,
                          "tool_calls": [{"id": "c1"}]},
                          "finish_reason": "tool_calls"}],
                          "model": "m"}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertEqual(len(out["tool_calls"]), 1)
        self.assertTrue(out["complete"])

    def test_oai_missing_finish_reason_incomplete(self):
        raw = json.dumps({"choices": [{"message": {"content": "hi"}}],
                          "model": "m"}).encode()
        out = normalizers.normalize_llm(raw, request_body={"model": "m"})
        self.assertFalse(out["complete"])

    def test_glm_think_low_and_deepseek_false(self):
        from unittest.mock import Mock
        from ai_yori_murasaki.adapters.llm import LLMClient
        b = Mock(spec=base.HttpBroker)
        b.request.return_value = {
            "status": 200, "headers": {},
            "body": json.dumps({"message": {"content": "x"},
                                "model": "m", "done": True,
                                "done_reason": "stop"}).encode(),
        }
        c = LLMClient(b)
        c.chat(url="http://x/api/chat", role="r", campaign="c", run="1",
               model="glm-4", messages=[{"role": "user", "content": "a"}])
        body = json.loads(b.request.call_args[1]["body"])
        self.assertEqual(body.get("think"), "low")
        c.chat(url="http://x/api/chat", role="r", campaign="c", run="2",
               model="deepseek-chat", messages=[{"role": "user", "content": "a"}])
        body = json.loads(b.request.call_args[1]["body"])
        self.assertIs(body.get("think"), False)

    def test_cache_isolation(self):
        from unittest.mock import Mock
        from ai_yori_murasaki.adapters.llm import LLMClient
        b = Mock(spec=base.HttpBroker)
        b.request.return_value = {
            "status": 200, "headers": {},
            "body": json.dumps({"message": {"content": "x"},
                                "model": "m"}).encode(),
        }
        c = LLMClient(b)
        c.chat(url="http://x/api/chat", role="r", campaign="c", run="1",
               model="m", messages=[{"role": "user", "content": "a"}])
        c.chat(url="http://x/api/chat", role="r", campaign="c", run="1",
               model="m", messages=[{"role": "user", "content": "a"}])
        self.assertEqual(b.request.call_count, 1)
        c.chat(url="http://x/api/chat", role="r", campaign="c", run="2",
               model="m", messages=[{"role": "user", "content": "a"}])
        self.assertEqual(b.request.call_count, 2)


class ToFindingsTest(unittest.TestCase):
    def _norm(self, **over):
        base_f = {
            "kind": "vulnerability",
            "stable_id": "a" * 64,
            "target": "app",
            "data": {"Severity": "HIGH", "Title": "t"},
        }
        base_f.update(over)
        return {"schema_version": "1.0.0", "findings": [base_f], "count": 1}

    def test_validates_against_schema(self):
        from ai_yori_murasaki import contracts
        out = normalizers.to_findings(self._norm(), "trivy", "c" * 64, "r" * 64)
        self.assertEqual(len(out), 1)
        contracts.validate("finding", out[0])
        self.assertEqual(out[0]["schema_version"], "1.0.0")
        self.assertEqual(out[0]["finding_id"], "a" * 64)
        self.assertIsInstance(out[0]["location"], dict)
        self.assertIn("path", out[0]["location"])
        self.assertIn("line", out[0]["location"])
        self.assertIn("column", out[0]["location"])

    def test_severity_high_mapped(self):
        out = normalizers.to_findings(self._norm(), "trivy", "c" * 64, "r" * 64)
        self.assertEqual(out[0]["severity"], "high")
        self.assertNotIn("original_severity", out[0]["extensions"])

    def test_severity_unknown_preserved(self):
        out = normalizers.to_findings(
            self._norm(data={"Severity": "WEIRD"}), "trivy", "c" * 64, "r" * 64)
        self.assertEqual(out[0]["severity"], "info")
        self.assertEqual(out[0]["extensions"]["original_severity"], "WEIRD")

    def test_title_truncated_preserved(self):
        long_title = "x" * 600
        out = normalizers.to_findings(
            self._norm(data={"Severity": "HIGH", "Title": long_title}),
            "trivy", "c" * 64, "r" * 64)
        self.assertEqual(len(out[0]["title"]), 512)
        self.assertEqual(out[0]["extensions"]["original_title"], long_title)

    def test_references_from_data(self):
        out = normalizers.to_findings(
            self._norm(data={"Severity": "HIGH", "Title": "t",
                             "References": ["https://x"]}),
            "trivy", "c" * 64, "r" * 64)
        self.assertEqual(out[0]["references"], ["https://x"])


class ContractTest(unittest.TestCase):
    def test_all_contracts_load(self):
        for name in base.ADAPTER_NAMES:
            c = base.load_contract(name)
            self.assertEqual(c["name"], name)
            self.assertNotEqual(c["input_ref"], "0" * 64)

    def test_contracts_match_schema_contracts(self):
        from ai_yori_murasaki.adapters import schema_contracts
        for name, schemas in schema_contracts.SCHEMAS.items():
            c = base.load_contract(name)
            self.assertEqual(c["input_ref"], base._digest_schema(schemas["input"]))
            self.assertEqual(c["output_ref"], base._digest_schema(schemas["output"]))

    def test_contract_rejects_dup_key(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trivy.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"name":"trivy","name":"trivy"}')
            with patch.object(base, "_contracts_dir", return_value=td):
                with self.assertRaises(base.ContractError):
                    base.load_contract("trivy")

    def test_contract_rejects_nan(self):
        import tempfile
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "trivy.json")
            with open(path, "w", encoding="utf-8") as fh:
                fh.write('{"name":"trivy","timeout_ms":NaN}')
            with patch.object(base, "_contracts_dir", return_value=td):
                with self.assertRaises(base.ContractError):
                    base.load_contract("trivy")


class BudgetTest(unittest.TestCase):
    def test_llm_budget_bounds(self):
        from unittest.mock import Mock
        from ai_yori_murasaki.adapters.llm import LLMClient
        b = Mock(spec=base.HttpBroker)
        with self.assertRaises(jsonio.DataError):
            LLMClient(b, retry_cap=3)
        with self.assertRaises(jsonio.DataError):
            LLMClient(b, timeout_ms=300001)
        with self.assertRaises(jsonio.DataError):
            LLMClient(b, response_cap=52428801)
        with self.assertRaises(jsonio.DataError):
            LLMClient(b, retry_cap=True)
        with self.assertRaises(jsonio.DataError):
            LLMClient(b, timeout_ms=float("nan"))

    def test_clients_budget_bounds(self):
        from unittest.mock import Mock
        from ai_yori_murasaki.adapters.clients import RustXNGClient, WazuhClient
        b = Mock(spec=base.HttpBroker)
        with self.assertRaises(jsonio.DataError):
            RustXNGClient(b, base_url="http://x", retry_cap=3)
        with self.assertRaises(jsonio.DataError):
            WazuhClient(b, base_url="http://x", timeout_ms=300001)


class RustXNGPolicyTest(unittest.TestCase):
    def _client(self):
        from unittest.mock import Mock
        from ai_yori_murasaki.adapters.clients import RustXNGClient
        b = Mock(spec=base.HttpBroker)
        b.request.return_value = {
            "status": 200, "headers": {},
            "body": json.dumps({"results": []}).encode(),
        }
        return RustXNGClient(b, base_url="http://x"), b

    def test_blue_normal_sends(self):
        c, b = self._client()
        c.search("public query", role="worker-blue")
        self.assertEqual(b.request.call_count, 1)
        url = b.request.call_args[0][1]
        self.assertIn("public%20query", url)

    def test_red_sends_zero(self):
        from ai_yori_murasaki import policy as policy_mod
        c, b = self._client()
        with self.assertRaises(policy_mod.PolicyDenied):
            c.search("q", role="worker-red")
        self.assertEqual(b.request.call_count, 0)

    def test_secret_rejected(self):
        from ai_yori_murasaki import policy as policy_mod
        c, b = self._client()
        with self.assertRaises(policy_mod.PolicyDenied):
            c.search("token=abc123", role="worker-blue", secrets=("abc123",))
        self.assertEqual(b.request.call_count, 0)

    def test_private_host_rejected(self):
        from ai_yori_murasaki import policy as policy_mod
        c, b = self._client()
        for q in ("host.internal", "localhost", "10.0.0.1", "192.168.1.1"):
            with self.assertRaises(policy_mod.PolicyDenied):
                c.search(q, role="worker-blue")
        self.assertEqual(b.request.call_count, 0)

    def test_raw_query_not_in_error(self):
        from unittest.mock import Mock
        from ai_yori_murasaki.adapters.clients import RustXNGClient
        b = Mock(spec=base.HttpBroker)
        b.request.return_value = {"status": 500, "headers": {}, "body": b""}
        c = RustXNGClient(b, base_url="http://x", retry_cap=0)
        with self.assertRaises(base.AdapterUnavailable) as ctx:
            c.search("supersecretquery", role="worker-blue")
        self.assertNotIn("supersecretquery", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
