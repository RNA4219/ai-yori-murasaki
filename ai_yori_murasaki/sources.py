"""Operator-owned reference documents, separate from Findings/Oracle/RSI."""
from datetime import datetime, timezone
from dataclasses import dataclass
import hashlib
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, unquote_plus, urlsplit
import re

from .jsonio import MAX_JSON_BYTES, DataError, canonical, digest, loads
from .observations import local_path
from .storage import SECRET_KEYS, _assert_no_ancestor_reparse, read_regular_bytes, sanitize

PROVIDERS = ("obsidian", "notion", "github")
MAX_TEXT = 256 * 1024
MAX_DOCUMENTS = 1000
_CREDENTIAL_PARAMS = SECRET_KEYS | {"auth", "key", "access_token", "client_secret", "refresh_token",
                                    "id_token", "sig", "signature", "x_amz_signature", "x_goog_signature"}


def safe_url(value, secrets=(), *, allow_http=False):
    if not isinstance(value, str) or not 1 <= len(value) <= 4096:
        raise DataError("invalid source URL")
    if any(ord(c) <= 32 or ord(c) == 127 for c in value) or "\\" in value:
        raise DataError("invalid source URL")
    try:
        url = urlsplit(value)
    except ValueError as exc:
        raise DataError("invalid source URL") from exc
    if secrets:
        decoded = unquote(value)
        query = unquote_plus(url.query)
        if any(secret in value or secret in decoded or secret in query for secret in secrets):
            raise DataError("registered secret in source URL")
    if url.username or url.password:
        raise DataError("source URL credentials forbidden")
    if (url.scheme == "https" or (allow_http and url.scheme == "http")) and url.hostname:
        if any(k.casefold().replace("-", "_") in _CREDENTIAL_PARAMS
               for part in (url.query, url.fragment) for k in parse_qs(part)):
            raise DataError("credentials in source URL forbidden")
        return value
    if url.scheme == "obsidian" and url.netloc == "open":
        args = parse_qs(url.query, keep_blank_values=True)
        if not url.path and not url.fragment and set(args) == {"vault", "file"} and all(len(v) == 1 for v in args.values()):
            vault, file = args["vault"][0], args["file"][0]
            parts = [vault, *file.split("/")]
            # file の末尾は拡張子を外したノート名。設定フォルダーと同名のノートもある。
            if ("/" not in vault and all(part.casefold() != ".obsidian" for part in parts[:-1])
                    and all(part and part not in (".", "..") and not re.search(r"[\\:\x00-\x1f\x7f]", part)
                            for part in parts)):
                return value
    raise DataError("source links must be HTTPS or Obsidian note links")


def visible_url(value, secrets, *, allow_http=False):
    # 読み取りでは、現行の条件を満たさない過去のリンクだけを伏せる。
    try:
        return safe_url(value, secrets, allow_http=allow_http)
    except DataError:
        return None


def visible_http_url(value, secrets=()):
    if isinstance(value, str) and value.lower().startswith(("http://", "https://")):
        return visible_url(value, secrets, allow_http=True)
    return None


def visible_obsidian_uri(value, secrets=()):
    if isinstance(value, str) and value.startswith("obsidian://open?"):
        return visible_url(value, secrets) or ""
    return ""


class Sources:
    def __init__(self, store):
        self.store = store
        self.path = Path(store.workspace) / "sources" / "catalog.json"

    def _catalog(self):
        _assert_no_ancestor_reparse(self.path)
        data = loads(read_regular_bytes(self.path, MAX_JSON_BYTES)) if self.path.exists() else {"documents": {}}
        if not isinstance(data, dict) or set(data) != {"documents"} or not isinstance(data["documents"], dict):
            raise DataError("source catalog invalid")
        if len(data["documents"]) > MAX_DOCUMENTS:
            raise DataError("source catalog exceeds limit")
        return data

    def add(self, *, provider, source_url, title, body, links=(), transport="file"):
        if provider not in PROVIDERS or transport not in ("file", "mcp"):
            raise DataError("invalid source provider/transport")
        source_url = safe_url(source_url, self.store.secrets)
        if not isinstance(title, str) or not 1 <= len(title) <= 512:
            raise DataError("source title must be 1..512 characters")
        if not isinstance(body, str) or not body.strip() or len(body.encode("utf-8")) > MAX_TEXT:
            raise DataError("source text empty or too large")
        if not isinstance(links, (list, tuple)) or len(links) > 32:
            raise DataError("too many source links")
        links = list(dict.fromkeys(safe_url(link, self.store.secrets) for link in links))
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
            secrets = self.store.secrets
            return {**record, "ref": ref, "title": sanitize(record["title"], secrets),
                    "body": sanitize(record["body"], secrets),
                    "source_url": visible_url(record["source_url"], secrets),
                    "links": [url for link in record["links"] if (url := visible_url(link, secrets))]}

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
    try:
        return read_regular_bytes(path, MAX_TEXT)
    except OSError as exc:
        raise DataError("source file unavailable") from exc


def _decode_text(raw):
    try:
        return raw.decode("utf-8-sig").replace("\r\n", "\n").replace("\r", "\n")
    except UnicodeError as exc:
        raise DataError("source text must be UTF-8") from exc


def read_text(path):
    return _decode_text(_read_source_bytes(local_path(path)))


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
