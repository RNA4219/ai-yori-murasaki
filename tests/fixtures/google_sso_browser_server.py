"""ブラウザ検収用の AYM と、別サイトの合成 Google ログイン画面を起動する。"""
from __future__ import annotations

import argparse
from base64 import urlsafe_b64encode
from contextlib import ExitStack
from dataclasses import dataclass
from hashlib import sha256
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import secrets
import sys
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import time
from unittest.mock import patch
from urllib.parse import parse_qsl, urlencode, urlsplit

from ai_yori_murasaki.google_sso import GoogleSSO
from ai_yori_murasaki.runtime import Runtime
from ai_yori_murasaki.server import configured_server
from ai_yori_murasaki.storage import Store
from tests.fixtures.google_oidc_fixture import SyntheticGoogleKey
from tests.fixtures.loopback_tls import LoopbackTLS


CLIENT = "browser-fixture"
DOMAIN = "studio.example"


@dataclass(frozen=True)
class Account:
    label: str
    subject: str
    domain: str
    verified: bool = True


ACCOUNTS = {
    "allow": Account("許可した利用者", "fixture-member", DOMAIN),
    "deny": Account("未許可の利用者", "fixture-guest", "other.example"),
    "domain-only": Account("ドメインのみ一致", "fixture-guest", DOMAIN),
    "subject-only": Account("利用者IDのみ一致", "fixture-member", "other.example"),
    "unverified": Account("未確認のメール", "fixture-member", DOMAIN, False),
}
CHOICES = {key: account.label for key, account in ACCOUNTS.items()}
CHOICES.update({"cancel": "キャンセル", "unavailable": "一時エラー"})


class LocalGoogle(GoogleSSO):
    def __init__(self, client_id, client_secret, allowed_subjects, *, allowed_email_domains=()):
        self.key = SyntheticGoogleKey()
        self.attempts = {}
        self.codes = {}
        super().__init__(client_id, client_secret, allowed_subjects, allowed_email_domains=allowed_email_domains,
                         request_json=self.reply)

    def begin(self, port: int, return_to: str = "/") -> tuple[str, str]:
        url, correlation = super().begin(port, return_to)
        query = urlsplit(url).query
        params = dict(parse_qsl(query))
        assert urlsplit(url).hostname == "accounts.google.com"
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
        account = ACCOUNTS[choice]
        claims = {
            "iss": "https://accounts.google.com", "aud": CLIENT, "iat": now, "exp": now + 600,
            "nonce": params["nonce"], "sub": account.subject,
        }
        if "email" in params["scope"].split():
            claims.update(email="member@" + account.domain, hd=account.domain, email_verified=account.verified)
        return {"id_token": self.key.sign(claims)}


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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy", choices=("subject", "domain", "both"), default="subject")
    parser.add_argument("--stop-on-eof", action="store_true")
    args = parser.parse_args()
    with ExitStack() as stack:
        root = Path(stack.enter_context(TemporaryDirectory(prefix="aym-sso-browser-")))
        workspace, trusted = root / "workspace", root / "trusted"
        workspace.mkdir()
        trusted.mkdir()
        tls = LoopbackTLS(root)
        provider = stack.enter_context(ThreadingHTTPServer(("127.0.0.1", 0), ChoiceHandler))
        provider.socket = tls.server_context().wrap_socket(provider.socket, server_side=True)
        # localhost と 127.0.0.1 を別のサイトとしてブラウザで往復する。
        origin = f"https://localhost:{provider.server_port}"
        with patch.dict(os.environ, AYM_GOOGLE_CLIENT_ID=CLIENT, AYM_GOOGLE_CLIENT_SECRET=secrets.token_urlsafe()):
            os.environ.pop("AYM_GOOGLE_ALLOWED_SUBJECTS", None)
            os.environ.pop("AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS", None)
            if args.policy in ("subject", "both"):
                os.environ["AYM_GOOGLE_ALLOWED_SUBJECTS"] = "fixture-member"
            if args.policy in ("domain", "both"):
                os.environ["AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS"] = DOMAIN
            with patch("ai_yori_murasaki.google_sso.GoogleSSO", LocalGoogle):
                monitor = stack.enter_context(configured_server(
                    Runtime(Store(workspace, trusted)), "127.0.0.1:0", cert=tls.cert, key=tls.key, google_sso=True))
        provider.google = monitor.auth.provider
        provider.google.origin = origin
        for server in (provider, monitor):
            Thread(target=server.serve_forever, daemon=True).start()
            stack.callback(server.shutdown)
        print(json.dumps({"monitor": f"https://127.0.0.1:{monitor.server_port}", "provider": origin,
                          "spkiPin": tls.spki_pin, "caFile": str(tls.ca)}), flush=True)
        if args.stop_on_eof:
            sys.stdin.buffer.read()
        else:
            try:
                Event().wait()
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
