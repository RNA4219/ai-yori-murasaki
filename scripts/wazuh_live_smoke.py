"""Live Wazuh smoke check.

This script is a read-only probe for an authorized local Wazuh manager.
It verifies the chain the product path uses:

  1. TCP reachability of the API port
  2. authenticate() -> JWT via POST /security/user/authenticate
  3. query_alerts() via GET /alerts (the standard manager API has no such
     route; HTTP 404 is reported as an unsupported capability, not success)
  4. alerts.json import path via WazuhClient.import_alert on a real line
     captured with `docker exec <container> cat /var/ossec/logs/alerts/alerts.json`
     (pass the file with --alerts-file; live docker access stays outside
     this script)

No credentials are embedded: username/password come from environment
variables WAZUH_USER / WAZUH_PASS (default wazuh:wazuh is refused unless
--allow-default-creds is given).

Exit codes:
  0  all attempted checks passed (unavailable steps are reported as skipped
     with a reason, never as a false pass)
  1  a check that ran failed
"""
from __future__ import annotations

import argparse
from http.client import HTTPException, HTTPSConnection
import os
from pathlib import Path
import re
import socket
import ssl
import sys
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_yori_murasaki.adapters.base import AdapterUnavailable, HttpBroker
from ai_yori_murasaki.adapters.clients import WazuhClient
from ai_yori_murasaki import wazuh_ingest


class _DirectBroker(HttpBroker):
    """Send only the two smoke requests to the operator-selected HTTPS endpoint."""

    def __init__(self, host: str, port: int, context: ssl.SSLContext) -> None:
        self._host = host
        self._port = port
        self._context = context

    def request(self, method, url, body=None, headers=None, timeout=10.0,
                max_bytes=1048576):
        parsed = urllib.parse.urlsplit(url)
        if (parsed.scheme != "https" or parsed.hostname != self._host.lower()
                or (parsed.port or 443) != self._port):
            raise ValueError("Wazuh smoke request outside the configured HTTPS endpoint")
        if (method, parsed.path) not in {("POST", "/security/user/authenticate"), ("GET", "/alerts")}:
            raise ValueError("unexpected Wazuh smoke request")
        path = parsed.path + ("?" + parsed.query if parsed.query else "")
        connection = HTTPSConnection(self._host, self._port, timeout=timeout, context=self._context)
        try:
            connection.request(method, path, body=body, headers={"Accept": "application/json", **(headers or {})})
            response = connection.getresponse()
            data = response.read(max_bytes + 1)
            if len(data) > max_bytes:
                raise ValueError("Wazuh response too large")
            return {"status": response.status, "headers": dict(response.getheaders()), "body": data}
        finally:
            connection.close()


def _failure_reason(exc: Exception) -> str:
    # ネットワーク例外には応答行や認証ヘッダーが含まれ得るため、固定の分類だけを返す。
    if isinstance(exc, ssl.SSLCertVerificationError):
        return "TLS certificate or hostname verification failed"
    if isinstance(exc, ssl.SSLError):
        return "TLS connection failed"
    if isinstance(exc, TimeoutError):
        return "connection timed out"
    if isinstance(exc, (OSError, HTTPException)):
        return "connection failed"
    if isinstance(exc, AdapterUnavailable):
        status = re.fullmatch(r"wazuh (?:auth|alerts query) failed: http ([1-5][0-9]{2})", str(exc))
        if status:
            return "HTTP " + status[1]
    return "request or response could not be processed"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wazuh live smoke check")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=55000)
    parser.add_argument("--ca-file", type=Path, help="PEM trust bundle for the Wazuh server (default: system trust)")
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--alerts-file", default=None,
                        help="optional captured /var/ossec/logs/alerts/alerts.json")
    parser.add_argument("--compat-rows", action="store_true",
                        help="also write legacy lab access-log compat rows "
                             "(pseudo HTTP fields; demo lab replay only)")
    parser.add_argument("--compat-out-jsonl", default=None,
                        help="output path for --compat-rows (required with it)")
    parser.add_argument("--allow-default-creds", action="store_true")
    args = parser.parse_args(argv)

    user = os.environ.get("WAZUH_USER", "wazuh")
    password = os.environ.get("WAZUH_PASS", "wazuh")
    if user == "wazuh" and password == "wazuh" and not args.allow_default_creds:
        print("FAIL: refusing default credentials without --allow-default-creds")
        return 1

    results = []
    try:
        context = ssl.create_default_context(cafile=args.ca_file)
    except (OSError, ValueError):
        _report([("tls_trust", "fail", "could not load CA trust bundle")])
        return 1

    # 1. TCP reachability
    try:
        s = socket.create_connection((args.host, args.port), timeout=5)
        s.close()
        results.append(("tcp_reachable", "pass", ""))
    except OSError as exc:
        results.append(("tcp_reachable", "fail", _failure_reason(exc)))
        _report(results)
        return 1

    broker = _DirectBroker(args.host, args.port, context)
    client = WazuhClient(broker, base_url=f"https://{args.host}:{args.port}",
                         timeout_ms=15000, retry_cap=0)

    # 2. authenticate
    try:
        auth = client.authenticate(username=user, password=password)
        token = auth["token"]
        results.append(("authenticate", "pass", ""))
    except Exception as exc:  # noqa: BLE001
        results.append(("authenticate", "fail", _failure_reason(exc)))
        _report(results)
        return 1

    # 3. /alerts via the same verified HTTPS broker
    alerts_status = "skipped"
    alerts_detail = "not attempted"
    try:
        out = client.query_alerts(token=token, limit=args.limit)
        items = out.get("data", {}).get("affected_items", [])
        failed = out.get("data", {}).get("failed_items")
        if items:
            alerts_status = "pass"
            alerts_detail = f"{len(items)} items"
        elif failed:
            alerts_status = "skipped"
            alerts_detail = "no indexer (failed_items present)"
        else:
            alerts_status = "pass"
            alerts_detail = "0 items"
    except Exception as exc:  # noqa: BLE001
        alerts_detail = _failure_reason(exc)
        if (isinstance(exc, AdapterUnavailable)
                and str(exc) == "wazuh alerts query failed: http 404"):
            alerts_status = "skipped"
            alerts_detail = "HTTP 404: manager API does not expose /alerts; indexer search not verified"
        elif isinstance(exc, AdapterUnavailable) and "indexer" in str(exc).lower():
            # 環境側のindexer停止は観測事実として記録し、製品コードの
            # fail-closed（フォールバックなし）をそのまま透過する。
            alerts_status = "skipped"
            alerts_detail = "indexer unavailable"
        else:
            alerts_status = "fail"
    results.append(("query_alerts", alerts_status, alerts_detail))

    # 4. alerts.json import path
    if args.alerts_file:
        try:
            # 既定は意味保持の正規化イベント取り込み（HTTP擬似フィールドを
            # 作らない）。lab access-log 互換行が必要な場合のみ --compat-rows。
            summary = wazuh_ingest.ingest_alerts_file(
                args.alerts_file,
                compat_rows=args.compat_rows,
                out_compat_jsonl=args.compat_out_jsonl if args.compat_rows else None)
        except Exception as exc:  # noqa: BLE001
            # 取り込み失敗（全行不正など）は fail として透過し、成功にしない。
            results.append(("alerts_json_import", "fail", str(exc)))
        else:
            results.append(("alerts_json_import", "pass",
                            f"events={summary['events']} rows={summary['rows']} "
                            f"skipped={summary['skipped']} "
                            f"compat_rows={summary['compat_rows']}"))
    else:
        results.append(("alerts_json_import", "skipped",
                        "no --alerts-file given"))

    _report(results)
    return 1 if any(status == "fail" for _, status, _ in results) else 0


def _report(results) -> None:
    for name, status, detail in results:
        line = f"{name}: {status}"
        if detail:
            line += f" ({detail})"
        print(line)


if __name__ == "__main__":
    raise SystemExit(main())
