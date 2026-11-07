"""Adapter execution base for ai-yori-murasaki v1 (Core-owned).

Owns: subprocess launch, timeout, retry cap, max bytes, adapter_unavailable
normalization, contract.json schema validation, and the HTTP broker.

Eval owns per-adapter contract.json contents and normalizers.
"""
from __future__ import annotations

import hashlib
import json
import os
import socket
import ssl
import subprocess
import threading
import time
from typing import Any

from .. import jsonio
from .. import policy as policy_mod


ADAPTER_NAMES = {"trivy", "codeql", "wazuh", "sigma", "rustxng", "rdsh", "llm"}

CONTRACT_KEYS = {
    "name",
    "version",
    "input_ref",
    "output_ref",
    "timeout_ms",
    "retry_cap",
    "response_cap",
}

_SHA256_RE = None


def _sha256_hex(s: str) -> bool:
    if not isinstance(s, str) or len(s) != 64:
        return False
    return all(c in "0123456789abcdef" for c in s)


class ContractError(Exception):
    """Raised when a contract.json fails schema validation."""


class AdapterUnavailable(Exception):
    """Raised when an adapter cannot be executed (normalized failure)."""

    reason_code = "adapter_unavailable"


# ---------------------------------------------------------------------------
# Contract loading
# ---------------------------------------------------------------------------


def _contracts_dir() -> str:
    return os.path.join(os.path.dirname(os.path.abspath(__file__)), "contracts")


def _validate_contract(doc: Any, name: str) -> dict:
    if not isinstance(doc, dict):
        raise ContractError("contract must be a JSON object")
    if set(doc.keys()) != CONTRACT_KEYS:
        raise ContractError(
            f"contract keys must be exactly {sorted(CONTRACT_KEYS)}"
        )
    if doc["name"] != name:
        raise ContractError(f"contract name mismatch: {doc['name']!r} != {name!r}")
    if doc["name"] not in ADAPTER_NAMES:
        raise ContractError(f"unknown adapter name: {doc['name']!r}")
    if not isinstance(doc["version"], str) or not doc["version"]:
        raise ContractError("contract version must be non-empty string")
    if not _sha256_hex(doc["input_ref"]):
        raise ContractError("contract input_ref must be sha256 hex")
    if not _sha256_hex(doc["output_ref"]):
        raise ContractError("contract output_ref must be sha256 hex")
    t = doc["timeout_ms"]
    if not isinstance(t, int) or isinstance(t, bool) or not (1 <= t <= 300000):
        raise ContractError("contract timeout_ms out of range")
    r = doc["retry_cap"]
    if not isinstance(r, int) or isinstance(r, bool) or not (0 <= r <= 2):
        raise ContractError("contract retry_cap out of range")
    c = doc["response_cap"]
    if not isinstance(c, int) or isinstance(c, bool) or not (1 <= c <= 52428800):
        raise ContractError("contract response_cap out of range")
    return doc


def load_contract(name: str) -> dict:
    """Load and schema-validate adapters/contracts/<name>.json."""
    if not isinstance(name, str) or name not in ADAPTER_NAMES:
        raise ContractError(f"unknown adapter name: {name!r}")
    path = os.path.join(_contracts_dir(), f"{name}.json")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        raise ContractError(f"contract unreadable for {name!r}: {exc}") from exc
    if len(raw) > 65536:
        raise ContractError(f"contract too large for {name!r}")
    try:
        doc = jsonio.loads(raw)
    except jsonio.DataError as exc:
        raise ContractError(f"contract parse error for {name!r}: {exc}") from exc
    doc = _validate_contract(doc, name)
    from .schema_contracts import SCHEMAS
    schemas = SCHEMAS.get(name)
    if schemas is None:
        raise ContractError(f"no schema for adapter {name!r}")
    if doc["input_ref"] != _digest_schema(schemas["input"]):
        raise ContractError(f"input_ref digest mismatch for {name!r}")
    if doc["output_ref"] != _digest_schema(schemas["output"]):
        raise ContractError(f"output_ref digest mismatch for {name!r}")
    return doc


def _digest_schema(schema: Any) -> str:
    canonical = json.dumps(schema, ensure_ascii=False, sort_keys=True,
                           separators=(",", ":"), allow_nan=False).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


# ---------------------------------------------------------------------------
# HTTP broker
# ---------------------------------------------------------------------------


class HttpBroker:
    """Policy-enforcing HTTP broker.

    Validates every request against the policy allowlist, pins the
    connection to the validated IP (no re-resolution), and enforces
    response size limits.
    """

    def __init__(self, policy: dict) -> None:
        if not isinstance(policy, dict):
            raise policy_mod.PolicyDenied("policy must be object")
        allow = policy.get("broker_allowlist")
        if not isinstance(allow, list):
            raise policy_mod.PolicyDenied("policy broker_allowlist missing")
        self._policy = policy
        self._allowlist = allow

    def request(self, method: str, url: str, body: bytes | None = None,
                headers: dict | None = None, timeout: float = 10.0,
                max_bytes: int = 1048576) -> dict:
        """Perform a policy-checked HTTP request.

        Returns {status, headers, body(bytes)}. Redirects are denied.
        """
        if not isinstance(method, str) or method not in policy_mod._ALLOWED_VERBS:
            raise policy_mod.PolicyDenied(f"method not allowed: {method!r}")
        if not isinstance(max_bytes, int) or max_bytes <= 0:
            raise policy_mod.PolicyDenied("max_bytes must be positive int")
        if not isinstance(timeout, (int, float)) or timeout <= 0:
            raise policy_mod.PolicyDenied("timeout must be positive")
        pin = policy_mod.resolve_and_pin(url, self._allowlist, method)
        return self._do_request(pin, method, body, headers, float(timeout), max_bytes)

    def _do_request(self, pin: dict, method: str, body: bytes | None,
                    headers: dict | None, timeout: float,
                    max_bytes: int) -> dict:
        host = pin["host"]
        port = pin["port"]
        ip = pin["ip"]
        tls = pin["tls"]
        path = pin["path"]
        if pin["query"]:
            path = path + "?" + pin["query"]

        sock = policy_mod.pinned_connect(host, port, ip, timeout, tls)
        try:
            sock.settimeout(timeout)
            req_headers = {
                "Host": host if port in (80, 443) else f"{host}:{port}",
                "Connection": "close",
                "Accept": "*/*",
            }
            if headers:
                for k, v in headers.items():
                    if not isinstance(k, str) or not isinstance(v, str):
                        raise policy_mod.PolicyDenied("header keys/values must be strings")
                    if any(c in k for c in ("\r", "\n", "\x00")) or any(
                            c in v for c in ("\r", "\n", "\x00")):
                        raise policy_mod.PolicyDenied("header contains control characters")
                    lk = k.lower()
                    if lk in ("host", "connection"):
                        continue
                    req_headers[k] = v
            if body is not None:
                if not isinstance(body, (bytes, bytearray)):
                    raise policy_mod.PolicyDenied("body must be bytes")
                req_headers["Content-Length"] = str(len(body))
            lines = [f"{method} {path} HTTP/1.1"]
            for k, v in req_headers.items():
                lines.append(f"{k}: {v}")
            lines.append("")
            lines.append("")
            raw = "\r\n".join(lines).encode("latin-1")
            if body is not None:
                raw += bytes(body)
            sock.sendall(raw)
            return _read_response(sock, max_bytes)
        finally:
            try:
                sock.close()
            except OSError:
                pass


def _read_response(sock: socket.socket, max_bytes: int) -> dict:
    buf = bytearray()
    header_end = -1
    while header_end < 0:
        chunk = sock.recv(65536)
        if not chunk:
            raise policy_mod.PolicyDenied("connection closed before headers")
        buf.extend(chunk)
        header_end = buf.find(b"\r\n\r\n")
        if header_end < 0 and len(buf) > 65536:
            raise policy_mod.PolicyDenied("response headers too large")
    if header_end > 65536:
        raise policy_mod.PolicyDenied("response headers too large")
    head = bytes(buf[:header_end]).decode("latin-1")
    rest = bytes(buf[header_end + 4:])
    lines = head.split("\r\n")
    status_line = lines[0]
    parts = status_line.split(" ", 2)
    if len(parts) < 2 or not parts[0].startswith("HTTP/"):
        raise policy_mod.PolicyDenied("malformed status line")
    try:
        status = int(parts[1])
    except ValueError as exc:
        raise policy_mod.PolicyDenied("malformed status code") from exc
    if 300 <= status < 400:
        raise policy_mod.PolicyDenied("redirects are denied")
    resp_headers: dict = {}
    for line in lines[1:]:
        if not line:
            continue
        if ":" not in line:
            raise policy_mod.PolicyDenied("malformed header line")
        k, v = line.split(":", 1)
        resp_headers[k.strip().lower()] = v.strip()

    te = resp_headers.get("transfer-encoding")
    cl = resp_headers.get("content-length")
    if te is not None and cl is not None:
        raise policy_mod.PolicyDenied("content-length and transfer-encoding both present")
    if te is not None:
        if te.strip().lower() != "chunked":
            raise policy_mod.PolicyDenied(f"transfer-encoding not allowed: {te!r}")
        body = _read_chunked(sock, rest, max_bytes)
        return {"status": status, "headers": resp_headers, "body": body}

    body = bytearray(rest)
    if len(body) > max_bytes:
        raise policy_mod.PolicyDenied("response exceeds max_bytes")

    if cl is not None:
        try:
            clen = int(cl)
        except ValueError as exc:
            raise policy_mod.PolicyDenied("malformed content-length") from exc
        if clen < 0:
            raise policy_mod.PolicyDenied("negative content-length")
        if clen > max_bytes:
            raise policy_mod.PolicyDenied("response exceeds max_bytes")
        while len(body) < clen:
            chunk = sock.recv(min(65536, clen - len(body)))
            if not chunk:
                raise AdapterUnavailable("connection closed before content-length")
            body.extend(chunk)
            if len(body) > max_bytes:
                raise policy_mod.PolicyDenied("response exceeds max_bytes")
        if len(body) != clen:
            raise AdapterUnavailable("content-length mismatch")
    else:
        while True:
            chunk = sock.recv(65536)
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > max_bytes:
                raise policy_mod.PolicyDenied("response exceeds max_bytes")

    return {"status": status, "headers": resp_headers, "body": bytes(body)}


def _read_chunked(sock: socket.socket, initial: bytes, max_bytes: int) -> bytes:
    buf = bytearray(initial)
    out = bytearray()

    def _fill() -> bool:
        chunk = sock.recv(65536)
        if not chunk:
            return False
        buf.extend(chunk)
        return True

    while True:
        while b"\r\n" not in buf:
            if not _fill():
                raise AdapterUnavailable("chunked stream truncated")
        idx = buf.find(b"\r\n")
        size_line = bytes(buf[:idx])
        del buf[:idx + 2]
        try:
            size = int(size_line.split(b";", 1)[0].strip(), 16)
        except ValueError as exc:
            raise policy_mod.PolicyDenied("malformed chunk size") from exc
        if size < 0:
            raise policy_mod.PolicyDenied("negative chunk size")
        if size == 0:
            # consume trailing CRLF (and optional trailers ignored)
            while b"\r\n" not in buf:
                if not _fill():
                    break
            return bytes(out)
        while len(buf) < size + 2:
            if not _fill():
                raise AdapterUnavailable("chunked stream truncated")
        out.extend(buf[:size])
        if len(out) > max_bytes:
            raise policy_mod.PolicyDenied("response exceeds max_bytes")
        if bytes(buf[size:size + 2]) != b"\r\n":
            raise policy_mod.PolicyDenied("malformed chunk terminator")
        del buf[:size + 2]


# ---------------------------------------------------------------------------
# Docker command runner (finite argv only)
# ---------------------------------------------------------------------------

_DOCKER_CONTAINER_OPS = {"inspect", "logs", "stop", "rm"}
_DOCKER_NETWORK_OPS = {"create", "inspect", "rm"}
_DOCKER_NETWORK_CREATE_FLAGS = {"--internal", "--label"}
_DOCKER_NETWORK_LABEL = "ai-yori-murasaki=lab"
_DOCKER_RUN_FLAGS_WITH_VALUE = {
    "--name", "--label", "--network", "--user", "--cap-drop",
    "--security-opt", "--pids-limit", "--memory", "--cpus", "--mount",
}
_DOCKER_RUN_BOOL_FLAGS = {"--rm", "--read-only"}
_DOCKER_RUN_ALLOWED_FLAGS = _DOCKER_RUN_FLAGS_WITH_VALUE | _DOCKER_RUN_BOOL_FLAGS
_DOCKER_FORBIDDEN_FLAGS = {
    "--privileged", "--pid", "--ipc", "--uts", "--userns", "--device",
    "--env", "-e", "--env-file", "--volume", "-v", "--mount-from",
    "--net", "--network-alias", "--add-host", "--dns", "--dns-search",
    "--entrypoint", "--workdir", "-w", "--hostname", "-h",
    "--cap-add", "--security-opt=seccomp=unconfined",
}


def _validate_docker_argv(argv: list, *, staging_root: str | None = None,
                          allowed_images: Iterable[str] | None = None,
                          allowed_names: Iterable[str] | None = None) -> None:
    if not isinstance(argv, list) or not argv:
        raise policy_mod.PolicyDenied("docker argv must be non-empty list")
    if argv[0] != "docker":
        raise policy_mod.PolicyDenied("only docker commands are allowed")
    if len(argv) < 2:
        raise policy_mod.PolicyDenied("docker subcommand missing")
    sub = argv[1]
    if sub == "run":
        _validate_docker_run(argv, staging_root=staging_root,
                             allowed_images=allowed_images)
        return
    if sub in _DOCKER_CONTAINER_OPS:
        _validate_docker_container_op(argv, allowed_names=allowed_names)
        return
    if sub == "network":
        _validate_docker_network(argv, allowed_names=allowed_names)
        return
    raise policy_mod.PolicyDenied(f"docker subcommand not allowed: {sub!r}")


def _validate_docker_network(argv: list, *,
                             allowed_names: Iterable[str] | None = None) -> None:
    # argv: docker network <create|inspect|rm> ...
    if len(argv) < 3:
        raise policy_mod.PolicyDenied("docker network op missing")
    for a in argv:
        if not isinstance(a, str):
            raise policy_mod.PolicyDenied("docker argv entries must be strings")
        if "\x00" in a:
            raise policy_mod.PolicyDenied("docker argv contains NUL")
    if allowed_names is None:
        raise policy_mod.PolicyDenied("network op requires allowed_names")
    allowed = set(allowed_names)
    op = argv[2]
    if op not in _DOCKER_NETWORK_OPS:
        raise policy_mod.PolicyDenied(f"docker network op not allowed: {op!r}")
    if op == "create":
        i = 3
        seen = set()
        while i < len(argv):
            a = argv[i]
            if not a.startswith("-"):
                break
            flag = a
            val = None
            if "=" in a:
                flag, val = a.split("=", 1)
            if flag not in _DOCKER_NETWORK_CREATE_FLAGS:
                raise policy_mod.PolicyDenied(
                    f"docker network create flag not allowed: {flag!r}")
            if flag == "--internal":
                if val is not None:
                    raise policy_mod.PolicyDenied(
                        "docker network create --internal takes no value")
                seen.add(flag)
                i += 1
                continue
            if val is None:
                if i + 1 >= len(argv):
                    raise policy_mod.PolicyDenied(
                        f"docker network create flag missing value: {flag!r}")
                val = argv[i + 1]
                i += 2
            else:
                i += 1
            if flag == "--label":
                if val != _DOCKER_NETWORK_LABEL:
                    raise policy_mod.PolicyDenied(
                        f"docker network create label not allowed: {val!r}")
            seen.add(flag)
        if "--internal" not in seen:
            raise policy_mod.PolicyDenied(
                "docker network create missing required flag: '--internal'")
        if "--label" not in seen:
            raise policy_mod.PolicyDenied(
                "docker network create missing required flag: '--label'")
        if i >= len(argv):
            raise policy_mod.PolicyDenied("docker network create missing name")
        if i != len(argv) - 1:
            raise policy_mod.PolicyDenied("docker network create extra arguments")
        name = argv[i]
        if not name.startswith("aym-"):
            raise policy_mod.PolicyDenied(
                f"docker network name not allowed: {name!r}")
        if name not in allowed:
            raise policy_mod.PolicyDenied(
                f"docker network name not in allowed_names: {name!r}")
        return
    # inspect / rm: docker network <op> <name> [<name>...]
    if len(argv) < 4:
        raise policy_mod.PolicyDenied("docker network op requires name")
    for name in argv[3:]:
        if not name.startswith("aym-"):
            raise policy_mod.PolicyDenied(
                f"docker network name not allowed: {name!r}")
        if name not in allowed:
            raise policy_mod.PolicyDenied(
                f"docker network name not in allowed_names: {name!r}")


def _validate_docker_container_op(argv: list, *,
                                  allowed_names: Iterable[str] | None = None) -> None:
    # argv: docker <op> <name> [<name>...]
    if len(argv) < 3:
        raise policy_mod.PolicyDenied("container op requires allowed_names")
    for a in argv:
        if not isinstance(a, str):
            raise policy_mod.PolicyDenied("docker argv entries must be strings")
        if "\x00" in a:
            raise policy_mod.PolicyDenied("docker argv contains NUL")
    if allowed_names is None:
        raise policy_mod.PolicyDenied("container op requires allowed_names")
    allowed = set(allowed_names)
    for name in argv[2:]:
        if not name.startswith("aym-"):
            raise policy_mod.PolicyDenied(f"container name not allowed: {name!r}")
        if name not in allowed:
            raise policy_mod.PolicyDenied(f"container name not in allowed_names: {name!r}")


def _validate_docker_run(argv: list, *, staging_root: str | None = None,
                         allowed_images: Iterable[str] | None = None) -> None:
    # argv: docker run <flags...> <image> <cmd...>
    if len(argv) < 3:
        raise policy_mod.PolicyDenied("docker run requires flags and image")
    i = 2
    seen = set()
    while i < len(argv):
        a = argv[i]
        if not isinstance(a, str):
            raise policy_mod.PolicyDenied("docker argv entries must be strings")
        if "\x00" in a:
            raise policy_mod.PolicyDenied("docker argv contains NUL")
        if not a.startswith("-"):
            break
        flag = a
        val = None
        if "=" in a:
            flag, val = a.split("=", 1)
        if flag in _DOCKER_FORBIDDEN_FLAGS:
            raise policy_mod.PolicyDenied(f"docker run flag forbidden: {flag!r}")
        if flag not in _DOCKER_RUN_ALLOWED_FLAGS:
            raise policy_mod.PolicyDenied(f"docker run flag not allowed: {flag!r}")
        if flag in _DOCKER_RUN_BOOL_FLAGS:
            if val is not None:
                raise policy_mod.PolicyDenied(f"docker run flag takes no value: {flag!r}")
            seen.add(flag)
            i += 1
            continue
        if val is None:
            if i + 1 >= len(argv):
                raise policy_mod.PolicyDenied(f"docker run flag missing value: {flag!r}")
            val = argv[i + 1]
            i += 2
        else:
            i += 1
        if flag == "--network":
            if val not in ("none",) and not val.startswith("aym-"):
                raise policy_mod.PolicyDenied(f"docker run network not allowed: {val!r}")
        elif flag == "--user":
            if val != "65534:65534":
                raise policy_mod.PolicyDenied(f"docker run user not allowed: {val!r}")
        elif flag == "--cap-drop":
            if val != "ALL":
                raise policy_mod.PolicyDenied(f"docker run cap-drop not allowed: {val!r}")
        elif flag == "--security-opt":
            if val != "no-new-privileges":
                raise policy_mod.PolicyDenied(f"docker run security-opt not allowed: {val!r}")
        elif flag == "--pids-limit":
            if val != "64":
                raise policy_mod.PolicyDenied(f"docker run pids-limit not allowed: {val!r}")
        elif flag == "--memory":
            if val != "128m":
                raise policy_mod.PolicyDenied(f"docker run memory not allowed: {val!r}")
        elif flag == "--cpus":
            if val != "1":
                raise policy_mod.PolicyDenied(f"docker run cpus not allowed: {val!r}")
        elif flag == "--name":
            if not val.startswith("aym-"):
                raise policy_mod.PolicyDenied(f"docker run name not allowed: {val!r}")
        elif flag == "--label":
            if val != "ai-yori-murasaki=lab":
                raise policy_mod.PolicyDenied(f"docker run label not allowed: {val!r}")
        elif flag == "--mount":
            _validate_docker_mount(val, staging_root=staging_root)
        elif flag == "--read-only":
            pass
        seen.add(flag)
    if i >= len(argv):
        raise policy_mod.PolicyDenied("docker run missing image")
    image = argv[i]
    if not isinstance(image, str) or not image:
        raise policy_mod.PolicyDenied("docker run image invalid")
    if allowed_images is None:
        raise policy_mod.PolicyDenied("docker run requires allowed_images")
    if image not in set(allowed_images):
        raise policy_mod.PolicyDenied(f"docker run image not allowed: {image!r}")
    i += 1
    if i >= len(argv):
        raise policy_mod.PolicyDenied("docker run missing command")
    if argv[i] != "python":
        raise policy_mod.PolicyDenied(f"docker run command must be python: {argv[i]!r}")
    i += 1
    if i >= len(argv):
        raise policy_mod.PolicyDenied("docker run missing script path")
    script = argv[i]
    if not isinstance(script, str) or not script.startswith("/app/"):
        raise policy_mod.PolicyDenied(f"docker run script must be under /app: {script!r}")
    if ".." in script.split("/"):
        raise policy_mod.PolicyDenied("docker run script contains dot segments")
    for required in ("--rm", "--read-only", "--name", "--label", "--network",
                     "--user", "--cap-drop", "--security-opt", "--pids-limit",
                     "--memory", "--cpus", "--mount"):
        if required not in seen:
            raise policy_mod.PolicyDenied(f"docker run missing required flag: {required!r}")


_DOCKER_MOUNT_ALLOWED_KEYS = {"type", "source", "target", "readonly"}


def _is_absolute_path(p: str) -> bool:
    """Accept POSIX absolute and native Windows absolute paths."""
    if not isinstance(p, str) or not p:
        return False
    if p.startswith("/"):
        return True
    try:
        from pathlib import Path
        return Path(p).is_absolute()
    except (ValueError, OSError):
        return False


def _has_dot_segments(p: str) -> bool:
    norm = p.replace("\\", "/")
    return ".." in norm.split("/")


def _reject_symlink_ancestors(path: str) -> None:
    """Reject any symlink/reparse-point ancestor of path."""
    try:
        from pathlib import Path
        cur = Path(path)
    except (ValueError, OSError) as exc:
        raise policy_mod.PolicyDenied(f"docker run mount path invalid: {path!r}") from exc
    parts = []
    while True:
        parts.append(cur)
        parent = cur.parent
        if parent == cur:
            break
        cur = parent
    for p in reversed(parts):
        try:
            if p.is_symlink():
                raise policy_mod.PolicyDenied(
                    f"docker run mount path has symlink ancestor: {str(p)!r}")
        except OSError as exc:
            raise policy_mod.PolicyDenied(
                f"docker run mount path unreadable: {str(p)!r}") from exc


def _validate_docker_mount(val: str, *, staging_root: str | None = None) -> None:
    if not isinstance(val, str) or not val:
        raise policy_mod.PolicyDenied("docker run mount malformed")
    parts = val.split(",")
    kv = {}
    for p in parts:
        if "=" not in p:
            raise policy_mod.PolicyDenied(f"docker run mount malformed: {val!r}")
        k, v = p.split("=", 1)
        if k not in _DOCKER_MOUNT_ALLOWED_KEYS:
            raise policy_mod.PolicyDenied(
                f"docker run mount key not allowed: {k!r}")
        if k in kv:
            raise policy_mod.PolicyDenied(
                f"docker run mount duplicate key: {k!r}")
        kv[k] = v
    if set(kv.keys()) != _DOCKER_MOUNT_ALLOWED_KEYS:
        raise policy_mod.PolicyDenied(
            f"docker run mount keys must be exactly {sorted(_DOCKER_MOUNT_ALLOWED_KEYS)}")
    if kv["type"] != "bind":
        raise policy_mod.PolicyDenied("docker run mount type must be bind")
    if kv["target"] != "/app":
        raise policy_mod.PolicyDenied("docker run mount target must be /app")
    if kv["readonly"] not in ("true", "1"):
        raise policy_mod.PolicyDenied("docker run mount must be readonly")
    src = kv["source"]
    if not _is_absolute_path(src):
        raise policy_mod.PolicyDenied("docker run mount source must be absolute")
    if _has_dot_segments(src):
        raise policy_mod.PolicyDenied("docker run mount source contains dot segments")
    if staging_root is None:
        raise policy_mod.PolicyDenied("docker run mount requires staging_root")
    if not _is_absolute_path(staging_root):
        raise policy_mod.PolicyDenied("docker run mount staging_root must be absolute")
    _reject_symlink_ancestors(src)
    _reject_symlink_ancestors(staging_root)
    root = os.path.realpath(staging_root)
    real_src = os.path.realpath(src)
    try:
        common = os.path.commonpath([root, real_src])
    except ValueError as exc:
        raise policy_mod.PolicyDenied(
            f"docker run mount source outside staging_root: {src!r}") from exc
    if common != root:
        raise policy_mod.PolicyDenied(
            f"docker run mount source outside staging_root: {src!r}")


def run_docker(argv: list, timeout: float = 30.0, max_bytes: int = 1048576,
               retry_cap: int = 0, *, staging_root: str | None = None,
               allowed_images: Iterable[str] | None = None,
               allowed_names: Iterable[str] | None = None) -> dict:
    """Run a finite docker argv with shell=False, timeout, output cap."""
    _validate_docker_argv(argv, staging_root=staging_root,
                          allowed_images=allowed_images,
                          allowed_names=allowed_names)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise policy_mod.PolicyDenied("timeout must be positive")
    if not isinstance(max_bytes, int) or max_bytes <= 0:
        raise policy_mod.PolicyDenied("max_bytes must be positive")
    if not isinstance(retry_cap, int) or not (0 <= retry_cap <= 2):
        raise policy_mod.PolicyDenied("retry_cap out of range")

    attempts = retry_cap + 1
    last_err: Exception | None = None
    for _ in range(attempts):
        try:
            return _run_once(argv, float(timeout), max_bytes)
        except AdapterUnavailable as exc:
            last_err = exc
            continue
    raise AdapterUnavailable(str(last_err) if last_err else "docker failed")


def _run_once(argv: list, timeout: float, max_bytes: int) -> dict:
    try:
        proc = subprocess.Popen(
            argv,
            shell=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
    except OSError as exc:
        raise AdapterUnavailable(f"docker launch failed: {exc}") from exc

    out_buf = bytearray()
    err_buf = bytearray()
    overflow = threading.Event()

    def _pump(stream, buf):
        try:
            while True:
                chunk = stream.read(65536)
                if not chunk:
                    break
                buf.extend(chunk)
                if len(buf) > max_bytes:
                    overflow.set()
                    try:
                        stream.close()
                    except Exception:
                        pass
                    break
        except Exception:
            pass

    t_out = threading.Thread(target=_pump, args=(proc.stdout, out_buf), daemon=True)
    t_err = threading.Thread(target=_pump, args=(proc.stderr, err_buf), daemon=True)
    t_out.start()
    t_err.start()

    deadline = time.monotonic() + timeout
    timed_out = False
    while proc.poll() is None:
        if overflow.is_set():
            break
        if time.monotonic() > deadline:
            timed_out = True
            break
        time.sleep(0.01)

    if overflow.is_set() or timed_out:
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            try:
                proc.kill()
            except OSError:
                pass
            proc.wait()
        if overflow.is_set():
            raise AdapterUnavailable("docker output exceeded max_bytes")
        raise AdapterUnavailable("docker command timed out")

    proc.wait()
    t_out.join(timeout=2.0)
    t_err.join(timeout=2.0)
    if t_out.is_alive() or t_err.is_alive():
        raise AdapterUnavailable("docker output pump did not finish")
    if len(out_buf) > max_bytes or len(err_buf) > max_bytes:
        raise AdapterUnavailable("docker output exceeded max_bytes")
    return {
        "returncode": proc.returncode,
        "stdout": bytes(out_buf),
        "stderr": bytes(err_buf),
    }


# ---------------------------------------------------------------------------
# run_adapter (common executor)
# ---------------------------------------------------------------------------


def run_adapter(name: str, request: dict) -> dict:
    """Run an adapter via the common executor.

    Loads the contract, applies timeout/retry/max bytes, and normalizes
    failures to adapter_unavailable. The normalizer is owned by Eval and
    is invoked via request['normalizer'] if provided.
    """
    if not isinstance(name, str) or name not in ADAPTER_NAMES:
        raise policy_mod.PolicyDenied(f"unknown adapter: {name!r}")
    if not isinstance(request, dict):
        raise policy_mod.PolicyDenied("request must be object")
    # run_adapter never executes docker directly. Only a trusted worker
    # (which calls run_docker itself) may execute; self-asserted
    # request['trusted'] is not accepted here.
    raise policy_mod.PolicyDenied(
        "run_adapter does not execute docker; trusted worker must call run_docker")


def _build_docker_argv_from_plan(plan: dict) -> list:
    """Build a fixed finite docker run argv from a trusted structured plan.

    plan keys: id, image, network, staged_source, script, args(list[str]).
    """
    required = {"id", "image", "network", "staged_source", "script", "args"}
    if set(plan.keys()) != required:
        raise policy_mod.PolicyDenied(
            f"plan keys must be exactly {sorted(required)}"
        )
    pid = plan["id"]
    if not isinstance(pid, str) or not pid or not pid.replace("-", "").isalnum():
        raise policy_mod.PolicyDenied("plan id invalid")
    image = plan["image"]
    if not isinstance(image, str) or not image:
        raise policy_mod.PolicyDenied("plan image invalid")
    network = plan["network"]
    if not isinstance(network, str) or (network != "none" and not network.startswith("aym-")):
        raise policy_mod.PolicyDenied(f"plan network invalid: {network!r}")
    src = plan["staged_source"]
    if not _is_absolute_path(src):
        raise policy_mod.PolicyDenied("plan staged_source must be absolute")
    if _has_dot_segments(src):
        raise policy_mod.PolicyDenied("plan staged_source contains dot segments")
    script = plan["script"]
    if not isinstance(script, str) or not script.startswith("/app/"):
        raise policy_mod.PolicyDenied("plan script must be under /app")
    if ".." in script.split("/"):
        raise policy_mod.PolicyDenied("plan script contains dot segments")
    args = plan["args"]
    if not isinstance(args, list):
        raise policy_mod.PolicyDenied("plan args must be list")
    for a in args:
        if not isinstance(a, str) or "\x00" in a:
            raise policy_mod.PolicyDenied("plan args invalid")
    name = f"aym-{pid}"
    argv = [
        "docker", "run", "--rm",
        "--name", name,
        "--label", "ai-yori-murasaki=lab",
        "--network", network,
        "--user", "65534:65534",
        "--read-only",
        "--cap-drop", "ALL",
        "--security-opt", "no-new-privileges",
        "--pids-limit", "64",
        "--memory", "128m",
        "--cpus", "1",
        "--mount", f"type=bind,source={src},target=/app,readonly=true",
        image, "python", script,
    ] + list(args)
    return argv
