#!/usr/bin/env python3
"""Finite-fixture Blue detector: static analysis of known target.py fixture."""
import ast
import hashlib
import json
import os
import platform
import re
import time

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "target.py")
ENV_FILE = os.path.join(HERE, "environment.json")
T0 = time.time()


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
            if val is None:
                for sub in ast.walk(test):
                    if isinstance(sub, ast.Name) and sub.id == name:
                        val = True
                        break
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
        "category": "dependency_cve",
        "cve": "CVE-2024-35195",
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
            m = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", ver)
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
    result = run_blue()
    print(json.dumps(result, default=str))


if __name__ == "__main__":
    main()
