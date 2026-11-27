"""読み取り専用モニターの Google OIDC 認証。状態とセッションはプロセス内に限る。"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import time
from collections.abc import Callable, Collection
from dataclasses import dataclass
from http.client import HTTPException
from urllib.parse import urlencode
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from .jsonio import DataError

_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_CERTS_URL = "https://www.googleapis.com/oauth2/v1/certs"
_ISSUERS = ("accounts.google.com", "https://accounts.google.com")
PENDING_TTL = 300
SESSION_TTL = 3600
_MAX_PENDING = 256
_MAX_SESSIONS = 1024
_MAX_RESPONSE = 64 * 1024


class GoogleLoginError(Exception):
    def __init__(self, state: str, return_to: str = "/") -> None:
        self.state = state
        self.return_to = return_to
        super().__init__(state)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        raise GoogleLoginError("unavailable")


def _request_json(url: str, data: bytes | None) -> dict:
    if not ((url == _TOKEN_URL and data is not None) or (url == _CERTS_URL and data is None)):
        raise GoogleLoginError("unavailable")
    opener = build_opener(ProxyHandler({}), _NoRedirect())
    req = Request(url, data=data, headers={"Accept": "application/json"})
    with opener.open(req, timeout=5) as response:
        body = response.read(_MAX_RESPONSE + 1)
    if len(body) > _MAX_RESPONSE:
        raise GoogleLoginError("unavailable")
    obj = json.loads(body)
    if not isinstance(obj, dict):
        raise GoogleLoginError("unavailable")
    return obj


def _return_path(path: str) -> str:
    if (not path.startswith("/") or path.startswith("//") or "\\" in path
            or any(ord(c) < 32 or ord(c) == 127 for c in path)):
        return "/"
    return path


def _callback(port: int) -> str:
    return f"http://127.0.0.1:{port}/auth/google/callback"


@dataclass(frozen=True, repr=False)
class _Attempt:
    expires: float
    browser: str
    verifier: str
    nonce: str
    port: int
    return_to: str


class GoogleSSO:
    def __init__(self, client_id: str, client_secret: str, allowed_subjects: Collection[str],
                 *, request_json: Callable[[str, bytes | None], dict] = _request_json,
                 clock: Callable[[], float] = time.monotonic) -> None:
        if (not client_id or not client_secret or not allowed_subjects
                or isinstance(allowed_subjects, str)
                or any(not re.fullmatch(r"[A-Za-z0-9._:-]{1,255}", sub) for sub in allowed_subjects)):
            raise DataError("Google SSO のクライアント設定・許可する sub を確認してください。",
                            "google_sso_configuration")
        try:
            from google.auth import jwt
        except ImportError:
            raise DataError("Google SSO 用の依存 [sso] が必要です。", "google_sso_configuration") from None
        self._jwt = jwt
        self._client_id = client_id
        self._client_secret = client_secret
        self._subjects = frozenset(allowed_subjects)
        self._request_json = request_json
        self._clock = clock
        self._lock = threading.Lock()
        self._attempts: dict[str, _Attempt] = {}
        self._sessions: dict[str, float] = {}

    @classmethod
    def from_env(cls) -> GoogleSSO:
        subjects = [s.strip() for s in os.getenv("AYM_GOOGLE_ALLOWED_SUBJECTS", "").split(",")]
        return cls(os.getenv("AYM_GOOGLE_CLIENT_ID", "").strip(),
                   os.getenv("AYM_GOOGLE_CLIENT_SECRET", "").strip(), subjects)

    def _prune(self, now: float) -> None:
        for key in [key for key, item in self._attempts.items() if item.expires <= now]:
            del self._attempts[key]
        for key in [key for key, expiry in self._sessions.items() if expiry <= now]:
            del self._sessions[key]

    def begin(self, port: int, return_to: str = "/") -> tuple[str, str]:
        state = secrets.token_urlsafe(32)
        with self._lock:
            now = self._clock()
            self._prune(now)
            if len(self._attempts) >= _MAX_PENDING:
                raise GoogleLoginError("unavailable")
            attempt = _Attempt(now + PENDING_TTL, secrets.token_urlsafe(32), secrets.token_urlsafe(48),
                               secrets.token_urlsafe(32), port, _return_path(return_to))
            self._attempts[state] = attempt
        digest = hashlib.sha256(attempt.verifier.encode("ascii")).digest()
        params = {"response_type": "code", "scope": "openid profile", "prompt": "select_account",
                  "client_id": self._client_id,
                  "redirect_uri": _callback(port), "state": state, "nonce": attempt.nonce,
                  "code_challenge": base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii"),
                  "code_challenge_method": "S256"}
        return _AUTH_URL + "?" + urlencode(params), attempt.browser

    def _exchange(self, code: str, attempt: _Attempt) -> float:
        data = urlencode({"grant_type": "authorization_code", "code": code,
                          "client_id": self._client_id, "client_secret": self._client_secret,
                          "redirect_uri": _callback(attempt.port), "code_verifier": attempt.verifier})
        try:
            tokens = self._request_json(_TOKEN_URL, data.encode("ascii"))
            token = tokens.get("id_token")
            if not isinstance(token, str) or len(token) > _MAX_RESPONSE:
                raise GoogleLoginError("denied", attempt.return_to)
            certs = self._request_json(_CERTS_URL, None)
        except GoogleLoginError as exc:
            raise GoogleLoginError(exc.state, attempt.return_to) from None
        except (OSError, HTTPException, ValueError, RecursionError):
            raise GoogleLoginError("unavailable", attempt.return_to) from None
        try:
            header = self._jwt.decode_header(token)
            if header.get("alg") != "RS256" or not isinstance(header.get("kid"), str) or not header["kid"]:
                raise ValueError
            claims = self._jwt.decode(token, certs=certs, audience=self._client_id)
            nonce = claims.get("nonce")
            subject = claims.get("sub")
            expiry = claims["exp"]
            if (claims.get("iss") not in _ISSUERS or claims.get("azp", self._client_id) != self._client_id
                    or not isinstance(nonce, str) or not hmac.compare_digest(nonce, attempt.nonce)
                    or not isinstance(subject, str) or subject not in self._subjects
                    or type(expiry) is not int or type(claims["iat"]) is not int or expiry <= time.time()):
                raise ValueError
        except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError):
            raise GoogleLoginError("denied", attempt.return_to) from None
        return expiry

    def complete(self, port: int, query: dict[str, str], correlation: str | None) -> tuple[str, str]:
        state = query.get("state", "")
        with self._lock:
            self._prune(self._clock())
            attempt = self._attempts.get(state)
            if (not attempt or port != attempt.port or not correlation or not correlation.isascii()
                    or not hmac.compare_digest(attempt.browser, correlation)):
                raise GoogleLoginError("denied")
            del self._attempts[state]
        if "error" in query:
            reason = "cancelled" if query["error"] == "access_denied" else "unavailable"
            raise GoogleLoginError(reason, attempt.return_to)
        code = query.get("code", "")
        if not code or len(code) > 4096 or query.get("iss", _ISSUERS[1]) not in _ISSUERS:
            raise GoogleLoginError("denied", attempt.return_to)
        expiry = self._exchange(code, attempt)
        with self._lock:
            now = self._clock()
            self._prune(now)
            if now >= attempt.expires or len(self._sessions) >= _MAX_SESSIONS:
                raise GoogleLoginError("unavailable", attempt.return_to)
            remaining = expiry - time.time()
            if remaining <= 0:
                raise GoogleLoginError("denied", attempt.return_to)
            session = secrets.token_urlsafe(32)
            self._sessions[session] = now + min(SESSION_TTL, remaining)
        return session, attempt.return_to

    def authenticated(self, token: str | None) -> bool:
        with self._lock:
            expiry = self._sessions.get(token) if token else None
            return expiry is not None and expiry > self._clock()

    def logout(self, token: str | None) -> None:
        if token:
            with self._lock:
                self._sessions.pop(token, None)
