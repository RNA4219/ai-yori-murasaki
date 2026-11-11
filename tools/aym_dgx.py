"""Private-configuration launcher for an OpenAI-compatible LLM endpoint.

No hostnames, usernames, workstation paths, or credentials are bundled.
The local policy is limited to the configured chat-completions endpoint.
"""
from __future__ import annotations

import json
import os
import socket
import stat
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlsplit

# Allow direct invocation from the repository checkout without editable install.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from ai_yori_murasaki.adapters.llm import _validate_configured_url


_GLOBAL_WITH_VALUE = frozenset({"--workspace", "--trusted-root", "--policy"})
_DGX_GROUPS = frozenset({"lab", "rsi"})


def _runtime_root() -> Path:
    configured = os.environ.get("AYM_RUNTIME_ROOT", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parent.parent.parent /
            ".local-runtime" / "ai-yori-murasaki")


def _make_runtime_dirs(root: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for name in ("workspace", "trusted", "staging", "config", "logs"):
        directory = root / name
        if directory.is_symlink():
            raise ValueError("runtime directory must not be a symlink")
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(directory, stat.S_IRWXU)
        result[name] = directory
    return result


def _has_option(argv: list[str], option: str) -> bool:
    return any(arg == option or arg.startswith(option + "=") for arg in argv)


def _command_index(argv: list[str]) -> int | None:
    i = 0
    while i < len(argv):
        arg = argv[i]
        if arg in _GLOBAL_WITH_VALUE:
            i += 2
        elif any(arg.startswith(option + "=") for option in _GLOBAL_WITH_VALUE):
            i += 1
        elif arg.startswith("-"):
            i += 1
        else:
            return i
    return None


def _resolve_ip(host: str, port: int) -> str:
    addresses = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if not addresses:
        raise ValueError("configured LLM host does not resolve")
    # Prefer IPv4 for broad Docker/macOS compatibility; IPv6 if IPv4 is absent.
    addresses.sort(key=lambda item: item[0] != socket.AF_INET)
    return addresses[0][4][0]


def _expected_entry(url: str) -> dict:
    _validate_configured_url(url)
    parts = urlsplit(url)
    assert parts.hostname is not None
    host = parts.hostname.lower()
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return {
        "host": host,
        "ip": _resolve_ip(host, port),
        "port": port,
        "path_prefix": parts.path,
        "verbs": ["POST"],
    }


def _ensure_policy(policy_path: Path, url: str) -> None:
    """Create a least-privilege policy, or reject stale/untrusted existing policy."""
    entry = _expected_entry(url)
    if policy_path.is_symlink():
        raise ValueError("policy file must not be a symlink")
    if policy_path.exists():
        existing = json.loads(policy_path.read_text(encoding="utf-8"))
        if existing.get("broker_allowlist") != [entry]:
            raise ValueError("existing broker policy differs; review and regenerate it")
        if policy_path.stat().st_mode & 0o077:
            raise ValueError("existing broker policy permissions must be 0600")
        return

    default_policy = (Path(__file__).resolve().parent.parent /
                      "ai_yori_murasaki" / "default-policy.json")
    policy = json.loads(default_policy.read_text(encoding="utf-8"))
    policy["broker_allowlist"] = [entry]
    payload = (json.dumps(policy, indent=2, ensure_ascii=False) + "\n").encode()
    # O_EXCL refuses races; 0600 from the first write; no secrets in this file.
    fd = os.open(policy_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(payload)


def _read_keychain() -> str:
    service = os.environ.get("AYM_LLM_KEYCHAIN_SERVICE", "").strip()
    account = os.environ.get("AYM_LLM_KEYCHAIN_ACCOUNT", "").strip()
    if not service:
        return ""
    if not account:
        raise ValueError("AYM_LLM_KEYCHAIN_ACCOUNT is required with Keychain")
    if sys.platform != "darwin":
        raise ValueError("macOS Keychain is supported only on macOS")
    result = subprocess.run(
        ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
        stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
        check=False, timeout=15,
    )
    if result.returncode != 0:
        raise ValueError("could not read the configured Keychain credential")
    return result.stdout.decode("utf-8").strip()


def _configure_llm() -> str:
    from ai_yori_murasaki.adapters.llm import _validate_and_normalize_key

    url = os.environ.get("AYM_LLM_URL", "")
    model = os.environ.get("AYM_LLM_MODEL", "")
    _validate_configured_url(url)
    if (not model or model != model.strip() or len(model) > 200
            or any(ord(ch) < 32 or ord(ch) == 127 for ch in model)):
        raise ValueError("AYM_LLM_MODEL must be a valid nonempty model name")

    key = os.environ.get("AYM_LLM_API_KEY", "")
    if not key:
        key = _read_keychain()
    if not key and not (urlsplit(url).hostname in {"localhost", "127.0.0.1", "::1"}):
        raise ValueError("remote LLM requires an API key")
    if key:
        # Reject injected control characters before normalizing whitespace.
        if any(ch in key for ch in "\r\n\t"):
            raise ValueError("invalid API key format")
        # Avoid the secret in CLI args, config files, exceptions, or debug output.
        auth = _validate_and_normalize_key(key.strip())
        os.environ["AYM_LLM_API_KEY"] = auth
        secrets = [s for s in os.environ.get("AYM_SECRETS", "").split("\n") if s]
        secrets.extend([key, key.strip(), auth, auth.removeprefix("Bearer ")])
        os.environ["AYM_SECRETS"] = "\n".join(dict.fromkeys(s for s in secrets if s))
    return url


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        argv = ["--help"]
    if "--help" in argv or "-h" in argv:
        from ai_yori_murasaki import cli
        return cli.main(argv)

    try:
        dirs = _make_runtime_dirs(_runtime_root())
        os.environ.setdefault("AYM_WORKSPACE", str(dirs["workspace"]))
        os.environ.setdefault("AYM_TRUSTED_ROOT", str(dirs["trusted"]))

        index = _command_index(argv)
        command = argv[index] if index is not None else ""
        if command in _DGX_GROUPS:
            url = _configure_llm()
            if command == "lab" and index is not None:
                sub = argv[index + 1:]
                sub_index = _command_index(sub)
                action = sub[sub_index] if sub_index is not None else ""
                if action in ("demo", "iterate") and not _has_option(argv, "--staging-root"):
                    pos = index + 1 + sub_index + 1
                    argv[pos:pos] = ["--staging-root", str(dirs["staging"])]
        else:
            url = os.environ.get("AYM_LLM_URL", "")

        if not _has_option(argv, "--policy"):
            policy_file = dirs["config"] / "broker-policy.json"
            if url:
                _ensure_policy(policy_file, url)
            else:
                # No remote endpoint is configured: use the existing default
                # Ollama policy instead of manufacturing an allowlist.
                policy_file = (Path(__file__).resolve().parent.parent /
                               "ai_yori_murasaki" / "default-policy.json")
            argv[:0] = ["--policy", str(policy_file)]
    except (OSError, ValueError, TimeoutError, json.JSONDecodeError):
        print("aym-dgx: invalid configuration or runtime policy", file=sys.stderr)
        return 2

    from ai_yori_murasaki import cli
    return cli.main(argv)


if __name__ == "__main__":
    sys.exit(main())
