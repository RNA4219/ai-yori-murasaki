"""ブラウザ検収用の AYM と、別サイトの合成 Google ログイン画面を起動する。"""
from __future__ import annotations

from base64 import urlsafe_b64encode
from contextlib import ExitStack
from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import time
from urllib.parse import parse_qsl, urlencode, urlsplit

from ai_yori_murasaki.google_sso import GoogleSSO
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import create_server
from ai_yori_murasaki.storage import Store
from tests.fixtures.google_oidc_fixture import SyntheticGoogleKey


CLIENT = "browser-fixture"
CHOICES = {"allow": "許可した利用者", "deny": "未許可の利用者", "cancel": "キャンセル", "unavailable": "一時エラー"}


class LocalGoogle(GoogleSSO):
    def __init__(self, origin: str):
        self.origin = origin
        self.key = SyntheticGoogleKey()
        self.attempts = {}
        self.codes = {}
        super().__init__(CLIENT, secrets.token_urlsafe(), ["fixture-member"], request_json=self.reply)

    def begin(self, port: int, return_to: str = "/") -> tuple[str, str]:
        url, correlation = super().begin(port, return_to)
        query = urlsplit(url).query
        params = dict(parse_qsl(query))
        assert urlsplit(url).hostname == "accounts.google.com"
        assert set(params["scope"].split()) == {"openid", "profile"}
        self.attempts[params["state"]] = params
        return self.origin + "/choose?" + query, correlation

    def answer(self, state: str, choice: str) -> str:
        params = self.attempts.pop(state)
        result = {"state": state}
        if choice == "cancel":
            result["error"] = "access_denied"
        else:
            code = secrets.token_urlsafe()
            self.codes[code] = (params, choice)
            result["code"] = code
        return params["redirect_uri"] + "?" + urlencode(result)

    def reply(self, url: str, data: bytes | None) -> dict:
        if url == "https://www.googleapis.com/oauth2/v1/certs" and data is None:
            return self.key.certs
        assert url == "https://oauth2.googleapis.com/token" and data is not None
        token_request = dict(parse_qsl(data.decode("ascii")))
        params, choice = self.codes.pop(token_request["code"])
        if choice == "unavailable":
            raise OSError("一時エラーの合成応答")
        challenge = urlsafe_b64encode(sha256(token_request["code_verifier"].encode("ascii")).digest()).rstrip(b"=")
        assert challenge.decode("ascii") == params["code_challenge"]
        assert token_request["redirect_uri"] == params["redirect_uri"] and token_request["client_id"] == CLIENT
        now = int(time())
        return {"id_token": self.key.sign({
            "iss": "https://accounts.google.com", "aud": CLIENT, "iat": now, "exp": now + 600,
            "nonce": params["nonce"], "sub": "fixture-member" if choice == "allow" else "fixture-guest",
        })}


class ChoiceHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        route = urlsplit(self.path)
        query = dict(parse_qsl(route.query))
        state = query.get("state")
        google = self.server.google
        if self.headers.get("Cookie") or state not in google.attempts:
            self.send_error(400)
            return
        if route.path == "/answer" and query.get("choice") in CHOICES:
            self.send_response(303)
            self.send_header("Location", google.answer(state, query["choice"]))
            self.end_headers()
            return
        if route.path != "/choose":
            self.send_error(404)
            return
        links = []
        for choice, label in CHOICES.items():
            href = "/answer?" + urlencode({"state": state, "choice": choice})
            links.append(f'<p><a href="{escape(href, quote=True)}">{label}</a></p>')
        body = ('<!doctype html><html lang="ja"><meta charset="utf-8">'
                '<meta name="viewport" content="width=device-width, initial-scale=1">'
                '<title>テスト用アカウント選択</title><h1>テスト用アカウント選択</h1>'
                + "".join(links) + "</html>").encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


def main():
    with ExitStack() as stack:
        root = Path(stack.enter_context(TemporaryDirectory(prefix="aym-sso-browser-")))
        workspace, trusted = root / "workspace", root / "trusted"
        workspace.mkdir()
        trusted.mkdir()
        provider = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), ChoiceHandler))
        # localhost と 127.0.0.1 を別のサイトとしてブラウザで往復する。
        origin = f"http://localhost:{provider.server_port}"
        provider.google = LocalGoogle(origin)
        monitor = stack.enter_context(create_server(Runtime(Store(workspace, trusted)), "127.0.0.1:0",
                                                    google_sso=provider.google))
        for server in (provider, monitor):
            Thread(target=server.serve_forever, daemon=True).start()
            stack.callback(server.shutdown)
        print(json.dumps({"monitor": f"http://127.0.0.1:{monitor.server_port}", "provider": origin}), flush=True)
        if "--stop-on-eof" in sys.argv:
            sys.stdin.buffer.read()
        else:
            try:
                Event().wait()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
