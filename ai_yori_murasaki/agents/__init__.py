"""Agent execution package for ai-yori-murasaki.

The package provides the brokered agent loop used by the product's
normal execution path: the LLM decides the next action from observations,
the ToolBroker validates grants/arguments and executes allowlisted tools,
and every decision is recorded on the evidence chain.

Public API:

- ``AgentLoop``: budget-bounded LLM agent loop (core)
- ``ToolBroker`` / ``build_tool_schemas``: brokered tool execution
- ``load_default_bundle`` / ``validate_bundle``: agent policy bundles
- ``list_scenarios`` / ``load_scenario`` / ``splits``: scenario fixtures
"""
from .core import AgentLoop
from .policies import (DEFAULT_BUNDLE_PATH, load_default_bundle,
                       render_task, validate_bundle)
from .scenarios import list_scenarios, load_scenario, splits
from .tools import ToolBroker, build_tool_schemas

__all__ = [
    "AgentLoop",
    "ToolBroker",
    "build_tool_schemas",
    "load_default_bundle",
    "validate_bundle",
    "render_task",
    "DEFAULT_BUNDLE_PATH",
    "list_scenarios",
    "load_scenario",
    "splits",
]
