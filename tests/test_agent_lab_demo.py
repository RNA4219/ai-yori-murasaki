"""Full lab run_demo integration test with a scripted fake LLM and fake lab.

Proves the normal execution path end to end without docker or a real
model: the red agent probes from its own decisions, the blue agent
submits findings from telemetry, the purple agent reads the target and
proposes a patch, the orchestrator applies it and the Oracle verifies
exploit blocked + benign regression. Also proves fail-closed behavior.
"""
from __future__ import annotations

import importlib.util
import json
import tempfile
import time
import unittest
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from ai_yori_murasaki import lab as lab_mod
from ai_yori_murasaki.agents.policies import load_default_bundle
from ai_yori_murasaki.jsonio import digest
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store

_PROJECT = Path(__file__).resolve().parent.parent


def _resp(content) -> dict:
    return {
        "schema_version": "1.0.0", "content": content,
        "effective_model": "fake-model", "prompt_hash": "b" * 64,
        "token_count": 10, "complete": True, "finish_reason": "stop",
        "done": True, "done_reason": "stop", "tool_calls": [],
        "response_metadata": {}, "request_hash": digest({"fake": content}),
    }


def _action(tool: str, **args) -> str:
    return json.dumps(
        {"thought": "判断", "action": {"tool": tool, "args": args}},
        ensure_ascii=False)


class FakeLab:
    """Deterministic stand-in for DockerLab implementing the used surface."""

    def __init__(self) -> None:
        self.running = False
        self.fixed = False
        self.rows: list[dict] = []
        self.cleanup_errors: tuple = ()
        self._health_logged = False
        self.probe_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def close(self):
        self.running = False

    def _authorize_of(self, source_dir):
        path = Path(source_dir) / "target.py"
        name = "fake_target_{}".format(uuid.uuid4().hex[:8])
        spec = importlib.util.spec_from_file_location(name, path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    def start_target(self, target_dir):
        module = self._authorize_of(target_dir)
        self.fixed = module.authorize("guest") is False
        self.running = True
        self._health_logged = False

    def wait_healthy(self, timeout=20.0):
        if not self.running:
            return False
        if not self._health_logged:
            self.rows.append({"timestamp": time.time(), "method": "GET",
                              "path": "/health", "status": 200, "role": ""})
            self._health_logged = True
        return True

    def stop_target(self):
        self.running = False

    def telemetry(self):
        return [dict(r) for r in self.rows]

    def _status_for(self, path: str, role: str) -> int:
        base = path.split("?", 1)[0]
        if base == "/health":
            return 200
        if base == "/public":
            return 200
        if base == "/record":
            if self.fixed:
                return 200 if role == "admin" else 403
            return 200
        return 404

    def run_worker(self, source_dir, role, mode, args=()):
        if mode == "probe_path":
            path = args[1]
            self.probe_calls.append(path)
            role_val = parse_qs(urlsplit(path).query).get("role", [""])[0]
            status = self._status_for(path, role_val)
            self.rows.append({"timestamp": time.time(), "method": "GET",
                              "path": path.split("?", 1)[0],
                              "status": status, "role": role_val})
            obs = {"status": status, "body": None, "method": "GET",
                   "path": path, "timestamp": time.time(), "elapsed": 0.01}
            return {"mode": "probe_path", "status": "ok",
                    "checks": [{"name": "probe_path",
                                "expected": "completed",
                                "actual": "completed", "passed": True}],
                    "observations": [obs]}
        if mode == "red":
            observations, checks = [], []
            for case in ("attack-1", "attack-2", "attack-3"):
                status = self._status_for("/record?role=guest", "guest")
                observations.append({
                    "status": status, "body": None, "method": "GET",
                    "path": "/record?role=guest&case={}".format(case),
                    "timestamp": time.time(), "elapsed": 0.01,
                    "case": case})
                checks.append({"name": case, "expected": 200,
                               "actual": status, "passed": status == 200})
            return {"mode": "red", "status": "ok", "checks": checks,
                    "observations": observations}
        if mode == "benign":
            plan = [("health", "/health", 200),
                    ("public", "/public", 200),
                    ("admin_record", "/record?role=admin", 200),
                    ("unknown", "/does-not-exist", 404),
                    ("health_repeat", "/health", 200)]
            checks, observations = [], []
            for name, p, expected in plan:
                role_val = parse_qs(urlsplit(p).query).get("role", [""])[0]
                status = self._status_for(p, role_val)
                observations.append({"name": name, "status": status,
                                     "method": "GET", "path": p,
                                     "timestamp": time.time(),
                                     "elapsed": 0.01})
                checks.append({"name": name, "expected": expected,
                               "actual": status,
                               "passed": status == expected})
            ok = all(c["passed"] for c in checks)
            return {"mode": "benign", "status": "ok" if ok else "fail",
                    "checks": checks, "observations": observations}
        if mode == "unit_authz":
            module = self._authorize_of(source_dir)
            cases = (("admin", True), ("guest", False), ("", False))
            checks = []
            for r, expected in cases:
                actual = bool(module.authorize(r))
                checks.append({"name": "authorize[{}]".format(r or "empty"),
                               "expected": expected, "actual": actual,
                               "passed": actual == expected})
            ok = all(c["passed"] for c in checks)
            return {"mode": "unit_authz", "status": "ok" if ok else "fail",
                    "checks": checks,
                    "observations": [{"scenario_check": "post_fix"}]}
        if mode == "unit":
            module = self._authorize_of(source_dir)
            require_admin = bool(getattr(module, "REQUIRE_ADMIN", False))
            cases = ((("admin", True), ("guest", False), ("", False))
                     if require_admin
                     else (("admin", True), ("guest", True), ("", True)))
            checks = []
            for r, expected in cases:
                actual = bool(module.authorize(r))
                checks.append({"name": "authorize[{}]".format(r or "empty"),
                               "expected": expected, "actual": actual,
                               "passed": actual == expected})
            return {"mode": "unit",
                    "status": "ok" if all(c["passed"] for c in checks)
                    else "fail", "checks": checks,
                    "observations": [{"require_admin": require_admin}]}
        if mode == "probe":
            status = self._status_for("/health", "")
            return {"mode": "probe",
                    "status": "ok" if status == 200 else "fail",
                    "checks": [{"name": "health", "expected": 200,
                                "actual": status,
                                "passed": status == 200}],
                    "observations": [{"status": status, "body": None,
                                      "method": "GET", "path": "/health",
                                      "timestamp": time.time(),
                                      "elapsed": 0.01}]}
        raise ValueError("unexpected mode: {}".format(mode))


def _parse_json_block(text: str, marker: str = "["):
    start = text.index(marker)
    end = text.rindex("]")
    return json.loads(text[start:end + 1])


class ScriptedLLM:
    """Role-aware scripted LLM implementing the honest agent behavior."""

    def __init__(self, scenario_dir: Path) -> None:
        self.scenario_dir = scenario_dir
        self.state: dict[str, int] = {}
        self.calls: list[dict] = []

    def chat(self, *, url, role, campaign, run, model, messages,
             temperature=0.0, seed=None, token_cap=None, native=True):
        self.calls.append({"role": role, "run": run})
        step = self.state.get(role, 0)
        self.state[role] = step + 1
        task = messages[1]["content"]
        if role == "red":
            if step == 0:
                return _resp(_action("probe_endpoint",
                                     path="/record?role=guest"))
            last = json.loads(messages[-1]["content"])
            ref = last["result"]["ref"]
            return _resp(_action("stop", reason="未許可アクセスを確認した",
                                 report={"unauthorized_access": True,
                                         "evidence_refs": [ref]}))
        if role == "blue":
            rows = _parse_json_block(task)
            suspicious = [r for r in rows
                          if r.get("path") == "/record"
                          and r.get("status") == 200
                          and r.get("role") != "admin"]
            if step == 0:
                findings = [
                    {"category": "authz", "severity": "high",
                     "title": "role={}による/recordへの未許可アクセス".format(
                         r.get("role") or "空"),
                     "path": "/record", "method": "GET",
                     "role": r.get("role"),
                     "evidence_ref": r["ref"],
                     "rationale": "保護対象の/recordに非管理roleでアクセスできている"}
                    for r in suspicious]
                return _resp(_action(
                    "submit_findings", findings=findings))
            return _resp(_action(
                "stop", reason="検知を報告した",
                report={"findings_submitted": len(suspicious)}))
        if role == "purple":
            if step == 0:
                return _resp(_action("read_target_file", path="target.py",
                                     start_line=30, end_line=60))
            if step == 1:
                original = (self.scenario_dir / "target.py").read_text(
                    encoding="utf-8")
                patched = original.replace(
                    "    if not REQUIRE_ADMIN:\n"
                    "        return True\n"
                    "    return role == \"admin\"",
                    "    return role == \"admin\"")
                if patched == original:
                    patched = original.replace(
                        "    return role != \"administrator\"",
                        "    return role == \"admin\"")
                if patched == original:
                    patched = original.replace(
                        "AUTHORIZED_ROLES = (\"admin\", \"guest\")",
                        "AUTHORIZED_ROLES = (\"admin\",)")
                if patched == original:
                    patched = original.replace(
                        "    return True\n",
                        "    return role == \"admin\"\n", 1)
                return _resp(_action(
                    "propose_patch",
                    root_cause="authorize関数が非管理roleも許可している",
                    rationale="authorizeをadminのみ許可する比較へ修正する",
                    new_content=patched))
            last = json.loads(messages[-1]["content"])
            return _resp(_action(
                "stop", reason="修正案を提出した",
                report={"proposal_ref": last["result"]["proposal_ref"]}))
        raise AssertionError("unexpected role: {}".format(role))


class RunDemoTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.staging = root / "staging"
        self.staging.mkdir()
        self.ws = root / "ws"
        self.tr = root / "tr"
        self.ws.mkdir()
        self.tr.mkdir()
        self.store = Store(self.ws, self.tr)
        self.rt = Runtime(self.store)

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _run(self, llm, scenario="authz-flag-false", **kw):
        fake = FakeLab()
        result = lab_mod.run_demo(
            self.rt, self.staging, seed=7, llm_client=llm,
            scenario_id=scenario, lab_factory=lambda jobdir: fake,
            **kw)
        return result, fake

    def test_agent_demo_end_to_end(self) -> None:
        scenario_dir = (_PROJECT / "ai_yori_murasaki" / "scenarios"
                        / "authz-flag-false")
        llm = ScriptedLLM(scenario_dir)
        result, fake = self._run(llm)
        self.assertEqual(result["status"], "review_pending")
        # Red agent actually probed with its chosen path/args
        self.assertEqual(fake.probe_calls, ["/record?role=guest"])
        # Final verdict: exploit blocked and benign regression passed
        verdict = result["final_verdict"]
        self.assertTrue(verdict["remediation_success"])
        self.assertTrue(verdict["benign_regression_passed"])
        self.assertEqual(result["replay_statuses"], [403, 403, 403])
        self.assertTrue(all(c["passed"] for c in result["benign_checks"]))
        # Blue finding comes from the LLM (source attribution)
        findings = [self.store.get_object(r)
                    for r in self.rt.list_records(result["campaign_id"],
                                                  "finding")]
        self.assertEqual(len(findings), 1)
        self.assertEqual(findings[0]["source"], "llm")
        self.assertIn("role=guest", findings[0]["title"])
        # The applied patch is the model's proposed content, not a
        # fixed replacement baked into the product
        proposal = self.store.get_object(result["proposal_ref"])
        self.assertIn('return role == "admin"', proposal["after"])
        self.assertNotIn("REQUIRE_ADMIN: bool = True", proposal["after"])
        # Agent loops completed with recorded turns
        for role in ("red", "blue", "purple"):
            self.assertEqual(result["agent"][role]["status"], "completed")
            self.assertGreaterEqual(result["agent"][role]["llm_calls"], 2)
        # Metrics measured (not HOLD)
        self.assertEqual(result["metrics"]["status"], "measured")
        # Evidence chain: agent turns recorded with llm attribution
        events = []
        for run in self.rt.list_runs(result["campaign_id"]):
            events.extend(self.store.read_events(run["run_id"]))
        turns = [e for e in events
                 if isinstance(e.get("extensions"), dict)
                 and e["extensions"].get("record_type") == "agent_turn"]
        self.assertGreaterEqual(len(turns), 6)
        self.assertTrue(all(e["source"] == "llm" for e in turns))
        attack = [e for e in events if e["event_type"] == "attack_executed"]
        self.assertEqual(len(attack), 1)
        prioritized = [e for e in events
                       if e["event_type"] == "prioritized"]
        self.assertEqual(len(prioritized), 1)
        patched_events = [e for e in events
                          if e["event_type"] in ("remediation_drafted",
                                                 "patch_drafted")]
        self.assertEqual(len(patched_events), 2)

    def test_metrics_hold_when_observation_reference_is_missing(self) -> None:
        from ai_yori_murasaki import lab_metrics
        scenario = _PROJECT / "ai_yori_murasaki" / "scenarios" / "authz-flag-false"
        result, _ = self._run(ScriptedLLM(scenario))
        measurement = self.store.get_object(result["measurement_ref"])
        observations = self.store.get_object(measurement["red_ref"])
        del observations["observations"][-1]["ref"]
        measurement["red_ref"] = self.store.put_object(observations)
        measured = lab_metrics.measure(self.rt, result["campaign_id"], measurement)
        self.assertEqual(measured["status"], "HOLD")
        self.assertEqual(measured["reason_code"], "evidence_missing")

    def test_agent_demo_on_unseen_scenario_shape(self) -> None:
        scenario_dir = (_PROJECT / "ai_yori_murasaki" / "scenarios"
                        / "authz-role-mismatch")
        llm = ScriptedLLM(scenario_dir)
        result, fake = self._run(llm, scenario="authz-role-mismatch")
        self.assertEqual(result["status"], "review_pending")
        self.assertTrue(result["final_verdict"]["remediation_success"])
        proposal = self.store.get_object(result["proposal_ref"])
        self.assertIn('return role == "admin"', proposal["after"])
        self.assertNotIn("!=", proposal["after"].split("def authorize")[1]
                         .split("\n")[1])

    def test_llm_failure_fails_closed(self) -> None:
        scenario_dir = (_PROJECT / "ai_yori_murasaki" / "scenarios"
                        / "authz-flag-false")

        class BrokenLLM(ScriptedLLM):
            def chat(self, **kwargs):
                return _resp("")

        result, _ = self._run(BrokenLLM(scenario_dir), failure_mode="record")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "red")
        self.assertEqual(result["failure_attribution"], "candidate")
        campaign = self.rt.get_campaign(result["campaign_id"])
        self.assertEqual(campaign["status"], "incomplete")

    def test_blue_miss_fails_closed(self) -> None:
        scenario_dir = (_PROJECT / "ai_yori_murasaki" / "scenarios"
                        / "authz-flag-false")

        class MissingBlueLLM(ScriptedLLM):
            def chat(self, **kwargs):
                role = kwargs["role"]
                if role == "blue":
                    step = self.state.get(role, 0)
                    self.state[role] = step + 1
                    return _resp(_action(
                        "stop", reason="不審なアクセスはない",
                        report={"findings_submitted": 0}))
                return super().chat(**kwargs)

        with self.assertRaises(lab_mod._LabFailure) as ctx:
            self._run(MissingBlueLLM(scenario_dir))
        self.assertEqual(ctx.exception.phase, "blue")
        self.assertEqual(ctx.exception.attribution, "candidate")
        campaigns = self.rt.list_campaigns()
        self.assertEqual(campaigns[-1]["status"], "incomplete")


if __name__ == "__main__":
    unittest.main()
