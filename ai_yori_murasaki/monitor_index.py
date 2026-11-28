"""検証時点の案件一覧を、信頼済みルートへ原子的に公開する。"""
from contextlib import closing
from datetime import datetime, timezone
import hashlib
from itertools import chain, groupby, islice
import json
import os
from operator import itemgetter
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory

import ijson

from .jsonio import DataError, MAX_JSON_BYTES, MAX_JSON_DEPTH
from .monitor_inputs import file_stamp
from .monitor_summary import CampaignSummary, summarize_run
from .storage import ID_RE, Store, _is_reparse, _safe_join

PAGE_SIZE = 50
_PROJECTION_VERSION = 5
_BLUE_CASE_FIELDS = ("campaign_id", "intent_id", "status", "created_at", "last_run_at")
_COUNT_FIELDS = ("finding_count", "finding_count_status", "verified_run_count", "unverified_run_count")


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
                hint = entry.get("campaign_id")
                if ID_RE.fullmatch(rid):
                    hints[rid] = hint if isinstance(hint, str) else ("?" if hint else None)
    except ijson.JSONError as exc:
        raise DataError("invalid catalog") from exc
    return hints


def _setup(db: sqlite3.Connection) -> None:
    # 個々のDDLも呼び出し元のトランザクションに含める（executescriptは暗黙にcommitする）。
    for statement in (
        "CREATE TABLE IF NOT EXISTS meta (checked_at TEXT, total INTEGER)",
        "CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, created TEXT, row_json TEXT, excerpt TEXT)",
        "CREATE INDEX IF NOT EXISTS cases_by_date ON cases(created DESC, id)",
        "CREATE TABLE catalog (rid TEXT PRIMARY KEY, hint TEXT)",
        "CREATE TABLE catalog_meta (stamp TEXT NOT NULL)",
        "CREATE TABLE run_cache (rid TEXT PRIMARY KEY, cid TEXT, inputs TEXT, objects TEXT, part TEXT)",
        "CREATE INDEX runs_by_case ON run_cache(cid, rid)",
    ):
        db.execute(statement)
    db.execute("PRAGMA user_version = 1")


def _hints(db, store, full):
    stamp = json.dumps(file_stamp(store._catalog_path()))
    if not full and db.execute("SELECT stamp FROM catalog_meta").fetchone() == (stamp,):
        return dict(db.execute("SELECT rid, hint FROM catalog"))
    hints = _catalog_hints(store)
    db.execute("DELETE FROM catalog")
    db.executemany("INSERT INTO catalog VALUES (?, ?)", hints.items())
    db.execute("DELETE FROM catalog_meta")
    db.execute("INSERT INTO catalog_meta VALUES (?)", (stamp,))
    return hints


def _pack(part: CampaignSummary) -> str:
    from .server import _project_finding
    # 照合用の元のfinding_idは辞書のキーに残し、値は一覧で公開する項目だけを保存する。
    return json.dumps({
        "campaign": {k: part.campaign[k] for k in ("campaign_id", "intent_id", "status", "created_at")
                     if k in part.campaign},
        "snapshot_at": part.snapshot_at, "last_run_at": part.last_run_at,
        "verified_runs": part.verified_runs, "pending_runs": part.pending_runs,
        "csv_only_failures": part.csv_only_failures,
        "private_finding_ids": sorted(part.private_finding_ids),
        # 正規の題名（最長512文字）は表示前に切らず、現在の秘密値をマスクしてから抜粋する。
        "findings": {fid: _project_finding(value, limit=512) for fid, value in part.findings.items()},
    }, ensure_ascii=False)


def _combined_summary(parts):
    summary = CampaignSummary()
    for data in parts:
        summary.include(CampaignSummary(**json.loads(data)))
    return summary


def _needs_projection_refresh(data):
    # 旧run_cacheの簡略化された検知には、原典の識別情報と題名のマスク根拠が残っていない。
    part = json.loads(data)
    return bool(part["findings"]) and "private_finding_ids" not in part


def _blue_counts(cid, summary):
    from .server import _project_campaign
    row = _project_campaign(cid, summary.diagnostics_only(), "blue")
    return {key: row[key] for key in _COUNT_FIELDS}


def _valid_blue_counts(value):
    if not isinstance(value, dict) or set(value) != set(_COUNT_FIELDS):
        return False
    verified, pending = value["verified_run_count"], value["unverified_run_count"]
    if type(verified) is not int or verified < 0 or type(pending) is not int or pending < 0:
        return False
    count, status = value["finding_count"], value["finding_count_status"]
    if status == "unavailable":
        return verified == 0 and count is None
    if type(count) is not int or count < 0:
        return False
    if status == "complete":
        return verified > 0 and pending == 0
    return status == "partial" and pending > 0 and (verified > 0 or count > 0)


def _write_case(db, cid, parts):
    from .decision_view import series_position
    from .server import _project_campaign
    summary = _combined_summary(parts)
    row = _project_campaign(cid, summary)
    row["blue_counts"] = _blue_counts(cid, summary)
    row["unverified_run_ids"] = row["unverified_run_ids"][:4]
    excerpt = list(islice(summary.findings.values(), 4))
    db.execute("INSERT OR REPLACE INTO cases VALUES (?, ?, ?, ?)", (
        cid, row["created_at"] or "", json.dumps(row, ensure_ascii=False),
        json.dumps(excerpt, ensure_ascii=False)))
    position = series_position(summary.campaign)
    if position:
        db.execute("INSERT INTO case_series VALUES (?, ?, ?, ?)", (cid, *position))


def run_file_stamps(folder):
    if _is_reparse(folder):
        return ["linked"]
    if not folder.is_dir():
        return []
    return [file_stamp(folder / "events.jsonl"), file_stamp(folder / "manifest.json")]


def needs_index(store):
    catalog = store._catalog_path()
    runs = store.workspace / "runs"
    return ((catalog.exists() and catalog.stat().st_size > MAX_JSON_BYTES) or
            (runs.is_dir() and next(islice(runs.iterdir(), 1000, None), None) is not None))


def _refresh(store, db, full):
    version = db.execute("PRAGMA user_version").fetchone()[0]
    if version == 0:
        _setup(db)
        full = True
    elif version not in range(1, _PROJECTION_VERSION + 1):
        raise DataError("未知のモニター索引形式です")
    if version < 2:
        db.execute("CREATE TABLE case_series (cid TEXT PRIMARY KEY, family TEXT, moment TEXT, dated INTEGER)")
        db.execute("CREATE INDEX series_by_time ON case_series(family, moment, cid)")
    if version < _PROJECTION_VERSION:
        db.execute(f"PRAGMA user_version = {_PROJECTION_VERSION}")
    hints = _hints(db, store, full)
    runs = _safe_join(store.workspace, "runs")
    objects = _safe_join(store.workspace, "objects")
    ids = set(hints)
    if runs.exists():
        ids.update(path.name for path in runs.iterdir() if path.is_dir() and ID_RE.fullmatch(path.name))

    previous = {}
    if full:
        db.execute("DELETE FROM run_cache")
    else:
        previous = {rid: (cid, inputs, refs) for rid, cid, inputs, refs in
                    db.execute("SELECT rid, cid, inputs, objects FROM run_cache")}
    refresh_projection = set()
    if not full and version < _PROJECTION_VERSION:
        refresh_projection = {rid for rid, part in db.execute("SELECT rid, part FROM run_cache")
                              if _needs_projection_refresh(part)}
    affected = set()
    verified = 0
    object_stamps = {}

    def refs_stamp(refs):
        result = {}
        for sha in sorted(refs):
            if sha not in object_stamps:
                object_stamps[sha] = file_stamp(objects / (sha + ".json"))
            result[sha] = object_stamps[sha]
        return json.dumps(result)

    for rid in sorted(ids):
        files = run_file_stamps(runs / rid)
        inputs = json.dumps([hints.get(rid), files])
        old = previous.pop(rid, None)
        if (old and rid not in refresh_projection and inputs == old[1]
                and (old[2] == "{}" or old[2] == refs_stamp(json.loads(old[2])))):
            continue
        refs = set()
        cid, part = summarize_run(store, rid, hints.get(rid), refs, strict_io=True)
        verified += 1
        db.execute("INSERT OR REPLACE INTO run_cache VALUES (?, ?, ?, ?, ?)",
                   (rid, cid, inputs, refs_stamp(refs), _pack(part)))
        if not full:
            affected.add(cid)
            if old:
                affected.add(old[0])
    for rid, (cid, _, _) in previous.items():
        affected.add(cid)
        db.execute("DELETE FROM run_cache WHERE rid = ?", (rid,))

    if full or version < _PROJECTION_VERSION:
        db.execute("DELETE FROM case_series")
        db.execute("DELETE FROM cases")
        rows = db.execute("SELECT cid, part FROM run_cache ORDER BY cid, rid")
        for cid, group in groupby(rows, key=itemgetter(0)):
            _write_case(db, cid, map(itemgetter(1), group))
    else:
        for cid in affected:
            db.execute("DELETE FROM case_series WHERE cid = ?", (cid,))
            db.execute("DELETE FROM cases WHERE id = ?", (cid,))
            rows = db.execute("SELECT part FROM run_cache WHERE cid = ? ORDER BY rid", (cid,))
            first = rows.fetchone()
            if first is not None:
                _write_case(db, cid, map(itemgetter(0), chain((first,), rows)))
    total = db.execute("SELECT count(*) FROM cases").fetchone()[0]
    stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds")
    db.execute("DELETE FROM meta")
    db.execute("INSERT INTO meta VALUES (?, ?)", (stamp, total))
    return {"total": total, "checked_at": stamp, "mode": "full" if full else "incremental",
            "rechecked_runs": verified, "removed_runs": len(previous)}


def build_index(store: Store, *, full: bool = False) -> dict:
    target = _index_path(store)
    store.trusted_root.mkdir(parents=True, exist_ok=True)
    with store.lock(), TemporaryDirectory(dir=store.trusted_root) as tmpdir:
        existing = target.exists()
        database = target if existing else Path(tmpdir) / "index.sqlite"
        with closing(sqlite3.connect(database)) as db, db:
            # 長い全件再検証中にも読者は直前にcommitされた世代を読める。
            db.execute("PRAGMA journal_mode = WAL")
            db.execute("BEGIN IMMEDIATE")
            result = _refresh(store, db, full)
        if not existing:
            os.replace(database, target)
    return result


def read_page(store: Store, page: int, view: str) -> dict | None:
    path = _index_path(store)
    if not path.exists():
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        checked_at, total = db.execute("SELECT checked_at, total FROM meta").fetchone()
        pages = max(1, (total + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, pages)
        query = "SELECT row_json, excerpt FROM cases ORDER BY created DESC, id LIMIT ? OFFSET ?"
        campaigns = []
        version = db.execute("PRAGMA user_version").fetchone()[0]
        for data, excerpt in db.execute(query, (PAGE_SIZE, (page-1)*PAGE_SIZE)):
            row = json.loads(data)
            blue_counts = row.pop("blue_counts", None)
            if version != _PROJECTION_VERSION:
                from .monitor_detail import IndexUpdateRequired
                if view == "operator" or version not in range(1, _PROJECTION_VERSION):
                    raise IndexUpdateRequired("案件索引の公開項目を更新してください")
                parts = [part for part, in db.execute("SELECT part FROM run_cache WHERE cid = ? ORDER BY rid",
                                                       (row["campaign_id"],))]
                if not parts or any(_needs_projection_refresh(part) for part in parts):
                    raise IndexUpdateRequired("Blue の案件集計を更新してください")
                blue_counts = _blue_counts(row["campaign_id"], _combined_summary(parts))
            if view == "operator":
                from .server import _project_finding
                row["finding_excerpt"] = [_project_finding(item, store.secrets) for item in json.loads(excerpt)]
            else:
                if not _valid_blue_counts(blue_counts):
                    from .monitor_detail import IndexUpdateRequired
                    raise IndexUpdateRequired("Blue の案件集計を更新してください")
                row = {**{key: row[key] for key in _BLUE_CASE_FIELDS}, **blue_counts}
            campaigns.append(row)
    return {"campaigns": campaigns, "page": page, "pages": pages, "total": total, "checked_at": checked_at}
