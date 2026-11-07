"""Tests for ai_yori_murasaki.severity.score_cvss4 (SPEC-ASSESSMENT §1.5)."""
from __future__ import annotations

import pytest

from ai_yori_murasaki.severity import (
    BASE_METRICS,
    CALCULATOR,
    FIRST_ATTRIBUTION,
    score_cvss4,
)
from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.storage import Store


# A known FIRST CVSS v4.0 vector scoring 9.9 (CVSS-B).
VECTOR_9_9 = "CVSS:4.0/AV:N/AC:L/AT:N/PR:N/UI:N/VC:H/VI:H/VA:H/SC:H/SI:H/SA:N"


def _basis_for(store: Store, metrics: list[str]) -> dict[str, list[str]]:
    """Create a real CAS object per metric and return a basis mapping."""
    basis: dict[str, list[str]] = {}
    for metric in metrics:
        ref = store.put_object({"metric": metric, "note": "basis"})
        basis[metric] = [ref]
    return basis


@pytest.fixture()
def store(tmp_path):
    workspace = tmp_path / "workspace"
    trusted_root = tmp_path / "trusted"
    workspace.mkdir()
    trusted_root.mkdir()
    return Store(workspace, trusted_root)


def test_first_attribution_constant():
    assert "FIRST" in FIRST_ATTRIBUTION
    assert "CVSS v4.0" in FIRST_ATTRIBUTION


def test_known_first_vector_scores_9_9(store):
    basis = _basis_for(store, list(BASE_METRICS))
    record = score_cvss4(VECTOR_9_9, basis, store)
    assert record["status"] == "scored"
    assert record["score"] == pytest.approx(9.9)
    assert record["nomenclature"] == "CVSS-B"
    assert record["calculator"] == CALCULATOR
    assert record["reason"] == ""
    assert record["vector"] is not None
    assert record["vector"].startswith("CVSS:4.0/")
    # Exactly the specification fields minus finding_id.
    assert set(record) == {
        "status", "vector", "score", "nomenclature", "calculator",
        "metric_basis", "reason",
    }


def test_invalid_vector_returns_invalid(store):
    basis = _basis_for(store, list(BASE_METRICS))
    record = score_cvss4("not-a-vector", basis, store)
    assert record["status"] == "invalid"
    assert record["score"] is None
    assert record["vector"] is None
    assert record["nomenclature"] is None
    assert record["reason"]


def test_null_vector_returns_invalid(store):
    basis = _basis_for(store, list(BASE_METRICS))
    record = score_cvss4(None, basis, store)
    assert record["status"] == "invalid"
    assert record["score"] is None
    assert record["reason"]


def test_invalid_syntax_wins_over_missing_basis(store):
    # No basis at all, but invalid syntax must still be reported as invalid.
    record = score_cvss4("CVSS:4.0/AV:N/BOGUS", {}, store)
    assert record["status"] == "invalid"
    assert record["score"] is None


def test_basis_omitted_returns_unevaluated(store):
    record = score_cvss4(VECTOR_9_9, {}, store)
    assert record["status"] == "unevaluated"
    assert record["score"] is None
    assert record["vector"] is None
    assert record["nomenclature"] is None
    assert record["reason"]


def test_partial_base_basis_returns_unevaluated(store):
    # Only some base metrics have a basis.
    basis = _basis_for(store, ["AV", "AC"])
    record = score_cvss4(VECTOR_9_9, basis, store)
    assert record["status"] == "unevaluated"
    assert record["score"] is None


def test_missing_cas_ref_raises_data_error(store):
    basis = _basis_for(store, list(BASE_METRICS))
    basis["AV"] = ["0" * 64]
    with pytest.raises(DataError):
        score_cvss4(VECTOR_9_9, basis, store)


def test_threat_metric_requires_basis(store):
    vector = VECTOR_9_9 + "/E:A"
    basis = _basis_for(store, list(BASE_METRICS))
    record = score_cvss4(vector, basis, store)
    assert record["status"] == "unevaluated"
    assert "E" in record["reason"]


def test_threat_metric_with_basis_is_bt(store):
    vector = VECTOR_9_9 + "/E:A"
    basis = _basis_for(store, list(BASE_METRICS) + ["E"])
    record = score_cvss4(vector, basis, store)
    assert record["status"] == "scored"
    assert record["nomenclature"] == "CVSS-BT"


def test_environmental_metric_with_basis_is_be(store):
    vector = VECTOR_9_9 + "/CR:H"
    basis = _basis_for(store, list(BASE_METRICS) + ["CR"])
    record = score_cvss4(vector, basis, store)
    assert record["status"] == "scored"
    assert record["nomenclature"] == "CVSS-BE"


def test_threat_and_environmental_with_basis_is_bte(store):
    vector = VECTOR_9_9 + "/E:A/CR:H"
    basis = _basis_for(store, list(BASE_METRICS) + ["E", "CR"])
    record = score_cvss4(vector, basis, store)
    assert record["status"] == "scored"
    assert record["nomenclature"] == "CVSS-BTE"


def test_explicit_x_does_not_change_nomenclature(store):
    # E:X and CR:X are unspecified and must not require a basis nor change
    # the nomenclature away from CVSS-B.
    vector = VECTOR_9_9 + "/E:X/CR:X"
    basis = _basis_for(store, list(BASE_METRICS))
    record = score_cvss4(vector, basis, store)
    assert record["status"] == "scored"
    assert record["nomenclature"] == "CVSS-B"


def test_environmental_metric_requires_basis(store):
    vector = VECTOR_9_9 + "/CR:H"
    basis = _basis_for(store, list(BASE_METRICS))
    record = score_cvss4(vector, basis, store)
    assert record["status"] == "unevaluated"
    assert "CR" in record["reason"]
