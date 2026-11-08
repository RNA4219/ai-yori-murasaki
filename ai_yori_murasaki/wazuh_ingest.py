"""Wazuh live ingest: alerts.json -> meaning-preserving normalized events.

Default output keeps the Wazuh meaning (R-008 "normalized event"): the
normalized wazuh_alert fields (rule id/level, agent id/name, timestamp,
stable id, sanitized data) written as JSONL. No HTTP-shaped fields are
invented here.

The lab Blue path consumes a five-field access-log telemetry row
(timestamp/method/path/status/role). The legacy synthetic mapping from
Wazuh alerts to that shape (rule_level -> HTTP status, agent -> role) is
opt-in only via ``compat_rows=True``. Those rows are pseudo-observations
for the demo lab scenario, NOT measured HTTP traffic, and must not become
the default Blue input for real Wazuh data (2026-11-08 external review;
see docs/evidence/wazuh-live-20261108.md).

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


# Fields a meaning-preserving normalized wazuh_alert keeps in alerts JSONL.
# Everything else on the normalized event is derived or raw and stays out.
_NORMALIZED_ALERT_FIELDS = (
    "schema_version",
    "kind",
    "stable_id",
    "timestamp",
    "rule_id",
    "rule_level",
    "agent_id",
    "agent_name",
)


def normalized_alert_to_event(event: dict) -> dict:
    """Project a normalized wazuh_alert to a meaning-preserving JSONL event.

    Only observed alert values are kept. This function does not invent
    method/path/status/role: those exist only in the lab access-log shape
    and would be pseudo-data here.
    """
    out: dict = {}
    for field in _NORMALIZED_ALERT_FIELDS:
        value = event.get(field)
        if value is None:
            continue
        out[field] = value
    data = event.get("data")
    if isinstance(data, dict):
        # Keep the sanitized alert payload (the source of the observed
        # fields). normalize_wazuh passes the alert doc through; the
        # meaning-preserving file is an operator-facing evidence artifact,
        # not the Blue telemetry input.
        out["data"] = data
    return out


def alerts_to_normalized_events(events: Iterable[dict]) -> List[dict]:
    """Filter normalized alerts (kind == wazuh_alert) and project them."""
    out: List[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("kind") != "wazuh_alert":
            continue
        out.append(normalized_alert_to_event(ev))
    return out


def normalize_alert_lines(text: str) -> Tuple[List[dict], int]:
    """Parse Wazuh alerts JSONL text into meaning-preserving events.

    Returns (events, skipped_count). Same skipping policy as
    ingest_alerts_from_text: bad lines count as skipped, they do not fail
    the run.
    """
    events, skipped = ingest_alerts_from_text(text)
    return alerts_to_normalized_events(events), skipped


def _rule_to_telemetry_row(event: dict) -> dict:
    """Map a normalized wazuh_alert to a sanitized lab telemetry row.

    LEGACY LAB COMPAT SHAPE (opt-in via ingest_alerts_file(compat_rows=True)).
    The row carries only the five sanitized fields (timestamp/method/path/
    status/role). method is always GET, status is derived from rule_level
    (>=3 -> 200, else 403), role is the agent id. NONE of these are observed
    HTTP values: they are pseudo-observations so the Wazuh feed can be
    replayed through the lab access-log Blue scenario. Do not treat them as
    real traffic when evaluating detection on real Wazuh data; use the
    meaning-preserving normalized event JSONL instead.
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
    """LEGACY: convert normalized alerts into lab access-log compat rows.

    See _rule_to_telemetry_row: the status/role values are pseudo-data.
    Kept for the lab demo replay path only.
    """
    rows: List[dict] = []
    for ev in events:
        if not isinstance(ev, dict):
            continue
        if ev.get("kind") != "wazuh_alert":
            continue
        rows.append(_rule_to_telemetry_row(ev))
    return rows


def rows_from_jsonl(path: str | Path) -> List[dict]:
    """Read lab compat telemetry.jsonl (only with ingest compat_rows=True).

    Rows are the sanitized five-field lab rows already. This helper just
    re-validates the shape so a hand-edited file cannot smuggle extra keys
    into stage_worker / ObservationBroker. Real Wazuh data should flow
    through normalized_events_from_jsonl instead, not into the Blue
    access-log input.
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


def normalized_events_from_jsonl(path: str | Path) -> List[dict]:
    """Read meaning-preserving alerts JSONL written by ingest_alerts_file.

    Validates that each line is an object with kind == "wazuh_alert" and
    carries no lab telemetry pseudo-fields (method/path/status/role).
    """
    p = Path(str(path))
    if not p.exists():
        raise FileNotFoundError(f"normalized alerts jsonl missing: {p}")
    events: List[dict] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        ev = json.loads(line)
        if not isinstance(ev, dict):
            raise ValueError("normalized alert events must be objects")
        if ev.get("kind") != "wazuh_alert":
            raise ValueError("normalized alert event must have kind=wazuh_alert")
        for pseudo in ("method", "path", "status", "role"):
            if pseudo in ev:
                raise ValueError(
                    f"normalized alert event must not carry lab field: {pseudo}")
        events.append(ev)
    return events


def ingest_alerts_file(path: str | Path, *,
                       out_jsonl: str | Path | None = None,
                       compat_rows: bool = False,
                       out_compat_jsonl: str | Path | None = None) -> dict:
    """Read alerts.json (JSONL), write alerts-normalized.jsonl, return a summary.

    Default output (out_jsonl): meaning-preserving normalized wazuh_alert
    events, one JSON object per line. ``rows`` in the summary is the count
    of normalized events written.

    compat_rows=True additionally writes the LEGACY five-field lab access-log
    rows to ``out_compat_jsonl`` (required in that mode). Those rows contain
    pseudo HTTP fields and are for the lab demo replay path only.

    A file where not a single line parses (no events while non-empty lines
    exist) is an ingest failure, not an empty run: raise instead of
    returning an empty silent success.
    """
    p = Path(str(path))
    if not p.exists():
        raise FileNotFoundError(f"alerts file missing: {p}")
    if compat_rows and out_compat_jsonl is None:
        raise ValueError("compat_rows=True requires out_compat_jsonl")
    text = p.read_text(encoding="utf-8", errors="replace")
    raw_events, skipped = ingest_alerts_from_text(text)
    if not raw_events and skipped > 0:
        raise ValueError(
            f"no parseable wazuh alert lines in {p} ({skipped} skipped)")
    events = alerts_to_normalized_events(raw_events)

    if out_jsonl is not None:
        _write_jsonl(Path(str(out_jsonl)), events)

    rows_count = len(events)
    if compat_rows:
        compat = alerts_to_telemetry_rows(raw_events)
        _write_jsonl(Path(str(out_compat_jsonl)), compat)
        rows_count = len(compat)

    return {
        "ok": True,
        "alerts_input": str(p),
        "events": len(events),
        "rows": rows_count,
        "skipped": skipped,
        "compat_rows": compat_rows,
    }


def _write_jsonl(out_path: Path, records: List[dict]) -> None:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    lines: List[str] = []
    for rec in records:
        lines.append(json.dumps(rec, ensure_ascii=False, sort_keys=True,
                                separators=(",", ":")))
    with open(str(out_path), "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines))
        if lines:
            fh.write("\n")
