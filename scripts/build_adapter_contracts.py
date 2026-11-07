#!/usr/bin/env python3
"""Generate the 7 adapter contract.json files with real schema digests.

SCHEMAS below are the public input/output schemas. input_ref/output_ref
are sha256 of the canonical JSON of the schema object (not placeholders).
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from ai_yori_murasaki.adapters.schema_contracts import SCHEMAS  # noqa: E402

CONTRACTS_DIR = os.path.join(ROOT, "ai_yori_murasaki", "adapters", "contracts")


def _canonical(v):
    return json.dumps(v, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def _digest(v):
    return hashlib.sha256(_canonical(v)).hexdigest()


LIMITS = {
    "trivy": (120000, 1, 52428800),
    "codeql": (60000, 1, 52428800),
    "wazuh": (30000, 2, 10485760),
    "sigma": (30000, 1, 5242880),
    "rustxng": (10000, 2, 5242880),
    "rdsh": (300000, 0, 10485760),
    "llm": (60000, 2, 5242880),
}


def build() -> int:
    os.makedirs(CONTRACTS_DIR, exist_ok=True)
    for name, schemas in SCHEMAS.items():
        timeout_ms, retry_cap, response_cap = LIMITS[name]
        contract = {
            "name": name,
            "version": "1.0.0",
            "input_ref": _digest(schemas["input"]),
            "output_ref": _digest(schemas["output"]),
            "timeout_ms": timeout_ms,
            "retry_cap": retry_cap,
            "response_cap": response_cap,
        }
        path = os.path.join(CONTRACTS_DIR, f"{name}.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(contract, fh, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"))
            fh.write("\n")
        print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(build())
