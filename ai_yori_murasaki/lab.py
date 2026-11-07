"""Agent-driven disposable local Docker lab orchestration.

The normal execution path runs LLM agents through the brokered tool loop:

- Red agent: probes the target from its own decisions and reports the
  unauthorized access it reproduced (with evidence references).
- Blue agent (blind): sees only sanitized telemetry and submits findings;
  it never sees the red plan, the scenario answer or Oracle expectations.
- Purple agent (collaborative, after the blind phase is closed): reads the
  staged target code, identifies the root cause and proposes a full patch
  which is validated immediately (syntax + post-fix behavior unit check).
- The orchestrator applies the agent's patch in the disposable lab and the
  non-LLM Oracle verifies the exploit is blocked and benign behavior is
  preserved (fixed replay + benign regression), then the campaign stops at
  the review gate.

Failure semantics are fail-closed: LLM failures, budget exhaustion and
invalid actions abort the campaign; there is no silent fallback to a
fixed "correct answer" patch.
"""
from __future__ import annotations

import ast
import json
import shutil
import sys
import time
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import parse_qs, urlsplit

from .worker import DockerLab, stage_worker, ObservationBroker
from .oracle import Oracle, build_final_verdict
from .jsonio import digest
from .adapters.llm import LLMClient
from .adapters.base import HttpBroker
from . import lab_metrics
from .p2 import route_model, purple_loop, tune_sigma
from .agents import (AgentLoop, load_default_bundle, load_scenario,
                     render_task, validate_bundle)

_ASSETS = Path(__file__).resolve().parent / "lab_assets"
_WORKER_ASSET = _ASSETS / "worker.py"

_TELEMETRY_FIELDS = ("timestamp", "method", "path", "status", "role")


class _LabFailure(Exception):
    """Fail-closed lab outcome with phase and attribution."""

    def __init__(self, phase: str, reason: str, attribution: str) -> None:
        super().__init__("{}: {}".format(phase, reason))
        self.phase = phase
        self.reason = reason
        self.attribution = attribution


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _utc_plus(hours: int) -> str:
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).strftime(
        "%Y-%m-%dT%H:%M:%SZ")


def _new_id(prefix: str) -> str:
    return "{}-{}".format(prefix, uuid.uuid4())


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise RuntimeError(msg)


def _finish(runtime: Any, cid: str, rid: str) -> None:
    runtime.finish_run(rid, "completed")
    runtime.seal_run(cid, rid)
    runtime.store.seal_checkpoint(cid, rid, "rna4219")


def _verify(runtime: Any, cid: str, rid: str) -> Dict[str, Any]:
    chain = runtime.store.verify_chain(rid)
    cp = runtime.store.verify_checkpoint(cid, rid)
    both = chain.get("result") == "PASS" and cp.get("result") == "PASS"
    result = "PASS" if both else "FAIL"
    journal_ref = runtime.store.append_verification({
        "verify_id": rid,
        "campaign_id": cid,
        "run_id": rid,
        "checked_at": _utc_now(),
        "result": result,
        "chain": chain,
        "checkpoint": cp,
    })
    _require(both, "verification failed for run {} (journal {})".format(
        rid, journal_ref))
    return {"run_id": rid, "chain": chain, "checkpoint": cp,
            "result": result}


def _extract_observations(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    obs = result.get("observations")
    _require(isinstance(obs, list), "worker result missing observations list")
    return obs


def _extract_checks(result: Dict[str, Any]) -> List[Dict[str, Any]]:
    checks = result.get("checks")
    _require(isinstance(checks, list), "worker result missing checks list")
    return checks


def _all_passed(checks: List[Dict[str, Any]]) -> bool:
    return bool(checks) and all(c.get("passed") is True for c in checks)


def _is_unauthorized_row(row: Dict[str, Any]) -> bool:
    """A successful access to the protected record by a non-admin role."""
    if not isinstance(row, dict):
        return False
    if row.get("method") != "GET" or row.get("status") != 200:
        return False
    path = row.get("path")
    role = row.get("role")
    if not isinstance(path, str):
        return False
    return (path.split("?", 1)[0] == "/record"
            and isinstance(role, str) and role != "admin")


def _make_probe_executor(lab: Any, red_stage: Path, runtime: Any,
                         red_rid: str):
    """Build the broker-facing probe executor for the red agent.

    The agent sees a sanitized observation (status, timing, requested
    role); the protected record body (Oracle canary) is never returned to
    the agent context. Each probe is submitted to the evidence chain via
    the role-bound ObservationBroker.
    """
    broker = ObservationBroker(runtime, red_rid, "red")
    rows: List[Dict[str, Any]] = []

    def probe(path: str) -> Dict[str, Any]:
        result = lab.run_worker(red_stage, "red", "probe_path",
                                args=("--path", path))
        obs_list = _extract_observations(result)
        if not obs_list or not isinstance(obs_list[0], dict):
            raise RuntimeError("probe_path returned no observation")
        full = dict(obs_list[0])
        sanitized = {k: v for k, v in full.items()
                     if k in ("status", "method", "path", "timestamp",
                              "elapsed", "error")}
        query = parse_qs(urlsplit(path).query)
        role_values = query.get("role", [])
        sanitized["role"] = role_values[0] if role_values else ""
        sub = broker.submit_observation({
            "kind": "http_response", "data": sanitized,
            "observed_at": _utc_now()})
        rows.append({"ref": sub["data_ref"], "data": dict(sanitized)})
        sanitized["observation_ref"] = sub["data_ref"]
        return sanitized

    return probe, rows


def _telemetry_rows_with_refs(lab: Any, runtime: Any, rid: str,
                              kind_role: str):
    """Submit sanitized telemetry rows and return (rows, refs)."""
    telemetry = lab.telemetry()
    broker = ObservationBroker(runtime, rid, kind_role)
    rows: List[Dict[str, Any]] = []
    refs: List[str] = []
    for row in telemetry:
        if not isinstance(row, dict):
            continue
        data = {k: row.get(k) for k in _TELEMETRY_FIELDS if k in row}
        sub = broker.submit_observation({
            "kind": "telemetry", "data": data,
            "observed_at": _utc_now()})
        refs.append(sub["data_ref"])
        rows.append({"ref": sub["data_ref"], "data": data})
    return rows, refs


def _unit_validator_factory(lab: Any, jobdir: Path):
    """Validate a proposed patch against the post-fix behavior contract."""

    def validate(new_content: str) -> Dict[str, Any]:
        check_dir = jobdir / ("unitcheck-{}".format(_new_id("u")))
        check_dir.mkdir(parents=False, exist_ok=False)
        shutil.copy2(str(_WORKER_ASSET), str(check_dir / "worker.py"))
        (check_dir / "target.py").write_text(new_content, encoding="utf-8")
        static_ok = True
        try:
            ast.parse(new_content)
        except SyntaxError:
            static_ok = False
        if not static_ok:
            return {"static_passed": False, "unit_checks": [],
                    "unit_passed": False}
        try:
            result = lab.run_worker(check_dir, "oracle", "unit_authz")
        except Exception as exc:  # lab unavailable -> not verified
            return {"static_passed": True, "unit_checks": [],
                    "unit_passed": None,
                    "error": "{}: {}".format(type(exc).__name__,
                                              exc)[:200]}
        checks = result.get("checks")
        if not isinstance(checks, list) or not checks:
            return {"static_passed": True, "unit_checks": [],
                    "unit_passed": None}
        passed = (result.get("status") == "ok"
                  and all(isinstance(c, dict) and c.get("passed") is True
                          for c in checks))
        return {"static_passed": True, "unit_checks": checks,
                "unit_passed": passed}

    return validate


def _findings_of_run(runtime: Any, cid: str, run_id: str) -> List[dict]:
    findings: List[dict] = []
    for ref in runtime.list_records(cid, "finding"):
        try:
            obj = runtime.store.get_object(ref)
        except Exception:
            continue
        if isinstance(obj, dict) and obj.get("run_id") == run_id:
            findings.append(obj)
    return findings


def _primary_finding(findings: List[dict],
                     telemetry_rows: List[dict]) -> Optional[dict]:
    unauthorized_refs = [row["ref"] for row in telemetry_rows
                         if _is_unauthorized_row(row.get("data", {}))]
    for finding in findings:
        refs = finding.get("references")
        if isinstance(refs, list) and any(r in refs
                                          for r in unauthorized_refs):
            return finding
    return findings[0] if findings else None


def run_demo(runtime: Any, staging_root: Path, seed: int = 1,
             llm_client: Optional[LLMClient] = None,
             routes: Optional[Dict[str, Any]] = None, *,
             scenario_id: str = "authz-flag-false",
             policy_bundle: Optional[Dict[str, Any]] = None,
             lab_factory: Optional[Any] = None,
             failure_mode: str = "raise") -> Dict[str, Any]:
    """Run one agent-driven lab campaign end to end.

    ``failure_mode="raise"`` (default) aborts the campaign on failure;
    ``failure_mode="record"`` returns a structured failure result instead
    (used by the agent RSI executor to measure failures as outcomes).
    """
    if failure_mode not in ("raise", "record"):
        raise ValueError("failure_mode must be raise or record")
    if llm_client is None:
        llm_client = LLMClient(HttpBroker(runtime.policy), timeout_ms=90000,
                               retry_cap=0)
    _require(isinstance(staging_root, Path), "staging_root must be a Path")
    _require(staging_root.is_absolute(), "staging_root must be absolute")
    _require(staging_root.is_dir(), "staging_root must exist")

    scenario = load_scenario(scenario_id)
    bundle = policy_bundle if policy_bundle is not None else \
        load_default_bundle()
    validate_bundle(bundle)

    jobdir = staging_root / _new_id("job")
    jobdir.mkdir(parents=False, exist_ok=False)
    target_dir = jobdir / "target"
    target_dir.mkdir()
    shutil.copy2(str(scenario["target_path"]), str(target_dir / "target.py"))
    shutil.copy2(str(_WORKER_ASSET), str(target_dir / "worker.py"))

    scope = {"targets": [{"kind": "lab-target",
                           "identifier": "local-http-lab"}],
             "max_targets": 1}
    authorization = {"operator_id": "rna4219", "granted_at": _utc_now(),
                     "expires_at": _utc_plus(2),
                     "scope_digest": digest(scope)}
    campaign = runtime.create_campaign("INT-AYM-LAB", scope, authorization,
                                       str(uuid.uuid4()))
    cid = campaign["campaign_id"]
    runtime.transition(cid, "running")

    runs: List[str] = []
    lab: Optional[DockerLab] = None
    current_rid: Optional[str] = None
    try:
        lab = (lab_factory or DockerLab)(jobdir.resolve())
        lab.__enter__()
        lab.start_target(target_dir)
        _require(lab.wait_healthy() is True, "target must be healthy")

        # ---- Red phase (agent-driven) ----
        red_run = runtime.start_run(cid, "red", seed, str(uuid.uuid4()))
        red_rid = red_run["run_id"]
        current_rid = red_rid
        runs.append(red_rid)
        red_stage = stage_worker(jobdir, "red")
        probe_executor, probe_rows = _make_probe_executor(
            lab, red_stage, runtime, red_rid)
        red_task = render_task(bundle["roles"]["red"],
                               {"surface": scenario["surface"]})
        red_loop = AgentLoop(
            llm_client=llm_client, runtime=runtime, campaign_id=cid,
            run_id=red_rid, role="red", role_cfg=bundle["roles"]["red"],
            routes=routes, seed=seed).run(
                task=red_task, probe_executor=probe_executor)
        if red_loop["status"] != "completed":
            raise _LabFailure("red", "red agent loop status: {}".format(
                red_loop["status"]), "candidate")
        red_report = red_loop.get("report") or {}
        if red_report.get("unauthorized_access") is not True:
            raise _LabFailure("red", "red agent did not confirm "
                              "unauthorized access", "candidate")
        red_plan_ref = runtime.store.put_object({
            "phase": "red", "report": red_report,
            "loop": {k: red_loop[k] for k in
                     ("status", "stop_reason", "turns", "llm_calls",
                      "actions")},
        })
        runtime.record_event(red_rid, "red_plan", "llm",
                             evidence_refs=(red_plan_ref,),
                             extensions={"record_type": "agent_report",
                                         "model": red_loop.get("model")})
        _finish(runtime, cid, red_rid)
        current_rid = None

        # ---- Exploit verification (non-LLM oracle) ----
        # Telemetry is read orchestrator-side (the target's own request
        # log). The blue phase later receives the same rows through the
        # role-bound observation broker with fresh references.
        red_telemetry = lab.telemetry()
        unauthorized = [row for row in red_telemetry
                        if _is_unauthorized_row(row)]
        if not unauthorized:
            raise _LabFailure("red", "no unauthorized access found in "
                              "target telemetry", "candidate")
        exploit_run = runtime.start_run(cid, "verify", seed, str(uuid.uuid4()))
        exploit_rid = exploit_run["run_id"]
        current_rid = exploit_rid
        runs.append(exploit_rid)
        telemetry_ref = runtime.store.put_object(
            {"telemetry": red_telemetry})
        runtime.record_event(exploit_rid, "state_captured", "orchestrator",
                             evidence_refs=(telemetry_ref,),
                             extensions={"record_type": "telemetry_snapshot"})
        first_row = unauthorized[0]
        actual_ref = runtime.store.put_object({
            "method": "GET", "path": first_row.get("path"),
            "status": first_row.get("status")})
        exploit_predicate = Oracle().make_predicate(
            "red-exploit", "http_status", actual_ref,
            {"method": "GET", "path": first_row.get("path"),
             "status": first_row.get("status")})
        evaluated_exploit = runtime.record_oracle(exploit_rid,
                                                  exploit_predicate)
        if evaluated_exploit.get("verdict") != "PASS":
            raise _LabFailure("red", "exploit predicate must PASS "
                              "(attack reproduced)", "candidate")
        _finish(runtime, cid, exploit_rid)
        current_rid = None

        # ---- Blue phase (blind, agent-driven) ----
        blue_run = runtime.start_run(cid, "blue_blind", seed,
                                      str(uuid.uuid4()))
        blue_rid = blue_run["run_id"]
        current_rid = blue_rid
        runs.append(blue_rid)
        blue_rows, blue_refs = _telemetry_rows_with_refs(
            lab, runtime, blue_rid, "blue")
        _require(blue_rows, "telemetry must not be empty for the blue phase")
        blue_entries = [
            {"ref": row["ref"], **row["data"]} for row in blue_rows]
        blue_task = render_task(bundle["roles"]["blue"], {
            "telemetry": json.dumps(blue_entries, ensure_ascii=False)})
        blue_loop = AgentLoop(
            llm_client=llm_client, runtime=runtime, campaign_id=cid,
            run_id=blue_rid, role="blue", role_cfg=bundle["roles"]["blue"],
            routes=routes, seed=seed).run(
                task=blue_task, initial_refs=blue_refs)
        blue_detected_at = time.time()
        if blue_loop["status"] != "completed":
            raise _LabFailure("blue", "blue agent loop status: {}".format(
                blue_loop["status"]), "candidate")
        _finish(runtime, cid, blue_rid)
        current_rid = None
        runtime.transition(cid, "blind_completed")

        # ---- Blind detection verification (non-LLM oracle) ----
        findings = _findings_of_run(runtime, cid, blue_rid)
        blind_run = runtime.start_run(cid, "verify", seed, str(uuid.uuid4()))
        blind_rid = blind_run["run_id"]
        current_rid = blind_rid
        runs.append(blind_rid)
        blind = Oracle().blind_detection_check(blue_rows, findings,
                                               runtime.store)
        runtime.record_event(blind_rid, "state_captured", "orchestrator",
                             evidence_refs=(blind["report_ref"],),
                             extensions={"record_type": "blind_detection"})
        evaluated_blind = runtime.record_oracle(
            blind_rid, blind["predicate"], evaluated_run_id=blue_rid)
        if evaluated_blind.get("verdict") != "PASS":
            uncovered = blind["report"].get("uncovered") or []
            raise _LabFailure("blue", "blind detection incomplete: "
                              "{} uncovered row(s)".format(len(uncovered)),
                              "candidate")
        _finish(runtime, cid, blind_rid)
        current_rid = None
        runtime.transition(cid, "purple_open")

        # ---- Purple phase (collaborative investigation + patch) ----
        purple_run = runtime.start_run(cid, "purple", seed, str(uuid.uuid4()))
        purple_rid = purple_run["run_id"]
        current_rid = purple_rid
        runs.append(purple_rid)
        finding_summaries = [
            {"finding_id": f.get("finding_id"),
             "severity": f.get("severity"),
             "title": f.get("title"),
             "path": f.get("location", {}).get("path"),
             "references": f.get("references")}
            for f in findings]
        purple_task = render_task(bundle["roles"]["purple"], {
            "surface": scenario["surface"],
            "findings": json.dumps(finding_summaries, ensure_ascii=False)})
        finding_refs = [runtime.store.put_object(f) for f in findings]
        unit_validator = _unit_validator_factory(lab, jobdir)
        purple_loop = AgentLoop(
            llm_client=llm_client, runtime=runtime, campaign_id=cid,
            run_id=purple_rid, role="purple",
            role_cfg=bundle["roles"]["purple"], routes=routes,
            seed=seed).run(
                task=purple_task, target_dir=target_dir,
                unit_validator=unit_validator,
                initial_refs=finding_refs + list(blue_refs))
        if purple_loop["status"] != "completed":
            raise _LabFailure("purple", "purple agent loop status: {}".format(
                purple_loop["status"]), "candidate")
        purple_report = purple_loop.get("report") or {}
        proposal_ref = purple_report.get("proposal_ref")
        proposal = runtime.store.get_object(proposal_ref)
        _require(isinstance(proposal, dict), "patch proposal missing")
        gap_ref = runtime.store.put_object({
            "phase": "purple", "report": purple_report,
            "root_cause": proposal.get("root_cause"),
            "rationale": proposal.get("rationale"),
            "loop": {k: purple_loop[k] for k in
                     ("status", "stop_reason", "turns", "llm_calls",
                      "actions")},
        })
        runtime.record_event(purple_rid, "gap_analyzed", "llm",
                             evidence_refs=(gap_ref, proposal_ref),
                             extensions={"record_type": "agent_report",
                                         "model": purple_loop.get("model"),
                                         "generated_by": "llm"})
        _finish(runtime, cid, purple_rid)
        current_rid = None
        runtime.transition(cid, "remediation_in_progress")

        # ---- Remediation phase (apply the agent's patch) ----
        rem_run = runtime.start_run(cid, "remediation", seed,
                                    str(uuid.uuid4()))
        rem_rid = rem_run["run_id"]
        current_rid = rem_rid
        runs.append(rem_rid)
        lab.stop_target()
        target_file = target_dir / "target.py"
        after = proposal["after"]
        target_file.write_text(after, encoding="utf-8")
        try:
            ast.parse(after)
            static_ok = True
        except SyntaxError:
            static_ok = False
        static_report = {
            "tool": "python-ast",
            "checks": [{"name": "python-ast", "passed": static_ok}],
        }
        static_ref = runtime.store.put_object(static_report)
        if not static_ok:
            raise _LabFailure("purple", "patched target failed ast.parse",
                              "candidate")
        lab.start_target(target_dir)
        _require(lab.wait_healthy() is True, "patched target must be healthy")
        unit_result = lab.run_worker(target_dir.resolve(), "oracle",
                                     "unit_authz")
        unit_checks = _extract_checks(unit_result)
        if not _all_passed(unit_checks):
            raise _LabFailure("purple",
                              "patched target failed post-fix behavior "
                              "check", "candidate")
        unit_ref = runtime.store.put_object(unit_result)
        runtime.record_event(rem_rid, "state_captured", "orchestrator",
                             evidence_refs=(proposal_ref,),
                             extensions={"path": "target.py",
                                         "generated_by": "llm"})
        runtime.record_event(rem_rid, "static_scan_result", "orchestrator",
                             evidence_refs=(static_ref,),
                             extensions={"tool": "python-ast"})
        runtime.record_event(rem_rid, "unit_result", "orchestrator",
                             evidence_refs=(unit_ref,),
                             extensions={"checks": len(unit_checks)})
        _finish(runtime, cid, rem_rid)
        current_rid = None

        # ---- Reevaluation phase (fixed replay + benign regression) ----
        reeval_run = runtime.start_run(cid, "reevaluation", seed,
                                       str(uuid.uuid4()))
        reeval_rid = reeval_run["run_id"]
        current_rid = reeval_rid
        runs.append(reeval_rid)
        replay_result = lab.run_worker(red_stage, "red", "red")
        replay_obs = _extract_observations(replay_result)
        if len(replay_obs) != 3:
            raise _LabFailure("purple", "replay must yield 3 observations",
                              "candidate")
        replay_statuses = [o.get("status") for o in replay_obs]
        if replay_statuses != [403, 403, 403]:
            raise _LabFailure("purple", "replay statuses must be "
                              "403,403,403 but were {}".format(
                                  replay_statuses), "candidate")
        replay_checks = _extract_checks(replay_result)
        if len(replay_checks) != 3:
            raise _LabFailure("purple", "replay must yield 3 checks",
                              "candidate")
        replay_raw_ref = runtime.store.put_object(replay_result)

        benign_stage = stage_worker(jobdir, "oracle")
        benign_result = lab.run_worker(benign_stage, "oracle", "benign")
        benign_checks = _extract_checks(benign_result)
        benign_passed = _all_passed(benign_checks)
        benign_report = {
            "campaign_id": cid, "run_id": reeval_rid,
            "checks": benign_checks, "passed": benign_passed}
        benign_ref = runtime.store.put_object(benign_report)

        integration_checks: List[Dict[str, Any]] = []
        for idx, status in enumerate(replay_statuses):
            integration_checks.append({
                "name": "replay-{}".format(idx),
                "expected": 403,
                "actual": status,
                "passed": status == 403,
            })
        integration_checks.append({
            "name": "patched_health",
            "expected": True,
            "actual": True,
            "passed": True,
        })
        integration_report = {
            "patched_health": True,
            "replay_statuses": replay_statuses,
            "checks": integration_checks,
            "passed": _all_passed(integration_checks),
        }
        integration_ref = runtime.store.put_object(integration_report)

        expected = {"method": "GET", "path": "/record?role=guest",
                    "status": 200}
        actual_obs = {"method": "GET", "path": "/record?role=guest",
                      "status": replay_statuses[0]}
        actual_obs_ref = runtime.store.put_object(actual_obs)
        replay_predicate = Oracle().make_predicate(
            "replay", "http_status", actual_obs_ref, expected)
        evaluated_replay = runtime.record_oracle(reeval_rid, replay_predicate)
        if (evaluated_replay.get("verdict") != "FAIL"
                or evaluated_replay.get("reason_code")
                != "exploit_not_succeeded"):
            raise _LabFailure("purple", "replay predicate must FAIL with "
                              "exploit_not_succeeded", "candidate")
        evaluated_ref = runtime.store.put_object(evaluated_replay)

        primary = _primary_finding(findings, blue_rows)
        if primary is None:
            raise _LabFailure("blue", "no finding to remediate", "candidate")
        remediation_id = _new_id("rem")
        remediation = {
            "schema_version": "1.0.0",
            "remediation_id": remediation_id,
            "finding_id": primary.get("finding_id"),
            "campaign_id": cid,
            "run_id": blue_rid,
            "patch_bundle_ref": proposal_ref,
            "static_scan_result": {"passed": static_ok, "ref": static_ref},
            "unit_result": {"passed": _all_passed(unit_checks),
                            "ref": unit_ref},
            "integration_result": {"passed": _all_passed(integration_checks),
                                   "ref": integration_ref},
            "oracle_replay_result": {
                "verdict": evaluated_replay["verdict"],
                "reason_code": evaluated_replay["reason_code"],
                "ref": evaluated_ref},
            "benign_regression_result": {"passed": benign_passed,
                                         "ref": benign_ref},
            "status": "review_pending",
        }
        runtime.register_record("remediation", remediation, run_id=reeval_rid)

        verdict = build_final_verdict(cid, reeval_rid, evaluated_replay,
                                       "completed", runtime.store,
                                       remediation_id=remediation_id,
                                       test_report_refs=(benign_ref,))
        runtime.register_record("final_verdict", verdict, run_id=reeval_rid,
                                oracle_evaluation=evaluated_replay)
        # ---- Sigma tuning (train only, no external client) ----
        red_obs_rows: List[Dict[str, Any]] = []
        for row in blue_rows:
            data = row.get("data", {})
            if _is_unauthorized_row(data):
                red_obs_rows.append({
                    "status": data.get("status"),
                    "path": data.get("path"),
                    "expected_detection": True,
                })
        benign_obs = benign_result.get("observations")
        benign_obs_rows: List[Dict[str, Any]] = []
        if isinstance(benign_obs, list):
            for obs in benign_obs:
                if isinstance(obs, dict):
                    benign_obs_rows.append({
                        "status": obs.get("status"),
                        "path": obs.get("path"),
                        "expected_detection": False,
                    })
        tune_result = tune_sigma({
            "id": "local-authz", "title": "local authz",
            "logsource": {"category": "webserver"},
            "detection": {"selection": {"status": 200, "path": "/health"},
                          "condition": "selection"},
        }, red_obs_rows + benign_obs_rows)
        tune_ref = runtime.store.put_object(tune_result)
        runtime.record_event(reeval_rid, "sigma_drafted", "orchestrator",
                             evidence_refs=(tune_ref,),
                             extensions={"rule_id": "local-authz"})
        fp_fn_matrix = tune_result["matrix"]
        fp_fn_ref = runtime.store.put_object({
            "campaign_id": cid, "run_id": reeval_rid,
            "matrix": fp_fn_matrix, "tuning_ref": tune_ref})
        runtime.record_event(reeval_rid, "fp_fn_matrix", "orchestrator",
                             evidence_refs=(fp_fn_ref,))
        _finish(runtime, cid, reeval_rid)
        current_rid = None
        runtime.transition(cid, "review_pending")

        for run_obj in runtime.list_runs(cid):
            rid = run_obj["run_id"]
            if runtime.store._is_sealed(rid):
                runtime.store.seal_checkpoint(cid, rid, "rna4219")
        verification: List[Dict[str, Any]] = []
        for run_obj in runtime.list_runs(cid):
            verification.append(_verify(runtime, cid, run_obj["run_id"]))
        journal = {"campaign_id": cid, "runs": verification,
                   "recorded_at": _utc_now()}
        journal_ref = runtime.store.put_object(journal)

        # ---- Extra verify run: measurement ----
        measure_run = runtime.start_run(cid, "verify", seed, str(uuid.uuid4()))
        measure_rid = measure_run["run_id"]
        current_rid = measure_rid
        runs.append(measure_rid)
        red_rows_ref = runtime.store.put_object({
            "observations": [{"ref": row["ref"], **row["data"]}
                            for row in blue_rows]})
        blue_findings_ref = runtime.store.put_object({"findings": findings})
        remediation_ref = runtime.store.put_object(remediation)
        measurement = {
            "red_run_id": red_rid,
            "blue_run_id": blue_rid,
            "reevaluation_run_id": reeval_rid,
            "red_ref": red_rows_ref,
            "blue_ref": blue_findings_ref,
            "replay_predicate_ref": evaluated_ref,
            "benign_ref": benign_ref,
            "remediation_ref": remediation_ref,
            "detected_at": blue_detected_at,
        }
        measurement_ref = runtime.store.put_object(measurement)
        runtime.record_event(measure_rid, "state_captured", "orchestrator",
                             evidence_refs=(measurement_ref,),
                             extensions={"record_type": "lab_measurement"})
        _finish(runtime, cid, measure_rid)
        current_rid = None
        metrics = lab_metrics.measure(runtime, cid, measurement)
        _verify(runtime, cid, measure_rid)

        return {
            "campaign_id": cid,
            "scenario_id": scenario_id,
            "status": "review_pending",
            "blue_detections": len(findings),
            "finding_ids": [f.get("finding_id") for f in findings],
            "proposal_ref": proposal_ref,
            "replay_statuses": replay_statuses,
            "benign_checks": benign_checks,
            "final_verdict": verdict,
            "verification_journal_ref": journal_ref,
            "metrics": metrics,
            "measurement_ref": measurement_ref,
            "tuning_ref": tune_ref,
            "agent": {
                "red": {k: red_loop[k] for k in
                        ("status", "stop_reason", "turns", "llm_calls",
                         "actions", "model")},
                "blue": {k: blue_loop[k] for k in
                         ("status", "stop_reason", "turns", "llm_calls",
                          "actions", "model")},
                "purple": {k: purple_loop[k] for k in
                           ("status", "stop_reason", "turns", "llm_calls",
                            "actions", "model")},
            },
        }
    except Exception as exc:
        if current_rid is not None:
            try:
                runtime.record_event(
                    current_rid, "state_captured", "orchestrator",
                    evidence_refs=(),
                    extensions={"cause": "crash",
                                "error": "{}: {}".format(
                                    type(exc).__name__, exc)})
            except Exception:
                pass
            try:
                runtime.finish_run(current_rid, "incomplete", cause="crash")
            except Exception:
                pass
            try:
                runtime.seal_run(cid, current_rid)
            except Exception:
                pass
            try:
                runtime.store.seal_checkpoint(cid, current_rid, "rna4219")
            except Exception:
                pass
        try:
            runtime.transition(cid, "incomplete")
        except Exception:
            pass
        if failure_mode == "record":
            if isinstance(exc, _LabFailure):
                return {
                    "status": "failed",
                    "phase": exc.phase,
                    "reason": exc.reason,
                    "failure_attribution": exc.attribution,
                    "campaign_id": cid,
                    "scenario_id": scenario_id,
                }
            return {
                "status": "failed",
                "phase": "unknown",
                "reason": "{}: {}".format(type(exc).__name__, exc)[:300],
                "failure_attribution": "external",
                "campaign_id": cid,
                "scenario_id": scenario_id,
            }
        raise
    finally:
        if lab is not None:
            try:
                lab.__exit__(*sys.exc_info())
            finally:
                errors = lab.cleanup_errors
                if errors:
                    try:
                        if current_rid is not None:
                            verify_id = current_rid
                            run_id = current_rid
                        elif runs:
                            verify_id = runs[-1]
                            run_id = runs[-1]
                        else:
                            verify_id = cid
                            run_id = None
                        runtime.store.append_verification({
                            "verify_id": verify_id,
                            "campaign_id": cid,
                            "run_id": run_id,
                            "checked_at": _utc_now(),
                            "result": "HOLD",
                            "reason_code": "cleanup_failed",
                            "errors": list(errors),
                        })
                    except Exception as journal_exc:
                        active = sys.exc_info()[1]
                        if active is not None:
                            try:
                                active.add_note(
                                    "cleanup journal write failed: {}: {}".format(
                                        type(journal_exc).__name__,
                                        journal_exc))
                            except Exception:
                                pass


def run_iterations(runtime: Any, staging_root: Path, *, seed: int = 1,
                   max_iterations: int = 2, max_campaigns: int = 2,
                   routes: Optional[Dict[str, Any]] = None,
                   scenario_id: str = "authz-flag-false",
                   policy_bundle: Optional[Dict[str, Any]] = None,
                   ) -> Dict[str, Any]:
    _require(isinstance(staging_root, Path), "staging_root must be a Path")
    _require(staging_root.is_absolute(), "staging_root must be absolute")
    _require(staging_root.is_dir(), "staging_root must exist")
    _require(isinstance(max_iterations, int) and max_iterations >= 0,
             "max_iterations must be non-negative int")
    _require(isinstance(max_campaigns, int) and max_campaigns >= 0,
             "max_campaigns must be non-negative int")

    campaigns = []
    def _callback(iteration: int, remaining: int) -> Dict[str, Any]:
        _require(remaining >= 1, "remaining budget must be >= 1")
        result = run_demo(runtime, staging_root, seed, routes=routes,
                          scenario_id=scenario_id,
                          policy_bundle=policy_bundle)
        verdict = result.get("final_verdict") or {}
        success = verdict.get("remediation_success") is True
        summary = {
            "campaign_id": result.get("campaign_id"),
            "success": success,
            "verdict": verdict,
        }
        summary_ref = runtime.store.put_object(summary)
        campaigns.append({**summary, "summary_ref": summary_ref})
        return {"success": success, "progress": success, "calls": 1,
                "failure_class": None if success else "search",
                "evidence_refs": [summary_ref]}

    loop_result = purple_loop(_callback, max_iterations=max_iterations,
                              max_calls=max_campaigns)
    return {
        "loop": loop_result,
        "campaigns": campaigns,
        "budget_unit": "campaign_cycles",
    }
