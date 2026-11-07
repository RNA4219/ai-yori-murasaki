import json
import os
import sqlite3
import tempfile
import threading
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

REQUIRE_ADMIN = False
PARAMETERIZE_QUERY = False
CONSTRAIN_PATH = False
ESCAPE_HTML = False

MAX_QUERY = 1024
MAX_RESPONSE = 8192


def _temp_root():
    base = "/dev/shm" if os.path.isdir("/dev/shm") else tempfile.gettempdir()
    return Path(tempfile.mkdtemp(prefix="aym_lab_", dir=base))


class LabServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, addr, seed):
        super().__init__(addr, LabHandler)
        self.seed = int(seed)
        self.lock = threading.Lock()
        self.temp_root = _temp_root()
        self.public_dir = self.temp_root / "public"
        self.public_dir.mkdir()
        (self.public_dir / "hello.txt").write_text("hello-public\n", encoding="utf-8")
        (self.temp_root / "secret.txt").write_text(
            "SECRET-MARKER-%d\n" % self.seed, encoding="utf-8"
        )
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.execute("CREATE TABLE users(id INTEGER PRIMARY KEY, name TEXT)")
        self.db.execute("INSERT INTO users(id,name) VALUES(1,'alice')")
        self.db.execute("INSERT INTO users(id,name) VALUES(2,'bob')")
        self.db.execute(
            "INSERT INTO users(id,name) VALUES(?,?)", (1000 + self.seed, "seed%d" % self.seed)
        )
        self.db.commit()

    def server_close(self):
        try:
            super().server_close()
        finally:
            try:
                self.db.close()
            except Exception:
                pass
            try:
                for p in sorted(self.temp_root.rglob("*"), reverse=True):
                    if p.is_file():
                        p.unlink()
                    elif p.is_dir():
                        p.rmdir()
                self.temp_root.rmdir()
            except Exception:
                pass


class LabHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body.encode("utf-8") if isinstance(body, str) else body
        if len(data) > MAX_RESPONSE:
            data = data[:MAX_RESPONSE]
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        try:
            self.wfile.write(data)
        except Exception:
            pass

    def _json(self, code, obj):
        self._send(code, json.dumps(obj))

    def do_GET(self):
        try:
            self._route()
        except Exception:
            try:
                self._json(500, {"error": "internal"})
            except Exception:
                pass

    def _route(self):
        parsed = urlparse(self.path)
        path = parsed.path
        qs = parse_qs(parsed.query, keep_blank_values=True)
        if len(parsed.query) > MAX_QUERY:
            return self._json(414, {"error": "query-too-long"})
        if path == "/health":
            return self._json(200, {"status": "ok"})
        if path == "/public":
            return self._json(200, {"message": "public-ok"})
        if path == "/record":
            return self._record(qs)
        if path == "/search":
            return self._search(qs)
        if path == "/file":
            return self._file(qs)
        if path == "/echo":
            return self._echo(qs)
        return self._json(404, {"error": "not-found"})

    def _record(self, qs):
        role = (qs.get("role") or ["guest"])[0]
        marker = "record-marker-%d" % self.server.seed
        if REQUIRE_ADMIN and role != "admin":
            return self._json(403, {"error": "forbidden"})
        return self._json(200, {"role": role, "record": marker})

    def _search(self, qs):
        q = (qs.get("q") or [""])[0]
        db = self.server.db
        with self.server.lock:
            try:
                if PARAMETERIZE_QUERY:
                    cur = db.execute("SELECT name FROM users WHERE name LIKE ?", ("%" + q + "%",))
                else:
                    cur = db.execute("SELECT name FROM users WHERE name LIKE '%" + q + "%'")
                rows = [r[0] for r in cur.fetchall()]
            except Exception:
                return self._json(400, {"error": "bad-query"})
        return self._json(200, {"rows": rows})

    def _file(self, qs):
        name = (qs.get("name") or ["hello.txt"])[0]
        root = self.server.public_dir
        try:
            if CONSTRAIN_PATH:
                target = (root / name).resolve()
                if root.resolve() not in target.parents and target != root.resolve():
                    return self._json(403, {"error": "forbidden"})
                if not target.is_file():
                    return self._json(404, {"error": "not-found"})
                text = target.read_text(encoding="utf-8")
            else:
                target = (root / name).resolve()
                base = self.server.temp_root.resolve()
                if base not in target.parents and target != base:
                    return self._json(403, {"error": "forbidden"})
                if not target.is_file():
                    return self._json(404, {"error": "not-found"})
                text = target.read_text(encoding="utf-8")
        except Exception:
            return self._json(400, {"error": "read-failed"})
        return self._json(200, {"text": text})

    def _echo(self, qs):
        q = (qs.get("q") or [""])[0]
        if ESCAPE_HTML:
            q = escape(q)
        body = "<!doctype html><html><body><p>echo:</p><div id='out'>" + q + "</div></body></html>"
        return self._send(200, body, "text/html; charset=utf-8")


def make_server(port=0, seed=1):
    return LabServer(("127.0.0.1", int(port)), int(seed))


def main():
    import argparse

    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--seed", type=int, default=1)
    args = ap.parse_args()
    srv = make_server(args.port, args.seed)
    print("READY %s:%d" % srv.server_address[:2], flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
