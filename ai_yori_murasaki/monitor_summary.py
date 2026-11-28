"""読み取り画面で使う、1リクエスト限りの案件集計。"""
from dataclasses import dataclass, field, replace

from .jsonio import DataError
from .storage import ID_RE, SHA_RE, Store
from .tracker_source import tracker_source


@dataclass
class CampaignSummary:
    campaign: dict = field(default_factory=dict)
    snapshot_at: str = ""
    last_run_at: str = ""
    verified_runs: int = 0
    pending_runs: list[str] = field(default_factory=list)
    findings: dict[str, dict] = field(default_factory=dict)
    remediation_refs: list[str] = field(default_factory=list)
    csv_only_failures: list[str] = field(default_factory=list)
    private_finding_ids: set[str] = field(default_factory=set)

    @property
    def status(self) -> str:
        if not self.verified_runs and not self.findings:
            return "unavailable"
        return "partial" if self.pending_runs else "complete"

    def severity_counts(self) -> dict[str, int] | None:
        if self.status == "unavailable":
            return None
        counts = dict.fromkeys(("critical", "high", "medium", "low", "info", "unknown"), 0)
        for finding in self.findings.values():
            level = finding.get("severity")
            counts[level if isinstance(level, str) and level in counts else "unknown"] += 1
        return counts

    def diagnostics_only(self):
        # 索引には同じ案件の担当者用とBlue用の集計を残す。CSVだけの欠損はBlueの実行数にも含めない。
        excluded = set(self.csv_only_failures)
        return replace(self, findings={fid: item for fid, item in self.findings.items()
                                       if fid not in self.private_finding_ids},
                       verified_runs=self.verified_runs + len(excluded),
                       pending_runs=[rid for rid in self.pending_runs if rid not in excluded],
                       csv_only_failures=[], private_finding_ids=set())

    def include(self, part: "CampaignSummary") -> None:
        if part.campaign and part.snapshot_at >= self.snapshot_at:
            self.campaign, self.snapshot_at = part.campaign, part.snapshot_at
        self.last_run_at = max(self.last_run_at, part.last_run_at)
        self.verified_runs += part.verified_runs
        self.pending_runs.extend(part.pending_runs)
        self.csv_only_failures.extend(part.csv_only_failures)
        self.private_finding_ids.update(part.private_finding_ids)
        for fid, finding in part.findings.items():
            self.findings.setdefault(fid, finding)
        self.remediation_refs.extend(part.remediation_refs)


def campaign_summaries(store: Store, *, catalog: dict | None = None, on_verified=None,
                       include_csv=True) -> dict[str, CampaignSummary]:
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
        cid, part = summarize_run(store, rid, (catalog.get(rid) or {}).get("campaign_id"),
                                  on_verified=on_verified, include_csv=include_csv)
        summaries.setdefault(cid, CampaignSummary()).include(part)
    if include_csv:
        return summaries
    return {cid: summary.diagnostics_only() for cid, summary in summaries.items()}


def summarize_run(store: Store, rid: str, hint: str | None,
                  finding_refs: set[str] | None = None, *, strict_io: bool = False,
                  on_verified=None, include_csv=True) -> tuple[str, CampaignSummary]:
    """1台帳を検証する。参照先が欠損した検知も、後から復旧を検出できるようにする。"""
    summary = CampaignSummary()
    unavailable_io = FileNotFoundError if strict_io else OSError
    try:
        events = store.read_verified_events(rid)
        cid = events[0].get("campaign_id")
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid) or any(e.get("campaign_id") != cid for e in events):
            raise DataError("inconsistent campaign")
    except (DataError, unavailable_io):
        cid = hint
        if not cid:
            try:
                cid = ((store.load_manifest(rid) or {}).get("run") or {}).get("campaign_id")
            except (DataError, unavailable_io):
                pass
        if not isinstance(cid, str) or not ID_RE.fullmatch(cid):
            raise DataError("unavailable ledger without a campaign")
        summary.pending_runs.append(rid)
        return cid, summary
    if on_verified is not None:
        on_verified(rid, cid, events)
    complete = True
    csv_complete = True
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
        if include_csv and ("notion_csv_ref" in ext or "obsidian_md_ref" in ext):
            from .notion_import import batch_findings
            try:
                for fid, issue in batch_findings(store, event, finding_refs).items():
                    summary.private_finding_ids.add(fid)
                    summary.findings.setdefault(fid, issue)
            except (DataError, unavailable_io):
                csv_complete = False
        if ext.get("record_kind") != "finding":
            continue
        ref = ext.get("record_ref")
        if finding_refs is not None and isinstance(ref, str) and SHA_RE.fullmatch(ref):
            finding_refs.add(ref)
        try:
            finding = store.get_object(ref)
            fid = finding.get("finding_id")
            if not isinstance(fid, str) or not fid or finding.get("campaign_id", cid) != cid:
                raise DataError("invalid finding")
            extensions = finding.get("extensions")
            if (finding.get("source") in ("notion", "obsidian")
                    or (isinstance(extensions, dict) and tracker_source(finding)[0])):
                summary.private_finding_ids.add(fid)
            summary.findings.setdefault(fid, finding)
        except (DataError, unavailable_io):
            complete = False
    if complete and csv_complete:
        summary.verified_runs += 1
    else:
        summary.pending_runs.append(rid)
        if complete:
            summary.csv_only_failures.append(rid)
    if not started_at:
        try:
            run = (store.load_manifest(rid) or {}).get("run") or {}
            if run.get("campaign_id") == cid and isinstance(run.get("started_at"), str):
                started_at = run["started_at"]
        except (DataError, unavailable_io):
            pass
    summary.last_run_at = max(summary.last_run_at, started_at)
    return cid, summary
