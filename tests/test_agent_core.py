"""Agent loop and tool broker tests with a scripted fake LLM.

No network, no docker, no real model: the fake LLM replays scripted
responses so the loop's decisions, budgets, failures and evidence
recording are tested deterministically against the real Runtime/Store.
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from ai_yori_murasaki.agents.core import AgentLoop
from ai_yori_murasaki.agents.policies import load_default_bundle
from ai_yori_murasaki.agents.tools import ToolBroker
from ai_yori_murasaki.jsonio import DataError, digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


def _resp(content) -> dict:
    return {
        "schema_version": "1.0.0",
        "content": content,
        "effective_model": "fake-model",
        "prompt_hash": "b" * 64,
        "token_count": 10,
        "complete": True,
        "finish_reason": "stop",
        "done": True,
        "done_reason": "stop",
        "tool_calls": [],
        "response_metadata": {},
        "request_hash": "c" * 64,
    }


def _action(tool: str, **args) -> str:
    return json.dumps({"thought": "てすと", "action": {"tool": tool,
                                                    "args": args}},
                      ensure_ascii=False)


class FakeLLM:
    """Scripted LLM: pops one queued response per chat() call."""

    def __init__(self, responses) -> None:
        self.responses = list(responses)
        self.calls = []

    def chat(self, *, url, role, campaign, run, model, messages,
             temperature=0.0, seed=None, token_cap=None, native=True):
        self.calls.append({"role": role, "model": model,
                           "messages": json.loads(json.dumps(messages))})
        if not self.responses:
            raise AssertionError("fake LLM exhausted")
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "ws"
        self.tr = root / "tr"
        self.ws.mkdir()
        self.tr.mkdir()
        self.store = Store(self.ws, self.tr)
        self.rt = Runtime(self.store)
        scope = {"targets": [{"kind": "fixture", "identifier": "t1"}],
                 "max_targets": 1}
        auth = {"operator_id": "op1",
                "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z",
                "scope_digest": digest(scope)}
        campaign = self.rt.create_campaign("INT-TEST-AG", scope, auth, "k1")
        self.cid = campaign["campaign_id"]
        self.rt.transition(self.cid, "running")
        run = self.rt.start_run(self.cid, "red", 1, "r1")
        self.rid = run["run_id"]
        self.bundle = load_default_bundle()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _events(self):
        return self.store.read_events(self.rid)

    def _loop(self, llm, role, **kw):
        cfg = kw.pop("role_cfg", None) or self.bundle["roles"][role]
        return AgentLoop(llm_client=llm, runtime=self.rt,
                         campaign_id=self.cid, run_id=self.rid, role=role,
                         role_cfg=cfg, seed=1, **kw)

    def _target_dir(self, text: str = "X = 1\n") -> Path:
        d = Path(self.tmp.name) / "target"
        d.mkdir(exist_ok=True)
        (d / "target.py").write_text(text, encoding="utf-8")
        return d


class RedLoopTest(_Base):
    def test_red_happy_path_records_evidence(self) -> None:
        probe_calls = []

        def probe(path):
            probe_calls.append(path)
            return {"status": 200, "body": {"record": "canary"},
                    "method": "GET", "path": path, "timestamp": 1.0,
                    "elapsed": 0.1, "role": "guest",
                    "observation_ref": digest({"probe": path})}

        seen = {}

        def script(llm: FakeLLM):
            # first turn: probe; second turn: stop citing the probe ref
            def step(*, url, role, campaign, run, model, messages,
                     temperature, seed, token_cap, native):
                seen["last_messages"] = messages
                if len(llm.calls) == 1:
                    return _resp(_action("probe_endpoint", path="/record?role=guest"))
                last = messages[-1]["content"]
                doc = json.loads(last)
                ref = doc["result"]["ref"]
                seen["probe_ref"] = ref
                return _resp(_action("stop", reason="未許可アクセスを確認",
                                     report={"unauthorized_access": True,
                                             "evidence_refs": [ref]}))
            return step

        llm = FakeLLM([])
        llm.chat = None
        # Build a callable-based fake
        holder = {"n": 0}

        def chat(**kwargs):
            holder["n"] += 1
            llm.calls.append(kwargs)
            if holder["n"] == 1:
                return _resp(_action("probe_endpoint",
                                     path="/record?role=guest"))
            last = kwargs["messages"][-1]["content"]
            doc = json.loads(last)
            ref = doc["result"]["ref"]
            return _resp(_action("stop", reason="未許可アクセスを確認",
                                 report={"unauthorized_access": True,
                                         "evidence_refs": [ref]}))

        llm.chat = chat
        result = self._loop(llm, "red").run(
            task="調査せよ", probe_executor=probe)
        self.assertEqual(result["status"], "completed")
        self.assertEqual(probe_calls, ["/record?role=guest"])
        types = [e["event_type"] for e in self._events()]
        self.assertIn("attack_executed", types)
        self.assertTrue(any(
            (e.get("extensions") or {}).get("record_type") == "agent_turn"
            for e in self._events()))
        self.assertTrue(result["report"]["unauthorized_access"])

    def test_llm_incomplete_fails_closed(self) -> None:
        llm = FakeLLM([_resp("")])
        result = self._loop(llm, "red").run(task="x")
        self.assertEqual(result["status"], "llm_failure")

    def test_llm_adapter_error_fails_closed(self) -> None:
        from ai_yori_murasaki.adapters.base import AdapterUnavailable
        llm = FakeLLM([AdapterUnavailable("down")])
        result = self._loop(llm, "red").run(task="x")
        self.assertEqual(result["status"], "llm_failure")

    def test_invalid_output_budget(self) -> None:
        llm = FakeLLM([_resp("not json"), _resp("still not json"),
                       _resp("nor this"), _resp("nor this")])
        result = self._loop(llm, "red").run(task="x")
        self.assertEqual(result["status"], "invalid_action_budget")
        self.assertEqual(result["llm_calls"], 3)

    def test_budget_exhaustion(self) -> None:
        cfg = dict(self.bundle["roles"]["red"])
        cfg["budget"] = {"max_turns": 8, "max_llm_calls": 1,
                         "max_invalid_actions": 3, "token_cap": 1024,
                         "wall_seconds": 300}
        llm = FakeLLM([_resp(_action("read_evidence", ref="0" * 64))])
        result = self._loop(llm, "red", role_cfg=cfg).run(task="x")
        self.assertEqual(result["status"], "budget_exhausted")
        self.assertEqual(result["stop_reason"], "budget")

    def test_unlisted_tool_denied(self) -> None:
        llm = FakeLLM([
            _resp(_action("read_target_file", path="target.py")),
            _resp(_action("stop", reason="完了",
                          report={"unauthorized_access": False,
                                  "evidence_refs": []})),
        ])
        result = self._loop(llm, "red").run(task="x")
        self.assertEqual(result["status"], "completed")
        denied = [e for e in self._events()
                  if (e.get("extensions") or {}).get("record_type")
                  == "agent_tool_denied"]
        self.assertEqual(len(denied), 1)
        self.assertEqual(denied[0]["extensions"]["reason"],
                         "tool_not_permitted")


class BlueBrokerTest(_Base):
    def _blue_broker(self, issued):
        broker = ToolBroker(runtime=self.rt, campaign_id=self.cid,
                            run_id=self.rid, role="blue",
                            grants=["read_evidence", "submit_findings",
                                    "stop"])
        broker.issue_refs(issued)
        return broker

    def _entry(self, ref, **kw):
        entry = {
            "category": "authz", "severity": "high",
            "title": "ゲストroleによる保護レコードへの未許可アクセス",
            "path": "/record", "method": "GET", "role": "guest",
            "evidence_ref": ref,
            "rationale": "保護対象への未許可アクセスが成功している"}
        entry.update(kw)
        return entry

    def test_submit_findings_registers_llm_sourced_finding(self) -> None:
        ref = self.store.put_object({"method": "GET", "path": "/record",
                                     "status": 200, "role": "guest"})
        broker = self._blue_broker([ref])
        result = broker.execute("submit_findings",
                                 {"findings": [self._entry(ref)]})
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["result"]["submitted"], 1)
        finding_id = result["result"]["finding_ids"][0]
        records = self.rt.list_records(self.cid, "finding")
        found = [self.store.get_object(r) for r in records]
        self.assertEqual(len(found), 1)
        self.assertEqual(found[0]["source"], "llm")
        self.assertEqual(found[0]["finding_id"], finding_id)
        types = [e["event_type"] for e in self._events()]
        self.assertIn("prioritized", types)
        # duplicate submission is idempotent
        again = broker.execute("submit_findings", {
            "findings": [self._entry(ref, title="再送",
                                    rationale="重複")]})
        self.assertEqual(again["result"]["submitted"], 0)
        self.assertEqual(again["result"]["duplicates"], 1)
        self.assertEqual(len(self.rt.list_records(self.cid, "finding")), 1)

    def test_submit_findings_batch_registers_each_row(self) -> None:
        ref1 = self.store.put_object({"path": "/record", "role": "user"})
        ref2 = self.store.put_object({"path": "/record", "role": "guest"})
        broker = self._blue_broker([ref1, ref2])
        result = broker.execute("submit_findings", {"findings": [
            self._entry(ref1, role="user", title="user行"),
            self._entry(ref2, role="guest", title="guest行"),
            self._entry(ref2, role="guest", title="guest行(同一行の重複)"),
        ]})
        self.assertEqual(result["result"]["submitted"], 2)
        self.assertEqual(result["result"]["duplicates"], 1)
        self.assertEqual(len(self.rt.list_records(self.cid, "finding")), 2)

    def test_submit_findings_rejects_fabricated_ref(self) -> None:
        broker = self._blue_broker([digest({"known": 1})])
        result = broker.execute("submit_findings", {
            "findings": [self._entry("f" * 64)]})
        self.assertEqual(result["type"], "tool_error")
        self.assertIn("unknown_evidence_ref", result["error"])

    def test_submit_findings_rejects_bad_category(self) -> None:
        ref = self.store.put_object({"row": 1})
        broker = self._blue_broker([ref])
        result = broker.execute("submit_findings", {
            "findings": [self._entry(ref, category="crypto")]})
        self.assertEqual(result["type"], "tool_error")
        self.assertIn("invalid_category", result["error"])
        # the whole batch is rejected: no finding registered
        self.assertEqual(self.rt.list_records(self.cid, "finding"), [])


class PurplePatchTest(_Base):
    def _broker(self, target_dir, validator):
        return ToolBroker(runtime=self.rt, campaign_id=self.cid,
                          run_id=self.rid, role="purple",
                          grants=["read_target_file", "propose_patch", "stop"],
                          target_dir=target_dir,
                          unit_validator=validator, max_fix_attempts=2)

    FLAWED = ("def authorize(role):\n"
              "    return role != 'administrator'\n")
    FIXED = ("def authorize(role):\n"
             "    return role == 'admin'\n")

    def test_propose_patch_validates_and_stops(self) -> None:
        target_dir = self._target_dir(self.FLAWED)
        broker = self._broker(target_dir, lambda nc: {
            "static_passed": True,
            "unit_checks": [{"name": "x", "expected": True,
                             "actual": True, "passed": True}],
            "unit_passed": True})
        result = broker.execute("propose_patch", {
            "root_cause": "比較が反転している", "rationale": "adminのみ許可",
            "new_content": self.FIXED})
        self.assertEqual(result["type"], "tool_result")
        proposal_ref = result["result"]["proposal_ref"]
        self.assertTrue(result["result"]["valid"])
        stop = broker.execute("stop", {
            "reason": "修正案を提出",
            "report": {"proposal_ref": proposal_ref}})
        self.assertEqual(stop["type"], "stop")
        types = [e["event_type"] for e in self._events()]
        self.assertIn("remediation_drafted", types)
        self.assertIn("patch_drafted", types)
        proposal = self.store.get_object(proposal_ref)
        self.assertEqual(proposal["after"], self.FIXED)
        self.assertEqual(proposal["before"], self.FLAWED)

    def test_propose_patch_rejects_syntax_error(self) -> None:
        target_dir = self._target_dir(self.FLAWED)
        broker = self._broker(target_dir, lambda nc: {})
        result = broker.execute("propose_patch", {
            "root_cause": "x", "rationale": "x",
            "new_content": "def authorize(role)\n    pass\n"})
        self.assertEqual(result["type"], "tool_error")
        self.assertTrue(result["error"].startswith("patch_syntax_error"))

    def test_stop_rejects_unvalidated_proposal(self) -> None:
        target_dir = self._target_dir(self.FLAWED)
        broker = self._broker(target_dir, lambda nc: {
            "static_passed": True, "unit_checks": [],
            "unit_passed": False})
        result = broker.execute("propose_patch", {
            "root_cause": "x", "rationale": "x",
            "new_content": self.FIXED})
        self.assertEqual(result["type"], "tool_result")
        self.assertFalse(result["result"]["valid"])
        stop = broker.execute("stop", {
            "reason": "未検証", "report": {
                "proposal_ref": result["result"]["proposal_ref"]}})
        self.assertEqual(stop["type"], "tool_error")
        self.assertEqual(stop["error"], "report_invalid")
        # A truly unissued ref must still be rejected.
        forged = broker.execute("stop", {
            "reason": "偽造", "report": {"proposal_ref": "f" * 64}})
        self.assertEqual(forged["error"], "unknown_evidence_ref")

    def test_read_target_file_window(self) -> None:
        text = "a = 1\nb = 2\nc = 3\n"
        target_dir = self._target_dir(text)
        broker = self._broker(target_dir, lambda nc: {})
        result = broker.execute("read_target_file", {
            "path": "target.py", "start_line": 2, "end_line": 2})
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["result"]["content"], "2: b = 2")
        self.assertEqual(result["result"]["total_lines"], 3)
        # optional line keys may be omitted entirely
        omitted = broker.execute("read_target_file", {"path": "target.py"})
        self.assertEqual(omitted["type"], "tool_result")
        self.assertEqual(omitted["result"]["start_line"], 1)
        self.assertEqual(omitted["result"]["end_line"], 3)
        self.assertEqual(
            omitted["result"]["content"],
            "1: a = 1\n2: b = 2\n3: c = 3")


    def test_read_target_file_clamps_oversized_request_to_eof(self) -> None:
        target_dir = self._target_dir("".join(
            "value = {}\\n".format(i) for i in range(1, 134)))
        broker = self._broker(target_dir, lambda nc: {})
        result = broker.execute("read_target_file", {
            "path": "target.py", "start_line": 1, "end_line": 300})
        self.assertEqual(result["type"], "tool_result")
        self.assertEqual(result["result"]["total_lines"], 133)
        self.assertEqual(result["result"]["start_line"], 1)
        self.assertEqual(result["result"]["end_line"], 133)
        self.assertNotIn("next_start_line", result["result"])
        self.assertIn("133: value = 133", result["result"]["content"])

    def test_read_target_file_paginates_at_200_lines(self) -> None:
        target_dir = self._target_dir("".join(
            "value = {}\\n".format(i) for i in range(1, 226)))
        broker = self._broker(target_dir, lambda nc: {})
        first = broker.execute("read_target_file", {
            "path": "target.py", "start_line": 1, "end_line": 300})
        self.assertEqual(first["type"], "tool_result")
        self.assertEqual(first["result"]["end_line"], 200)
        self.assertEqual(first["result"]["next_start_line"], 201)
        second = broker.execute("read_target_file", {
            "path": "target.py",
            "start_line": first["result"]["next_start_line"],
            "end_line": 300})
        self.assertEqual(second["type"], "tool_result")
        self.assertEqual(second["result"]["start_line"], 201)
        self.assertEqual(second["result"]["end_line"], 225)
        self.assertNotIn("next_start_line", second["result"])
        self.assertTrue(second["result"]["content"].startswith("201: value = 201"))

    def test_read_target_file_resumes_at_byte_boundary(self) -> None:
        target_dir = self._target_dir(("x" * 500 + "\\n") * 100)
        broker = self._broker(target_dir, lambda nc: {})
        first = broker.execute("read_target_file", {"path": "target.py"})
        self.assertEqual(first["type"], "tool_result")
        self.assertLessEqual(len(first["result"]["content"].encode("utf-8")),
                             32 * 1024)
        self.assertLess(first["result"]["end_line"], 100)
        next_line = first["result"]["next_start_line"]
        self.assertEqual(next_line, first["result"]["end_line"] + 1)
        second = broker.execute("read_target_file", {
            "path": "target.py", "start_line": next_line, "end_line": 300})
        self.assertEqual(second["type"], "tool_result")
        self.assertEqual(second["result"]["end_line"], 100)
        self.assertNotIn("next_start_line", second["result"])

    def test_read_target_file_empty_and_invalid_ranges(self) -> None:
        target_dir = self._target_dir("")
        broker = self._broker(target_dir, lambda nc: {})
        empty = broker.execute("read_target_file", {"path": "target.py"})
        self.assertEqual(empty["type"], "tool_result")
        self.assertEqual(empty["result"]["total_lines"], 0)
        self.assertEqual(empty["result"]["content"], "")
        self.assertNotIn("next_start_line", empty["result"])
        for start, end in ((True, 2), (3, 2), (0, 10)):
            invalid = broker.execute("read_target_file", {
                "path": "target.py", "start_line": start, "end_line": end})
            self.assertEqual(invalid["type"], "tool_error")
            self.assertEqual(invalid["error"], "invalid_line_range")
        target_dir = self._target_dir("x" * (33 * 1024) + "\\n")
        oversized = broker.execute("read_target_file", {"path": "target.py"})
        self.assertEqual(oversized["type"], "tool_error")
        self.assertEqual(oversized["error"], "range_too_large")

    def test_fix_attempts_exhausted(self) -> None:
        target_dir = self._target_dir(self.FLAWED)
        broker = self._broker(target_dir, lambda nc: {
            "static_passed": True, "unit_checks": [],
            "unit_passed": False})
        for _ in range(2):
            result = broker.execute("propose_patch", {
                "root_cause": "x", "rationale": "x",
                "new_content": self.FIXED + "\n# attempt\n"})
            self.assertEqual(result["type"], "tool_result")
        third = broker.execute("propose_patch", {
            "root_cause": "x", "rationale": "x",
            "new_content": self.FIXED + "\n# third\n"})
        self.assertEqual(third["type"], "tool_error")
        self.assertEqual(third["error"], "fix_attempts_exhausted")

    def test_failed_patch_limit_ends_loop_as_exhausted(self) -> None:
        target_dir = self._target_dir(self.FLAWED)
        cfg = json.loads(json.dumps(self.bundle["roles"]["purple"]))
        cfg["budget"]["max_fix_attempts"] = 1
        llm = FakeLLM([_resp(_action(
            "propose_patch", root_cause="x", rationale="x",
            new_content=self.FIXED))])
        result = self._loop(llm, "purple", role_cfg=cfg).run(
            task="fix", target_dir=target_dir,
            unit_validator=lambda _: {
                "static_passed": True, "unit_passed": False,
                "unit_checks": [{"name": "gate", "passed": False}],
            })
        self.assertEqual(result["status"], "fix_attempts_exhausted")
        self.assertEqual(result["stop_reason"], "max_fix_attempts")
        self.assertEqual(result["last_error"], "patch_validation_failed")
        self.assertIsNone(result["report"])
        self.assertEqual(result["llm_calls"], 1)
        self.assertEqual(result["actions"][0]["tool"], "propose_patch")


class BundleValidationTest(_Base):
    def test_default_bundle_loads(self) -> None:
        bundle = load_default_bundle()
        self.assertEqual(bundle["bundle_id"], "default")

    def test_invalid_bundle_rejected(self) -> None:
        bad = load_default_bundle()
        bad["roles"]["red"]["tools"] = ["stop", "write_shell"]
        with self.assertRaises(DataError):
            from ai_yori_murasaki.agents.policies import validate_bundle
            validate_bundle(bad)

    def test_tool_catalog_matches_grants(self) -> None:
        from ai_yori_murasaki.agents.tools import build_tool_schemas
        red_grants = ["probe_endpoint", "read_evidence", "stop"]
        catalog = build_tool_schemas(red_grants)
        self.assertEqual(sorted(catalog.keys()),
                         sorted(red_grants))
        full = build_tool_schemas()
        self.assertIn("submit_findings", full)
        self.assertNotIn("submit_findings", catalog)


if __name__ == "__main__":
    unittest.main()
