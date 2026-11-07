from __future__ import annotations

import hashlib
import importlib.metadata
import json
import sys
from copy import deepcopy
from pathlib import Path

from . import evaluation_identity as identity
from .jsonio import canonical, loads, DataError
from .rsi_compare import (
    load_dataset as _load_dataset, execute_policy as _execute_policy,
    _validate_comparison, _evaluate_pairs,
)


_RUNNER_MODULES = (
    "rsi_eval.py",
    "rsi_compare.py",
    "rsi_train.py",
    "rsi.py",
    "evaluation_identity.py",
    "jsonio.py",
    "storage.py",
    "contracts.py",
)


def _dep_version(name: str) -> str:
    try:
        return importlib.metadata.version(name)
    except importlib.metadata.PackageNotFoundError:
        return "missing"


def runner_snapshot() -> bytes:
    base = Path(__file__).resolve().parent
    files: dict[str, str] = {}
    for name in _RUNNER_MODULES:
        path = base / name
        try:
            data = path.read_bytes()
        except OSError as error:
            raise DataError(
                "評価器bundleの読み取りに失敗しました", "input_invalid"
            ) from error
        files[name] = hashlib.sha256(data).hexdigest()
    schemas = base / "schemas" / "v1"
    if schemas.is_dir():
        for path in sorted(schemas.glob("*.json"), key=lambda p: p.name):
            try:
                data = path.read_bytes()
            except OSError as error:
                raise DataError(
                    "評価器bundleの読み取りに失敗しました", "input_invalid"
                ) from error
            files["schemas/v1/" + path.name] = hashlib.sha256(data).hexdigest()
    snapshot = {
        "format": "aym-rsi-runner-bundle-v1",
        "python": sys.version,
        "dependencies": {
            "jsonschema": _dep_version("jsonschema"),
            "referencing": _dep_version("referencing"),
        },
        "files": files,
    }
    return canonical(snapshot)


def _safe_write(root, path, data):
    identity._assert_no_symlink_ancestors(root, path, "artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    identity._assert_no_symlink_ancestors(root, path, "artifact")
    try:
        with open(path, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        if identity._read_bounded(path, "artifact") == data:
            return
        raise DataError(
            "既存成果物と内容が異なります", "freeze_modified"
        ) from None
    except OSError as error:
        raise DataError(
            "成果物保存に失敗しました", "input_invalid"
        ) from error


def compare(rsi, candidate_id, on_holdout=None):
    c = rsi.get_candidate(candidate_id)
    f = rsi.load_freeze(c["freeze_id"])
    root = Path(f["artifact_root"])
    b = f["bindings"]
    manifest = rsi._store.get_object(f["manifest_ref"])
    identity.preflight(manifest, root, b)

    boundrunner = identity._resolve_within(root, b["runner"], "runner")
    if identity._read_bounded(boundrunner, "runner") != runner_snapshot():
        raise DataError("評価器bundleが変更されています", "freeze_modified")

    legacy_input = root / b["legacy_input"]
    candidate_input = root / b["candidate_input"]
    if legacy_input.read_bytes() != candidate_input.read_bytes():
        raise ValueError("input mismatch")
    if loads(legacy_input.read_bytes()) != f["config"]:
        raise ValueError("config mismatch")
    if manifest["evaluation"]["configuration"] != f["config"]:
        raise ValueError("manifest config mismatch")

    tasks = sorted(
        [t for t in _load_dataset(f) if t["split"] != "train"],
        key=lambda t: t["task_id"],
    )
    seeds = f["config"]["seed_pairs"]
    policyorder = f["config"]["order_policy"]
    budget = f["config"]["budget"]

    plannedpairs = len(tasks) * len(seeds)
    calls = sum(len(t["cases"]) * len(seeds) * 2 for t in tasks)
    if plannedpairs > budget.get("max_pairs", plannedpairs) or calls > budget.get("max_calls", calls):
        raise ValueError("over budget")

    audit = rsi.record_holdout_access(
        candidate_id, [t["task_id"] for t in tasks if t["split"] == "holdout"]
    )
    if on_holdout is not None:
        on_holdout(audit)

    pairs = []
    for task in tasks:
        for seed in seeds:
            order = ["baseline", "candidate"]
            if policyorder == "alternating" and len(pairs) % 2 == 1:
                order = ["candidate", "baseline"]
            results = {
                side: _execute_policy(f[side + "_policy"], task, seed) for side in order
            }
            pairs.append(
                {
                    "task_id": task["task_id"],
                    "split": task["split"],
                    "seed": seed,
                    "order": order,
                    "baseline": results["baseline"],
                    "candidate": results["candidate"],
                    "excluded_reason": None,
                }
            )

    comparison = _validate_comparison(
        {"freeze_id": f["freeze_id"], "candidate_id": candidate_id, "pairs": pairs}, f
    )
    summary = _evaluate_pairs(pairs, f)

    artifact = root / "results" / (candidate_id + ".json")
    manifestpath = root / "results" / (candidate_id + "-manifest.json")
    bytes_ = canonical(comparison)
    _safe_write(root, artifact, bytes_)

    completed = deepcopy(manifest)
    completed["status"] = "completed"
    completed["outcome"] = {
        "artifact_path": artifact.relative_to(root).as_posix(),
        "artifact_sha256": "sha256:" + hashlib.sha256(bytes_).hexdigest(),
        "records": len(pairs),
        "metrics": {manifest["evaluation"]["primary_metric"]: summary["gain"]},
        "error_count": 0,
        "crash_count": 0,
        "timeout_count": 0,
    }
    identity.postrun(completed, manifest, root, b)
    refs = rsi.record_comparison(candidate_id, comparison, completed)
    _safe_write(root, manifestpath, json.dumps(completed, sort_keys=True).encode())

    return {
        "candidate_id": candidate_id,
        **refs,
        "summary": summary,
        "holdout_access_ref": audit["audit_ref"],
        "completed_manifest_path": str(manifestpath),
    }
