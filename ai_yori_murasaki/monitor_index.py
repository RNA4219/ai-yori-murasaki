"""検証時点の案件一覧を、信頼済みルートへ原子的に公開する。"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from itertools import islice
import json
import os
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import ijson

from .jsonio import DataError, MAX_JSON_DEPTH
from .monitor_summary import campaign_summaries
from .storage import Store, _safe_join

PAGE_SIZE = 50


def _index_path(store: Store) -> Path:
    identity = hashlib.sha256(os.fsencode(os.path.normcase(str(store.workspace)))).hexdigest()
    return _safe_join(store.trusted_root, f"monitor-{identity}.sqlite")


def _catalog_hints(store: Store) -> dict:
    """大きな旧索引から、読めない実行の所属を調べるためのIDだけを残す。"""
    path = store._catalog_path()
    if not path.exists():
        return {}
    hints = {}
    def checked_events(stream):
        containers = []
        for prefix, kind, value in ijson.parse(stream):
            if not containers and (prefix, kind) != ("", "start_map"):
                raise DataError("invalid catalog root")
            if prefix == "runs" and kind not in {"map_key", "start_map", "end_map"}:
                raise DataError("invalid catalog runs")
            if kind in {"start_map", "start_array"}:
                if len(containers) > MAX_JSON_DEPTH:
                    raise DataError("catalog nesting exceeds limit")
                containers.append(set() if kind == "start_map" else None)
            elif kind == "map_key":
                keys = containers[-1]
                if value in keys:
                    raise DataError("duplicate catalog key")
                keys.add(value)
            elif kind in {"end_map", "end_array"}:
                containers.pop()
            yield prefix, kind, value
    try:
        with path.open("rb") as stream:
            for rid, entry in ijson.kvitems(checked_events(stream), "runs"):
                if not isinstance(entry, dict):
                    raise DataError("invalid catalog entry")
                hints[rid] = {"campaign_id": entry.get("campaign_id")}
    except ijson.JSONError as exc:
        raise DataError("invalid catalog") from exc
    return hints


def build_index(store: Store) -> dict:
    from .server import _project_campaign, _project_finding

    target = _index_path(store)
    store.trusted_root.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(dir=store.trusted_root) as tmpdir:
        database = Path(tmpdir) / "index.sqlite"
        # 同じストアへの更新と再構築を直列化する。閲覧中の旧索引は完成まで使える。
        with store.lock():
            summaries = campaign_summaries(store, catalog=_catalog_hints(store))
            stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
            with closing(sqlite3.connect(database)) as db, db:
                db.execute("CREATE TABLE meta (checked_at TEXT, total INTEGER)")
                db.execute("CREATE TABLE cases (id TEXT PRIMARY KEY, created TEXT, row_json TEXT, excerpt TEXT)")
                for cid, summary in summaries.items():
                    row = _project_campaign(cid, summary)
                    # 一覧の転送量を制限し、全件数と詳細画面への導線を保つ。
                    row["unverified_run_ids"] = row["unverified_run_ids"][:4]
                    excerpt = [_project_finding(f) for f in islice(summary.findings.values(), 4)]
                    db.execute("INSERT INTO cases VALUES (?, ?, ?, ?)", (
                        cid, row["created_at"] or "", json.dumps(row, ensure_ascii=False),
                        json.dumps(excerpt, ensure_ascii=False)))
                db.execute("CREATE INDEX cases_by_date ON cases(created DESC, id)")
                db.execute("INSERT INTO meta VALUES (?, ?)", (stamp, len(summaries)))
            os.replace(database, target)
    return {"total": len(summaries), "checked_at": stamp}


def read_page(store: Store, page: int, view: str) -> dict | None:
    path = _index_path(store)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)) as db:
        checked_at, total = db.execute("SELECT checked_at, total FROM meta").fetchone()
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        query = "SELECT row_json, excerpt FROM cases ORDER BY created DESC, id LIMIT ? OFFSET ?"
        campaigns = []
        for data, excerpt in db.execute(query, (PAGE_SIZE, (page-1)*PAGE_SIZE)):
            row = json.loads(data)
            if view == "operator":
                row["finding_excerpt"] = json.loads(excerpt)
            else:
                row.pop("unverified_run_ids", None)
            campaigns.append(row)
    return {"campaigns": campaigns, "page": page, "pages": pages, "total": total, "checked_at": checked_at}
