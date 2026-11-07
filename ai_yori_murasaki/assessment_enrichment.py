"""SPEC: .ga/goal-20261106/kev-epss-integration-plan.md。assessment.py/assessment_contracts.pyへの接続は別工程。

KEV/EPSS enrichment record builder for assessment reports.

This module reuses the existing enrichment snapshot store
(``enrichment.import_snapshot`` / ``enrichment.query_snapshot``) as the only
acquisition and retention path. It does not implement a second system and it
never starts network access.

Design references (kev-epss-integration-plan.md):
- §2.2 EnrichmentRecord (closed type, 14 fields)
- §2.3 status conversion rules (no conversion of expired/missing/corrupt into
  "safe" or "not detected")
- §2.5 semantic validation rules

Trust boundary: records are produced only by the trusted diagnostic producer
via real ``query_snapshot`` calls. External context cannot inject record bodies
or statuses.
"""
from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional, Sequence

from .enrichment import import_snapshot, query_snapshot  # noqa: F401  (re-exported)


class EnrichmentError(ValueError):
    """Raised when enrichment records violate the closed contract."""


ENRICHMENT_STATUSES = {
    "kev": {
        "listed",
        "not_listed",
        "expired",
        "missing",
        "corrupt",
        "not_applicable",
        "not_tested",
    },
    "epss": {
        "scored",
        "not_listed",
        "expired",
        "missing",
        "corrupt",
        "not_applicable",
        "not_tested",
    },
}

KINDS = ("kev", "epss")

# Fields copied from the snapshot meta (ref) into each record.
_META_FIELDS = (
    "source_url",
    "source_version",
    "fetched_at",
    "as_of",
    "expires_at",
    "raw_sha256",
    "content_digest",
)

# Fields that must be non-None for listed/scored records.
_REQUIRED_META_FOR_VALUE = (
    "source_url",
    "source_version",
    "fetched_at",
    "as_of",
    "expires_at",
    "raw_sha256",
    "content_digest",
)

# Operator projection allowlist (12 keys). raw body / CAS content excluded.
_PROJECTION_KEYS = (
    "finding_id",
    "kind",
    "cve",
    "status",
    "epss",
    "percentile",
    "source_url",
    "source_version",
    "fetched_at",
    "as_of",
    "expires_at",
    "reason",
)

_REASON_NOT_APPLICABLE = "CVEが付与されていないため照会できない"
_REASON_NOT_TESTED = "スナップショットが未登録のため照会していません"
_REASON_EXPIRED = "スナップショットの期限が切れています"
_REASON_MISSING = "スナップショットが見つかりません"
_REASON_CORRUPT = "スナップショットが破損しています"
_REASON_NOT_LISTED = "スナップショットに収録されていません"

# query_snapshot status -> record status, per kind.
_QUERY_STATUS_MAP = {
    "kev": {
        "ok": "listed",
        "not_found": "not_listed",
        "expired": "expired",
        "missing": "missing",
        "corrupt": "corrupt",
    },
    "epss": {
        "ok": "scored",
        "not_found": "not_listed",
        "expired": "expired",
        "missing": "missing",
        "corrupt": "corrupt",
    },
}

_REASON_BY_STATUS = {
    "expired": _REASON_EXPIRED,
    "missing": _REASON_MISSING,
    "corrupt": _REASON_CORRUPT,
    "not_listed": _REASON_NOT_LISTED,
}


def _empty_record(finding_id: str, kind: str) -> Dict[str, Any]:
    return {
        "finding_id": finding_id,
        "kind": kind,
        "cve": None,
        "status": None,
        "epss": None,
        "percentile": None,
        "source_url": None,
        "source_version": None,
        "fetched_at": None,
        "as_of": None,
        "expires_at": None,
        "raw_sha256": None,
        "content_digest": None,
        "reason": "",
    }


def _copy_meta(record: Dict[str, Any], ref: Optional[Dict[str, Any]]) -> None:
    if not isinstance(ref, dict):
        return
    for field in _META_FIELDS:
        record[field] = ref.get(field)


def _cve_for_finding(finding: Dict[str, Any]) -> Optional[str]:
    """Return the CVE to query, or None when the finding is not applicable."""
    classification = finding.get("classification") or {}
    cve_block = classification.get("cve") or {}
    status = cve_block.get("status")
    ids = cve_block.get("ids") or []
    if not isinstance(ids, (list, tuple)) or len(ids) == 0:
        return None
    if status in ("unknown", "not_applicable"):
        return None
    first = ids[0]
    if not isinstance(first, str) or not first:
        return None
    return first


def _build_one(finding_id: str, kind: str, cve: Optional[str],
               ref: Optional[Dict[str, Any]], store: Any,
               now: Any) -> Dict[str, Any]:
    record = _empty_record(finding_id, kind)

    # (a) No CVE -> not_applicable. Never invent numbers or false.
    if cve is None:
        record["status"] = "not_applicable"
        record["reason"] = _REASON_NOT_APPLICABLE
        return record

    record["cve"] = cve

    # (b) CVE present but no snapshot ref -> not_tested.
    if ref is None:
        record["status"] = "not_tested"
        record["reason"] = _REASON_NOT_TESTED
        return record

    # (c) Real query_snapshot call. The ref dict is passed through as-is
    # because query_snapshot requires raw_sha256/content_digest.
    result = query_snapshot(store, ref, cve=cve, now=now)
    query_status = result.get("status")
    mapped = _QUERY_STATUS_MAP[kind].get(query_status)
    if mapped is None:
        raise EnrichmentError(
            f"unexpected query_snapshot status {query_status!r} for kind {kind!r}")

    record["status"] = mapped
    _copy_meta(record, ref)
    # The stored meta is the source of truth for expires_at; never echo a
    # caller-supplied ref value that could have been forged.
    if result.get("expires_at") is not None:
        record["expires_at"] = result["expires_at"]

    if mapped in ("listed", "scored"):
        record["reason"] = ""
        if kind == "epss":
            record["epss"] = result.get("score")
            record["percentile"] = result.get("percentile")
        # kev: value fields stay None.
    else:
        record["epss"] = None
        record["percentile"] = None
        record["reason"] = _REASON_BY_STATUS.get(mapped, "")
        if not record["reason"]:
            raise EnrichmentError(
                f"missing reason for status {mapped!r} (kind {kind!r})")

    return record


def build_enrichment_records(store: Any, findings: Sequence[Dict[str, Any]],
                             kev_ref: Optional[Dict[str, Any]] = None,
                             epss_ref: Optional[Dict[str, Any]] = None,
                             now: Any = None) -> List[Dict[str, Any]]:
    """Build one EnrichmentRecord per (finding, kind).

    ``findings`` items are minimal dicts of the form::

        {"finding_id": str,
         "classification": {"cve": {"status": str, "ids": [str, ...]}}}

    ``kev_ref`` / ``epss_ref`` are snapshot meta dicts as returned by
    ``enrichment.import_snapshot`` (they contain raw_sha256/content_digest and
    are passed straight to ``query_snapshot``).
    """
    if not isinstance(findings, (list, tuple)):
        raise EnrichmentError("findings must be a list")

    refs = {"kev": kev_ref, "epss": epss_ref}
    records: List[Dict[str, Any]] = []
    for finding in findings:
        if not isinstance(finding, dict):
            raise EnrichmentError("finding must be an object")
        finding_id = finding.get("finding_id")
        if not isinstance(finding_id, str) or not finding_id:
            raise EnrichmentError("finding_id must be a non-empty string")
        cve = _cve_for_finding(finding)
        for kind in KINDS:
            records.append(
                _build_one(finding_id, kind, cve, refs[kind], store, now))
    return records


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def validate_enrichment_records(records: Iterable[Dict[str, Any]],
                                finding_ids: Iterable[str]) -> None:
    """Validate records against the closed contract. Raises EnrichmentError."""
    if not isinstance(records, (list, tuple)):
        raise EnrichmentError("records must be a list")
    known_ids = set(finding_ids)
    seen = set()

    for record in records:
        if not isinstance(record, dict):
            raise EnrichmentError("record must be an object")

        finding_id = record.get("finding_id")
        kind = record.get("kind")
        status = record.get("status")

        if not isinstance(finding_id, str) or not finding_id:
            raise EnrichmentError("record.finding_id must be a non-empty string")
        if finding_id not in known_ids:
            raise EnrichmentError(
                f"record.finding_id {finding_id!r} not in report findings")
        if kind not in KINDS:
            raise EnrichmentError(f"record.kind invalid: {kind!r}")
        if status not in ENRICHMENT_STATUSES[kind]:
            raise EnrichmentError(
                f"record.status {status!r} invalid for kind {kind!r}")

        key = (finding_id, kind)
        if key in seen:
            raise EnrichmentError(
                f"duplicate enrichment record for {finding_id!r}/{kind!r}")
        seen.add(key)

        cve = record.get("cve")
        epss = record.get("epss")
        percentile = record.get("percentile")
        reason = record.get("reason")

        if cve is None:
            if epss is not None or percentile is not None:
                raise EnrichmentError(
                    f"cve is null but epss/percentile set for {finding_id!r}/{kind!r}")

        is_value = status in ("listed", "scored")

        if is_value:
            for field in _REQUIRED_META_FOR_VALUE:
                if record.get(field) is None:
                    raise EnrichmentError(
                        f"status {status!r} requires {field} for "
                        f"{finding_id!r}/{kind!r}")
            if reason != "":
                raise EnrichmentError(
                    f"status {status!r} requires empty reason for "
                    f"{finding_id!r}/{kind!r}")
        else:
            if epss is not None or percentile is not None:
                raise EnrichmentError(
                    f"status {status!r} must not carry epss/percentile for "
                    f"{finding_id!r}/{kind!r}")
            if not isinstance(reason, str) or reason == "":
                raise EnrichmentError(
                    f"status {status!r} requires non-empty reason for "
                    f"{finding_id!r}/{kind!r}")

        # epss/percentile may only be set for kind=epss and status=scored.
        for label, value in (("epss", epss), ("percentile", percentile)):
            if value is None:
                continue
            if not (kind == "epss" and status == "scored"):
                raise EnrichmentError(
                    f"{label} set outside kind=epss/status=scored for "
                    f"{finding_id!r}/{kind!r}")
            if not _is_number(value):
                raise EnrichmentError(
                    f"{label} must be numeric for {finding_id!r}/{kind!r}")
            if not (0.0 <= float(value) <= 1.0):
                raise EnrichmentError(
                    f"{label} out of [0,1] for {finding_id!r}/{kind!r}")


def verify_against_meta(records: Iterable[Dict[str, Any]],
                        metas: Iterable[Dict[str, Any]]) -> None:
    """Cross-check records against imported snapshot metas.

    ``metas`` are the dicts returned by ``enrichment.import_snapshot``. When a
    record carries a non-None content_digest, a matching meta (by raw_sha256)
    must exist and agree on kind/source_url/content_digest.
    """
    if not isinstance(records, (list, tuple)):
        raise EnrichmentError("records must be a list")
    if not isinstance(metas, (list, tuple)):
        raise EnrichmentError("metas must be a list")

    by_raw: Dict[str, Dict[str, Any]] = {}
    for meta in metas:
        if not isinstance(meta, dict):
            raise EnrichmentError("meta must be an object")
        raw_sha = meta.get("raw_sha256")
        if isinstance(raw_sha, str):
            by_raw[raw_sha] = meta

    for record in records:
        if not isinstance(record, dict):
            raise EnrichmentError("record must be an object")
        content_digest = record.get("content_digest")
        raw_sha = record.get("raw_sha256")
        if content_digest is None:
            continue
        if not isinstance(raw_sha, str) or not raw_sha:
            raise EnrichmentError(
                "record has content_digest but no raw_sha256")
        meta = by_raw.get(raw_sha)
        if meta is None:
            raise EnrichmentError(
                f"no enrichment meta for raw_sha256 {raw_sha!r}")
        if meta.get("kind") != record.get("kind"):
            raise EnrichmentError(
                f"kind mismatch for raw_sha256 {raw_sha!r}")
        if meta.get("source_url") != record.get("source_url"):
            raise EnrichmentError(
                f"source_url mismatch for raw_sha256 {raw_sha!r}")
        if meta.get("content_digest") != content_digest:
            raise EnrichmentError(
                f"content_digest mismatch for raw_sha256 {raw_sha!r}")


def project_enrichment(records: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Project records to the operator allowlist (12 keys).

    Raw snapshot bodies and CAS contents are never included. There is no blue
    projection path here; blue stays a fixed withheld response elsewhere.
    """
    if not isinstance(records, (list, tuple)):
        raise EnrichmentError("records must be a list")
    projected: List[Dict[str, Any]] = []
    for record in records:
        if not isinstance(record, dict):
            raise EnrichmentError("record must be an object")
        projected.append({key: record.get(key) for key in _PROJECTION_KEYS})
    return projected
