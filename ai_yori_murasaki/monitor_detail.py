"""案件画面の読み取り。参照できる実行を、同一世代の検証済み索引で確定する。"""
from contextlib import closing
from dataclasses import dataclass, field
from functools import cached_property
import json
import sqlite3

from .jsonio import DataError
from .monitor_index import _PROJECTION_VERSION, _index_path, needs_index, run_file_stamps
from .monitor_inputs import file_stamp
from .monitor_summary import CampaignSummary, summarize_run
from .storage import Store, _safe_join


class IndexUpdateRequired(DataError):
    pass


@dataclass
class IndexedCase:
    store: Store
    cid: str
    inputs: dict[str, str]
    events: dict[str, list] = field(default_factory=dict, init=False)

    def _remember(self, rid, owner, events):
        if owner != self.cid:
            raise IndexUpdateRequired("実行の所属が変わりました")
        self.events[rid] = events

    @cached_property
    def summary(self):
        result = CampaignSummary()
        runs = _safe_join(self.store.workspace, "runs")
        for rid, saved in self.inputs.items():
            hint, _ = json.loads(saved)
            if json.dumps([hint, run_file_stamps(runs / rid)]) != saved:
                raise IndexUpdateRequired("対象案件の記録が更新されています")
            owner, part = summarize_run(self.store, rid, self.cid, on_verified=self._remember)
            if owner != self.cid:
                raise IndexUpdateRequired("実行の所属が変わりました")
            result.include(part)
        return result

    def get_run(self, rid):
        if rid not in self.inputs:
            raise DataError("索引の案件に属さない実行です")
        run = (self.store.load_manifest(rid) or {}).get("run")
        if not isinstance(run, dict) or run.get("run_id") != rid or run.get("campaign_id") != self.cid:
            raise DataError("実行記録を確認できません")
        return run

    def list_runs(self, cid):
        if cid != self.cid:
            raise DataError("対象外の案件です")
        result = []
        for rid in self.inputs:
            try:
                result.append(self.get_run(rid))
            except DataError:
                continue
        return result

    def report(self):
        from .assessment import _read_report
        if self.summary.status != "complete":
            raise DataError("未集計の実行があります")
        events = ((event, ext) for group in self.events.values() for event in group
                  if isinstance(ext := event.get("extensions"), dict)
                  and ext.get("assessment_version") and ext.get("assessment_ref"))
        return _read_report(self.store, self.cid, "operator", events)


@dataclass
class DetailSelection:
    current: IndexedCase
    candidates: tuple[str, ...]
    previous: IndexedCase | None


def _case(db, store, cid):
    runs = dict(db.execute("SELECT rid, inputs FROM run_cache WHERE cid = ? ORDER BY rid", (cid,)))
    if not runs:
        raise KeyError(cid)
    return IndexedCase(store, cid, runs)


def read_selection(store, cid, *, compare=False, selected=None, check_membership=False) -> DetailSelection | None:
    path = _index_path(store)
    if not path.exists():
        if needs_index(store):
            raise IndexUpdateRequired("案件索引の作成が必要です")
        return None
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        if db.execute("PRAGMA user_version").fetchone()[0] not in range(2, _PROJECTION_VERSION + 1):
            raise IndexUpdateRequired("案件索引の更新が必要です")
        if check_membership:
            indexed_stamp = db.execute("SELECT stamp FROM catalog_meta").fetchone()
            if indexed_stamp != (json.dumps(file_stamp(store._catalog_path())),):
                raise IndexUpdateRequired("実行の一覧が変更されています")
        current = _case(db, store, cid)
        candidates = ()
        if compare:
            row = db.execute("SELECT family, moment FROM case_series WHERE cid = ? AND dated = 1",
                             (cid,)).fetchone()
            if row:
                candidates = tuple(r[0] for r in db.execute(
                    "SELECT cid FROM case_series WHERE family = ? AND moment = "
                    "(SELECT max(moment) FROM case_series WHERE family = ? AND moment < ?) ORDER BY cid",
                    (row[0], *row)))
        previous_id = candidates[0] if len(candidates) == 1 else selected
        previous = _case(db, store, previous_id) if previous_id in candidates else None
        return DetailSelection(current, candidates, previous)
