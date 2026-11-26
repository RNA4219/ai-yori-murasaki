"""検証済みの案件記録から、課題の照合結果を読み取り専用で出力する。"""
import csv
import html
import io
import json
import os
from pathlib import Path
import re
import sys
import tempfile

from .decision_view import load_mapping, read_source_records
from .jsonio import DataError
from .monitor_detail import IndexUpdateRequired, read_selection
from .monitor_index import needs_index
from .monitor_summary import campaign_summaries
from .observations import load_observations, local_path
from .storage import sanitize
from .tracker_drift import build_drift


def read_drift(runtime, campaign_id: str, *, use_index: bool = True) -> dict:
    store = runtime.store
    events = {}
    try:
        selection = read_selection(store, campaign_id, check_membership=True) if use_index else None
        if selection is not None:
            summary = selection.current.summary
            events = selection.current.events
        else:
            def remember(run_id, owner, verified):
                if owner == campaign_id:
                    events[run_id] = verified

            summary = campaign_summaries(store, on_verified=remember).get(campaign_id)
    except KeyError as exc:
        raise DataError("campaign not found", "not_found") from exc
    except IndexUpdateRequired as exc:
        raise DataError("campaign index requires updating", "hold") from exc
    if summary is None:
        raise DataError("campaign not found", "not_found")
    if summary.status != "complete" or not summary.campaign:
        raise DataError("campaign evidence incomplete", "hold")
    findings = [finding for finding in summary.findings.values()
                if isinstance((finding.get("extensions") or {}).get("notion"), dict)]
    records = read_source_records(store, findings)
    observations = load_observations(runtime, campaign_id, verified_events=events)
    local_path(store.workspace / "decision-view" / "mapping.json")
    result = build_drift(campaign_id, findings, records, load_mapping(store), observations)
    if store.secrets:
        # CLI のレポート全体には、個別の CAS オブジェクトに対するサイズ制限を適用しない。
        result = {key: [sanitize(row, store.secrets) for row in value] if isinstance(value, list)
                  else sanitize(value, store.secrets) for key, value in result.items()}
    return result


_COLUMNS = ("finding_id", "title", "source_status", "classification", "reason", "stale",
            "resource_id", "match_method", "detection_id", "collected_at", "rules_used",
            "rules_unfiltered", "excluded_resource_count", "resource_type", "in_inventory")
_PRESENCE = {True: "true", False: "false", None: "unknown"}


def _rows(report):
    for item in report["items"]:
        matches = item["matches"]
        yield (item["finding_id"], item["title"], item["source_status"] or "", item["classification"],
               item["reason"] or "", str(item["stale"]).lower(),
               ", ".join(m["id"] for m in matches), ", ".join(m["method"] for m in matches),
               ", ".join(det["id"] for m in matches for det in m["detections"]),
               ", ".join(item["collected_at"]), ", ".join(item["rules_used"]),
               str(item["rules_unfiltered"]).lower(), str(item["excluded_resource_count"]),
               ", ".join(m["type"] or "" for m in matches),
               ", ".join(_PRESENCE[m["in_inventory"]] for m in matches))


def _csv_cell(value: str) -> str:
    # 表計算ソフトで、入力由来の値を数式として開かないようにする。
    if value.lstrip().startswith(("=", "+", "-", "@")) or value.startswith(("\t", "\r", "\n")):
        value = "'" + value
    return " ".join(value.splitlines())


def _md_cell(value: str) -> str:
    escaped = html.escape(" ".join(value.splitlines()), quote=False)
    return re.sub(r"([\\|`*_\[\]~])", r"\\\1", escaped)


def _render(report, fmt):
    if fmt == "json":
        return json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if fmt == "csv":
        buffer = io.StringIO(newline="")
        writer = csv.writer(buffer, lineterminator="\n")
        writer.writerow(_COLUMNS)
        writer.writerows([_csv_cell(cell) for cell in row] for row in _rows(report))
        return buffer.getvalue()
    lines = ["| " + " | ".join(_COLUMNS) + " |", "| " + " | ".join(["---"] * len(_COLUMNS)) + " |"]
    lines.extend("| " + " | ".join(_md_cell(cell) for cell in row) + " |" for row in _rows(report))
    return "\n".join(lines) + "\n"


def export_drift(runtime, campaign_id: str, fmt: str, output: Path | None) -> None:
    report = read_drift(runtime, campaign_id, use_index=needs_index(runtime.store))
    text = _render(report, fmt)
    if output is None:
        sys.stdout.write(text)
        return
    target = local_path(output)
    if any(target.is_relative_to(root) for root in (runtime.store.workspace, runtime.store.trusted_root)):
        raise DataError("export destination overlaps campaign storage")
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(text.encode("utf-8"))
        os.replace(temporary, target)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
