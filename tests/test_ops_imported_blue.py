"""Regression: imported-log Blind Blue is auditable and Red remains gated."""
import json
import tempfile
from pathlib import Path

import pytest

from ai_yori_murasaki.cli import main
from ai_yori_murasaki.jsonio import DataError, digest
from ai_yori_murasaki.oracle import Oracle
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.storage import Store


@pytest.fixture
def env(tmp_path):
    ws, tr = tmp_path / "ws", tmp_path / "tr"
    ws.mkdir()
    tr.mkdir()
    rt = Runtime(Store(ws, tr))
    scope = {"targets": [{"kind": "fixture", "identifier": "imported-logs"}],
             "max_targets": 1}
    authorization = {
        "operator_id": "tester", "granted_at": "2026-01-01T00:00:00Z",
        "expires_at": "2099-01-01T00:00:00Z", "scope_digest": digest(scope),
    }
    campaign = rt.create_campaign("INT-OPS-REGRESSION", scope, authorization,
                                  "ops-campaign")
    cid = campaign["campaign_id"]
    rt.transition(cid, "running")
    return rt, cid, ws, tr


def _logs(rt):
    return rt.store.put_object({
        "kind": "imported_logs",
        "records": [{"source": "offline-export", "message": "test fixture"}],
    })


def _seal(rt, cid, run_id):
    rt.finish_run(run_id, "completed")
    rt.seal_run(cid, run_id)
    rt.store.seal_checkpoint(cid, run_id, "tester")


def _cli_call(ws, tr, capsys, *args):
    code = main(["--workspace", str(ws), "--trusted-root", str(tr), *args])
    captured = capsys.readouterr()
    assert not captured.err
    return code, json.loads(captured.out)


def test_standard_blind_blue_still_requires_sealed_red(env):
    rt, cid, _, _ = env
    with pytest.raises(DataError, match="prior phase completed and sealed"):
        rt.start_run(cid, "blue_blind", 1, "plain-blue")


def test_imported_blue_without_red_is_auditable_and_idempotent(env):
    rt, cid, _, _ = env
    ref = _logs(rt)
    blue = rt.start_run(cid, "blue_blind", 1, "from-logs",
                        imported_source_ref=ref)
    assert rt.start_run(cid, "blue_blind", 1, "from-logs",
                        imported_source_ref=ref) == blue
    assert blue["extensions"] == {
        "input_mode": "imported_logs", "imported_source_ref": ref,
    }
    manifest = rt.store.get_object(blue["manifest_ref"])
    assert manifest["imported_source_ref"] == ref
    assert manifest["input_mode"] == "imported_logs"
    assert not any(run["phase"] == "red" for run in rt.list_runs(cid))
    with pytest.raises(DataError):
        rt.start_run(cid, "blue_blind", 1, "from-logs",
                     imported_source_ref=rt.store.put_object({"other": 1}))
    _seal(rt, cid, blue["run_id"])
    events = rt.store.read_events(blue["run_id"])
    assert any(event.get("extensions", {}).get("imported_source_ref") == ref
               and ref in event.get("evidence_refs", [])
               for event in events)
    assert rt.store.verify_chain(blue["run_id"])["result"] == "PASS"


def test_imported_source_validation_and_phase_isolation(env):
    rt, cid, _, _ = env
    ref = _logs(rt)
    with pytest.raises(DataError, match="only allowed"):
        rt.start_run(cid, "red", 1, "bad-phase", imported_source_ref=ref)
    with pytest.raises(DataError, match="not found"):
        rt.start_run(cid, "blue_blind", 1, "missing",
                     imported_source_ref="0" * 64)
    with pytest.raises(DataError, match="nonempty JSON"):
        rt.start_run(cid, "blue_blind", 1, "empty",
                     imported_source_ref=rt.store.put_object({}))


def test_imported_blue_resume_retains_source(env):
    rt, cid, _, _ = env
    ref = _logs(rt)
    first = rt.start_run(cid, "blue_blind", 4, "to-resume",
                         imported_source_ref=ref)
    recovered = rt.resume_after_crash(first["run_id"])
    assert recovered["run_id"] != first["run_id"]
    assert recovered["extensions"]["imported_source_ref"] == ref
    assert rt.resume_after_crash(first["run_id"]) == recovered
    assert rt.store.get_object(recovered["manifest_ref"])["imported_source_ref"] == ref


def test_cli_import_blue_oracle_target_unlocks_purple(env, tmp_path, capsys):
    rt, cid, ws, tr = env
    export = tmp_path / "logs.json"
    export.write_text(json.dumps({
        "kind": "imported_logs",
        "records": [{"source": "test-wazuh-export", "message": "fixture"}],
    }), encoding="utf-8")
    code, reply = _cli_call(ws, tr, capsys, "run", "start",
                             "--campaign-id", cid, "--phase", "blue_blind",
                             "--idempotency-key", "blue-cli",
                             "--imported-logs-file", str(export))
    assert code == 0, reply
    blue_id = reply["run_id"]
    blue = rt.get_run(blue_id)
    source_ref = blue["extensions"]["imported_source_ref"]
    assert rt.store.get_object(source_ref)["kind"] == "imported_logs"
    assert not any(r["phase"] == "red" for r in rt.list_runs(cid))
    _seal(rt, cid, blue_id)
    rt.transition(cid, "blind_completed")

    verify = rt.start_run(cid, "verify", 5, "verify-cli")
    ev = rt.store.put_object({"ref": source_ref})
    pred = Oracle().make_predicate("p1", "evidence_hash", ev,
                                   {"ref": source_ref})
    predicate = tmp_path / "predicate.json"
    predicate.write_text(json.dumps(pred), encoding="utf-8")
    code, result = _cli_call(ws, tr, capsys, "oracle", "evaluate",
                              "--run-id", verify["run_id"],
                              "--predicate-file", str(predicate),
                              "--evaluated-run-id", blue_id)
    assert code == 0 and result["data"]["verdict"] == "PASS", result
    _seal(rt, cid, verify["run_id"])
    rt.transition(cid, "purple_open")
    assert rt.get_campaign(cid)["status"] == "purple_open"


def test_cli_oracle_target_rejects_unsealed_blue(env, tmp_path, capsys):
    rt, cid, ws, tr = env
    ref = _logs(rt)
    blue = rt.start_run(cid, "blue_blind", 1, "blue-unsealed",
                        imported_source_ref=ref)
    # A verify run may not start while another run is running.
    rt.finish_run(blue["run_id"], "incomplete", "not_executed")
    verification = rt.start_run(cid, "verify", 0, "oracle-guard")
    ev = rt.store.put_object({"code": 0})
    predicate = Oracle().make_predicate("p1", "exit_code", ev, {"code": 0})
    f = tmp_path / "p.json"
    f.write_text(json.dumps(predicate), encoding="utf-8")
    code, reply = _cli_call(ws, tr, capsys, "oracle", "evaluate",
                             "--run-id", verification["run_id"],
                             "--predicate-file", str(f),
                             "--evaluated-run-id", blue["run_id"])
    assert code == 4 and reply["status"] == "denied", reply


def test_pytest_tmpdir_resolved():
    """macOS /var is a symlink; pytest's temp roots must use /private/var."""
    root = Path(tempfile.gettempdir())
    assert root == root.resolve()
    with tempfile.TemporaryDirectory() as name:
        assert Path(name) == Path(name).resolve()
