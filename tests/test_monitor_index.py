"""台帳の判定互換と、検証済み案件のページ表示。"""
import json
from pathlib import Path
from unittest.mock import patch

import pytest

from ai_yori_murasaki.contracts import validate
from ai_yori_murasaki.jsonio import DataError, canonical
from ai_yori_murasaki.monitor_index import build_index
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
