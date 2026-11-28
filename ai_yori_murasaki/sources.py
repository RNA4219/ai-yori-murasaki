"""Operator-owned reference documents, separate from Findings/Oracle/RSI."""
from datetime import datetime, timezone
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import stat
from urllib.parse import quote, urlsplit, parse_qs
import re

from .jsonio import DataError, canonical, digest, read_json
from .observations import local_path
from .storage import _assert_no_ancestor_reparse, sanitize

PROVIDERS = ("obsidian", "notion", "github")
MAX_TEXT = 256 * 1024
MAX_DOCUMENTS = 1000


def safe_url(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise DataError("invalid source URL")
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise DataError("invalid source URL")
    url = urlsplit(value)
    if url.username or url.password:
        raise DataError("source URL credentials forbidden")
    if url.scheme == "https" and url.hostname:
        if any(k.lower() in {"token", "key", "api_key", "access_token", "auth"}
               for k in parse_qs(url.query)):
            raise DataError("credentials in source URL forbidden")
        return value
    if url.scheme == "obsidian" and url.netloc == "open":
        args = parse_qs(url.query)
        if (set(args) == {"vault", "file"} and all(len(v) == 1 for v in args.values())
                and not any(c in args["file"][0] for c in ("\\", ":"))
                and not args["file"][0].startswith("/")
                and ".." not in args["file"][0].split("/")):
            return value
    raise DataError("source links must be HTTPS or Obsidian note links")


class Sources:
    def __init__(self, store):
        self.store = store
        self.path = Path(store.workspace) / "sources" / "catalog.json"

    def _catalog(self):
        _assert_no_ancestor_reparse(self.path)
        data = read_json(self.path) if self.path.exists() else {"documents": {}}
        if not isinstance(data, dict) or set(data) != {"documents"} or not isinstance(data["documents"], dict):
            raise DataError("source catalog invalid")
        if len(data["documents"]) > MAX_DOCUMENTS:
            raise DataError("source catalog exceeds limit")
        return data

    def add(self, *, provider, source_url, title, body, links=(), transport="file"):
        if provider not in PROVIDERS or transport not in ("file", "mcp"):
            raise DataError("invalid source provider/transport")
        source_url = safe_url(source_url)
        if not isinstance(title, str) or not 1 <= len(title) <= 512:
            raise DataError("source title must be 1..512 characters")
        if not isinstance(body, str) or not body.strip() or len(body.encode("utf-8")) > MAX_TEXT:
            raise DataError("source text empty or too large")
        if not isinstance(links, (list, tuple)) or len(links) > 32:
            raise DataError("too many source links")
        links = list(dict.fromkeys(safe_url(link) for link in links))
        # Redact registered secrets before computing IDs and comparing versions.
        content = sanitize({"provider": provider, "source_url": source_url,
                            "title": title, "body": body, "links": links}, self.store.secrets)
        safe_url(content["source_url"])
        document_id = "src-" + digest({"provider": provider, "url": content["source_url"]})[:32]
        content_hash = digest(content)
        with self.store.lock():
            catalog = self._catalog()
            previous = catalog["documents"].get(document_id)
            if previous:
                old = self.store.get_object(previous)
                if old["content_sha256"] == content_hash:
                    return {"status": "unchanged", "document_id": document_id, "ref": previous}
            elif len(catalog["documents"]) >= MAX_DOCUMENTS:
                raise DataError("source document limit reached")
            record = {**content, "document_id": document_id, "content_sha256": content_hash,
                      "imported_at": datetime.now(timezone.utc).isoformat(),
                      "previous_ref": previous, "transport": transport, "status": "reference_only"}
            ref = self.store.put_object(record)
            catalog["documents"][document_id] = ref
            self.store.atomic_json(self.path, catalog)
            return {"status": "updated" if previous else "imported", "document_id": document_id, "ref": ref}

    def get(self, document_id):
        if not isinstance(document_id, str) or not re.fullmatch(r"src-[0-9a-f]{32}", document_id):
            raise DataError("invalid source document ID")
        with self.store.lock():
            ref = self._catalog()["documents"].get(document_id)
            if not ref:
                raise DataError("source document not found")
            record = self.store.get_object(ref)
            if record.get("document_id") != document_id or record.get("status") != "reference_only":
                raise DataError("source document mismatch")
            safe_url(record["source_url"])
            for link in record["links"]:
                safe_url(link)
            return {**record, "ref": ref}

    def list(self):
        with self.store.lock():
            ids = list(self._catalog()["documents"])
            result = []
            for document_id in ids:
                record = self.get(document_id)
                result.append({k: record[k] for k in ("document_id", "provider", "source_url", "title",
                                                     "links", "imported_at", "status", "transport")})
            return sorted(result, key=lambda x: x["imported_at"], reverse=True)

    def obsidian(self, vault, note, *, title=None, links=()):
        selected = read_obsidian_note(vault, note)
        return self.add(provider="obsidian", source_url=selected.uri, title=title or selected.title,
                        body=selected.body, links=links)


def _read_source_bytes(path):
    _assert_no_ancestor_reparse(path)
    try:
        entry = path.lstat()
        if not stat.S_ISREG(entry.st_mode) or entry.st_size > MAX_TEXT:
            raise DataError("source text missing or too large")
        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        with os.fdopen(os.open(path, flags), "rb") as stream:
            _assert_no_ancestor_reparse(path)
            opened = os.fstat(stream.fileno())
            if not os.path.samestat(entry, opened) or not stat.S_ISREG(opened.st_mode):
                raise DataError("source file changed before reading")
            raw = stream.read(MAX_TEXT + 1)
            _assert_no_ancestor_reparse(path)
            if not os.path.samestat(opened, path.lstat()):
                raise DataError("source file changed while reading")
    except OSError as exc:
        raise DataError("source file unavailable") from exc
    if len(raw) > MAX_TEXT:
        raise DataError("source text too large")
    return raw


def _decode_text(raw):
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as exc:
        raise DataError("source text must be UTF-8") from exc


def read_text(path):
    return _decode_text(_read_source_bytes(Path(path)))


@dataclass(frozen=True)
class ObsidianNote:
    title: str
    uri: str
    body: str
    sha256: str


def read_obsidian_note(vault, note):
    vault, note = Path(vault), Path(note)
    if (not vault.is_absolute() or not vault.name or note.anchor or ".." in note.parts
            or note.suffix.lower() != ".md"):
        raise DataError("specify an absolute vault and a relative Markdown note")
    root = local_path(vault)
    path = local_path(root / note)
    if not path.is_relative_to(root):
        raise DataError("note outside vault")
    relative = path.relative_to(root)
    if any(part.casefold() == ".obsidian" for part in (*root.parts, *note.parts, *relative.parts)):
        raise DataError("Obsidian settings directories cannot be imported")
    uri = "obsidian://open?vault=" + quote(root.name, safe="") + "&file=" + quote(relative.with_suffix("").as_posix(), safe="")
    safe_url(uri)
    raw = _read_source_bytes(path)
    return ObsidianNote(relative.stem, uri, _decode_text(raw), hashlib.sha256(raw).hexdigest())
