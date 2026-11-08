"""Keep optional API capability checks separate from genuine smoke failures."""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from ai_yori_murasaki.adapters.base import AdapterUnavailable


@pytest.fixture
def smoke(monkeypatch):
    path = Path(__file__).resolve().parents[1] / "scripts" / "wazuh_live_smoke.py"
    spec = importlib.util.spec_from_file_location("wazuh_smoke_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    class Connection:
        def close(self):
            pass

    monkeypatch.setattr(module.socket, "create_connection", lambda *a, **k: Connection())
    monkeypatch.delenv("WAZUH_USER", raising=False)
    monkeypatch.delenv("WAZUH_PASS", raising=False)
    return module


@pytest.mark.parametrize("status,expected_exit,expected_status", [
    (404, 0, "skipped"),
    (401, 1, "fail"),
    (500, 1, "fail"),
])
def test_smoke_reports_missing_route_without_masking_http_failures(
        smoke, monkeypatch, capsys, status, expected_exit, expected_status):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def authenticate(self, **kwargs):
            return {"token": "smoke-test-placeholder"}

        def query_alerts(self, **kwargs):
            raise AdapterUnavailable(f"wazuh alerts query failed: http {status}")

    monkeypatch.setattr(smoke, "WazuhClient", Client)
    assert smoke.main(["--allow-default-creds"]) == expected_exit
    output = capsys.readouterr().out
    assert f"query_alerts: {expected_status}" in output
    assert "smoke-test-placeholder" not in output


def test_missing_alert_endpoint_does_not_hide_invalid_alert_file(
        smoke, monkeypatch, capsys, tmp_path):
    class Client:
        def __init__(self, *args, **kwargs):
            pass

        def authenticate(self, **kwargs):
            return {"token": "smoke-test-placeholder"}

        def query_alerts(self, **kwargs):
            raise AdapterUnavailable("wazuh alerts query failed: http 404")

    monkeypatch.setattr(smoke, "WazuhClient", Client)
    alerts = tmp_path / "alerts.json"
    alerts.write_text("not json\n", encoding="utf-8")
    assert smoke.main(["--allow-default-creds", "--alerts-file", str(alerts)]) == 1
    output = capsys.readouterr().out
    assert "query_alerts: skipped" in output
    assert "alerts_json_import: fail" in output
