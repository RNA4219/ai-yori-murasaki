"""10万案件の実ストアをブラウザーで開き、画面別に台帳のopenを記録する。"""
import argparse
from collections import Counter
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from threading import Thread

from playwright.sync_api import sync_playwright

from monitor_scale_store import create
from ai_yori_murasaki.jsonio import canonical
from ai_yori_murasaki.monitor_index import build_index
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import create_server, handle_request
from ai_yori_murasaki.storage import Store, event_hash


TITLES = ("前々回の確認項目", "前回の確認項目", "今回の確認項目")
SUFFIXES = ("", "/runs", "/assessment", "/rsi", "/decision-view")


def seed_case(store, number, offset):
    cid, rid = f"c{number:06}", f"r{number:06}"
    day = 21 + offset
    stamp = f"2026-11-{day}T00:00:00Z"
    campaign = dict(campaign_id=cid, created_at=stamp, intent_id=f"棚卸し-202611{day}", status="created")
    run = dict(run_id=rid, campaign_id=cid, phase="verify", started_at=stamp, status="completed")
    path = store._ledger_path(rid)
    event = json.loads(path.read_bytes())
    ref = store.put_object(dict(campaign_id=cid, finding_id=f"f-{cid}", title=TITLES[offset], severity="high"))
    event.update(timestamp=stamp, extensions=dict(campaign_snapshot=campaign, run_snapshot=run,
                                                  record_kind="finding", record_ref=ref))
    event["event_hash"] = event_hash(event)
    path.write_bytes(canonical(event) + b"\n")
    store.save_manifest(rid, {"run": run})
    return {**run, "campaign": campaign}


def comparable(response):
    view = response.get("decision_view")
    if isinstance(view, dict):
        view.pop("generated_at", None)
    return response


def reference(root, store, number):
    root.mkdir()
    (root / "workspace").mkdir()
    (root / "trusted").mkdir()
    small = Store(root / "workspace", root / "trusted")
    for offset, i in enumerate(range(number - 2, number + 1)):
        run = seed_case(store, i, offset)
        rid = run["run_id"]
        path = small._ledger_path(rid)
        path.parent.mkdir(parents=True)
        path.write_bytes(store._ledger_path(rid).read_bytes())
        small.save_manifest(rid, store.load_manifest(rid))
        ref = json.loads(path.read_bytes())["extensions"]["record_ref"]
        assert small.put_object(store.get_object_bytes(ref)) == ref
        small.update_catalog(rid, run)
    cid = f"c{number:06}"
    expected = {}
    for suffix in SUFFIXES:
        path = f"/api/campaigns/{cid}{suffix}"
        status, _, body = handle_request(Runtime(small), "GET", path)
        expected[path] = (status, comparable(json.loads(body)))
    return expected


def verify(root, count):
    dataset = root / "scale"
    create(dataset, count)
    store = Store(dataset / "workspace", dataset / "trusted")
    number = count // 2
    cid, rid, prev = f"c{number:06}", f"r{number:06}", f"r{number-1:06}"
    prefix = f"/api/campaigns/{cid}"
    expected = reference(root / "reference", store, number)
    assert expected[prefix][1]["findings"][0]["title"] == TITLES[2]
    assert expected[prefix + "/runs"][1]["runs"][0]["run_id"] == rid
    decision = expected[prefix + "/decision-view"][1]["decision_view"]
    assert decision["previous"]["id"] == f"c{number-1:06}"
    assert decision["items"][0]["change"] == "new" and decision["removed"][0]["title"] == TITLES[1]
    print(f"{count}案件を生成。索引を構築中", flush=True)
    assert build_index(store)["total"] == count
    counts = Counter()
    catalog = str(store._catalog_path())
    runs = str(store.workspace / "runs")
    active = False

    def count_opens(kind, args):
        if active and kind == "open" and isinstance(path := args[0], str):
            if path == catalog:
                counts["catalog"] += 1
            elif path.startswith(runs) and Path(path).name == "events.jsonl":
                counts[Path(path).parent.name] += 1

    sys.addaudithook(count_opens)
    screens = {}
    with create_server(Runtime(store), "127.0.0.1:0") as server, sync_playwright() as pw:
        thread = Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            browser = pw.chromium.launch()
            page = browser.new_page()
            page.set_default_timeout(10000)
            errors = []
            page.on("pageerror", lambda error: errors.append(str(error)))
            page.on("requestfailed", lambda request: errors.append(request.url))
            page.add_init_script("""{
                const fetch = window.fetch.bind(window);
                window.caseResponses = {};
                window.fetch = async (...args) => {
                    const response = await fetch(...args);
                    const path = new URL(response.url).pathname;
                    if (path.startsWith('/api/campaigns/')) {
                        const status = response.status;
                        response.clone().json().then(body => { window.caseResponses[path] = [status, body]; });
                    }
                    return response;
                };
            }""")
            for name, allowed, suffixes in (("detail", {rid}, SUFFIXES[:4]), ("decision", {rid, prev}, SUFFIXES[4:])):
                counts.clear()
                active = True
                if name == "detail":
                    page.goto(f"http://127.0.0.1:{server.server_port}/#/campaign/{cid}", wait_until="domcontentloaded")
                    page.get_by_role("heading", name="棚卸し-20261123", exact=True).wait_for()
                    page.locator(f'[data-key="campaign-findings-{cid}"]').get_by_text(TITLES[2], exact=True).wait_for()
                else:
                    page.locator("#switch-to-decision").click()
                    page.locator("#view").wait_for()
                    for title in TITLES[1:]:
                        fold = page.locator("#changes details").filter(has_text=title)
                        if fold.get_attribute("open") is None:
                            fold.locator("summary").click()
                        fold.get_by_text(title, exact=False).last.wait_for()
                    assert TITLES[0] not in page.locator("#changes").inner_text()
                page.wait_for_function("n => Object.keys(window.caseResponses).length >= n", arg=len(suffixes))
                responses = page.evaluate("window.caseResponses")
                active = False
                assert not errors, errors
                assert set(responses) == {prefix + suffix for suffix in suffixes}, list(responses)
                for path, (status, body) in responses.items():
                    actual = status, comparable(body)
                    assert actual == expected[path], (path, actual, expected[path])
                assert set(counts) == allowed, (name, counts, allowed)
                screens[name] = dict(ledger_opens=dict(sorted(counts.items())), unrelated_ledger_opens=0,
                                     catalog_opens=0, matching_api_responses=len(responses))
            version = browser.version
            browser.close()
        finally:
            active = False
            server.shutdown()
            thread.join()
    return dict(campaigns=count, selected=cid, previous=decision["previous"]["id"], browser=version,
                catalog_bytes=Path(catalog).stat().st_size, screens=screens,
                matching_api_responses=sum(s["matching_api_responses"] for s in screens.values()),
                unrelated_ledger_opens=0)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.count < 6:
        parser.error("6案件以上を指定してください")
    with TemporaryDirectory() as temp:
        result = verify(Path(temp), args.count)
    payload = json.dumps(result, indent=2, ensure_ascii=False) + "\n"
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_bytes(payload.encode())
    print(payload)
