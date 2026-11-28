"""追記式の検証履歴を、1回の取得量を制限してモニターに投影する。"""
from contextlib import contextmanager
import os
import re
import stat

from .jsonio import DataError, canonical, loads
from .storage import Store, _assert_no_ancestor_reparse, _safe_join, sanitize


READ_LIMIT = 1024 * 1024
MAX_PAGE = 100
HASH = re.compile(r"[0-9a-f]{64}")


def _record(line: bytes, operator: bool, secrets: tuple[str, ...]) -> dict | None:
    try:
        record = loads(line)
    except (DataError, RecursionError):
        return None
    if not isinstance(record, dict) or not isinstance(record.get("verify_id"), str):
        return None
    projected = {}
    for key in ("verify_id", "run_id", "result", "reason_code", "checked_at"):
        value = record.get(key)
        projected[key] = value if isinstance(value, str) else None
    method = record.get("method")
    if operator and method in ("chain", "checkpoint"):
        projected["method"] = method
        seq = record.get("target_last_sequence")
        tail = record.get("target_last_event_hash")
        if type(seq) is int and seq >= 0 and isinstance(tail, str) and HASH.fullmatch(tail):
            projected.update(target_last_sequence=seq, target_last_event_hash=tail)
        digest = record.get("checkpoint_digest")
        if method == "checkpoint" and isinstance(digest, str) and HASH.fullmatch(digest):
            projected["checkpoint_digest"] = digest
    try:
        for key, value in projected.items():
            if isinstance(value, str):
                displayed = sanitize(value, secrets) if secrets else value
                projected[key] = displayed[:256]
                projected[key].encode("utf-8")
    except (DataError, UnicodeError):
        return None
    return projected


def _empty(status: str) -> dict:
    return {"verification": [], "next_before": None, "omitted": 0, "journal_status": status}


def _page(stream, end: int, limit: int, operator: bool, budget: int, secrets: tuple[str, ...]) -> dict:
    # 通常の履歴は末尾付近だけで足りる。長い記録も1リクエストの予算内で遡る。
    minimum = max(0, end - budget)
    chunks = []
    start = end
    breaks = 0
    while start > minimum and breaks <= limit:
        count = min(8192, start - minimum)
        start -= count
        stream.seek(start)
        chunk = stream.read(count)
        if len(chunk) != count:
            raise DataError("履歴が変更されました", "verification_unavailable")
        chunks.append(chunk)
        breaks += chunk.count(b"\n")
    data = b"".join(reversed(chunks))
    if start:
        boundary = data.find(b"\n")
        if boundary < 0:
            raise DataError("1件の記録が読み取り上限を超えました", "verification_record_too_large")
        start += boundary + 1
        data = data[boundary + 1:]
    result = _empty("present")
    pos = len(data)
    if not pos:
        raise DataError("1件の記録が読み取り上限を超えました", "verification_record_too_large")
    for _ in range(limit):
        line_end = pos - 1 if data[pos - 1:pos] == b"\n" else pos
        line_start = data.rfind(b"\n", 0, line_end) + 1
        row = _record(data[line_start:line_end], operator, secrets)
        if row is None:
            result["omitted"] += 1
        else:
            result["verification"].append(row)
        pos = line_start
        if not pos:
            break
    result["next_before"] = start + pos or None
    return result


@contextmanager
def _journal(store: Store, run_id: str):
    directory = _safe_join(store.workspace, "verification")
    path = _safe_join(directory, run_id + ".jsonl")
    _assert_no_ancestor_reparse(directory)
    parent = directory.lstat()
    entry = path.lstat()
    if not stat.S_ISDIR(parent.st_mode) or not stat.S_ISREG(entry.st_mode):
        raise OSError("履歴が通常のファイルではありません")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb", buffering=0) as stream:
        current = os.fstat(stream.fileno())
        _assert_no_ancestor_reparse(path)
        if not os.path.samestat(parent, directory.lstat()) or not os.path.samestat(entry, current):
            raise OSError("履歴が置き換わりました")
        yield stream, current.st_size


def read_verification(store: Store, run_id: str, *, limit: int | None, before: int | None,
                      operator: bool) -> dict:
    try:
        with _journal(store, run_id) as (stream, size):
            if before is not None:
                if not 0 < before <= size:
                    raise DataError("ページ位置が無効です", "invalid_before")
                stream.seek(before - 1)
                if stream.read(1) != b"\n":
                    raise DataError("ページ位置が無効です", "invalid_before")
            if not size:
                return _empty("empty")
            if limit is not None:
                budget = READ_LIMIT - (1 if before is not None else 0)
                return _page(stream, before if before is not None else size, limit, operator, budget, store.secrets)
            # ページを指定しない従来の呼び出しは全件を記録順に受け取る。
            if size > READ_LIMIT:
                raise DataError("ページ指定が必要です", "verification_paging_required")
            result = _empty("present")
            projected_size = 0
            raw = stream.read(size)
            if len(raw) != size:
                raise DataError("履歴が変更されました", "verification_unavailable")
            for line in raw.splitlines():
                row = _record(line, operator, store.secrets)
                if row is None:
                    result["omitted"] += 1
                else:
                    projected_size += len(canonical(row)) + 1
                    if projected_size + 128 > READ_LIMIT:
                        raise DataError("ページ指定が必要です", "verification_paging_required")
                    result["verification"].append(row)
            return result
    except FileNotFoundError:
        if before is None:
            return _empty("missing")
        raise DataError("ページ位置が無効です", "invalid_before") from None
    except OSError:
        raise DataError("履歴を読み取れません", "verification_unavailable") from None
