"""Deterministic local RSI training. No network, no LLM."""
from __future__ import annotations

from typing import Any

from .jsonio import DataError, canonical
from . import rsi_compare

MAX_TASKS = 32
MAX_CASES = 64
ROLES = ("admin", "user", "guest")
RESOURCES = ("record", "public")
REPORT_KEYS = {"kind", "split", "completion", "policy_ref", "dataset_ref",
               "seed", "results", "evidence_count", "failure_class"}
RESULT_KEYS = {"task_id", "completion", "success", "benign_passed",
               "safety_violations", "calls", "failure_attribution"}


def _fail(msg: str, code: str = "input_invalid") -> None:
    raise DataError(msg, code)


def _is_id(value: Any) -> bool:
    return isinstance(value, str) and 0 < len(value) <= 128 and value.strip() == value


def _exact(obj: Any, fields: set[str], what: str) -> None:
    if not isinstance(obj, dict):
        _fail(f"{what} must be an object")
    if set(obj.keys()) != fields:
        _fail(f"{what} has wrong fields")


def _check_policy(policy: Any) -> None:
    _exact(policy, {"require_admin"}, "policy")
    if not isinstance(policy["require_admin"], bool):
        _fail("require_admin must be bool")


def _check_seed(seed: Any) -> None:
    if isinstance(seed, bool) or not isinstance(seed, int):
        _fail("seed must be int")
    if seed < 0 or seed > 2 ** 31:
        _fail("seed out of range")


def _validate_dataset(dataset: Any) -> list[dict]:
    _exact(dataset, {"tasks"}, "dataset")
    tasks = dataset["tasks"]
    if not isinstance(tasks, list) or not tasks:
        _fail("tasks must be nonempty list")
    if len(tasks) > MAX_TASKS:
        _fail("too many tasks")
    seen: set[str] = set()
    for task in tasks:
        _exact(task, {"task_id", "split", "cases"}, "task")
        tid = task["task_id"]
        if not _is_id(tid):
            _fail("invalid task_id")
        if tid in seen:
            _fail("duplicate task_id")
        seen.add(tid)
        if task["split"] not in rsi_compare.SPLITS:
            _fail("invalid split")
        if task["split"] != "train":
            continue
        cases = task["cases"]
        if not isinstance(cases, list) or not cases or len(cases) > MAX_CASES:
            _fail("invalid cases")
        for case in cases:
            _exact(case, {"role", "resource", "expected_allowed"}, "case")
            if not isinstance(case["role"], str) or not 0 < len(case["role"]) <= 64:
                _fail("invalid role")
            if case["resource"] not in RESOURCES:
                _fail("invalid resource")
            if not isinstance(case["expected_allowed"], bool):
                _fail("invalid expected_allowed")
    if not any(t["split"] == "train" for t in tasks):
        _fail("at least one train task required")
    return tasks


def _train_tasks(tasks: list[dict]) -> list[dict]:
    return [t for t in tasks if t["split"] == "train"]


def _report(policy: dict, tasks: list[dict], seed: int,
            policy_ref: str, dataset_ref: str) -> dict:
    results = []
    evidence = 0
    any_failed = False
    for task in _train_tasks(tasks):
        side = rsi_compare.execute_policy(policy, task, seed)
        evidence += len(task["cases"])
        if not side["success"]:
            any_failed = True
        results.append({
            "task_id": task["task_id"],
            "completion": side["completion"],
            "success": side["success"],
            "benign_passed": side["benign_passed"],
            "safety_violations": side["safety_violations"],
            "calls": side["calls"],
            "failure_attribution": side["failure_attribution"],
        })
    return {
        "kind": "rsi_train",
        "split": "train",
        "completion": "completed",
        "policy_ref": policy_ref,
        "dataset_ref": dataset_ref,
        "seed": seed,
        "results": results,
        "evidence_count": evidence,
        "failure_class": "search" if any_failed else "state",
    }


def _get(store: Any, ref: Any, what: str) -> Any:
    if not _is_id(ref):
        _fail(f"invalid {what} ref")
    try:
        return store.get_object(ref)
    except Exception as exc:
        raise DataError(f"{what} not found") from exc


def train(rsi: Any, policy_ref: str, dataset_ref: str, seed: int = 0) -> dict:
    store = rsi._store
    _check_seed(seed)
    policy = _get(store, policy_ref, "policy")
    _check_policy(policy)
    dataset = _get(store, dataset_ref, "dataset")
    tasks = _validate_dataset(dataset)
    report = _report(policy, tasks, seed, policy_ref, dataset_ref)
    train_ref = store.put_object(report)
    return {"train_ref": train_ref, "report": report}


def _check_report(report: Any) -> None:
    _exact(report, REPORT_KEYS, "report")
    if report["kind"] != "rsi_train":
        _fail("invalid kind")
    if report["split"] != "train":
        _fail("invalid split")
    if report["completion"] != "completed":
        _fail("invalid completion")
    if not _is_id(report["policy_ref"]) or not _is_id(report["dataset_ref"]):
        _fail("invalid refs")
    _check_seed(report["seed"])
    if report["failure_class"] not in ("search", "state"):
        _fail("invalid failure_class")
    results = report["results"]
    if not isinstance(results, list):
        _fail("results must be list")
    total = 0
    for r in results:
        _exact(r, RESULT_KEYS, "result")
        if not _is_id(r["task_id"]):
            _fail("invalid task_id")
        if r["completion"] not in rsi_compare.COMPLETIONS:
            _fail("invalid completion")
        for key in ("success", "benign_passed"):
            if not isinstance(r[key], bool):
                _fail(f"{key} must be bool")
        for key in ("safety_violations", "calls"):
            v = r[key]
            if isinstance(v, bool) or not isinstance(v, int) or v < 0:
                _fail(f"{key} must be nonnegative int")
        if r["failure_attribution"] not in (None, "candidate", "external"):
            _fail("invalid failure_attribution")
        total += r["calls"]
    if report["evidence_count"] != total:
        _fail("evidence_count mismatch")


def verify_train(rsi: Any, train_ref: str) -> dict:
    store = rsi._store
    report = _get(store, train_ref, "train")
    _check_report(report)
    policy = _get(store, report["policy_ref"], "policy")
    _check_policy(policy)
    dataset = _get(store, report["dataset_ref"], "dataset")
    tasks = _validate_dataset(dataset)
    expected = _report(policy, tasks, report["seed"],
                       report["policy_ref"], report["dataset_ref"])
    if canonical(expected) != canonical(report):
        _fail("report mismatch", "verification_failed")
    return {"ok": True, "train_ref": train_ref, "report": report}


def propose(rsi: Any, train_ref: str) -> dict:
    store = rsi._store
    verified = verify_train(rsi, train_ref)
    report = verified["report"]
    policy = _get(store, report["policy_ref"], "policy")
    dataset = _get(store, report["dataset_ref"], "dataset")
    tasks = _validate_dataset(dataset)
    train_tasks = _train_tasks(tasks)
    seed = report["seed"]

    baseline_successes = 0
    for task in train_tasks:
        if rsi_compare.execute_policy(policy, task, seed)["success"]:
            baseline_successes += 1

    candidate_policy = {"require_admin": not policy["require_admin"]}
    candidate_successes = 0
    for task in train_tasks:
        if rsi_compare.execute_policy(candidate_policy, task, seed)["success"]:
            candidate_successes += 1

    improved = candidate_successes > baseline_successes
    if improved:
        proposal_ref = store.put_object(candidate_policy)
        changed = True
        reason = "candidate strictly improves train success"
    else:
        proposal_ref = report["policy_ref"]
        changed = False
        reason = "no strict train improvement; retain baseline"

    card = {
        "split": "train",
        "completion": "completed",
        "failure_class": report["failure_class"],
        "outcome": "supported" if improved else "unverified",
        "action": "change_search" if improved else "restore_state",
        "evidence_count": report["evidence_count"],
    }

    return {
        "train_ref": train_ref,
        "reason": reason,
        "baseline_policy_ref": report["policy_ref"],
        "proposal_ref": proposal_ref,
        "changed": changed,
        "training_successes": {
            "baseline": baseline_successes,
            "candidate": candidate_successes,
        },
        "evaluated_on": "train",
        "promotion_ready": False,
        "experience_card": card,
    }
