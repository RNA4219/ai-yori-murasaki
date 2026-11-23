"""合成ストアで台帳本文を開いた回数、全一覧の一致、失敗時のロールバックを確認する。"""
import argparse
from collections import Counter
from contextlib import closing, redirect_stdout
import io
import json
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory

from ai_yori_murasaki.cli import main
from ai_yori_murasaki.jsonio import canonical
from ai_yori_murasaki.monitor_index import _index_path, read_page
from ai_yori_murasaki.storage import Store, event_hash
from monitor_scale_store import create


def verify(root, count):
    create(root, count)
    store = Store(root / "workspace", root / "trusted")
    args = ["--workspace", str(store.workspace), "--trusted-root", str(store.trusted_root), "monitor", "index"]
    active = False
    opens = Counter()

    def observe(event, args):
        if active and event == "open" and isinstance(args[0], str) and args[0].endswith("events.jsonl"):
            opens[Path(args[0]).parent.name] += 1

    sys.addaudithook(observe)

    def command(*options, success=True):
        nonlocal active
        opens.clear()
        out = io.StringIO()
        active = True
        try:
            with redirect_stdout(out):
                code = main(args + list(options))
        finally:
            active = False
        response = json.loads(out.getvalue())
        assert (code == 0) == success, response
        return response["data"]

    def rows():
        with closing(sqlite3.connect(_index_path(store).as_uri() + "?mode=ro", uri=True)) as db:
            return list(db.execute("SELECT id, created, row_json, excerpt FROM cases ORDER BY created DESC, id"))

    def published():
        return [read_page(store, 1, view) for view in ("operator", "blue")]

    rid, cid = f"r{count // 2:06}", f"c{count // 2:06}"
    file = store._ledger_path(rid)
    assert command()["total"] == count
    assert len(opens) == opens.total() == count
    print(f"initial: {opens.total()} ledgers", flush=True)
    data = json.loads(file.read_bytes())
    ref = store.put_object({"campaign_id": cid, "finding_id": "f1", "title": "棚卸し記録", "severity": "low"})
    data["extensions"].update(record_kind="finding", record_ref=ref)
    data["event_hash"] = event_hash(data)
    file.write_bytes(canonical(data) + b"\n")

    applied = command()
    assert applied["mode"] == "incremental" and opens == {rid: 1}
    expected = rows()
    assert len(expected) == count
    row = next(row for row in expected if row[0] == cid)
    assert json.loads(row[2])["finding_count"] == 1
    assert json.loads(row[3])[0]["title"] == "棚卸し記録"
    print(f"incremental: only {rid} opened", flush=True)

    rebuilt = command("--full")
    assert rebuilt["mode"] == "full" and rebuilt["total"] == count
    assert len(opens) == opens.total() == count and rows() == expected
    print(f"rebuild: all {count} rows match", flush=True)
    before_failure = published()
    data["extensions"]["campaign_snapshot"]["intent_id"] = "更新失敗後の再送"
    data["event_hash"] = event_hash(data)
    file.write_bytes(canonical(data) + b"\n")
    with closing(sqlite3.connect(_index_path(store))) as db:
        db.execute("CREATE TRIGGER stop BEFORE INSERT ON meta BEGIN SELECT RAISE(ABORT, 'stop'); END")
    command(success=False)
    after_failure = published()
    assert opens == {rid: 1} and rows() == expected and after_failure == before_failure
    with closing(sqlite3.connect(_index_path(store))) as db:
        db.execute("DROP TRIGGER stop")
    retry = command()
    assert opens == {rid: 1} and retry["checked_at"] != rebuilt["checked_at"]
    assert json.loads(next(row for row in rows() if row[0] == cid)[2])["intent_id"] == "更新失敗後の再送"
    return {"campaigns": count, "initial_ledger_opens": count, "changed_run": rid,
            "incremental_ledger_opens": 1, "unchanged_ledger_opens": 0,
            "full_rebuild_ledger_opens": count, "matching_rows": count,
            "rows_preserved_on_failure": count, "checked_at_before_failure": rebuilt["checked_at"],
            "checked_at_after_failure": after_failure[0]["checked_at"], "retry_ledger_opens": 1}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, default=100_000)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("--count は正の整数です")
    with TemporaryDirectory() as parent:
        result = verify(Path(parent) / "fixture", args.count)
    report = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_bytes(report.encode())
    print(report)
