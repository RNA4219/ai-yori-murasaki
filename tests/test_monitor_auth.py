"""モニターの公開入口から、認証前後の画面・API・ログアウトを照合する。"""
import contextlib
from concurrent.futures import ThreadPoolExecutor
from http.cookiejar import CookieJar
import io
import json
from pathlib import Path
import re
import tempfile
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import (
    build_opener, HTTPCookieProcessor, HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request,
)

from ai_yori_murasaki import cli
from ai_yori_murasaki.google_sso import GoogleLoginError
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import _handle, create_server
from ai_yori_murasaki.storage import Store

try:
    from tests.fixtures.loopback_tls import LoopbackTLS
except ModuleNotFoundError as exc:
    if exc.name != "cryptography":
        raise
    LoopbackTLS = None


class LocalProvider:
    """合成の承認応答だけを返す。HTTP の Cookie・フォーム・遷移は実際に処理する。"""
    def __init__(self):
        self.pending = {}
        self.sessions = set()
        self.serial = 0

    def begin(self, port, return_to):
        self.serial += 1
        correlation = f"synthetic-flow-{self.serial}"
        self.pending[correlation] = return_to
        return f"https://127.0.0.1:{port}/auth/google/callback?state={correlation}", correlation

    def complete(self, port, query, correlation):
        if not correlation or correlation != query.get("state") or correlation not in self.pending:
            raise GoogleLoginError("denied")
        target = self.pending.pop(correlation)
        if "error" in query:
            raise GoogleLoginError("cancelled", target)
        session = f"synthetic-session-{correlation}"
        self.sessions.add(session)
        return session, target

    def authenticated(self, session):
        return session in self.sessions

    def logout(self, session):
        self.sessions.discard(session)


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


def client(tls):
    return build_opener(ProxyHandler({}), HTTPSHandler(context=tls.client_context()),
                        HTTPCookieProcessor(CookieJar()), NoRedirect())


def request(browser, url, *, data=None, headers=None):
    req = Request(url, data=data, headers=headers or {})
    try:
        response = browser.open(req, timeout=5)
    except HTTPError as error:
        response = error
    with response:
        return response.status, response.headers, response.read()


@unittest.skipUnless(LoopbackTLS, "合成証明書の生成には cryptography が必要")
class MonitorAuthTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        workspace, trusted = [Path(directory.name) / name for name in ("workspace", "trusted")]
        workspace.mkdir()
        trusted.mkdir()
        self.runtime = Runtime(Store(workspace, trusted))
        self.provider = LocalProvider()
        self.tls = LoopbackTLS(Path(directory.name))
        self.server = create_server(self.runtime, "127.0.0.1:0", google_sso=self.provider,
                                    tls_context=self.tls.server_context())
        self.addCleanup(self.server.server_close)
        thread = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.05})
        thread.start()
        self.addCleanup(thread.join, 5)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_port
        self.origin = f"https://127.0.0.1:{self.port}"
        self.browser = client(self.tls)

    def assert_secure_cookie(self, headers):
        attributes = headers["Set-Cookie"].split("; ")
        for attribute in ("Secure", "HttpOnly", "SameSite=Lax"):
            self.assertIn(attribute, attributes)
        self.assertNotIn("Domain=", headers["Set-Cookie"])

    def enter(self, target="/"):
        start = "/auth/google/start?" + urlencode({"next": target})
        code, head, _ = request(self.browser, self.origin + start)
        self.assertEqual(code, 303)
        self.assert_secure_cookie(head)
        code, head, _ = request(self.browser, head["Location"])
        self.assertEqual(code, 303)
        self.assertEqual(head["Location"], target)
        self.assertEqual(len(head.get_all("Set-Cookie")), 1)
        self.assertTrue(head["Set-Cookie"].startswith("aym_session_"))
        self.assert_secure_cookie(head)

    def test_anonymous_pages_api_assets_and_independent_browser(self):
        for page in ("/", "/intake", "/decision", "/architecture"):
            code, head, body = request(self.browser, self.origin + page)
            self.assertEqual(code, 401)
            self.assertIn("モニターにログイン".encode(), body)
            self.assertNotIn(b'action="/auth/logout"', body)
            self.assertEqual(head["Cache-Control"], "no-store")
            self.assertEqual(head["Referrer-Policy"], "no-referrer")
        for path in ("/api/health", "/api/campaigns", "/api/intake", "/architecture/map.mjs",
                     "/architecture/product.json"):
            code, head, body = request(self.browser, self.origin + path)
            self.assertEqual(code, 401)
            self.assertEqual(json.loads(body), {"error": "authentication_required"})
        self.enter()
        self.assertEqual(request(self.browser, self.origin + "/api/health")[0], 200)
        self.assertEqual(request(client(self.tls), self.origin + "/api/health")[0], 401)
        self.assertEqual(request(self.browser, self.origin + "/architecture/map.mjs")[0], 200)
        self.provider.sessions.clear()  # サーバー側の期限切れ後に Cookie が残った場合
        self.assertEqual(request(self.browser, self.origin + "/api/health")[0], 401)
        self.assertEqual(request(self.browser, self.origin + "/")[0], 401)

    def test_localhost_preserves_selected_blue_page_on_canonical_host(self):
        target = "/architecture?view=blue&map=sample"
        start = "/auth/google/start?" + urlencode({"next": target})
        alias = f"https://localhost:{self.port}"
        code, _, body = request(self.browser, alias + target)
        self.assertEqual(code, 401)
        self.assertIn(start.encode(), body)
        code, headers, _ = request(self.browser, alias + start)
        self.assertEqual(code, 303)
        self.assertEqual(headers["Location"], self.origin + start)
        self.assertNotIn("Set-Cookie", headers)
        self.enter(target)
        code, _, body = request(self.browser, self.origin + "/api/architecture/maps?view=blue")
        self.assertEqual(code, 200)
        self.assertEqual(json.loads(body), {"status": "withheld"})

    def test_cancellation_removes_provider_query_and_keeps_blue_retry(self):
        target = "/decision?view=blue&campaign=synthetic-campaign"
        start = "/auth/google/start?" + urlencode({"next": target})
        _, head, _ = request(self.browser, self.origin + start)
        code, head, _ = request(self.browser, head["Location"] + "&error=access_denied")
        self.assertEqual(code, 303)
        self.assertNotIn("synthetic-flow", head["Location"])
        self.assertNotIn("access_denied", head["Location"])
        code, head, body = request(self.browser, self.origin + head["Location"])
        self.assertEqual(code, 401)
        self.assertIn("ログインをキャンセルしました".encode(), body)
        self.assertIn(start.encode(), body)
        self.assertEqual(head["Referrer-Policy"], "no-referrer")

    def test_newer_tab_can_finish_after_first_tab_returns(self):
        first = request(self.browser, self.origin + "/auth/google/start")[1]["Location"]
        target = "/architecture?view=blue&system=aym"
        start = "/auth/google/start?" + urlencode({"next": target})
        second = request(self.browser, self.origin + start)[1]["Location"]
        code, head, _ = request(self.browser, first + "&error=access_denied")
        self.assertEqual(code, 303)
        self.assertTrue(head["Location"].startswith("/auth/result?"))
        self.assertEqual(request(self.browser, self.origin + "/api/health")[0], 401)
        code, head, _ = request(self.browser, second)
        self.assertEqual(code, 303)
        self.assertEqual(head["Location"], target)
        self.assertEqual(request(self.browser, self.origin + target)[0], 200)

    def test_newer_tab_can_finish_when_first_success_was_delayed(self):
        exchanged = threading.Event()
        release = threading.Event()
        complete = self.provider.complete

        def wait_for_response(port, query, correlation):
            result = complete(port, query, correlation)
            exchanged.set()
            if not release.wait(5):
                raise RuntimeError("合成の応答待ち時間を超過")
            return result

        first = request(self.browser, self.origin + "/auth/google/start")[1]["Location"]
        with ThreadPoolExecutor(max_workers=1) as worker, patch.object(self.provider, "complete", wait_for_response):
            first_response = worker.submit(request, self.browser, first)
            try:
                self.assertTrue(exchanged.wait(5))
                start = "/auth/google/start?" + urlencode({"next": "/intake?view=blue"})
                second = request(self.browser, self.origin + start)[1]["Location"]
            finally:
                release.set()
            self.assertEqual(first_response.result()[0], 303)
        code, head, _ = request(self.browser, second)
        self.assertEqual(code, 303)
        self.assertEqual(head["Location"], "/intake?view=blue")
        self.assertEqual(request(self.browser, self.origin + head["Location"])[0], 200)

    def test_default_https_port_can_be_omitted_in_canonical_host_and_origin(self):
        auth = self.server.auth
        for authority in ("127.0.0.1", "127.0.0.1:443"):
            status, head, _ = _handle(self.runtime, "GET", "/auth/google/start", {},
                                      {"Host": authority}, 443, auth, scheme="https")
            self.assertEqual(status, 303)
            self.assertIn("/auth/google/callback?", head["Location"])
            self.assertEqual(len(head["Set-Cookie"]), 1)
            self.provider.sessions.add("fixture-session")
            headers = {"Host": authority, "Origin": "https://" + authority,
                       "Cookie": "aym_session_443=fixture-session"}
            status, _, _ = _handle(self.runtime, "POST", "/auth/logout", {}, headers, 443, auth,
                                   lambda: auth.for_session("fixture-session"), scheme="https")
            self.assertEqual(status, 303)
            self.assertFalse(self.provider.authenticated("fixture-session"))

    def test_all_pages_offer_session_bound_logout(self):
        self.enter()
        for path in ("/", "/intake", "/decision", "/architecture?view=blue"):
            code, head, body = request(self.browser, self.origin + path)
            self.assertEqual(code, 200)
            self.assertIn(b'action="/auth/logout"', body)
            self.assertIn("form-action 'self'", head["Content-Security-Policy"])
            self.assertEqual(head["Referrer-Policy"], "same-origin")
            token = re.search(rb'name="csrf" value="([a-f0-9]{64})"', body).group(1)
        logout = self.origin + "/auth/logout"
        self.assertEqual(request(self.browser, logout)[0], 405)
        self.assertEqual(request(self.browser, logout, data=b"csrf=" + token)[0], 403)
        self.assertEqual(request(self.browser, logout, headers={"Origin": self.origin}, data=b"csrf=")[0], 403)
        code, head, _ = request(self.browser, logout, headers={"Origin": self.origin}, data=b"csrf=" + token)
        self.assertEqual(code, 303)
        self.assertEqual(head["Location"], "/")
        self.assertIn("Max-Age=0", head["Set-Cookie"])
        self.assert_secure_cookie(head)
        self.assertFalse(self.provider.sessions)
        self.assertEqual(request(self.browser, self.origin + "/api/health")[0], 401)

    def test_cli_opt_in_configuration_failure_stops_before_serving(self):
        args = ["--workspace", str(self.runtime.store.workspace), "--trusted-root",
                str(self.runtime.store.trusted_root), "monitor", "serve"]
        self.assertFalse(cli._build_parser().parse_args(args).google_sso)
        with patch.dict("os.environ", {}, clear=True), contextlib.redirect_stdout(io.StringIO()) as output:
            code = cli.main([*args, "--google-sso"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(output.getvalue())["reason_code"], "google_sso_configuration")
