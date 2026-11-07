"""Offline enrichment snapshot import/query (SPEC A-5, R-046).

No network is ever started. Caller supplies raw bytes.

Kinds:
- "attack": MITRE ATT&CK STIX 2.1 bundle (JSON). Extracts attack-pattern
  external_references with source_name == "mitre-attack" and external_id
  matching T####(.###).
- "kev": CISA KEV JSON (catalogVersion, dateReleased, count, vulnerabilities[]).
- "epss": EPSS CSV (optional leading '#' metadata lines, then header
  cve,epss,percentile) OR EPSS JSON API ({"data":[{cve,epss,percentile,...}]}).

Limits:
- MAX_INPUT_BYTES = 12 MiB. Larger input is rejected, never truncated.
- No automatic decompression. Caller must decompress gzip themselves.
- source_version is mandatory.
- as_of (source date) and fetched_at are distinct; future/invalid dates rejected.
- TTL defaults: attack=30d, kev=1d, epss=1d. Override via ttl_seconds (positive
  finite int only).
- Expired or missing snapshot -> query returns score=null (unconfirmed);
  stale values are never used for prioritization.

CAS contract (actual Store):
- Store.put_object(value: dict|bytes) -> digest (sha256 of canonical JSON).
- Store.get_object(digest) -> dict; Store.get_object_bytes(digest) -> bytes.
- CAS is JSON-only. Raw CSV/JSON text is stored as a JSON wrapper:
  {"type":"raw_text","encoding":"utf-8","text":<raw>,"raw_sha256":<hash>}
  where raw_sha256 is the sha256 of the original raw bytes.
- Metadata is persisted through the actual Store (put_object of a metadata
  wrapper) and indexed in workspace/enrichment/index.json under Store.lock
  with Store.atomic_json. content_digest is the CAS digest of the wrapper;
  raw_sha256 is the digest of the original raw bytes.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

MAX_INPUT_BYTES = 12 * 1024 * 1024
DEFAULT_TTL = {"attack": 30 * 86400, "kev": 86400, "epss": 86400}

_CVE_RE = re.compile(r"^CVE-[0-9]{4}-[0-9]{4,19}$")
_TECH_RE = re.compile(r"^T[0-9]{4}(\.[0-9]{3})?$")


class EnrichmentError(ValueError):
    pass


# ---------- CAS helpers (actual Store signature) ----------

def _raw_sha256(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


def _wrap_raw_text(raw: bytes) -> Dict[str, Any]:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as e:
        raise EnrichmentError(f"raw text is not utf-8: {e}") from e
    return {
        "type": "raw_text",
        "encoding": "utf-8",
        "text": text,
        "raw_sha256": _raw_sha256(raw),
    }


def _unwrap_raw_text(value: Dict[str, Any]) -> bytes:
    if not isinstance(value, dict) or value.get("type") != "raw_text":
        raise EnrichmentError("CAS object is not a raw_text wrapper")
    if value.get("encoding") != "utf-8":
        raise EnrichmentError("unsupported raw_text encoding")
    text = value.get("text")
    if not isinstance(text, str):
        raise EnrichmentError("raw_text.text must be string")
    raw = text.encode("utf-8")
    declared = value.get("raw_sha256")
    if declared is not None and declared != _raw_sha256(raw):
        raise EnrichmentError("raw_text.raw_sha256 mismatch")
    return raw


def _cas_put_wrapper(store: Any, wrapper: Dict[str, Any]) -> str:
    return store.put_object(wrapper)


def _cas_get_wrapper(store: Any, digest: str) -> Optional[Dict[str, Any]]:
    try:
        return store.get_object(digest)
    except FileNotFoundError:
        return None
    except Exception as e:
        # Store raises DataError on missing/corrupt; treat as missing here.
        if type(e).__name__ == "DataError":
            return None
        raise


# ---------- date helpers ----------

def _parse_date(s: str) -> datetime:
    try:
        if len(s) == 10:
            dt = datetime.strptime(s, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        else:
            dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception as e:
        raise EnrichmentError(f"invalid date: {s!r}") from e
    if dt.tzinfo is None:
        raise EnrichmentError(f"date requires timezone: {s!r}")
    return dt


def _check_not_future(dt: datetime, now: datetime, label: str) -> None:
    try:
        if dt > now:
            raise EnrichmentError(f"{label} is in the future: {dt.isoformat()}")
    except EnrichmentError:
        raise
    except TypeError as e:
        raise EnrichmentError(f"{label} comparison failed: {e}") from e


# ---------- parsers ----------

def _parse_attack(raw: bytes) -> Dict[str, Any]:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise EnrichmentError(f"attack JSON parse: {e}") from e
    if not isinstance(doc, dict) or doc.get("type") != "bundle":
        raise EnrichmentError("attack: not a STIX bundle")
    objs = doc.get("objects")
    if not isinstance(objs, list):
        raise EnrichmentError("attack: bundle.objects missing")
    techniques: Dict[str, Dict[str, Any]] = {}
    duplicates = []
    for o in objs:
        if not isinstance(o, dict) or o.get("type") != "attack-pattern":
            continue
        for ref in (o.get("external_references") or []):
            if not isinstance(ref, dict):
                continue
            if ref.get("source_name") != "mitre-attack":
                continue
            eid = ref.get("external_id")
            if not isinstance(eid, str) or not _TECH_RE.match(eid):
                continue
            if eid in techniques:
                duplicates.append(eid)
                continue
            techniques[eid] = {
                "technique_id": eid,
                "name": o.get("name"),
                "stix_id": o.get("id"),
            }
    return {"techniques": techniques, "duplicates": duplicates}


def _parse_kev(raw: bytes) -> Dict[str, Any]:
    try:
        doc = json.loads(raw.decode("utf-8"))
    except Exception as e:
        raise EnrichmentError(f"kev JSON parse: {e}") from e
    if not isinstance(doc, dict):
        raise EnrichmentError("kev: not object")
    for k in ("catalogVersion", "dateReleased", "count", "vulnerabilities"):
        if k not in doc:
            raise EnrichmentError(f"kev: missing {k}")
    if not isinstance(doc["count"], int) or isinstance(doc["count"], bool):
        raise EnrichmentError("kev: count must be int")
    vulns = doc["vulnerabilities"]
    if not isinstance(vulns, list):
        raise EnrichmentError("kev: vulnerabilities must be array")
    if doc["count"] != len(vulns):
        raise EnrichmentError("kev: count mismatch")
    by_cve: Dict[str, Dict[str, Any]] = {}
    duplicates = []
    for v in vulns:
        if not isinstance(v, dict):
            raise EnrichmentError("kev: vulnerability not object")
        cve = v.get("cveID")
        if not isinstance(cve, str) or not _CVE_RE.match(cve):
            raise EnrichmentError(f"kev: bad cveID {cve!r}")
        for req in ("vendorProject", "product", "vulnerabilityName",
                    "dateAdded", "shortDescription", "requiredAction", "dueDate"):
            if req not in v:
                raise EnrichmentError(f"kev: {cve} missing {req}")
        if cve in by_cve:
            duplicates.append(cve)
            continue
        by_cve[cve] = v
    return {
        "catalog_version": doc["catalogVersion"],
        "date_released": doc["dateReleased"],
        "count": doc["count"],
        "vulnerabilities": by_cve,
        "duplicates": duplicates,
    }


def _parse_epss(raw: bytes) -> Dict[str, Any]:
    text = raw.decode("utf-8", errors="strict")
    stripped = text.lstrip()
    if stripped.startswith("{"):
        return _parse_epss_json(text)
    return _parse_epss_csv(text)


def _parse_epss_json(text: str) -> Dict[str, Any]:
    try:
        doc = json.loads(text)
    except Exception as e:
        raise EnrichmentError(f"epss JSON parse: {e}") from e
    if not isinstance(doc, dict) or not isinstance(doc.get("data"), list):
        raise EnrichmentError("epss JSON: data[] missing")
    scores: Dict[str, Dict[str, Any]] = {}
    duplicates = []
    for row in doc["data"]:
        if not isinstance(row, dict):
            raise EnrichmentError("epss JSON: row not object")
        cve = row.get("cve")
        if not isinstance(cve, str) or not _CVE_RE.match(cve):
            raise EnrichmentError(f"epss: bad cve {cve!r}")
        score = _coerce_score(row.get("epss"))
        pct = _coerce_score(row.get("percentile"))
        if cve in scores:
            duplicates.append(cve)
            continue
        scores[cve] = {"cve": cve, "epss": score, "percentile": pct,
                       "date": row.get("date")}
    return {"scores": scores, "duplicates": duplicates}


def _parse_epss_csv(text: str) -> Dict[str, Any]:
    lines = text.splitlines()
    meta: Dict[str, str] = {}
    idx = 0
    while idx < len(lines) and lines[idx].startswith("#"):
        body = lines[idx][1:].strip()
        if ":" in body:
            k, v = body.split(":", 1)
            meta[k.strip()] = v.strip()
        idx += 1
    if idx >= len(lines):
        raise EnrichmentError("epss CSV: no header")
    header = [h.strip() for h in lines[idx].split(",")]
    if header[:3] != ["cve", "epss", "percentile"]:
        raise EnrichmentError(f"epss CSV: bad header {header}")
    reader = csv.reader(io.StringIO("\n".join(lines[idx + 1:])))
    scores: Dict[str, Dict[str, Any]] = {}
    duplicates = []
    for row in reader:
        if not row or all(not c.strip() for c in row):
            continue
        if len(row) < 3:
            raise EnrichmentError(f"epss CSV: short row {row}")
        cve = row[0].strip()
        if not _CVE_RE.match(cve):
            raise EnrichmentError(f"epss CSV: bad cve {cve!r}")
        score = _coerce_score(row[1])
        pct = _coerce_score(row[2])
        if cve in scores:
            duplicates.append(cve)
            continue
        scores[cve] = {"cve": cve, "epss": score, "percentile": pct}
    return {"scores": scores, "duplicates": duplicates, "meta": meta}


def _coerce_score(v: Any) -> Optional[float]:
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        raise EnrichmentError("score must not be bool")
    try:
        f = float(v)
    except Exception as e:
        raise EnrichmentError(f"score not numeric: {v!r}") from e
    if f != f:  # NaN
        raise EnrichmentError("score is NaN")
    if f in (float("inf"), float("-inf")):
        raise EnrichmentError("score is infinite")
    if not (0.0 <= f <= 1.0):
        raise EnrichmentError(f"score out of [0,1]: {f}")
    return f


# ---------- metadata index ----------

def _index_path(store: Any) -> Path:
    return Path(store.workspace) / "enrichment" / "index.json"


def _load_index(store: Any) -> Dict[str, Any]:
    path = _index_path(store)
    if not path.exists():
        return {"schema_version": "1.0.0", "entries": {}}
    try:
        value = json.loads(path.read_bytes().decode("utf-8"))
    except Exception as e:
        raise EnrichmentError(f"enrichment index unreadable: {e}") from e
    if not isinstance(value, dict):
        raise EnrichmentError("enrichment index must be object")
    value.setdefault("schema_version", "1.0.0")
    value.setdefault("entries", {})
    return value


def _persist_meta(store: Any, meta: Dict[str, Any]) -> str:
    """Persist metadata as a CAS object and index it under Store.lock."""
    meta_digest = store.put_object({"type": "enrichment_meta", "meta": meta})
    with store.lock():
        index = _load_index(store)
        index["entries"][meta["raw_sha256"]] = {
            "content_digest": meta["content_digest"],
            "raw_sha256": meta["raw_sha256"],
            "meta_digest": meta_digest,
            "kind": meta["kind"],
            "expires_at": meta["expires_at"],
        }
        store.atomic_json(_index_path(store), index)
    return meta_digest


def _load_meta(store: Any, raw_sha256: str) -> Optional[Dict[str, Any]]:
    index = _load_index(store)
    entry = index.get("entries", {}).get(raw_sha256)
    if not isinstance(entry, dict):
        return None
    meta_digest = entry.get("meta_digest")
    if not isinstance(meta_digest, str):
        return None
    try:
        wrapper = store.get_object(meta_digest)
    except Exception:
        return None
    if not isinstance(wrapper, dict) or wrapper.get("type") != "enrichment_meta":
        return None
    meta = wrapper.get("meta")
    if not isinstance(meta, dict):
        return None
    return meta


# ---------- public API ----------

def import_snapshot(store: Any, kind: str, raw: bytes, source_url: str,
                    fetched_at: str, source_version: str, as_of: str,
                    ttl_seconds: Optional[int] = None,
                    expected_sha256: Optional[str] = None) -> Dict[str, Any]:
    """Import a snapshot into CAS and return metadata."""
    if kind not in ("attack", "kev", "epss"):
        raise EnrichmentError(f"unknown kind: {kind}")
    if not isinstance(raw, (bytes, bytearray)):
        raise EnrichmentError("raw must be bytes")
    raw = bytes(raw)
    if len(raw) > MAX_INPUT_BYTES:
        raise EnrichmentError(
            f"input exceeds {MAX_INPUT_BYTES} bytes ({len(raw)}); refusing to truncate")
    if not source_version:
        raise EnrichmentError("source_version required")
    if not source_url:
        raise EnrichmentError("source_url required")
    if not isinstance(source_url, str) or not (
            source_url.startswith("http://") or source_url.startswith("https://")):
        raise EnrichmentError(
            f"source_url must start with http:// or https://: {source_url!r}")

    now = datetime.now(timezone.utc)
    fetched_dt = _parse_date(fetched_at)
    as_of_dt = _parse_date(as_of)
    _check_not_future(fetched_dt, now, "fetched_at")
    _check_not_future(as_of_dt, now, "as_of")

    if ttl_seconds is None:
        ttl = DEFAULT_TTL[kind]
    else:
        if isinstance(ttl_seconds, bool) or not isinstance(ttl_seconds, int) or ttl_seconds <= 0:
            raise EnrichmentError("ttl_seconds must be positive int")
        ttl = ttl_seconds

    # Verify declared hash against raw original bytes BEFORE any write.
    raw_sha = _raw_sha256(raw)
    if expected_sha256 is not None and expected_sha256 != raw_sha:
        raise EnrichmentError(
            f"sha256 mismatch: declared {expected_sha256} != actual {raw_sha}")

    if kind == "attack":
        parsed = _parse_attack(raw)
    elif kind == "kev":
        parsed = _parse_kev(raw)
    else:
        parsed = _parse_epss(raw)

    # Store raw bytes as a JSON raw_text wrapper (CAS is JSON-only).
    wrapper = _wrap_raw_text(raw)
    content_digest = _cas_put_wrapper(store, wrapper)

    # Read back and verify both wrapper digest and raw_sha256.
    stored = _cas_get_wrapper(store, content_digest)
    if stored is None:
        raise EnrichmentError("CAS verification failed: object missing")
    if _raw_sha256(_unwrap_raw_text(stored)) != raw_sha:
        raise EnrichmentError("CAS verification failed: raw_sha256 mismatch")

    meta = {
        "kind": kind,
        "source_url": source_url,
        "source_version": source_version,
        "fetched_at": fetched_dt.isoformat(),
        "as_of": as_of_dt.isoformat(),
        "raw_sha256": raw_sha,
        "content_digest": content_digest,
        "cas_ref": content_digest,
        "ttl_seconds": ttl,
        "expires_at": (fetched_dt.timestamp() + ttl),
        "duplicates": parsed.get("duplicates", []),
    }
    if kind == "attack":
        meta["technique_count"] = len(parsed["techniques"])
    elif kind == "kev":
        meta["catalog_version"] = parsed["catalog_version"]
        meta["count"] = parsed["count"]
    else:
        meta["score_count"] = len(parsed["scores"])
        if "meta" in parsed:
            meta["csv_meta"] = parsed["meta"]

    _persist_meta(store, meta)
    return meta


def query_snapshot(store: Any, ref: Dict[str, Any], cve: Optional[str] = None,
                   technique_id: Optional[str] = None,
                   now: Optional[datetime] = None) -> Dict[str, Any]:
    """Query a previously imported snapshot. Returns score=null if expired/missing."""
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    kind = ref.get("kind")
    raw_sha = ref.get("raw_sha256")
    content_digest = ref.get("content_digest") or ref.get("cas_ref")
    if not raw_sha or not content_digest:
        raise EnrichmentError("ref missing raw_sha256/content_digest")

    # Verify metadata via index (meta_digest) and raw_sha256 consistency.
    meta = _load_meta(store, raw_sha)
    if meta is None:
        return {"status": "missing", "score": None, "kind": kind,
                "reason": "meta_missing"}
    if meta.get("raw_sha256") != raw_sha:
        return {"status": "corrupt", "score": None, "kind": kind,
                "reason": "meta_raw_sha256_mismatch",
                "expires_at": meta.get("expires_at")}
    if meta.get("content_digest") != content_digest:
        return {"status": "corrupt", "score": None, "kind": kind,
                "reason": "meta_content_digest_mismatch",
                "expires_at": meta.get("expires_at")}

    # TTL contract: the stored meta is the single source of truth for
    # expires_at. A caller-supplied ref.expires_at must never extend it.
    meta_expires_at = meta.get("expires_at")
    if "expires_at" in ref:
        ref_expires_at = ref.get("expires_at")
        if ref_expires_at != meta_expires_at:
            return {"status": "corrupt", "score": None, "kind": kind,
                    "reason": "meta_expires_at_mismatch",
                    "expires_at": meta_expires_at}

    if meta_expires_at is not None and now.timestamp() > float(meta_expires_at):
        return {"status": "expired", "score": None, "kind": kind,
                "reason": "ttl_expired", "expires_at": meta_expires_at}

    wrapper = _cas_get_wrapper(store, content_digest)
    if wrapper is None:
        return {"status": "missing", "score": None, "kind": kind,
                "reason": "cas_missing", "expires_at": meta_expires_at}
    try:
        raw = _unwrap_raw_text(wrapper)
    except EnrichmentError as e:
        return {"status": "corrupt", "score": None, "kind": kind,
                "reason": f"wrapper_invalid: {e}",
                "expires_at": meta_expires_at}
    if _raw_sha256(raw) != raw_sha:
        return {"status": "corrupt", "score": None, "kind": kind,
                "reason": "raw_sha256_mismatch",
                "expires_at": meta_expires_at}

    if kind == "attack":
        parsed = _parse_attack(raw)
        if technique_id is None:
            return {"status": "ok", "kind": kind,
                    "techniques": sorted(parsed["techniques"]),
                    "expires_at": meta_expires_at}
        t = parsed["techniques"].get(technique_id)
        return {"status": "ok" if t else "not_found", "kind": kind,
                "technique_id": technique_id, "technique": t,
                "expires_at": meta_expires_at}

    if kind == "kev":
        parsed = _parse_kev(raw)
        if cve is None:
            return {"status": "ok", "kind": kind,
                    "count": parsed["count"],
                    "catalog_version": parsed["catalog_version"],
                    "expires_at": meta_expires_at}
        v = parsed["vulnerabilities"].get(cve)
        return {"status": "ok" if v else "not_found", "kind": kind,
                "cve": cve, "vulnerability": v,
                "expires_at": meta_expires_at}

    if kind == "epss":
        parsed = _parse_epss(raw)
        if cve is None:
            return {"status": "ok", "kind": kind,
                    "score_count": len(parsed["scores"]),
                    "expires_at": meta_expires_at}
        s = parsed["scores"].get(cve)
        if s is None:
            return {"status": "not_found", "kind": kind, "cve": cve,
                    "score": None, "expires_at": meta_expires_at}
        return {"status": "ok", "kind": kind, "cve": cve,
                "score": s["epss"], "percentile": s["percentile"],
                "expires_at": meta_expires_at}

    raise EnrichmentError(f"unknown kind: {kind}")