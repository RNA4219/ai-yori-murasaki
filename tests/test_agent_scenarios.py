"""Scenario fixtures and lab worker mode tests (agent-driven lab)."""
from __future__ import annotations

import ast
import importlib.util
import sys
import unittest
from pathlib import Path

from ai_yori_murasaki.agents.scenarios import (list_scenarios, load_scenario,
                                               splits)
from ai_yori_murasaki.jsonio import DataError

_PROJECT = Path(__file__).resolve().parent.parent
_ASSET = _PROJECT / "ai_yori_murasaki" / "lab_assets" / "worker.py"

sys.path.insert(0, str(_PROJECT))


def _load_worker():
    spec = importlib.util.spec_from_file_location("aym_test_worker_asset",
                                                  _ASSET)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _load_target(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ScenarioCatalogTest(unittest.TestCase):
    def test_catalog_has_four_scenarios_with_splits(self) -> None:
        catalog = list_scenarios()
        ids = {s["scenario_id"] for s in catalog}
        self.assertEqual(ids, {"authz-flag-false", "authz-allow-missing",
                               "authz-role-mismatch", "authz-default-allow"})
        grouped = splits()
        self.assertEqual(grouped["train"],
                         ["authz-allow-missing", "authz-flag-false"])
        self.assertEqual(grouped["validation"], ["authz-role-mismatch"])
        self.assertEqual(grouped["holdout"], ["authz-default-allow"])
        for entry in catalog:
            self.assertTrue(entry["surface"].startswith("GET /health"))
            self.assertEqual(entry["flaw_class"], "authz")

    def test_load_scenario_returns_target_path(self) -> None:
        meta = load_scenario("authz-role-mismatch")
        self.assertEqual(meta["split"], "validation")
        self.assertTrue(meta["target_path"].is_file())
        self.assertEqual(meta["target_path"].name, "target.py")

    def test_load_scenario_rejects_unknown(self) -> None:
        with self.assertRaises(DataError):
            load_scenario("no-such-scenario")
        with self.assertRaises(DataError):
            load_scenario("../evil")


class ScenarioTargetTest(unittest.TestCase):
    def _targets(self):
        root = _PROJECT / "ai_yori_murasaki" / "scenarios"
        for name in sorted(splits()["train"] + splits()["validation"]
                          + splits()["holdout"]):
            path = root / name / "target.py"
            module = _load_target(path, "aym_target_" + name)
            yield name, path, module

    def test_all_targets_parse_and_have_flawed_pre_fix_behavior(self) -> None:
        base_text = (_PROJECT / "ai_yori_murasaki" / "lab_assets"
                     / "target.py").read_text(encoding="utf-8")
        for name, path, module in self._targets():
            ast.parse(path.read_text(encoding="utf-8"), str(path))
            self.assertTrue(callable(module.authorize), name)
            # Pre-fix: guest may read the protected record (the flaw).
            self.assertIs(module.authorize("guest"), True, name)
            # Admin must keep normal access.
            self.assertIs(module.authorize("admin"), True, name)
            # Same documented HTTP surface.
            self.assertEqual(module.PROTECTED_RECORD,
                             {"record": "lab-protected"}, name)
            self.assertEqual(module.PUBLIC_RECORD,
                             {"public": "lab-public"}, name)
            self.assertEqual(module.PORT, 8080, name)
        # The train scenario is the historical fixture (unchanged copy);
        # the other scenarios must differ from it structurally.
        texts = {name: path.read_text(encoding="utf-8")
                for name, path, _ in self._targets()}
        self.assertEqual(texts["authz-flag-false"], base_text)
        for name, text in texts.items():
            if name != "authz-flag-false":
                self.assertNotEqual(text, base_text, name)

    def test_role_mismatch_allows_empty_role(self) -> None:
        module = _load_target(
            _PROJECT / "ai_yori_murasaki" / "scenarios"
            / "authz-role-mismatch" / "target.py",
            "aym_target_mismatch")
        self.assertIs(module.authorize(""), True)
        self.assertIs(module.authorize("administrator"), False)


class WorkerModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.worker = _load_worker()

    def test_probe_path_validation(self) -> None:
        validate = self.worker._validate_probe_path
        self.assertEqual(validate("/record?role=guest"),
                         "/record?role=guest")
        for bad in ("record", "/a b", "/.." + "x" * 100, "/" + "a" * 512,
                    "/re\ncord", "/record\r", "/record\x00"):
            with self.assertRaises(ValueError, msg=repr(bad)):
                validate(bad)

    def test_probe_path_mode_shape(self) -> None:
        result = self.worker.mode_probe_path("aym-tgt-x", "/health")
        self.assertEqual(result["mode"], "probe_path")
        self.assertEqual(result["status"], "ok")
        obs = result["observations"][0]
        self.assertEqual(obs["path"], "/health")
        self.assertIn("status", obs)

    def test_unit_authz_passes_for_fixed_target(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.py"
            target.write_text(
                "def authorize(role):\n    return role == 'admin'\n",
                encoding="utf-8")
            result = self.worker.mode_unit_authz(str(tmp))
            self.assertEqual(result["status"], "ok")
            self.assertTrue(all(c["passed"] for c in result["checks"]))

    def test_unit_authz_fails_for_flawed_target(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "target.py"
            target.write_text("def authorize(role):\n    return True\n",
                             encoding="utf-8")
            result = self.worker.mode_unit_authz(str(tmp))
            self.assertEqual(result["status"], "fail")
            self.assertFalse(all(c["passed"] for c in result["checks"]))

    def test_unit_authz_missing_target(self) -> None:
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            result = self.worker.mode_unit_authz(str(tmp))
            self.assertEqual(result["status"], "fail")


if __name__ == "__main__":
    unittest.main()
