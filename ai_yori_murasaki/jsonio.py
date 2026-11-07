"""曖昧な JSON と非有限数を拒否し、hash 対象の表現を固定する。"""
from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

MAX_JSON_BYTES = 16 * 1024 * 1024
MAX_JSON_DEPTH = 32


class DataError(ValueError):
    """外部入力に原因がある不正。秘密の値をメッセージへ含めない。"""

    def __init__(self, message: str, reason_code: str = "input_invalid") -> None:
        super().__init__(message)
        self.reason_code = reason_code


class ContractError(DataError):
    """契約違反。reason_code で細分する。"""

    def __init__(self, message: str, reason_code: str = "input_invalid") -> None:
        super().__init__(message, reason_code)


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise DataError("duplicate JSON key")
        result[key] = value
    return result


def _constant(_: str) -> None:
    raise DataError("non-finite JSON number")


def _float(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value):
        raise DataError("non-finite JSON number")
    return value


def _check_depth(value: Any, depth: int = 0) -> None:
    if depth > MAX_JSON_DEPTH:
        raise DataError("JSON nesting exceeds depth limit")
    if isinstance(value, dict):
        for key, item in value.items():
            if not isinstance(key, str):
                raise DataError("JSON object key must be a string")
            _check_depth(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _check_depth(item, depth + 1)


def loads(raw: str | bytes) -> Any:
    encoded = raw.encode("utf-8") if isinstance(raw, str) else raw
    if len(encoded) > MAX_JSON_BYTES:
        raise DataError("JSON input exceeds size limit")
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant,
                           parse_float=_float)
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise DataError("invalid JSON") from exc
    _check_depth(value)
    return value


def read_json(path: Path) -> Any:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise DataError("JSON input exceeds size limit")
    return loads(path.read_bytes())


def canonical(value: Any) -> bytes:
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
        # dict の非文字列キーやシリアライズによる型の変化も拒否する。
        decoded = loads(raw)
        if decoded != value:
            raise DataError("value is not JSON data")
        return raw
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        raise DataError("value is not finite JSON data") from exc


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value)).hexdigest()
