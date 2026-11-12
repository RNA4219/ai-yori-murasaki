#!/usr/bin/env python3
"""Finite local lab target: path traversal via unsanitized filename.

Educational-level: the /files endpoint reads a file by name without
validating the path, allowing ../ to access files outside the intended
directory.

GET /health            -> 200 {"ok": true}
GET /public            -> 200 {"public": "lab-public"}
GET /files?name=<input> -> 200 with file content (normal: readme.txt)
   name=../../secret.txt -> 200 with secret file content (traversal)

No secrets, no credentials, no external network access, no file writes.
"""
from __future__ import annotations

import json
import os
import sys
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

FILES_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "files")
SECRET_FILE = "/etc/hostname"  # traversal target (exists in container)

HOST = "0.0.0.0"
PORT = 8080


def read_file(name: str) -> str | None:
    """Vulnerable: no path validation, allows traversal."""
    # The container mounts /app read-only; keep the benign fixture in memory.
    if name == "readme.txt":
        return "This is a public readme file.\n"
    try:
        path = os.path.join(FILES_DIR, name)
        with open(path, "r") as fh:
            return fh.read()
    except Exception:
        # Traversal may reach outside FILES_DIR — this is the vulnerability
        try:
            with open(name, "r") as fh:
                return fh.read()
        except Exception:
            return None


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

        if path == "/files":
            name = query.get("name", [""])[0]
            content = read_file(name)
            if content is not None:
                has_traversal = ".." in name
                self._send_json(200, {"content": content[:1024],
                                     "traversal_detected": has_traversal})
                _emit_telemetry("GET", self.path, 200, name[:32])
            else:
                self._send_json(404, {"error": "file_not_found"})
                _emit_telemetry("GET", self.path, 404, name[:32])
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
