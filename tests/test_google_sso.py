"""Google には接続せず、発行元の合成証明書で ID トークンを検証する。"""
import base64
import hashlib
import os
import sys
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

from ai_yori_murasaki import google_sso as sso
from ai_yori_murasaki.jsonio import DataError

try:
    import google.auth
except ModuleNotFoundError as exc:
    if exc.name not in {"google", "google.auth"}:
        raise
    SyntheticGoogleKey = None
else:
    from tests.fixtures.google_oidc_fixture import SyntheticGoogleKey


class ConfigurationTests(unittest.TestCase):
    def test_configuration_and_optional_dependency_failure(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaises(DataError) as err:
            sso.GoogleSSO.from_env()
        self.assertEqual(err.exception.reason_code, "google_sso_configuration")
        with patch.dict(sys.modules, {"google.auth": None}), self.assertRaises(DataError) as err:
            sso.GoogleSSO("client", "secret", {"one"})
        self.assertEqual(err.exception.reason_code, "google_sso_configuration")
        for invalid in ("", "*", "person@example.invalid", " "):
            with self.subTest(invalid=invalid), self.assertRaises(DataError):
                sso.GoogleSSO("client", "secret", {invalid})

    def test_invalid_domain_policy_fails_even_with_a_valid_subject(self):
        env = {"AYM_GOOGLE_CLIENT_ID": "client", "AYM_GOOGLE_CLIENT_SECRET": "secret",
               "AYM_GOOGLE_ALLOWED_SUBJECTS": "allowed"}
        invalid = ("", " ", ",", "example.test,", "*.example.test", "@example.test", "https://example.test",
                   "example.test.", "local", "127.0.0.1", "[::1]", ".example.test", "example..test",
                   "-mail.example", "mail-.example", "a" * 64 + ".test", ("a" * 63 + ".") * 4 + "test",
                   "two names.test", "K.example", "例.test")
        for domains in invalid:
            with (self.subTest(domains=domains), self.assertRaises(DataError) as err,
                  patch.dict(os.environ, {**env, "AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS": domains}, clear=True)):
                sso.GoogleSSO.from_env()
            self.assertEqual(err.exception.reason_code, "google_sso_configuration")
        for subjects, domains in ((iter(["only-me"]), ()), ((), iter([])), ((), "example.test")):
            with self.assertRaises(DataError):
                sso.GoogleSSO("client", "secret", subjects, allowed_email_domains=domains)

    def test_fixed_transport_limits_and_redirect_policy(self):
        opener = MagicMock()
        response = opener.open.return_value.__enter__.return_value
        response.read.return_value = b'{"field":"ok"}'
        with patch.object(sso, "build_opener", return_value=opener) as build:
            self.assertEqual(sso._request_json(sso._TOKEN_URL, b"code=sample"), {"field": "ok"})
            request = opener.open.call_args.args[0]
            self.assertEqual((request.get_method(), request.full_url), ("POST", sso._TOKEN_URL))
            self.assertEqual(opener.open.call_args.kwargs, {"timeout": 5})
            self.assertEqual(build.call_args.args[0].proxies, {})
            with self.assertRaises(sso.GoogleLoginError):
                build.call_args.args[1].redirect_request(None, None, 307, "", {}, "https://fixture.invalid/")
            response.read.return_value = b" " * (sso._MAX_RESPONSE + 1)
            with self.assertRaises(sso.GoogleLoginError):
                sso._request_json(sso._CERTS_URL, None)
            with self.assertRaises(sso.GoogleLoginError):
                sso._request_json("https://fixture.invalid/", b"code=sample")
            self.assertEqual(opener.open.call_count, 2)


@unittest.skipUnless(SyntheticGoogleKey, "Google SSO の任意依存 [sso] が必要")
class FederatedLoginTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.key = SyntheticGoogleKey()
        cls.other_key = SyntheticGoogleKey()

    def setUp(self):
        self.now = 1000
        self.params = {}
        self.claim_changes = {}
        self.certs = self.key.certs
        self.calls = []
        self.backend = sso.GoogleSSO("client", "test-secret", {"allowed"},
                                     request_json=self.provider, clock=lambda: self.now)

    def provider(self, url, data):
        self.calls.append(url)
        if url == sso._CERTS_URL:
            self.assertIsNone(data)
            return self.certs
        self.assertEqual(url, sso._TOKEN_URL)
        form = dict((k, v[0]) for k, v in parse_qs(data.decode()).items())
        self.assertEqual(form["code"], "test-code")
        self.assertEqual(form["client_secret"], "test-secret")
        self.assertEqual(form["redirect_uri"], self.params["redirect_uri"])
        self.assertEqual(form["grant_type"], "authorization_code")
        self.assertEqual(form["client_id"], "client")
        sha = hashlib.sha256(form["code_verifier"].encode("ascii")).digest()
        self.assertEqual(base64.urlsafe_b64encode(sha).decode().rstrip("="), self.params["code_challenge"])
        now = int(time.time())
        claims = {"iss": "https://accounts.google.com", "aud": "client", "azp": "client", "sub": "allowed",
                  "nonce": self.params["nonce"], "iat": now, "exp": now + 7200, **self.claim_changes}
        return {"id_token": self.key.sign(claims), "access_token": "unused"}

    def begin(self, target="/decision?view=blue", *, email_scope=False):
        url, self.cookie = self.backend.begin(8123, target)
        parts = urlsplit(url)
        self.assertEqual((parts.scheme, parts.netloc, parts.path),
                         ("https", "accounts.google.com", "/o/oauth2/v2/auth"))
        self.params = {k: v[0] for k, v in parse_qs(parts.query).items()}
        self.assertEqual(self.params["scope"], "openid email" if email_scope else "openid profile")
        self.assertEqual(self.params["prompt"], "select_account")
        self.assertEqual(self.params["redirect_uri"], "https://127.0.0.1:8123/auth/google/callback")
        self.assertEqual(self.params["code_challenge_method"], "S256")
        self.assertNotIn(self.cookie, url)
        self.assertNotIn("test-secret", url)
        return self.params["state"]

    def complete(self):
        return self.backend.complete(8123, {"code": "test-code", "state": self.params["state"],
                                             "next": "/ignored"}, self.cookie)

    def test_signed_login_session_and_logout(self):
        self.begin()
        token, target = self.complete()
        self.assertEqual(target, "/decision?view=blue")
        self.assertTrue(self.backend.authenticated(token))
        self.assertFalse(self.backend.authenticated(self.cookie))
        self.assertEqual(self.calls, [sso._TOKEN_URL, sso._CERTS_URL])
        self.backend.logout(token)
        self.assertFalse(self.backend.authenticated(token))
        with self.assertRaises(sso.GoogleLoginError):
            self.complete()

    def test_domain_configuration_and_scope(self):
        env = {"AYM_GOOGLE_CLIENT_ID": "client", "AYM_GOOGLE_CLIENT_SECRET": "test-secret"}
        for settings, needs_email in (({"AYM_GOOGLE_ALLOWED_SUBJECTS": "one, two"}, False),
                                     ({"AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS": " Dept.EXAMPLE, other.test "}, True),
                                     ({"AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS": "other.test",
                                       "AYM_GOOGLE_ALLOWED_SUBJECTS": "one"}, True)):
            with self.subTest(settings=settings), patch.dict(os.environ, {**env, **settings}, clear=True):
                self.backend = sso.GoogleSSO.from_env()
                self.begin(email_scope=needs_email)
        for settings in ({}, {"AYM_GOOGLE_ALLOWED_SUBJECTS": "", "AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS": "org.test"}):
            with (self.subTest(settings=settings), patch.dict(os.environ, {**env, **settings}, clear=True),
                  self.assertRaises(DataError)):
                sso.GoogleSSO.from_env()

    def test_signed_workspace_claims_require_matching_verified_address_and_hd(self):
        self.backend = sso.GoogleSSO("client", "test-secret", allowed_email_domains=["Dept.Example", "org.test"],
                                     request_json=self.provider)
        valid = {"sub": "person-51", "email_verified": True, "email": "member@DEPT.Example", "hd": "dept.EXAMPLE"}
        self.claim_changes = valid
        self.begin(email_scope=True)
        token, target = self.complete()
        self.assertEqual(target, "/decision?view=blue")
        self.assertTrue(self.backend.authenticated(token))
        self.assertNotIn("member@", repr(vars(self.backend)))
        for change in ({"email_verified": False}, {"email_verified": "true"}, {"email_verified": 1},
                       {"email": None}, {"email": "missing-at"}, {"email": "@dept.example"},
                       {"email": "extra@part@dept.example"}, {"email": "a@child.dept.example"},
                       {"email": "a@dеpt.example"}, {"email": "a@gmail.com", "hd": "gmail.com"},
                       {"hd": "org.test"}, {"hd": ""}, {"hd": None}, {"sub": ""}, {"sub": None}, {"sub": "a b"}):
            self.claim_changes = {**valid, **change}
            with self.subTest(change=change):
                self.begin(email_scope=True)
                with self.assertRaises(sso.GoogleLoginError) as err:
                    self.complete()
                self.assertEqual(err.exception.state, "denied")
        for missing in ("email", "email_verified", "hd"):
            self.claim_changes = {key: value for key, value in valid.items() if key != missing}
            with self.subTest(missing=missing):
                self.begin(email_scope=True)
                with self.assertRaises(sso.GoogleLoginError):
                    self.complete()

    def test_gmail_and_combined_subject_policy(self):
        self.backend = sso.GoogleSSO("client", "test-secret", {"allowed"}, allowed_email_domains={"gmail.com"},
                                     request_json=self.provider)
        valid = {"email": "fixture@GMAIL.com", "email_verified": True}
        for hd in ({}, {"hd": "GMAIL.COM"}):
            self.claim_changes = {**valid, **hd}
            self.begin(email_scope=True)
            self.assertTrue(self.backend.authenticated(self.complete()[0]))
        for change in ({"sub": "different"}, {"hd": None}, {"hd": "other.test"}, {"email": "fixture@googlemail.com"},
                       {"email": "fixture@child.gmail.com"}, {"email_verified": False}):
            self.claim_changes = {**valid, **change}
            with self.subTest(change=change):
                self.begin(email_scope=True)
                with self.assertRaises(sso.GoogleLoginError):
                    self.complete()

    def test_issued_session_and_pending_request_expire(self):
        self.begin("//fixture.invalid/")
        token, target = self.complete()
        self.assertEqual(target, "/")
        self.now += sso.SESSION_TTL
        self.assertFalse(self.backend.authenticated(token))
        self.begin()
        self.now += sso.PENDING_TTL
        with self.assertRaises(sso.GoogleLoginError):
            self.complete()

    def test_cancel_correlates_browser_and_port_and_consumes_state(self):
        state = self.begin()
        response = {"error": "access_denied", "state": state}
        for port, cookie in ((8123, "another-browser"), (8910, self.cookie)):
            with self.assertRaises(sso.GoogleLoginError) as err:
                self.backend.complete(port, response, cookie)
            self.assertEqual((err.exception.state, err.exception.return_to), ("denied", "/"))
        with self.assertRaises(sso.GoogleLoginError) as err:
            self.backend.complete(8123, response, self.cookie)
        self.assertEqual((err.exception.state, err.exception.return_to), ("cancelled", "/decision?view=blue"))
        with self.assertRaises(sso.GoogleLoginError):
            self.complete()
        self.assertEqual(self.calls, [])

    def test_signed_claims_and_signing_certificate_must_match(self):
        for change in ({"iss": "https://fixture.invalid"}, {"aud": "other"}, {"azp": "other"},
                       {"sub": "not-listed", "email": "allowed"}, {"nonce": "different"},
                       {"iat": int(time.time()) + 900}, {"exp": int(time.time()) - 60}):
            with self.subTest(change=change):
                self.begin()
                self.claim_changes = change
                with self.assertRaises(sso.GoogleLoginError) as err:
                    self.complete()
                self.assertEqual((err.exception.state, err.exception.return_to), ("denied", "/decision?view=blue"))
        self.claim_changes = {}
        for certs in ({}, self.other_key.certs):
            self.begin()
            self.certs = certs
            with self.assertRaises(sso.GoogleLoginError):
                self.complete()

    def test_state_is_consumed_once_with_concurrent_callbacks(self):
        self.begin()

        def callback(_):
            try:
                return self.complete()[0]
            except sso.GoogleLoginError:
                return None

        with ThreadPoolExecutor(max_workers=4) as threads:
            results = list(threads.map(callback, range(4)))
        self.assertEqual(sum(token is not None for token in results), 1)
        self.assertEqual(self.calls.count(sso._TOKEN_URL), 1)

    def test_only_rs256_and_named_signing_key_are_supported(self):
        for header in ({"alg": "ES256", "kid": "local-fixture"}, {"alg": "RS256"}):
            self.begin()
            with patch.object(self.backend._jwt, "decode_header", return_value=header), self.assertRaises(sso.GoogleLoginError):
                self.complete()

    def test_pending_and_session_limits(self):
        with patch.object(sso, "_MAX_PENDING", 1), patch.object(sso, "_MAX_SESSIONS", 1):
            self.begin()
            with self.assertRaises(sso.GoogleLoginError):
                self.backend.begin(8123)
            token = self.complete()[0]
            self.begin()
            with self.assertRaises(sso.GoogleLoginError):
                self.complete()
            self.assertTrue(self.backend.authenticated(token))

    def test_provider_unavailable_keeps_verified_return_target(self):
        self.begin()
        with patch.object(self.backend, "_request_json", side_effect=TimeoutError), self.assertRaises(sso.GoogleLoginError) as err:
            self.complete()
        self.assertEqual((err.exception.state, err.exception.return_to), ("unavailable", "/decision?view=blue"))
