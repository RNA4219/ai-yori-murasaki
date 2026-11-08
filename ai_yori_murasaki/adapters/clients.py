"""RustXNG and Wazuh HTTP clients via HttpBroker."""
from __future__ import annotations

from .. import jsonio
from .. import policy as policy_mod
from . import base
from .llm import _check_budget
from .normalizers import normalize_rustxng, normalize_rustxng_health, normalize_wazuh


_PRIVATE_HOST_MARKERS = (".internal", ".local", "localhost")


def _is_private_ipv4(host: str) -> bool:
    parts = host.split(".")
    if len(parts) != 4:
        return False
    try:
        octets = [int(p) for p in parts]
    except ValueError:
        return False
    if any(o < 0 or o > 255 for o in octets):
        return False
    a, b = octets[0], octets[1]
    if a == 10:
        return True
    if a == 127:
        return True
    if a == 172 and 16 <= b <= 31:
        return True
    if a == 192 and b == 168:
        return True
    if a == 169 and b == 254:
        return True
    return False


def _contains_private_host(text: str) -> bool:
    low = text.lower()
    for marker in _PRIVATE_HOST_MARKERS:
        if marker in low:
            return True
    for token in low.replace("=", " ").replace("&", " ").replace("/", " ").split():
        if _is_private_ipv4(token):
            return True
    return False


def _check_query_policy(query: str, *, role: str, secrets: tuple) -> None:
    if not isinstance(role, str) or role != "worker-blue":
        raise policy_mod.PolicyDenied("rustxng search requires role worker-blue")
    if not isinstance(query, str):
        raise policy_mod.PolicyDenied("query must be string")
    if not isinstance(secrets, tuple):
        raise policy_mod.PolicyDenied("secrets must be tuple")
    for s in secrets:
        if not isinstance(s, str):
            raise policy_mod.PolicyDenied("secret entries must be strings")
        if s and s in query:
            raise policy_mod.PolicyDenied("query contains registered secret")
    if _contains_private_host(query):
        raise policy_mod.PolicyDenied("query contains private host")


class RustXNGClient:
    """Blue-only RustXNG search client.

    - GET /search?q=...&format=json&max_results=5
    - GET /healthz
    - role must be worker-blue; red/other roles send zero requests
    - query secrets/private hostnames rejected before send (PolicyDenied)
    - raw query never appears in errors or metadata
    - result URLs hashed, fetch time recorded
    """

    def __init__(self, broker: base.HttpBroker, *, base_url: str,
                 timeout_ms: int = 10000, retry_cap: int = 2,
                 response_cap: int = 5242880) -> None:
        if not isinstance(broker, base.HttpBroker):
            raise jsonio.DataError("broker must be HttpBroker")
        self._broker = broker
        self._base_url = base_url.rstrip("/")
        self._timeout_ms = _check_budget("timeout_ms", timeout_ms, 1, 300000)
        self._retry_cap = _check_budget("retry_cap", retry_cap, 0, 2)
        self._response_cap = _check_budget("response_cap", response_cap, 1, 52428800)

    def search(self, query: str, *, role: str, secrets: tuple = (),
               max_results: int = 5) -> dict:
        from urllib.parse import quote
        _check_query_policy(query, role=role, secrets=secrets)
        safe_q = quote(query, safe="")
        url = f"{self._base_url}/search?q={safe_q}&format=json&max_results={max_results}"
        last_err: Exception | None = None
        for _ in range(self._retry_cap + 1):
            try:
                resp = self._broker.request(
                    "GET", url, timeout=self._timeout_ms / 1000.0,
                    max_bytes=self._response_cap)
                if resp["status"] != 200:
                    raise base.AdapterUnavailable(
                        f"rustxng status {resp['status']}")
                return normalize_rustxng(resp["body"], query=query)
            except base.AdapterUnavailable as exc:
                last_err = exc
                continue
        raise base.AdapterUnavailable(str(last_err) if last_err else "rustxng failed")

    def health(self) -> dict:
        url = f"{self._base_url}/healthz"
        resp = self._broker.request("GET", url,
                                    timeout=self._timeout_ms / 1000.0,
                                    max_bytes=self._response_cap)
        if resp["status"] != 200:
            raise base.AdapterUnavailable(f"rustxng health {resp['status']}")
        return normalize_rustxng_health(resp["body"])


class WazuhClient:
    """Wazuh alert import + configured HTTP client."""

    def __init__(self, broker: base.HttpBroker, *, base_url: str,
                 timeout_ms: int = 30000, retry_cap: int = 2,
                 response_cap: int = 10485760) -> None:
        if not isinstance(broker, base.HttpBroker):
            raise jsonio.DataError("broker must be HttpBroker")
        self._broker = broker
        self._base_url = base_url.rstrip("/")
        self._timeout_ms = _check_budget("timeout_ms", timeout_ms, 1, 300000)
        self._retry_cap = _check_budget("retry_cap", retry_cap, 0, 2)
        self._response_cap = _check_budget("response_cap", response_cap, 1, 52428800)

    def _request(self, method: str, url: str, *, headers: dict | None = None,
                 body: bytes | None = None, max_bytes: int | None = None) -> dict:
        last_err: Exception | None = None
        for _ in range(self._retry_cap + 1):
            try:
                return self._broker.request(
                    method, url, body=body, headers=headers,
                    timeout=self._timeout_ms / 1000.0,
                    max_bytes=max_bytes or self._response_cap)
            except base.AdapterUnavailable as exc:
                last_err = exc
                continue
        raise base.AdapterUnavailable(
            str(last_err) if last_err else "wazuh request failed")

    def import_alert(self, raw: bytes) -> dict:
        return normalize_wazuh(raw)

    def fetch_alert(self, alert_id: str) -> dict:
        from urllib.parse import quote
        url = f"{self._base_url}/alerts/{quote(alert_id, safe='')}"
        resp = self._request("GET", url)
        if resp["status"] != 200:
            raise base.AdapterUnavailable(f"wazuh http status {resp['status']}")
        return normalize_wazuh(resp["body"])

    def health(self) -> dict:
        url = f"{self._base_url}/"
        resp = self._request("GET", url, max_bytes=65536)
        return {"status": "ok" if resp["status"] in (200, 401, 403) else "fail",
                "http_status": resp["status"]}

    def authenticate(self, *, username: str, password: str) -> dict:
        """Obtain a JWT token from the Wazuh API (POST /security/user/authenticate)."""
        import base64
        url = f"{self._base_url}/security/user/authenticate"
        creds = base64.b64encode(f"{username}:{password}".encode()).decode()
        resp = self._request(
            "POST", url, headers={"Authorization": f"Basic {creds}"},
            body=b"", max_bytes=65536)
        if resp["status"] != 200:
            raise base.AdapterUnavailable(
                f"wazuh auth failed: http {resp['status']}")
        import json as _json
        doc = _json.loads(resp["body"])
        token = doc.get("data", {}).get("token")
        if not isinstance(token, str) or not token:
            raise base.AdapterUnavailable("wazuh auth: no token in response")
        return {"token": token}

    def query_alerts(self, *, token: str, limit: int = 10) -> dict:
        """Query an /alerts-compatible gateway, not the standard manager API."""
        if not isinstance(limit, int) or limit <= 0 or limit > 1000:
            raise jsonio.DataError("limit must be 1..1000")
        url = f"{self._base_url}/alerts?limit={limit}&pretty=true"
        resp = self._request(
            "GET", url, headers={"Authorization": f"Bearer {token}"})
        if resp["status"] != 200:
            raise base.AdapterUnavailable(
                f"wazuh alerts query failed: http {resp['status']}")
        import json as _json
        return _json.loads(resp["body"])
