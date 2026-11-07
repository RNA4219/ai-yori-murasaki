"""Dependency advisory import and version matching (SPEC-ASSESSMENT §1.8).

This module is deliberately independent of the assessment contract layer.  It
stores the *raw* GitHub advisory JSON in the CAS and a separate, closed-shape
extraction object that references the raw object.  Every read re-validates the
extraction against the raw JSON so a tampered or mismatched CAS object can
never be trusted.

No network access happens here.  Callers supply the raw GitHub API payload.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from .jsonio import DataError

ADVISORY_VERSION = "1.0.0"

MAX_TTL_SECONDS = 31_536_000
MIN_TTL_SECONDS = 1

# Ecosystems whose version ranges we can actually evaluate.  Everything else
# is reported as ``unknown`` rather than guessed.
SUPPORTED_ECOSYSTEMS = frozenset({"pip", "pypi"})

_GHSA_RE = re.compile(r"^GHSA(?:-[0-9a-z]{4}){3}$", re.IGNORECASE)
_CVE_RE = re.compile(r"^CVE-\d{4}-\d{4,}$", re.IGNORECASE)

_API_HOST = "api.github.com"
_WEB_HOST = "github.com"

# Keys that must never appear in a stored raw advisory.  The Store already
# sanitizes known secret names, but we reject them explicitly so a caller
# cannot smuggle credentials into the CAS through this path.
_FORBIDDEN_KEYS = frozenset({
    "authorization",
    "cookie",
    "set-cookie",
    "password",
    "passwd",
    "secret",
    "token",
    "access_token",
    "api_key",
    "apikey",
    "private_key",
    "client_secret",
})

_RAW_KEYS = frozenset({
    "ghsa_id",
    "cve_id",
    "summary",
    "description",
    "vulnerabilities",
})

_ENTRY_KEYS = frozenset({
    "ecosystem",
    "package",
    "affected_range",
    "first_patched_version",
    "conditions",
})

_ADVISORY_KEYS = frozenset({
    "advisory_version",
    "cve_id",
    "source_url",
    "fetched_at",
    "ttl",
    "raw_ref",
    "entries",
})


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DataError(message)


def _is_aware(value: Any) -> bool:
    return isinstance(value, datetime) and value.tzinfo is not None and value.utcoffset() is not None


def _parse_iso(value: Any, field: str) -> datetime:
    _require(isinstance(value, str) and value, f"{field} must be an ISO8601 string")
    text = value
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise DataError(f"{field} is not a valid ISO8601 timestamp") from exc
    _require(_is_aware(parsed), f"{field} must include a timezone")
    return parsed


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _check_no_secret_keys(value: Any, path: str = "raw") -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            _require(isinstance(key, str), f"{path} key must be a string")
            _require(key.lower() not in _FORBIDDEN_KEYS, f"{path} contains a forbidden secret key")
            _check_no_secret_keys(item, f"{path}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _check_no_secret_keys(item, f"{path}[{index}]")


def _validate_source_url(source_url: Any) -> str:
    _require(isinstance(source_url, str) and source_url, "source_url must be a string")
    parts = urlsplit(source_url)
    _require(parts.scheme == "https", "source_url must use https")
    _require(not parts.username and not parts.password, "source_url must not embed credentials")
    _require(not parts.query and not parts.fragment, "source_url must not carry query or fragment")
    host = (parts.hostname or "").lower()
    path = parts.path
    if host == _API_HOST:
        _require(
            re.fullmatch(r"/advisories/GHSA(?:-[0-9a-z]{4}){3}", path, re.IGNORECASE) is not None,
            "source_url must be https://api.github.com/advisories/<GHSA>",
        )
    elif host == _WEB_HOST:
        _require(
            re.fullmatch(
                r"/[^/]+/[^/]+/security/advisories/GHSA(?:-[0-9a-z]{4}){3}",
                path,
                re.IGNORECASE,
            )
            is not None,
            "source_url must be https://github.com/<owner>/<repo>/security/advisories/<GHSA>",
        )
    else:
        raise DataError("source_url host must be api.github.com or github.com")
    return source_url


def _ghsa_from_url(source_url: str) -> str:
    match = re.search(r"GHSA(?:-[0-9a-z]{4}){3}", source_url, re.IGNORECASE)
    _require(match is not None, "source_url must contain a GHSA id")
    return match.group(0).upper()


def _normalize_range(raw: Any) -> str:
    _require(isinstance(raw, str) and raw.strip(), "affected_range must be a non-empty string")
    # GitHub API sometimes emits ranges with spaces around comparators.
    text = re.sub(r"\s+", "", raw)
    _require(text, "affected_range must be a non-empty string")
    try:
        SpecifierSet(text)
    except InvalidSpecifier as exc:
        raise DataError("affected_range is not a PEP440 specifier set") from exc
    return text


def _normalize_package(raw: Any) -> str:
    _require(isinstance(raw, str) and raw.strip(), "package must be a non-empty string")
    name = raw.strip()
    _require("/" not in name and " " not in name, "package must be a PyPI distribution name")
    return name


def _extract_entries(raw: dict[str, Any]) -> list[dict[str, Any]]:
    vulnerabilities = raw.get("vulnerabilities")
    _require(isinstance(vulnerabilities, list), "raw advisory must contain a vulnerabilities array")
    entries: list[dict[str, Any]] = []
    for item in vulnerabilities:
        _require(isinstance(item, dict), "vulnerability entry must be an object")
        package = item.get("package")
        _require(isinstance(package, dict), "vulnerability.package must be an object")
        ecosystem = package.get("ecosystem")
        name = package.get("name")
        _require(isinstance(ecosystem, str) and ecosystem, "vulnerability.package.ecosystem is required")
        _require(isinstance(name, str) and name, "vulnerability.package.name is required")
        affected = item.get("vulnerable_version_range")
        patched = item.get("first_patched_version")
        if patched is not None:
            _require(isinstance(patched, str) and patched, "first_patched_version must be a string")
        conditions = item.get("vulnerable_functions")
        if conditions is None:
            conditions = []
        _require(isinstance(conditions, list), "vulnerable_functions must be an array")
        _require(all(isinstance(c, str) for c in conditions), "vulnerable_functions entries must be strings")
        entries.append({
            "ecosystem": ecosystem,
            "package": name,
            "affected_range": affected,
            "first_patched_version": patched,
            "conditions": list(conditions),
        })
    return entries


def _normalize_entries(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    normalized: list[dict[str, Any]] = []
    for entry in entries:
        _require(isinstance(entry, dict), "entry must be an object")
        _require(set(entry) == _ENTRY_KEYS, "entry has unexpected or missing fields")
        ecosystem = entry["ecosystem"]
        _require(isinstance(ecosystem, str) and ecosystem, "entry.ecosystem must be a string")
        package = _normalize_package(entry["package"])
        affected = _normalize_range(entry["affected_range"])
        patched = entry["first_patched_version"]
        if patched is not None:
            _require(isinstance(patched, str) and patched, "first_patched_version must be a string")
            try:
                Version(patched)
            except InvalidVersion as exc:
                raise DataError("first_patched_version is not a PEP440 version") from exc
        conditions = entry["conditions"]
        _require(isinstance(conditions, list), "entry.conditions must be an array")
        _require(all(isinstance(c, str) for c in conditions), "entry.conditions entries must be strings")
        normalized.append({
            "ecosystem": ecosystem,
            "package": package,
            "affected_range": affected,
            "first_patched_version": patched,
            "conditions": list(conditions),
        })
    return normalized


def _raw_entries(raw: dict[str, Any]) -> list[dict[str, Any]]:
    """Re-derive the extraction from the raw JSON and normalize it."""
    return _normalize_entries(_extract_entries(raw))


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def import_advisory(
    store: Any,
    source_url: str,
    raw: dict[str, Any],
    fetched_at: datetime,
    ttl: int,
) -> str:
    """Validate and store a GitHub advisory; return the advisory CAS ref."""
    _validate_source_url(source_url)
    _require(isinstance(raw, dict), "raw advisory must be a JSON object")
    _require(set(raw) >= _RAW_KEYS, "raw advisory is missing required GitHub fields")
    _check_no_secret_keys(raw)

    ghsa = raw.get("ghsa_id")
    _require(isinstance(ghsa, str) and _GHSA_RE.match(ghsa) is not None, "ghsa_id is invalid")
    _require(ghsa.upper() == _ghsa_from_url(source_url), "source_url GHSA does not match raw ghsa_id")

    cve = raw.get("cve_id")
    _require(cve is None or (isinstance(cve, str) and _CVE_RE.match(cve) is not None), "cve_id is invalid")

    _require(isinstance(raw.get("summary"), str), "summary must be a string")
    _require(isinstance(raw.get("description"), str), "description must be a string")

    _require(_is_aware(fetched_at), "fetched_at must be timezone-aware")
    now = datetime.now(timezone.utc)
    _require(fetched_at <= now, "fetched_at must not be in the future")
    _require(isinstance(ttl, int) and not isinstance(ttl, bool), "ttl must be an integer")
    _require(MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS, "ttl is out of range")

    entries = _raw_entries(raw)

    raw_ref = store.put_object(raw)

    advisory = {
        "advisory_version": ADVISORY_VERSION,
        "cve_id": cve,
        "source_url": source_url,
        "fetched_at": _iso(fetched_at),
        "ttl": ttl,
        "raw_ref": raw_ref,
        "entries": entries,
    }
    # Validate before persisting so a bad extraction never reaches the CAS.
    validate_advisory(advisory, store, now=now)
    return store.put_object(advisory)


def validate_advisory(obj: Any, store: Any, now: datetime | None = None) -> None:
    """Validate an advisory extraction against its raw CAS object."""
    _require(isinstance(obj, dict), "advisory must be a JSON object")
    _require(set(obj) == _ADVISORY_KEYS, "advisory has unexpected or missing fields")
    _require(obj["advisory_version"] == ADVISORY_VERSION, "unsupported advisory_version")

    cve = obj["cve_id"]
    _require(cve is None or (isinstance(cve, str) and _CVE_RE.match(cve) is not None), "cve_id is invalid")

    source_url = _validate_source_url(obj["source_url"])

    fetched_at = _parse_iso(obj["fetched_at"], "fetched_at")
    reference = now if now is not None else datetime.now(timezone.utc)
    _require(_is_aware(reference), "now must be timezone-aware")
    _require(fetched_at <= reference, "fetched_at must not be in the future")

    ttl = obj["ttl"]
    _require(isinstance(ttl, int) and not isinstance(ttl, bool), "ttl must be an integer")
    _require(MIN_TTL_SECONDS <= ttl <= MAX_TTL_SECONDS, "ttl is out of range")

    raw_ref = obj["raw_ref"]
    _require(isinstance(raw_ref, str) and re.fullmatch(r"[0-9a-f]{64}", raw_ref) is not None,
             "raw_ref must be a sha256 hex digest")

    entries = obj["entries"]
    _require(isinstance(entries, list), "entries must be an array")
    normalized = _normalize_entries(entries)

    try:
        raw = store.get_object(raw_ref)
    except FileNotFoundError as exc:
        raise DataError("raw advisory object is unavailable") from exc
    _require(isinstance(raw, dict), "raw advisory must be a JSON object")
    _check_no_secret_keys(raw)

    ghsa = raw.get("ghsa_id")
    _require(isinstance(ghsa, str) and _GHSA_RE.match(ghsa) is not None, "raw ghsa_id is invalid")
    _require(ghsa.upper() == _ghsa_from_url(source_url), "source_url GHSA does not match raw ghsa_id")
    _require(raw.get("cve_id") == cve, "cve_id does not match raw advisory")

    expected = _raw_entries(raw)
    _require(normalized == expected, "extracted entries do not match raw advisory")


def _is_fresh(fetched_at: datetime, ttl: int, now: datetime) -> bool:
    return now <= fetched_at + timedelta(seconds=ttl)


def match_version(
    store: Any,
    advisory_ref: str,
    package: str,
    version: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Match a PyPI package version against a stored advisory.

    Returns ``version_status`` and always ``applicability="unknown"``: a
    version match alone never confirms that the vulnerability conditions hold.
    """
    reference = now if now is not None else datetime.now(timezone.utc)
    _require(_is_aware(reference), "now must be timezone-aware")

    _require(isinstance(advisory_ref, str) and re.fullmatch(r"[0-9a-f]{64}", advisory_ref) is not None,
             "advisory_ref must be a sha256 hex digest")
    advisory = store.get_object(advisory_ref)
    validate_advisory(advisory, store, now=reference)

    basis_refs = [advisory_ref, advisory["raw_ref"]]

    def result(status: str, reason: str) -> dict[str, Any]:
        return {
            "version_status": status,
            "applicability": "unknown",
            "basis_refs": list(basis_refs),
            "reason": reason,
        }

    fetched_at = _parse_iso(advisory["fetched_at"], "fetched_at")
    if not _is_fresh(fetched_at, advisory["ttl"], reference):
        return result("unknown", "advisory is stale")

    _require(isinstance(package, str) and package.strip(), "package must be a non-empty string")
    _require(isinstance(version, str) and version.strip(), "version must be a non-empty string")

    try:
        parsed_version = Version(version.strip())
    except InvalidVersion:
        return result("unknown", "version is not a valid PEP440 version")

    entries = advisory["entries"]
    package_entries = [
        entry for entry in entries
        if entry["package"].lower() == package.strip().lower()
    ]
    if not package_entries:
        return result("not_applicable", "no advisory entry for this package")
    relevant = [
        entry for entry in package_entries
        if entry["ecosystem"].lower() in SUPPORTED_ECOSYSTEMS
    ]
    if not relevant:
        return result("unknown", "advisory ecosystem is not supported")

    affected = False
    patched = False
    for entry in relevant:
        try:
            spec = SpecifierSet(entry["affected_range"])
        except InvalidSpecifier:
            return result("unknown", "advisory range is not a supported PEP440 range")
        if spec.contains(parsed_version, prereleases=True):
            affected = True
            continue
        first_patched = entry["first_patched_version"]
        if first_patched is not None:
            try:
                if parsed_version >= Version(first_patched):
                    patched = True
            except InvalidVersion:
                return result("unknown", "first_patched_version is not a valid PEP440 version")

    if affected:
        return result("affected", "version falls inside the advisory range")
    if patched:
        return result("patched", "version is at or above first_patched_version")
    return result("unknown", "version is outside the range but no first_patched_version is recorded")
