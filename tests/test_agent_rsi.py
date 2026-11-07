"""Agent RSI tests: freeze/train/compare/ready/review/apply with fake
executors (deterministic, no docker, no real model)."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from ai_yori_murasaki import rsi_agent
from ai_yori_murasaki.agents import load_default_bundle
from ai_yori_murasaki.jsonio import DataError, canonical
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
