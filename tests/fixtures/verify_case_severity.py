"""実ストアで重要度の件数を押し、デスクトップとスマートフォンの明細を検査する。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import expect, sync_playwright

from ai_yori_murasaki.jsonio import canonical
from ai_yori_murasaki.monitor_index import build_index
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import create_server
from ai_yori_murasaki.storage import Store, ZERO, event_hash
from monitor_scale_store import create


CASES = (
    ("critical", "管理者権限の承認記録"),
    ("medium", "共有フォルダーの公開範囲"),
    ("low", "アクセス記録の保管期間"),
    ("info", "権限一覧の更新日"),
    ("high", "退職者アカウントの停止記録"),
    ("high", "委託先アカウントの有効期限"),
    (None, "担当者が未記入の確認項目"),
)


def fixture(store):
    path = store._ledger_path("r000000")
    template = json.loads(path.read_bytes())
    campaign = dict(template["extensions"]["campaign_snapshot"], intent_id="社内アカウントの権限棚卸し")
    previous = ZERO
    with path.open("wb") as stream:
        for sequence, i in enumerate([*range(len(CASES)), 4], start=1):
            level, title = CASES[i]
            finding = dict(campaign_id="c000000", finding_id=f"finding-{i}", severity=level,
                           title=title, source="benchmark_lab", extensions={"notion": {"properties": {
                               "ステータス": "修正済" if i == 5 else "未対応"}}})
            ext = dict(template["extensions"], campaign_snapshot=campaign,
                       record_kind="finding", record_ref=store.put_object(finding))
            event = dict(template, sequence=sequence, previous_event_hash=previous, extensions=ext)
            previous = event["event_hash"] = event_hash(event)
            stream.write(canonical(event) + b"\n")


def verify(root, count, capture):
    create(root, count)
    store = Store(root / "workspace", root / "trusted")
    fixture(store)
    print(f"{count}案件の索引を構築", flush=True)
    assert build_index(store)["total"] == count
    reads = Counter()
    def observe(name, args):
        if name == "open" and isinstance(args[0], str) and args[0].endswith("events.jsonl"):
            reads[Path(args[0]).parent.name] += 1
    sys.addaudithook(observe)
    with create_server(Runtime(store), "127.0.0.1:0") as server, sync_playwright() as pw:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            browser = pw.chromium.launch()
            for width in (1440, 390):
                page = browser.new_page(viewport={"width": width, "height": 900}, reduced_motion="reduce")
                errors = []
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                reads.clear()
                page.goto(f"http://127.0.0.1:{server.server_port}/", wait_until="domcontentloaded")
                sheet = page.locator("#case-sheet")
                high = sheet.locator('.risk-levels a[href$="severity=high"]')
                expect(high).to_have_text("高2 件")
                expect(sheet.locator(".excerpt-list")).not_to_contain_text(CASES[4][1])
                assert not reads, reads
                if capture and width == 1440:
                    sheet.screenshot(path=capture / "campaign.png")
                high.focus()
                page.keyboard.press("Enter")
                panel = page.locator('[data-key="campaign-findings-c000000"]')
                expect(panel.locator(".incident-title")).to_have_text([CASES[4][1], CASES[5][1]])
                expect(panel.locator('[aria-current="true"]')).to_have_text("高2 件")
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert set(reads) == {"r000000"}, reads
                read_count = sum(reads.values())
                if capture:
                    panel.screenshot(path=capture / f"detail-{width}.png")
                panel.locator('a[href$="severity=unknown"]').click()
                expect(panel.locator(".incident-title")).to_have_text([CASES[6][1]])
                page.go_back()
                expect(panel.locator(".incident-title")).to_have_text([CASES[4][1], CASES[5][1]])
                panel.locator('a[href$="severity=all"]').click()
                expect(panel.locator(".incident-title")).to_have_count(len(CASES))
                assert sum(reads.values()) == read_count, reads
                page.locator("#switch-to-decision").click()
                levels = page.locator("#risk-levels")
                expect(levels.locator('[data-level="high"]')).to_have_text("高2 件")
                page.locator("#q").fill("一致しない文字列")
                page.locator("#f-kev").click()
                levels.locator('[data-level="high"]').click()
                expect(page.locator("#f-any")).to_have_attribute("aria-pressed", "true")
                expect(page.locator("#f-kev")).to_have_attribute("aria-pressed", "false")
                expect(page.locator("#q")).to_have_value("")
                expect(page.locator("#all .item-title")).to_have_count(2)
                assert set(page.locator("#all .item-title").all_text_contents()) == {CASES[4][1], CASES[5][1]}
                levels.locator('[data-level="unknown"]').click()
                expect(page.locator("#all .item-title")).to_have_text([CASES[6][1]])
                levels.locator('[data-level="info"]').click()
                expect(page.locator("#all .item-title")).to_have_text([CASES[3][1]])
                assert set(reads) == {"r000000"}, reads
                assert page.evaluate("document.documentElement.scrollWidth <= innerWidth")
                assert not errors, errors
                print(f"{width}px: 選択・履歴・全件表示・判断の件名が一致。無関係な台帳0本", flush=True)
                page.close()
            browser.close()
        finally:
            server.shutdown()
            thread.join()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--capture", type=Path)
    args = parser.parse_args()
    if args.count < 2:
        parser.error("2案件以上を指定してください")
    if args.capture:
        args.capture.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory() as tmp:
        verify(Path(tmp) / "store", args.count, args.capture)
