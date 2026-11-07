"""Budget-bounded agent loop: observation -> LLM decision -> brokered tool.

Protocol: the LLM answers each turn with exactly one JSON object
``{"thought": str, "action": {"tool": str, "args": {...}}}``. The broker
validates grants and arguments, executes the tool and returns the result
as the next user message. Failure semantics are fail-closed:

- incomplete / empty / unavailable LLM responses end the loop as
  ``llm_failure`` (never retried into a fixed answer),
- budget exhaustion ends the loop as ``budget_exhausted`` /
  ``invalid_action_budget``,
- only a validated ``stop`` report yields ``completed``.

Every turn, tool request and denial is recorded on the run's evidence
chain with source="llm" and the effective request hash, so the monitor
and the Oracle can audit what the model actually decided.
"""
from __future__ import annotations

import copy
import json
import time
from typing import Any, Dict, List, Optional

from ..adapters.llm import LLMClient
from ..p2 import route_model
from .tools import ToolBroker, build_tool_schemas

_RESULT_CAP = 32 * 1024


class AgentLoop:
    """One role's LLM agent loop for a single run."""

    def __init__(self, *, llm_client: LLMClient, runtime: Any,
                 campaign_id: str, run_id: str, role: str,
                 role_cfg: Dict[str, Any], routes: Optional[dict] = None,
                 seed: int = 0,
                 url: str = "http://localhost:11434/api/chat") -> None:
        self._llm = llm_client
        self._runtime = runtime
        self._campaign_id = campaign_id
        self._run_id = run_id
        self._role = role
        self._role_cfg = role_cfg
        self._routes = routes
        self._seed = seed
        self._url = url
        self._budget = role_cfg["budget"]
        self._model = route_model(role_cfg["model_role"], routes)

    # ------------------------------------------------------------------ #

    def _system_prompt(self) -> str:
        schemas = json.dumps(build_tool_schemas(), ensure_ascii=False,
                             sort_keys=True)
        return "{}\n\n利用可能なツール(JSON Schema):\n{}".format(
            self._role_cfg["system_prompt"], schemas)

    def _record_turn(self, extensions: Dict[str, Any],
                     evidence_refs: tuple = ()) -> None:
        try:
            self._runtime.record_event(
                self._run_id, "state_captured", "llm",
                evidence_refs=evidence_refs, extensions=extensions)
        except Exception:
            pass

    def _parse_action(self, content: str) -> Dict[str, Any]:
        text = content.strip()
        if text.startswith("```"):
            first_newline = text.find("\n")
            if first_newline >= 0:
                text = text[first_newline + 1:]
            if text.rstrip().endswith("```"):
                text = text.rstrip()[:-3]
            text = text.strip()
        doc = json.loads(text)
        if not isinstance(doc, dict):
            raise ValueError("action must be object")
        action = doc.get("action")
        if not isinstance(action, dict):
            raise ValueError("action missing")
        tool = action.get("tool")
        if not isinstance(tool, str) or not tool:
            raise ValueError("tool missing")
        args = action.get("args")
        if args is None:
            args = {}
        if not isinstance(args, dict):
            raise ValueError("args must be object")
        thought = doc.get("thought", "")
        if not isinstance(thought, str):
            thought = ""
        return {"thought": thought, "tool": tool, "args": args}

    def _summarize(self, status: str, stop_reason: Optional[str],
                   report: Optional[dict], turns: int, llm_calls: int,
                   actions: List[dict], last_error: Optional[str]) -> dict:
        return {
            "status": status,
            "stop_reason": stop_reason,
            "report": report,
            "turns": turns,
            "llm_calls": llm_calls,
            "actions": actions,
            "last_error": last_error,
            "model": self._model,
        }

    # ------------------------------------------------------------------ #

    def run(self, *, task: str, target_dir: Any = None,
            probe_executor: Optional[Any] = None,
            unit_validator: Optional[Any] = None,
            initial_refs: Optional[List[str]] = None) -> dict:
        budget = self._budget
        broker = ToolBroker(
            runtime=self._runtime, campaign_id=self._campaign_id,
            run_id=self._run_id, role=self._role, grants=self._role_cfg["tools"],
            target_dir=target_dir, probe_executor=probe_executor,
            unit_validator=unit_validator,
            max_fix_attempts=budget.get("max_fix_attempts"))
        if initial_refs:
            broker.issue_refs(list(initial_refs))

        messages: List[dict] = [
            {"role": "system", "content": self._system_prompt()},
            {"role": "user", "content": task},
        ]
        turns = 0
        llm_calls = 0
        invalid = 0
        actions: List[dict] = []
        started = time.monotonic()
        max_invalid = budget["max_invalid_actions"]

        while True:
            if turns >= budget["max_turns"] or llm_calls >= budget["max_llm_calls"]:
                return self._summarize("budget_exhausted", "budget", None,
                                       turns, llm_calls, actions, None)
            if time.monotonic() - started > budget["wall_seconds"]:
                return self._summarize("budget_exhausted", "wall_seconds",
                                       None, turns, llm_calls, actions, None)

            turns += 1
            llm_calls += 1
            resp: Optional[dict] = None
            try:
                resp = self._llm.chat(
                    url=self._url, role=self._role,
                    campaign=self._campaign_id, run=self._run_id,
                    model=self._model, messages=copy.deepcopy(messages),
                    temperature=0.0, seed=self._seed,
                    token_cap=budget["token_cap"], native=True)
            except Exception:
                resp = None
            if not isinstance(resp, dict) or resp.get("complete") is not True:
                refs = ()
                if isinstance(resp, dict):
                    try:
                        refs = (self._runtime.store.put_object(resp),)
                    except Exception:
                        refs = ()
                self._record_turn({
                    "record_type": "agent_turn", "turn": turns,
                    "model": self._model, "llm_status": "incomplete",
                }, evidence_refs=refs)
                return self._summarize(
                    "llm_failure", "llm_incomplete", None, turns, llm_calls,
                    actions, "llm response incomplete or unavailable")
            content = resp.get("content")
            if not isinstance(content, str) or content.strip() == "":
                try:
                    refs = (self._runtime.store.put_object(resp),)
                except Exception:
                    refs = ()
                self._record_turn({
                    "record_type": "agent_turn", "turn": turns,
                    "model": self._model, "llm_status": "empty",
                }, evidence_refs=refs)
                return self._summarize(
                    "llm_failure", "llm_empty", None, turns, llm_calls,
                    actions, "llm content empty")
            try:
                refs = (self._runtime.store.put_object(resp),)
            except Exception:
                refs = ()
            request_hash = resp.get("request_hash")

            try:
                parsed = self._parse_action(content)
            except ValueError:
                invalid += 1
                self._record_turn({
                    "record_type": "agent_turn", "turn": turns,
                    "model": self._model, "parse_error": True,
                    "request_hash": request_hash,
                }, evidence_refs=refs)
                if invalid >= max_invalid:
                    return self._summarize(
                        "invalid_action_budget", "invalid_actions", None,
                        turns, llm_calls, actions,
                        "model output was not a single JSON action object")
                messages.append({"role": "user", "content": json.dumps(
                    {"type": "tool_error", "tool": None,
                     "error": "output_must_be_single_json_object"},
                    ensure_ascii=False)})
                continue

            tool = parsed["tool"]
            args = parsed["args"]
            thought = parsed["thought"]
            self._record_turn({
                "record_type": "agent_turn", "turn": turns,
                "model": self._model, "request_hash": request_hash,
                "thought": thought[:512], "tool": tool,
            }, evidence_refs=refs)

            result = broker.execute(tool, args)
            actions.append({"turn": turns, "tool": tool,
                            "status": result["type"]})

            if result["type"] == "stop":
                payload = result["result"]
                self._record_turn({
                    "record_type": "agent_stopped", "turn": turns,
                    "model": self._model, "reason": payload["reason"][:512],
                })
                return self._summarize(
                    "completed", payload["reason"], payload["report"],
                    turns, llm_calls, actions, None)

            encoded = json.dumps(result, ensure_ascii=False, sort_keys=True,
                                 allow_nan=False)
            if len(encoded.encode("utf-8")) > _RESULT_CAP:
                encoded = encoded[:_RESULT_CAP] + "...truncated"
            messages.append({"role": "user", "content": encoded})

            if result["type"] == "tool_error":
                invalid += 1
                if invalid >= max_invalid:
                    return self._summarize(
                        "invalid_action_budget", "invalid_actions", None,
                        turns, llm_calls, actions,
                        "repeated invalid or denied tool requests")
