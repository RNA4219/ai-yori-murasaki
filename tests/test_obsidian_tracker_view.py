"""Obsidian と Notion の課題を同じ判断画面と照合表に表示する契約。"""
import csv
from datetime import datetime, timezone
import io

from ai_yori_murasaki.decision_view import build_view
from ai_yori_murasaki.drift_report import _render
from ai_yori_murasaki.tracker_drift import build_drift


URI = "obsidian://open?vault=Demo&file=Review%2FTasks"
MAPPING = {"fields": {"status": "進捗", "scope": "環境", "key": "番号"},
           "status_values": {"要確認": "open", "open": "done", "done": "open"}}
CAMPAIGN = {"campaign_id": "demo"}
NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)


def task(fid="obsidian-a", uri=URI):
    return {"source": "obsidian", "finding_id": fid, "title": "node-27 の点検",
            "extensions": {"obsidian": {"source_kind": "obsidian_md/1", "source_uri": uri}}}


def observation(account):
    return {"kind": "detections", "collected_at": NOW.isoformat(), "method": "架空の記録",
            "scope": {"account": account, "regions": ["east"], "provider": "demo"},
            "detections": [{"resource_id": "node-27", "id": account + "-check", "rule": "notice",
                            "status": "active"}]}


def test_mixed_report_uses_checkbox_state_and_exact_account_and_exports_sources():
    notion = {"source": "notion", "finding_id": "notion-b", "title": "node-27 の点検",
              "extensions": {"notion": {"properties": {"進捗": "要確認", "環境": "研修"}}}}
    records = {"obsidian-a": {"status": "done", "scope": "研修 環境", "key": "task"}}
    observations = [observation("研修"), observation("研修 環境")]
    report = build_drift("demo", [task(), notion], records, MAPPING, observations)
    obsidian, notion_item = report["items"]
    assert obsidian["source"] == "obsidian" and obsidian["source_uri"] == URI
    assert obsidian["closed"] and obsidian["classification"] == "closed_active"
    assert [d["id"] for m in obsidian["matches"] for d in m["detections"]] == ["研修 環境-check"]
    assert notion_item["source"] == "notion" and not notion_item["closed"]
    assert "source_uri" not in notion_item
    rows = list(csv.DictReader(io.StringIO(_render(report, "csv"))))
    assert rows[0]["source_uri"] == URI and rows[1]["source_uri"] == ""
    assert [row["source"] for row in rows] == ["obsidian", "notion"]
    assert URI.replace("&", "&amp;") in _render(report, "md")
    obsidian, notion_item = build_view(CAMPAIGN, [task(), notion], records, mapping=MAPPING, now=NOW)["items"]
    assert (obsidian["state"], obsidian["state_label"], obsidian["ref"]) == ("done", "完了", "task")
    assert obsidian["source_uri"] == URI and obsidian["link"] is None
    assert notion_item["state"] == "open"


def test_block_id_is_local_to_one_note_and_missing_scope_is_unknown():
    before = {"campaign": CAMPAIGN, "findings": [task()],
              "records": {"obsidian-a": {"key": "block", "status": "done"}}}
    current = task("obsidian-b", "obsidian://open?vault=Demo&file=Other")
    records = {"obsidian-b": {"key": "block", "status": "open"}}
    result = build_view(CAMPAIGN, [current], records, mapping=MAPPING, now=NOW, previous=before)
    assert result["items"][0]["change"] == "new"
    assert result["items"][0]["state_label"] == "未完了"
    assert [row["key"] for row in result["removed"]] == ["obsidian-a"]
    item = build_drift("demo", [current], records, MAPPING, [observation("研修")])["items"][0]
    assert item["reason"] == "out_of_scope" and not item["matches"]
