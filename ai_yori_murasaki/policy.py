"""Policy engine for ai-yori-murasaki v1 (fail-closed).

Owns: policy load/validation, allowlist matching, actor resolution.
All failures are fail-closed: PolicyDenied (a DataError subclass) with
reason_code and exit code 4.
"""
from __future__ import annotations

import ipaddress
import json
import os
import socket
import ssl
import urllib.parse
from dataclasses import dataclass
from typing import Any, Iterable


EXIT_POLICY_DENIED = 4


class DataError(Exception):
    """Base error carrying a reason_code and exit code."""

    reason_code: str = "input_invalid"
    exit_code: int = 2

    def __init__(self, message: str = "", *, reason_code: str | None = None,
                 exit_code: int | None = None) -> None:
        super().__init__(message)
        if reason_code is not None:
            self.reason_code = reason_code
        if exit_code is not None:
            self.exit_code = exit_code


class PolicyDenied(DataError):
    """Policy denial: exit 4, reason_code policy_denied by default."""

    reason_code = "policy_denied"
    exit_code = EXIT_POLICY_DENIED

    def __init__(self, message: str = "", *, reason_code: str | None = None) -> None:
        super().__init__(message, reason_code=reason_code or "policy_denied",
                         exit_code=EXIT_POLICY_DENIED)


# ---------------------------------------------------------------------------
# Policy schema (fixed, fail-closed)
# ---------------------------------------------------------------------------

_ALLOWED_VERBS = {"GET", "POST", "PUT", "DELETE", "HEAD"}
_ROLES = {"operator", "orchestrator", "oracle", "worker-red", "worker-blue",
          "monitor", "auditor"}
_AUTO_APPLY_KEYS = {"dev", "lab", "production", "staging"}
_AUTO_APPLY_VALUES = {
    "deny",
    "allow_verified_patch_in_ephemeral_workspace",
    "approval_required",
}
_TOP_KEYS = {
    "schema_version",
    "scope",
    "authorized_targets",
    "role_permissions",
    "credential_namespaces",
    "auto_apply",
    "broker_allowlist",
}
_ALLOWLIST_KEYS = {"host", "ip", "port", "path_prefix", "verbs"}


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for k, v in pairs:
        if k in out:
            raise PolicyDenied(f"duplicate key in policy: {k!r}")
        out[k] = v
    return out


def _reject_constant(token: str) -> Any:
    raise PolicyDenied(f"non-finite number in policy: {token}")


def _require(cond: bool, msg: str) -> None:
    if not cond:
        raise PolicyDenied(msg)


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _validate_allowlist_entry(entry: Any) -> dict:
    _require(isinstance(entry, dict), "broker_allowlist entry must be object")
    _require(set(entry.keys()) == _ALLOWLIST_KEYS,
             f"broker_allowlist entry keys must be exactly {sorted(_ALLOWLIST_KEYS)}")
    host = entry["host"]
    _require(isinstance(host, str) and host != "", "allowlist host must be non-empty string")
    _require(host == host.lower(), "allowlist host must be lowercase")
    _require("://" not in host and "/" not in host and "@" not in host,
             "allowlist host must be a bare hostname")
    ip = entry["ip"]
    _require(isinstance(ip, str), "allowlist ip must be string")
    try:
        ipaddress.ip_address(ip)
    except ValueError as exc:
        raise PolicyDenied(f"allowlist ip invalid: {ip!r}") from exc
    port = entry["port"]
    _require(_is_int(port) and 1 <= port <= 65535, "allowlist port out of range")
    prefix = entry["path_prefix"]
    _require(isinstance(prefix, str) and prefix.startswith("/"),
             "allowlist path_prefix must start with '/'")
    verbs = entry["verbs"]
    _require(isinstance(verbs, list) and verbs, "allowlist verbs must be non-empty list")
    for v in verbs:
        _require(isinstance(v, str) and v in _ALLOWED_VERBS,
                 f"allowlist verb invalid: {v!r}")
    return entry


def _validate_policy(doc: Any) -> dict:
    _require(isinstance(doc, dict), "policy must be a JSON object")
    _require(set(doc.keys()) == _TOP_KEYS,
             f"policy keys must be exactly {sorted(_TOP_KEYS)}")
    _require(doc["schema_version"] == "1.0.0", "policy schema_version must be '1.0.0'")

    scope = doc["scope"]
    _require(isinstance(scope, str) and scope != "", "policy scope must be non-empty string")

    targets = doc["authorized_targets"]
    _require(isinstance(targets, list), "authorized_targets must be list")
    for t in targets:
        _require(isinstance(t, str) and t != "", "authorized_targets entries must be non-empty strings")

    role_perms = doc["role_permissions"]
    _require(isinstance(role_perms, dict), "role_permissions must be object")
    for role, perms in role_perms.items():
        _require(role in _ROLES, f"unknown role in role_permissions: {role!r}")
        _require(isinstance(perms, list), f"role_permissions[{role!r}] must be list")
        for p in perms:
            _require(isinstance(p, str) and p != "",
                     f"role_permissions[{role!r}] entries must be non-empty strings")

    creds = doc["credential_namespaces"]
    _require(isinstance(creds, list), "credential_namespaces must be list")
    for c in creds:
        _require(isinstance(c, str) and c != "",
                 "credential_namespaces entries must be non-empty strings")

    auto_apply = doc["auto_apply"]
    _require(isinstance(auto_apply, dict), "auto_apply must be object")
    _require(set(auto_apply.keys()) == _AUTO_APPLY_KEYS,
             f"auto_apply keys must be exactly {sorted(_AUTO_APPLY_KEYS)}")
    for k, v in auto_apply.items():
        _require(isinstance(v, str) and v in _AUTO_APPLY_VALUES,
                 f"auto_apply[{k!r}] invalid value: {v!r}")
    _require(auto_apply["production"] == "deny", "auto_apply.production must be 'deny'")
    _require(auto_apply["dev"] == "deny", "auto_apply.dev must be 'deny'")

    allow = doc["broker_allowlist"]
    _require(isinstance(allow, list), "broker_allowlist must be list")
    for entry in allow:
        _validate_allowlist_entry(entry)

    return doc


def load(path: str) -> dict:
    """Load and validate a policy JSON file. Fail-closed on any error."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise PolicyDenied(f"policy file unreadable: {exc}") from exc
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PolicyDenied("policy file is not valid UTF-8") from exc
    try:
        doc = json.loads(
            text,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except PolicyDenied:
        raise
    except json.JSONDecodeError as exc:
        raise PolicyDenied(f"policy JSON parse error: {exc}") from exc
    return _validate_policy(doc)


# ---------------------------------------------------------------------------
# URL normalization / allowlist matching
# ---------------------------------------------------------------------------


_BAD_URL_CHARS = ("\r", "\n", "\x00")


def _normalize_url(url: str) -> dict:
    """Parse and normalize a request URL. Reject userinfo/fragment."""
    if not isinstance(url, str) or url == "":
        raise PolicyDenied("url must be non-empty string")
    for ch in _BAD_URL_CHARS:
        if ch in url:
            raise PolicyDenied("url contains control characters")
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        raise PolicyDenied(f"url parse error: {exc}") from exc
    if parts.scheme not in ("http", "https"):
        raise PolicyDenied(f"url scheme not allowed: {parts.scheme!r}")
    if parts.username is not None or parts.password is not None:
        raise PolicyDenied("url userinfo is not allowed")
    if "@" in parts.netloc:
        raise PolicyDenied("url userinfo is not allowed")
    if parts.fragment:
        raise PolicyDenied("url fragment is not allowed")
    host = parts.hostname
    if not host:
        raise PolicyDenied("url host missing")
    host = host.lower()
    try:
        port = parts.port
    except ValueError as exc:
        raise PolicyDenied(f"url port invalid: {exc}") from exc
    if port is None:
        port = 443 if parts.scheme == "https" else 80
    path = parts.path or "/"
    if not path.startswith("/"):
        raise PolicyDenied("url path must be absolute")
    if "\\" in path:
        raise PolicyDenied("url path contains backslash")
    for seg in path.split("/"):
        if seg in (".", ".."):
            raise PolicyDenied("url path contains dot segments")
    if "%2f" in path.lower() or "%5c" in path.lower():
        raise PolicyDenied("url path contains encoded slash")
    return {
        "scheme": parts.scheme,
        "host": host,
        "port": port,
        "path": path,
        "query": parts.query,
    }


def _path_prefix_match(path: str, prefix: str) -> bool:
    """Segment-boundary prefix match: /a matches /a and /a/b but not /ab."""
    if prefix == "/":
        return True
    if path == prefix:
        return True
    if path.startswith(prefix + "/"):
        return True
    return False


def _resolve_ips(host: str) -> list[str]:
    """Resolve host to IPs. No caching (DNS rebind safe)."""
    try:
        infos = socket.getaddrinfo(host, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise PolicyDenied(f"dns resolution failed for {host!r}: {exc}") from exc
    ips: list[str] = []
    for info in infos:
        addr = info[4][0]
        if addr not in ips:
            ips.append(addr)
    if not ips:
        raise PolicyDenied(f"dns resolution returned no addresses for {host!r}")
    return ips


def _ip_matches(resolved: str, allowed: str) -> bool:
    try:
        return ipaddress.ip_address(resolved) == ipaddress.ip_address(allowed)
    except ValueError:
        return False


def check_allowlist(request: dict, allowlist: list) -> bool:
    """Check a request {url, method} against the allowlist.

    Returns True only if every check passes. Raises PolicyDenied on
    malformed input or on any mismatch (fail-closed).
    """
    if not isinstance(request, dict):
        raise PolicyDenied("request must be object")
    if set(request.keys()) != {"url", "method"}:
        raise PolicyDenied("request keys must be exactly {'url','method'}")
    method = request["method"]
    if not isinstance(method, str) or method not in _ALLOWED_VERBS:
        raise PolicyDenied(f"method not allowed: {method!r}")
    norm = _normalize_url(request["url"])
    if not isinstance(allowlist, list):
        raise PolicyDenied("allowlist must be list")

    # (1) host exact match
    candidates = [e for e in allowlist if e.get("host") == norm["host"]]
    if not candidates:
        raise PolicyDenied(f"host not in allowlist: {norm['host']!r}")

    # (2) resolved IP must match allowlist IP (no caching)
    resolved = _resolve_ips(norm["host"])
    ip_ok = [e for e in candidates if any(_ip_matches(r, e["ip"]) for r in resolved)]
    if not ip_ok:
        raise PolicyDenied(
            f"resolved IP not in allowlist for host {norm['host']!r}: {resolved}"
        )

    # (3) port match
    port_ok = [e for e in ip_ok if e["port"] == norm["port"]]
    if not port_ok:
        raise PolicyDenied(f"port not in allowlist: {norm['port']}")

    # (4) path prefix (segment boundary)
    path_ok = [e for e in port_ok if _path_prefix_match(norm["path"], e["path_prefix"])]
    if not path_ok:
        raise PolicyDenied(f"path not in allowlist: {norm['path']!r}")

    # (5) verb match
    verb_ok = [e for e in path_ok if method in e["verbs"]]
    if not verb_ok:
        raise PolicyDenied(f"verb not allowed for path: {method}")

    return True


# ---------------------------------------------------------------------------
# Actor resolution (orchestrator config only)
# ---------------------------------------------------------------------------


_ROLE_ACTOR_MAP = {
    "operator": {"agent_id": "operator", "model": None},
    "orchestrator": {"agent_id": "orchestrator", "model": None},
    "oracle": {"agent_id": "oracle", "model": None},
    "worker-red": {"agent_id": "worker-red", "model": None},
    "worker-blue": {"agent_id": "worker-blue", "model": None},
    "monitor": {"agent_id": "monitor", "model": None},
    "auditor": {"agent_id": "auditor", "model": None},
}


def resolve_actor(request: dict, policy: dict) -> dict:
    """Resolve the actor from orchestrator configuration only.

    The request may carry a role hint, but agent_id/model are taken from
    the fixed role mapping. Client-supplied agent_id/model are rejected.
    """
    if not isinstance(request, dict):
        raise PolicyDenied("actor request must be object")
    if "agent_id" in request or "model" in request:
        raise PolicyDenied("client-supplied actor identity is not trusted")
    role = request.get("role")
    if not isinstance(role, str) or role not in _ROLES:
        raise PolicyDenied(f"actor role invalid: {role!r}")
    role_perms = policy.get("role_permissions") if isinstance(policy, dict) else None
    if not isinstance(role_perms, dict) or role not in role_perms:
        raise PolicyDenied(f"role not configured in policy: {role!r}")
    perms = role_perms[role]
    if not isinstance(perms, list) or not perms:
        raise PolicyDenied(f"role has no permissions: {role!r}")
    mapping = _ROLE_ACTOR_MAP[role]
    return {"role": role, "agent_id": mapping["agent_id"], "model": mapping["model"]}


# ---------------------------------------------------------------------------
# Production write denial / lab finite operations
# ---------------------------------------------------------------------------


def check_production_write(env: str, operation: str) -> None:
    """Deny any write to production. Lab allows only finite operations."""
    if env == "production":
        raise PolicyDenied("production writes are denied")
    if env == "lab":
        if operation not in _LAB_FINITE_OPS:
            raise PolicyDenied(f"lab operation not allowed: {operation!r}")
        return
    if env == "dev":
        raise PolicyDenied("dev writes are denied")
    if env == "staging":
        raise PolicyDenied("staging writes require approval")
    raise PolicyDenied(f"unknown environment: {env!r}")


_LAB_FINITE_OPS = {
    "fixture_reset",
    "attack_executed",
    "state_captured",
    "exploit_judged",
    "static_scan",
    "telemetry_ingested",
    "oracle_replay",
    "benign_regression",
}


# ---------------------------------------------------------------------------
# TLS / connection pinning helpers (used by HttpBroker)
# ---------------------------------------------------------------------------


def pinned_connect(host: str, port: int, ip: str, timeout: float,
                   tls: bool) -> socket.socket:
    """Connect to a pre-validated IP, preserving SNI/cert for the host.

    The IP is the one validated by check_allowlist; no re-resolution
    happens here (TOCTOU-safe).
    """
    sock = socket.create_connection((ip, port), timeout=timeout)
    if not tls:
        return sock
    ctx = ssl.create_default_context()
    try:
        wrapped = ctx.wrap_socket(sock, server_hostname=host)
    except Exception:
        sock.close()
        raise
    return wrapped


def resolve_and_pin(url: str, allowlist: list, method: str) -> dict:
    """Validate a request and return pinned connection parameters.

    DNS is resolved exactly once; the validated IP is the one used for
    the connection (TOCTOU-safe).
    """
    if not isinstance(method, str) or method not in _ALLOWED_VERBS:
        raise PolicyDenied(f"method not allowed: {method!r}")
    norm = _normalize_url(url)
    if not isinstance(allowlist, list):
        raise PolicyDenied("allowlist must be list")
    candidates = [e for e in allowlist if e.get("host") == norm["host"]]
    if not candidates:
        raise PolicyDenied(f"host not in allowlist: {norm['host']!r}")
    resolved = _resolve_ips(norm["host"])
    ip_ok = [e for e in candidates if any(_ip_matches(r, e["ip"]) for r in resolved)]
    if not ip_ok:
        raise PolicyDenied(
            f"resolved IP not in allowlist for host {norm['host']!r}: {resolved}"
        )
    port_ok = [e for e in ip_ok if e["port"] == norm["port"]]
    if not port_ok:
        raise PolicyDenied(f"port not in allowlist: {norm['port']}")
    path_ok = [e for e in port_ok if _path_prefix_match(norm["path"], e["path_prefix"])]
    if not path_ok:
        raise PolicyDenied(f"path not in allowlist: {norm['path']!r}")
    verb_ok = [e for e in path_ok if method in e["verbs"]]
    if not verb_ok:
        raise PolicyDenied(f"verb not allowed for path: {method}")
    entry = verb_ok[0]
    pinned_ip = None
    for r in resolved:
        if _ip_matches(r, entry["ip"]):
            pinned_ip = r
            break
    if pinned_ip is None:
        raise PolicyDenied("no pinned IP match")
    return {
        "host": norm["host"],
        "port": norm["port"],
        "ip": pinned_ip,
        "path": norm["path"],
        "query": norm["query"],
        "scheme": norm["scheme"],
        "tls": norm["scheme"] == "https",
    }
