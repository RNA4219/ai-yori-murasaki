"""Agent RSI: improving the policy bundles the product agents run with.

This module is the RSI loop for the product's own agents. The improvement
target is the agent policy bundle (prompts, grants, budgets) that the lab
agents actually use at runtime - not model weights and not the separate
deterministic local-authorization policy of ``rsi_train``/``rsi_compare``.

Loop (mirrors the 9-item RSI policy and the RSI contract):

1. ``freeze``: fix the objective, scenario split assignment (train /
   validation / holdout from the packaged scenario catalog), seed pairs,
   order policy, budgets, thresholds and the model pinning status BEFORE
   any evaluation result is observed. The baseline bundle, evaluation
   manifest, dataset and runner snapshot digests are frozen and pinned
   in the trusted root.
2. ``train``: execute the baseline bundle on the train scenarios, analyze
   the recorded failures deterministically, and derive a candidate bundle
   through fixed policy-editor rules (recorded, idempotent). Completed
   train runs also produce abstract experience cards.
3. ``compare``: run baseline and candidate on the validation and holdout
   scenarios under identical seeds/budgets, pair by pair, and compute the
   adoption decision against the frozen thresholds. Candidate-caused
   failures count as losses; only external (infrastructure) failures may
   exclude a pair and are reported as such.
4. ``ready`` / ``review`` / ``apply``: promotion-ready is separate from
   reflection into the default executor; apply is an explicit operator
   operation with an approved review, expected refs and a rollback path.

Model pinning: promotion requires frozen expected SHA-256 digests and
matching per-run Ollama-native model observations; alias names and
unverifiable OpenAI-compatible endpoints remain exploratory (SPEC-
EVALUATION A-3 / decision D-058), regardless of measured gains.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional
from urllib.parse import quote

from . import evaluation_identity as identity
from .agents.policies import validate_bundle
from .agents.scenarios import list_scenarios
from .jsonio import DataError, canonical, digest, loads, read_json
from .storage import _assert_no_ancestor_reparse

SideExecutor = Callable[[str, dict, int], dict]

_RUNNER_FILES = (
    "agents/__init__.py",
    "agents/core.py",
    "agents/policies.py",
    "agents/scenarios.py",
    "agents/tools.py",
    "agents/policies_default.json",
    "lab.py",
    "lab_metrics.py",
    "worker.py",
    "oracle.py",
    "p2.py",
    "runtime.py",
    "storage.py",
    "contracts.py",
    "jsonio.py",
    "rsi_agent.py",
    "adapters/llm.py",
    "adapters/base.py",
    "adapters/normalizers.py",
    "lab_assets/worker.py",
)

_SPLIT_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_SPLITS = ("train", "validation", "holdout")


def _fail(reason: str, message: str) -> None:
    raise DataError(message, reason)


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128 and \
        value.strip() == value


def _verify_model_pinning(pairs: List[dict], pinning: dict,
                          train_results: List[dict]) -> tuple[bool, str, int]:
    """Verify each observed invocation against immutable, frozen Ollama digests.

    A config label, a requested model, and an OpenAI-compatible response
    model name alone are not evidence of a pinned model.
    """
    status = pinning.get("status", "")
    if status != "pinned" and not status.startswith("pinned:"):
        return False, "unpinned_config", 0
    expected = pinning.get("expected_models")
    if not isinstance(expected, dict) or not expected:
        return False, "expected_models_missing", 0
    runs = [item.get("outcome") for item in train_results
            if isinstance(item, dict)]
    for pair in pairs:
        runs.extend((pair["baseline"], pair["candidate"]))
    seen = 0
    if not train_results or len(runs) != len(train_results) + 2 * len(pairs):
        return False, "train_observations_missing", seen
    for run in runs:
        observations = run.get("model_observations") if isinstance(run, dict) else None
        if not isinstance(observations, list) or not observations:
            return False, "model_observations_missing", seen
        for item in observations:
            seen += 1
            if not isinstance(item, dict):
                return False, "invalid_model_observation", seen
            model = item.get("requested_model")
            if (not isinstance(model, str) or model not in expected
                    or model.endswith(":cloud")
                    or item.get("effective_model") != model
                    or item.get("transport") != "ollama-native"
                    or item.get("source") != "ollama-tags-prepost"
                    or item.get("digest") != expected[model]):
                return False, "model_digest_unverified_or_mismatch", seen
    return True, "verified", seen


def agent_runner_snapshot() -> bytes:
    """Hash the agent execution bundle (code + policies + scenarios)."""
    base = Path(__file__).resolve().parent
    files: Dict[str, str] = {}
    for name in _RUNNER_FILES:
        path = base / name
        try:
            data = path.read_bytes()
        except OSError as error:
            raise DataError("agent runner bundle unreadable",
                            "input_invalid") from error
        files[name] = hashlib.sha256(data).hexdigest()
    scenarios_root = base / "scenarios"
    if scenarios_root.is_dir():
        for path in sorted(scenarios_root.glob("*/*")):
            if not path.is_file():
                continue
            rel = "scenarios/" + path.relative_to(scenarios_root).as_posix()
            files[rel] = hashlib.sha256(path.read_bytes()).hexdigest()
    snapshot = {
        "format": "aym-agent-rsi-runner-bundle-v1",
        "files": files,
    }
    return canonical(snapshot)


# --------------------------------------------------------------------------- #
# failure classification and policy editor
# --------------------------------------------------------------------------- #

_GUIDANCE = {
    "red_not_reproduced": (
        "確認するroleには admin と guest を必ず含め、/record へのアクセス"
        "結果を証拠として報告に含めること。"),
    "blue_missed_detection": (
        "path が /record で status が 200 かつ role が admin 以外の行は、"
        "必ず検知として submit_findings でまとめて報告すること。"),
    "purple_patch_failed": (
        "修正後は authorize が admin のみ True を返し、guest と空roleは"
        " False を返すこと。/health と /public の処理は変更しないこと。"),
}


def classify_failure(outcome: dict) -> Optional[str]:
    """Deterministic failure classification from one recorded outcome."""
    if outcome.get("status") == "success":
        return None
    attribution = outcome.get("failure_attribution")
    if attribution == "external":
        return "external"
    reason = str(outcome.get("reason", ""))
    phase = outcome.get("phase")
    if "llm_failure" in reason or "llm response" in reason:
        return "llm_failure"
    if "budget" in reason or "fix_attempts" in reason:
        return "budget_exhausted"
    if phase == "red":
        return "red_not_reproduced"
    if phase == "blue":
        return "blue_missed_detection"
    if phase == "purple":
        return "purple_patch_failed"
    if phase == "remediation":
        return "purple_patch_failed"
    return "search"


def _apply_guidance(bundle: dict, rules: List[str]) -> dict:
    """Apply the deterministic policy editor rules to a bundle copy."""
    candidate = json.loads(json.dumps(bundle, ensure_ascii=False))
    changed_rules: List[str] = []
    for rule in rules:
        guidance = _GUIDANCE.get(rule)
        if guidance is None:
            continue
        role = {"red_not_reproduced": "red",
                "blue_missed_detection": "blue",
                "purple_patch_failed": "purple"}[rule]
        prompt = candidate["roles"][role]["system_prompt"]
        if guidance not in prompt:
            candidate["roles"][role]["system_prompt"] = (
                prompt.rstrip() + "\n" + guidance)
            changed_rules.append(rule)
    if "budget_exhausted" in rules:
        for role in ("red", "blue", "purple"):
            budget = candidate["roles"][role]["budget"]
            budget["max_turns"] = min(16, budget["max_turns"] + 2)
            budget["max_llm_calls"] = min(24, budget["max_llm_calls"] + 4)
        changed_rules.append("budget_bump")
    if changed_rules:
        candidate["version"] = "{}-candidate".format(candidate["version"])
    return candidate, changed_rules


def outcome_from_demo(result: dict) -> dict:
    """Map one ``lab.run_demo`` result (record mode) to an RSI outcome."""
    if not isinstance(result, dict):
        raise DataError("run result must be object", "input_invalid")
    if result.get("status") == "review_pending":
        verdict = result.get("final_verdict") or {}
        agent = result.get("agent") or {}
        calls = 0
        for role in ("red", "blue", "purple"):
            calls += int((agent.get(role) or {}).get("llm_calls") or 0)
        return {
            "status": "success",
            "phase": None,
            "reason": None,
            "failure_attribution": None,
            "campaign_id": result.get("campaign_id"),
            "llm_calls": calls,
            "remediation_success": verdict.get("remediation_success") is True,
            "benign_passed": verdict.get("benign_regression_passed") is True,
        }
    if result.get("status") == "failed":
        agent = result.get("agent") or {}
        calls = 0
        for role in ("red", "blue", "purple"):
            calls += int((agent.get(role) or {}).get("llm_calls") or 0)
        attribution = result.get("failure_attribution")
        return {
            "status": "failed",
            "phase": result.get("phase"),
            "reason": result.get("reason"),
            "failure_attribution": attribution,
            "campaign_id": result.get("campaign_id"),
            "llm_calls": calls,
            "remediation_success": False,
            "benign_passed": None,
            "completion": "incomplete" if attribution == "external"
            else "completed",
        }
    raise DataError("run result status unknown", "input_invalid")


# --------------------------------------------------------------------------- #
# AgentRSI state machine
# --------------------------------------------------------------------------- #

class AgentRSI:
    """State machine for agent-bundle RSI (separate namespace from RSI)."""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._workspace = Path(store.workspace)
        self._trusted = Path(store.trusted_root)
        self._state_path = self._workspace / "agent_rsi" / "state.json"
        self._registry_path = self._workspace / "agent_rsi" / "registry.json"
        self._activation_path = self._trusted / "agent_rsi" / "activation.json"

    def _safe_path(self, root: Path, path: Path) -> Path:
        if ".." in path.parts:
            _fail("policy_denied", "path traversal denied")
        _assert_no_ancestor_reparse(path)
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            _fail("policy_denied", "agent rsi path escapes root")
        return path

    def _bundle(self, ref: str) -> dict:
        if not isinstance(ref, str) or not re.fullmatch(r"[0-9a-f]{64}", ref):
            _fail("policy_denied", "invalid bundle reference")
        return validate_bundle(self._store.get_object(ref))

    def _registry(self, *, recovery: bool = False) -> Optional[dict]:
        self._safe_path(self._workspace, self._registry_path)
        self._safe_path(self._trusted, self._activation_path)
        if not self._activation_path.exists():
            if self._registry_path.exists():
                _fail("policy_denied", "registry has no trusted activation")
            return None
        registry = read_json(self._activation_path)
        if (not isinstance(registry, dict) or set(registry) != {
                "current_bundle_ref", "baseline_bundle_ref", "history"}
                or not isinstance(registry["history"], list)):
            _fail("policy_denied", "activation invalid")
        for key in ("current_bundle_ref", "baseline_bundle_ref"):
            if not isinstance(registry[key], str) or not re.fullmatch(
                    r"[0-9a-f]{64}", registry[key]):
                _fail("policy_denied", "activation reference invalid")
        current = registry["baseline_bundle_ref"]
        active = []
        for event in registry["history"]:
            if not isinstance(event, dict) or event.get("from_ref") != current:
                _fail("policy_denied", "activation history is not continuous")
            if event.get("operation") == "apply":
                review_id = event.get("review_id")
                if not isinstance(review_id, str) or not re.fullmatch(r"agr-[0-9a-f]{24}", review_id):
                    _fail("not_approved", "activation review invalid")
                path = self._safe_path(self._trusted, self._trusted / "agent_rsi" / "reviews" / (review_id + ".json"))
                pin = read_json(path)
                review = pin.get("review") or {}
                if (review.get("decision") != "approved"
                        or review.get("review_id") != review_id
                        or review.get("candidate_id") != event.get("candidate_id")
                        or pin.get("bundle_ref") != event.get("to_ref")
                        or pin.get("baseline_digest") != current):
                    _fail("not_approved", "activation review mismatch")
                active.append(event)
            elif event.get("operation") == "rollback":
                previous = active.pop()["from_ref"] if active else registry["baseline_bundle_ref"]
                if event.get("to_ref") != previous:
                    _fail("policy_denied", "rollback target not previously approved")
            else:
                _fail("policy_denied", "unknown activation operation")
            current = event.get("to_ref")
        if current != registry["current_bundle_ref"]:
            _fail("policy_denied", "activation current does not match history")
        if not recovery:
            if (not self._registry_path.exists()
                    or read_json(self._registry_path) != registry):
                _fail("policy_denied", "registry/activation mismatch; rollback required")
        return registry

    def _commit_registry(self, registry: dict) -> None:
        # Trusted activation is the commit point. A partial write leaves a
        # mismatch that stops execution and can be recovered by rollback.
        self._safe_path(self._workspace, self._registry_path)
        self._safe_path(self._trusted, self._activation_path)
        self._store.atomic_json(self._registry_path, registry)
        self._store.atomic_json(self._activation_path, registry)

    def resolve_bundle(self, bundle_file: Optional[Path] = None) -> tuple:
        """Explicit operator selection > trusted activation > default.

        Invalid selected state fails closed; no fallback after a failure.
        Evaluation executors pass their frozen bundles explicitly instead.
        """
        from .agents import load_default_bundle
        with self._store.lock():
            if bundle_file is not None:
                path = Path(bundle_file)
                if not path.is_absolute():
                    _fail("input_invalid", "bundle file must be absolute")
                self._safe_path(path.parent, path)
                bundle = validate_bundle(read_json(path))
                source, reason = "explicit_file", "operator_override"
            else:
                registry = self._registry()
                if registry is None:
                    bundle = load_default_bundle()
                    source, reason = "packaged_default", "no_activation"
                else:
                    bundle = self._bundle(registry["current_bundle_ref"])
                    source, reason = "approved_registry", "trusted_activation"
            ref = self._store.put_object(bundle)
            selection = {
                "bundle_id": bundle["bundle_id"], "version": bundle["version"],
                "bundle_ref": ref, "sha256": digest(bundle),
                "source": source, "reason": reason,
            }
            if source == "approved_registry":
                selection["activation_sha256"] = digest(registry)
                if registry["history"]:
                    selection["reason"] = registry["history"][-1]["operation"]
                    selection["review_id"] = registry["history"][-1].get("review_id")
                    selection["candidate_id"] = registry["history"][-1].get("candidate_id")
                else:
                    selection["reason"] = "operator_initialized_baseline"
            return bundle, selection

    # -- state helpers ----------------------------------------------------

    def _load_state(self) -> dict:
        self._safe_path(self._workspace, self._state_path)
        if not self._state_path.exists():
            return {"candidates": {}, "freezes": {}, "reviews": {},
                    "memory": []}
        raw = self._state_path.read_text(encoding="utf-8")
        data = loads(raw.encode("utf-8"))
        if not isinstance(data, dict):
            _fail("state_invalid", "agent rsi state must be object")
        for key in ("candidates", "freezes", "reviews", "memory"):
            if key not in data:
                data[key] = {} if key != "memory" else []
        return data

    def _save_state(self, state: dict) -> None:
        self._safe_path(self._workspace, self._state_path)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._store.atomic_json(self._state_path, state)

    def _pin_path(self, freeze_id: str) -> Path:
        if not isinstance(freeze_id, str) or not re.fullmatch(r"ag-[0-9a-f]{24}", freeze_id):
            _fail("input_invalid", "invalid freeze id")
        return self._safe_path(self._trusted, self._trusted / "agent_rsi" / (freeze_id + ".json"))

    # -- freeze ------------------------------------------------------------

    def _validate_config(self, config: Any) -> dict:
        if not isinstance(config, dict):
            _fail("input_invalid", "config must be object")
        keys = {"objective", "seed_pairs", "order_policy", "budget",
                "thresholds", "stop_conditions", "scenario_splits",
                "model_pinning"}
        if set(config.keys()) != keys:
            _fail("input_invalid", "config keys invalid")
        if not isinstance(config["objective"], str) or not (
                1 <= len(config["objective"]) <= 2000):
            _fail("input_invalid", "objective must be 1..2000 chars")
        seeds = config["seed_pairs"]
        if (not isinstance(seeds, list) or not seeds
                or len(seeds) > 8):
            _fail("input_invalid", "seed_pairs must be 1..8 items")
        for s in seeds:
            if isinstance(s, bool) or not isinstance(s, int) or s < 0:
                _fail("input_invalid", "seed must be non-negative int")
        if config["order_policy"] not in ("alternating", "baseline_first"):
            _fail("input_invalid", "order_policy invalid")
        budget = config["budget"]
        if not isinstance(budget, dict):
            _fail("input_invalid", "budget must be object")
        if set(budget.keys()) != {"max_pairs", "max_calls"}:
            _fail("input_invalid", "budget keys invalid")
        for key in ("max_pairs", "max_calls"):
            v = budget[key]
            if isinstance(v, bool) or not isinstance(v, int) or not (
                    1 <= v <= 10 ** 9):
                _fail("input_invalid", "budget.{} invalid".format(key))
        thresholds = config["thresholds"]
        if not isinstance(thresholds, dict):
            _fail("input_invalid", "thresholds must be object")
        if set(thresholds.keys()) != {"success_gain_min",
                                      "max_safety_violations",
                                      "holdout_min_delta"}:
            _fail("input_invalid", "thresholds keys invalid")
        gain = thresholds["success_gain_min"]
        if isinstance(gain, bool) or not isinstance(gain, (int, float)):
            _fail("input_invalid", "success_gain_min invalid")
        if not (-1.0 <= float(gain) <= 1.0):
            _fail("input_invalid", "success_gain_min out of range")
        msv = thresholds["max_safety_violations"]
        if isinstance(msv, bool) or not isinstance(msv, int) or msv < 0:
            _fail("input_invalid", "max_safety_violations invalid")
        hmd = thresholds["holdout_min_delta"]
        if (isinstance(hmd, bool) or not isinstance(hmd, (int, float))
                or not (-1.0 <= float(hmd) <= 1.0)):
            _fail("input_invalid", "holdout_min_delta invalid")
        stop = config["stop_conditions"]
        if (not isinstance(stop, dict)
                or set(stop.keys()) != {"max_iterations"}
                or isinstance(stop["max_iterations"], bool)
                or not isinstance(stop["max_iterations"], int)
                or not (1 <= stop["max_iterations"] <= 64)):
            _fail("input_invalid", "stop_conditions invalid")
        splits = config["scenario_splits"]
        if not isinstance(splits, dict):
            _fail("input_invalid", "scenario_splits must be object")
        if set(splits.keys()) != set(_SPLITS):
            _fail("input_invalid", "scenario_splits keys invalid")
        catalog = {meta["scenario_id"]: meta for meta in list_scenarios()}
        seen: set = set()
        for split in _SPLITS:
            ids = splits[split]
            if not isinstance(ids, list):
                _fail("input_invalid", "scenario_splits values must be lists")
            for scenario_id in ids:
                if not _SPLIT_RE.match(str(scenario_id)):
                    _fail("input_invalid", "scenario id invalid")
                if scenario_id in seen:
                    _fail("input_invalid",
                          "scenario appears in two splits")
                seen.add(scenario_id)
                if scenario_id not in catalog:
                    _fail("input_invalid", "unknown scenario")
                if catalog[scenario_id]["split"] != split:
                    _fail("policy_denied",
                          "scenario split disagrees with catalog")
        if not splits["train"]:
            _fail("input_invalid", "train split must be non-empty")
        if not splits["validation"] or not splits["holdout"]:
            _fail("input_invalid",
                  "validation and holdout splits must be non-empty")
        pinning = config["model_pinning"]
        if not isinstance(pinning, dict):
            _fail("input_invalid", "model_pinning must be object")
        if (set(pinning.keys()) - {"status", "note", "expected_models"}
                or not {"status", "note"} <= set(pinning.keys())):
            _fail("input_invalid", "model_pinning keys invalid")
        if not isinstance(pinning["status"], str) or not (
                1 <= len(pinning["status"]) <= 200):
            _fail("input_invalid", "model_pinning status invalid")
        if not isinstance(pinning["note"], str) or len(pinning["note"]) > 500:
            _fail("input_invalid", "model_pinning note invalid")
        expected = pinning.get("expected_models", {})
        if (not isinstance(expected, dict) or len(expected) > 16):
            _fail("input_invalid", "expected_models must be a mapping of at most 16 models")
        for model, model_digest in expected.items():
            if (not isinstance(model, str) or not (1 <= len(model) <= 200)
                    or model.strip() != model
                    or not isinstance(model_digest, str)
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", model_digest) is None):
                _fail("input_invalid", "expected_models requires immutable sha256 digests")
        return config

    def freeze(self, campaign_id: str, config: dict, manifest: dict,
               baseline_bundle: dict, artifact_root: Path) -> dict:
        """Freeze the evaluation conditions before any result is seen."""
        if not _is_id(campaign_id):
            _fail("input_invalid", "campaign_id invalid")
        self._validate_config(config)
        validate_bundle(baseline_bundle)
        if not isinstance(manifest, dict):
            _fail("input_invalid", "manifest must be object")
        if not isinstance(artifact_root, Path) or not artifact_root.is_absolute():
            _fail("input_invalid", "artifact_root must be absolute Path")
        identity._assert_no_symlink_ancestors(artifact_root,
                                              artifact_root, "artifact_root")
        freeze_id = "ag-{}".format(digest({
            "campaign_id": campaign_id, "config": config,
            "manifest": manifest, "baseline": baseline_bundle,
        })[:24])
        state = self._load_state()
        if freeze_id in state["freezes"]:
            return state["freezes"][freeze_id]["record"]

        root = artifact_root / "agent-rsi" / freeze_id
        root.mkdir(parents=True, exist_ok=True)
        dataset = {"tasks": [
            {"task_id": sid, "split": split}
            for split in _SPLITS for sid in config["scenario_splits"][split]]}
        dataset_bytes = canonical(dataset)
        runner_bytes = agent_runner_snapshot()
        baseline_bytes = canonical(baseline_bundle)
        manifest_bytes = canonical(manifest)
        for rel, data in (("dataset.json", dataset_bytes),
                          ("runner-bundle.json", runner_bytes),
                          ("baseline-policy-bundle.json", baseline_bytes),
                          ("evaluation-manifest.json", manifest_bytes)):
            path = root / rel
            identity._assert_no_symlink_ancestors(artifact_root, path,
                                                   "artifact")
            if path.exists():
                if identity._read_bounded(path, "artifact") != data:
                    _fail("freeze_modified",
                          "artifact differs: {}".format(rel))
            else:
                path.write_bytes(data)

        record = {
            "freeze_id": freeze_id,
            "campaign_id": campaign_id,
            "config": config,
            "config_digest": digest(config),
            "manifest_ref": self._store.put_object(manifest),
            "manifest_digest": digest(manifest),
            "baseline_ref": self._store.put_object(baseline_bundle),
            "baseline_bundle": baseline_bundle,
            "baseline_digest": digest(baseline_bundle),
            "dataset_digest": digest(dataset),
            "runner_digest": digest(loads(runner_bytes)),
            "artifact_root": str(artifact_root),
            "frozen_at": None,
        }
        record_ref = self._store.put_object(record)
        record["frozen_at"] = record_ref
        pin = {"freeze_id": freeze_id, "content_ref": record_ref,
               "content_digest": record_ref}
        pin_path = self._pin_path(freeze_id)
        pin_path.parent.mkdir(parents=True, exist_ok=True)
        self._store.atomic_json(pin_path, pin)
        state["freezes"][freeze_id] = {"record_ref": record_ref,
                                        "record": record}
        self._save_state(state)
        return record

    def load_freeze(self, freeze_id: str) -> dict:
        if not _is_id(freeze_id):
            _fail("input_invalid", "freeze_id invalid")
        state = self._load_state()
        entry = state["freezes"].get(freeze_id)
        if entry is None:
            _fail("freeze_missing", "unknown freeze")
        pin_path = self._pin_path(freeze_id)
        if not pin_path.exists():
            _fail("freeze_missing", "trusted pin missing")
        pin = loads(pin_path.read_bytes())
        record = self._store.get_object(pin["content_ref"])
        if digest(record) != pin["content_digest"]:
            _fail("freeze_modified", "freeze record digest mismatch")
        if record.get("freeze_id") != freeze_id:
            _fail("freeze_modified", "freeze id mismatch")
        if digest(record["config"]) != record["config_digest"]:
            _fail("freeze_modified", "config digest mismatch")
        if digest(record["baseline_bundle"]) != record["baseline_digest"]:
            _fail("freeze_modified", "baseline digest mismatch")
        runner_now = loads(agent_runner_snapshot())
        if digest(runner_now) != record["runner_digest"]:
            _fail("freeze_modified", "agent runner bundle changed")
        root = Path(record["artifact_root"])
        for rel, expected in (
                ("dataset.json", record["dataset_digest"]),
                ("baseline-policy-bundle.json", record["baseline_digest"]),
                ("runner-bundle.json", digest(runner_now))):
            path = root / "agent-rsi" / freeze_id / rel
            data = identity._read_bounded(path, "artifact")
            if digest(loads(data)) != expected:
                _fail("freeze_modified",
                      "artifact digest mismatch: {}".format(rel))
        return record

    # -- candidate / train -----------------------------------------------

    def create_candidate(self, freeze_id: str, candidate_bundle: dict,
                         rules_applied: List[str],
                         train_report_ref: str) -> dict:
        record = self.load_freeze(freeze_id)
        validate_bundle(candidate_bundle)
        state = self._load_state()
        candidate_id = "agc-{}".format(digest({
            "freeze_id": freeze_id,
            "candidate": candidate_bundle,
        })[:24])
        candidate = {
            "candidate_id": candidate_id,
            "freeze_id": freeze_id,
            "campaign_id": record["campaign_id"],
            "bundle_ref": self._store.put_object(candidate_bundle),
            "bundle_digest": digest(candidate_bundle),
            "baseline_digest": record["baseline_digest"],
            "rules_applied": list(rules_applied),
            "train_report_ref": train_report_ref,
            "compared": False,
            "applied": False,
        }
        state["candidates"][candidate_id] = candidate
        self._save_state(state)
        return candidate

    def get_candidate(self, candidate_id: str) -> dict:
        if not _is_id(candidate_id):
            _fail("input_invalid", "candidate_id invalid")
        state = self._load_state()
        candidate = state["candidates"].get(candidate_id)
        if candidate is None:
            _fail("input_invalid", "unknown candidate")
        return candidate

    def train(self, freeze_id: str, executor: SideExecutor) -> dict:
        """Run the baseline bundle on train scenarios and derive a
        candidate through the deterministic policy editor."""
        record = self.load_freeze(freeze_id)
        config = record["config"]
        baseline = record["baseline_bundle"]
        results = []
        rules: List[str] = []
        for scenario_id in config["scenario_splits"]["train"]:
            for seed in config["seed_pairs"]:
                outcome = executor(scenario_id, baseline, seed)
                failure_class = classify_failure(outcome)
                if failure_class and failure_class not in rules:
                    rules.append(failure_class)
                results.append({
                    "scenario_id": scenario_id, "seed": seed,
                    "bundle_id": baseline.get("bundle_id"),
                    "outcome": outcome,
                    "failure_class": failure_class,
                })
        candidate_bundle, changed_rules = _apply_guidance(baseline, rules)
        changed = bool(changed_rules)
        if not changed:
            candidate_bundle = baseline
        train_report = {
            "kind": "agent_rsi_train",
            "freeze_id": freeze_id,
            "results": results,
            "failure_classes": rules,
            "rules_applied": changed_rules,
            "changed": changed,
            "candidate_bundle_id": candidate_bundle.get("bundle_id"),
            "candidate_version": candidate_bundle.get("version"),
        }
        train_ref = self._store.put_object(train_report)
        candidate = self.create_candidate(
            freeze_id, candidate_bundle, changed_rules, train_ref)
        # Experience cards: only from completed train runs (abstract).
        memory_refs = []
        for item in results:
            if item["outcome"].get("completion") == "incomplete":
                continue
            try:
                card = {
                    "split": "train",
                    "completion": "completed",
                    "failure_class": item["failure_class"] or "state",
                    "outcome": "supported"
                    if item["outcome"]["status"] == "success"
                    else "unverified",
                    "action": "retain" if item["outcome"]["status"] ==
                    "success" else "change_search",
                    "evidence_count": 1,
                }
                ref = self._add_experience_agent(
                    card, train_ref=train_ref)
                memory_refs.append(ref)
            except Exception:
                continue
        return {
            "freeze_id": freeze_id,
            "candidate_id": candidate["candidate_id"],
            "changed": changed,
            "rules_applied": changed_rules,
            "failure_classes": rules,
            "train_ref": train_ref,
            "candidate_bundle_ref": candidate["bundle_ref"],
            "results": results,
            "memory_refs": memory_refs,
        }

    def _add_experience_agent(self, card: dict, *,
                              train_ref: str) -> str:
        """Create and pin an experience card from an agent train run.

        Unlike ``rsi.RSI.add_experience`` (which requires a
        ``rsi_train``-format report), this validates the agent RSI train
        report directly and writes to the same trusted-root memory
        namespace.
        """
        if not _is_id(train_ref) or len(train_ref) != 64:
            _fail("input_invalid", "train_ref must be sha256")
        train_report = self._store.get_object(train_ref)
        if not isinstance(train_report, dict):
            _fail("input_invalid", "train report not found")
        if train_report.get("kind") != "agent_rsi_train":
            _fail("input_invalid", "not an agent train report")
        normalized = {
            "split": "train",
            "completion": "completed",
            "failure_class": card.get("failure_class") or "state",
            "outcome": card.get("outcome") or "unverified",
            "action": card.get("action") or "retain",
            "evidence_count": card.get("evidence_count", 1),
        }
        ref = self._store.put_object(normalized)
        pin_dir = self._trusted / "rsi" / "memory"
        pin_dir.mkdir(parents=True, exist_ok=True)
        pin_path = pin_dir / (ref + ".json")
        if not pin_path.exists():
            self._store.atomic_json(
                pin_path, {"memory_ref": ref, "train_ref": train_ref})
        state = self._load_state()
        if ref not in state.get("memory", []):
            state.setdefault("memory", []).append(ref)
            self._save_state(state)
        return ref

    # -- compare -----------------------------------------------------------

    def _side_result(self, outcome: dict) -> dict:
        success = outcome.get("status") == "success"
        attribution = outcome.get("failure_attribution")
        return {
            "completion": outcome.get("completion", "completed"),
            "success": success,
            "detection_covered": success,
            "remediation_success": bool(outcome.get("remediation_success")),
            "benign_passed": outcome.get("benign_passed"),
            "safety_violations": 0,
            "calls": int(outcome.get("llm_calls") or 0),
            "failure_attribution": attribution if attribution in
            ("candidate", "external") else
            ("candidate" if not success else None),
            "model_observations": outcome.get("model_observations"),
        }

    def compare(self, freeze_id: str, candidate_id: str,
                executor: SideExecutor,
                on_holdout: Optional[Callable[[dict], None]] = None) -> dict:
        record = self.load_freeze(freeze_id)
        candidate = self.get_candidate(candidate_id)
        if candidate["freeze_id"] != freeze_id:
            _fail("input_invalid", "candidate freeze mismatch")
        if candidate["compared"]:
            _fail("policy_denied", "candidate already compared")
        config = record["config"]
        baseline = record["baseline_bundle"]
        candidate_bundle = self._store.get_object(candidate["bundle_ref"])
        if (digest(candidate_bundle) != candidate["bundle_digest"]
                or candidate["baseline_digest"] != record["baseline_digest"]):
            _fail("review_mismatch", "candidate/baseline digest mismatch")
        # Refuse a no-op before creating a holdout-access audit or running
        # an executor. A no-op cannot establish evidence of improvement.
        if candidate["bundle_digest"] == record["baseline_digest"]:
            _fail("policy_denied", "unchanged candidate cannot be compared")
        tasks = [(sid, "validation") for sid in
                 config["scenario_splits"]["validation"]]
        tasks += [(sid, "holdout") for sid in
                  config["scenario_splits"]["holdout"]]
        seeds = config["seed_pairs"]
        planned_pairs = len(tasks) * len(seeds)
        if planned_pairs > config["budget"]["max_pairs"]:
            _fail("policy_denied", "planned pairs exceed frozen budget")

        audit = {
            "candidate_id": candidate_id,
            "holdout_task_ids": [sid for sid, _ in tasks if _ == "holdout"],
            "accessed_at": digest({"candidate": candidate_id}),
        }
        audit_ref = self._store.put_object(audit)
        if on_holdout is not None:
            on_holdout(audit)

        pairs = []
        for scenario_id, split in tasks:
            for seed in seeds:
                order = ["baseline", "candidate"]
                if (config["order_policy"] == "alternating"
                        and len(pairs) % 2 == 1):
                    order = ["candidate", "baseline"]
                sides = {}
                for side_name in order:
                    bundle = baseline if side_name == "baseline" \
                        else candidate_bundle
                    outcome = executor(scenario_id, bundle, seed)
                    sides[side_name] = self._side_result(outcome)
                excluded = None
                if any(sides[s]["failure_attribution"] == "external"
                       for s in sides):
                    excluded = "external_failure"
                pairs.append({
                    "task_id": scenario_id,
                    "split": split,
                    "seed": seed,
                    "order": order,
                    "baseline": sides["baseline"],
                    "candidate": sides["candidate"],
                    "excluded_reason": excluded,
                })

        summary = self._evaluate_pairs(pairs, config, candidate)
        comparison = {
            "kind": "agent_rsi_compare",
            "freeze_id": freeze_id,
            "candidate_id": candidate_id,
            "config_digest": record["config_digest"],
            "model_pinning": config["model_pinning"],
            "pairs": pairs,
        }
        comparison_ref = self._store.put_object(comparison)
        state = self._load_state()
        cand = state["candidates"][candidate_id]
        cand["compared"] = True
        cand["comparison_ref"] = comparison_ref
        cand["summary"] = summary
        self._save_state(state)
        return {
            "candidate_id": candidate_id,
            "freeze_id": freeze_id,
            "comparison_ref": comparison_ref,
            "holdout_access_ref": audit_ref,
            "summary": summary,
        }

    def _evaluate_pairs(self, pairs: List[dict], config: dict,
                        candidate: dict) -> dict:
        thresholds = config["thresholds"]
        invalid_pairs = 0
        losses = 0
        total_calls = 0
        val_den = val_b = val_c = hold_den = hold_b = hold_c = 0
        complete = True
        benign_ok = True
        for pair in pairs:
            b = pair["baseline"]
            c = pair["candidate"]
            total_calls += b["calls"] + c["calls"]
            if pair["excluded_reason"] == "external_failure":
                invalid_pairs += 1
                complete = False
                continue
            if b["completion"] != "completed" or c["completion"] != "completed":
                complete = False
            if c["completion"] == "completed" and not c["success"]:
                losses += 1
            if b["completion"] == "completed" and c["completion"] == "completed":
                if b["success"] and not c["success"]:
                    losses += 1
            if c["benign_passed"] is False or c["safety_violations"] > 0:
                benign_ok = False
            if pair["split"] == "validation":
                val_den += 1
                val_b += 1 if b["success"] else 0
                val_c += 1 if c["success"] else 0
            else:
                hold_den += 1
                hold_b += 1 if b["success"] else 0
                hold_c += 1 if c["success"] else 0
        val_rate_b = val_b / val_den if val_den else 0.0
        val_rate_c = val_c / val_den if val_den else 0.0
        hold_rate_b = hold_b / hold_den if hold_den else 0.0
        hold_rate_c = hold_c / hold_den if hold_den else 0.0
        gain = val_rate_c - val_rate_b
        train_report = self._store.get_object(candidate["train_report_ref"])
        train_results = train_report.get("results", []) if isinstance(
            train_report, dict) else []
        pinned, model_pin_reason, model_observations = _verify_model_pinning(
            pairs, config["model_pinning"], train_results)
        ready = (
            complete
            and invalid_pairs == 0
            and gain > max(0.0, float(thresholds["success_gain_min"]))
            and hold_rate_c >= hold_rate_b - float(thresholds["holdout_min_delta"])
            and benign_ok
            and pinned
        )
        return {
            "pairs": len(pairs),
            "validation_baseline": val_b,
            "validation_candidate": val_c,
            "validation_denominator": val_den,
            "gain": gain,
            "holdout_baseline": hold_b,
            "holdout_candidate": hold_c,
            "holdout_denominator": hold_den,
            "invalid_pairs": invalid_pairs,
            "losses": losses,
            "complete": complete,
            "benign_ok": benign_ok,
            "total_calls": total_calls,
            "model_pinned": pinned,
            "model_pin_reason": model_pin_reason,
            "model_observations_checked": model_observations,
            "exploration_grade": not pinned,
            "ready": ready,
            "thresholds": thresholds,
            "candidate_bundle_digest": candidate.get("bundle_digest"),
            "baseline_bundle_digest": candidate.get("baseline_digest"),
        }

    # -- ready / review / apply --------------------------------------------

    def ready(self, candidate_id: str) -> dict:
        candidate = self.get_candidate(candidate_id)
        if not candidate.get("compared"):
            _fail("hold", "candidate not compared yet")
        summary = candidate.get("summary") or {}
        if summary.get("ready") is True:
            status = "promotion-ready"
        elif summary.get("exploration_grade") is True:
            status = "exploratory-unpinned-model"
        else:
            status = "not-ready"
        return {"status": status, "candidate_id": candidate_id,
                "summary": summary}

    def record_review(self, candidate_id: str, reviewer: str,
                      decision: str) -> dict:
        with self._store.lock():
            return self._record_review(candidate_id, reviewer, decision)

    def _record_review(self, candidate_id: str, reviewer: str,
                       decision: str) -> dict:
        candidate = self.get_candidate(candidate_id)
        if not _is_id(reviewer):
            _fail("input_invalid", "reviewer invalid")
        if decision not in ("approved", "rejected"):
            _fail("input_invalid", "decision invalid")
        state = self._load_state()
        review_id = "agr-{}".format(digest({
            "candidate_id": candidate_id, "reviewer": reviewer,
            "decision": decision,
        })[:24])
        review = {
            "review_id": review_id,
            "candidate_id": candidate_id,
            "freeze_id": candidate["freeze_id"],
            "reviewer": reviewer,
            "decision": decision,
        }
        state["reviews"][review_id] = review
        self._save_state(state)
        pin_path = self._trusted / "agent_rsi" / "reviews" / (review_id + ".json")
        self._safe_path(self._trusted, pin_path)
        self._store.atomic_json(pin_path, {
            "review": review, "bundle_ref": candidate["bundle_ref"],
            "comparison_ref": candidate.get("comparison_ref"),
            "baseline_digest": candidate["baseline_digest"],
        })
        return review

    def initialize_registry(self, baseline_ref: str) -> dict:
        with self._store.lock():
            self._bundle(baseline_ref)
            self._safe_path(self._workspace, self._registry_path)
            self._safe_path(self._trusted, self._activation_path)
            if self._registry_path.exists() or self._activation_path.exists():
                _fail("policy_denied", "registry already exists")
            registry = {"current_bundle_ref": baseline_ref,
                        "baseline_bundle_ref": baseline_ref, "history": []}
            self._commit_registry(registry)
            return registry

    def apply(self, review_id: str, expected_baseline: str,
              expected_candidate: str, rollback: str) -> dict:
        with self._store.lock():
            return self._apply(review_id, expected_baseline, expected_candidate, rollback)

    def _apply(self, review_id: str, expected_baseline: str,
               expected_candidate: str, rollback: str) -> dict:
        state = self._load_state()
        review = state["reviews"].get(review_id)
        if not isinstance(review, dict):
            _fail("input_invalid", "review not found")
        candidate = self.get_candidate(review["candidate_id"])
        if review["decision"] != "approved":
            _fail("not_approved", "review not approved")
        if candidate.get("applied"):
            _fail("policy_denied", "candidate already applied")
        if not candidate.get("compared"):
            _fail("hold", "candidate not compared")
        summary = candidate.get("summary") or {}
        if summary.get("ready") is not True:
            _fail("not_ready", "candidate is not promotion-ready")
        if expected_baseline != candidate["baseline_digest"]:
            _fail("review_mismatch", "baseline digest mismatch")
        if expected_candidate != candidate["bundle_digest"]:
            _fail("review_mismatch", "candidate digest mismatch")
        if not re.fullmatch(r"agr-[0-9a-f]{24}", str(review_id)):
            _fail("input_invalid", "invalid review id")
        pin_path = self._trusted / "agent_rsi" / "reviews" / (review_id + ".json")
        self._safe_path(self._trusted, pin_path)
        expected_pin = {"review": review, "bundle_ref": candidate["bundle_ref"],
                        "comparison_ref": candidate.get("comparison_ref"),
                        "baseline_digest": candidate["baseline_digest"]}
        if not pin_path.exists() or read_json(pin_path) != expected_pin:
            _fail("not_approved", "trusted review mismatch")
        bundle = self._bundle(candidate["bundle_ref"])
        if digest(bundle) != expected_candidate:
            _fail("review_mismatch", "bundle digest mismatch")
        record = self.load_freeze(candidate["freeze_id"])
        comparison = self._store.get_object(candidate.get("comparison_ref"))
        planned = {(sid, split, seed) for split in ("validation", "holdout")
                   for sid in record["config"]["scenario_splits"][split]
                   for seed in record["config"]["seed_pairs"]}
        pairs = comparison.get("pairs", [])
        actual = {(p["task_id"], p["split"], p["seed"]) for p in pairs}
        if (comparison.get("candidate_id") != candidate["candidate_id"]
                or comparison.get("freeze_id") != record["freeze_id"]
                or comparison.get("config_digest") != record["config_digest"]
                or candidate["baseline_digest"] != record["baseline_digest"]
                or actual != planned or len(pairs) != len(planned)
                or self._evaluate_pairs(pairs, record["config"], candidate) != summary
                or summary.get("ready") is not True):
            _fail("not_ready", "comparison does not match frozen evaluation")
        if not _is_id(rollback):
            _fail("input_invalid", "rollback description required")
        registry = self._registry()
        if registry is None:
            _fail("checkpoint_missing", "registry not initialized")
        previous = registry["current_bundle_ref"]
        if previous != expected_baseline:
            _fail("review_mismatch", "current registry is not evaluated baseline")
        self._bundle(previous)
        registry["history"].append({
            "candidate_id": candidate["candidate_id"],
            "review_id": review_id,
            "from_ref": previous,
            "to_ref": candidate["bundle_ref"],
            "rollback": rollback,
            "operation": "apply",
            "campaign_id": candidate["campaign_id"],
        })
        registry["current_bundle_ref"] = candidate["bundle_ref"]
        candidate["applied"] = True
        state["candidates"][candidate["candidate_id"]] = candidate
        self._save_state(state)
        try:
            self._commit_registry(registry)
        except Exception:
            candidate["applied"] = False
            self._save_state(state)
            raise
        return {"applied": True,
                "candidate_id": candidate["candidate_id"],
                "current_bundle_ref": candidate["bundle_ref"],
                "rollback": rollback}

    def rollback(self, expected_current: str, reason: str) -> dict:
        """Restore the most recent active apply's previous approved bundle.

        Reads the trusted commit even if its workspace mirror is damaged;
        never loads a broken current bundle in order to restore its predecessor.
        """
        if not _is_id(reason):
            _fail("input_invalid", "rollback reason required (1..128 chars)")
        with self._store.lock():
            registry = self._registry(recovery=True)
            if registry is None or registry["current_bundle_ref"] != expected_current:
                _fail("review_mismatch", "rollback current mismatch")
            active = []
            for event in registry["history"]:
                if event["operation"] == "apply":
                    active.append(event)
                elif event["operation"] == "rollback" and active:
                    active.pop()
            previous = active[-1]["from_ref"] if active else registry["baseline_bundle_ref"]
            self._bundle(previous)
            registry["history"].append({
                "operation": "rollback", "from_ref": expected_current,
                "to_ref": previous, "reason": reason,
                "campaign_id": active[-1]["campaign_id"] if active else None,
            })
            registry["current_bundle_ref"] = previous
            self._commit_registry(registry)
            return {"rolled_back": True, "current_bundle_ref": previous,
                    "previous_bundle_ref": expected_current, "reason": reason}
