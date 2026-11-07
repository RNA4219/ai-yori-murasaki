#!/usr/bin/env python3
"""CVE-2024-35195 behavioral probe (loopback TLS, isolated)."""
import argparse
import json
import os
import ssl
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

CERT_DIR = "/opt/aym-test-cert"
CERT = os.path.join(CERT_DIR, "cert.pem")
KEY = os.path.join(CERT_DIR, "key.pem")
DEPS = {"old": "/opt/requests-old", "fixed": "/opt/requests-fixed"}
EXPECTED = {"old": "2.31.0", "fixed": "2.32.5"}


def fail(reason, code=3):
    print(json.dumps({"status": "invalid", "reason": reason}))
    sys.exit(code)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        with self.server.counter_lock:
            self.server.request_count += 1
        body = b"ok"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def make_server():
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    try:
        ctx.load_cert_chain(CERT, KEY)
    except Exception as e:
        fail("cert_load_failed:" + type(e).__name__)
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    srv.daemon_threads = True
    srv.counter_lock = threading.Lock()
    srv.request_count = 0
    srv.socket = ctx.wrap_socket(srv.socket, server_side=True)
    return srv


def err_name(exc):
    return type(exc).__name__


def probe(port):
    import requests
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

    url = "https://127.0.0.1:%d/" % port
    out = {
        "library_version": requests.__version__,
        "initial_status": None,
        "second_status": None,
        "second_error": None,
        "fresh_status": None,
        "fresh_error": None,
        "benign_status": None,
        "benign_error": None,
        "requests_count": 0,
        "endpoint": "loopback_tls",
    }

    def do(sess, verify):
        try:
            r = sess.get(url, verify=verify, timeout=3)
            return r.status_code, None
        except Exception as e:
            return None, err_name(e)

    s = requests.Session()
    s.trust_env = False
    st, er = do(s, False)
    out["initial_status"] = st
    if er:
        out["second_error"] = er
    st, er = do(s, True)
    out["second_status"] = st
    out["second_error"] = er
    s.close()

    s2 = requests.Session()
    s2.trust_env = False
    st, er = do(s2, True)
    out["fresh_status"] = st
    out["fresh_error"] = er
    s2.close()

    s3 = requests.Session()
    s3.trust_env = False
    st, er = do(s3, CERT)
    out["benign_status"] = st
    out["benign_error"] = er
    s3.close()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--deps", choices=["old", "fixed"], required=True)
    args = ap.parse_args()

    dep = DEPS[args.deps]
    if not os.path.isdir(dep):
        fail("deps_missing:" + args.deps)
    if not (os.path.isfile(CERT) and os.path.isfile(KEY)):
        fail("cert_missing")

    sys.path.insert(0, dep)
    try:
        import requests
    except Exception as e:
        fail("import_failed:" + type(e).__name__)

    if requests.__version__ != EXPECTED[args.deps]:
        fail("version_mismatch:%s" % requests.__version__)

    srv = make_server()
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    port = srv.server_address[1]
    try:
        result = probe(port)
    except Exception as e:
        fail("probe_failed:" + type(e).__name__)
    finally:
        try:
            srv.shutdown()
        except Exception:
            pass
        try:
            srv.server_close()
        except Exception:
            pass

    with srv.counter_lock:
        result["requests_count"] = srv.request_count
    print(json.dumps(result))


if __name__ == "__main__":
    main()
