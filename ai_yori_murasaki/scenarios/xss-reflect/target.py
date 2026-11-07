#!/usr/bin/env python3
"""Finite local lab target: XSS via unescaped reflected input.

Educational-level: the /greet endpoint reflects the name parameter
without HTML escaping, allowing script injection.

GET /health            -> 200 {"ok": true}
GET /public            -> 200 {"public": "lab-public"}
GET /greet?name=<input> -> 200 {"greeting": "Hello, <input>!"}
   name=<script>alert(1)</script> -> reflected raw (XSS)

No secrets, no credentials, no external network access, no file writes.
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

HOST = "0.0.0.0"
PORT = 8080


def greet(name: str) -> str:
    """Vulnerable: reflects input without HTML escaping."""
    return f"Hello, {name}!"


def _emit_telemetry(method, path, status, role=""):
    entry = {"method": method, "path": path, "status": int(status),
             "role": role, "timestamp": time.time()}
    try:
        sys.stdout.write(json.dumps(entry, separators=(",", ":")) + "\n")
        sys.stdout.flush()
    except Exception:
        pass


class Handler(BaseHTTPRequestHandler):
    server_version = "aym-lab-target/1"
    sys_version = ""

    def log_message(self, fmt, *args): pass

    def _send_json(self, status, payload):
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try: self.wfile.write(body)
        except Exception: pass

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query, keep_blank_values=True)

        if path == "/health":
            self._send_json(200, {"ok": True})
            _emit_telemetry("GET", path, 200)
            return

        if path == "/public":
            self._send_json(200, {"public": "lab-public"})
            _emit_telemetry("GET", path, 200)
            return

        if path == "/greet":
            name = query.get("name", [""])[0]
            greeting = greet(name)
            has_script = "<script>" in name.lower()
            self._send_json(200, {"greeting": greeting,
                                   "xss_reflected": has_script})
            _emit_telemetry("GET", self.path, 200, name[:32])
            return

        self._send_json(404, {"error": "not_found"})
        _emit_telemetry("GET", path, 404)


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.daemon_threads = True
    try: server.serve_forever()
    except KeyboardInterrupt: pass
    finally: server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
