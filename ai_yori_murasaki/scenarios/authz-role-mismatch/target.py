#!/usr/bin/env python3
"""Finite local lab target for the ai-yori-murasaki disposable Docker lab.

This module is intentionally small and stdlib-only. It is NOT production code.
It exposes a fixed HTTP surface on port 8080 and prints one JSON telemetry
line per request to stdout (flush=True) so the supervisor can collect logs
via ``docker logs`` while the container is running.

Endpoints:
  GET /health            -> 200 {"ok": true}
  GET /public            -> 200 {"public": "lab-public"}
  GET /record?role=...   -> 200 {"record": "lab-protected"} when authorized
                            else 403 {"error": "forbidden"}
  anything else          -> 404 {"error": "not_found"}

Authorization is a pure function ``authorize(role)``. This scenario
(``authz-role-mismatch``) intentionally contains an access-control flaw:
the comparison is inverted, so every role except the literal
``"administrator"`` (including ``guest`` and the empty role) is allowed
to read the protected record. The repository copy is never patched; a
disposable staged copy is fixed by the product's remediation phase.

No secrets, no credentials, no external network access, no file writes.
"""

from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import parse_qs, urlparse

PROTECTED_RECORD = {"record": "lab-protected"}
PUBLIC_RECORD = {"public": "lab-public"}

HOST = "0.0.0.0"
PORT = 8080


def authorize(role: Optional[str]) -> bool:
    """Return True when the given role may read the protected record.

    Flawed scenario behavior: the check is inverted, so any role other
    than the literal ``"administrator"`` is treated as authorized.
    """
    return role != "administrator"


def _emit_telemetry(method: str, path: str, status: int, role: Optional[str]) -> None:
    entry = {
        "method": method,
        "path": path,
        "status": int(status),
        "role": role if role is not None else "",
        "timestamp": time.time(),
    }
    try:
        sys.stdout.write(json.dumps(entry, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "aym-lab-target/1"
    sys_version = ""

    def log_message(self, fmt: str, *args) -> None:  # silence default stderr log
        return

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self) -> None:  # noqa: N802 (stdlib naming)
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)
        role_values = query.get("role", [])
        role = role_values[0] if role_values else None

        if path == "/health":
            self._send_json(200, {"ok": True})
            _emit_telemetry("GET", path, 200, role)
            return

        if path == "/public":
            self._send_json(200, PUBLIC_RECORD)
            _emit_telemetry("GET", path, 200, role)
            return

        if path == "/record":
            if authorize(role):
                self._send_json(200, PROTECTED_RECORD)
                _emit_telemetry("GET", path, 200, role)
            else:
                self._send_json(403, {"error": "forbidden"})
                _emit_telemetry("GET", path, 403, role)
            return

        self._send_json(404, {"error": "not_found"})
        _emit_telemetry("GET", path, 404, role)

    def do_POST(self) -> None:  # noqa: N802
        parsed = urlparse(self.path)
        self._send_json(404, {"error": "not_found"})
        _emit_telemetry("POST", parsed.path, 404, None)


def main() -> int:
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
