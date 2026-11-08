"""Wazuh live ingest: alerts.json -> normalized events -> blue telemetry rows.

Keep this path thin. It reads a Wazuh alerts.json (file or stream), normalizes
each line with normalize_wazuh, converts to a sanitized telemetry row
(method/path/status/role fields via rule fields), and writes a JSONL file
for stage_worker to stage as telemetry.json.

No test-only logic. No fallback to fixed rows. An individual bad line is
counted as skipped and does not block the run; a file whose every non-empty
line fails to parse raises instead of reporting an empty success.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable, List, Tuple

from .adapters.normalizers import normalize_wazuh
from .worker import _TELEMETRY_FIELDS, _sanitize_telemetry_row


def _rule_to_telemetry_row(event: dict) -> dict:
    """Map a normalized wazuh_alert to a sanitized telemetry row.

    The row carries only the five sanitized fields (timestamp/method/path/
    status/role) derived from the alert's rule id/level/agent. Raw alert data
    (for example srcip) never reaches the Blue telemetry. This is intentional:
    the blue agent sees sanitized telemetry, not raw Wazuh internals.
    """
    full_id = str(event.get("rule_id", ""))
    level = event.get("rule_level")
    agent_id = str(event.get("agent_id", ""))
    ts = str(event.get("timestamp", ""))

    method = "GET"
    path = f"/wazuh/alert/{full_id or 'unknown'}"
    status = 200 if isinstance(level, int) and level >= 3 else 403
    role = f"agent:{agent_id or 'unknown'}"
    row = {
        "timestamp": ts,
        "method": method,
        "path": path,
        "status": status,
        "role": role,
    }
    return _sanitize_telemetry_row(row)


def ingest_alerts_from_text(text: str) -> Tuple[List[dict], int]:
    """Parse Wazuh alerts JSONL/text. Returns (events, skipped_count)."""
    events: List[dict] = []
    skipped = 0
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            norm = normalize_wazuh(line.encode("utf-8"))
        except Exception:  # noqa: BLE001
            skipped += 1
            continue
        evs = norm.get("events")
        if isinstance(evs, list) and evs:
            ev = evs[0]
            if isinstance(ev, dict):
                events.append(ev)
                continue
        skipped += 1
    return events, skipped


def alerts_to_telemetry_rows(events: Iterable[dict]) -> List[dict]:
    rows: List[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("kind") != "wazuh_alert":
            continue
        rows.append(_rule_to_telemetry_row(ev))
    return rows


def rows_from_jsonl(path: str | Path) -> List[dict]:
    """Read telemetry.jsonl produced by ingest_alerts_file(out_jsonl=...).

    Rows are the sanitized telemetry rows already (only the five allowed
    fields survive _sanitize_telemetry_row at write time). This helper just
    re-validates the shape so a hand-edited file cannot smuggle extra keys
    into stage_worker / ObservationBroker.
    """
    p = Path(str(path))
    if not p.exists():
        raise FileNotFoundError(f"telemetry jsonl missing: {p}")
    rows: List[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("telemetry rows must be objects")
        for key in row:
            if key not in _TELEMETRY_FIELDS:
                raise ValueError(f"unexpected telemetry field: {key}")
        rows.append(row)
    return rows


def ingest_alerts_file(path: str | Path, *, out_jsonl: str | Path | None = None) -> dict:
    """Read alerts.json (JSONL), write telemetry.jsonl (optional), and return a summary.

    A file where not a single line parses (events == 0 while non-empty
    lines exist) is an ingest failure, not an empty run: raise instead of
    returning an empty silent success.
    """
    p = Path(str(path))
    if not p.exists():
        raise FileNotFoundError(f"alerts file missing: {p}")
    text = p.read_text(encoding="utf-8", errors="replace")
    events, skipped = ingest_alerts_from_text(text)
    if not events and skipped > 0:
        raise ValueError(
            f"no parseable wazuh alert lines in {p} ({skipped} skipped)")
    rows = alerts_to_telemetry_rows(events)

    if out_jsonl is not None:
        out_path = Path(str(out_jsonl))
        out_path.parent.mkdir(parents=True, exist_ok=True)
        lines: List[str] = []
        for row in rows:
            lines.append(json.dumps(row, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        with open(str(out_path), "w", encoding="utf-8") as fh:
            fh.write("\n".join(lines))
            if lines:
                fh.write("\n")

    return {
        "ok": True,
        "alerts_input": str(p),
        "events": len(events),
        "rows": len(rows),
        "skipped": skipped,
    }
