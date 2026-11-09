"""Event-level detection regression tests; real broker/Oracle, no LLM."""
import pytest

from ai_yori_murasaki.agents.tools import ToolBroker
from ai_yori_murasaki.jsonio import DataError, digest
from ai_yori_murasaki.oracle import Oracle
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


@pytest.fixture
def blue(tmp_path):
    store = Store(tmp_path / "ws", tmp_path / "trusted")
    rt = Runtime(store)
    scope = {"targets": [{"kind": "fixture", "identifier": "t1"}],
             "max_targets": 1}
    auth = {"operator_id": "op1", "granted_at": "2026-01-01T00:00:00Z",
            "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope)}
    cid = rt.create_campaign("INT-TEST", scope, auth, "c1")["campaign_id"]
    rt.transition(cid, "running")
    red = rt.start_run(cid, "red", 1, "r1")["run_id"]
    rt.finish_run(red, "completed")
    rt.seal_run(cid, red)
    rid = rt.start_run(cid, "blue_blind", 1, "b1")["run_id"]
    broker = ToolBroker(runtime=rt, campaign_id=cid, run_id=rid,
                        role="blue", grants=["submit_findings"])
    return rt, cid, broker


def row(rt, timestamp, **changes):
    data = {"timestamp": timestamp, "method": "GET", "path": "/record",
            "status": 200, "role": "guest"}
    data.update(changes)
    observation = {"kind": "telemetry", "observed_at": "2026-11-09T00:00:00Z",
                   "data": data}
    return {"ref": rt.store.put_object(observation), "data": data}


def entry(observation):
    data = observation["data"]
    return {"category": "authz", "severity": "high", "title": "access",
            "path": data["path"], "method": data["method"],
            "role": data["role"], "evidence_ref": observation["ref"],
            "rationale": "observed access"}


def saved(rt, cid):
    return [rt.store.get_object(ref) for ref in rt.list_records(cid, "finding")]


@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("separate_calls", [False, True])
def test_distinct_events_survive_deduplication(blue, reverse, separate_calls):
    rt, cid, broker = blue
    rows = [row(rt, 100), row(rt, 101)]
    broker.issue_refs([r["ref"] for r in rows])
    reports = [entry(r) for r in (rows[::-1] if reverse else rows)]
    batches = [[r] for r in reports] if separate_calls else [reports]
    for batch in batches:
        result = broker.execute("submit_findings", {"findings": batch})
        assert result["type"] == "tool_result"
    findings = saved(rt, cid)
    report = Oracle().blind_detection_check(rows, findings, rt.store)["report"]
    assert report["passed"] is True, report
    assert len(findings) == 2
    assert {f["references"][0] for f in findings} == {r["ref"] for r in rows}
    assert report["detected_events"] == 2
    assert report["missed_events"] == report["false_positive_findings"] == 0


def test_same_evidence_is_idempotent_in_batch_and_on_retry(blue):
    rt, cid, broker = blue
    event = row(rt, 100)
    broker.issue_refs([event["ref"]])
    first = broker.execute("submit_findings", {"findings": [entry(event)] * 2})
    retry = broker.execute("submit_findings", {"findings": [entry(event)]})
    assert first["result"]["submitted"] == first["result"]["duplicates"] == 1
    assert retry["result"]["submitted"] == 0
    assert retry["result"]["duplicates"] == 1
    assert len(saved(rt, cid)) == 1


@pytest.mark.parametrize("report_benign", [False, True])
def test_benign_and_attack_counts(blue, report_benign):
    rt, cid, broker = blue
    rows = [row(rt, 100), row(rt, 101, role="admin"),
            row(rt, 102, status=403), row(rt, 103, path="/public")]
    broker.issue_refs([r["ref"] for r in rows])
    reports = [entry(rows[0])] + ([entry(rows[1])] if report_benign else [])
    broker.execute("submit_findings", {"findings": reports})
    report = Oracle().blind_detection_check(rows, saved(rt, cid), rt.store)["report"]
    assert report["rows_requiring_detection"] == report["detected_events"] == 1
    assert report["missed_events"] == 0
    assert report["true_positive_findings"] == 1
    assert report["false_positive_findings"] == int(report_benign)
    assert report["passed"] is (not report_benign)


@pytest.mark.parametrize("bad_ref", [None, "", "not-a-ref", "f" * 64, 3])
def test_invalid_or_unissued_refs_reject_whole_batch(blue, bad_ref):
    rt, cid, broker = blue
    event = row(rt, 100)
    broker.issue_refs([event["ref"]])
    invalid = entry(event)
    invalid["evidence_ref"] = bad_ref
    result = broker.execute("submit_findings", {"findings": [entry(event), invalid]})
    assert result["type"] == "tool_error"
    assert saved(rt, cid) == []


def test_missing_and_other_agent_refs_are_rejected(blue):
    rt, cid, broker = blue
    event = row(rt, 100)
    red_broker = ToolBroker(runtime=rt, campaign_id=cid,
                           run_id=broker._run_id, role="red", grants=["read_evidence"])
    red_broker.issue_refs([event["ref"]])
    assert broker.execute("submit_findings", {"findings": [entry(event)]})["type"] == "tool_error"
    assert broker.execute("read_evidence", {"ref": event["ref"]})["type"] == "tool_error"
    missing = entry(event)
    del missing["evidence_ref"]
    assert broker.execute("submit_findings", {"findings": [missing]})["type"] == "tool_error"
    assert saved(rt, cid) == []


def test_oracle_rejects_missing_or_mismatched_cas(blue):
    rt, _, _ = blue
    event = row(rt, 100)
    event["data"]["status"] = 403
    with pytest.raises(DataError):
        Oracle().blind_detection_check([event], [], rt.store)
    event["ref"] = "f" * 64
    with pytest.raises((DataError, FileNotFoundError)):
        Oracle().blind_detection_check([event], [], rt.store)


@pytest.mark.parametrize("changes", [{"role": "admin"}, {"method": "POST"},
                                     {"path": "/public"}])
def test_report_metadata_must_match_issued_observation(blue, changes):
    rt, cid, broker = blue
    event = row(rt, 100)
    broker.issue_refs([event["ref"]])
    finding = entry(event)
    finding.update(changes)
    assert broker.execute("submit_findings", {"findings": [finding]})["type"] == "tool_error"
    assert saved(rt, cid) == []
