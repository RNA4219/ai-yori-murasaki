"""Agent policy bundles: the runtime policy the lab agents actually use.

A bundle is a JSON document holding, per agent role (red/blue/purple):

- ``model_role``: routing key for ``p2.route_model``
- ``tools``: allowlisted tool grants enforced by the ToolBroker
- ``budget``: loop budgets (turns, LLM calls, invalid actions, token cap,
  wall seconds and, for purple, fix attempts)
- ``system_prompt`` / ``task_template``: the prompts the LLM actually sees

The bundle is the improvement target of the agent RSI loop: candidates
modify prompts, grants or budgets, and comparisons run the same lab with
baseline and candidate bundles. The packaged ``policies_default.json`` is
the default executor's policy until an operator applies an approved
candidate (promotion and default-executor reflection are separate
operations).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict

from ..jsonio import DataError

DEFAULT_BUNDLE_PATH = Path(__file__).with_name("policies_default.json")

ROLES = ("red", "blue", "purple")

ALLOWED_TOOLS = (
    "probe_endpoint",
    "read_evidence",
    "submit_finding",
    "submit_hypothesis",
    "list_target_files",
    "read_target_file",
    "propose_patch",
    "stop",
)

MODEL_ROLES = ("red", "blue", "purple", "reviewer")

_BUDGET_INT_RANGES = {
    "max_turns": (1, 64),
    "max_llm_calls": (1, 64),
    "max_invalid_actions": (1, 16),
    "token_cap": (128, 8192),
    "wall_seconds": (30, 3600),
    "max_fix_attempts": (1, 8),
}

_PLACEHOLDERS = {
    "red": ("{surface}",),
    "blue": ("{telemetry}",),
    "purple": ("{surface}", "{findings}"),
}


def _fail(msg: str) -> None:
    raise DataError(msg, "input_invalid")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def validate_bundle(bundle: Any) -> Dict[str, Any]:
    """Strictly validate an agent policy bundle. Returns the bundle."""
    if not isinstance(bundle, dict):
        _fail("bundle must be object")
    if set(bundle.keys()) != {"bundle_id", "version", "roles"}:
        _fail("bundle keys must be exactly bundle_id, version, roles")
    if not isinstance(bundle["bundle_id"], str) or not (
            1 <= len(bundle["bundle_id"]) <= 64):
        _fail("bundle_id must be 1..64 chars")
    if not isinstance(bundle["version"], str) or not bundle["version"]:
        _fail("version must be non-empty string")
    roles = bundle["roles"]
    if not isinstance(roles, dict) or set(roles.keys()) != set(ROLES):
        _fail("roles must be exactly red, blue, purple")
    for role in ROLES:
        cfg = roles[role]
        if not isinstance(cfg, dict):
            _fail("role config must be object")
        if set(cfg.keys()) != {"model_role", "tools", "budget",
                               "system_prompt", "task_template"}:
            _fail("role config keys invalid for {}".format(role))
        if cfg["model_role"] not in MODEL_ROLES:
            _fail("model_role invalid for {}".format(role))
        tools = cfg["tools"]
        if not isinstance(tools, list) or not tools:
            _fail("tools must be non-empty list")
        for tool in tools:
            if not isinstance(tool, str) or tool not in ALLOWED_TOOLS:
                _fail("tool not allowed: {!r}".format(tool))
        if "stop" not in tools:
            _fail("tools must include stop")
        if "probe_endpoint" in tools and role != "red":
            _fail("probe_endpoint is red-only")
        if "submit_finding" in tools and role != "blue":
            _fail("submit_finding is blue-only")
        if "propose_patch" in tools and role != "purple":
            _fail("propose_patch is purple-only")
        budget = cfg["budget"]
        if not isinstance(budget, dict):
            _fail("budget must be object")
        expected_keys = set(_BUDGET_INT_RANGES)
        if role == "purple":
            if set(budget.keys()) != expected_keys:
                _fail("purple budget keys invalid")
        else:
            if set(budget.keys()) != expected_keys - {"max_fix_attempts"}:
                _fail("{} budget keys invalid".format(role))
        for key, value in budget.items():
            low, high = _BUDGET_INT_RANGES[key]
            if not _is_int(value) or not (low <= value <= high):
                _fail("budget.{} out of range".format(key))
        for key in ("system_prompt", "task_template"):
            text = cfg[key]
            if not isinstance(text, str) or not (
                    1 <= len(text) <= 8000):
                _fail("{}.{} must be 1..8000 chars".format(role, key))
        for placeholder in _PLACEHOLDERS[role]:
            if placeholder not in cfg["task_template"]:
                _fail("{} task_template missing {}".format(role, placeholder))
    return bundle


def load_default_bundle() -> Dict[str, Any]:
    """Load and validate the packaged default bundle."""
    try:
        raw = DEFAULT_BUNDLE_PATH.read_text(encoding="utf-8")
    except OSError as exc:
        raise DataError("default bundle unreadable", "input_invalid") from exc
    try:
        bundle = json.loads(raw)
    except ValueError as exc:
        raise DataError("default bundle invalid JSON", "input_invalid") from exc
    return validate_bundle(bundle)


def render_task(role_cfg: Dict[str, Any], placeholders: Dict[str, str]) -> str:
    """Fill the role task template with the given placeholders."""
    template = role_cfg["task_template"]
    try:
        return template.format(**placeholders)
    except (KeyError, IndexError, ValueError) as exc:
        raise DataError("task placeholders invalid: {}".format(
            exc), "input_invalid") from exc
