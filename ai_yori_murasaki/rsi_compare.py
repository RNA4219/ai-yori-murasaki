"""Deterministic local RSI policy comparison. No network, no LLM."""
from __future__ import annotations

import random
from pathlib import Path
from typing import Any

from .jsonio import DataError, canonical, loads
from . import evaluation_identity as identity

MAX_TASKS = 32
MAX_CASES = 64
FIXED_THRESHOLD = 0.0
SPLITS = ("train", "validation", "holdout")
SIDE_FIELDS = ("completion", "success", "benign_passed", "safety_violations",
               "calls", "failure_attribution")
COMPLETIONS = ("completed", "incomplete", "adapter_unavailable", "cancelled")


def _fail(msg: str, code: str = "input_invalid") -> None:
    raise DataError(msg, code)


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128 and value.strip() == value


def _exact(obj: Any, fields: set[str], what: str) -> None:
    if not isinstance(obj, dict):
        _fail(f"{what} must be an object")
    if set(obj.keys()) != fields:
        _fail(f"{what} has wrong fields")


def load_dataset(freeze: dict) -> list[dict]:
    if not isinstance(freeze, dict):
        _fail("freeze must be an object")
    root = freeze.get("artifact_root")
    bindings = freeze.get("bindings")
    if not isinstance(root, str) or not isinstance(bindings, dict):
        _fail("freeze missing artifact_root/bindings")
    rel = bindings.get("dataset")
    if not isinstance(rel, str):
        _fail("freeze missing dataset binding")
    path = identity._resolve_within(Path(root), rel, "dataset")
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DataError("dataset unreadable") from exc
    data = loads(raw)
    _exact(data, {"tasks"}, "dataset")
    tasks = data["tasks"]
    if not isinstance(tasks, list) or not tasks:
        _fail("tasks must be nonempty list")
    if len(tasks) > MAX_TASKS:
        _fail("too many tasks")
    seen: set[str] = set()
    splits: set[str] = set()
    for task in tasks:
        _exact(task, {"task_id", "split", "cases"}, "task")
        tid = task["task_id"]
        if not _is_id(tid):
            _fail("invalid task_id")
        if tid in seen:
            _fail("duplicate task_id")
        seen.add(tid)
        split = task["split"]
        if split not in SPLITS:
            _fail("invalid split")
        splits.add(split)
        cases = task["cases"]
        if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
            _fail("invalid cases")
        for case in cases:
            _exact(case, {"role", "resource", "expected_allowed"}, "case")
            role = case["role"]
            if not isinstance(role, str) or not role or len(role) > 64:
                _fail("invalid role")
            res = case["resource"]
            if res not in ("record", "public"):
                _fail("invalid resource")
            if not isinstance(case["expected_allowed"], bool):
                _fail("invalid expected_allowed")
    if "validation" not in splits or "holdout" not in splits:
        _fail("need validation and holdout tasks")
    return tasks


def _decide(policy: dict, role: str, resource: str) -> bool:
    return resource == "public" or not policy["require_admin"] or role == "admin"


def execute_policy(policy: dict, task: dict, seed: int) -> dict:
    _exact(policy, {"require_admin"}, "policy")
    if not isinstance(policy["require_admin"], bool):
        _fail("require_admin must be bool")
    if isinstance(seed, bool) or not isinstance(seed, int):
        _fail("seed must be int")
    cases = list(task["cases"])
    random.Random(seed).shuffle(cases)
    success = True
    benign_passed = True
    safety_violations = 0
    for case in cases:
        actual = _decide(policy, case["role"], case["resource"])
        expected = case["expected_allowed"]
        if actual != expected:
            success = False
        if expected and not actual:
            benign_passed = False
    return {
        "completion": "completed",
        "success": success,
        "benign_passed": benign_passed,
        "safety_violations": safety_violations,
        "calls": len(cases),
        "failure_attribution": None,
    }


def _check_side(side: Any, what: str) -> None:
    _exact(side, set(SIDE_FIELDS), what)
    if side["completion"] not in COMPLETIONS:
        _fail(f"{what} invalid completion")
    for key in ("success", "benign_passed"):
        if not isinstance(side[key], bool):
            _fail(f"{what} {key} must be bool")
    for key in ("safety_violations", "calls"):
        v = side[key]
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            _fail(f"{what} {key} must be nonnegative int")
    fa = side["failure_attribution"]
    if fa not in (None, "candidate", "external"):
        _fail(f"{what} invalid failure_attribution")
    if side["completion"] != "completed" and side["success"]:
        _fail(f"{what} incomplete cannot succeed")


def _validate_comparison(comparison: Any, freeze: dict) -> dict:
    _exact(comparison, {"freeze_id", "candidate_id", "pairs"}, "comparison")
    if not _is_id(comparison["candidate_id"]):
        _fail("invalid candidate_id")
    if comparison["freeze_id"] != freeze.get("freeze_id"):
        _fail("freeze_id mismatch")
    tasks = load_dataset(freeze)
    by_id = {t["task_id"]: t for t in tasks}
    config = freeze.get("config")
    if not isinstance(config, dict):
        _fail("freeze missing config")
    seeds = config.get("seed_pairs")
    if not isinstance(seeds, list) or not seeds:
        _fail("invalid seed_pairs")
    for s in seeds:
        if isinstance(s, bool) or not isinstance(s, int):
            _fail("seed must be int")
    max_pairs = config["budget"]["max_pairs"]
    max_calls = config["budget"]["max_calls"]
    if isinstance(max_pairs, bool) or not isinstance(max_pairs, int):
        _fail("invalid max_pairs")
    if isinstance(max_calls, bool) or not isinstance(max_calls, int):
        _fail("invalid max_calls")
    pairs = comparison["pairs"]
    if not isinstance(pairs, list):
        _fail("pairs must be list")
    if len(pairs) > max_pairs:
        _fail("too many pairs")
    eval_tasks = sorted(
        (t for t in tasks if t["split"] in ("validation", "holdout")),
        key=lambda t: t["task_id"])
    expected: list[tuple[str, str, int, list[str]]] = []
    for task in eval_tasks:
        for idx, seed in enumerate(seeds):
            order = ["baseline", "candidate"]
            if config["order_policy"] == "alternating" and len(expected) % 2 == 1:
                order = ["candidate", "baseline"]
            expected.append((task["task_id"], task["split"], seed, order))
    seen: set[tuple[str, int]] = set()
    total_calls = 0
    for pair in pairs:
        _exact(pair, {"task_id", "split", "seed", "order", "baseline",
                      "candidate", "excluded_reason"}, "pair")
        tid = pair["task_id"]
        seed = pair["seed"]
        if not _is_id(tid) or tid not in by_id:
            _fail("unknown task_id")
        if isinstance(seed, bool) or not isinstance(seed, int):
            _fail("seed must be int")
        key = (tid, seed)
        if key in seen:
            _fail("duplicate pair")
        seen.add(key)
        if pair["split"] != by_id[tid]["split"]:
            _fail("split mismatch")
        order = pair["order"]
        if not isinstance(order, list) or len(order) != 2 or set(order) != {"baseline", "candidate"}:
            _fail("invalid order")
        _check_side(pair["baseline"], "baseline")
        _check_side(pair["candidate"], "candidate")
        total_calls += pair["baseline"]["calls"] + pair["candidate"]["calls"]
    if total_calls > max_calls:
        _fail("too many calls")
    expected_keys = {(t, s) for t, _, s, _ in expected}
    if seen != expected_keys:
        _fail("pair set mismatch")
    for pair in pairs:
        tid = pair["task_id"]
        seed = pair["seed"]
        match = next(e for e in expected if e[0] == tid and e[2] == seed)
        if pair["order"] != match[3]:
            _fail("order mismatch")
        task = by_id[tid]
        for side_name in ("baseline", "candidate"):
            policy = freeze.get(side_name + "_policy")
            if not isinstance(policy, dict):
                _fail(f"missing {side_name}_policy")
            actual = execute_policy(policy, task, seed)
            reported = pair[side_name]
            if reported["completion"] == "completed":
                if reported != actual:
                    _fail(f"{side_name} metrics mismatch")
            else:
                if reported["success"]:
                    _fail("incomplete success")
                if reported["failure_attribution"] == "external":
                    if pair["excluded_reason"] != "external_failure":
                        _fail("external failure needs excluded_reason")
                elif reported["failure_attribution"] == "candidate":
                    if pair["excluded_reason"] is not None:
                        _fail("candidate failure must not be excluded")
                else:
                    if pair["excluded_reason"] is not None:
                        _fail("invalid excluded_reason")
        external = any(pair[side]["failure_attribution"] == "external" for side in ("baseline", "candidate"))
        if pair["excluded_reason"] != ("external_failure" if external else None):
            _fail("excluded_reason mismatch")
    return comparison


def _evaluate_pairs(pairs: list[dict], freeze: dict) -> dict:
    config = freeze["config"]
    max_pairs = config["budget"]["max_pairs"]
    max_calls = config["budget"]["max_calls"]
    threshold = config["thresholds"]["success_gain_min"]
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool):
        _fail("invalid threshold")
    invalid_pairs = 0
    losses = 0
    total_calls = 0
    val_den = 0
    hold_den = 0
    val_b = 0
    val_c = 0
    hold_b = 0
    hold_c = 0
    complete = True
    ready = True
    for pair in pairs:
        b = pair["baseline"]
        c = pair["candidate"]
        total_calls += b["calls"] + c["calls"]
        excluded = pair["excluded_reason"] == "external_failure"
        if excluded:
            invalid_pairs += 1
            complete = False
            ready = False
            continue
        if b["completion"] != "completed":
            complete = False
            ready = False
        if c["completion"] != "completed":
            complete = False
            ready = False
            losses += 1
        if b["completion"] == "completed" and c["completion"] == "completed":
            if b["success"] and not c["success"]:
                losses += 1
        if c["benign_passed"] is False or c["safety_violations"] > 0:
            ready = False
        if b["benign_passed"] is False or b["safety_violations"] > 0:
            ready = False
        if pair["split"] == "validation":
            val_den += 1
            if b["success"]:
                val_b += 1
            if c["success"]:
                val_c += 1
        elif pair["split"] == "holdout":
            hold_den += 1
            if b["success"]:
                hold_b += 1
            if c["success"]:
                hold_c += 1
    if len(pairs) > max_pairs or total_calls > max_calls:
        ready = False
    val_rate_b = val_b / val_den if val_den else 0.0
    val_rate_c = val_c / val_den if val_den else 0.0
    hold_rate_b = hold_b / hold_den if hold_den else 0.0
    hold_rate_c = hold_c / hold_den if hold_den else 0.0
    gain = val_rate_c - val_rate_b
    if not (gain > max(0.0, float(threshold))):
        ready = False
    if hold_rate_c < hold_rate_b:
        ready = False
    if invalid_pairs > 0:
        ready = False
    if not complete:
        ready = False
    return {
        "pairs": len(pairs),
        "baseline": val_b,
        "candidate": val_c,
        "gain": gain,
        "holdout_baseline": hold_b,
        "holdout_candidate": hold_c,
        "invalid_pairs": invalid_pairs,
        "losses": losses,
        "complete": complete,
        "ready": ready,
        "total_calls": total_calls,
        "validation_denominator": val_den,
        "holdout_denominator": hold_den,
    }
