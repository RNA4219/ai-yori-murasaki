"""Per-adapter normalizers (Eval-owned).

Each normalizer maps external tool output to Core Finding / event shapes.
Lossless where the spec requires it; secrets are reduced to kind/location/rule
version only. All parsers use stdlib + jsonschema only.
"""
from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .. import jsonio
from . import base


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _sha256_text(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _require(cond: bool, msg: str, code: str = "input_invalid") -> None:
    if not cond:
        raise jsonio.DataError(msg, code)


def _as_dict(v: Any, what: str) -> dict:
    _require(isinstance(v, dict), f"{what} must be object")
    return v


def _as_list(v: Any, what: str) -> list:
    _require(isinstance(v, list), f"{what} must be array")
    return v


def _as_str(v: Any, what: str, *, allow_empty: bool = False) -> str:
    _require(isinstance(v, str), f"{what} must be string")
    if not allow_empty:
        _require(len(v) > 0, f"{what} must be non-empty")
    return v


def _as_int(v: Any, what: str) -> int:
    _require(isinstance(v, int) and not isinstance(v, bool), f"{what} must be integer")
    return v


def _stable_id(*parts: str) -> str:
    return _sha256_text("\x1f".join(parts))


# ---------------------------------------------------------------------------
# trivy
# ---------------------------------------------------------------------------

_TRIVY_SECRET_KEEP = {"RuleID", "Category", "Severity", "Title", "Target", "StartLine", "EndLine"}
_SECRET_KEY_RE = re.compile(r"(secret|token|password|api[_-]?key|credential)", re.I)


def _strip_secret_keys(obj: Any) -> Any:
    """Recursively drop secret-bearing keys from raw vuln data."""
    if isinstance(obj, dict):
        return {k: _strip_secret_keys(v) for k, v in obj.items()
                if not _SECRET_KEY_RE.search(str(k))}
    if isinstance(obj, list):
        return [_strip_secret_keys(v) for v in obj]
    return obj


def normalize_trivy(raw: bytes) -> dict:
    """Trivy `fs --format json` stdout -> Finding list.

    Lossless for vulnerabilities/misconfigurations/licenses: the full
    sanitized source snapshot (root + Result extensions) is preserved in
    source_snapshot so IDs/counts/positions/references/extensions survive.
    Secrets are reduced to kind/location/rule version (no Match/Code content).
    """
    doc = jsonio.loads(raw)
    _require(isinstance(doc, dict), "trivy output must be object")
    results = _as_list(doc.get("Results", []), "Results")
    findings: list[dict] = []
    for r in results:
        r = _as_dict(r, "Result")
        target = _as_str(r.get("Target", ""), "Target", allow_empty=True)
        rtype = _as_str(r.get("Class", r.get("Type", "")), "Class", allow_empty=True)
        # Result-level extensions (root/Result fields beyond known lists)
        result_ext = {k: v for k, v in r.items()
                      if k not in ("Vulnerabilities", "Misconfigurations",
                                   "Licenses", "Secrets")}
        for v in _as_list(r.get("Vulnerabilities", []), "Vulnerabilities"):
            v = _as_dict(v, "Vulnerability")
            vid = _as_str(v.get("VulnerabilityID", ""), "VulnerabilityID", allow_empty=True)
            pkg = _as_str(v.get("PkgName", ""), "PkgName", allow_empty=True)
            findings.append({
                "kind": "vulnerability",
                "stable_id": _stable_id("trivy", "vuln", target, vid, pkg),
                "target": target,
                "class": rtype,
                "data": _strip_secret_keys(v),
                "source_snapshot": {"result": result_ext},
            })
        for m in _as_list(r.get("Misconfigurations", []), "Misconfigurations"):
            m = _as_dict(m, "Misconfiguration")
            mid = _as_str(m.get("ID", ""), "ID", allow_empty=True)
            findings.append({
                "kind": "misconfiguration",
                "stable_id": _stable_id("trivy", "misconf", target, mid),
                "target": target,
                "class": rtype,
                "data": _strip_secret_keys(m),
                "source_snapshot": {"result": result_ext},
            })
        for lic in _as_list(r.get("Licenses", []), "Licenses"):
            lic = _as_dict(lic, "License")
            name = _as_str(lic.get("Name", ""), "Name", allow_empty=True)
            findings.append({
                "kind": "license",
                "stable_id": _stable_id("trivy", "license", target, name),
                "target": target,
                "class": rtype,
                "data": _strip_secret_keys(lic),
                "source_snapshot": {"result": result_ext},
            })
        for s in _as_list(r.get("Secrets", []), "Secrets"):
            s = _as_dict(s, "Secret")
            redacted = {k: s[k] for k in _TRIVY_SECRET_KEEP if k in s}
            rid = _as_str(s.get("RuleID", ""), "RuleID", allow_empty=True)
            findings.append({
                "kind": "secret",
                "stable_id": _stable_id("trivy", "secret", target, rid,
                                        str(s.get("StartLine", ""))),
                "target": target,
                "class": rtype,
                "data": redacted,
                "source_snapshot": {"result": result_ext},
            })
    return {"schema_version": "1.0.0", "findings": findings,
            "count": len(findings),
            "source_snapshot": {"root": {k: v for k, v in doc.items()
                                          if k != "Results"}}}


# ---------------------------------------------------------------------------
# codeql-sarif
# ---------------------------------------------------------------------------


def normalize_sarif(raw: bytes) -> dict:
    """SARIF 2.1.0 -> Finding list (count/identifiers preserved).

    Root, run-level, rule and raw result objects are retained so nothing is
    silently dropped.
    """
    doc = jsonio.loads(raw)
    _require(isinstance(doc, dict), "SARIF must be object")
    _require(doc.get("version") == "2.1.0", "SARIF version must be 2.1.0")
    runs = _as_list(doc.get("runs", []), "runs")
    findings: list[dict] = []
    for run in runs:
        run = _as_dict(run, "run")
        tool = _as_dict(run.get("tool", {}), "tool")
        driver = _as_dict(tool.get("driver", {}), "driver")
        tool_name = _as_str(driver.get("name", ""), "driver.name", allow_empty=True)
        rules = _as_list(driver.get("rules", []), "rules")
        rule_index: dict[str, dict] = {}
        for rule in rules:
            rule = _as_dict(rule, "rule")
            rid = _as_str(rule.get("id", ""), "rule.id", allow_empty=True)
            rule_index[rid] = rule
        run_ext = {k: v for k, v in run.items()
                   if k not in ("tool", "results")}
        for res in _as_list(run.get("results", []), "results"):
            res = _as_dict(res, "result")
            rule_id = _as_str(res.get("ruleId", ""), "ruleId", allow_empty=True)
            rule = rule_index.get(rule_id, {})
            locations = _as_list(res.get("locations", []), "locations")
            locs_out = []
            for loc in locations:
                loc = _as_dict(loc, "location")
                phys = _as_dict(loc.get("physicalLocation", {}), "physicalLocation")
                art = _as_dict(phys.get("artifactLocation", {}), "artifactLocation")
                region = _as_dict(phys.get("region", {}), "region")
                locs_out.append({
                    "uri": _as_str(art.get("uri", ""), "uri", allow_empty=True),
                    "startLine": region.get("startLine"),
                    "endLine": region.get("endLine"),
                    "startColumn": region.get("startColumn"),
                    "endColumn": region.get("endColumn"),
                })
            findings.append({
                "kind": "sarif_result",
                "stable_id": _stable_id("sarif", tool_name, rule_id,
                                        json.dumps(locs_out, sort_keys=True)),
                "tool": tool_name,
                "rule_id": rule_id,
                "rule": rule,
                "level": res.get("level"),
                "message": _as_dict(res.get("message", {}), "message").get("text"),
                "locations": locs_out,
                "data": res,
                "source_snapshot": {"run": run_ext, "rule": rule},
            })
    return {"schema_version": "1.0.0", "findings": findings,
            "count": len(findings),
            "source_snapshot": {"root": {k: v for k, v in doc.items()
                                          if k != "runs"}}}


# ---------------------------------------------------------------------------
# wazuh
# ---------------------------------------------------------------------------


def normalize_wazuh(raw: bytes) -> dict:
    """Wazuh alert JSON -> normalized event."""
    doc = jsonio.loads(raw)
    _require(isinstance(doc, dict), "wazuh alert must be object")
    rule = _as_dict(doc.get("rule", {}), "rule")
    agent = _as_dict(doc.get("agent", {}), "agent")
    rule_id = _as_str(rule.get("id", ""), "rule.id", allow_empty=True)
    agent_id = _as_str(agent.get("id", ""), "agent.id", allow_empty=True)
    ts = _as_str(doc.get("timestamp", ""), "timestamp", allow_empty=True)
    event = {
        "schema_version": "1.0.0",
        "kind": "wazuh_alert",
        "stable_id": _stable_id("wazuh", rule_id, agent_id, ts,
                                json.dumps(doc.get("data", {}), sort_keys=True)),
        "timestamp": ts,
        "rule_id": rule_id,
        "rule_level": rule.get("level"),
        "agent_id": agent_id,
        "agent_name": agent.get("name"),
        "data": doc,
    }
    return {"schema_version": "1.0.0", "events": [event], "count": 1}


# ---------------------------------------------------------------------------
# sigma (bounded subset, stdlib parser, no eval)
# ---------------------------------------------------------------------------

_SIGMA_ALLOWED_RULE_KEYS = {
    "title", "id", "status", "logsource", "detection",
    "description", "author", "references", "tags", "level",
}
_SIGMA_ALLOWED_MODIFIERS = {"contains"}
_SIGMA_TOKEN_RE = re.compile(r"\s*(?:(?P<lp>\()|(?P<rp>\))|(?P<word>[A-Za-z0-9_.\-]+))")


class SigmaUnsupported(Exception):
    """Unsupported syntax -> HOLD."""


class SigmaMalformed(Exception):
    """Malformed rule -> FAIL."""


def _sigma_tokenize(expr: str) -> list[str]:
    tokens: list[str] = []
    pos = 0
    while pos < len(expr):
        m = _SIGMA_TOKEN_RE.match(expr, pos)
        if not m:
            raise SigmaMalformed(f"unexpected character at {pos}")
        pos = m.end()
        if m.group("lp"):
            tokens.append("(")
        elif m.group("rp"):
            tokens.append(")")
        else:
            tokens.append(m.group("word"))
    return tokens


class _SigmaParser:
    """Recursive-descent parser: not > and > or."""

    def __init__(self, tokens: list[str], known: set[str]) -> None:
        self.tokens = tokens
        self.pos = 0
        self.known = known

    def _peek(self) -> str | None:
        return self.tokens[self.pos] if self.pos < len(self.tokens) else None

    def _next(self) -> str:
        tok = self._peek()
        if tok is None:
            raise SigmaMalformed("unexpected end of condition")
        self.pos += 1
        return tok

    def parse(self) -> dict:
        node = self._parse_or()
        if self._peek() is not None:
            raise SigmaMalformed("trailing tokens in condition")
        return node

    def _parse_or(self) -> dict:
        node = self._parse_and()
        while self._peek() == "or":
            self._next()
            node = {"op": "or", "left": node, "right": self._parse_and()}
        return node

    def _parse_and(self) -> dict:
        node = self._parse_not()
        while self._peek() == "and":
            self._next()
            node = {"op": "and", "left": node, "right": self._parse_not()}
        return node

    def _parse_not(self) -> dict:
        if self._peek() == "not":
            self._next()
            return {"op": "not", "child": self._parse_not()}
        return self._parse_atom()

    def _parse_atom(self) -> dict:
        tok = self._next()
        if tok == "(":
            node = self._parse_or()
            if self._next() != ")":
                raise SigmaMalformed("unbalanced parenthesis")
            return node
        if tok in ("and", "or", "not", ")"):
            raise SigmaMalformed(f"unexpected token {tok!r}")
        if tok not in self.known:
            raise SigmaMalformed(f"unknown selection {tok!r}")
        return {"op": "sel", "name": tok}


def _sigma_compile_condition(expr: str, known: set[str]) -> dict:
    tokens = _sigma_tokenize(expr)
    if not tokens:
        raise SigmaMalformed("empty condition")
    return _SigmaParser(tokens, known).parse()


def _sigma_eval(node: dict, selections: dict[str, dict], event: dict) -> bool:
    op = node["op"]
    if op == "sel":
        return _sigma_match_selection(selections[node["name"]], event)
    if op == "not":
        return not _sigma_eval(node["child"], selections, event)
    if op == "and":
        return _sigma_eval(node["left"], selections, event) and _sigma_eval(node["right"], selections, event)
    if op == "or":
        return _sigma_eval(node["left"], selections, event) or _sigma_eval(node["right"], selections, event)
    raise SigmaMalformed(f"unknown op {op!r}")


def _sigma_match_selection(sel: dict, event: dict) -> bool:
    for field, expected in sel.items():
        if "|" in field:
            base_field, mod = field.split("|", 1)
            if mod not in _SIGMA_ALLOWED_MODIFIERS:
                raise SigmaUnsupported(f"modifier not supported: {mod!r}")
            actual = event.get(base_field)
            if not isinstance(actual, str) or not isinstance(expected, str):
                return False
            if expected not in actual:
                return False
        else:
            actual = event.get(field)
            if isinstance(expected, bool):
                if actual is not expected:
                    return False
            elif isinstance(expected, int):
                if not isinstance(actual, int) or isinstance(actual, bool) or actual != expected:
                    return False
            elif isinstance(expected, str):
                if actual != expected:
                    return False
            else:
                raise SigmaMalformed(f"unsupported value type for {field!r}")
    return True


def _sigma_validate_rule(rule: Any) -> dict:
    rule = _as_dict(rule, "rule")
    extra = set(rule.keys()) - _SIGMA_ALLOWED_RULE_KEYS
    if extra:
        raise SigmaUnsupported(f"rule keys not supported: {sorted(extra)}")
    title = _as_str(rule.get("title", ""), "title")
    _require(1 <= len(title) <= 200, "title length out of range")
    _as_str(rule.get("id", ""), "id")
    _require(rule.get("status") == "experimental", "status must be experimental")
    logsource = _as_dict(rule.get("logsource", {}), "logsource")
    _as_str(logsource.get("category", ""), "logsource.category")
    detection = _as_dict(rule.get("detection", {}), "detection")
    condition = _as_str(detection.get("condition", ""), "detection.condition")
    selections: dict[str, dict] = {}
    for key, val in detection.items():
        if key == "condition":
            continue
        if not isinstance(val, dict):
            raise SigmaUnsupported(f"selection {key!r} must be object")
        for f, exp in val.items():
            if "|" in f:
                _, mod = f.split("|", 1)
                if mod not in _SIGMA_ALLOWED_MODIFIERS:
                    raise SigmaUnsupported(f"modifier not supported: {mod!r}")
            if isinstance(exp, list):
                raise SigmaUnsupported("list values not supported")
            if not isinstance(exp, (str, bool, int)):
                raise SigmaMalformed(f"unsupported value type for {f!r}")
        selections[key] = val
    if not selections:
        raise SigmaMalformed("no selections")
    ast = _sigma_compile_condition(condition, set(selections.keys()))
    return {"rule": rule, "selections": selections, "ast": ast}


def normalize_sigma(raw: bytes, events: list[dict] | None = None) -> dict:
    """Sigma JSON bounded subset -> validate + fire results.

    Unsupported syntax raises SigmaUnsupported (HOLD). Malformed raises
    SigmaMalformed (FAIL).
    """
    doc = jsonio.loads(raw)
    _require(isinstance(doc, dict), "sigma wrapper must be object")
    _require(doc.get("schema_version") == "1.0.0", "schema_version must be 1.0.0")
    compiled = _sigma_validate_rule(doc.get("rule"))
    fired: list[int] = []
    if events is not None:
        for idx, ev in enumerate(events):
            ev = _as_dict(ev, "event")
            if _sigma_eval(compiled["ast"], compiled["selections"], ev):
                fired.append(idx)
    return {
        "schema_version": "1.0.0",
        "valid": True,
        "rule_id": compiled["rule"]["id"],
        "fired_indices": fired,
        "fired_count": len(fired),
    }


# ---------------------------------------------------------------------------
# rustxng
# ---------------------------------------------------------------------------

_REDACT_KEYS = {"token", "api_key", "apikey", "secret", "password", "authorization"}


def _redact_query(q: str) -> str:
    parts = []
    for seg in q.split("&"):
        if "=" in seg:
            k, v = seg.split("=", 1)
            if k.lower() in _REDACT_KEYS:
                parts.append(f"{k}=REDACTED")
                continue
        parts.append(seg)
    return "&".join(parts)


def normalize_rustxng(raw: bytes, *, query: str = "") -> dict:
    """RustXNG search JSON -> results with URL hash + fetch time."""
    doc = jsonio.loads(raw)
    _require(isinstance(doc, dict), "rustxng response must be object")
    results = _as_list(doc.get("results", []), "results")
    out = []
    for r in results:
        r = _as_dict(r, "result")
        url = _as_str(r.get("url", ""), "url", allow_empty=True)
        out.append({
            "title": r.get("title"),
            "url_hash": _sha256_text(url),
            "snippet": r.get("content", r.get("snippet")),
            "engine": r.get("engine"),
            "score": r.get("score"),
        })
    return {
        "schema_version": "1.0.0",
        "query_redacted": _redact_query(query),
        "results": out,
        "count": len(out),
    }


def normalize_rustxng_health(raw: bytes) -> dict:
    doc = jsonio.loads(raw)
    _require(isinstance(doc, dict), "health must be object")
    return {"schema_version": "1.0.0", "status": doc.get("status")}


# ---------------------------------------------------------------------------
# rdsh
# ---------------------------------------------------------------------------


def normalize_rdsh(raw: bytes) -> dict:
    """rdsh CLI output -> execution result (no arbitrary command)."""
    doc = jsonio.loads(raw)
    _require(isinstance(doc, dict), "rdsh output must be object")
    return {
        "schema_version": "1.0.0",
        "returncode": _as_int(doc.get("returncode", 0), "returncode"),
        "stdout": _as_str(doc.get("stdout", ""), "stdout", allow_empty=True),
        "stderr": _as_str(doc.get("stderr", ""), "stderr", allow_empty=True),
    }


# ---------------------------------------------------------------------------
# findings contract
# ---------------------------------------------------------------------------

_SEVERITY_ENUM = {"critical", "high", "medium", "low", "info"}
_SEVERITY_ALIASES = {
    "critical": "critical", "high": "high", "medium": "medium",
    "moderate": "medium", "low": "low", "info": "info",
    "informational": "info", "note": "info", "none": "info",
}
_EXTENSIONS_CAP = 64 * 1024
_TITLE_MAX = 512


def _map_severity(raw_sev: Any) -> tuple[str, Any]:
    """Map raw severity to schema enum; return (severity, original_or_None)."""
    if not isinstance(raw_sev, str) or not raw_sev:
        return "info", raw_sev if raw_sev is not None else None
    key = raw_sev.strip().lower()
    if key in _SEVERITY_ALIASES:
        return _SEVERITY_ALIASES[key], None
    return "info", raw_sev


def _extract_location(f: dict) -> dict:
    """Build schema-conformant location object {path, line, column}."""
    path = None
    line = None
    column = None
    loc = f.get("location")
    if isinstance(loc, dict):
        p = loc.get("path")
        if isinstance(p, str) and p:
            path = p
        ln = loc.get("line")
        if isinstance(ln, int) and not isinstance(ln, bool) and ln >= 1:
            line = ln
        col = loc.get("column")
        if isinstance(col, int) and not isinstance(col, bool) and col >= 1:
            column = col
    if path is None:
        tgt = f.get("target")
        if isinstance(tgt, str) and tgt:
            path = tgt
    if path is None:
        path = "unknown"
    if len(path) > 1024:
        path = path[:1024]
    return {"path": path, "line": line, "column": column}


def _extract_references(f: dict) -> list[str]:
    refs: list[str] = []
    raw = f.get("references")
    if isinstance(raw, list):
        for r in raw:
            if isinstance(r, str) and r:
                refs.append(r[:2048])
            if len(refs) >= 32:
                break
    data = f.get("data")
    if isinstance(data, dict):
        for key in ("References", "references"):
            extra = data.get(key)
            if isinstance(extra, list):
                for r in extra:
                    if isinstance(r, str) and r and r[:2048] not in refs:
                        refs.append(r[:2048])
                    if len(refs) >= 32:
                        break
    return refs[:32]


def _extract_title(f: dict) -> tuple[str, Any]:
    """Return (title<=512, original_title_if_truncated_or_None)."""
    title = f.get("title")
    if not isinstance(title, str) or not title:
        data = f.get("data")
        if isinstance(data, dict):
            t = data.get("Title")
            if isinstance(t, str) and t:
                title = t
    if not isinstance(title, str) or not title:
        kind = f.get("kind")
        title = kind if isinstance(kind, str) and kind else "finding"
    if len(title) > _TITLE_MAX:
        return title[:_TITLE_MAX], title
    return title, None


def to_findings(normalized: dict, source: str, campaign_id: str,
                run_id: str) -> list[dict]:
    """Map a normalized adapter output to the canonical Finding schema.

    Emits schema-conformant Finding objects (schema_version, finding_id,
    campaign_id, run_id, source, severity, title, location{path,line,column},
    references, extensions). Unknown severities map to "info" with the
    original preserved in extensions.original_severity. Over-long titles are
    truncated to 512 with the full value preserved in extensions.original_title.
    Every result is validated against the Finding schema before return.
    """
    from .. import contracts  # local import to avoid cycle
    normalized = _as_dict(normalized, "normalized")
    source = _as_str(source, "source")
    campaign_id = _as_str(campaign_id, "campaign_id")
    run_id = _as_str(run_id, "run_id")
    raw_findings = _as_list(normalized.get("findings", []), "findings")
    out: list[dict] = []
    for f in raw_findings:
        f = _as_dict(f, "finding")
        sid = _as_str(f.get("stable_id", ""), "stable_id")
        _require(re.fullmatch(r"[0-9a-f]{64}", sid) is not None,
                 "stable_id must be 64 hex")
        data = f.get("data") if isinstance(f.get("data"), dict) else {}
        raw_sev = f.get("severity")
        if raw_sev is None and isinstance(data, dict):
            raw_sev = data.get("Severity")
        sev, original_sev = _map_severity(raw_sev)
        title, original_title = _extract_title(f)
        location = _extract_location(f)
        refs = _extract_references(f)
        extensions: dict = {
            "kind": f.get("kind"),
            "data": f.get("data"),
            "source_snapshot": f.get("source_snapshot"),
        }
        if original_sev is not None:
            extensions["original_severity"] = original_sev
        if original_title is not None:
            extensions["original_title"] = original_title
        ext_bytes = jsonio.canonical(extensions)
        if len(ext_bytes) > _EXTENSIONS_CAP:
            raise jsonio.DataError(
                "extensions exceed 64KiB; input rejected", "input_invalid")
        finding = {
            "schema_version": "1.0.0",
            "finding_id": sid,
            "campaign_id": campaign_id,
            "run_id": run_id,
            "source": source,
            "severity": sev,
            "title": title,
            "location": location,
            "references": refs,
            "extensions": extensions,
        }
        contracts.validate("finding", finding)
        out.append(finding)
    return out


# ---------------------------------------------------------------------------
# llm
# ---------------------------------------------------------------------------


def normalize_llm(raw: bytes, *, request_body: dict | None = None) -> dict:
    """LLM response -> normalized record with measured metadata.

    effective_model and prompt_hash are computed from request/response
    bodies, not self-reported. token_count stays None if not provided.
    Ollama done:false / done_reason:length and OAI finish_reason:length /
    content_filter are NOT treated as completion.
    """
    doc = jsonio.loads(raw)
    _require(isinstance(doc, dict), "llm response must be object")
    # Ollama native: {message:{content}, model, done, done_reason, ...}
    # OpenAI compatible: {choices:[{message:{content}, finish_reason}], model, usage}
    content = None
    effective_model = None
    finish_reason = None
    done = None
    done_reason = None
    tool_calls: list = []
    native = False
    if "message" in doc:
        native = True
        msg = _as_dict(doc["message"], "message")
        content = msg.get("content")
        mtc = msg.get("tool_calls")
        if isinstance(mtc, list):
            tool_calls = mtc
        effective_model = doc.get("model")
        done = doc.get("done")
        done_reason = doc.get("done_reason")
    elif "choices" in doc:
        choices = _as_list(doc["choices"], "choices")
        if choices:
            c0 = _as_dict(choices[0], "choice")
            msg = _as_dict(c0.get("message", {}), "message")
            content = msg.get("content")
            mtc = msg.get("tool_calls")
            if isinstance(mtc, list):
                tool_calls = mtc
            finish_reason = c0.get("finish_reason")
        effective_model = doc.get("model")
    else:
        raise jsonio.DataError("llm response missing message/choices")
    usage = doc.get("usage")
    token_count = None
    if native:
        pec = doc.get("prompt_eval_count")
        ec = doc.get("eval_count")
        if (isinstance(pec, int) and not isinstance(pec, bool) and pec >= 0
                and isinstance(ec, int) and not isinstance(ec, bool) and ec >= 0):
            token_count = pec + ec
    elif isinstance(usage, dict):
        tc = usage.get("total_tokens")
        if isinstance(tc, int) and not isinstance(tc, bool):
            token_count = tc
    prompt_hash = None
    if request_body is not None:
        prompt_hash = jsonio.digest(request_body)
    # Completion semantics: truncated / filtered / missing metadata are not complete.
    has_content = isinstance(content, str) and len(content) > 0
    has_tool_calls = len(tool_calls) > 0
    if native:
        complete = (done is True
                    and done_reason in ("stop", "tool_calls")
                    and (has_content or has_tool_calls))
    else:
        complete = (finish_reason in ("stop", "tool_calls")
                    and (has_content or has_tool_calls))
    return {
        "schema_version": "1.0.0",
        "content": content,
        "effective_model": effective_model,
        "prompt_hash": prompt_hash,
        "token_count": token_count,
        "complete": complete,
        "finish_reason": finish_reason,
        "done": done,
        "done_reason": done_reason,
        "tool_calls": tool_calls,
        "response_metadata": {k: v for k, v in doc.items()
                              if k not in ("message", "choices", "model")},
    }
