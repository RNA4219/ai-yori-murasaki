"""実際の CLI で同じ案件に Notion と Obsidian を取り込み、利用者向け出力を確かめる。"""
import csv
import json

from ai_yori_murasaki.cli import main
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import handle_request
from ai_yori_murasaki.storage import Store
from tests.test_tracker_drift_cli import ACCOUNT
from tests.test_tracker_drift_e2e import prepare_csv_fixture, run_cli


def test_mixed_campaign_with_custom_notion_mapping(tmp_path):
    ids = prepare_csv_fixture(tmp_path)
    inputs = tmp_path / "input"
    cid = ids["campaign_id"]
    common = ("--campaign", cid, "--run", ids["run_id"], "--context", inputs / "context.json")
    vault = tmp_path / "架空のノート"
    vault.mkdir()
    note = vault / "点検.md"
    contents = "# 点検\n普通の本文は非公開\n- [x] asset-active を確認 ^done-one\n- [ ] QuartzName を確認\n"
    note.write_text(contents, encoding="utf-8-sig")
    original = note.read_bytes()

    ingest = ("assess", "import", "--format", "obsidian", "--vault", vault,
              "--note", note.name, "--scope", ACCOUNT, *common)
    assert run_cli(tmp_path, *ingest)["data"] == {"imported_count": 2, "existing_count": 0}
    assert run_cli(tmp_path, *ingest)["data"] == {"imported_count": 0, "existing_count": 2}
    run_cli(tmp_path, "assess", "import", "--format", "notion", "--input", inputs / "notion.csv", *common)
    for name in ("inventory", "detections"):
        run_cli(tmp_path, "assess", "import", "--format", "observation", "--input", inputs / (name + ".json"), *common)
    report = run_cli(tmp_path, "assess", "drift", "--campaign", cid)
    by_title = {item["title"]: item for item in report["items"]}
    assert len(by_title) == 10
    assert by_title["asset-active を確認"]["classification"] == "closed_active"
    assert by_title["QuartzName を確認"]["classification"] == "open_resolved"
    assert by_title["Issue case-active"]["classification"] == "closed_active"

    runtime = Runtime(Store(tmp_path / "workspace", tmp_path / "trusted"))
    endpoint = f"/api/campaigns/{cid}/decision-view"
    status, _, body = handle_request(runtime, "GET", endpoint)
    assert status == 200
    projected = {it["title"]: it for it in json.loads(body)["decision_view"]["items"]}
    assert projected["asset-active を確認"]["state"] == "done"
    assert projected["QuartzName を確認"]["state"] == "open"
    assert "普通の本文" not in body.decode()
    for endpoint in (endpoint, f"/api/campaigns/{cid}/drift"):
        status, _, body = handle_request(runtime, "GET", endpoint + "?view=blue")
        assert status == 200
        assert b"withheld" in body
        assert b"asset-active" not in body

    for fmt in ("csv", "md"):
        out = tmp_path / f"report.{fmt}"
        # ファイル出力の成功時は標準出力を使わない。
        assert main(["--workspace", str(tmp_path / "workspace"), "--trusted-root", str(tmp_path / "trusted"),
                     "assess", "drift", "--campaign", cid, "--format", fmt, "--output", str(out)]) == 0
        rendered = out.read_text(encoding="utf-8")
        assert "QuartzName を確認" in rendered and "Issue case-active" in rendered
        assert "普通の本文" not in rendered
        if fmt == "csv":
            rows = {row["title"]: row for row in csv.DictReader(rendered.splitlines())}
            assert rows["asset-active を確認"]["classification"] == "closed_active"
    assert original == note.read_bytes()
