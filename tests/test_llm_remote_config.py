"""Offline regression checks for a configurable, authenticated LLM endpoint."""
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from ai_yori_murasaki.adapters.base import HttpBroker
from ai_yori_murasaki.adapters.llm import (
    LLMClient,
    _validate_and_normalize_key,
    _validate_configured_url,
)
from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.p2 import route_model


class RecordingBroker(HttpBroker):
    def __init__(self):
        super().__init__({"broker_allowlist": []})
        self.calls = []

    def request(self, method, url, body=None, headers=None, timeout=10.0,
                max_bytes=1048576):
        request = json.loads(body)
        self.calls.append((method, url, request, dict(headers or {}), timeout))
        if url.endswith("/api/chat"):
            result = {"model": request["model"], "message": {
                "role": "assistant", "content": "ok"}, "done": True}
        else:
            result = {"model": request["model"], "object": "chat.completion",
                      "choices": [{"index": 0, "finish_reason": "stop",
                                   "message": {"role": "assistant", "content": "ok"}}]}
        return {"status": 200, "headers": {}, "body": json.dumps(result).encode()}


@pytest.fixture(autouse=True)
def clear_remote_config(monkeypatch):
    for name in ("AYM_LLM_URL", "AYM_LLM_API_KEY", "AYM_LLM_MODEL",
                 "AYM_LLM_MAX_TOKENS", "AYM_LLM_TIMEOUT_MS",
                 "AYM_LLM_REASONING_EFFORT", "AYM_SECRETS"):
        monkeypatch.delenv(name, raising=False)


def _call(client, model):
    return client.chat(url="http://localhost:11434/api/chat", native=True,
                       role="purple", campaign="offline", run="r1", model=model,
                       messages=[{"role": "user", "content": "ping"}],
                       temperature=0.0, token_cap=128, seed=1)


def test_remote_config_redirects_ollama_to_authenticated_openai(monkeypatch):
    monkeypatch.setenv("AYM_LLM_URL", "https://llm.example.test/v1/chat/completions")
    monkeypatch.setenv("AYM_LLM_MODEL", "test-model")
    monkeypatch.setenv("AYM_LLM_API_KEY", "secret-example")
    monkeypatch.setenv("AYM_LLM_MAX_TOKENS", "8192")
    monkeypatch.setenv("AYM_LLM_TIMEOUT_MS", "180000")
    monkeypatch.setenv("AYM_LLM_REASONING_EFFORT", "low")
    broker = RecordingBroker()
    result = _call(LLMClient(broker, retry_cap=0), route_model("purple"))
    method, url, body, headers, timeout = broker.calls[0]
    assert method == "POST"
    assert url == "https://llm.example.test/v1/chat/completions"
    assert headers["Authorization"] == "Bearer secret-example"
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 8192
    assert body["reasoning_effort"] == "low"
    assert "options" not in body and "think" not in body
    assert timeout == 180.0 and result["timeout_ms"] == 180000
    assert "secret-example" not in repr(result)


def test_unconfigured_client_keeps_ollama_transport():
    broker = RecordingBroker()
    _call(LLMClient(broker, retry_cap=0), route_model("purple"))
    _, url, body, headers, _ = broker.calls[0]
    assert url == "http://localhost:11434/api/chat"
    assert "options" in body and "max_tokens" not in body
    assert "Authorization" not in headers


@pytest.mark.parametrize("url", [
    "http://remote.example.test/v1/chat/completions",
    "https://user@llm.example.test/v1/chat/completions",
    "https://llm.example.test:99999/v1/chat/completions",
    "https://llm.example.test/path/other",
    "https://llm.example.test/v1/chat/completions?token=dont-put-key-here",
    "https://llm.example.test/v1/chat/completions\n",
    "https://[invalid/v1/chat/completions",
])
def test_invalid_endpoint_rejected(url):
    with pytest.raises(DataError):
        _validate_configured_url(url)


@pytest.mark.parametrize("token", ["", "Bearer", "Bearer ", "bad\nkey", "bad\tkey"])
def test_bad_auth_token_rejected(token):
    with pytest.raises(DataError):
        _validate_and_normalize_key(token)


def test_key_without_endpoint_rejected(monkeypatch):
    monkeypatch.setenv("AYM_LLM_API_KEY", "not-a-real-key")
    with pytest.raises(DataError):
        LLMClient(RecordingBroker())


def test_explicit_role_route_beats_global_fallback(monkeypatch):
    monkeypatch.setenv("AYM_LLM_MODEL", "default-model")
    assert route_model("red") == "default-model"
    assert route_model("red", {"red": "override-model"}) == "override-model"


def test_launcher_rejects_broad_existing_policy(tmp_path, monkeypatch):
    import importlib.util
    launcher = Path(__file__).resolve().parents[1] / "tools" / "aym_dgx.py"
    spec = importlib.util.spec_from_file_location("aym_launcher_test", launcher)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    policy = tmp_path / "policy.json"
    policy.write_text(json.dumps({"broker_allowlist": [{"host": "*"}]}))
    policy.chmod(0o600)
    monkeypatch.setattr(module, "_resolve_ip", lambda host, port: "192.0.2.20")
    with pytest.raises(ValueError):
        module._ensure_policy(policy, "https://llm.example.test/v1/chat/completions")
