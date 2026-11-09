"""Agent RSI tests: freeze/train/compare/ready/review/apply with fake
executors (deterministic, no docker, no real model)."""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from pathlib import Path

from ai_yori_murasaki import rsi_agent
from ai_yori_murasaki.agents import load_default_bundle
from ai_yori_murasaki.jsonio import DataError, canonical, digest
from ai_yori_murasaki.rsi_agent import (AgentRSI, classify_failure,
                                        outcome_from_demo)
from ai_yori_murasaki.storage import Store

_PINNED = {"status": "pinned:local-digest-abc", "note": "local"}
_UNPINNED = {"status": "unpinned:cloud-alias", "note": "cloud"}

_TRAIN = ["authz-flag-false", "authz-allow-missing"]
_VALIDATION = ["authz-role-mismatch"]
_HOLDOUT = ["authz-default-allow"]


def _config(pinning=_UNPINNED):
    return {
        "objective": "検知と修復の成功率を改善する",
        "seed_pairs": [11],
        "order_policy": "alternating",
        "budget": {"max_pairs": 8, "max_calls": 1000},
        "thresholds": {"success_gain_min": 0.0,
                       "max_safety_violations": 0,
                       "holdout_min_delta": 0.0},
        "stop_conditions": {"max_iterations": 2},
        "scenario_splits": {"train": list(_TRAIN),
                            "validation": list(_VALIDATION),
                            "holdout": list(_HOLDOUT)},
        "model_pinning": pinning,
    }


def _success(cid="c1", calls=6):
    return {"status": "success", "phase": None, "reason": None,
            "failure_attribution": None, "campaign_id": cid,
            "llm_calls": calls, "remediation_success": True,
            "benign_passed": True}


def _failed(phase, reason, attribution="candidate", cid="c1", calls=3):
    return {"status": "failed", "phase": phase, "reason": reason,
            "failure_attribution": attribution, "campaign_id": cid,
            "llm_calls": calls, "remediation_success": False,
            "benign_passed": None,
            "completion": "incomplete" if attribution == "external"
            else "completed"}


class ScriptedExecutor:
    """Outcome keyed by (scenario_id, side label).

    The side label is derived from the bundle version: a bundle whose
    version differs from the frozen baseline version is the candidate.
    """

    def __init__(self, script, baseline_version="1.0.0"):
        self.script = dict(script)
        self.baseline_version = baseline_version
        self.calls = []

    def __call__(self, scenario_id, bundle, seed):
        version = bundle.get("version")
        label = "baseline" if version == self.baseline_version \
            else "candidate"
        self.calls.append((scenario_id, label, seed))
        action = self.script.get((scenario_id, label))
        if action is None:
            action = self.script.get(("*", label))
        if action is None:
            raise AssertionError(
                "no script for {} {}".format(scenario_id, label))
        if action == "success":
            return _success(calls=6)
        if action == "external":
            return _failed("unknown", "docker unavailable",
                           attribution="external")
        phase, reason = action
        return _failed(phase, reason)


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.ws = root / "ws"
        self.tr = root / "tr"
        self.ws.mkdir()
        self.tr.mkdir()
        self.artifacts = root / "artifacts"
        self.artifacts.mkdir()
        self.store = Store(self.ws, self.tr)
        self.rsi = AgentRSI(self.store)
        self.baseline = load_default_bundle()

    def tearDown(self) -> None:
        self.tmp.cleanup()

    def _freeze(self, config=None, manifest=None):
        cfg = config if config is not None else _config()
        man = manifest or {"manifest_id": "eval:ag-test-01"}
        return self.rsi.freeze("c-rsi", cfg, man, self.baseline,
                               self.artifacts)

    def _candidate_of(self, freeze_id):
        state = self.rsi._load_state()
        for cid, cand in state["candidates"].items():
            if cand["freeze_id"] == freeze_id:
                return cand
        raise AssertionError("no candidate for freeze")

    def _train_with_one_failure(self, freeze_id):
        """Train where the baseline misses detection on one train scenario,
        so the derived candidate differs from the baseline."""
        script = {(sid, "baseline"): "success" for sid in _TRAIN}
        script[(_TRAIN[0], "baseline")] = ("blue", "blind detection fail")
        return self.rsi.train(freeze_id, ScriptedExecutor(script))


class FreezeTest(_Base):
    def test_freeze_and_load_roundtrip(self) -> None:
        record = self._freeze()
        loaded = self.rsi.load_freeze(record["freeze_id"])
        self.assertEqual(loaded["freeze_id"], record["freeze_id"])
        self.assertEqual(loaded["baseline_digest"],
                         record["baseline_digest"])

    def test_freeze_is_idempotent(self) -> None:
        first = self._freeze()
        second = self._freeze()
        self.assertEqual(first["freeze_id"], second["freeze_id"])

    def test_split_must_match_catalog(self) -> None:
        bad = _config()
        bad["scenario_splits"] = {
            "train": ["authz-role-mismatch"],
            "validation": ["authz-flag-false"],
            "holdout": ["authz-default-allow"]}
        with self.assertRaises(DataError):
            self._freeze(config=bad)

    def test_unknown_scenario_rejected(self) -> None:
        bad = _config()
        bad["scenario_splits"] = {
            "train": ["no-such"], "validation": ["authz-role-mismatch"],
            "holdout": ["authz-default-allow"]}
        with self.assertRaises(DataError):
            self._freeze(config=bad)

    def test_missing_validation_rejected(self) -> None:
        bad = _config()
        bad["scenario_splits"] = {
            "train": ["authz-flag-false"], "validation": [],
            "holdout": ["authz-default-allow"]}
        with self.assertRaises(DataError):
            self._freeze(config=bad)

    def test_runner_change_breaks_freeze(self) -> None:
        record = self._freeze()
        original = rsi_agent.agent_runner_snapshot

        def changed():
            return canonical({"format": "aym-agent-rsi-runner-bundle-v1",
                              "files": {"lab.py": "0" * 64}})
        rsi_agent.agent_runner_snapshot = changed
        try:
            with self.assertRaises(DataError):
                self.rsi.load_freeze(record["freeze_id"])
        finally:
            rsi_agent.agent_runner_snapshot = original


class TrainTest(_Base):
    def test_train_derives_candidate_from_failures(self) -> None:
        record = self._freeze()
        result = self._train_with_one_failure(record["freeze_id"])
        self.assertTrue(result["changed"])
        self.assertIn("blue_missed_detection", result["failure_classes"])
        self.assertIn("blue_missed_detection", result["rules_applied"])
        candidate = self.store.get_object(result["candidate_bundle_ref"])
        self.assertIn("必ず検知として submit_finding",
                      candidate["roles"]["blue"]["system_prompt"])
        self.assertNotIn("必ず検知として submit_finding",
                         self.baseline["roles"]["blue"]["system_prompt"])

    def test_train_all_success_keeps_baseline(self) -> None:
        record = self._freeze()
        script = {(sid, "baseline"): "success" for sid in _TRAIN}
        result = self.rsi.train(record["freeze_id"],
                                ScriptedExecutor(script))
        self.assertFalse(result["changed"])
        self.assertEqual(result["rules_applied"], [])
        candidate = self.store.get_object(result["candidate_bundle_ref"])
        self.assertEqual(candidate, self.baseline)

    def test_train_external_failure_records_class_only(self) -> None:
        record = self._freeze()
        script = {(sid, "baseline"): "external" for sid in _TRAIN}
        result = self.rsi.train(record["freeze_id"],
                                ScriptedExecutor(script))
        self.assertIn("external", result["failure_classes"])
        self.assertFalse(result["changed"])

    def test_budget_failure_bumps_budgets(self) -> None:
        record = self._freeze()
        script = {(sid, "baseline"): "success" for sid in _TRAIN}
        script[(_TRAIN[0], "baseline")] = ("purple", "budget_exhausted")
        result = self.rsi.train(record["freeze_id"],
                                ScriptedExecutor(script))
        self.assertIn("budget_bump", result["rules_applied"])
        candidate = self.store.get_object(result["candidate_bundle_ref"])
        self.assertEqual(candidate["roles"]["red"]["budget"]["max_turns"],
                         self.baseline["roles"]["red"]["budget"]
                         ["max_turns"] + 2)

    def test_classify_failure(self) -> None:
        self.assertIsNone(classify_failure(_success()))
        self.assertEqual(
            classify_failure(_failed("blue", "blue agent loop status: x")),
            "blue_missed_detection")
        self.assertEqual(
            classify_failure(_failed("red", "red agent did not confirm")),
            "red_not_reproduced")
        self.assertEqual(
            classify_failure(_failed("unknown", "docker unavailable",
                                    attribution="external")),
            "external")


class CompareTest(_Base):
    def _compare(self, script, config=None):
        record = self._freeze(config)
        self._train_with_one_failure(record["freeze_id"])
        candidate = self._candidate_of(record["freeze_id"])
        return self.rsi.compare(record["freeze_id"],
                                candidate["candidate_id"],
                                ScriptedExecutor(script)), record

    def test_unpinned_gain_is_exploratory_not_ready(self) -> None:
        script = {
            (sid, "baseline"): ("blue", "miss")
            for sid in _VALIDATION + _HOLDOUT}
        script.update({(sid, "candidate"): "success"
                       for sid in _VALIDATION + _HOLDOUT})
        result, record = self._compare(script)
        summary = result["summary"]
        self.assertGreater(summary["gain"], 0.0)
        self.assertTrue(summary["exploration_grade"])
        self.assertFalse(summary["ready"])
        ready = self.rsi.ready(result["candidate_id"])
        self.assertEqual(ready["status"], "exploratory-unpinned-model")

    def test_pinned_gain_is_promotion_ready(self) -> None:
        script = {
            (sid, "baseline"): ("blue", "miss")
            for sid in _VALIDATION + _HOLDOUT}
        script.update({(sid, "candidate"): "success"
                       for sid in _VALIDATION + _HOLDOUT})
        result, record = self._compare(script,
                                       config=_config(pinning=_PINNED))
        summary = result["summary"]
        self.assertTrue(summary["model_pinned"])
        self.assertTrue(summary["ready"])
        ready = self.rsi.ready(result["candidate_id"])
        self.assertEqual(ready["status"], "promotion-ready")

    def test_holdout_regression_blocks_ready(self) -> None:
        script = {(sid, "baseline"): ("blue", "miss") for sid in _VALIDATION}
        script.update({(sid, "candidate"): "success" for sid in _VALIDATION})
        script.update({(sid, "baseline"): "success" for sid in _HOLDOUT})
        script.update({(sid, "candidate"): ("blue", "miss")
                       for sid in _HOLDOUT})
        result, record = self._compare(script,
                                       config=_config(pinning=_PINNED))
        summary = result["summary"]
        self.assertFalse(summary["ready"])
        self.assertEqual(summary["holdout_candidate"], 0)

    def test_external_failure_excludes_pair(self) -> None:
        script = {(sid, "baseline"): "success"
                  for sid in _VALIDATION + _HOLDOUT}
        script.update({(sid, "candidate"): "success" for sid in _VALIDATION})
        script[(_HOLDOUT[0], "candidate")] = "external"
        result, record = self._compare(script,
                                       config=_config(pinning=_PINNED))
        summary = result["summary"]
        self.assertEqual(summary["invalid_pairs"], 1)
        self.assertFalse(summary["complete"])
        self.assertFalse(summary["ready"])

    def test_candidate_failure_is_loss_not_excluded(self) -> None:
        script = {(sid, "baseline"): "success"
                  for sid in _VALIDATION + _HOLDOUT}
        script.update({(sid, "candidate"): "success"
                       for sid in _VALIDATION + _HOLDOUT})
        script[(_VALIDATION[0], "candidate")] = ("blue", "miss")
        result, record = self._compare(script)
        summary = result["summary"]
        self.assertEqual(summary["invalid_pairs"], 0)
        self.assertGreaterEqual(summary["losses"], 1)
        self.assertFalse(summary["ready"])

    def test_budget_cap_enforced(self) -> None:
        config = _config(pinning=_PINNED)
        config["budget"]["max_pairs"] = 1
        record = self._freeze(config)
        self._train_with_one_failure(record["freeze_id"])
        candidate = self._candidate_of(record["freeze_id"])
        with self.assertRaises(DataError):
            self.rsi.compare(record["freeze_id"], candidate["candidate_id"],
                             ScriptedExecutor({}))

    def test_double_compare_rejected(self) -> None:
        script = {(sid, "baseline"): "success"
                  for sid in _VALIDATION + _HOLDOUT}
        script.update({(sid, "candidate"): "success"
                       for sid in _VALIDATION + _HOLDOUT})
        result, record = self._compare(script)
        with self.assertRaises(DataError):
            self.rsi.compare(record["freeze_id"], result["candidate_id"],
                             ScriptedExecutor({}))


class ApplyTest(_Base):
    def _ready_candidate(self):
        record = self._freeze(_config(pinning=_PINNED))
        self._train_with_one_failure(record["freeze_id"])
        candidate = self._candidate_of(record["freeze_id"])
        script = {(sid, "baseline"): ("blue", "miss")
                  for sid in _VALIDATION + _HOLDOUT}
        script.update({(sid, "candidate"): "success"
                       for sid in _VALIDATION + _HOLDOUT})
        compared = self.rsi.compare(record["freeze_id"],
                                    candidate["candidate_id"],
                                    ScriptedExecutor(script))
        return candidate, compared, record

    def test_apply_state_machine(self) -> None:
        candidate, compared, record = self._ready_candidate()
        # apply before registry initialization
        review0 = self.rsi.record_review(candidate["candidate_id"],
                                         "reviewer-1", "approved")
        with self.assertRaises(DataError):
            self.rsi.apply(review0["review_id"], record["baseline_digest"],
                           candidate["bundle_digest"], "rollback-x")
        self.rsi.initialize_registry(record["baseline_ref"])
        # apply without review
        with self.assertRaises(DataError):
            self.rsi.apply("agr-nothing", record["baseline_digest"],
                           candidate["bundle_digest"], "rollback-x")
        # rejected review
        review = self.rsi.record_review(candidate["candidate_id"],
                                        "reviewer-1", "rejected")
        with self.assertRaises(DataError):
            self.rsi.apply(review["review_id"], record["baseline_digest"],
                           candidate["bundle_digest"], "rollback-x")

        # approved review but wrong expected refs
        review = self.rsi.record_review(candidate["candidate_id"],
                                        "reviewer-1", "approved")
        with self.assertRaises(DataError):
            self.rsi.apply(review["review_id"], "0" * 64,
                           candidate["bundle_digest"], "rollback-x")
        # correct apply
        applied = self.rsi.apply(review["review_id"],
                                 record["baseline_digest"],
                                 candidate["bundle_digest"], "rollback-x")
        self.assertTrue(applied["applied"])
        self.assertTrue((self.ws / "agent_rsi" / "registry.json").exists())
        candidate = self.rsi.get_candidate(candidate["candidate_id"])
        self.assertTrue(candidate["applied"])
        # double apply rejected
        with self.assertRaises(DataError):
            self.rsi.apply(review["review_id"], record["baseline_digest"],
                           candidate["bundle_digest"], "rollback-x")

    def test_normal_cli_uses_applied_bundle(self) -> None:
        from ai_yori_murasaki import cli
        from ai_yori_murasaki.runtime import Runtime
        candidate, _, record = self._ready_candidate()
        self.rsi.initialize_registry(record["baseline_ref"])
        review = self.rsi.record_review(candidate["candidate_id"], "reviewer", "approved")
        self.rsi.apply(review["review_id"], record["baseline_digest"],
                       candidate["bundle_digest"], "restore baseline")
        args = SimpleNamespace(group="lab", action="demo", routes_file=None,
                               policy_bundle_file=None, scenario=None,
                               staging_root=self.artifacts, seed=7)
        with patch("ai_yori_murasaki.lab.run_demo", return_value={}) as run:
            cli._dispatch(args, Runtime(self.store))
        assert digest(run.call_args.kwargs["policy_bundle"]) == candidate["bundle_digest"]

    def _activate(self):
        candidate, compared, record = self._ready_candidate()
        self.rsi.initialize_registry(record["baseline_ref"])
        review = self.rsi.record_review(candidate["candidate_id"], "reviewer", "approved")
        self.rsi.apply(review["review_id"], record["baseline_digest"],
                       candidate["bundle_digest"], "restore baseline")
        return candidate, record, review

    def test_default_explicit_and_rollback_resolution(self):
        bundle, selection = self.rsi.resolve_bundle()
        self.assertEqual(selection["source"], "packaged_default")
        self.assertEqual(selection["sha256"], digest(self.baseline))
        candidate, record, review = self._activate()
        applied, selection = self.rsi.resolve_bundle()
        self.assertEqual(digest(applied), candidate["bundle_digest"])
        self.assertEqual(selection["review_id"], review["review_id"])
        explicit = self.artifacts / "override.json"
        override = load_default_bundle()
        override["version"] = "operator-choice"
        explicit.write_bytes(canonical(override))
        selected, metadata = self.rsi.resolve_bundle(explicit)
        self.assertEqual(digest(selected), digest(override))
        self.assertEqual(metadata["source"], "explicit_file")
        rolled = self.rsi.rollback(candidate["bundle_ref"], "restore")
        self.assertEqual(rolled["current_bundle_ref"], record["baseline_ref"])
        restored, metadata = self.rsi.resolve_bundle()
        self.assertEqual(digest(restored), record["baseline_digest"])
        self.assertEqual(metadata["reason"], "rollback")

    def test_forged_unapproved_registry_is_not_automatically_used(self):
        ref = self.store.put_object(self.baseline)
        path = self.ws / "agent_rsi" / "registry.json"
        self.store.atomic_json(path, {"current_bundle_ref": ref, "history": []})
        with self.assertRaises(DataError):
            self.rsi.resolve_bundle()

    def test_corrupt_applied_bundle_stops_and_rollback_recovers(self):
        candidate, record, _ = self._activate()
        self.store._object_path(candidate["bundle_ref"]).write_bytes(b"{}")
        with self.assertRaises(DataError):
            self.rsi.resolve_bundle()
        self.rsi.rollback(candidate["bundle_ref"], "corrupt candidate")
        self.assertEqual(self.rsi.resolve_bundle()[1]["sha256"], record["baseline_digest"])

    def test_activation_requires_continuous_approved_history(self):
        candidate, record, _ = self._activate()
        original = self.rsi._registry()
        for mutation in ("current", "from_ref", "review", "operation"):
            invalid = json.loads(json.dumps(original))
            if mutation == "current":
                invalid["current_bundle_ref"] = record["baseline_ref"]
            elif mutation == "from_ref":
                invalid["history"][0]["from_ref"] = candidate["bundle_ref"]
            elif mutation == "review":
                invalid["history"][0]["review_id"] = "agr-" + "0" * 24
            else:
                invalid["history"][0]["operation"] = "unapproved"
            self.rsi._commit_registry(invalid)
            with self.assertRaises((DataError, OSError)):
                self.rsi.resolve_bundle()
        self.rsi._commit_registry(original)
        self.assertEqual(self.rsi.resolve_bundle()[1]["sha256"], candidate["bundle_digest"])

    def test_registry_tampering_stops_and_trusted_rollback_restores(self):
        candidate, record, _ = self._activate()
        path = self.ws / "agent_rsi" / "registry.json"
        path.write_text('{"current_bundle_ref":"../../outside"}', encoding="utf-8")
        with self.assertRaises(DataError):
            self.rsi.resolve_bundle()
        self.rsi.rollback(candidate["bundle_ref"], "repair registry")
        self.assertEqual(self.rsi.resolve_bundle()[1]["sha256"], record["baseline_digest"])
        with self.assertRaises(DataError):
            self.rsi.rollback(candidate["bundle_ref"], "stale expected ref")

    def test_interrupted_apply_is_not_success_and_can_be_recovered(self):
        candidate, _, record = self._ready_candidate()
        self.rsi.initialize_registry(record["baseline_ref"])
        review = self.rsi.record_review(candidate["candidate_id"], "reviewer", "approved")
        original = self.store.atomic_json

        def failing_write(path, value):
            if path == self.rsi._activation_path:
                raise OSError("injected activation write failure")
            original(path, value)

        with patch.object(self.store, "atomic_json", side_effect=failing_write):
            with self.assertRaises(OSError):
                self.rsi.apply(review["review_id"], record["baseline_digest"],
                               candidate["bundle_digest"], "restore")
        self.assertFalse(self.rsi.get_candidate(candidate["candidate_id"])["applied"])
        with self.assertRaises(DataError):
            self.rsi.resolve_bundle()
        self.rsi.rollback(record["baseline_ref"], "recover interrupted commit")
        self.assertEqual(self.rsi.resolve_bundle()[1]["sha256"], record["baseline_digest"])

    def test_tampered_approval_is_rejected(self):
        candidate, _, record = self._ready_candidate()
        self.rsi.initialize_registry(record["baseline_ref"])
        rejected = self.rsi.record_review(candidate["candidate_id"], "reviewer", "rejected")
        state = self.rsi._load_state()
        state["reviews"][rejected["review_id"]]["decision"] = "approved"
        self.rsi._save_state(state)
        with self.assertRaises(DataError):
            self.rsi.apply(rejected["review_id"], record["baseline_digest"],
                           candidate["bundle_digest"], "restore")

    def test_stale_baseline_is_rejected(self):
        candidate, _, record = self._ready_candidate()
        other = load_default_bundle()
        other["version"] = "other-baseline"
        self.rsi.initialize_registry(self.store.put_object(other))
        review = self.rsi.record_review(candidate["candidate_id"], "reviewer", "approved")
        with self.assertRaises(DataError):
            self.rsi.apply(review["review_id"], record["baseline_digest"],
                           candidate["bundle_digest"], "restore")
        self.assertEqual(self.rsi.resolve_bundle()[1]["sha256"], digest(other))
        with self.assertRaises(DataError):
            self.rsi.initialize_registry("../outside")

    def test_invalid_explicit_bundle_never_falls_back(self):
        for value in (b"{}", b"broken-json"):
            explicit = self.artifacts / "bad.json"
            explicit.write_bytes(value)
            with self.assertRaises(DataError):
                self.rsi.resolve_bundle(explicit)
        with self.assertRaises(DataError):
            self.rsi.resolve_bundle(Path("relative.json"))
        with self.assertRaises((DataError, OSError)):
            self.rsi.resolve_bundle(self.artifacts / "missing.json")

    def test_symlinked_registry_directory_is_rejected(self):
        destination = Path(self.tmp.name) / "outside"
        destination.mkdir()
        try:
            (self.ws / "agent_rsi").symlink_to(destination, target_is_directory=True)
        except OSError:
            self.skipTest("OS does not allow symlink creation")
        with self.assertRaises(DataError):
            self.rsi.initialize_registry(self.store.put_object(self.baseline))
        self.assertFalse((destination / "registry.json").exists())

    def test_normal_demo_records_the_policy_it_actually_passes_to_agents(self):
        from ai_yori_murasaki import lab
        from ai_yori_murasaki.runtime import Runtime
        from tests.test_agent_lab_demo import FakeLab, ScriptedLLM
        candidate, record, _ = self._activate()
        scenario = Path(lab.__file__).parent / "scenarios" / "authz-flag-false"
        class ObservedLLM(ScriptedLLM):
            def chat(self, **kwargs):
                self.last_system = getattr(self, "last_system", {})
                self.last_system[kwargs["role"]] = kwargs["messages"][0]["content"]
                return super().chat(**kwargs)

        llm = ObservedLLM(scenario)
        rt = Runtime(self.store)
        result = lab.run_demo(rt, self.artifacts, seed=7, llm_client=llm,
                              lab_factory=lambda jobdir: FakeLab())
        selected = result["policy_selection"]
        self.assertEqual(selected["sha256"], candidate["bundle_digest"])
        applied = self.store.get_object(candidate["bundle_ref"])
        for role in ("red", "blue", "purple"):
            self.assertIn(applied["roles"][role]["system_prompt"], llm.last_system[role])
        events = [event for run in rt.list_runs(result["campaign_id"])
                  for event in self.store.read_events(run["run_id"])]
        policy_events = [e for e in events if (e.get("extensions") or {}).get(
            "record_type") == "agent_policy_selection"]
        self.assertEqual(len(policy_events), 1)
        self.assertEqual(policy_events[0]["extensions"]["sha256"], selected["sha256"])
        self.assertEqual(digest(self.store.get_object(selected["bundle_ref"])), selected["sha256"])
        self.rsi.rollback(candidate["bundle_ref"], "restore")
        again = lab.run_demo(rt, self.artifacts, seed=7, llm_client=ScriptedLLM(scenario),
                             lab_factory=lambda jobdir: FakeLab())
        self.assertEqual(again["policy_selection"]["sha256"], record["baseline_digest"])

    def test_cli_lifecycle_with_real_policy_resolution_and_audit(self):
        from ai_yori_murasaki import cli, lab
        from ai_yori_murasaki.runtime import Runtime
        from tests.test_agent_lab_demo import FakeLab, ScriptedLLM
        rt = Runtime(self.store)
        scope = {"targets": [{"kind": "fixture", "identifier": "t1"}], "max_targets": 1}
        auth = {"operator_id": "op1", "granted_at": "2026-01-01T00:00:00Z",
                "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope)}
        cid = rt.create_campaign("INT-TEST", scope, auth, "cli-cycle")["campaign_id"]
        rt.transition(cid, "running")
        for setup_run in rt.list_runs(cid):
            self.store.seal_checkpoint(cid, setup_run["run_id"], "op1")
        freeze_file = self.artifacts / "freeze.json"
        freeze_file.write_bytes(canonical({"config": _config(_PINNED),
            "manifest": {"manifest_id": "fixture-only-no-performance-claim"},
            "artifact_root": str(self.artifacts)}))
        commands = []

        def command(*args, expected=0):
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = cli.main(["--workspace", str(self.ws), "--trusted-root", str(self.tr),
                                 *args])
            result = json.loads(stdout.getvalue())
            self.assertEqual(code, expected, result)
            commands.append({"command": result["command"], "status": result["status"],
                             "exit_code": code})
            return result["data"]

        frozen = command("rsi", "agent-freeze", "--campaign-id", cid,
                         "--freeze-file", str(freeze_file))
        train_script = {(_TRAIN[0], "baseline"): ("blue", "blind detection fail"),
                        (_TRAIN[1], "baseline"): "success"}
        with patch("ai_yori_murasaki.cli_rsi._agent_executor",
                   return_value=ScriptedExecutor(train_script)):
            command("rsi", "agent-train", "--freeze-id", frozen["freeze_id"],
                    "--staging-root", str(self.artifacts))
        candidate = self._candidate_of(frozen["freeze_id"])
        self.assertNotEqual(candidate["bundle_digest"], frozen["baseline_digest"])
        compare_script = {(sid, "baseline"): ("blue", "miss")
                          for sid in _VALIDATION + _HOLDOUT}
        compare_script.update({(sid, "candidate"): "success"
                               for sid in _VALIDATION + _HOLDOUT})
        with patch("ai_yori_murasaki.cli_rsi._agent_executor",
                   return_value=ScriptedExecutor(compare_script)):
            command("rsi", "agent-compare", "--candidate-id", candidate["candidate_id"],
                    "--staging-root", str(self.artifacts))
        self.assertEqual(command("rsi", "agent-ready", "--candidate-id",
                                 candidate["candidate_id"])["status"], "promotion-ready")
        command("rsi", "agent-registry-init", "--campaign-id", cid,
                "--baseline-ref", frozen["baseline_ref"])
        review = command("rsi", "agent-review", "--candidate-id", candidate["candidate_id"],
                         "--reviewer", "fixture-reviewer", "--decision", "approved")
        command("rsi", "agent-apply", "--review-id", review["review_id"],
                "--expected-baseline", frozen["baseline_digest"],
                "--expected-candidate", candidate["bundle_digest"], "--rollback", "restore")
        self.assertEqual(command("rsi", "agent-status")["policy_selection"]["sha256"],
                         candidate["bundle_digest"])
        scenario = Path(lab.__file__).parent / "scenarios" / "authz-flag-false"

        def demo():
            with patch("ai_yori_murasaki.lab.LLMClient", return_value=ScriptedLLM(scenario)), \
                 patch("ai_yori_murasaki.lab.DockerLab", side_effect=lambda jobdir: FakeLab()):
                return command("lab", "demo", "--staging-root", str(self.artifacts))

        applied_run = demo()
        self.assertEqual(applied_run["policy_selection"]["sha256"], candidate["bundle_digest"])
        command("rsi", "agent-rollback", "--campaign-id", cid,
                "--expected-current", candidate["bundle_ref"], "--reason", "restore baseline")
        restored_run = demo()
        self.assertEqual(restored_run["policy_selection"]["sha256"], frozen["baseline_digest"])
        for run in rt.list_runs():
            self.assertEqual(self.store.verify_chain(run["run_id"])["result"], "PASS")
            checkpoint = self.store.verify_checkpoint(run["campaign_id"], run["run_id"])
            self.assertEqual(checkpoint["result"], "PASS", (run, checkpoint))
        print("CLI_LIFECYCLE " + json.dumps({
            "evaluation": "scripted fixture, not measured improvement",
            "commands": commands, "candidate_id": candidate["candidate_id"],
            "review_id": review["review_id"], "applied": applied_run["policy_selection"],
            "restored": restored_run["policy_selection"],
            "audit": "all chains and checkpoints PASS"}, sort_keys=True))

    def test_apply_requires_ready(self) -> None:
        record = self._freeze(_config(pinning=_UNPINNED))
        self._train_with_one_failure(record["freeze_id"])
        candidate = self._candidate_of(record["freeze_id"])
        script = {(sid, "baseline"): ("blue", "miss")
                  for sid in _VALIDATION + _HOLDOUT}
        script.update({(sid, "candidate"): "success"
                       for sid in _VALIDATION + _HOLDOUT})
        self.rsi.compare(record["freeze_id"], candidate["candidate_id"],
                         ScriptedExecutor(script))
        self.rsi.initialize_registry(record["baseline_ref"])
        review = self.rsi.record_review(candidate["candidate_id"],
                                        "reviewer-1", "approved")
        with self.assertRaises(DataError):
            self.rsi.apply(review["review_id"], record["baseline_digest"],
                           candidate["bundle_digest"], "rollback-x")

    def test_ready_requires_comparison(self) -> None:
        with self.assertRaises(DataError):
            self.rsi.ready("agc-nothing")


class OutcomeTest(_Base):
    def test_outcome_mapping(self) -> None:
        ok = outcome_from_demo({
            "status": "review_pending", "campaign_id": "c1",
            "final_verdict": {"remediation_success": True,
                              "benign_regression_passed": True},
            "agent": {"red": {"llm_calls": 3}, "blue": {"llm_calls": 2},
                      "purple": {"llm_calls": 4}}})
        self.assertEqual(ok["status"], "success")
        self.assertEqual(ok["llm_calls"], 9)
        bad = outcome_from_demo({
            "status": "failed", "phase": "purple",
            "reason": "purple agent loop status: budget_exhausted",
            "failure_attribution": "candidate", "campaign_id": "c1",
            "agent": {}})
        self.assertEqual(bad["status"], "failed")
        self.assertEqual(bad["completion"], "completed")


if __name__ == "__main__":
    unittest.main()
