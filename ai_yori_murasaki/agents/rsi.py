"""Real-LLM agent RSI loop (Purple DeepSeek driven).

This module is deliberately separate from the deterministic policy RSI
(``rsi_train`` / ``rsi_compare`` / ``rsi_eval``).  It runs the *actual* lab
agents through ``lab.run_demo`` with a real LLM client, records the run
evidence, asks the LLM to analyse the evidence and propose a candidate
policy bundle, then compares baseline vs candidate on validation/holdout
scenarios under identical conditions.

Public API (do not extend):

- ``train(runtime, staging_root, output_root, train_scenarios, seed=1,
  routes=None, llm_client=None)``
- ``compare(runtime, staging_root, training_root, validation_scenarios,
  holdout_scenarios, seed=1, routes=None, llm_client=None)``

Design rules enforced here:

- Freeze evaluation conditions before any run; ``evaluation_identity.preflight``
  must pass before comparison starts.
- Candidate bundles may only change ``system_prompt`` / ``task_template`` of
  the ``blue`` and ``purple`` (blue_repair) entries.  Everything else is
  immutable.
- No hidden fallback: LLM failure, empty output or invalid bundle is recorded
  and the call stops.
- All artifacts are written exclusively (no overwrite) under ``output_root``
  with symlink-ancestor rejection.
- Promotion readiness is reported only; this module never rewrites the
  default bundle.
"""
from __future__ import annotations

import datetime
import hashlib
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import evaluation_identity as identity
from .. import lab
from ..agents import policies, scenarios
from ..jsonio import DataError, canonical

_SCHEMA_VERSION = "1.1"
_PROFILE = "workflow"
_MAX_SCENARIOS = 32
_MAX_ARTIFACT_BYTES = 4 * 1024 * 1024
_MAX_LLM_OUTPUT_CHARS = 200_000
_MUTABLE_ROLES = ("blue", "purple")
_MUTABLE_FIELDS = ("system_prompt", "task_template")
_REQUIRED_PLACEHOLDERS = ("{surface}", "{findings}")


# ---------------------------------------------------------------------------
# small helpers
# ---------------------------------------------------------------------------

def _fail(msg: str, code: str = "input_invalid") -> None:
    raise DataError(code, msg)


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _sha256_file(path: Path) -> str:
    try:
        data = path.read_bytes()
    except OSError as exc:
        raise DataError("file unreadable: {}".format(path.name),
                        "input_invalid") from exc
    return _sha256_bytes(data)


def _canonical_bytes(obj: Any) -> bytes:
    return canonical(obj)


def _exclusive_write(root: Path, path: Path, data: bytes) -> None:
    """Write ``data`` to ``path`` exclusively; identical content is a no-op."""
    if len(data) > _MAX_ARTIFACT_BYTES:
        _fail("artifact exceeds size limit", "input_invalid")
    identity._assert_no_symlink_ancestors(root, path, "artifact")
    path.parent.mkdir(parents=True, exist_ok=True)
    identity._assert_no_symlink_ancestors(root, path, "artifact")
    try:
        with open(path, "xb") as fh:
            fh.write(data)
    except FileExistsError:
        if identity._read_bounded(path, "artifact") == data:
            return
        _fail("existing artifact differs", "freeze_modified")
    except OSError as exc:
        raise DataError("artifact write failed", "input_invalid") from exc


def _write_json(root: Path, path: Path, obj: Any) -> str:
    data = _canonical_bytes(obj)
    _exclusive_write(root, path, data)
    return _sha256_bytes(data)


def _read_json(root: Path, path: Path) -> Any:
    identity._assert_no_symlink_ancestors(root, path, "artifact")
    raw = identity._read_bounded(path, "artifact")
    try:
        return json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as exc:
        raise DataError("artifact invalid JSON", "input_invalid") from exc


def _assert_separated(output_root: Path, staging_root: Path) -> None:
    out = output_root.resolve()
    stg = staging_root.resolve()
    if out == stg or out in stg.parents or stg in out.parents:
        _fail("output_root must be separated from staging_root",
              "input_invalid")
    repo = Path(__file__).resolve().parent.parent.parent
    for guarded in (repo, repo / "workspace", repo / "trusted"):
        try:
            g = guarded.resolve()
        except OSError:
            continue
        if out == g or g in out.parents:
            _fail("output_root overlaps protected area", "input_invalid")


def _scenario_ids(items: Any) -> List[str]:
    if not isinstance(items, (list, tuple)) or not items:
        _fail("scenario list must be non-empty", "input_invalid")
    if len(items) > _MAX_SCENARIOS:
        _fail("too many scenarios", "input_invalid")
    out: List[str] = []
    for item in items:
        sid = item if isinstance(item, str) else (
            item.get("scenario_id") if isinstance(item, dict) else None)
        if not isinstance(sid, str) or not sid:
            _fail("invalid scenario id", "input_invalid")
        out.append(sid)
    if len(set(out)) != len(out):
        _fail("duplicate scenario id", "input_invalid")
    return out


def _scenario_fingerprint(sid: str) -> Dict[str, Any]:
    meta = scenarios.load_scenario(sid)
    target = Path(meta["target_path"])
    return {
        "scenario_id": sid,
        "split": meta["split"],
        "flaw_class": meta["flaw_class"],
        "target_sha256": _sha256_file(target),
    }


def _bundle_hash(bundle: Dict[str, Any]) -> str:
    return _sha256_bytes(_canonical_bytes(bundle))


# ---------------------------------------------------------------------------
# manifest / bindings
# ---------------------------------------------------------------------------

def _build_manifest(
    *,
    manifest_id: str,
    purpose: str,
    hypothesis: str,
    action_delta: str,
    negative_control: str,
    baseline_bundle: Dict[str, Any],
    candidate_bundle: Optional[Dict[str, Any]],
    scenario_fps: List[Dict[str, Any]],
    seed: int,
    thresholds: Dict[str, Any],
    budget: Dict[str, Any],
    stop_conditions: Dict[str, Any],
    model_version: str,
) -> Dict[str, Any]:
    dataset = {
        "dataset_id": "aym-agent-rsi-{}".format(manifest_id),
        "scenarios": scenario_fps,
    }
    dataset_sha = _sha256_bytes(_canonical_bytes(dataset))
    base_hash = _bundle_hash(baseline_bundle)
    cand_hash = _bundle_hash(candidate_bundle) if candidate_bundle else base_hash
    return {
        "schema_version": _SCHEMA_VERSION,
        "profile": _PROFILE,
        "manifest_id": manifest_id,
        "task_id": "agent-rsi",
        "owner": "RNA4219",
        "purpose": purpose,
        "status": "frozen",
        "freeze_date": datetime.date.today().isoformat(),
        "hypothesis": hypothesis,
        "action_delta": action_delta,
        "negative_control": negative_control,
        "identity": {
            "legacy": {
                "label": "baseline",
                "source_path": "baseline/bundle.json",
                "source_revision": "bundle:" + base_hash.split(":", 1)[1],
                "source_sha256": base_hash,
                "input_sha256": dataset_sha,
            },
            "candidate": {
                "label": "candidate",
                "source_path": "candidate/bundle.json",
                "source_revision": "bundle:" + cand_hash.split(":", 1)[1],
                "source_sha256": cand_hash,
                "input_sha256": dataset_sha,
            },
            "runner": {
                "id": "aym-agent-rsi-v1",
                "sha256": _sha256_file(Path(__file__).resolve()),
            },
        },
        "evaluation": {
            "dataset_id": dataset["dataset_id"],
            "dataset_sha256": dataset_sha,
            "measurement_unit": "task",
            "primary_metric": "success_gain",
            "model_version": model_version,
            "policy_version": "aym-agent-rsi-v1",
            "configuration": {
                "objective": hypothesis,
                "seed_pairs": [seed],
                "order_policy": "alternating",
                "budget": budget,
                "thresholds": thresholds,
                "stop_conditions": stop_conditions,
                "memory_refs": [],
                "memory_disabled": True,
            },
        },
        "external_mutations": {
            "registry_frozen": True,
            "submission_frozen": True,
        },
    }


def _build_bindings(manifest: Dict[str, Any]) -> Dict[str, Any]:
    """Bindings must reference real files under the artifact root."""
    return {
        "legacy_source": manifest["identity"]["legacy"]["source_path"],
        "candidate_source": manifest["identity"]["candidate"]["source_path"],
        "legacy_input": "dataset.json",
        "candidate_input": "dataset.json",
        "runner": "runner.py",
        "dataset": "dataset.json",
    }


def _write_runner(root: Path) -> None:
    """Persist a runner artifact whose hash matches the manifest runner."""
    source = Path(__file__).resolve()
    data = source.read_bytes()
    _exclusive_write(root, root / "runner.py", data)


def _write_dataset(root: Path, scenario_fps: List[Dict[str, Any]]) -> None:
    dataset = {
        "dataset_id": "aym-agent-rsi-dataset",
        "scenarios": scenario_fps,
    }
    _exclusive_write(root, root / "dataset.json", _canonical_bytes(dataset))


# ---------------------------------------------------------------------------
# run evidence
# ---------------------------------------------------------------------------

def _run_one(
    runtime: Any,
    staging_root: Path,
    sid: str,
    seed: int,
    bundle: Dict[str, Any],
    routes: Optional[Dict[str, Any]],
    llm_client: Any,
) -> Dict[str, Any]:
    meta = scenarios.load_scenario(sid)
    target_dir = Path(meta["target_path"]).parent
    try:
        result = lab.run_demo(
            runtime,
            target_dir,
            seed,
            llm_client=llm_client,
            routes=routes,
            scenario_id=sid,
            policy_bundle=bundle,
            failure_mode="record",
        )
    except Exception as exc:  # noqa: BLE001 - recorded, not swallowed
        return {
            "scenario_id": sid,
            "seed": seed,
            "status": "crash",
            "error": "{}: {}".format(type(exc).__name__, exc)[:300],
        }
    return {
        "scenario_id": sid,
        "seed": seed,
        "status": result.get("status"),
        "campaign_id": result.get("campaign_id"),
        "proposal_ref": result.get("proposal_ref"),
        "final_verdict": result.get("final_verdict"),
        "benign_checks": result.get("benign_checks"),
        "replay_statuses": result.get("replay_statuses"),
        "agent": result.get("agent"),
        "metrics": result.get("metrics"),
        "verification_journal_ref": result.get("verification_journal_ref"),
    }


def _evidence_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    completed = [r for r in records if r.get("status") == "review_pending"]
    failed = [r for r in records if r.get("status") not in
              ("review_pending", None)]
    return {
        "total": len(records),
        "completed": len(completed),
        "failed": len(failed),
        "completion_rate": (len(completed) / len(records)) if records else 0.0,
        "records": records,
    }


# ---------------------------------------------------------------------------
# LLM candidate proposal
# ---------------------------------------------------------------------------

def _proposal_prompt(evidence: Dict[str, Any], baseline: Dict[str, Any]) -> str:
    return (
        "You are analysing lab agent run evidence. Propose an improved policy "
        "bundle as strict JSON. You may ONLY change the 'system_prompt' and "
        "'task_template' fields of the 'blue' and 'purple' roles. "
        "task_template must keep the placeholders {surface} and {findings}. "
        "Do not change model_role, tools, budget or grants. "
        "Return JSON: {\"bundle\": <full bundle>, \"rationale\": <string>}.\n"
        "BASELINE_BUNDLE:\n" + json.dumps(baseline, sort_keys=True) + "\n"
        "EVIDENCE:\n" + json.dumps(evidence, sort_keys=True)
    )


def _call_llm(llm_client: Any, prompt: str) -> str:
    if llm_client is None:
        _fail("llm_client required for candidate proposal", "input_invalid")
    try:
        if hasattr(llm_client, "complete"):
            out = llm_client.complete(prompt)
        elif callable(llm_client):
            out = llm_client(prompt)
        else:
            _fail("llm_client has no callable interface", "input_invalid")
    except DataError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DataError(
            "llm communication failed: {}: {}".format(
                type(exc).__name__, exc)[:300], "input_invalid") from exc
    if not isinstance(out, str) or not out.strip():
        _fail("llm returned empty output", "input_invalid")
    if len(out) > _MAX_LLM_OUTPUT_CHARS:
        _fail("llm output exceeds size limit", "input_invalid")
    return out


def _parse_proposal(raw: str) -> Dict[str, Any]:
    try:
        obj = json.loads(raw)
    except ValueError as exc:
        raise DataError("llm output is not JSON", "input_invalid") from exc
    if not isinstance(obj, dict) or "bundle" not in obj:
        _fail("llm output missing bundle", "input_invalid")
    return obj


def _apply_candidate(
    baseline: Dict[str, Any], proposed: Dict[str, Any]
) -> Dict[str, Any]:
    """Merge only allowed fields; reject any other change."""
    if not isinstance(proposed, dict):
        _fail("proposed bundle must be object", "input_invalid")
    if set(proposed.keys()) != set(baseline.keys()):
        _fail("proposed bundle keys differ", "input_invalid")
    if proposed.get("bundle_id") == baseline.get("bundle_id"):
        _fail("candidate bundle_id must differ", "input_invalid")
    if proposed.get("version") == baseline.get("version"):
        _fail("candidate version must differ", "input_invalid")
    base_roles = baseline["roles"]
    prop_roles = proposed.get("roles")
    if not isinstance(prop_roles, dict) or set(prop_roles) != set(base_roles):
        _fail("proposed roles differ", "input_invalid")
    merged = deepcopy(baseline)
    merged["bundle_id"] = proposed["bundle_id"]
    merged["version"] = proposed["version"]
    for role, base_cfg in base_roles.items():
        prop_cfg = prop_roles[role]
        if not isinstance(prop_cfg, dict):
            _fail("proposed role config invalid", "input_invalid")
        if set(prop_cfg.keys()) != set(base_cfg.keys()):
            _fail("proposed role keys differ", "input_invalid")
        for key in base_cfg:
            if key in _MUTABLE_FIELDS and role in _MUTABLE_ROLES:
                continue
            if prop_cfg[key] != base_cfg[key]:
                _fail("immutable field changed: {}.{}".format(role, key),
                      "input_invalid")
        if role in _MUTABLE_ROLES:
            for key in _MUTABLE_FIELDS:
                value = prop_cfg[key]
                if not isinstance(value, str) or not value:
                    _fail("mutable field empty", "input_invalid")
                merged["roles"][role][key] = value
            for ph in _REQUIRED_PLACEHOLDERS:
                if ph not in merged["roles"][role]["task_template"]:
                    _fail("task_template missing {}".format(ph),
                          "input_invalid")
    return policies.validate_bundle(merged)


# ---------------------------------------------------------------------------
# public API: train
# ---------------------------------------------------------------------------

def train(
    runtime: Any,
    staging_root: Path,
    output_root: Path,
    train_scenarios: Any,
    seed: int = 1,
    routes: Optional[Dict[str, Any]] = None,
    llm_client: Any = None,
) -> Dict[str, Any]:
    staging_root = Path(staging_root)
    output_root = Path(output_root)
    _assert_separated(output_root, staging_root)
    identity._assert_no_symlink_ancestors(output_root, output_root,
                                         "output_root")
    output_root.mkdir(parents=True, exist_ok=True)

    sids = _scenario_ids(train_scenarios)
    fps = [_scenario_fingerprint(s) for s in sids]
    for fp in fps:
        if fp["split"] != "train":
            _fail("train scenario not in train split", "input_invalid")

    baseline = policies.load_default_bundle()
    manifest_id = "agent-rsi-train-" + _sha256_bytes(
        _canonical_bytes({"sids": sids, "seed": seed,
                          "base": _bundle_hash(baseline)})
    ).split(":", 1)[1][:16]

    thresholds = {
        "success_gain_min": 0.0,
        "no_benign_regression": True,
        "max_safety_violations": 0,
    }
    budget = {"max_pairs": len(sids), "max_calls": len(sids) * 2}
    stop_conditions = {"max_iterations": 1}

    manifest = _build_manifest(
        manifest_id=manifest_id,
        purpose="experiment",
        hypothesis="Evidence-driven prompt improvement raises success rate",
        action_delta="Change blue/purple prompts only",
        negative_control="Benign checks must not regress",
        baseline_bundle=baseline,
        candidate_bundle=None,
        scenario_fps=fps,
        seed=seed,
        thresholds=thresholds,
        budget=budget,
        stop_conditions=stop_conditions,
        model_version="llm:purple-deepseek",
    )

    # Persist baseline bundle, runner, dataset and manifest, then preflight.
    _write_json(output_root, output_root / "baseline" / "bundle.json",
                baseline)
    _write_runner(output_root)
    _write_dataset(output_root, fps)
    manifest_path = output_root / "manifest.json"
    _write_json(output_root, manifest_path, manifest)
    bindings = _build_bindings(manifest)
    identity.preflight(manifest, output_root, bindings)

    # Baseline runs.
    records: List[Dict[str, Any]] = []
    for sid in sids:
        records.append(_run_one(runtime, staging_root, sid, seed, baseline,
                                routes, llm_client))
    evidence = _evidence_summary(records)
    _write_json(output_root, output_root / "evidence" / "baseline.json",
                evidence)

    # Candidate proposal via LLM.
    prompt = _proposal_prompt(evidence, baseline)
    raw = _call_llm(llm_client, prompt)
    _write_json(output_root, output_root / "evidence" / "llm_raw.json",
                {"raw": raw})
    proposal = _parse_proposal(raw)
    candidate = _apply_candidate(baseline, proposal["bundle"])
    cand_hash = _bundle_hash(candidate)
    _write_json(output_root, output_root / "candidate" / "bundle.json",
                candidate)

    # Freeze candidate.
    freeze = {
        "manifest_id": manifest_id,
        "candidate_sha256": cand_hash,
        "baseline_sha256": _bundle_hash(baseline),
        "scenario_fingerprints": fps,
        "seed": seed,
        "rationale": proposal.get("rationale", ""),
        "artifact_root": str(output_root),
        "bindings": bindings,
        "manifest_ref": str(manifest_path),
    }
    freeze_path = output_root / "freeze.json"
    _write_json(output_root, freeze_path, freeze)

    # Reject train/val/holdout overlap and same-target duplicates.
    all_splits = scenarios.splits()
    train_set = set(all_splits.get("train", []))
    for sid in sids:
        if sid not in train_set:
            _fail("scenario not in train split", "input_invalid")
    seen_targets: Dict[str, str] = {}
    for fp in fps:
        key = fp["target_sha256"]
        if key in seen_targets:
            _fail("duplicate target across scenarios", "input_invalid")
        seen_targets[key] = fp["scenario_id"]

    return {
        "manifest_id": manifest_id,
        "manifest_path": str(manifest_path),
        "freeze_path": str(freeze_path),
        "candidate_sha256": cand_hash,
        "baseline_sha256": _bundle_hash(baseline),
        "evidence": evidence,
        "status": "trained",
    }


# ---------------------------------------------------------------------------
# public API: compare
# ---------------------------------------------------------------------------

def _load_frozen(training_root: Path) -> Dict[str, Any]:
    freeze = _read_json(training_root, training_root / "freeze.json")
    manifest = _read_json(training_root, training_root / "manifest.json")
    baseline = _read_json(training_root, training_root / "baseline" /
                          "bundle.json")
    candidate = _read_json(training_root, training_root / "candidate" /
                           "bundle.json")
    if _bundle_hash(baseline) != freeze["baseline_sha256"]:
        _fail("baseline bundle modified after freeze", "freeze_modified")
    if _bundle_hash(candidate) != freeze["candidate_sha256"]:
        _fail("candidate bundle modified after freeze", "freeze_modified")
    if manifest.get("status") != "frozen":
        _fail("manifest not frozen", "freeze_modified")
    return {
        "freeze": freeze,
        "manifest": manifest,
        "baseline": baseline,
        "candidate": candidate,
    }


def _holdout_marker(training_root: Path, holdout_ids: List[str]) -> Path:
    digest = _sha256_bytes(_canonical_bytes(sorted(holdout_ids)))
    return training_root / "holdout" / (digest.split(":", 1)[1] + ".started")


def _evaluate_pairs(
    pairs: List[Dict[str, Any]],
    thresholds: Dict[str, Any],
) -> Dict[str, Any]:
    missing = [p for p in pairs if p.get("baseline") is None
               or p.get("candidate") is None]
    if missing:
        return {"promotion_ready": False, "reason": "missing_pairs",
                "missing": len(missing)}
    base_ok = sum(1 for p in pairs
                  if p["baseline"].get("status") == "review_pending")
    cand_ok = sum(1 for p in pairs
                  if p["candidate"].get("status") == "review_pending")
    gain = (cand_ok - base_ok) / len(pairs) if pairs else 0.0
    benign_regress = any(
        (p["candidate"].get("benign_checks") or {}) !=
        (p["baseline"].get("benign_checks") or {})
        for p in pairs)
    safety = sum(1 for p in pairs
                 if (p["candidate"].get("final_verdict") or {})
                 .get("safety_violations", 0) > 0)
    ready = (
        gain > thresholds.get("success_gain_min", 0.0)
        and not (thresholds.get("no_benign_regression") and benign_regress)
        and safety <= thresholds.get("max_safety_violations", 0)
    )
    return {
        "promotion_ready": ready,
        "gain": gain,
        "benign_regression": benign_regress,
        "safety_violations": safety,
        "pairs": len(pairs),
    }


def compare(
    runtime: Any,
    staging_root: Path,
    training_root: Path,
    validation_scenarios: Any,
    holdout_scenarios: Any,
    seed: int = 1,
    routes: Optional[Dict[str, Any]] = None,
    llm_client: Any = None,
) -> Dict[str, Any]:
    staging_root = Path(staging_root)
    training_root = Path(training_root)
    _assert_separated(training_root, staging_root)
    identity._assert_no_symlink_ancestors(training_root, training_root,
                                         "training_root")

    frozen = _load_frozen(training_root)
    manifest = frozen["manifest"]
    bindings = frozen["freeze"]["bindings"]
    identity.preflight(manifest, training_root, bindings)

    val_ids = _scenario_ids(validation_scenarios)
    hold_ids = _scenario_ids(holdout_scenarios)
    if set(val_ids) & set(hold_ids):
        _fail("validation/holdout overlap", "input_invalid")
    train_ids = {fp["scenario_id"]
                 for fp in frozen["freeze"]["scenario_fingerprints"]}
    if (set(val_ids) | set(hold_ids)) & train_ids:
        _fail("validation/holdout overlaps train", "input_invalid")

    # Exclusive holdout start marker.
    marker = _holdout_marker(training_root, hold_ids)
    _exclusive_write(training_root, marker,
                     _canonical_bytes({"holdout": sorted(hold_ids),
                                       "seed": seed}))

    baseline = frozen["baseline"]
    candidate = frozen["candidate"]
    thresholds = manifest["evaluation"]["configuration"]["thresholds"]

    pairs: List[Dict[str, Any]] = []
    all_ids = val_ids + hold_ids
    for idx, sid in enumerate(all_ids):
        order = ["baseline", "candidate"] if idx % 2 == 0 else \
            ["candidate", "baseline"]
        results: Dict[str, Any] = {}
        for side in order:
            bundle = baseline if side == "baseline" else candidate
            results[side] = _run_one(runtime, staging_root, sid, seed,
                                     bundle, routes, llm_client)
        pairs.append({
            "scenario_id": sid,
            "seed": seed,
            "order": order,
            "baseline": results["baseline"],
            "candidate": results["candidate"],
            "memory_disabled": True,
        })

    summary = _evaluate_pairs(pairs, thresholds)
    comparison = {
        "manifest_id": manifest["manifest_id"],
        "candidate_sha256": frozen["freeze"]["candidate_sha256"],
        "pairs": pairs,
        "summary": summary,
        "memory_disabled": True,
    }
    result_path = training_root / "results" / "comparison.json"
    result_sha = _write_json(training_root, result_path, comparison)

    completed = deepcopy(manifest)
    completed["status"] = "completed"
    completed["outcome"] = {
        "artifact_path": result_path.relative_to(training_root).as_posix(),
        "artifact_sha256": result_sha,
        "records": len(pairs),
        "metrics": {"success_gain": summary.get("gain", 0.0)},
        "error_count": 0,
        "crash_count": sum(1 for p in pairs
                           if p["candidate"].get("status") == "crash"),
        "timeout_count": 0,
    }
    identity.postrun(completed, manifest, training_root, bindings)
    completed_path = training_root / "results" / "completed-manifest.json"
    _write_json(training_root, completed_path, completed)

    return {
        "manifest_id": manifest["manifest_id"],
        "comparison_path": str(result_path),
        "completed_manifest_path": str(completed_path),
        "summary": summary,
        "promotion_ready": summary.get("promotion_ready", False),
        "status": "compared",
    }
