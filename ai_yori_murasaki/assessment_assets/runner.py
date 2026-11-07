#!/usr/bin/env python3
"""Assessment runner: oracle (live probes) or blue (static fixture analysis)."""
import argparse
import ast
import hashlib
import json
import os
import platform
import re
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "target.py")
TLS_PROBE = "/app/tls_probe.py"
ENV_FILE = os.path.join(HERE, "environment.json")
MAX_DOM = 65536
T0 = time.time()


def _bounded(text, n=MAX_DOM):
    if text is None:
        return None
    if len(text) > n:
        return text[:n]
    return text


def _http_get(url, timeout=3):
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(MAX_DOM)
            status = resp.getcode()
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            body = _bounded(raw.decode("utf-8", "replace"))
        return {"status": status, "body": body, "error": None}
    except urllib.error.HTTPError as e:
        try:
            raw = e.read(MAX_DOM)
        except Exception:
            raw = b""
        try:
            body = json.loads(raw.decode("utf-8", "replace"))
        except Exception:
            body = _bounded(raw.decode("utf-8", "replace"))
        return {"status": e.code, "body": body, "error": None}
    except Exception as e:
        return {"status": None, "body": None, "error": type(e).__name__}


def _chromium_version():
    for exe in ("chromium", "chromium-browser", "google-chrome"):
        try:
            p = subprocess.run([exe, "--version"], capture_output=True, timeout=10)
            if p.returncode == 0:
                return p.stdout.decode("utf-8", "replace").strip()
        except Exception:
            continue
    return None


def _run_chromium(url, seed):
    exe = None
    for cand in ("chromium", "chromium-browser", "google-chrome"):
        try:
            p = subprocess.run([cand, "--version"], capture_output=True, timeout=10)
            if p.returncode == 0:
                exe = cand
                break
        except Exception:
            continue
    if exe is None:
        return {"returncode": None, "dom": None, "error": "chromium_missing"}
    base = "/dev/shm" if os.path.isdir("/dev/shm") else "/tmp"
    prof = os.path.join(base, "aym_chrome_%d_%d" % (seed, int(time.time() * 1000) % 100000))
    try:
        os.makedirs(prof, exist_ok=True)
    except Exception:
        prof = None
    argv = [
        exe,
        "--headless",
        "--no-sandbox",
        "--disable-gpu",
        "--no-zygote",
        "--single-process",
        "--user-data-dir=" + (prof or "/tmp/aym_chrome_default"),
        "--dump-dom",
        url,
    ]
    try:
        p = subprocess.run(argv, capture_output=True, timeout=20)
        dom = _bounded(p.stdout.decode("utf-8", "replace"))
        return {"returncode": p.returncode, "dom": dom, "error": None}
    except subprocess.TimeoutExpired:
        return {"returncode": None, "dom": None, "error": "timeout"}
    except Exception as e:
        return {"returncode": None, "dom": None, "error": type(e).__name__}
    finally:
        if prof:
            try:
                for root, dirs, files in os.walk(prof, topdown=False):
                    for f in files:
                        try:
                            os.unlink(os.path.join(root, f))
                        except Exception:
                            pass
                    for d in dirs:
                        try:
                            os.rmdir(os.path.join(root, d))
                        except Exception:
                            pass
                os.rmdir(prof)
            except Exception:
                pass


def _tls_probe(deps):
    try:
        p = subprocess.run(
            [sys.executable, TLS_PROBE, "--deps", deps],
            capture_output=True,
            timeout=20,
        )
        raw = p.stdout.decode("utf-8", "replace")
        try:
            parsed = json.loads(raw)
        except Exception:
            parsed = None
        return {
            "returncode": p.returncode,
            "raw": _bounded(raw, 8192),
            "parsed": parsed,
            "stderr": _bounded(p.stderr.decode("utf-8", "replace"), 2048),
        }
    except subprocess.TimeoutExpired:
        return {"returncode": None, "raw": None, "parsed": None, "error": "timeout"}
    except Exception as e:
        return {"returncode": None, "raw": None, "parsed": None, "error": type(e).__name__}


def run_oracle(seed, deps):
    obs = {}
    srv = None
    try:
        sys.path.insert(0, HERE)
        import target  # noqa: F401
        srv = target.make_server(0, seed)
        th = threading.Thread(target=srv.serve_forever, daemon=True)
        th.start()
        host, port = srv.server_address[0], srv.server_address[1]
        base = "http://%s:%d" % (host, port)

        def q(path):
            return base + path

        obs["auth_guest"] = _http_get(q("/record?role=guest"))
        obs["auth_admin"] = _http_get(q("/record?role=admin"))
        obs["public"] = _http_get(q("/public"))
        obs["sql_attack"] = _http_get(q("/search?q=" + urllib.parse.quote("' OR 1=1 --")))
        obs["sql_normal"] = _http_get(q("/search?q=" + urllib.parse.quote("alice")))
        obs["path_attack"] = _http_get(q("/file?name=" + urllib.parse.quote("../secret.txt")))
        obs["path_normal"] = _http_get(q("/file?name=" + urllib.parse.quote("hello.txt")))

        payload = "<script>document.documentElement.dataset.aymProbe='seed-%d'</script>" % seed
        xss_url = q("/echo?q=" + urllib.parse.quote(payload))
        obs["xss_attack"] = _run_chromium(xss_url, seed)
        normal_url = q("/echo?q=" + urllib.parse.quote("hello"))
        obs["xss_normal"] = _run_chromium(normal_url, seed)
    except Exception as e:
        obs.setdefault("_oracle_error", type(e).__name__)
    finally:
        if srv is not None:
            try:
                srv.shutdown()
            except Exception:
                pass
            try:
                srv.server_close()
            except Exception:
                pass

    obs["dependency"] = _tls_probe(deps)

    return {
        "mode": "oracle",
        "seed": seed,
        "observations": obs,
        "runtime": {
            "python_version": platform.python_version(),
            "chromium_version": _chromium_version(),
            "elapsed_sec": round(time.time() - T0, 3),
        },
    }


GUARD_MAP = {
    "REQUIRE_ADMIN": ("authz", "CWE-862"),
    "PARAMETERIZE_QUERY": ("sqli", "CWE-89"),
    "CONSTRAIN_PATH": ("path_traversal", "CWE-22"),
    "ESCAPE_HTML": ("xss", "CWE-79"),
}

HANDLER_HINTS = {
    "authz": ("_record",),
    "sqli": ("_search",),
    "path_traversal": ("_file",),
    "xss": ("_echo",),
}


def _const_bool(node):
    if isinstance(node, ast.Constant) and isinstance(node.value, bool):
        return node.value
    return None


def _find_guard_uses(tree, name):
    """Return list of (lineno, bool_value_or_None) for `if <name>` / `if not <name>`."""
    uses = []
    for node in ast.walk(tree):
        if isinstance(node, ast.If):
            test = node.test
            val = None
            if isinstance(test, ast.Name) and test.id == name:
                val = True
            elif isinstance(test, ast.UnaryOp) and isinstance(test.op, ast.Not):
                if isinstance(test.operand, ast.Name) and test.operand.id == name:
                    val = False
            if val is not None:
                uses.append((node.lineno, val))
    return uses


def _handler_exists(tree, names):
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name in names:
            return True
    return False


def run_blue():
    with open(TARGET, "rb") as f:
        src_bytes = f.read()
    sha = hashlib.sha256(src_bytes).hexdigest()
    src = src_bytes.decode("utf-8", "replace")

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return {
            "mode": "blue",
            "source_sha256": sha,
            "findings": [],
            "error": "parse_failed:" + type(e).__name__,
            "scope": "finite_fixture_detector",
        }

    consts = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for tgt in node.targets:
                if isinstance(tgt, ast.Name) and tgt.id in GUARD_MAP:
                    consts[tgt.id] = _const_bool(node.value)

    findings = []
    for guard, (category, cwe) in GUARD_MAP.items():
        hints = HANDLER_HINTS[category]
        handler_present = _handler_exists(tree, hints)
        uses = _find_guard_uses(tree, guard)
        const_val = consts.get(guard, "missing")
        source_lines = [ln for ln, _ in uses]

        if not handler_present:
            findings.append({
                "category": category,
                "cwe": cwe,
                "detected": None,
                "cause": "handler_missing_for_%s" % guard,
                "source_lines": source_lines,
                "classification_status": "unknown",
            })
            continue

        if const_val == "missing":
            findings.append({
                "category": category,
                "cwe": cwe,
                "detected": None,
                "cause": "guard_constant_missing:%s" % guard,
                "source_lines": source_lines,
                "classification_status": "unknown",
            })
            continue

        if const_val is None:
            findings.append({
                "category": category,
                "cwe": cwe,
                "detected": None,
                "cause": "guard_constant_nonliteral:%s" % guard,
                "source_lines": source_lines,
                "classification_status": "unknown",
            })
            continue

        if not uses:
            findings.append({
                "category": category,
                "cwe": cwe,
                "detected": None,
                "cause": "guard_constant_present_but_unused:%s" % guard,
                "source_lines": source_lines,
                "classification_status": "unknown",
            })
            continue

        # Constant False => unsafe branch active (candidate). Constant True => safe branch.
        detected = (const_val is False)
        findings.append({
            "category": category,
            "cwe": cwe,
            "detected": detected,
            "cause": "guard_%s=%s_used_in_handler" % (guard, str(const_val).lower()),
            "source_lines": source_lines,
            "classification_status": "candidate",
        })

    # Dependency check from environment.json (approved input only).
    dep_finding = {
        "category": "dependency",
        "cwe": "CVE-2024-35195",
        "detected": None,
        "cause": "environment_missing",
        "source_lines": [],
        "classification_status": "unknown",
    }
    try:
        with open(ENV_FILE, "r", encoding="utf-8") as f:
            env = json.load(f)
        ver = env.get("requests_version")
        if isinstance(ver, str):
            m = re.match(r"^(\d+)\.(\d+)\.(\d+)", ver)
            if m:
                parts = tuple(int(x) for x in m.groups())
                if parts == (2, 31, 0):
                    dep_finding.update({
                        "detected": True,
                        "cause": "requests_version_2.31.0_candidate",
                        "classification_status": "candidate",
                    })
                elif parts == (2, 32, 5):
                    dep_finding.update({
                        "detected": False,
                        "cause": "requests_version_2.32.5_negative",
                        "classification_status": "candidate",
                    })
                else:
                    dep_finding.update({
                        "detected": None,
                        "cause": "requests_version_unknown:%s" % ver,
                        "classification_status": "unknown",
                    })
            else:
                dep_finding.update({
                    "detected": None,
                    "cause": "requests_version_unparseable:%s" % ver,
                    "classification_status": "unknown",
                })
        else:
            dep_finding.update({
                "detected": None,
                "cause": "requests_version_not_string",
                "classification_status": "unknown",
            })
    except FileNotFoundError:
        dep_finding["cause"] = "environment_missing"
    except Exception as e:
        dep_finding["cause"] = "environment_error:" + type(e).__name__

    findings.append(dep_finding)

    return {
        "mode": "blue",
        "source_sha256": sha,
        "findings": findings,
        "scope": (
            "finite_fixture_detector: inspects only the four named guard constants "
            "(REQUIRE_ADMIN, PARAMETERIZE_QUERY, CONSTRAIN_PATH, ESCAPE_HTML) and their "
            "conditional use in known handlers; not a general AST vulnerability scanner"
        ),
        "runtime": {
            "python_version": platform.python_version(),
            "elapsed_sec": round(time.time() - T0, 3),
        },
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["oracle", "blue"], required=True)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--deps", choices=["old", "fixed"], default="old")
    args = ap.parse_args()

    if not (0 <= args.seed <= 2147483647):
        print(json.dumps({"error": "seed_out_of_range"}))
        sys.exit(2)

    if args.mode == "oracle":
        result = run_oracle(args.seed, args.deps)
    else:
        result = run_blue()

    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
