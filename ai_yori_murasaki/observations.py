"""ローカルの観測を検証し、非公開の CAS と実行台帳に記録する。"""
from __future__ import annotations

import os
import re
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from . import assessment
from .jsonio import MAX_JSON_BYTES, DataError, loads
from .storage import _is_reparse


def local_path(value: str | Path) -> Path:
    raw_path = os.fspath(value)
    if raw_path.replace("\\", "/").startswith("//") or "://" in raw_path:
        raise DataError("path must be local")
    path = Path(os.path.abspath(raw_path))
    if os.name == "nt":
        import ctypes

        if path.drive.startswith("\\\\") or ctypes.windll.kernel32.GetDriveTypeW(path.anchor) not in (2, 3, 5, 6):
            raise DataError("path must be on a local drive")
        if any(part.endswith((" ", ".")) or ":" in part or Path(part).is_reserved()
               for part in path.parts[1:]):
            raise DataError("invalid local file name")
    try:
        # 親から確認し、ネットワーク共有を指すリンクの先を照会しない。
        if any(_is_reparse(part) for part in (*reversed(path.parents), path)):
            raise DataError("path must not use a symlink or reparse point")
        # Windows の短いファイル名も、出力先とストアの境界比較に使える正規形に戻す。
        return path.resolve()
    except OSError as exc:
        raise DataError("cannot verify local path") from exc


def _read_local_json(value: str | Path):
    path = local_path(value)
    try:
        if not path.is_file():
            raise DataError("input must be a regular file")
        with path.open("rb") as stream:
            raw = stream.read(MAX_JSON_BYTES + 1)
        if len(raw) > MAX_JSON_BYTES:
            raise DataError("input exceeds 16 MiB")
        return loads(raw.decode("utf-8"))
    except (OSError, UnicodeError) as exc:
        raise DataError("cannot read local UTF-8 JSON") from exc


def _text(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _timestamp(value) -> bool:
    if not isinstance(value, str) or not any(sep in value for sep in ("T", "t", " ")):
        return False
    try:
        return datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


_ID_CLASS = r"\[(?:0-9|a-z|A-Z|0-9a-f|0-9A-F|a-z0-9|A-Za-z0-9)\]"
_ID_LITERAL = r"(?:[a-zA-Z0-9_-]|\\\.)"
_ID_PATTERN = re.compile(
    rf"\^?{_ID_LITERAL}*(?:{_ID_CLASS}(?:\+|\{{([0-9]{{1,3}})(?:,([0-9]{{1,3}}))?\}})){_ID_LITERAL}*\$?"
)


@lru_cache(maxsize=256)
def id_pattern(value: str) -> re.Pattern:
    """ID 全体に適用する。可変長部分を一つに制限して後戻りの重複を防ぐ。"""
    match = _ID_PATTERN.fullmatch(value) if len(value) <= 128 else None
    if not match:
        raise DataError("invalid inventory ID pattern")
    if match[1] and not 1 <= int(match[1]) <= int(match[2] or match[1]) <= 256:
        raise DataError("invalid inventory ID pattern length")
    return re.compile(value, re.ASCII)


def _validate_coverage(doc):
    if "types" in doc:
        types = doc["types"]
        if (not isinstance(types, list) or not all(_text(t) for t in types)
                or len(types) != len(set(types))):
            raise DataError("invalid inventory types")
    if "id_patterns" in doc:
        patterns = doc["id_patterns"]
        if not isinstance(patterns, dict):
            raise DataError("invalid inventory ID patterns")
        for kind, values in patterns.items():
            if (not _text(kind) or not isinstance(values, list) or not values
                    or not all(_text(v) for v in values)):
                raise DataError("invalid inventory ID patterns")
            for value in values:
                id_pattern(value)


def _validate_observation(doc: dict) -> None:
    if not isinstance(doc, dict) or doc.get("schema_version") != "1.0.0":
        raise DataError("invalid observation version")
    kind = doc.get("kind")
    if kind not in ("inventory", "detections"):
        raise DataError("invalid observation kind")
    if not _text(doc.get("method")) or not _timestamp(doc.get("collected_at")):
        raise DataError("observation requires a collection method and a timestamp with offset")
    scope = doc.get("scope")
    if not isinstance(scope, dict) or not all(_text(scope.get(key)) for key in ("provider", "account")):
        raise DataError("observation requires a provider and account")
    if not isinstance(scope.get("regions"), list) or not all(_text(region) for region in scope["regions"]):
        raise DataError("invalid observation regions")

    rows = doc.get("resources" if kind == "inventory" else "detections")
    if not isinstance(rows, list):
        raise DataError("missing observation entries")
    if kind == "inventory":
        _validate_coverage(doc)
    ids = set()
    for row in rows:
        if not isinstance(row, dict) or not _text(row.get("id")):
            raise DataError("missing observation entry ID")
        if kind == "inventory":
            if not (_text(row.get("type")) and isinstance(row.get("name"), str) and _text(row.get("state"))):
                raise DataError("invalid inventory entry")
            key = (row["id"], row["type"])
        elif not (_text(row.get("resource_id")) and _text(row.get("rule"))
                  and row.get("status") in ("active", "resolved") and _timestamp(row.get("updated_at"))
                  and ("resource_type" not in row or _text(row["resource_type"]))):
            raise DataError("invalid detection entry")
        else:
            key = row["id"]
        if key in ids:
            raise DataError("duplicate observation entry ID")
        ids.add(key)


def _run_events(store, campaign_id, run_id):
    # start_run の直後は manifest だけが存在し、初回の記録で台帳ができる。
    if not store._ledger_path(run_id).exists():
        run = (store.load_manifest(run_id) or {}).get("run") or {}
        if run.get("campaign_id") == campaign_id and run.get("status") == "running":
            return []
    return store.read_verified_events(run_id)


def import_observation(runtime, campaign_id, run_id, input_path, context_path) -> dict:
    doc = _read_local_json(input_path)
    _validate_observation(doc)
    context = _read_local_json(context_path)
    store = runtime.store
    with store.lock():
        campaign = runtime.get_campaign(campaign_id)
        run = assessment._active_run(runtime, campaign_id, run_id)
        try:
            context = assessment._validate_context(runtime, campaign, run, context, external=True)
        except TypeError as exc:
            raise DataError("invalid observation import context") from exc
        events = _run_events(store, campaign_id, run_id)
        if any(event["campaign_id"] != campaign_id for event in events):
            raise DataError("inconsistent run ownership", "hold")
        ref = store.put_object(doc)
        context_ref = store.put_object(context)
        keys = {"observation_ref": ref, "observation_context_ref": context_ref}
        if not any(event.get("extensions") == keys for event in events):
            runtime.record_event(run_id, "state_captured", "orchestrator",
                                 evidence_refs=(ref, context_ref), extensions=keys)
    return {"observation_ref": ref, "kind": doc["kind"]}


def load_observations(runtime, campaign_id, *, verified_events=None) -> list[dict]:
    """索引利用時は、同じリクエストで検証した対象案件の台帳を受け取れる。"""
    store = runtime.store
    result = []
    seen = set()
    try:
        if verified_events is None:
            runs = store.load_catalog()["runs"]
            groups = (_run_events(store, campaign_id, rid) for rid, entry in runs.items()
                      if entry.get("campaign_id") == campaign_id)
        else:
            groups = verified_events.values()
        for events in groups:
            for event in events:
                if event["campaign_id"] != campaign_id:
                    raise DataError("inconsistent run ownership")
                ext = event.get("extensions") or {}
                if "observation_ref" not in ext:
                    continue
                ref = ext["observation_ref"]
                if (event["event_type"] != "state_captured" or event["source"] != "orchestrator"
                        or event["evidence_refs"] != [ref, ext.get("observation_context_ref")]):
                    raise DataError("invalid observation reference")
                store.get_object(ext["observation_context_ref"])
                if ref not in seen:
                    doc = store.get_object(ref)
                    _validate_observation(doc)
                    result.append(doc)
                    seen.add(ref)
    except (OSError, DataError, KeyError, TypeError, AttributeError) as exc:
        raise DataError("observation evidence unavailable", "hold") from exc
    return result
