"""Small test for the new Wazuh live connection pieces.

- WazuhClient.authenticate/query_alerts/health against a fake broker
- wazuh_ingest alerts.json -> telemetry rows (sanitized)
"""
from __future__ import annotations

import json
import base64

import pytest

from ai_yori_murasaki.adapters import base as adapter_base
from ai_yori_murasaki.adapters.base import AdapterUnavailable
from ai_yori_murasaki.adapters.clients import WazuhClient
from ai_yori_murasaki import wazuh_ingest


class _FakeBroker(adapter_base.HttpBroker):
    def __init__(self) -> None:
        self._policy = {"broker_allowlist": []}
        self._allowlist = []
        self.calls = []  # type: ignore
        self._responses = []

    def queue(self, *, status: int = 200, body: bytes = b"{}", headers: dict | None = None) -> None:
        self._responses.append({"status": status, "headers": headers or {}, "body": body})

    def queue_error(self, exc: Exception, *, times: int = 1) -> None:
        for _ in range(times):
            self._responses.append(exc)

    def request(self, method, url, body=None, headers=None, timeout=10.0, max_bytes=1048576):
        self.calls.append((method, url, body, headers))
        if not self._responses:
            raise AssertionError("no queued response")
        item = self._responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


def _wazuh_alert_line(rule_id: str = "5710", level: int = 3, agent_id: str = "001") -> str:
    doc = {
        "timestamp": "2026-11-08T01:00:00.000+0000",
        "rule": {"level": level, "id": rule_id, "description": "test"},
        "agent": {"id": agent_id, "name": "agent-1"},
        "data": {"srcip": "10.0.0.9"},
    }
    return json.dumps(doc, separators=(",", ":"))


def test_wazuh_client_authenticate_and_query_alerts() -> None:
    broker = _FakeBroker()
    token = "jwt-token-abc"
    broker.queue(status=200, body=json.dumps({"data": {"token": token}}).encode("utf-8"))
    alerts_body = json.dumps({"data": {"affected_items": [{"rule": {"id": "5710"}}]}}).encode("utf-8")
    broker.queue(status=200, body=alerts_body)

    client = WazuhClient(broker, base_url="https://localhost:55000")
    auth = client.authenticate(username="wazuh", password="wazuh")
    assert auth == {"token": token}

    out = client.query_alerts(token=token, limit=3)
    assert out["data"]["affected_items"][0]["rule"]["id"] == "5710"

    # Validate auth request shape
    m, url, body, headers = broker.calls[0]
    assert m == "POST"
    assert url.endswith("/security/user/authenticate")
    expected = "Basic " + base64.b64encode(b"wazuh:wazuh").decode()
    assert headers is not None
    assert headers.get("Authorization") == expected
    assert body == b""

    # Validate alerts request shape
    m, url, body, headers = broker.calls[1]
    assert m == "GET"
    assert "/alerts?limit=3&pretty=true" in url
    assert headers is not None
    assert headers.get("Authorization") == f"Bearer {token}"


def test_wazuh_health_ok_on_401() -> None:
    broker = _FakeBroker()
    broker.queue(status=401, body=b"{}")
    client = WazuhClient(broker, base_url="https://localhost:55000")
    assert client.health() == {"status": "ok", "http_status": 401}


def test_wazuh_request_retries_then_fails_closed() -> None:
    broker = _FakeBroker()
    broker.queue_error(AdapterUnavailable("connection refused"), times=3)
    client = WazuhClient(broker, base_url="https://localhost:55000", retry_cap=2)
    with pytest.raises(AdapterUnavailable):
        client.health()
    assert len(broker.calls) == 3


def test_wazuh_ingest_converts_alerts_to_sanitized_rows(tmp_path) -> None:
    alerts = tmp_path / "alerts.json"
    alerts.write_text("\n".join([_wazuh_alert_line(), _wazuh_alert_line(rule_id="5715", level=10)]) + "\n", encoding="utf-8")
    out = tmp_path / "telemetry.jsonl"

    summary = wazuh_ingest.ingest_alerts_file(alerts, out_jsonl=out)
    assert summary["ok"] is True
    assert summary["events"] == 2
    assert summary["rows"] == 2
    assert summary["skipped"] == 0

    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    row = json.loads(lines[0])
    assert set(row.keys()) <= {"timestamp", "method", "path", "status", "role"}
    assert row["method"] == "GET"
    assert row["path"].startswith("/wazuh/alert/")
    assert row["role"].startswith("agent:")


def test_wazuh_ingest_skips_unparseable_lines(tmp_path) -> None:
    alerts = tmp_path / "alerts.json"
    alerts.write_text(_wazuh_alert_line() + "\nnot json\n" + _wazuh_alert_line(rule_id="9999") + "\n", encoding="utf-8")

    summary = wazuh_ingest.ingest_alerts_file(alerts)
    assert summary["events"] == 2
    assert summary["rows"] == 2
    assert summary["skipped"] == 1


def test_wazuh_ingest_all_lines_unparseable_fails_closed(tmp_path) -> None:
    alerts = tmp_path / "alerts.json"
    alerts.write_text("not json\nalso not json\n", encoding="utf-8")

    with pytest.raises(ValueError):
        wazuh_ingest.ingest_alerts_file(alerts)


def test_rows_from_jsonl_validates_shape(tmp_path) -> None:
    good = tmp_path / "good.jsonl"
    good.write_text(json.dumps({"timestamp": "t", "method": "GET",
                                "path": "/p", "status": 200, "role": "r"},
                               separators=(",", ":")) + "\n", encoding="utf-8")
    rows = wazuh_ingest.rows_from_jsonl(good)
    assert len(rows) == 1

    bad = tmp_path / "bad.jsonl"
    bad.write_text(json.dumps({"path": "/p", "command": "rm -rf /"},
                              separators=(",", ":")) + "\n", encoding="utf-8")
    with pytest.raises(ValueError):
        wazuh_ingest.rows_from_jsonl(bad)

    with pytest.raises(FileNotFoundError):
        wazuh_ingest.rows_from_jsonl(tmp_path / "missing.jsonl")
