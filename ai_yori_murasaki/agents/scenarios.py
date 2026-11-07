"""Scenario fixtures for the agent-driven lab (RSI train/validation/holdout).

Each scenario is a directory ``ai_yori_murasaki/scenarios/<scenario_id>/``
containing:

- ``target.py``: a lab target with the same documented HTTP surface
  (``/health``, ``/public``, ``/record?role=``) but a structurally
  different access-control flaw. Before the fix, every scenario lets a
  non-admin role (e.g. ``guest``) read the protected record.
- ``scenario.json``: ``scenario_id``, ``split`` (train/validation/holdout),
  ``flaw_class`` and the public API ``surface`` text shown to agents.

The scenario answer (where the flaw lives, how to fix it) is never
exposed to agents; the Oracle verifies actual HTTP behavior only.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Dict, List

from ..jsonio import DataError

_SCENARIOS_ROOT = Path(__file__).resolve().parent.parent / "scenarios"

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,63}$")

_SPLITS = ("train", "validation", "holdout")

_REQUIRED_KEYS = {"scenario_id", "split", "flaw_class", "surface"}


def _load_all() -> Dict[str, dict]:
    scenarios: Dict[str, dict] = {}
    if not _SCENARIOS_ROOT.is_dir():
        return scenarios
    for entry in sorted(_SCENARIOS_ROOT.iterdir()):
        if not entry.is_dir():
            continue
        meta_path = entry / "scenario.json"
        target_path = entry / "target.py"
        if not meta_path.is_file() or not target_path.is_file():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise DataError(
                "scenario metadata unreadable: {}".format(entry.name),
                "input_invalid") from exc
        if not isinstance(meta, dict) or set(meta.keys()) != _REQUIRED_KEYS:
            raise DataError(
                "scenario metadata invalid: {}".format(entry.name),
                "input_invalid")
        if meta["scenario_id"] != entry.name:
            raise DataError(
                "scenario id mismatch: {}".format(entry.name), "input_invalid")
        if meta["split"] not in _SPLITS:
            raise DataError(
                "scenario split invalid: {}".format(entry.name), "input_invalid")
        if not isinstance(meta["flaw_class"], str) or not meta["flaw_class"]:
            raise DataError(
                "scenario flaw_class invalid: {}".format(entry.name),
                "input_invalid")
        if not isinstance(meta["surface"], str) or not meta["surface"]:
            raise DataError(
                "scenario surface invalid: {}".format(entry.name),
                "input_invalid")
        scenarios[entry.name] = {
            "scenario_id": entry.name,
            "split": meta["split"],
            "flaw_class": meta["flaw_class"],
            "surface": meta["surface"],
            "dir": entry,
            "target_path": target_path,
        }
    if not scenarios:
        raise DataError("no scenarios found", "input_invalid")
    return scenarios


def list_scenarios() -> List[dict]:
    """Return the catalog of scenarios (without filesystem paths)."""
    out: List[dict] = []
    for meta in _load_all().values():
        out.append({
            "scenario_id": meta["scenario_id"],
            "split": meta["split"],
            "flaw_class": meta["flaw_class"],
            "surface": meta["surface"],
        })
    return out


def load_scenario(scenario_id: str) -> dict:
    """Return one scenario descriptor or raise DataError."""
    if not isinstance(scenario_id, str) or not _ID_RE.match(scenario_id):
        raise DataError("invalid scenario_id", "input_invalid")
    all_scenarios = _load_all()
    meta = all_scenarios.get(scenario_id)
    if meta is None:
        raise DataError("unknown scenario_id", "input_invalid")
    return dict(meta)


def splits() -> Dict[str, List[str]]:
    """Return scenario ids grouped by split (train/validation/holdout)."""
    grouped: Dict[str, List[str]] = {name: [] for name in _SPLITS}
    for meta in _load_all().values():
        grouped[meta["split"]].append(meta["scenario_id"])
    for name in _SPLITS:
        grouped[name].sort()
    return grouped
