"""Regression tests for the 2026-11-12 agent RSI scenario report.

No Docker or live LLM is needed. The read-only container itself must still
be smoke-tested separately before declaring the lab scenario operational.
"""
from __future__ import annotations

import importlib.util
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from ai_yori_murasaki import lab
from ai_yori_murasaki.agents.policies import load_default_bundle, render_task
from ai_yori_murasaki.agents.scenarios import load_scenario
from ai_yori_murasaki.rsi_agent import outcome_from_demo
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store
from tests.test_agent_lab_demo import (
    FakeLab, ScriptedLLM, _action, _resp,
)

_ROOT = Path(__file__).resolve().parent.parent
_AUTHZ_DIR = (_ROOT / "ai_yori_murasaki" / "scenarios"
             / "authz-flag-false")


class ScenarioSurfaceRegressionTest(unittest.TestCase):
    def test_red_tasks_include_documented_entry_points(self):
        expected = {
            "sqli-inject": ("/api/items",),
            "xss-reflect": ("/greet",),
            "path-traversal-read": ("/files",),
            "dep-cve-outdated": ("/version", "/api/process"),
        }
        red = load_default_bundle()["roles"]["red"]
        for scenario_id, paths in expected.items():
            with self.subTest(scenario=scenario_id):
                scenario = load_scenario(scenario_id)
                task = render_task(red, {"surface": scenario["surface"]})
                self.assertNotIn("see target.py", task)
                for path in paths:
                    self.assertIn(path, task)

    def test_path_traversal_target_starts_without_writing(self):
        source = load_scenario("path-traversal-read")["target_path"]
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.py"
            shutil.copy2(source, target)
            spec = importlib.util.spec_from_file_location(
                "aym_readonly_path_target", target)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            self.assertEqual(module.read_file("readme.txt"),
                             "This is a public readme file.\n")
            self.assertFalse((Path(tmp) / "files").exists())

            class FakeHttpServer:
                def __init__(self, address, handler):
                    self.address = address

                def serve_forever(self):
                    pass

                def server_close(self):
                    pass

            # A startup attempt to create a file would raise. This models
            # /app being bind-mounted read-only without needing Docker.
            with mock.patch.object(module, "ThreadingHTTPServer",
                                   FakeHttpServer):
                with mock.patch.object(module.os, "makedirs",
                                       side_effect=AssertionError("write")):
                    with mock.patch("builtins.open",
                                    side_effect=AssertionError("write")):
                        self.assertEqual(module.main(), 0)
            self.assertFalse((Path(tmp) / "files").exists())


class FailedRunCallAccountingTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.staging = root / "staging"
        ws, tr = root / "ws", root / "tr"
        for path in (self.staging, ws, tr):
            path.mkdir()
        self.runtime = Runtime(Store(ws, tr))

    def tearDown(self):
        self.tmp.cleanup()

    def _run(self, llm, *, lab_class=FakeLab):
        return lab.run_demo(
            self.runtime, self.staging, seed=11,
            llm_client=llm, scenario_id="authz-flag-false",
            policy_bundle=load_default_bundle(),
            failure_mode="record",
            lab_factory=lambda jobdir: lab_class(),
        )

    def test_red_budget_exhaustion_counts_calls(self):
        class AlwaysHealth:
            def __init__(self):
                self.calls = 0

            def chat(self, **kwargs):
                self.calls += 1
                return _resp(_action("probe_endpoint", path="/health"))

        llm = AlwaysHealth()
        result = self._run(llm)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["phase"], "red")
        self.assertEqual(result["agent"]["red"]["status"],
                         "budget_exhausted")
        self.assertGreater(llm.calls, 0)
        self.assertEqual(result["agent"]["red"]["llm_calls"], llm.calls)
        self.assertEqual(outcome_from_demo(result)["llm_calls"], llm.calls)

    def test_blue_failure_keeps_red_and_blue_calls(self):
        class BadBlue(ScriptedLLM):
            def chat(self, **kwargs):
                if kwargs["role"] == "blue":
                    self.calls.append({"role": "blue", "run": kwargs["run"]})
                    return _resp("")
                return super().chat(**kwargs)

        llm = BadBlue(_AUTHZ_DIR)
        result = self._run(llm)
        self.assertEqual(result["phase"], "blue")
        self.assertEqual(set(result["agent"]), {"red", "blue"})
        total = sum(v["llm_calls"] for v in result["agent"].values())
        self.assertEqual(total, len(llm.calls))
        self.assertEqual(outcome_from_demo(result)["llm_calls"], total)

    def test_purple_failure_keeps_all_role_calls(self):
        class BadPurple(ScriptedLLM):
            def chat(self, **kwargs):
                if kwargs["role"] == "purple":
                    self.calls.append({"role": "purple", "run": kwargs["run"]})
                    return _resp("")
                return super().chat(**kwargs)

        llm = BadPurple(_AUTHZ_DIR)
        result = self._run(llm)
        self.assertEqual(result["phase"], "purple")
        self.assertEqual(set(result["agent"]), {"red", "blue", "purple"})
        total = sum(v["llm_calls"] for v in result["agent"].values())
        self.assertEqual(total, len(llm.calls))
        self.assertEqual(outcome_from_demo(result)["llm_calls"], total)

    def test_external_pre_agent_failure_keeps_zero(self):
        class UnhealthyLab(FakeLab):
            def wait_healthy(self, timeout=20.0):
                return False

        result = self._run(ScriptedLLM(_AUTHZ_DIR),
                           lab_class=UnhealthyLab)
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["failure_attribution"], "external")
        self.assertEqual(result["agent"], {})
        self.assertEqual(outcome_from_demo(result)["llm_calls"], 0)


if __name__ == "__main__":
    unittest.main()
