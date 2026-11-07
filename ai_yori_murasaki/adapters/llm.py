"""LLM adapter: Ollama native + OpenAI compatible via HttpBroker.

- stream=false always
- response cap / timeout / retry budget enforced
- cache keyed by role/campaign/run/model/prompt_digest
- tool_calls are NOT executed
- metadata measured from request/response bodies
"""
from __future__ import annotations

import copy
import json
from typing import Any

from .. import jsonio
from . import base
from .normalizers import normalize_llm


def _check_budget(name: str, value: int, lo: int, hi: int) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise jsonio.DataError(f"{name} must be integer")
    if value < lo or value > hi:
        raise jsonio.DataError(f"{name} out of range")
    return value


class LLMClient:
    def __init__(self, broker: base.HttpBroker, *, timeout_ms: int = 60000,
                 retry_cap: int = 2, response_cap: int = 5242880) -> None:
        if not isinstance(broker, base.HttpBroker):
            raise jsonio.DataError("broker must be HttpBroker")
        self._broker = broker
        self._timeout_ms = _check_budget("timeout_ms", timeout_ms, 1, 300000)
        self._retry_cap = _check_budget("retry_cap", retry_cap, 0, 2)
        self._response_cap = _check_budget("response_cap", response_cap, 1, 52428800)
        self._cache: dict[str, dict] = {}

    def _cache_key(self, *, role: str, campaign: str, run: str,
                   model: str, prompt_digest: str) -> str:
        return jsonio.digest({
            "role": role, "campaign": campaign, "run": run,
            "model": model, "prompt_digest": prompt_digest,
        })

    def chat(self, *, url: str, role: str, campaign: str, run: str,
             model: str, messages: list[dict], temperature: float = 0.0,
             seed: int | None = None, token_cap: int | None = None,
             native: bool = True) -> dict:
        """Send a chat request. Returns normalized record.

        native=True -> Ollama /api/chat; native=False -> OpenAI compatible.
        """
        if native:
            body = {
                "model": model, "messages": messages, "stream": False,
                "options": {"temperature": temperature},
            }
            if seed is not None:
                body["options"]["seed"] = seed
            if token_cap is not None:
                body["options"]["num_predict"] = token_cap
            body["think"] = "low" if model.startswith("glm") else False
        else:
            body = {
                "model": model, "messages": messages, "stream": False,
                "temperature": temperature,
            }
            if seed is not None:
                body["seed"] = seed
            if token_cap is not None:
                body["max_tokens"] = token_cap

        # Cache key covers the real request digest (URL/native/temp/seed/
        # token_cap/think) plus role/campaign/run/model, not just messages.
        request_digest = jsonio.digest({
            "url": url, "native": native, "temperature": temperature,
            "seed": seed, "token_cap": token_cap,
            "think": body.get("think"),
            "body": body,
        })
        key = self._cache_key(role=role, campaign=campaign, run=run,
                              model=model, prompt_digest=request_digest)
        if key in self._cache:
            return copy.deepcopy(self._cache[key])

        raw_body = jsonio.canonical(body)
        last_err: Exception | None = None
        for _ in range(self._retry_cap + 1):
            try:
                resp = self._broker.request(
                    "POST", url, body=raw_body,
                    headers={"Content-Type": "application/json"},
                    timeout=self._timeout_ms / 1000.0,
                    max_bytes=self._response_cap,
                )
                if resp["status"] != 200:
                    raise base.AdapterUnavailable(
                        f"llm http status {resp['status']}")
                record = normalize_llm(resp["body"], request_body=body)
                # tool_calls are recorded but never executed
                record["tool_calls_executed"] = False
                record["requested_model"] = model
                record["temperature"] = temperature
                record["seed"] = seed
                record["token_cap"] = token_cap
                record["timeout_ms"] = self._timeout_ms
                record["request_hash"] = request_digest
                self._cache[key] = record
                return copy.deepcopy(record)
            except base.AdapterUnavailable as exc:
                last_err = exc
                continue
        raise base.AdapterUnavailable(str(last_err) if last_err else "llm failed")
