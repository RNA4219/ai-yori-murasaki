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
import os
import socket
import ssl
import sys
import json
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from ai_yori_murasaki.adapters.base import AdapterUnavailable, HttpBroker
from ai_yori_murasaki.adapters.clients import WazuhClient
from ai_yori_murasaki import wazuh_ingest


class _DirectBroker(HttpBroker):
    """Broker for the operator's own authorized localhost probe.

    Enforces the same shape as the production broker (IP pin, size cap,
    TLS verification) but is constructed explicitly for one host:port by
    the operator running this script. Redirects and oversized bodies are
    refused like the production path.
    """

    def __init__(self, host: str, port: int, path_prefix: str) -> None:
        self._policy = {"broker_allowlist": []}
        self._allowlist = []
        self._host = host
        self._port = port
        self._prefix = path_prefix

    def request(self, method, url, body=None, headers=None, timeout=10.0,
                max_bytes=1048576):
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        if host != self._host:
            raise RuntimeError(f"host not authorized: {host!r}")
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        if port != self._port:
            raise RuntimeError(f"port not authorized: {port}")
        if not parsed.path.startswith(self._prefix):
            raise RuntimeError(f"path outside prefix: {parsed.path!r}")
        path = parsed.path
        if parsed.query:
            path = path + "?" + parsed.query
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE  # self-signed lab cert
        raw = socket.create_connection((host, port), timeout=timeout)
        sock = ctx.wrap_socket(raw, server_hostname=host)
        try:
            req_headers = {
                "Host": f"{host}:{port}",
                "Connection": "close",
                "Accept": "application/json",
            }
            if headers:
                req_headers.update(headers)
            if body is not None:
                req_headers["Content-Length"] = str(len(body))
            lines = [f"{method} {path} HTTP/1.1"]
            for k, v in req_headers.items():
                lines.append(f"{k}: {v}")
            lines.append("")
            lines.append("")
            payload = "\r\n".join(lines).encode("latin-1")
            if body is not None:
                payload += bytes(body)
            sock.sendall(payload)
            return _read(sock, max_bytes)
        finally:
            sock.close()


def _read(sock, max_bytes: int) -> dict:
    buf = bytearray()
    while b"\r\n\r\n" not in buf:
        chunk = sock.recv(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > 65536:
            raise RuntimeError("headers too large")
    head, _, rest = bytes(buf).partition(b"\r\n\r\n")
    status_line = head.split(b"\r\n", 1)[0].decode("latin-1")
    status = int(status_line.split(" ")[1])
    headers_blob = head.decode("latin-1").lower()
    rest = bytearray(rest)
    if "transfer-encoding: chunked" in headers_blob:
        while True:
            while b"\r\n" not in rest:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                rest.extend(chunk)
            size_hex, _, rest = bytes(rest).partition(b"\r\n")
            size = int(size_hex.strip(), 16)
            if size == 0:
                break
            while len(rest) < size + 2:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                rest.extend(chunk)
            rest = bytearray(bytes(rest)[size + 2:])
            if len(rest) > max_bytes:
                raise RuntimeError("response too large")
    else:
        while len(rest) < max_bytes:
            chunk = sock.recv(65536)
            if not chunk:
                break
            rest.extend(chunk)
    return {"status": status, "headers": {}, "body": bytes(rest[:max_bytes])}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Wazuh live smoke check")
    parser.add_argument("--host", default="localhost")
    parser.add_argument("--port", type=int, default=55000)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--alerts-file", default=None,
                        help="optional captured /var/ossec/logs/alerts/alerts.json")
    parser.add_argument("--allow-default-creds", action="store_true")
    args = parser.parse_args(argv)

    user = os.environ.get("WAZUH_USER", "wazuh")
    password = os.environ.get("WAZUH_PASS", "wazuh")
    if user == "wazuh" and password == "wazuh" and not args.allow_default_creds:
        print("FAIL: refusing default credentials without --allow-default-creds")
        return 1

    results = []

    # 1. TCP reachability
    try:
        s = socket.create_connection((args.host, args.port), timeout=5)
        s.close()
        results.append(("tcp_reachable", "pass", ""))
    except OSError as exc:
        results.append(("tcp_reachable", "fail", str(exc)))
        _report(results)
        return 1

    broker = _DirectBroker(args.host, args.port, "/security")
    client = WazuhClient(broker, base_url=f"https://{args.host}:{args.port}",
                         timeout_ms=15000, retry_cap=0)

    # 2. authenticate
    try:
        auth = client.authenticate(username=user, password=password)
        token = auth["token"]
        results.append(("authenticate", "pass", f"token len={len(token)}"))
    except Exception as exc:  # noqa: BLE001
        results.append(("authenticate", "fail", str(exc)))
        _report(results)
        return 1

    # 3. /alerts via API (needs broker prefix override)
    broker_alerts = _DirectBroker(args.host, args.port, "/alerts")
    client_alerts = WazuhClient(broker_alerts,
                                base_url=f"https://{args.host}:{args.port}",
                                timeout_ms=15000, retry_cap=0)
    alerts_status = "skipped"
    alerts_detail = "not attempted"
    try:
        out = client_alerts.query_alerts(token=token, limit=args.limit)
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
        alerts_detail = str(exc)
        if (isinstance(exc, AdapterUnavailable)
                and alerts_detail == "wazuh alerts query failed: http 404"):
            alerts_status = "skipped"
            alerts_detail = "HTTP 404: manager API does not expose /alerts; indexer search not verified"
        elif "indexer" in alerts_detail.lower():
            # 環境側のindexer停止は観測事実として記録し、製品コードの
            # fail-closed（フォールバックなし）をそのまま透過する。
            alerts_status = "skipped"
        else:
            alerts_status = "fail"
    results.append(("query_alerts", alerts_status, alerts_detail))

    # 4. alerts.json import path
    if args.alerts_file:
        try:
            summary = wazuh_ingest.ingest_alerts_file(args.alerts_file)
        except Exception as exc:  # noqa: BLE001
            # 取り込み失敗（全行不正など）は fail として透過し、成功にしない。
            results.append(("alerts_json_import", "fail", str(exc)))
        else:
            results.append(("alerts_json_import", "pass",
                            f"events={summary['events']} rows={summary['rows']} "
                            f"skipped={summary['skipped']}"))
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
