"""台帳の判定互換と、検証済み案件のページ表示。"""
import json
import os
import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_yori_murasaki.contracts import validate
from ai_yori_murasaki.jsonio import DataError, canonical
from ai_yori_murasaki.monitor_index import _index_path, build_index, read_page
from ai_yori_murasaki.monitor_validation import validate_monitor_event
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import handle_request
from ai_yori_murasaki.storage import Store, ZERO, event_hash

EVENT = json.loads((Path(__file__).parents[1] / "fixtures/v1/contracts.json").read_bytes())["evidence_event"]


@pytest.mark.parametrize("updates", [
    {}, {"timestamp": "2026-02-31T12:00:00Z"}, {"sequence": False}, {"unexpected": 1},
    {"target": {"kind": "fixture", "identifier": "も" * 257}}, {"extensions": {"number": 2**128}},
    {"evidence_refs": ["0" * 64] * 33}, {"campaign_id": "legacy\n"},
])
def test_schema_matches_original(updates):
    event = {**EVENT, **updates}
    def verdict(check):
        try:
            check(event)
        except DataError as exc:
            return exc.reason_code, str(exc)
        return "ok"
    assert verdict(validate_monitor_event) == verdict(lambda e: validate("evidence_event", e))


@pytest.fixture
def store(tmp_path):
    ws, tr = tmp_path / "workspace", tmp_path / "trusted"
    ws.mkdir()
    tr.mkdir()
    return Store(ws, tr)


def api(store, query):
    status, _, body = handle_request(Runtime(store), "GET", "/api/campaigns" + query)
    return status, json.loads(body)


def ledger(store, rid, cid, **extra):
    ext = {"campaign_snapshot":{"campaign_id":cid, "created_at":EVENT["timestamp"]},
           "run_snapshot":{"started_at":EVENT["timestamp"]}, **extra}
    event = dict(EVENT, run_id=rid, campaign_id=cid, sequence=1, previous_event_hash=ZERO, extensions=ext)
    event["event_hash"] = event_hash(event)
    path = store._ledger_path(rid)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical(event) + b"\n")
    return path


def test_index_pages_and_failed_rebuild(store):
    for i in range(51):
        ledger(store, f"r{i:02}", f"c{i:02}")
    obj = store.put_object({"campaign_id":"c00","finding_id":"f1","severity":"high","title":"サンプル"})
    ledger(store, "r-extra", "c00", record_kind="finding", record_ref=obj)
    ledger(store, "r-again", "c00", record_kind="finding", record_ref=obj)
    store.update_catalog("missing", {"campaign_id":"c00"})
    store.update_catalog("absent", {"campaign_id":"absent-case"})
    damaged = ledger(store, "damaged", "damaged-case")
    damaged.write_bytes(damaged.read_bytes().replace(b'scope_validated', b'state_captured'))
    store.update_catalog("damaged", {"campaign_id":"damaged-case"})
    assert build_index(store)["total"] == 53
    with patch.object(store, "read_verified_events", side_effect=AssertionError("live scan")):
        first = api(store, "?page=1")[1]
        last = api(store, "?page=2")[1]
        assert (first["total"],first["pages"],len(first["campaigns"]),len(last["campaigns"])) == (53,2,50,3)
        rows = {c["campaign_id"]:c for c in first["campaigns"]+last["campaigns"]}
        assert len(rows) == 53 and first["checked_at"] == last["checked_at"]
        assert rows["c00"]["finding_count_status"] == "partial" and rows["c00"]["finding_count"] == 1
        assert rows["c00"]["finding_excerpt"][0]["title"] == "サンプル"
        assert rows["c01"]["finding_count"] == 0
        assert rows["damaged-case"]["finding_count"] is None and rows["absent-case"]["finding_count"] is None
        assert all("finding_excerpt" not in c and "unverified_run_ids" not in c for c in api(store,"?page=1&view=blue")[1]["campaigns"])
        assert api(store,"?page=0")[0] == 400
        assert api(store,"?page=999")[1]["page"] == 2
    store._catalog_path().write_bytes(b'{"runs":{},"runs":{}}')
    with pytest.raises(DataError):
        build_index(store)
    assert api(store,"?page=1")[1] == first
    store._catalog_path().write_bytes(b'{"runs":{"absent":{"campaign_id":"absent-case"},"damaged":{"campaign_id":"damaged-case"}}}')
    ledger(store, "r-new", "new-case")
    assert build_index(store)["total"] == 54
    assert api(store,"?page=2")[1]["checked_at"] != first["checked_at"]


def test_large_store_requires_index_and_legacy_api_is_unchanged(store):
    ledger(store, "r1", "c1")
    assert set(api(store,"")[1]) == {"campaigns"}
    assert api(store,"?page=1")[1]["checked_at"] is None
    with store._catalog_path().open("wb") as stream:
        stream.truncate(17*1024*1024)
    assert api(store,"?page=1") == (409, {"error":"monitor_index_required"})


def test_incremental_inputs_and_membership(store):
    same = ledger(store, "same", "untouched")
    changed = ledger(store, "moved", "old")
    ledger(store, "keep", "old")
    store.update_catalog("orphan", {"campaign_id": "catalog-only"})
    assert build_index(store)["mode"] == "full"

    def delta(expected):
        with patch.object(store, "read_verified_events", wraps=store.read_verified_events) as read:
            result = build_index(store)
        assert result["mode"] == "incremental"
        assert [call.args[0] for call in read.call_args_list] == expected
        assert result["rechecked_runs"] == len(expected)

    delta([])
    # 同じサイズ・同じmtimeでも別の内容。実行の移籍では旧案件と新案件の両方を更新する。
    prev = changed.stat()
    ledger(store, "moved", "new")
    os.utime(changed, ns=(prev.st_atime_ns, prev.st_mtime_ns))
    assert changed.stat().st_size == prev.st_size
    delta(["moved"])
    same.unlink()
    same.parent.rmdir()
    ledger(store, "extra", "added")
    store._catalog_path().write_bytes(b'{"runs":{"orphan":{"campaign_id":"other"}}}')
    delta(["extra", "orphan"])
    page = read_page(store, 1, "operator")
    rows = {row["campaign_id"]: row for row in page["campaigns"]}
    assert set(rows) == {"new", "old", "added", "other"}
    assert rows["old"]["verified_run_count"] == rows["new"]["verified_run_count"] == 1
    assert rows["other"]["finding_count"] is None
    build_index(store, full=True)
    assert read_page(store, 1, "operator")["campaigns"] == page["campaigns"]


def test_shared_finding_repair_manifest_and_excerpt(store):
    # 検知ファイルだけが変わり、2つの実行・同じfinding_idに影響する。
    obj = store.put_object({"finding_id":"f1","title":"共有検知","severity":"high"})
    for rid in ("a", "b"):
        ledger(store, rid, "case", record_kind="finding", record_ref=obj)
    ledger(store, "stay", "stay")
    ledger(store, "c", "case", run_snapshot={})
    build_index(store)
    target = store._object_path(obj)
    original = target.read_bytes()
    target.unlink()
    assert build_index(store)["rechecked_runs"] == 2
    row = next(c for c in read_page(store, 1, "operator")["campaigns"] if c["campaign_id"] == "case")
    assert (row["finding_count_status"], row["unverified_run_ids"]) == ("partial", ["a", "b"])
    target.write_bytes(original)
    store._manifest_path("c").write_text('{"run":{"campaign_id":"case","started_at":"2099-01-01T00:00:00Z"}}')
    assert build_index(store)["rechecked_runs"] == 3
    rows = read_page(store, 1, "operator")["campaigns"]
    row = next(c for c in rows if c["campaign_id"] == "case")
    assert row["last_run_at"] == "2099-01-01T00:00:00Z"
    assert row["finding_count"] == 1 and row["finding_excerpt"][0]["title"] == "共有検知"
    build_index(store, full=True)
    assert read_page(store, 1, "operator")["campaigns"] == rows


def test_failed_publication_preserves_rows_clock_and_retry(store):
    ledger(store, "a", "old")
    build_index(store)
    before = read_page(store, 1, "operator")
    ledger(store, "a", "new")
    with patch.object(store, "read_verified_events", side_effect=PermissionError("fixture")):
        with pytest.raises(PermissionError):
            build_index(store)
    assert read_page(store, 1, "operator") == before
    with closing(sqlite3.connect(_index_path(store))) as db:
        db.execute("CREATE TRIGGER stop BEFORE INSERT ON meta BEGIN SELECT RAISE(ABORT,'stop'); END")
    with pytest.raises(sqlite3.IntegrityError, match="stop"):
        build_index(store)
    assert read_page(store, 1, "operator") == before
    with closing(sqlite3.connect(_index_path(store))) as db:
        db.execute("DROP TRIGGER stop")
    assert build_index(store)["rechecked_runs"] == 1
    after = read_page(store, 1, "operator")
    assert after["checked_at"] != before["checked_at"]
    assert [row["campaign_id"] for row in after["campaigns"]] == ["new"]


def test_legacy_snapshot_migrates_atomically(store):
    ledger(store, "a", "a")
    with closing(sqlite3.connect(_index_path(store))) as db, db:
        db.execute("CREATE TABLE meta (checked_at TEXT, total INTEGER)")
        db.execute("CREATE TABLE cases (id TEXT PRIMARY KEY, created TEXT, row_json TEXT, excerpt TEXT)")
        db.execute("INSERT INTO meta VALUES ('previous',0)")
    with patch.object(store, "read_verified_events", side_effect=OSError("I/O")):
        with pytest.raises(OSError):
            build_index(store)
    assert read_page(store, 1, "operator")["checked_at"] == "previous"
    assert build_index(store)["mode"] == "full"
    assert build_index(store)["rechecked_runs"] == 0


def test_reader_keeps_previous_generation_until_commit(store):
    from ai_yori_murasaki import monitor_index
    ledger(store, "r", "old")
    build_index(store)
    before = read_page(store, 1, "operator")
    ledger(store, "r", "new")
    write_case = monitor_index._write_case

    def observe(*args):
        write_case(*args)
        assert read_page(store, 1, "operator") == before

    with patch.object(monitor_index, "_write_case", side_effect=observe):
        build_index(store)
    assert read_page(store, 1, "operator")["campaigns"][0]["campaign_id"] == "new"
