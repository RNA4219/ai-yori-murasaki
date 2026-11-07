"""Sigma bounded-subset parser (stdlib, no eval).

Re-exports the parser from normalizers for direct use.
"""
from __future__ import annotations

from .normalizers import (
    SigmaMalformed,
    SigmaUnsupported,
    _sigma_compile_condition,
    _sigma_eval,
    _sigma_match_selection,
    _sigma_tokenize,
    _sigma_validate_rule,
    normalize_sigma,
)

__all__ = [
    "SigmaMalformed",
    "SigmaUnsupported",
    "normalize_sigma",
    "_sigma_compile_condition",
    "_sigma_eval",
    "_sigma_match_selection",
    "_sigma_tokenize",
    "_sigma_validate_rule",
]
