"""Public adapter input/output schemas (single source of truth).

Imported by scripts/build_adapter_contracts.py to generate contract.json
with real object digests. Kept inside the package so product distribution
has no dependency on scripts/.
"""
from __future__ import annotations

SCHEMAS = {
    "trivy": {
        "input": {"type": "object", "required": ["Results"],
                  "properties": {"Results": {"type": "array"}}},
        "output": {"type": "object", "required": ["schema_version", "findings", "count"],
                   "properties": {"schema_version": {"const": "1.0.0"},
                                  "findings": {"type": "array"},
                                  "count": {"type": "integer"}}},
    },
    "codeql": {
        "input": {"type": "object", "required": ["version", "runs"],
                  "properties": {"version": {"const": "2.1.0"},
                                 "runs": {"type": "array"}}},
        "output": {"type": "object", "required": ["schema_version", "findings", "count"],
                   "properties": {"schema_version": {"const": "1.0.0"},
                                  "findings": {"type": "array"},
                                  "count": {"type": "integer"}}},
    },
    "wazuh": {
        "input": {"type": "object", "required": ["rule", "agent"],
                  "properties": {"rule": {"type": "object"},
                                 "agent": {"type": "object"}}},
        "output": {"type": "object", "required": ["schema_version", "events", "count"],
                   "properties": {"schema_version": {"const": "1.0.0"},
                                  "events": {"type": "array"},
                                  "count": {"type": "integer"}}},
    },
    "sigma": {
        "input": {"type": "object", "required": ["schema_version", "rule"],
                  "properties": {"schema_version": {"const": "1.0.0"},
                                 "rule": {"type": "object"}}},
        "output": {"type": "object", "required": ["schema_version", "valid", "rule_id", "fired_indices", "fired_count"],
                   "properties": {"schema_version": {"const": "1.0.0"},
                                  "valid": {"type": "boolean"},
                                  "rule_id": {"type": "string"},
                                  "fired_indices": {"type": "array"},
                                  "fired_count": {"type": "integer"}}},
    },
    "rustxng": {
        "input": {"type": "object", "required": ["results"],
                  "properties": {"results": {"type": "array"}}},
        "output": {"type": "object", "required": ["schema_version", "query_redacted", "results", "count"],
                   "properties": {"schema_version": {"const": "1.0.0"},
                                  "query_redacted": {"type": "string"},
                                  "results": {"type": "array"},
                                  "count": {"type": "integer"}}},
    },
    "rdsh": {
        "input": {"type": "object", "required": ["returncode", "stdout", "stderr"],
                  "properties": {"returncode": {"type": "integer"},
                                 "stdout": {"type": "string"},
                                 "stderr": {"type": "string"}}},
        "output": {"type": "object", "required": ["schema_version", "returncode", "stdout", "stderr"],
                   "properties": {"schema_version": {"const": "1.0.0"},
                                  "returncode": {"type": "integer"},
                                  "stdout": {"type": "string"},
                                  "stderr": {"type": "string"}}},
    },
    "llm": {
        "input": {"type": "object", "required": ["model", "messages", "stream"],
                  "properties": {"model": {"type": "string"},
                                 "messages": {"type": "array"},
                                 "stream": {"const": False}}},
        "output": {"type": "object", "required": ["schema_version", "content", "effective_model", "prompt_hash", "token_count", "tool_calls", "response_metadata"],
                   "properties": {"schema_version": {"const": "1.0.0"},
                                  "content": {"type": ["string", "null"]},
                                  "effective_model": {"type": ["string", "null"]},
                                  "prompt_hash": {"type": ["string", "null"]},
                                  "token_count": {"type": ["integer", "null"]},
                                  "tool_calls": {"type": "array"},
                                  "response_metadata": {"type": "object"}}},
    },
}


def validate_adapter_payload(name: str, direction: str, payload: object) -> None:
    """Validate an adapter payload against its input/output schema.

    direction is 'input' or 'output'. Raises jsonio.DataError on failure.
    """
    from .. import jsonio
    if name not in SCHEMAS:
        raise jsonio.DataError(f"unknown adapter {name!r}")
    if direction not in ("input", "output"):
        raise jsonio.DataError(f"unknown direction {direction!r}")
    schema = SCHEMAS[name][direction]
    try:
        import jsonschema
        jsonschema.validate(payload, schema)
    except ImportError:
        # Minimal structural check when jsonschema is unavailable.
        if not isinstance(payload, dict):
            raise jsonio.DataError("payload must be object")
        for req in schema.get("required", []):
            if req not in payload:
                raise jsonio.DataError(f"missing required field {req!r}")
    except Exception as exc:  # jsonschema.ValidationError
        raise jsonio.DataError(f"schema validation failed: {exc}")
