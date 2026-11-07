"""Tests for ai_yori_murasaki.advisories (SPEC-ASSESSMENT §1.8)."""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from ai_yori_murasaki.advisories import (
    import_advisory,
    match_version,
    validate_advisory,
)
from ai_yori_murasaki.jsonio import DataError


GHSA = "GHSA-9wx4-h78v-vm56"
API_URL = f"https://api.github.com/advisories/{GHSA}"
WEB_URL = f"https://github.com/psf/requests/security/advisories/{GHSA}"


def _raw(**overrides):
    raw = {
        "ghsa_id": GHSA,
        "cve_id": "CVE-2024-35195",
        "summary": "Session keeps verify=False",
        "description": "Requests session does not reset verify after first request.",
        "vulnerabilities": [
            {
                "package": {"ecosystem": "pip", "name": "requests"},
                "vulnerable_version_range": "< 2.32.0",
                "first_patched_version": "2.32.0",
                "vulnerable_functions": [],
            }
        ],
    }
    raw.update(overrides)
    return raw


@pytest.fixture()
def store(tmp_path):
    from ai_yori_murasaki.storage import Store

    workspace = tmp_path / "workspace"
    trusted_root = tmp_path / "trusted"
    workspace.mkdir()
    trusted_root.mkdir()
    return Store(workspace, trusted_root)


@pytest.fixture()
def now():
    return datetime.now(timezone.utc) - timedelta(seconds=1)


def test_import_and_match_affected(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 3600)
    result = match_version(store, ref, "requests", "2.31.0", now=now)
    assert result["version_status"] == "affected"
    assert result["applicability"] == "unknown"
    assert ref in result["basis_refs"]


def test_match_patched_requires_first_patched(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 3600)
    result = match_version(store, ref, "requests", "2.32.0", now=now)
    assert result["version_status"] == "patched"
    assert result["applicability"] == "unknown"


def test_newer_outside_range_without_first_patched_is_unknown(store, now):
    raw = _raw()
    raw["vulnerabilities"][0]["first_patched_version"] = None
    ref = import_advisory(store, API_URL, raw, now, 3600)
    result = match_version(store, ref, "requests", "2.32.0", now=now)
    assert result["version_status"] == "unknown"
    assert result["applicability"] == "unknown"


def test_alternate_package_not_applicable(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 3600)
    result = match_version(store, ref, "urllib3", "2.31.0", now=now)
    assert result["version_status"] == "not_applicable"


def test_unknown_ecosystem_is_unknown(store, now):
    raw = _raw()
    raw["vulnerabilities"][0]["package"]["ecosystem"] = "npm"
    ref = import_advisory(store, API_URL, raw, now, 3600)
    result = match_version(store, ref, "requests", "2.31.0", now=now)
    assert result["version_status"] == "unknown"
    assert result["reason"]


def test_prerelease_and_invalid_versions(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 3600)
    pre = match_version(store, ref, "requests", "2.32.0rc1", now=now)
    assert pre["version_status"] == "unknown"
    assert pre["reason"]
    bad = match_version(store, ref, "requests", "not-a-version", now=now)
    assert bad["version_status"] == "unknown"


def test_stale_advisory_is_unknown(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 60)
    later = now + timedelta(seconds=61)
    result = match_version(store, ref, "requests", "2.31.0", now=later)
    assert result["version_status"] == "unknown"
    assert "stale" in result["reason"]


def test_mismatched_cas_extraction_rejected(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 3600)
    advisory = store.get_object(ref)
    tampered = copy.deepcopy(advisory)
    tampered["entries"][0]["affected_range"] = "<1.0.0"
    with pytest.raises(DataError):
        validate_advisory(tampered, store, now=now)


def test_raw_ref_must_resolve(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 3600)
    advisory = store.get_object(ref)
    advisory["raw_ref"] = "0" * 64
    with pytest.raises(DataError):
        validate_advisory(advisory, store, now=now)


def test_web_url_accepted(store, now):
    ref = import_advisory(store, WEB_URL, _raw(), now, 3600)
    assert match_version(store, ref, "requests", "2.31.0", now=now)["version_status"] == "affected"


def test_rejects_non_https_and_wrong_host(store, now):
    with pytest.raises(DataError):
        import_advisory(store, f"http://api.github.com/advisories/{GHSA}", _raw(), now, 3600)
    with pytest.raises(DataError):
        import_advisory(store, f"https://evil.example/advisories/{GHSA}", _raw(), now, 3600)


def test_rejects_ghsa_mismatch(store, now):
    with pytest.raises(DataError):
        import_advisory(store, API_URL, _raw(ghsa_id="GHSA-aaaa-bbbb-cccc"), now, 3600)


def test_rejects_future_fetched_at(store, now):
    with pytest.raises(DataError):
        import_advisory(store, API_URL, _raw(), now + timedelta(days=1), 3600)


def test_rejects_naive_fetched_at(store):
    with pytest.raises(DataError):
        import_advisory(store, API_URL, _raw(), datetime(2026, 11, 7, 12, 0), 3600)


def test_rejects_bad_ttl(store, now):
    for ttl in (0, -1, 31_536_001, True, 1.5):
        with pytest.raises(DataError):
            import_advisory(store, API_URL, _raw(), now, ttl)


def test_rejects_secret_keys(store, now):
    raw = _raw()
    raw["token"] = "x"
    with pytest.raises(DataError):
        import_advisory(store, API_URL, raw, now, 3600)


def test_rejects_unknown_fields_in_advisory(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 3600)
    advisory = store.get_object(ref)
    advisory["extra"] = 1
    with pytest.raises(DataError):
        validate_advisory(advisory, store, now=now)


def test_rejects_invalid_range(store, now):
    raw = _raw()
    raw["vulnerabilities"][0]["vulnerable_version_range"] = "not a range"
    with pytest.raises(DataError):
        import_advisory(store, API_URL, raw, now, 3600)


def test_no_success_from_version_alone(store, now):
    ref = import_advisory(store, API_URL, _raw(), now, 3600)
    for version in ("2.31.0", "2.32.0", "1.0.0"):
        result = match_version(store, ref, "requests", version, now=now)
        assert result["applicability"] == "unknown"
        assert "success" not in result
