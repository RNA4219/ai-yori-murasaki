"""CVSS v4.0 severity scoring (R-056, SPEC-ASSESSMENT §1.5).

Uses the third-party ``cvss`` package (version 3.6, LGPLv3+) as the
calculator.  FIRST CVSS v4.0 specification attribution is recorded in
``THIRD_PARTY_NOTICES`` and in the ``calculator`` field of every record.

Public API::

    score_cvss4(vector, metric_basis, store) -> CvssRecord

``CvssRecord`` is a plain ``dict`` with exactly the specification fields
minus ``finding_id`` (the caller attaches it).  No extra keys are added.
"""
from __future__ import annotations

from typing import Any, Mapping

from cvss import CVSS4

from .jsonio import DataError


#: Attribution for the FIRST CVSS v4.0 specification and the calculator.
FIRST_ATTRIBUTION = (
    "CVSS v4.0 specification, FIRST (Forum of Incident Response and "
    "Security Teams), https://www.first.org/cvss/v4-0/"
)

#: Calculator identifier recorded in every CvssRecord.
CALCULATOR = "cvss/3.6"

#: The eleven CVSS v4.0 base metrics.
BASE_METRICS: tuple[str, ...] = (
    "AV", "AC", "AT", "PR", "UI", "VC", "VI", "VA", "SC", "SI", "SA",
)

#: Threat metrics (non-X means the vector is at least CVSS-BT).
THREAT_METRICS: tuple[str, ...] = ("E",)

#: Environmental metrics (non-X means the vector is at least CVSS-BE).
ENVIRONMENTAL_METRICS: tuple[str, ...] = (
    "CR", "IR", "AR", "MAV", "MAC", "MAT", "MPR", "MUI", "MVC", "MVI",
    "MVA", "MSC", "MSI", "MSA",
)

#: All metrics that may appear in a CVSS v4.0 vector.
ALL_METRICS: tuple[str, ...] = BASE_METRICS + THREAT_METRICS + ENVIRONMENTAL_METRICS

#: Metrics that require a basis reference when explicitly specified (non-X).
#: The eleven base metrics always require a basis; threat/environmental
#: metrics require one only when explicitly set to a non-X value.
_BASIS_REQUIRED_ALWAYS: frozenset[str] = frozenset(BASE_METRICS)
_BASIS_REQUIRED_IF_SET: frozenset[str] = frozenset(THREAT_METRICS + ENVIRONMENTAL_METRICS)


def _empty_record(
    status: str,
    reason: str,
    metric_basis: Mapping[str, list[str]] | None,
) -> dict[str, Any]:
    return {
        "status": status,
        "vector": None,
        "score": None,
        "nomenclature": None,
        "calculator": CALCULATOR,
        "metric_basis": dict(metric_basis) if metric_basis else {},
        "reason": reason,
    }


def _parse_metrics(vector: str) -> dict[str, str]:
    """Parse a CVSS v4.0 vector into a metric->value mapping.

    Raises ``DataError`` for malformed syntax.  The ``cvss`` package is the
    authority on validity; this helper only extracts the metric keys so we
    can inspect which metrics were explicitly specified.
    """
    if not isinstance(vector, str) or not vector:
        raise DataError("CVSS vector must be a non-empty string")
    if not vector.startswith("CVSS:4.0/"):
        raise DataError("CVSS vector must start with CVSS:4.0/")
    metrics: dict[str, str] = {}
    for part in vector[len("CVSS:4.0/"):].split("/"):
        if not part:
            raise DataError("CVSS vector contains an empty metric")
        if ":" not in part:
            raise DataError("CVSS metric must be KEY:VALUE")
        key, _, value = part.partition(":")
        if not key or not value:
            raise DataError("CVSS metric must be KEY:VALUE")
        if key in metrics:
            raise DataError(f"duplicate CVSS metric: {key}")
        metrics[key] = value
    return metrics


def _nomenclature(metrics: Mapping[str, str]) -> str:
    """Derive nomenclature from the metrics actually specified.

    ``X`` means unspecified and must not change the nomenclature.
    """
    threat = any(metrics.get(m, "X") != "X" for m in THREAT_METRICS)
    environmental = any(metrics.get(m, "X") != "X" for m in ENVIRONMENTAL_METRICS)
    if threat and environmental:
        return "CVSS-BTE"
    if threat:
        return "CVSS-BT"
    if environmental:
        return "CVSS-BE"
    return "CVSS-B"


def _resolve_basis(
    metric_basis: Mapping[str, list[str]] | None,
    store: Any,
) -> dict[str, list[str]]:
    """Validate and resolve every basis reference against the store.

    Raises ``DataError`` if a reference cannot be resolved.
    """
    if metric_basis is None:
        return {}
    if not isinstance(metric_basis, Mapping):
        raise DataError("metric_basis must be a mapping")
    resolved: dict[str, list[str]] = {}
    for metric, refs in metric_basis.items():
        if not isinstance(metric, str):
            raise DataError("metric_basis keys must be strings")
        if not isinstance(refs, list):
            raise DataError("metric_basis values must be lists of refs")
        for ref in refs:
            if not isinstance(ref, str) or not ref:
                raise DataError("metric_basis refs must be non-empty strings")
            try:
                store.get_object(ref)
            except DataError:
                raise
            except Exception as exc:  # pragma: no cover - defensive
                raise DataError("metric_basis ref cannot be resolved") from exc
        resolved[metric] = list(refs)
    return resolved


def _missing_basis(
    metrics: Mapping[str, str],
    metric_basis: Mapping[str, list[str]],
) -> list[str]:
    """Return metrics that require a basis but have none."""
    missing: list[str] = []
    for metric in _BASIS_REQUIRED_ALWAYS:
        if not metric_basis.get(metric):
            missing.append(metric)
    for metric in _BASIS_REQUIRED_IF_SET:
        value = metrics.get(metric, "X")
        if value != "X" and not metric_basis.get(metric):
            missing.append(metric)
    return missing


def score_cvss4(
    vector: str | None,
    metric_basis: Mapping[str, list[str]] | None,
    store: Any,
) -> dict[str, Any]:
    """Score a CVSS v4.0 vector per SPEC-ASSESSMENT §1.5.

    Returns a ``CvssRecord`` dict (without ``finding_id``) with exactly the
    specification fields.  Behaviour:

    * Invalid syntax -> ``status="invalid"``, ``score=None``, reason set.
    * Missing basis for any required metric -> ``status="unevaluated"``.
    * Unresolvable basis ref -> ``DataError``.
    * Otherwise -> ``status="scored"`` with the calculator's score.

    Parsing happens first so that invalid syntax is reported as invalid
    even when the basis is incomplete.
    """
    # 1. Parse first: invalid syntax must win over missing basis.
    if vector is None:
        return _empty_record("invalid", "vector is required", metric_basis)
    try:
        metrics = _parse_metrics(vector)
        parsed = CVSS4(vector)
        clean = parsed.clean_vector()
        score = parsed.scores()[0]
    except DataError as exc:
        return _empty_record("invalid", f"invalid CVSS vector: {exc}", metric_basis)
    except Exception as exc:
        return _empty_record("invalid", f"invalid CVSS vector: {exc}", metric_basis)

    # 2. Resolve basis refs (raises DataError on unresolvable refs).
    resolved_basis = _resolve_basis(metric_basis, store)

    # 3. Check that every required metric has a basis.
    missing = _missing_basis(metrics, resolved_basis)
    if missing:
        return _empty_record(
            "unevaluated",
            "missing metric basis: " + ", ".join(sorted(missing)),
            resolved_basis,
        )

    # 4. Scored.
    return {
        "status": "scored",
        "vector": clean,
        "score": float(score),
        "nomenclature": _nomenclature(metrics),
        "calculator": CALCULATOR,
        "metric_basis": resolved_basis,
        "reason": "",
    }
