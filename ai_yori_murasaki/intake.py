"""初期監査から独立した案件受付とモニター向けの読み取り。"""
from contextlib import closing
from hashlib import sha256
from pathlib import Path
import re
import sqlite3

from .jsonio import DataError
from .storage import Store, _safe_join, sanitize

PAGE_SIZE = 50
RECEIPT_ID = re.compile(r"int-[0-9a-f]{32}")


def _path(store: Store) -> Path:
    filename = "case-intake.sqlite3"
    for suffix in ("-journal", "-wal", "-shm"):
        _safe_join(store.workspace, filename + suffix)
    return _safe_join(store.workspace, filename)


def _valid_text(value: str, limit: int) -> bool:
    return bool(value and len(value) <= limit and value.replace("　", " ").isprintable())


def register(store: Store, name: str, key: str) -> dict:
    from . import _intake_index

    name = name.strip()
    if not _valid_text(name, 160) or sanitize(name, store.secrets) != name:
        raise DataError("案件名が不正です")
    if not _valid_text(key, 256) or not key.strip():
        raise DataError("再送キーが不正です")
    path = _path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        record = _intake_index.register(path, sha256(key.encode()).digest(), name)
    except OSError as exc:
        raise DataError("受付台帳を更新できません", "intake_unavailable") from exc
    if record is None:
        raise DataError("同じ再送キーに異なる案件名が指定されました", "idempotency_conflict")
    receipt_id, name, registered_at = record
    return {"receipt_id": receipt_id, "name": name, "registered_at": registered_at}


def search(store: Store, query: str) -> dict:
    query = query.strip()
    if query and not _valid_text(query, 160):
        raise DataError("検索条件が不正です", "invalid_query")
    path = _path(store)
    page = {"receipts": [], "has_more": False}
    if not path.exists():
        return page

    sql = "SELECT receipt_id, name, registered_at FROM receipts "
    params = []
    if RECEIPT_ID.fullmatch(query):
        sql += "WHERE receipt_id = ?"
        params.append(query)
    elif query:
        prefix = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
        sql += "WHERE name LIKE ? ESCAPE '\\' ORDER BY name COLLATE NOCASE, receipt_id"
        params.append(prefix + "%")
    else:
        sql += "ORDER BY rowid DESC"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("PRAGMA trusted_schema = OFF")
        db.row_factory = sqlite3.Row
        rows = db.execute(sql + " LIMIT ?", (*params, PAGE_SIZE + 1)).fetchall()
    page["receipts"] = [sanitize(dict(row), store.secrets) for row in rows[:PAGE_SIZE]]
    page["has_more"] = len(rows) > PAGE_SIZE
    return page
