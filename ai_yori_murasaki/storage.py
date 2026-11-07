"""Storage: single-source ledger, CAS, catalog projection, checkpoints, verification journal.

Owns: Workspace/runs/<run_id>/events.jsonl (append-only chain), objects/<sha256>.json
(CAS), catalog.json (rebuildable projection), manifest.json, verification/<id>.jsonl,
and TRUSTED_ROOT/checkpoints/<campaign>/<run>.json.

Structural contract + hash/CAS checks only; semantic validation is runtime's job.
"""
from __future__ import annotations

import contextlib
import hashlib
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Iterator

from .jsonio import MAX_JSON_BYTES, DataError, canonical, digest, loads

ZERO = "0" * 64
MAX_EVENT_BYTES = 1024 * 1024
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
SECRET_KEYS = {"password", "passwd", "secret", "token", "api_key", "apikey",
               "authorization", "credential", "credentials"}


# --------------------------------------------------------------------------- #
# sanitize (kept from existing implementation)
# --------------------------------------------------------------------------- #
_AUTH_KEYS = {"operator_id", "granted_at", "expires_at", "scope_digest"}
_TS_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}T[0-2]\d:[0-5]\d:[0-5]\d(?:\.\d{1,9})?(?:Z|[+-][0-2]\d:[0-5]\d)")


def _is_strict_id(value: Any) -> bool:
    return isinstance(value, str) and bool(ID_RE.fullmatch(value))


def _is_timestamp(value: Any) -> bool:
    if not isinstance(value, str) or not _TS_RE.fullmatch(value):
        return False
    try:
        from datetime import datetime as _dt
        parsed = _dt.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return False
    return parsed.tzinfo is not None


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and bool(SHA_RE.fullmatch(value))


def _sanitize_authorization(value: Any, secrets: tuple[str, ...]) -> Any:
    """Narrowly typed exception for Campaign.authorization scope-grant metadata.

    Only the exact four keys operator_id/granted_at/expires_at/scope_digest
    are preserved (validated strictly); any unknown key or malformed value
    falls back to full redaction.  HTTP Authorization strings remain redacted.
    """
    if not isinstance(value, dict):
        return "[REDACTED]"
    if set(value.keys()) != _AUTH_KEYS:
        return "[REDACTED]"
    if not _is_strict_id(value["operator_id"]):
        return "[REDACTED]"
    if not _is_timestamp(value["granted_at"]):
        return "[REDACTED]"
    if not _is_timestamp(value["expires_at"]):
        return "[REDACTED]"
    if not _is_sha(value["scope_digest"]):
        return "[REDACTED]"
    return {
        "operator_id": value["operator_id"],
        "granted_at": value["granted_at"],
        "expires_at": value["expires_at"],
        "scope_digest": value["scope_digest"],
    }


def sanitize(value: Any, secrets: tuple[str, ...] = ()) -> Any:
    canonical(value)
    if any(not isinstance(s, str) or not s for s in secrets):
        raise DataError("registered secrets must be nonempty strings")
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            safe_key = sanitize(key, secrets)
            if safe_key in result:
                raise DataError("redaction would merge JSON keys")
            normalized = key.lower().replace("-", "_") if isinstance(key, str) else ""
            if normalized in SECRET_KEYS:
                if normalized == "authorization":
                    result[safe_key] = _sanitize_authorization(item, secrets)
                else:
                    result[safe_key] = "[REDACTED]"
            else:
                result[safe_key] = sanitize(item, secrets)
        return result
    if isinstance(value, list):
        return [sanitize(item, secrets) for item in value]
    if isinstance(value, str):
        for secret in sorted(set(secrets), key=len, reverse=True):
            value = value.replace(secret, "[REDACTED]")
    return value


# --------------------------------------------------------------------------- #
# path safety
# --------------------------------------------------------------------------- #
def _is_reparse(path: Path) -> bool:
    try:
        st = path.lstat()
    except FileNotFoundError:
        return False
    if path.is_symlink():
        return True
    if os.name == "nt":
        return bool(getattr(st, "st_file_attributes", 0) & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    return False


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not ID_RE.fullmatch(value):
        raise DataError(f"invalid {what}")
    return value


def _check_sha(value: str, what: str) -> str:
    if not isinstance(value, str) or not SHA_RE.fullmatch(value):
        raise DataError(f"invalid {what}")
    return value


def _safe_join(root: Path, *parts: str) -> Path:
    """Reject '..', absolute parts, symlinks/reparse points before resolve."""
    for part in parts:
        if not isinstance(part, str) or not part or part in {".", ".."}:
            raise DataError("unsafe path component")
        if "/" in part or "\\" in part or os.path.isabs(part):
            raise DataError("unsafe path component")
    candidate = root.joinpath(*parts)
    # raw-path safety before resolve
    if _is_reparse(candidate):
        raise DataError("path must not be a symlink or reparse point")
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise DataError("path escapes root") from exc
    return resolved


def _assert_disjoint(workspace: Path, trusted_root: Path) -> None:
    ws = workspace.resolve()
    tr = trusted_root.resolve()
    if ws == tr:
        raise DataError("workspace and trusted_root must differ")
    for a, b in ((ws, tr), (tr, ws)):
        try:
            b.relative_to(a)
        except ValueError:
            continue
        raise DataError("workspace and trusted_root must not be nested")


def _assert_outside_repo(path: Path) -> None:
    repo = Path(__file__).resolve().parent.parent
    try:
        path.resolve().relative_to(repo)
    except ValueError:
        return
    raise DataError("storage root must be outside the repository")


def _assert_no_ancestor_reparse(path: Path) -> None:
    """Reject any ancestor of path (up to filesystem root) that is a reparse point."""
    current = path
    while True:
        if _is_reparse(current):
            raise DataError("storage root has a symlink/reparse ancestor")
        parent = current.parent
        if parent == current:
            return
        current = parent


# Process-wide reentrant lock keyed by lockfile path.  Nested Store instances
# sharing the same workspace must not deadlock; the outermost holder owns the
# OS lock and releases it on exit.
_PATH_LOCKS: dict[str, threading.RLock] = {}
_PATH_LOCKS_GUARD = threading.Lock()
_TLS = threading.local()


def _path_lock(key: str) -> threading.RLock:
    with _PATH_LOCKS_GUARD:
        lock = _PATH_LOCKS.get(key)
        if lock is None:
            lock = threading.RLock()
            _PATH_LOCKS[key] = lock
        return lock


def _depth(key: str) -> int:
    depths = getattr(_TLS, "depths", None)
    if depths is None:
        return 0
    return depths.get(key, 0)


def _set_depth(key: str, value: int) -> None:
    depths = getattr(_TLS, "depths", None)
    if depths is None:
        depths = {}
        _TLS.depths = depths
    if value:
        depths[key] = value
    else:
        depths.pop(key, None)


# --------------------------------------------------------------------------- #
# event hash
# --------------------------------------------------------------------------- #
def event_hash(event: dict) -> str:
    body = {k: v for k, v in event.items() if k != "event_hash"}
    return hashlib.sha256(canonical(body) + event["previous_event_hash"].encode("ascii")).hexdigest()


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #
class Store:
    def __init__(self, workspace: str | Path, trusted_root: str | Path,
                 secrets: tuple[str, ...] = ()) -> None:
        ws = Path(workspace)
        tr = Path(trusted_root)
        if not ws.is_absolute() or not tr.is_absolute():
            raise DataError("workspace and trusted_root must be absolute paths")
        if _is_reparse(ws) or _is_reparse(tr):
            raise DataError("storage roots must not be symlinks")
        _assert_disjoint(ws, tr)
        _assert_outside_repo(ws)
        _assert_outside_repo(tr)
        _assert_no_ancestor_reparse(ws)
        _assert_no_ancestor_reparse(tr)
        self.workspace = ws.resolve()
        self.trusted_root = tr.resolve()
        self.secrets = tuple(secrets)
        self._lock = threading.RLock()
        self._lockfile = self.workspace / ".store.lock"

    @classmethod
    def from_env(cls) -> "Store":
        ws = os.environ.get("AYM_WORKSPACE")
        tr = os.environ.get("AYM_TRUSTED_ROOT")
        if not ws or not tr:
            raise DataError("AYM_WORKSPACE and AYM_TRUSTED_ROOT are required")
        raw = os.environ.get("AYM_SECRETS", "")
        secrets = tuple(s for s in raw.split("\n") if s)
        return cls(ws, tr, secrets)

    # -- paths ------------------------------------------------------------- #
    def _run_dir(self, run_id: str) -> Path:
        _check_id(run_id, "run_id")
        return _safe_join(self.workspace, "runs", run_id)

    def _ledger_path(self, run_id: str) -> Path:
        return _safe_join(self._run_dir(run_id), "events.jsonl")

    def _manifest_path(self, run_id: str) -> Path:
        return _safe_join(self._run_dir(run_id), "manifest.json")

    def _object_path(self, sha: str) -> Path:
        _check_sha(sha, "digest")
        return _safe_join(self.workspace, "objects", sha + ".json")

    def _catalog_path(self) -> Path:
        return _safe_join(self.workspace, "catalog.json")

    def _verification_path(self, verify_id: str) -> Path:
        _check_id(verify_id, "verify_id")
        return _safe_join(self.workspace, "verification", verify_id + ".jsonl")

    def _checkpoint_path(self, campaign_id: str, run_id: str) -> Path:
        _check_id(campaign_id, "campaign_id")
        _check_id(run_id, "run_id")
        return _safe_join(self.trusted_root, "checkpoints", campaign_id, run_id + ".json")

    # -- locking / atomic -------------------------------------------------- #
    @contextlib.contextmanager
    def lock(self, deadline: float = 30.0) -> Iterator[None]:
        """Reentrant per-path lock; outermost holder takes the OS lock.

        The OS lock on workspace/.store.lock is held across the whole
        read->check->append/catalog-update transaction so multiple processes
        and multiple Store instances cannot interleave sequence numbers.
        """
        key = str(self._lockfile)
        plock = _path_lock(key)
        acquired = plock.acquire(timeout=deadline)
        if not acquired:
            raise DataError("lock acquisition timed out", "hold")
        try:
            depth = _depth(key)
            if depth == 0:
                self._acquire_os_lock(deadline)
            _set_depth(key, depth + 1)
            try:
                with self._lock:
                    yield
            finally:
                _set_depth(key, depth)
                if depth == 0:
                    self._release_os_lock()
        finally:
            plock.release()

    def _acquire_os_lock(self, deadline: float) -> None:
        self._lockfile.parent.mkdir(parents=True, exist_ok=True)
        # Create the lockfile if missing; tolerate races between processes.
        try:
            fd = os.open(self._lockfile, os.O_RDWR | os.O_CREAT, 0o600)
        except FileExistsError:
            fd = os.open(self._lockfile, os.O_RDWR, 0o600)
        end = time.monotonic() + deadline
        try:
            if os.name == "nt":
                import msvcrt
                while True:
                    try:
                        os.lseek(fd, 0, os.SEEK_SET)
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                        break
                    except OSError:
                        if time.monotonic() >= end:
                            raise DataError("lock acquisition timed out", "hold")
                        time.sleep(0.01)
            else:
                import fcntl
                while True:
                    try:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                        break
                    except OSError:
                        if time.monotonic() >= end:
                            raise DataError("lock acquisition timed out", "hold")
                        time.sleep(0.01)
        except BaseException:
            os.close(fd)
            raise
        self._lock_fd = fd

    def _release_os_lock(self) -> None:
        fd = getattr(self, "_lock_fd", None)
        if fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
            self._lock_fd = None

    def atomic_json(self, path: Path, data: Any) -> None:
        raw = canonical(data)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp",
                                        dir=str(path.parent))
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            with contextlib.suppress(FileNotFoundError):
                tmp.unlink()

    # -- CAS --------------------------------------------------------------- #
    def put_object(self, data: dict | bytes) -> str:
        if isinstance(data, (bytes, bytearray)):
            value = loads(bytes(data))
        elif isinstance(data, dict):
            value = data
        else:
            raise DataError("object must be a dict or canonical JSON bytes")
        raw = canonical(sanitize(value, self.secrets))
        sha = hashlib.sha256(raw).hexdigest()
        path = self._object_path(sha)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            with open(path, "xb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
        except FileExistsError:
            if path.read_bytes() != raw:
                raise DataError("object digest collision or corruption")
        return sha

    def get_object_bytes(self, sha: str) -> bytes:
        path = self._object_path(sha)
        if _is_reparse(path):
            raise DataError("object must not be a symlink")
        if path.stat().st_size > MAX_JSON_BYTES:
            raise DataError("object exceeds size limit")
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != sha:
            raise DataError("object hash mismatch")
        return raw

    def get_object(self, sha: str) -> dict:
        raw = self.get_object_bytes(sha)
        value = loads(raw)
        if canonical(value) != raw:
            raise DataError("object is not canonical JSON")
        if not isinstance(value, dict):
            raise DataError("object is not a JSON object")
        return value

    # -- ledger ------------------------------------------------------------ #
    def _read_events_raw(self, run_id: str) -> list[dict]:
        path = self._ledger_path(run_id)
        if _is_reparse(path):
            raise DataError("ledger must not be a symlink")
        if not path.exists():
            return []
        raw = path.read_bytes()
        if raw and not raw.endswith(b"\n"):
            raise DataError("incomplete ledger write")
        return [loads(line) for line in raw.splitlines()]

    def read_events(self, run_id: str) -> list[dict]:
        return self._read_events_raw(run_id)

    def _inspect_chain(self, run_id: str, events: list[dict]) -> dict:
        head = ZERO
        for sequence, event in enumerate(events, 1):
            if not isinstance(event, dict):
                raise DataError("event must be an object")
            if event.get("run_id") != run_id:
                raise DataError("event run_id mismatch")
            _validate_evidence_event(event)
            if event.get("sequence") != sequence:
                raise DataError("event sequence mismatch")
            if event.get("previous_event_hash") != head:
                raise DataError("event chain mismatch")
            if event_hash(event) != event.get("event_hash"):
                raise DataError("event hash mismatch")
            head = event["event_hash"]
        return {"result": "PASS", "reason_code": "ok",
                "last_sequence": len(events), "last_event_hash": head}

    def verify_chain(self, run_id: str) -> dict:
        try:
            events = self._read_events_raw(run_id)
        except DataError as exc:
            return {"result": "FAIL", "reason_code": "verdict_fail",
                    "last_sequence": 0, "last_event_hash": ZERO,
                    "detail": str(exc)}
        if not events:
            return {"result": "HOLD", "reason_code": "hold",
                    "last_sequence": 0, "last_event_hash": ZERO}
        try:
            return self._inspect_chain(run_id, events)
        except DataError as exc:
            return {"result": "FAIL", "reason_code": "verdict_fail",
                    "last_sequence": 0, "last_event_hash": ZERO,
                    "detail": str(exc)}

    def _is_sealed(self, run_id: str) -> bool:
        events = self._read_events_raw(run_id)
        if events and events[-1].get("event_type") == "chain_sealed":
            return True
        manifest = self.load_manifest(run_id)
        return bool(manifest and manifest.get("sealed"))

    def append_event(self, run_id: str, event: dict) -> str:
        if not isinstance(event, dict):
            raise DataError("event must be an object")
        if {"sequence", "previous_event_hash", "event_hash"} & event.keys():
            raise DataError("derived event fields must not be supplied")
        if "run_id" in event and event.get("run_id") != run_id:
            raise DataError("event run_id mismatch")
        with self.lock():
            if self._is_sealed(run_id):
                raise DataError("chain is sealed", "policy_denied")
            events = self._read_events_raw(run_id)
            self._inspect_chain(run_id, events)  # refuse append on broken chain
            record = sanitize(event, self.secrets)
            record["run_id"] = run_id
            record["sequence"] = len(events) + 1
            record["previous_event_hash"] = events[-1]["event_hash"] if events else ZERO
            record["event_hash"] = event_hash(record)
            _validate_evidence_event(record)
            raw = canonical(record) + b"\n"
            if len(raw) > MAX_EVENT_BYTES:
                raise DataError("event exceeds 1 MiB cap")
            path = self._ledger_path(run_id)
            path.parent.mkdir(parents=True, exist_ok=True)
            self._append_locked(path, raw)
            return record["event_hash"]

    @staticmethod
    def _append_locked(path: Path, raw: bytes) -> None:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        try:
            if os.name == "nt":
                import msvcrt
                # Lock a fixed byte range at offset 0 so all writers agree.
                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_LOCK, 1)
                try:
                    os.lseek(descriptor, 0, os.SEEK_END)
                    view = memoryview(raw)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise DataError("short write to ledger")
                        view = view[written:]
                    os.fsync(descriptor)
                finally:
                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(descriptor, fcntl.LOCK_EX)
                try:
                    view = memoryview(raw)
                    while view:
                        written = os.write(descriptor, view)
                        if written <= 0:
                            raise DataError("short write to ledger")
                        view = view[written:]
                    os.fsync(descriptor)
                finally:
                    fcntl.flock(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)

    # -- checkpoint -------------------------------------------------------- #
    def seal_checkpoint(self, campaign_id: str, run_id: str, operator_id: str) -> dict:
        _check_id(operator_id, "operator_id")
        with self.lock():
            events = self._read_events_raw(run_id)
            if not events:
                raise DataError("cannot seal empty chain", "hold")
            self._inspect_chain(run_id, events)
            if not self._is_sealed(run_id):
                raise DataError("cannot issue checkpoint for unsealed chain", "hold")
            checkpoint = {
                "campaign_id": campaign_id,
                "run_id": run_id,
                "last_sequence": len(events),
                "last_event_hash": events[-1]["event_hash"],
                "sealed_at": events[-1].get("timestamp"),
                "operator_id": operator_id,
            }
            checkpoint["checkpoint_digest"] = digest(checkpoint)
            path = self._checkpoint_path(campaign_id, run_id)
            if path.exists():
                try:
                    existing = loads(path.read_bytes())
                except DataError as exc:
                    raise DataError("existing checkpoint unreadable", "hold") from exc
                if canonical(existing) != canonical(checkpoint):
                    raise DataError("checkpoint already exists with different content",
                                    "policy_denied")
                return existing
            self.atomic_json(path, checkpoint)
            return checkpoint

    def verify_checkpoint(self, campaign_id: str, run_id: str) -> dict:
        path = self._checkpoint_path(campaign_id, run_id)
        if not path.exists():
            return {"result": "HOLD", "reason_code": "checkpoint_missing"}
        try:
            checkpoint = loads(path.read_bytes())
        except DataError:
            return {"result": "HOLD", "reason_code": "hold"}
        if not isinstance(checkpoint, dict):
            return {"result": "HOLD", "reason_code": "hold"}
        if checkpoint.get("campaign_id") != campaign_id:
            return {"result": "FAIL", "reason_code": "checkpoint_mismatch"}
        if checkpoint.get("run_id") != run_id:
            return {"result": "FAIL", "reason_code": "checkpoint_mismatch"}
        body = {k: v for k, v in checkpoint.items() if k != "checkpoint_digest"}
        if checkpoint.get("checkpoint_digest") != digest(body):
            return {"result": "FAIL", "reason_code": "checkpoint_mismatch"}
        chain = self.verify_chain(run_id)
        if chain["result"] != "PASS":
            return {"result": "FAIL", "reason_code": "verdict_fail"}
        if (chain["last_sequence"] != checkpoint.get("last_sequence")
                or chain["last_event_hash"] != checkpoint.get("last_event_hash")):
            return {"result": "FAIL", "reason_code": "checkpoint_mismatch"}
        return {"result": "PASS", "reason_code": "ok"}

    # -- verification journal --------------------------------------------- #
    def append_verification(self, record: dict) -> None:
        if not isinstance(record, dict):
            raise DataError("verification record must be an object")
        verify_id = _check_id(record.get("verify_id"), "verify_id")
        raw = canonical(sanitize(record, self.secrets)) + b"\n"
        path = self._verification_path(verify_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._append_locked(path, raw)

    # -- catalog projection ------------------------------------------------ #
    def load_catalog(self) -> dict:
        path = self._catalog_path()
        if not path.exists():
            return {"schema_version": "1.0.0", "runs": {}}
        value = loads(path.read_bytes())
        if not isinstance(value, dict):
            raise DataError("catalog must be an object")
        return value

    def update_catalog(self, run_id: str, entry: dict) -> None:
        _check_id(run_id, "run_id")
        if not isinstance(entry, dict):
            raise DataError("catalog entry must be an object")
        with self.lock():
            catalog = self.load_catalog()
            runs = catalog.setdefault("runs", {})
            runs[run_id] = sanitize(entry, self.secrets)
            self.atomic_json(self._catalog_path(), catalog)

    def rebuild_catalog(self) -> dict:
        """Rebuild catalog projection from verified ledger snapshots.

        Entry shape: run_snapshot fields at top level, campaign_snapshot nested
        under key "campaign".  Only chains that pass hash verification are
        projected; corrupt chains are skipped (not treated as success).
        """
        runs: dict[str, dict] = {}
        runs_dir = self.workspace / "runs"
        if runs_dir.exists():
            for child in sorted(runs_dir.iterdir()):
                if not child.is_dir() or _is_reparse(child):
                    continue
                run_id = child.name
                if not ID_RE.fullmatch(run_id):
                    continue
                try:
                    events = self._read_events_raw(run_id)
                    self._inspect_chain(run_id, events)
                except DataError:
                    continue
                entry: dict = {}
                campaign: dict = {}
                for event in events:
                    ext = event.get("extensions") or {}
                    if not isinstance(ext, dict):
                        continue
                    if isinstance(ext.get("run_snapshot"), dict):
                        entry.update(ext["run_snapshot"])
                    if isinstance(ext.get("campaign_snapshot"), dict):
                        campaign.update(ext["campaign_snapshot"])
                if campaign:
                    entry["campaign"] = campaign
                if entry:
                    runs[run_id] = entry
        catalog = {"schema_version": "1.0.0", "runs": runs}
        self.atomic_json(self._catalog_path(), catalog)
        return catalog

    # -- manifest ---------------------------------------------------------- #
    def save_manifest(self, run_id: str, manifest: dict) -> None:
        if not isinstance(manifest, dict):
            raise DataError("manifest must be an object")
        with self.lock():
            self.atomic_json(self._manifest_path(run_id), sanitize(manifest, self.secrets))

    def load_manifest(self, run_id: str) -> dict | None:
        path = self._manifest_path(run_id)
        if not path.exists():
            return None
        value = loads(path.read_bytes())
        if not isinstance(value, dict):
            raise DataError("manifest must be an object")
        return value


# --------------------------------------------------------------------------- #
# module-level spec functions -> default Store
# --------------------------------------------------------------------------- #
_default: Store | None = None


def _store() -> Store:
    global _default
    if _default is None:
        _default = Store.from_env()
    return _default


def put_object(data: dict | bytes) -> str:
    return _store().put_object(data)


def get_object(sha: str) -> dict:
    return _store().get_object(sha)


def get_object_bytes(sha: str) -> bytes:
    return _store().get_object_bytes(sha)


def append_event(run_id: str, event: dict) -> str:
    return _store().append_event(run_id, event)


def _validate_evidence_event(record: dict) -> None:
    from .contracts import validate
    validate("evidence_event", record)


def read_events(run_id: str) -> list[dict]:
    return _store().read_events(run_id)


def verify_chain(run_id: str) -> dict:
    return _store().verify_chain(run_id)


def seal_checkpoint(campaign_id: str, run_id: str, operator_id: str) -> dict:
    return _store().seal_checkpoint(campaign_id, run_id, operator_id)


def verify_checkpoint(campaign_id: str, run_id: str) -> dict:
    return _store().verify_checkpoint(campaign_id, run_id)


def append_verification(record: dict) -> None:
    _store().append_verification(record)


def update_catalog(run_id: str, entry: dict) -> None:
    _store().update_catalog(run_id, entry)


def load_catalog() -> dict:
    return _store().load_catalog()


def save_manifest(run_id: str, manifest: dict) -> None:
    _store().save_manifest(run_id, manifest)


def load_manifest(run_id: str) -> dict | None:
    return _store().load_manifest(run_id)
