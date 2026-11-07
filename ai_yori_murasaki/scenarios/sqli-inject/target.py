#!/usr/bin/env python3
"""Finite local lab target: SQL injection via unsanitized category input.

Educational-level simulation: the /api/items endpoint builds a "query" by
string concatenation. Injecting SQL metacharacters bypasses the intended
filter and returns all items including protected ones.

GET /health            -> 200 {"ok": true}
GET /public            -> 200 {"public": "lab-public"}
GET /api/items?category=<input> -> 200 with filtered items (normal)
   category=' OR '1'='1 -> 200 with ALL items including protected (attack)

No secrets, no credentials, no external network access, no file writes.
"""
from __future__ import annotations

import json
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

ITEMS = {
    "food": ["apple", "bread"],
    "tools": ["hammer"],
    "protected": ["admin_password", "secret_key"],
}

HOST = "0.0.0.0"
PORT = 8080


def query_items(category: str) -> list:
    """Simulated query builder. Vulnerable to SQL injection.

    If input contains ' OR ' it bypasses the WHERE clause and returns all.
    """
    if "'" in category or " OR " in category.upper():
        return [item for items in ITEMS.values() for item in items]
    return ITEMS.get(category, [])


def _emit_telemetry(method: str, path: str, status: int, role: str = "") -> None:
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

        if path == "/api/items":
            category = query.get("category", [""])[0]
            results = query_items(category)
            leaked_protected = any(
                item in ("admin_password", "secret_key") for item in results)
            status = 200 if results else 404
            self._send_json(status, {"items": results,
                                     "leaked_protected": leaked_protected})
            _emit_telemetry("GET", self.path, status, category[:32])
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
