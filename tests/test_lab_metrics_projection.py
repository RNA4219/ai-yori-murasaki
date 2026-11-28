"""合成の監査記録だけを使い、計測と案件APIが同じ封印済み台帳を採用することを確かめる。"""
import json
from unittest.mock import patch

from ai_yori_murasaki import lab_metrics
from ai_yori_murasaki.oracle import Oracle
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import _project_lab_metrics, handle_request
from ai_yori_murasaki.storage import Store
from tests.test_tracker_drift_e2e import prepare_csv_fixture


def test_measurement_and_campaign_projection_use_checkpointed_events(tmp_path):
    ids = prepare_csv_fixture(tmp_path)
    cid, observations = ids["campaign_id"], ids["run_id"]
    store = Store(tmp_path / "workspace", tmp_path / "trusted")
    runtime = Runtime(store)

    def finish(rid):
        runtime.finish_run(rid, "completed")
        runtime.seal_run(cid, rid)

    finish(observations)
    findings = runtime.start_run(cid, "verify", 2, "collect-fixture-findings")["run_id"]
    finish(findings)
    audit = runtime.start_run(cid, "verify", 4, "collect-fixture-audit")["run_id"]
    runtime.record_event(audit, "state_captured", "orchestrator", extensions={"fixture": True})
    earlier = store.read_verified_events(audit, campaign_id=cid)
    runtime.record_event(audit, "state_captured", "orchestrator",
                         extensions={"fixture": True, "operation": "apply", "environment": "production"})
    finish(audit)
    evaluation = runtime.start_run(cid, "verify", 3, "collect-fixture-results")["run_id"]
    put = store.put_object
    check = {"suite_id": "offline-fixture", "passed": True}
    oracle = Oracle()
    predicate = oracle.make_predicate("fixture-check", "test_pass", put(check), check)
    checks = {"checks": [{"passed": True}]}
    passed = {"passed": True, "ref": put(checks)}
    measurement = {
        "red_run_id": observations, "blue_run_id": findings, "reevaluation_run_id": evaluation,
        "red_ref": put({"observations": []}), "blue_ref": put({"findings": []}), "detected_at": 0,
        "replay_predicate_ref": put(oracle.evaluate(predicate, store)),
        "benign_ref": put({**checks, "passed": True, "campaign_id": cid, "run_id": evaluation}),
        "remediation_ref": put({"campaign_id": cid, "run_id": findings, "patch_bundle_ref": put({"fixture": True}),
                                "static_scan_result": passed, "unit_result": passed, "integration_result": passed}),
    }
    runtime.record_event(evaluation, "state_captured", "orchestrator", evidence_refs=(put(measurement),),
                         extensions={"record_type": "lab_measurement"})
    finish(evaluation)
    events = {}
    for run in runtime.list_runs(cid):
        rid = run["run_id"]
        store.seal_checkpoint(cid, rid, "fixture")
        events[rid] = store.read_verified_events(rid, campaign_id=cid)

    measured = lab_metrics.measure(runtime, cid, measurement)
    assert measured["status"] == "measured"
    assert measured["expected_manifest"]["event_counts"] == {rid: len(rows) for rid, rows in events.items()}
    assert measured["metrics"]["production_auto_apply_count"]["value"] == 1
    status, _, body = handle_request(runtime, "GET", "/api/campaigns/" + cid)
    assert status == 200
    assert json.loads(body)["metrics"]["metrics"] == measured["metrics"]

    # 正規の追記前に取得していた別の実行の列は、最新の計測に混ぜない。
    held = _project_lab_metrics(runtime, cid, verified_events={**events, audit: earlier})
    assert held == {"status": "HOLD", "reason_code": "evidence_unverified", "metrics": None, "limitations": []}

    listed = [run for run in runtime.list_runs(cid) if run["run_id"] != audit]
    with patch.object(runtime, "list_runs", return_value=listed):
        assert lab_metrics.measure(runtime, cid, measurement) == {
            "status": "HOLD", "reason_code": "evidence_unverified", "metrics": None}
        partial = {rid: rows for rid, rows in events.items() if rid != audit}
        assert _project_lab_metrics(runtime, cid, verified_events=partial) == held
