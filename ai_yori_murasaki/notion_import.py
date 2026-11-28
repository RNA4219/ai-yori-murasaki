"""ローカルの課題スナップショットを案件に記録し、読み取り画面へ渡す。"""
import csv
import hashlib
import io
import re

from .assessment import _active_run, _validate_context
from .decision_view import load_mapping
from .jsonio import DataError, MAX_JSON_BYTES
from .observations import _read_local_json, _run_events, local_path
from .storage import SHA_RE, read_regular_bytes, sanitize
from .sources import safe_url, visible_url


_BATCHES = {"notion": ("notion_csv_ref", "notion_csv/1"),
            "obsidian": ("obsidian_md_ref", "obsidian_md/1")}


def has_issue_batch(extensions):
    return any(key in extensions for key, _ in _BATCHES.values())


def _read_csv(path, fields):
    source = local_path(path)
    try:
        raw = read_regular_bytes(source, MAX_JSON_BYTES)
    except OSError as exc:
        raise DataError("Notion CSV unavailable") from exc
    try:
        reader = csv.reader(io.StringIO(raw.decode("utf-8-sig"), newline=""), strict=True)
        header = next(reader, [])
        if (not header or len(header) > 256 or any(not name.strip() or "\x00" in name for name in header)
                or len(set(header)) != len(header)):
            raise DataError("missing or duplicate CSV column")
        title_col = fields.get("title", header[0])
        if title_col not in header:
            raise DataError("mapped title column missing")
        key_col = fields.get("key")
        if key_col not in header:
            key_col = title_col
        rows = {}
        for cells in reader:
            if not cells:
                continue
            if len(rows) >= 10000:
                raise DataError("Notion CSV exceeds 10000 rows")
            if len(cells) != len(header) or any("\x00" in cell for cell in cells):
                raise DataError("invalid CSV row")
            row = dict(zip(header, cells))
            title, key = row[title_col].strip(), row[key_col].strip()
            if not title or len(title) > 512 or not key:
                raise DataError("missing key or invalid title")
            fid = "notion-" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:48]
            if fid in rows:
                raise DataError("duplicate issue key in CSV")
            # 題名へコピーする前に元の列名でマスクする。
            rows[fid] = (sanitize({title_col: title})[title_col], row)
    except (UnicodeError, csv.Error) as exc:
        raise DataError("invalid UTF-8 CSV") from exc
    return hashlib.sha256(raw).hexdigest(), rows


def load_issue_row(store, ref):
    try:
        row = store.get_object(ref).get("record")
        if not isinstance(row, dict) or not row or not all(isinstance(v, str) for v in row.values()):
            raise DataError("invalid tracker source record")
        return sanitize(row, store.secrets)
    except (OSError, DataError) as exc:
        raise DataError("tracker source record unavailable", "hold") from exc


load_csv_row = load_issue_row


def batch_findings(store, event, tracked_refs=None):
    """オペレーター画面でのみ展開する。通常の診断・ラボの台帳レコードには含めない。"""
    ext = event["extensions"]
    providers = [name for name, (key, _) in _BATCHES.items() if key in ext]
    if len(providers) != 1:
        raise DataError("ambiguous issue batch event")
    provider = providers[0]
    event_key, kind = _BATCHES[provider]
    ref = ext[event_key]
    if (event["event_type"] != "state_captured" or event["source"] != "orchestrator"
            or event["evidence_refs"] != [ref] or not isinstance(ref, str) or not SHA_RE.fullmatch(ref)):
        raise DataError("invalid issue batch event")
    if tracked_refs is not None:
        tracked_refs.add(ref)
    batch = store.get_object(ref)
    if (batch.get("kind") != kind or batch.get("campaign_id") != event["campaign_id"]
            or batch.get("run_id") != event["run_id"] or not isinstance(batch.get("items"), list)):
        raise DataError("invalid issue batch")
    origin = {}
    if provider == "obsidian":
        uri = batch.get("source_uri")
        if not isinstance(uri, str) or not uri.startswith("obsidian://open?"):
            raise DataError("invalid Obsidian source")
        origin["source_uri"] = visible_url(uri, store.secrets) or ""
    result = {}
    for item in batch["items"]:
        if not isinstance(item, dict):
            raise DataError("invalid tracker issue")
        fid, row_ref, title = item.get("finding_id"), item.get("source_record_ref"), item.get("title")
        if (not isinstance(fid, str) or not re.fullmatch(provider + r"-[0-9a-f]{48}", fid) or fid in result
                or not isinstance(row_ref, str) or not SHA_RE.fullmatch(row_ref)
                or not isinstance(title, str) or not title):
            raise DataError("invalid tracker issue")
        if tracked_refs is not None:
            tracked_refs.add(row_ref)
        title = sanitize(title, store.secrets)
        if provider == "notion" or tracked_refs is not None:
            row = load_issue_row(store, row_ref)
            if provider == "notion":
                # 旧CSVは題名の列名を保存せず、マスク前の値を別に保持していた。
                # 現在公開できる元行の値で裏付けられた題名だけを投影する。
                if not any(value.strip() == title for value in row.values()):
                    title = "[REDACTED]"
        result[fid] = {
            "finding_id": fid, "title": title, "source": provider,
            "campaign_id": event["campaign_id"], "run_id": event["run_id"],
            "extensions": {provider: {"source_record_ref": row_ref, "source_kind": kind, **origin}},
        }
    return result


def campaign_issues(store, campaign_id):
    result = {}
    for rid, entry in store.load_catalog()["runs"].items():
        if entry.get("campaign_id") != campaign_id:
            continue
        for event in _run_events(store, campaign_id, rid):
            if event["campaign_id"] != campaign_id:
                raise DataError("campaign ledger mismatch", "hold")
            if has_issue_batch(event.get("extensions") or {}):
                result.update(batch_findings(store, event))
    return result


def import_notion(runtime, campaign_id, run_id, input_path, context_path):
    store = runtime.store
    local_path(store.workspace / "decision-view" / "mapping.json")
    source_hash, rows = _read_csv(input_path, load_mapping(store)["fields"])
    return import_issue_rows(runtime, campaign_id, run_id, context_path,
                             provider="notion", source_hash=source_hash, rows=rows)


def import_issue_rows(runtime, campaign_id, run_id, context_path, *, provider, source_hash, rows, source_uri=None):
    store = runtime.store
    event_key, kind = _BATCHES[provider]
    origin = {}
    if provider == "obsidian":
        uri = safe_url(source_uri, store.secrets)
        if not uri.startswith("obsidian://open?"):
            raise DataError("invalid Obsidian source")
        origin["source_uri"] = uri
    context = _read_local_json(context_path)
    if not isinstance(context, dict):
        raise DataError("invalid import context")
    with store.lock():
        campaign = runtime.get_campaign(campaign_id)
        run = _active_run(runtime, campaign_id, run_id)
        context = _validate_context(runtime, campaign, run, context, external=True)
        previous = campaign_issues(store, campaign_id)

        new_rows = []
        for fid, (title, row) in rows.items():
            record = {"record": row}
            prior = previous.get(fid)
            if prior:
                metadata = prior["extensions"][provider]
                ref = metadata["source_record_ref"]
                if (load_issue_row(store, ref) != sanitize(row, store.secrets)
                        or prior["title"] != sanitize(title, store.secrets)
                        or metadata.get("source_uri") != origin.get("source_uri")):
                    raise DataError("issue key already exists with different data")
            else:
                new_rows.append((fid, title, record))
        if new_rows:
            # CAS への準備中に停止しても、台帳イベントが無ければ行は公開されない。
            items = [{"finding_id": fid, "title": title, "source_record_ref": store.put_object(record)}
                     for fid, title, record in new_rows]
            ref = store.put_object({"kind": kind, "campaign_id": campaign_id, **origin,
                                    "run_id": run_id, "source_sha256": source_hash,
                                    "context": context, "items": items})
            runtime.record_event(run_id, "state_captured", "orchestrator",
                                 evidence_refs=(ref,), extensions={event_key: ref})
    return {"imported_count": len(new_rows), "existing_count": len(rows) - len(new_rows)}
