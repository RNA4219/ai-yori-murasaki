"""tools/aym_decision_view.py の試験（AYM には依存しない部分だけ）。

作成: Claude (Opus 5.5) / 2026-11-16。実行: python -m pytest tests/
"""
import importlib.util
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("aym_decision_view", ROOT / "tools" / "aym_decision_view.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
MAPPING = json.loads((ROOT / "tools" / "mapping.example.json").read_text(encoding="utf-8"))
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)


def f(fid, *, title="t", sev="high", path=None, status=None, recs=None, cves=None, refs=None):
    notion = {"url": path or f"https://example.invalid/{fid}", "properties": {"ステータス": status} if status else {}}
    if cves is not None:
        notion["cve"] = {"ids": cves}
    out = {"finding_id": fid, "title": title, "severity": sev, "location": {"path": path or f"https://example.invalid/{fid}"},
           "references": refs if refs is not None else [path or f"https://example.invalid/{fid}"], "extensions": {"notion": notion}}
    if recs is not None:
        out["extensions"]["enrichment"] = {"records": recs}
    return out


def test_state_due_and_ref_come_from_record_first():
    v = mod.build_view({"campaign_id": "c1"}, [f("a", status="対応中")],
                       {"a": {"ステータス": "判断待ち", "棚卸キー": "EX-1", "date:対応期限:start": "2026-11-20T00:00:00Z"}},
                       mapping=MAPPING, now=NOW)
    it = v["items"][0]
    assert it["state"] == "waiting" and it["state_label"] == "判断待ち"
    assert it["ref"] == "EX-1" and it["due"] == "2026-11-20"


def test_falls_back_to_finding_properties_and_unknown_status():
    v = mod.build_view({"campaign_id": "c1"}, [f("a", status="未対応"), f("b", status="なにか"), f("c")], {},
                       mapping=MAPPING, now=NOW)
    assert [i["state"] for i in v["items"]] == ["open", "other", "other"]
    assert v["items"][2]["state_label"] is None


def test_enrichment_kev_epss_and_freshness():
    exp_future = NOW.timestamp() + 3600
    recs = [
        {"kind": "kev", "cve": "CVE-2099-0001", "status": "listed", "expires_at": exp_future, "source_version": "v1", "fetched_at": "x"},
        {"kind": "kev", "cve": "CVE-2099-0002", "status": "not_listed", "expires_at": exp_future},
        {"kind": "epss", "cve": "CVE-2099-0001", "epss": 0.2, "percentile": 0.9, "expires_at": NOW.timestamp() - 1},
        {"kind": "epss", "cve": "CVE-2099-0002", "epss": 0.7, "percentile": 0.99, "expires_at": NOW.timestamp() - 1},
        {"kind": "epss", "cve": "CVE-2099-0003", "epss": None},
    ]
    v = mod.build_view({"campaign_id": "c1"}, [f("a", recs=recs, cves=["CVE-2099-0001", "CVE-2099-0002", "CVE-2099-0003"])], {},
                       mapping=MAPPING, now=NOW)
    it = v["items"][0]
    assert it["kev"] is True and it["kev_cves"] == ["CVE-2099-0001"]
    assert it["epss_max"] == 0.7 and it["epss_cve"] == "CVE-2099-0002" and it["epss_percentile"] == 0.99
    assert it["cve_count"] == 3
    assert v["freshness"]["kev"]["expired"] is False
    assert v["freshness"]["epss"]["expired"] is True


def test_freshness_uses_the_oldest_record():
    fresh = {"kind": "kev", "cve": "CVE-2099-0001", "status": "not_listed", "expires_at": NOW.timestamp() + 3600, "source_version": "new"}
    old = {"kind": "kev", "cve": "CVE-2099-0002", "status": "not_listed", "expires_at": NOW.timestamp() - 3600, "source_version": "old"}
    unknown = {"kind": "epss", "cve": "CVE-2099-0003", "epss": 0.1}
    v = mod.build_view({"campaign_id": "c1"}, [f("a", recs=[fresh]), f("b", recs=[old, unknown])], {}, mapping=MAPPING, now=NOW)
    assert v["freshness"]["kev"]["source_version"] == "old" and v["freshness"]["kev"]["expired"] is True
    assert v["freshness"]["epss"]["expired"] is True and v["freshness"]["epss"]["expires_at"] is None


def test_no_enrichment_means_no_kev_and_empty_freshness():
    v = mod.build_view({"campaign_id": "c1"}, [f("a")], {}, mapping=MAPPING, now=NOW)
    assert v["items"][0]["kev"] is False and v["items"][0]["epss_max"] is None
    assert v["freshness"] == {}


def test_link_only_http():
    v = mod.build_view({"campaign_id": "c1"}, [f("a", refs=["javascript:alert(1)", "https://example.invalid/ok"])], {},
                       mapping=MAPPING, now=NOW)
    assert v["items"][0]["link"] == "https://example.invalid/ok"


def test_changes_against_previous():
    prev = {"campaign": {"campaign_id": "p", "created_at": "2026-11-13T00:00:00Z"},
            "findings": [f("a", status="未対応"), f("b", status="未対応"), f("gone", status="対応中", title="g")], "records": {}}
    v = mod.build_view({"campaign_id": "c"}, [f("a", status="修正済"), f("b", status="未対応"), f("new", status="未対応")], {},
                       mapping=MAPPING, now=NOW, previous=prev)
    ch = {i["key"].rsplit("/", 1)[-1]: i for i in v["items"]}
    assert ch["a"]["change"] == "state_changed" and ch["a"]["prev_state"] == "open" and ch["a"]["prev_state_label"] == "未対応"
    assert ch["b"]["change"] == "unchanged"
    assert ch["new"]["change"] == "new"
    assert [r["title"] for r in v["removed"]] == ["g"]
    assert v["previous"] == {"id": "p", "created_at": "2026-11-13T00:00:00Z"}


def test_severity_outside_the_list_becomes_info():
    v = mod.build_view({"campaign_id": "c1"}, [f("a", sev="weird")], {}, mapping=MAPPING, now=NOW)
    assert v["items"][0]["severity"] == "info"


def test_pick_previous_by_intent_prefix():
    cur = {"campaign_id": "c3", "intent_id": "INT-X-BACKLOG-20261116A", "created_at": "2026-11-16T00:00:00Z"}
    cs = [
        {"campaign_id": "c1", "intent_id": "INT-X-BACKLOG-20261112B", "created_at": "2026-11-12T00:00:00Z"},
        {"campaign_id": "c2", "intent_id": "INT-X-BACKLOG-20261113A", "created_at": "2026-11-13T00:00:00Z"},
        {"campaign_id": "c9", "intent_id": "INT-OTHER-20261115A", "created_at": "2026-11-15T00:00:00Z"},
        {"campaign_id": "c4", "intent_id": "INT-X-BACKLOG-20261117A", "created_at": "2026-11-17T00:00:00Z"},
        cur,
    ]
    assert mod.pick_previous(cs, cur) == "c2"
    assert mod.pick_previous(cs, {"campaign_id": "z", "intent_id": "INT-NODATE", "created_at": "2026-11-16"}) is None


def test_output_matches_schema_keys():
    schema = json.loads((ROOT / "schema" / "decision-view.schema.json").read_text(encoding="utf-8"))
    v = mod.build_view({"campaign_id": "c1"}, [f("a", status="未対応")], {}, mapping=MAPPING, now=NOW)
    assert set(v) == set(schema["required"])
    allowed = set(schema["$defs"]["item"]["properties"])
    assert set(v["items"][0]) <= allowed
    assert set(schema["$defs"]["item"]["required"]) <= set(v["items"][0])


def test_sample_is_valid_shape():
    sample = json.loads((ROOT / "samples" / "decision-sample.json").read_text(encoding="utf-8"))
    assert sample["schema"] == "aym.decision-view/1"
    assert all(i["state"] in mod.STATES and i["severity"] in mod.SEVERITIES for i in sample["items"])


def test_rejects_credential_bearing_links():
    v = mod.build_view({"campaign_id": "c1"}, [f("a", refs=["https://user:pass@example.invalid/secret", "https://example.invalid/ok"])], {}, mapping=MAPPING, now=NOW)
    assert v["items"][0]["link"] == "https://example.invalid/ok"


def test_invalid_dates_are_not_shown_as_deadlines():
    v = mod.build_view({"campaign_id": "c1"}, [f("a")], {"a": {"date:対応期限:start": "2026-99-99"}}, mapping=MAPPING, now=NOW)
    assert v["items"][0]["due"] is None
