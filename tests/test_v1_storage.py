"""Storage contract tests: chain, CAS, checkpoint, journal, tamper detection."""
from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import pytest

from ai_yori_murasaki.jsonio import DataError, canonical, digest
from ai_yori_murasaki.storage import Store, event_hash


@pytest.fixture()
def store(tmp_path: Path) -> Store:
    ws = tmp_path / "ws"
    tr = tmp_path / "trusted"
    ws.mkdir()
    tr.mkdir()
    return Store(ws, tr, secrets=("s3cr3t-value",))


def _event(run_id: str, n: int, **extra) -> dict:
    base = {"schema_version": "1.0.0", "campaign_id": "c1", "run_id": run_id,
            "actor": {"role": "orchestrator", "agent_id": "a1", "model": None},
            "source": "orchestrator", "event_type": "state_captured",
            "timestamp": "2026-11-06T00:00:00Z",
            "target": {"kind": "fixture", "identifier": "t1"},
            "finding": None, "evidence_refs": []}
    base.update(extra)
    return base


def _seal(store: Store, run_id: str) -> None:
    store.append_event(run_id, _event(run_id, 0, event_type="chain_sealed"))


def _manifest(run_id: str) -> dict:
    return {"schema_version": "1.0.0", "campaign_id": "c1", "run_id": run_id,
            "phase": "red", "status": "completed", "sealed": True}


# -- chain basics ---------------------------------------------------------- #
def test_append_and_verify(store: Store) -> None:
    h1 = store.append_event("r1", _event("r1", 1))
    h2 = store.append_event("r1", _event("r1", 2))
    assert h1 != h2
    result = store.verify_chain("r1")
    assert result["result"] == "PASS"
    assert result["last_sequence"] == 2
    assert result["last_event_hash"] == h2


def test_empty_chain_hold(store: Store) -> None:
    assert store.verify_chain("r1")["result"] == "HOLD"
    with pytest.raises(DataError):
        store.seal_checkpoint("c1", "r1", "op1")


def test_derived_fields_rejected(store: Store) -> None:
    with pytest.raises(DataError):
        store.append_event("r1", _event("r1", 1, sequence=1))


def test_event_cap(store: Store) -> None:
    big = _event("r1", 1, extensions={"blob": "x" * (1024 * 1024)})
    with pytest.raises(DataError):
        store.append_event("r1", big)


# -- tamper detection ------------------------------------------------------ #
def test_modify_detected(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    store.append_event("r1", _event("r1", 2))
    path = store.workspace / "runs" / "r1" / "events.jsonl"
    lines = path.read_bytes().splitlines()
    obj = json.loads(lines[0])
    obj["event_type"] = "attack_executed"
    lines[0] = canonical(obj)
    path.write_bytes(b"\n".join(lines) + b"\n")
    assert store.verify_chain("r1")["result"] == "FAIL"


def test_delete_detected(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    store.append_event("r1", _event("r1", 2))
    store.append_event("r1", _event("r1", 3))
    path = store.workspace / "runs" / "r1" / "events.jsonl"
    lines = path.read_bytes().splitlines()
    path.write_bytes(b"\n".join([lines[0], lines[2]]) + b"\n")
    assert store.verify_chain("r1")["result"] == "FAIL"


def test_reorder_detected(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    store.append_event("r1", _event("r1", 2))
    path = store.workspace / "runs" / "r1" / "events.jsonl"
    lines = path.read_bytes().splitlines()
    path.write_bytes(b"\n".join([lines[1], lines[0]]) + b"\n")
    assert store.verify_chain("r1")["result"] == "FAIL"


def test_full_rewrite_detected_by_checkpoint(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    _seal(store, "r1")
    store.save_manifest("r1", _manifest("r1"))
    cp = store.seal_checkpoint("c1", "r1", "op1")
    assert cp["last_sequence"] == 2
    # full rewrite: write a self-consistent shorter chain directly to the raw
    # file (sealed API correctly refuses append), then checkpoint must mismatch.
    path = store.workspace / "runs" / "r1" / "events.jsonl"
    forged = _event("r1", 1)
    forged["run_id"] = "r1"
    forged["sequence"] = 1
    forged["previous_event_hash"] = "0" * 64
    forged["event_hash"] = event_hash(forged)
    path.write_bytes(canonical(forged) + b"\n")
    assert store.verify_chain("r1")["result"] == "PASS"
    result = store.verify_checkpoint("c1", "r1")
    assert result["result"] == "FAIL"
    assert result["reason_code"] == "checkpoint_mismatch"


def test_tail_deletion_detected(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    _seal(store, "r1")
    store.save_manifest("r1", _manifest("r1"))
    store.seal_checkpoint("c1", "r1", "op1")
    path = store.workspace / "runs" / "r1" / "events.jsonl"
    lines = path.read_bytes().splitlines()
    path.write_bytes(lines[0] + b"\n")
    result = store.verify_checkpoint("c1", "r1")
    assert result["result"] == "FAIL"


def test_checkpoint_missing_hold(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    assert store.verify_checkpoint("c1", "r1")["result"] == "HOLD"


def test_unsealed_checkpoint_refused(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    with pytest.raises(DataError):
        store.seal_checkpoint("c1", "r1", "op1")


# -- sealed append --------------------------------------------------------- #
def test_sealed_append_refused(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    _seal(store, "r1")
    with pytest.raises(DataError) as exc:
        store.append_event("r1", _event("r1", 2))
    assert exc.value.reason_code == "policy_denied"


def test_sealed_append_refused_after_tail_delete(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    _seal(store, "r1")
    store.save_manifest("r1", _manifest("r1"))
    store.seal_checkpoint("c1", "r1", "op1")
    path = store.workspace / "runs" / "r1" / "events.jsonl"
    lines = path.read_bytes().splitlines()
    path.write_bytes(lines[0] + b"\n")
    with pytest.raises(DataError):
        store.append_event("r1", _event("r1", 2))


# -- CAS ------------------------------------------------------------------- #
def test_cas_roundtrip(store: Store) -> None:
    sha = store.put_object({"a": 1, "b": 1.0})
    assert store.get_object(sha) == {"a": 1, "b": 1.0}
    assert store.get_object_bytes(sha) == b'{"a":1,"b":1.0}'


def test_cas_corrupt_detected(store: Store) -> None:
    sha = store.put_object({"a": 1})
    path = store.workspace / "objects" / (sha + ".json")
    path.write_bytes(b'{"a":2}')
    with pytest.raises(DataError):
        store.get_object(sha)


def test_cas_secret_redacted(store: Store) -> None:
    sha = store.put_object({"token": "s3cr3t-value", "note": "has s3cr3t-value inside"})
    obj = store.get_object(sha)
    assert obj["token"] == "[REDACTED]"
    assert "s3cr3t-value" not in obj["note"]


def test_cas_immutable(store: Store) -> None:
    sha = store.put_object({"a": 1})
    assert store.put_object({"a": 1}) == sha


# -- path safety ----------------------------------------------------------- #
def test_symlink_rejected(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    tr = tmp_path / "trusted"
    ws.mkdir()
    tr.mkdir()
    link = tmp_path / "link"
    try:
        link.symlink_to(ws)
    except (OSError, NotImplementedError):
        pytest.skip("symlink unsupported")
    with pytest.raises(DataError):
        Store(link, tr)


def test_nested_roots_rejected(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    with pytest.raises(DataError):
        Store(ws, ws / "trusted")
    with pytest.raises(DataError):
        Store(ws, ws)


def test_bad_id_rejected(store: Store) -> None:
    with pytest.raises(DataError):
        store.append_event("../evil", _event("r1", 1))
    with pytest.raises(DataError):
        store.get_object("not-a-digest")


# -- concurrency ----------------------------------------------------------- #
def test_concurrent_append(store: Store) -> None:
    errors: list[BaseException] = []

    def worker(n: int) -> None:
        try:
            for i in range(5):
                store.append_event("r1", _event("r1", n * 10 + i))
        except BaseException as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    result = store.verify_chain("r1")
    assert result["result"] == "PASS"
    assert result["last_sequence"] == 20


# -- catalog / manifest / journal ----------------------------------------- #
def test_catalog_projection(store: Store) -> None:
    store.append_event("r1", _event("r1", 1, extensions={
        "run_snapshot": {"phase": "red", "status": "running"}}))
    store.update_catalog("r1", {"phase": "red", "status": "running"})
    assert store.load_catalog()["runs"]["r1"]["phase"] == "red"
    rebuilt = store.rebuild_catalog()
    assert rebuilt["runs"]["r1"]["phase"] == "red"


def test_manifest_roundtrip(store: Store) -> None:
    store.save_manifest("r1", _manifest("r1"))
    assert store.load_manifest("r1")["sealed"] is True


def test_verification_journal(store: Store) -> None:
    store.append_verification({"verify_id": "v1", "target_run_id": "r1",
                               "result": "PASS", "reason_code": "ok"})
    path = store.workspace / "verification" / "v1.jsonl"
    assert path.exists()
    assert json.loads(path.read_bytes().splitlines()[0])["result"] == "PASS"


def test_checkpoint_roundtrip(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    _seal(store, "r1")
    store.save_manifest("r1", _manifest("r1"))
    cp = store.seal_checkpoint("c1", "r1", "op1")
    assert cp["checkpoint_digest"] == digest({k: v for k, v in cp.items()
                                              if k != "checkpoint_digest"})
    assert store.verify_checkpoint("c1", "r1")["result"] == "PASS"
    # idempotent re-seal with identical content
    again = store.seal_checkpoint("c1", "r1", "op1")
    assert again == cp


def test_checkpoint_overwrite_refused(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    _seal(store, "r1")
    store.save_manifest("r1", _manifest("r1"))
    store.seal_checkpoint("c1", "r1", "op1")
    with pytest.raises(DataError) as exc:
        store.seal_checkpoint("c1", "r1", "op2")
    assert exc.value.reason_code == "policy_denied"


def test_checkpoint_campaign_mismatch(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    _seal(store, "r1")
    store.save_manifest("r1", _manifest("r1"))
    store.seal_checkpoint("c1", "r1", "op1")
    path = store.trusted_root / "checkpoints" / "c1" / "r1.json"
    obj = json.loads(path.read_bytes())
    obj["campaign_id"] = "c2"
    obj["checkpoint_digest"] = digest({k: v for k, v in obj.items()
                                       if k != "checkpoint_digest"})
    path.write_bytes(canonical(obj))
    assert store.verify_checkpoint("c1", "r1")["result"] == "FAIL"


def test_append_refused_after_manifest_delete(store: Store) -> None:
    store.append_event("r1", _event("r1", 1))
    _seal(store, "r1")
    store.save_manifest("r1", _manifest("r1"))
    store.seal_checkpoint("c1", "r1", "op1")
    (store.workspace / "runs" / "r1" / "manifest.json").unlink()
    with pytest.raises(DataError):
        store.append_event("r1", _event("r1", 2))
