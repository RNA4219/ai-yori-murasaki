"""作業場所に配置された構成図を読み取り専用で公開する。"""
from __future__ import annotations

import os
from pathlib import Path
import stat

from .jsonio import DataError, loads
from .storage import ID_RE, _assert_no_ancestor_reparse


MAX_MAPS = 100
MAX_MAP_BYTES = 1024 * 1024
MAX_NAME_CHARS = 200


class MapUnavailable(Exception):
    """ファイルはあるが、構成図として読み出せない。"""


def _directory_stat(directory: Path) -> os.stat_result:
    try:
        _assert_no_ancestor_reparse(directory)
    except DataError as exc:
        raise FileNotFoundError from exc
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise FileNotFoundError
    return info


def _load(path: Path, directory_stat: os.stat_result) -> tuple[bytes, str]:
    entry = path.lstat()
    if not stat.S_ISREG(entry.st_mode) or getattr(entry, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
        raise FileNotFoundError
    if entry.st_size > MAX_MAP_BYTES:
        raise MapUnavailable

    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    with os.fdopen(os.open(path, flags), "rb") as stream:
        # 列挙後にフォルダーやファイルが置き換わった場合は内容を返さない。
        if (not os.path.samestat(directory_stat, _directory_stat(path.parent))
                or not os.path.samestat(entry, os.fstat(stream.fileno()))):
            raise MapUnavailable
        raw = stream.read(MAX_MAP_BYTES + 1)
    if len(raw) > MAX_MAP_BYTES:
        raise MapUnavailable
    try:
        doc = loads(raw.decode("utf-8-sig"))
        if not isinstance(doc, dict) or not isinstance(doc.get("nodes"), list):
            raise MapUnavailable
        name = doc.get("name")
        label = name.strip()[:MAX_NAME_CHARS] if isinstance(name, str) else ""
        label.encode("utf-8")  # API の一覧に UTF-8 で表示できる名前を使う。
    except (DataError, UnicodeError) as exc:
        raise MapUnavailable from exc
    return raw, label or path.stem


def list_maps(workspace: Path) -> list[dict[str, str]]:
    directory = workspace / "architecture"
    try:
        directory_stat = _directory_stat(directory)
        paths = sorted(directory.iterdir(), key=lambda entry: entry.name)
    except (FileNotFoundError, NotADirectoryError):
        return []
    maps = []
    for path in paths:
        if path.suffix != ".json" or ID_RE.fullmatch(path.stem) is None:
            continue
        try:
            _, name = _load(path, directory_stat)
        except (MapUnavailable, OSError):
            continue
        maps.append({"id": path.stem, "name": name})
        if len(maps) == MAX_MAPS:
            break
    return maps


def read_map(workspace: Path, map_id: str) -> bytes | None:
    if ID_RE.fullmatch(map_id) is None:
        return None
    directory = workspace / "architecture"
    try:
        directory_stat = _directory_stat(directory)
        # 大文字小文字を区別しないファイルシステムでも、実際の名前が一致するものだけを開く。
        for path in directory.iterdir():
            if path.name == map_id + ".json":
                raw, _ = _load(path, directory_stat)
                return raw
    except (FileNotFoundError, NotADirectoryError):
        pass
    return None
