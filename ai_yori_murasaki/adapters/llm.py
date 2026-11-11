"""LLM adapter: Ollama native + OpenAI compatible via HttpBroker.

- stream=false always
- response cap / timeout / retry budget enforced
- cache keyed by role/campaign/run/model/prompt_digest
- tool_calls are NOT executed
- metadata measured from request/response bodies
"""
from __future__ import annotations

import copy
import os
import json
from urllib.parse import urlsplit
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


_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _validate_configured_url(raw: str) -> str:
    """Require an unambiguous OpenAI-compatible URL; remote HTTP is denied."""
    if (not isinstance(raw, str) or not raw or raw != raw.strip()
            or any(ord(ch) < 33 or ord(ch) == 127 for ch in raw)):
        raise jsonio.DataError("AYM_LLM_URL contains whitespace/control characters")
    try:
        parts = urlsplit(raw)
        host = parts.hostname
        port = parts.port
    except ValueError as exc:
        raise jsonio.DataError("AYM_LLM_URL is malformed") from exc
    if parts.scheme not in ("https", "http"):
        raise jsonio.DataError("AYM_LLM_URL scheme must be https or http")
    if not parts.netloc or not host:
        raise jsonio.DataError("AYM_LLM_URL must have a host")
    if parts.username is not None or parts.password is not None or "@" in parts.netloc:
        raise jsonio.DataError("AYM_LLM_URL must not contain credentials")
    if parts.query or parts.fragment:
        raise jsonio.DataError("AYM_LLM_URL must not contain query or fragment")
    if not parts.path.endswith("/chat/completions"):
        raise jsonio.DataError("AYM_LLM_URL must end in /chat/completions")
    if parts.scheme == "http" and host.lower() not in _LOOPBACK_HOSTS:
        raise jsonio.DataError("AYM_LLM_URL remote HTTP is not permitted")
    # Touch .port above to reject invalid ports, including out-of-range values.
    return raw


def _validate_and_normalize_key(raw: str) -> str:
    """Normalize AYM_LLM_API_KEY to a Bearer-prefixed header value."""
    if not raw:
        raise jsonio.DataError("AYM_LLM_API_KEY is empty")
    if "\r" in raw or "\n" in raw:
        raise jsonio.DataError("AYM_LLM_API_KEY contains CR/LF")
    if raw.lower().startswith("bearer "):
        token = raw[len("bearer "):].strip()
    elif raw.lower() == "bearer":
        raise jsonio.DataError("AYM_LLM_API_KEY bearer value is empty")
    else:
        token = raw
    if not token or any(not 33 <= ord(ch) <= 126 for ch in token):
        raise jsonio.DataError("AYM_LLM_API_KEY contains invalid characters")
    return "Bearer " + token


def _read_env_str(name: str) -> str:
    val = os.environ.get(name, "")
    return val.strip() if isinstance(val, str) else ""


def _read_env_raw(name: str) -> str:
    """Read env value without stripping; caller must validate CR/LF."""
    val = os.environ.get(name, "")
    return val if isinstance(val, str) else ""


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
        # OpenAI-compatible connection settings captured at construction.
        # When configured, chat() overrides transport regardless of caller
        # defaults, enabling seamless DGX/remote endpoints without touching
        # existing Ollama call sites.
        _url_raw = _read_env_raw("AYM_LLM_URL")
        _key_raw = _read_env_raw("AYM_LLM_API_KEY")
        if _key_raw and ("\r" in _key_raw or "\n" in _key_raw):
            raise jsonio.DataError("AYM_LLM_API_KEY must not contain CR or LF")
        _key_raw = _key_raw.strip() if _key_raw else _key_raw
        if _key_raw and not _url_raw:
            raise jsonio.DataError(
                "AYM_LLM_API_KEY requires AYM_LLM_URL to be set")
        self._configured_url: str | None = None
        self._auth_header: str | None = None
        if _url_raw:
            self._configured_url = _validate_configured_url(_url_raw)
        if _key_raw and self._configured_url:
            self._auth_header = _validate_and_normalize_key(_key_raw)
        # Transport generation budget: the max_tokens for OpenAI-compatible
        # transport includes reasoning tokens for models that emit them.
        # The agent bundle token_cap (128-8192) is a native/Ollama concept
        # and remains unchanged for existing native execution paths.
        # When configured URL is present and this override is set, it
        # replaces the caller-supplied token_cap in the OpenAI body.
        self._configured_max_tokens: int | None = None
        _mt = _read_env_str("AYM_LLM_MAX_TOKENS")
        if _mt and self._configured_url:
            try:
                _mt_val = int(_mt)
            except ValueError:
                raise jsonio.DataError(
                    "AYM_LLM_MAX_TOKENS must be a valid integer")
            _check_budget("AYM_LLM_MAX_TOKENS", _mt_val, 1, 131072)
            self._configured_max_tokens = _mt_val
        self._configured_timeout_ms: int | None = None
        _tm = _read_env_str("AYM_LLM_TIMEOUT_MS")
        if _tm and self._configured_url:
            try:
                _tm_val = int(_tm)
            except ValueError:
                raise jsonio.DataError(
                    "AYM_LLM_TIMEOUT_MS must be a valid integer")
            self._configured_timeout_ms = _check_budget(
                "AYM_LLM_TIMEOUT_MS", _tm_val, 1, 300000)
        self._reasoning_effort: str | None = None
        _re = _read_env_str("AYM_LLM_REASONING_EFFORT")
        if _re and self._configured_url:
            if _re not in ("low", "medium", "high"):
                raise jsonio.DataError(
                    "AYM_LLM_REASONING_EFFORT must be low, medium or high")
            self._reasoning_effort = _re

    def __repr__(self) -> str:
        return (f"LLMClient(timeout_ms={self._timeout_ms}, "
                f"retry_cap={self._retry_cap}, "
                f"configured_url={'set' if self._configured_url else 'none'})")

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
        # When an OpenAI-compatible endpoint is configured via AYM_LLM_URL,
        # override transport unconditionally so existing native=True callers
        # are transparently redirected. The transport generation budget
        # (max_tokens) includes reasoning tokens; the agent bundle token_cap
        # is unchanged for existing native execution.
        effective_timeout_ms = self._timeout_ms
        if self._configured_url:
            url = self._configured_url
            native = False
            if self._configured_max_tokens is not None:
                token_cap = self._configured_max_tokens
            if self._configured_timeout_ms is not None:
                effective_timeout_ms = self._configured_timeout_ms

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
            # reasoning_effort is passed ONLY in OpenAI-compatible body and
            # is included in the request digest via the body dict.
            if self._configured_url and self._reasoning_effort:
                body["reasoning_effort"] = self._reasoning_effort

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
        headers: dict[str, str] = {"Content-Type": "application/json"}
        if self._auth_header and self._configured_url:
            headers["Authorization"] = self._auth_header
        last_err: Exception | None = None
        for _ in range(self._retry_cap + 1):
            try:
                resp = self._broker.request(
                    "POST", url, body=raw_body,
                    headers=headers,
                    timeout=effective_timeout_ms / 1000.0,
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
                record["timeout_ms"] = effective_timeout_ms
                record["request_hash"] = request_digest
                self._cache[key] = record
                return copy.deepcopy(record)
            except base.AdapterUnavailable as exc:
                last_err = exc
                continue
        raise base.AdapterUnavailable(str(last_err) if last_err else "llm failed")
