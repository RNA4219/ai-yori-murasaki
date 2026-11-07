"""Evidence helpers: legacy ArtifactStore/Ledger wrappers over the new Store.

Kept for callers that still import these names; they delegate to storage.Store
and use the v1 canonical/hash format. No blue-purple/v1 compatibility.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from .jsonio import DataError, canonical, digest, loads
from .storage import Store, event_hash, sanitize

ZERO = "0" * 64


class ArtifactStore:
    """Legacy wrapper: CAS backed by Store.put_object/get_object."""

    def __init__(self, root: Path, secrets: tuple[str, ...] = ()) -> None:
        self.root = Path(root)
        self.secrets = secrets
        self._store = Store(self.root, self.root.parent / (self.root.name + ".trusted"),
                            secrets)

    def put(self, value: Any) -> str:
        return self._store.put_object(value)

    def get(self, digest_value: str) -> Any:
        return self._store.get_object(digest_value)


class Ledger:
    """Legacy wrapper: append-only chain backed by Store."""

    def __init__(self, path: Path, secrets: tuple[str, ...] = ()) -> None:
        self.path = Path(path)
        self.secrets = secrets
        self.run_id = self.path.parent.name
        self._store = Store(self.path.parent.parent.parent,
                            self.path.parent.parent.parent.parent / "trusted",
                            secrets)

    def _read(self) -> list[dict]:
        return self._store.read_events(self.run_id)

    def verify(self, expected: dict, artifacts: ArtifactStore) -> list[dict]:
        events = self._read()
        result = self._store.verify_chain(self.run_id)
        if result["result"] != "PASS":
            raise DataError("event chain mismatch")
        if (result["last_sequence"] != expected.get("event_count")
                or result["last_event_hash"] != expected.get("head_hash")):
            raise DataError("trusted seal does not match ledger")
        return events

    def append(self, record: dict, expected: dict, artifacts: ArtifactStore) -> dict:
        self._store.append_event(self.run_id, record)
        events = self._read()
        return {"schema_version": "1.0.0", "campaign_id": expected.get("campaign_id"),
                "run_id": self.run_id, "event_count": len(events),
                "head_hash": events[-1]["event_hash"] if events else ZERO}


def genesis(campaign_id: str, run_id: str) -> dict:
    return {"schema_version": "1.0.0", "campaign_id": campaign_id,
            "run_id": run_id, "event_count": 0, "head_hash": ZERO}


def blind_blue_view(events: list[dict]) -> list[dict]:
    """Information-extraction contract; OS access control is separate."""
    fields = ("event_id", "timestamp", "actor", "tool_name", "permission_decision",
              "target_id", "status", "payload", "evidence_refs")
    result = []
    for event in events:
        if event.get("actor") not in {"red", "oracle"} and event.get("visibility") in {"blue", "shared"}:
            result.append(loads(canonical({k: event[k] for k in fields if k in event})))
    return result
