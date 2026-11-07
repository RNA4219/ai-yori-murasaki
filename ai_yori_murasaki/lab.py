"""Finite disposable local Docker lab orchestration for ai_yori_murasaki."""
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

from .worker import DockerLab, stage_worker, ObservationBroker
from .oracle import Oracle, build_final_verdict
from .jsonio import digest, DataError
from .adapters.llm import LLMClient
from .adapters.base import HttpBroker
from . import lab_metrics
from .p2 import route_model, purple_loop, tune_sigma

_ASSETS = Path(__file__).resolve().parent / "lab_assets"
_ANCHOR = "REQUIRE_ADMIN: bool = False"
_REPLACEMENT = "REQUIRE_ADMIN: bool = True"


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


def run_demo(runtime: Any, staging_root: Path, seed: int = 1,
             llm_client: Optional[LLMClient] = None,
             routes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    if llm_client is None:
        llm_client = LLMClient(HttpBroker(runtime.policy), timeout_ms=90000,
                               retry_cap=0)
    _require(isinstance(staging_root, Path), "staging_root must be a Path")
    _require(staging_root.is_absolute(), "staging_root must be absolute")
    _require(staging_root.is_dir(), "staging_root must exist")

    jobdir = staging_root / _new_id("job")
    jobdir.mkdir(parents=False, exist_ok=False)
    target_dir = jobdir / "target"
    target_dir.mkdir()
    shutil.copy2(str(_ASSETS / "target.py"), str(target_dir / "target.py"))
    shutil.copy2(str(_ASSETS / "worker.py"), str(target_dir / "worker.py"))

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
        lab = DockerLab(jobdir.resolve())
        lab.__enter__()
        lab.start_target(target_dir)
        _require(lab.wait_healthy() is True, "target must be healthy")

        # ---- Red phase ----
        red_run = runtime.start_run(cid, "red", seed, str(uuid.uuid4()))
        red_rid = red_run["run_id"]
        current_rid = red_rid
        runs.append(red_rid)
        red_stage = stage_worker(jobdir, "red")
        red_result = lab.run_worker(red_stage, "red", "red")
        red_obs = _extract_observations(red_result)
        red_checks = _extract_checks(red_result)
        _require(len(red_obs) == 3, "red phase must yield 3 observations")
        baseline_statuses = [o.get("status") for o in red_obs]
        _require(baseline_statuses == [200, 200, 200],
                 "red baseline statuses must be 200,200,200")
        _require(_all_passed(red_checks), "red checks must all pass")
        broker_red = ObservationBroker(runtime, red_rid, "red")
        red_refs: List[str] = []
        for obs in red_obs:
            sub = broker_red.submit_observation({
                "kind": "http_response", "data": obs, "observed_at": _utc_now()})
            red_refs.append(sub["data_ref"])
        red_raw_ref = runtime.store.put_object(red_result)
        runtime.record_event(red_rid, "attack_executed", "orchestrator",
                             evidence_refs=(red_raw_ref,),
                             extensions={"observations": len(red_obs)})
        _finish(runtime, cid, red_rid)
        current_rid = None

        # ---- Blue phase ----
        blue_run = runtime.start_run(cid, "blue_blind", seed, str(uuid.uuid4()))
        blue_rid = blue_run["run_id"]
        current_rid = blue_rid
        runs.append(blue_rid)
        telemetry = lab.telemetry()
        blue_stage = stage_worker(jobdir, "blue", telemetry)
        blue_result = lab.run_worker(blue_stage, "blue", "blue")
        blue_detected_at = time.time()
        detections = blue_result.get("detection_count")
        _require(isinstance(detections, int), "blue detections must be int")
        blue_obs = _extract_observations(blue_result)
        broker_blue = ObservationBroker(runtime, blue_rid, "blue")
        for obs in blue_obs:
            data = {k: v for k, v in obs.items() if k != "source"}
            broker_blue.submit_observation({
                "kind": "detection", "data": data, "observed_at": _utc_now()})
        blue_raw_ref = runtime.store.put_object(blue_result)

        llm_resp = llm_client.chat(
            url="http://localhost:11434/api/chat", role="blue", campaign=cid,
            run=blue_rid, model=route_model("blue", routes),
            messages=[
                {"role": "system",
                 "content": "Summarize observed suspicious access in one short Japanese sentence."},
                {"role": "user",
                 "content": json.dumps(blue_result, ensure_ascii=False,
                                       sort_keys=True)},
            ],
            token_cap=512, native=True)
        _require(bool(llm_resp.get("complete")), "llm response incomplete")
        content = llm_resp.get("content")
        _require(isinstance(content, str) and content.strip() != "",
                 "llm content empty")
        llm_ref = runtime.store.put_object(llm_resp)
        finding_id = _new_id("f")
        trigger = blue_obs[0] if blue_obs else None
        _require(isinstance(trigger, dict), "blue trigger observation missing")
        trigger_path = trigger.get("path")
        _require(isinstance(trigger_path, str), "blue trigger path must be str")
        if (trigger.get("status") == 200 and
                trigger.get("role") == "guest" and
                trigger_path.split("?", 1)[0] == "/record"):
            severity = "high"
        else:
            severity = "info"
        classification = {
            "rule": "local-guest-record-access",
            "severity": severity,
            "path": trigger_path,
            "observation_ref": blue_raw_ref,
        }
        classification_ref = runtime.store.put_object(classification)
        finding = {
            "schema_version": "1.0.0",
            "finding_id": finding_id,
            "campaign_id": cid,
            "run_id": blue_rid,
            "source": "llm",
            "severity": severity,
            "title": content[:512],
            "location": {"path": trigger_path, "line": None,
                         "column": None},
            "references": [blue_raw_ref, llm_ref, classification_ref],
        }
        runtime.register_record("finding", finding, run_id=blue_rid)
        _finish(runtime, cid, blue_rid)
        current_rid = None
        runtime.transition(cid, "blind_completed")

        # ---- Verify run ----
        verify_run = runtime.start_run(cid, "verify", seed, str(uuid.uuid4()))
        verify_rid = verify_run["run_id"]
        current_rid = verify_rid
        runs.append(verify_rid)
        blind = Oracle().blind_predicate(
            "blue-detection", red_raw_ref, blue_raw_ref, runtime.store)
        predicate = blind["predicate"]
        runtime.record_event(verify_rid, "state_captured", "orchestrator",
                             evidence_refs=(blind["report_ref"],))
        evaluated = runtime.record_oracle(verify_rid, predicate,
                                          evaluated_run_id=blue_rid)
        _require(evaluated.get("verdict") == "PASS",
                 "blue-detection predicate must PASS")
        _finish(runtime, cid, verify_rid)
        current_rid = None
        runtime.transition(cid, "purple_open")

        # ---- Purple patch run ----
        purple_run = runtime.start_run(cid, "purple", seed, str(uuid.uuid4()))
        purple_rid = purple_run["run_id"]
        current_rid = purple_rid
        runs.append(purple_rid)
        target_file = target_dir / "target.py"
        before = target_file.read_text(encoding="utf-8")
        _require(before.count(_ANCHOR) == 1, "anchor must occur exactly once")
        after = before.replace(_ANCHOR, _REPLACEMENT)
        patch_ref = runtime.store.put_object({
            "path": "target.py", "before": before, "after": after})
        runtime.record_event(purple_rid, "patch_drafted", "orchestrator",
                             evidence_refs=(patch_ref,),
                             extensions={"path": "target.py"})
        purple_prompt = (
            "次のパッチ差分を日本語で1文だけ説明してください。"
            "コードは実行しないでください。\n"
            "対象ファイル: target.py\n"
            "変更前(抜粋):\n" + before[:2048] + "\n"
            "変更後(抜粋):\n" + after[:2048])
        purple_resp = llm_client.chat(
            url="http://localhost:11434/api/chat", role="purple",
            campaign=cid, run=purple_rid,
            model=route_model("purple", routes),
            messages=[
                {"role": "system",
                 "content": "あなたはセキュリティ修正の説明者です。"},
                {"role": "user", "content": purple_prompt},
            ],
            token_cap=512, native=True)
        _require(bool(purple_resp.get("complete")),
                 "purple llm response incomplete")
        purple_content = purple_resp.get("content")
        _require(isinstance(purple_content, str) and
                 purple_content.strip() != "", "purple llm content empty")
        purple_llm_ref = runtime.store.put_object(purple_resp)
        runtime.record_event(purple_rid, "remediation_drafted", "llm",
                             evidence_refs=(patch_ref, purple_llm_ref),
                             extensions={"path": "target.py",
                                         "model": route_model("purple", routes)})
        _finish(runtime, cid, purple_rid)
        current_rid = None
        runtime.transition(cid, "remediation_in_progress")

        # ---- Remediation run ----
        rem_run = runtime.start_run(cid, "remediation", seed, str(uuid.uuid4()))
        rem_rid = rem_run["run_id"]
        current_rid = rem_rid
        runs.append(rem_rid)
        lab.stop_target()
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
        _require(static_ok, "patched target failed ast.parse")
        unit_result = lab.run_worker(target_dir.resolve(), "oracle", "unit")
        unit_checks = _extract_checks(unit_result)
        _require(_all_passed(unit_checks), "unit checks must all pass")
        unit_ref = runtime.store.put_object(unit_result)
        runtime.record_event(rem_rid, "state_captured", "orchestrator",
                             evidence_refs=(patch_ref,),
                             extensions={"path": "target.py"})
        runtime.record_event(rem_rid, "static_scan_result", "orchestrator",
                             evidence_refs=(static_ref,),
                             extensions={"tool": "python-ast"})
        runtime.record_event(rem_rid, "unit_result", "orchestrator",
                             evidence_refs=(unit_ref,),
                             extensions={"checks": len(unit_checks)})
        _finish(runtime, cid, rem_rid)
        current_rid = None

        # ---- Reevaluation run ----
        reeval_run = runtime.start_run(cid, "reevaluation", seed,
                                       str(uuid.uuid4()))
        reeval_rid = reeval_run["run_id"]
        current_rid = reeval_rid
        runs.append(reeval_rid)
        lab.start_target(target_dir)
        _require(lab.wait_healthy() is True, "patched target must be healthy")
        replay_result = lab.run_worker(red_stage, "red", "red")
        replay_obs = _extract_observations(replay_result)
        _require(len(replay_obs) == 3, "replay must yield 3 observations")
        replay_statuses = [o.get("status") for o in replay_obs]
        _require(replay_statuses == [403, 403, 403],
                 "replay statuses must be 403,403,403")
        replay_checks = _extract_checks(replay_result)
        _require(len(replay_checks) == 3, "replay must yield 3 checks")
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
        _require(evaluated_replay.get("verdict") == "FAIL",
                 "replay predicate must FAIL (exploit not succeeded)")
        _require(evaluated_replay.get("reason_code") == "exploit_not_succeeded",
                 "replay reason_code must be exploit_not_succeeded")
        evaluated_ref = runtime.store.put_object(evaluated_replay)

        remediation_id = _new_id("rem")
        remediation = {
            "schema_version": "1.0.0",
            "remediation_id": remediation_id,
            "finding_id": finding_id,
            "campaign_id": cid,
            "run_id": blue_rid,
            "patch_bundle_ref": patch_ref,
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
        benign_obs_rows: List[Dict[str, Any]] = []
        benign_obs = benign_result.get("observations")
        if isinstance(benign_obs, list):
            for obs in benign_obs:
                if isinstance(obs, dict):
                    benign_obs_rows.append({
                        "status": obs.get("status"),
                        "path": obs.get("path"),
                        "expected_detection": False,
                    })
        red_obs_rows: List[Dict[str, Any]] = []
        for obs in red_obs:
            if isinstance(obs, dict):
                red_obs_rows.append({
                    "status": obs.get("status"),
                    "path": obs.get("path"),
                    "expected_detection": True,
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
        remediation_ref = runtime.store.put_object(remediation)
        measurement = {
            "red_run_id": red_rid,
            "blue_run_id": blue_rid,
            "reevaluation_run_id": reeval_rid,
            "red_ref": red_raw_ref,
            "blue_ref": blue_raw_ref,
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
            "baseline_statuses": baseline_statuses,
            "blue_detections": detections,
            "replay_statuses": replay_statuses,
            "benign_checks": benign_checks,
            "final_verdict": verdict,
            "status": "review_pending",
            "verification_journal_ref": journal_ref,
            "metrics": metrics,
            "measurement_ref": measurement_ref,
            "tuning_ref": tune_ref,
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
                   routes: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
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
        result = run_demo(runtime, staging_root, seed, routes=routes)
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
