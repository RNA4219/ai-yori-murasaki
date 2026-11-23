"""読み取り画面で使う、1リクエスト限りの案件集計。"""
from dataclasses import dataclass, field

from .jsonio import DataError
from .storage import ID_RE, Store


@dataclass
class CampaignSummary:
    campaign: dict = field(default_factory=dict)
    snapshot_at: str = ""
    last_run_at: str = ""
    verified_runs: int = 0
    pending_runs: list[str] = field(default_factory=list)
    findings: dict[str, dict] = field(default_factory=dict)
    remediation_refs: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if not self.verified_runs and not self.findings:
            return "unavailable"
        return "partial" if self.pending_runs else "complete"


def campaign_summaries(store: Store, *, catalog: dict | None = None) -> dict[str, CampaignSummary]:
    """台帳上の案件IDを正本とし、読めない実行の所属確認にだけ索引を使う。"""
    if catalog is None:
        catalog = store.load_catalog().get("runs", {})
    runs_dir = store.workspace / "runs"
    run_ids = set(catalog)
    if runs_dir.exists():
        run_ids.update(path.name for path in runs_dir.iterdir() if path.is_dir())
    summaries: dict[str, CampaignSummary] = {}
    for rid in sorted(run_ids):
        if not ID_RE.fullmatch(rid):
            continue
        try:
            events = store.read_verified_events(rid)
            cid = events[0].get("campaign_id")
            if not isinstance(cid, str) or not ID_RE.fullmatch(cid) or any(e.get("campaign_id") != cid for e in events):
                raise DataError("inconsistent campaign")
        except (DataError, OSError):
            entry = catalog.get(rid) or {}
            cid = entry.get("campaign_id")
            if not cid:
                try:
                    cid = ((store.load_manifest(rid) or {}).get("run") or {}).get("campaign_id")
                except (DataError, OSError):
                    pass
            if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
                raise DataError("unavailable ledger without a campaign")
            summaries.setdefault(cid, CampaignSummary()).pending_runs.append(rid)
            continue
        summary = summaries.setdefault(cid, CampaignSummary())
        complete = True
        started_at = ""
        for event in events:
            ext = event.get("extensions") or {}
            if not isinstance(ext, dict):
                continue
            snapshot = ext.get("campaign_snapshot")
            when = event.get("timestamp")
            if isinstance(snapshot, dict) and snapshot.get("campaign_id") == cid and isinstance(when, str) and when >= summary.snapshot_at:
                summary.campaign = snapshot
                summary.snapshot_at = when
            run = ext.get("run_snapshot") or {}
            if isinstance(run, dict) and isinstance(run.get("started_at"), str):
                started_at = run["started_at"]
            if ext.get("record_kind") == "remediation" and isinstance(ext.get("record_ref"), str):
                summary.remediation_refs.append(ext["record_ref"])
            if ext.get("record_kind") != "finding":
                continue
            try:
                finding = store.get_object(ext.get("record_ref"))
                fid = finding.get("finding_id")
                if not isinstance(fid, str) or not fid or finding.get("campaign_id", cid) != cid:
                    raise DataError("invalid finding")
                summary.findings.setdefault(fid, finding)
            except (DataError, OSError):
                complete = False
        if complete:
            summary.verified_runs += 1
        else:
            summary.pending_runs.append(rid)
        if not started_at:
            try:
                run = (store.load_manifest(rid) or {}).get("run") or {}
                if run.get("campaign_id") == cid and isinstance(run.get("started_at"), str):
                    started_at = run["started_at"]
            except (DataError, OSError):
                pass
        summary.last_run_at = max(summary.last_run_at, started_at)
    return summaries
