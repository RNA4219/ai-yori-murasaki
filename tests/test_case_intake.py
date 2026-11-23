from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from hashlib import sha256
import json
import os
import sqlite3
import subprocess
import sys
from threading import Barrier
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest

from ai_yori_murasaki import intake
from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.server import handle_request
from ai_yori_murasaki.storage import Store


@pytest.fixture
def store(tmp_path):
    return Store(tmp_path / "workspace", tmp_path / "trusted")


def cli(store, name, key):
    # 独立したプロセスで実際のCLIから台帳を開き直す。
    args = [sys.executable, "-m", "ai_yori_murasaki.cli",
            "--workspace", str(store.workspace), "--trusted-root", str(store.trusted_root),
            "intake", "register", "--name", name, "--idempotency-key", key]
    result = subprocess.run(args, capture_output=True, encoding="utf-8", timeout=60,
                            env={**os.environ, "PYTHONIOENCODING": "utf-8"})
    return result.returncode, json.loads(result.stdout)


def api(store, **query):
    code, _, body = handle_request(SimpleNamespace(store=store), "GET", "/api/intake?" + urlencode(query))
    return code, json.loads(body)


def test_registration_after_100000_cases_and_replay(store):
    first = intake.register(store, "既存000000", "seed-0")
    path = store.workspace / "case-intake.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        # 初期状態は実際の受付スキーマへ一括投入。検収対象の操作はすべて公開CLIを通す。
        db.executemany("INSERT INTO receipts(key_digest, name) VALUES (?, ?)",
                       ((sha256(f"seed-{i}".encode()).digest(), f"既存{i:06}") for i in range(1, 100_000)))
        assert db.execute("SELECT count(*), count(DISTINCT receipt_id) FROM receipts").fetchone() == (100_000, 100_000)

    def count():
        with closing(sqlite3.connect(path)) as db:
            return db.execute("SELECT count(*) FROM receipts").fetchone()[0]

    code, created = cli(store, "追加案件", "request-100001")
    assert code == 0 and created["status"] == "ok"
    assert created["campaign_id"] is None and created["run_id"] is None
    record = created["data"]
    assert intake.RECEIPT_ID.fullmatch(record["receipt_id"])
    assert count() == 100_001

    code, replay = cli(store, "追加案件", "request-100001")
    assert code == 0 and replay == created
    assert count() == 100_001
    code, conflict = cli(store, "別の案件", "request-100001")
    assert code == 2 and conflict["reason_code"] == "idempotency_conflict"
    assert count() == 100_001

    assert api(store, q=record["receipt_id"]) == (200, {"receipts": [record], "has_more": False})
    assert api(store, q="追加")[1]["receipts"] == [record]
    assert api(store, q=first["receipt_id"])[1]["receipts"] == [first]
    _, latest = api(store)
    assert latest["has_more"] and len(latest["receipts"]) == 50
    assert latest["receipts"][0] == record
    assert [p.name for p in store.workspace.iterdir()] == [path.name]
    assert not store.trusted_root.exists()


def test_competing_processes_on_first_registration(store):
    barrier = Barrier(4)

    def submit(_):
        barrier.wait(timeout=30)
        return cli(store, "同時受付", "one-request")

    with ThreadPoolExecutor(max_workers=4) as executor:
        replies = list(executor.map(submit, range(4)))
    assert all(code == 0 for code, _ in replies)
    receipt = replies[0][1]["data"]
    assert all(reply["data"] == receipt for _, reply in replies)
    assert intake.search(store, "") == {"receipts": [receipt], "has_more": False}


def test_search_and_visibility(store):
    assert api(store) == (200, {"receipts": [], "has_more": False})
    assert not store.workspace.exists()
    literal = intake.register(store, "棚卸し_100%完了", "ticket-one")
    other = intake.register(store, "棚卸しA100X完了", "ticket-two")
    assert api(store, q="棚卸し_100%")[1]["receipts"] == [literal]
    assert api(store, q="棚卸し")[1]["receipts"] == [literal, other]
    assert api(store, q="見つからない")[1]["receipts"] == []
    assert api(store, q="a" * 161)[0] == 400
    assert api(store, view="blue", q="棚卸し") == (200, {"status": "withheld"})
    assert handle_request(SimpleNamespace(store=store), "POST", "/api/intake")[0] == 405
    assert set(literal) == {"name", "receipt_id", "registered_at"}
    second = intake.register(store, literal["name"], "ticket-three")
    assert second["receipt_id"] != literal["receipt_id"]


@pytest.mark.parametrize(("name", "key"), [("", "ok"), ("a" * 161, "ok"), ("秘密の値", "ok"),
                                           ("案件", "  "), ("案件", "a" * 257)])
def test_invalid_request_leaves_no_database(tmp_path, name, key):
    store = Store(tmp_path / "ws", tmp_path / "tr", ("秘密",))
    with pytest.raises(DataError):
        intake.register(store, name, key)
    assert not store.workspace.exists()


def test_failed_insert_can_be_retried(store):
    intake.register(store, "既存", "old")
    path = store.workspace / "case-intake.sqlite3"
    with closing(sqlite3.connect(path)) as db, db:
        db.execute("CREATE TRIGGER reject BEFORE INSERT ON receipts BEGIN SELECT RAISE(ABORT, 'failure'); END")
    with pytest.raises(DataError, match="受付台帳を更新できません"):
        intake.register(store, "追加", "retry")
    with closing(sqlite3.connect(path)) as db, db:
        assert db.execute("SELECT count(*) FROM receipts").fetchone() == (1,)
        db.execute("DROP TRIGGER reject")
    new = intake.register(store, "追加", "retry")
    assert intake.register(store, "追加", "retry") == new
    assert len(intake.search(store, "")["receipts"]) == 2
