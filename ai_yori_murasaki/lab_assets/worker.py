#!/usr/bin/env python3
"""Finite local lab worker for the ai-yori-murasaki disposable Docker lab.

Modes (positional):
  probe   GET /health against the fixed target host.
  red     Three fixed guest attempts against /record with case ids
          attack-1..attack-3. Only the fixed query role=guest is used.
  benign  Five actual checks: /health, /public, admin /record, unknown 404,
          /health repeat. Returns measured expected/actual/passed rows.
  blue    Network-none mode. Reads ONLY /app/telemetry.json and reports
          guest-200 /record events as detection objects with source row
          counts. No answer key, no candidate patch, no red prompts, no
          oracle canary.
  unit    Imports the local target module (only when present in the staged
          source) and exercises the pure ``authorize(role)`` function.

CLI is intentionally narrow: --host must match a fixed aym-* hostname and
port is fixed at 8080. No arbitrary URLs, no redirects, no external network.

This module has no import side effects. ``main`` only runs under
``if __name__ == "__main__"``.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

FIXED_PORT = 8080
HOST_RE = re.compile(r"^aym-[a-z0-9][a-z0-9-]{0,61}[a-z0-9]$")
TELEMETRY_PATH = "/app/telemetry.json"

RED_CASES: Tuple[str, ...] = ("attack-1", "attack-2", "attack-3")


def _validate_host(host: str) -> str:
    if not isinstance(host, str) or not HOST_RE.match(host):
        raise ValueError("host must match fixed aym-* lab hostname")
    return host


def _http_get(host: str, path: str, timeout: float = 5.0) -> Dict[str, Any]:
    """Fixed HTTP GET against http://<host>:8080<path>. No redirects."""
    _validate_host(host)
    if not path.startswith("/"):
        raise ValueError("path must start with /")
    url = "http://{}:{}{}".format(host, FIXED_PORT, path)

    class _NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
            return None

    opener = urllib.request.build_opener(_NoRedirect)
    started = time.time()
    status = 0
    body_text = ""
    try:
        with opener.open(url, timeout=timeout) as resp:
            status = int(resp.getcode() or 0)
            body_text = resp.read(65536).decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        status = int(exc.code)
        try:
            body_text = exc.read(65536).decode("utf-8", "replace")
        except Exception:
            body_text = ""
    except Exception as exc:  # network error, timeout, etc.
        return {
            "status": 0,
            "body": None,
            "error": type(exc).__name__,
            "method": "GET",
            "path": path,
            "timestamp": time.time(),
            "elapsed": time.time() - started,
        }
    parsed_body: Optional[Any]
    try:
        parsed_body = json.loads(body_text) if body_text else None
    except Exception:
        parsed_body = None
    return {
        "status": status,
        "body": parsed_body,
        "method": "GET",
        "path": path,
        "timestamp": time.time(),
        "elapsed": time.time() - started,
    }


def mode_probe(host: str) -> Dict[str, Any]:
    result = _http_get(host, "/health")
    ok = result.get("status") == 200 and isinstance(result.get("body"), dict) and result["body"].get("ok") is True
    return {
        "mode": "probe",
        "status": "ok" if ok else "fail",
        "checks": [{"name": "health", "expected": 200, "actual": result.get("status"), "passed": ok}],
        "observations": [result],
    }


def mode_red(host: str) -> Dict[str, Any]:
    observations: List[Dict[str, Any]] = []
    checks: List[Dict[str, Any]] = []
    for case_id in RED_CASES:
        path = "/record?role=guest&case={}".format(case_id)
        result = _http_get(host, path)
        result["case"] = case_id
        observations.append(result)
        checks.append({
            "name": case_id,
            "expected": 200,
            "actual": result.get("status"),
            "passed": result.get("status") == 200,
        })
    return {"mode": "red", "status": "ok", "checks": checks, "observations": observations}


def mode_benign(host: str) -> Dict[str, Any]:
    plan: List[Tuple[str, str, int]] = [
        ("health", "/health", 200),
        ("public", "/public", 200),
        ("admin_record", "/record?role=admin", 200),
        ("unknown", "/does-not-exist", 404),
        ("health_repeat", "/health", 200),
    ]
    checks: List[Dict[str, Any]] = []
    observations: List[Dict[str, Any]] = []
    for name, path, expected in plan:
        result = _http_get(host, path)
        observations.append({"name": name, **result})
        checks.append({
            "name": name,
            "expected": expected,
            "actual": result.get("status"),
            "passed": result.get("status") == expected,
        })
    all_passed = all(c["passed"] for c in checks)
    return {
        "mode": "benign",
        "status": "ok" if all_passed else "fail",
        "checks": checks,
        "observations": observations,
    }


def mode_blue(telemetry_path: str = TELEMETRY_PATH) -> Dict[str, Any]:
    """Network-none blue mode. Reads ONLY the telemetry file."""
    detections: List[Dict[str, Any]] = []
    source_rows = 0
    try:
        with open(telemetry_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                source_rows += 1
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if not isinstance(row, dict):
                    continue
                if row.get("path") == "/record" and row.get("status") == 200 and row.get("role") == "guest":
                    detections.append({
                        "source": "telemetry",
                        "method": row.get("method"),
                        "path": row.get("path"),
                        "status": row.get("status"),
                        "role": row.get("role"),
                        "timestamp": row.get("timestamp"),
                    })
    except FileNotFoundError:
        return {
            "mode": "blue",
            "status": "fail",
            "checks": [{"name": "telemetry_present", "expected": True, "actual": False, "passed": False}],
            "observations": [],
            "source_rows": 0,
        }
    return {
        "mode": "blue",
        "status": "ok",
        "checks": [{"name": "telemetry_present", "expected": True, "actual": True, "passed": True}],
        "observations": detections,
        "source_rows": source_rows,
        "detection_count": len(detections),
    }


def mode_unit(source_dir: Optional[str] = None) -> Dict[str, Any]:
    """Import local target module (only if present) and test authorize()."""
    if source_dir is None:
        source_dir = os.path.dirname(os.path.abspath(__file__))
    target_path = os.path.join(source_dir, "target.py")
    if not os.path.isfile(target_path):
        return {
            "mode": "unit",
            "status": "fail",
            "checks": [{"name": "target_present", "expected": True, "actual": False, "passed": False}],
            "observations": [],
        }
    spec = importlib.util.spec_from_file_location("aym_lab_target_under_test", target_path)
    if spec is None or spec.loader is None:
        return {
            "mode": "unit",
            "status": "fail",
            "checks": [{"name": "target_importable", "expected": True, "actual": False, "passed": False}],
            "observations": [],
        }
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    authorize = getattr(module, "authorize", None)
    if not callable(authorize):
        return {
            "mode": "unit",
            "status": "fail",
            "checks": [{"name": "authorize_callable", "expected": True, "actual": False, "passed": False}],
            "observations": [],
        }
    require_admin = bool(getattr(module, "REQUIRE_ADMIN", False))
    checks: List[Dict[str, Any]] = []
    if require_admin:
        cases = [("admin", True), ("guest", False), ("", False)]
    else:
        cases = [("admin", True), ("guest", True), ("", True)]
    for role, expected in cases:
        actual = bool(authorize(role))
        checks.append({
            "name": "authorize[{}]".format(role or "empty"),
            "expected": expected,
            "actual": actual,
            "passed": actual == expected,
        })
    all_passed = all(c["passed"] for c in checks)
    return {
        "mode": "unit",
        "status": "ok" if all_passed else "fail",
        "checks": checks,
        "observations": [{"require_admin": require_admin}],
    }


def run(mode: str, host: Optional[str] = None, telemetry: str = TELEMETRY_PATH) -> Dict[str, Any]:
    if mode == "probe":
        if host is None:
            raise ValueError("probe requires --host")
        return mode_probe(_validate_host(host))
    if mode == "red":
        if host is None:
            raise ValueError("red requires --host")
        return mode_red(_validate_host(host))
    if mode == "benign":
        if host is None:
            raise ValueError("benign requires --host")
        return mode_benign(_validate_host(host))
    if mode == "blue":
        return mode_blue(telemetry)
    if mode == "unit":
        return mode_unit()
    raise ValueError("unknown mode: {}".format(mode))


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(prog="worker.py", add_help=True)
    parser.add_argument("mode", choices=["probe", "red", "benign", "blue", "unit"])
    parser.add_argument("--host", default=None)
    parser.add_argument("--telemetry", default=TELEMETRY_PATH)
    args = parser.parse_args(argv)
    try:
        result = run(args.mode, host=args.host, telemetry=args.telemetry)
    except Exception as exc:
        result = {
            "mode": args.mode,
            "status": "error",
            "checks": [],
            "observations": [],
            "error": type(exc).__name__,
        }
    sys.stdout.write(json.dumps(result, separators=(",", ":")) + "\n")
    sys.stdout.flush()
    return 0 if result.get("status") in ("ok", "fail") else 1


if __name__ == "__main__":
    raise SystemExit(main())
