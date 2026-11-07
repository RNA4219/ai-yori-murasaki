"""Brokered tool execution for the lab agents.

The ToolBroker is the single execution path for agent-requested actions.
Every request is checked against the role's allowlisted grants and
strictly validated before execution (evidence -> plan -> policy check ->
tool execution). Unlisted tools, malformed arguments and fabricated
evidence references are rejected and returned to the agent as errors;
they are never silently executed.

Attribution: events recorded here use source="llm" only for actions the
LLM actually decided (tool requests, findings, patch proposals). The
broker itself does not fabricate LLM attribution.
"""
from __future__ import annotations

import ast
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

from .. import contracts
from ..jsonio import DataError, canonical, digest
from .policies import ALLOWED_TOOLS

_OBS_DATA_CAP = 64 * 1024
_PATH_MAX = 512
_TITLE_MAX = 512
_TEXT_MAX = 2000
_REASON_MAX = 512
_LINE_WINDOW = 200
_LINE_BYTES = 32 * 1024
_EVIDENCE_REFS_MAX = 32
_REPORT_CAP = 16 * 1024

_CATEGORIES = ("authz", "sqli", "xss", "path_traversal", "dependency_cve")
_SEVERITIES = ("critical", "high", "medium", "low", "info")
_METHODS = ("GET", "POST")

ProbeExecutor = Callable[[str], Dict[str, Any]]
UnitValidator = Callable[[str], Dict[str, Any]]


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def build_tool_schemas(grants: Optional[List[str]] = None,
                      role: Optional[str] = None) -> Dict[str, Any]:
    """Return the tool catalog serialized into agent system prompts.

    ``grants`` filters the catalog to the tools the role may actually
    call; ``role`` narrows the stop-report description to this role's
    report format so the model cannot pick another role's shape.
    """
    if role == "red":
        stop_report = ("報告オブジェクト。redの報告はこの形式のみ: "
                       "{\"unauthorized_access\": <true|false>, "
                       "\"evidence_refs\": [<観測したプローブ結果のref>...]}")
    elif role == "blue":
        stop_report = ("報告オブジェクト。blueの報告はこの形式のみ: "
                       "{\"findings_submitted\": <submit_findingで報告した件数(整数)>}")
    elif role == "purple":
        stop_report = ("報告オブジェクト。purpleの報告はこの形式のみ: "
                       "{\"proposal_ref\": <propose_patchの結果で受け取ったref>}")
    else:
        stop_report = ("役割別の報告オブジェクト。red: {\"unauthorized_access\": <bool>, "
                       "\"evidence_refs\": [<観測ref>...]} / blue: {\"findings_submitted\": <数> } / "
                       "purple: {\"proposal_ref\": <propose_patchで受け取ったref>}")
    catalog: Dict[str, Any] = {
        "probe_endpoint": {
            "description": "対象ホストの固定ポート8080へGETでプローブする。",
            "args": {"path": "URLパス。'/'で開始、512文字まで。例: /record?role=guest"},
        },
        "read_evidence": {
            "description": "観測やツール結果で受け取ったevidence refの中身を読む。",
            "args": {"ref": "evidence ref (64桁hex)"},
        },
        "submit_finding": {
            "description": "不審なアクセス1行を検知として報告する。1回の報告は1つの行のみ。その行のroleとevidence refを正確に使うこと。別の行は別の報告として提出する。",
            "args": {
                "category": "authz|sqli|xss|path_traversal|dependency_cve",
                "severity": "critical|high|medium|low|info",
                "title": "標題(日本語、512文字まで)",
                "path": "対象パス",
                "method": "GET|POST",
                "role": "この報告対象行のrole（その行のrole値をそのまま）",
                "evidence_refs": ["この報告対象行のref（1報告につきその行のref）"],
                "rationale": "根拠(日本語、2000文字まで)",
            },
        },
        "submit_hypothesis": {
            "description": "調査仮説を証拠付きで記録する。",
            "args": {"hypothesis": "仮説(日本語)", "evidence_refs": ["ref ..."]},
        },
        "list_target_files": {
            "description": "対象コードのファイル一覧を返す。",
            "args": {},
        },
        "read_target_file": {
            "description": "対象コードの一部を行番号付きで読む。",
            "args": {"path": "ファイル名", "start_line": "1以上(省略可)",
                     "end_line": "(省略可)"},
        },
        "propose_patch": {
            "description": "対象ファイルの修正案を提出する。検証結果が返る。",
            "args": {
                "root_cause": "原因(日本語、2000文字まで)",
                "rationale": "修正方針(日本語、2000文字まで)",
                "new_content": "修正後のファイル全文",
            },
        },
        "stop": {
            "description": "調査を終了し、報告する。",
            "args": {
                "reason": "理由(日本語、512文字まで)",
                "report": stop_report,
            },
        },
    }
    if grants is not None:
        return {name: catalog[name] for name in catalog if name in grants}
    return catalog


def _is_hex_ref(value: Any) -> bool:
    return (isinstance(value, str) and len(value) == 64
            and all(c in "0123456789abcdef" for c in value))


def _is_printable_path(path: str) -> bool:
    return all(33 <= ord(c) <= 126 for c in path)


def _error(tool: str, reason: str) -> Dict[str, Any]:
    return {"type": "tool_error", "tool": tool, "error": reason}


class _ArgError(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class ToolBroker:
    """Grant-checked, argument-validated tool executor for one agent loop."""

    def __init__(self, *, runtime: Any, campaign_id: str, run_id: str,
                 role: str, grants: List[str], target_dir: Optional[Any] = None,
                 probe_executor: Optional[ProbeExecutor] = None,
                 unit_validator: Optional[UnitValidator] = None,
                 max_fix_attempts: Optional[int] = None) -> None:
        self._runtime = runtime
        self._campaign_id = campaign_id
        self._run_id = run_id
        self._role = role
        self._grants = set(grants)
        self._target_dir = target_dir
        self._probe_executor = probe_executor
        self._unit_validator = unit_validator
        self._max_fix_attempts = max_fix_attempts
        self._issued_refs: Set[str] = set()
        self._proposed_count = 0
        for grant in self._grants:
            if grant not in ALLOWED_TOOLS:
                raise DataError("unknown tool grant", "input_invalid")

    # -- issued evidence bookkeeping -------------------------------------

    def issue_refs(self, refs: List[str]) -> None:
        """Register evidence references handed to the agent as input."""
        if not isinstance(refs, (list, tuple)):
            raise DataError("refs must be list", "input_invalid")
        for ref in refs:
            if not _is_hex_ref(ref):
                raise DataError("invalid evidence ref", "input_invalid")
            self._issued_refs.add(ref)

    @property
    def issued_refs(self) -> Set[str]:
        return set(self._issued_refs)

    # -- event helpers ----------------------------------------------------

    def _record(self, event_type: str, extensions: Dict[str, Any],
                evidence_refs: tuple = (), finding: Optional[dict] = None) -> None:
        try:
            self._runtime.record_event(
                self._run_id, event_type, "llm",
                evidence_refs=tuple(evidence_refs[:_EVIDENCE_REFS_MAX]),
                finding=finding, extensions=extensions)
        except DataError:
            raise
        except Exception:
            # Recording failures must not crash the loop; the caller's own
            # evidence chain remains authoritative.
            pass

    # -- argument validators ------------------------------------------------

    def _require_keys(self, args: Any, keys: Set[str],
                      optional: Optional[Set[str]] = None) -> None:
        if not isinstance(args, dict):
            raise _ArgError("args must be object")
        optional = optional or set()
        expected = keys | optional
        if not set(args.keys()) <= expected or not keys <= set(args.keys()):
            raise _ArgError(
                "args keys invalid: required={}, optional={}".format(
                    sorted(keys), sorted(optional)))

    # -- execution ---------------------------------------------------------

    def execute(self, tool: Any, args: Any) -> Dict[str, Any]:
        if not isinstance(tool, str) or tool not in self._grants:
            self._record("state_captured", {
                "record_type": "agent_tool_denied", "tool": str(tool)[:64],
                "reason": "tool_not_permitted"})
            return _error(str(tool)[:64], "tool_not_permitted")
        handler = getattr(self, "_run_" + tool, None)
        if handler is None:
            self._record("state_captured", {
                "record_type": "agent_tool_denied", "tool": tool,
                "reason": "tool_not_implemented"})
            return _error(tool, "tool_not_implemented")
        try:
            return handler(args)
        except _ArgError as exc:
            self._record("state_captured", {
                "record_type": "agent_tool_denied", "tool": tool,
                "reason": exc.reason})
            return _error(tool, exc.reason)
        except Exception:
            self._record("state_captured", {
                "record_type": "agent_tool_denied", "tool": tool,
                "reason": "internal_error"})
            return _error(tool, "internal_error")

    # read_evidence -------------------------------------------------------

    def _run_read_evidence(self, args: Any) -> Dict[str, Any]:
        self._require_keys(args, {"ref"})
        ref = args["ref"]
        if not _is_hex_ref(ref) or ref not in self._issued_refs:
            raise _ArgError("unknown_evidence_ref")
        obj = self._runtime.store.get_object(ref)
        if not isinstance(obj, dict):
            raise _ArgError("evidence_not_object")
        if len(canonical(obj)) > _OBS_DATA_CAP:
            raise _ArgError("evidence_too_large")
        return {"type": "tool_result", "tool": "read_evidence",
                "result": {"ref": ref, "object": obj}}

    # probe_endpoint --------------------------------------------------------

    def _run_probe_endpoint(self, args: Any) -> Dict[str, Any]:
        self._require_keys(args, {"path"})
        path = args["path"]
        if (not isinstance(path, str) or not path.startswith("/")
                or len(path) > _PATH_MAX or ".." in path
                or not _is_printable_path(path)):
            raise _ArgError("invalid_probe_path")
        if self._probe_executor is None:
            raise _ArgError("probe_executor_unavailable")
        obs = self._probe_executor(path)
        if not isinstance(obs, dict):
            raise _ArgError("probe_failed")
        ref = obs.get("observation_ref")
        if not _is_hex_ref(ref):
            cas_obj = {k: v for k, v in obs.items() if k != "observation_ref"}
            ref = self._runtime.store.put_object(cas_obj)
        obs = {k: v for k, v in obs.items() if k != "observation_ref"}
        self._issued_refs.add(ref)
        self._record("attack_executed", {
            "record_type": "agent_tool", "tool": "probe_endpoint",
            "path": path[:_PATH_MAX]}, evidence_refs=(ref,))
        return {"type": "tool_result", "tool": "probe_endpoint",
                "result": {"ref": ref, "observation": obs}}

    # submit_finding ---------------------------------------------------------

    def _run_submit_finding(self, args: Any) -> Dict[str, Any]:
        self._require_keys(args, {"category", "severity", "title", "path",
                                  "method", "role", "evidence_refs",
                                  "rationale"})
        category = args["category"]
        severity = args["severity"]
        title = args["title"]
        path = args["path"]
        method = args["method"]
        role = args["role"]
        refs = args["evidence_refs"]
        rationale = args["rationale"]
        if category not in _CATEGORIES:
            raise _ArgError("invalid_category")
        if severity not in _SEVERITIES:
            raise _ArgError("invalid_severity")
        if not isinstance(title, str) or not (1 <= len(title) <= _TITLE_MAX):
            raise _ArgError("invalid_title")
        if not isinstance(path, str) or not (1 <= len(path) <= _PATH_MAX):
            raise _ArgError("invalid_path")
        if method not in _METHODS:
            raise _ArgError("invalid_method")
        if not isinstance(role, str) or not (1 <= len(role) <= 64):
            raise _ArgError("invalid_role")
        if not isinstance(rationale, str) or not (
                1 <= len(rationale) <= _TEXT_MAX):
            raise _ArgError("invalid_rationale")
        if (not isinstance(refs, list) or not refs
                or len(refs) > _EVIDENCE_REFS_MAX):
            raise _ArgError("invalid_evidence_refs")
        for ref in refs:
            if not _is_hex_ref(ref) or ref not in self._issued_refs:
                raise _ArgError("unknown_evidence_ref")
        finding_id = "f-" + digest({
            "campaign": self._campaign_id, "run": self._run_id,
            "path": path, "role": role, "category": category})[:32]
        for ref in self._runtime.list_records(self._campaign_id, "finding"):
            try:
                existing = self._runtime.store.get_object(ref)
            except Exception:
                continue
            if isinstance(existing, dict) and existing.get(
                    "finding_id") == finding_id:
                return {"type": "tool_result", "tool": "submit_finding",
                        "result": {"finding_id": finding_id,
                                   "duplicate": True,
                                   "message": "この内容はすでに報告済みです。"
                                   "重複した再報告は不要です。別の行を報告する"
                                   "場合は、その行のroleとevidence refに置き"
                                   "換えて提出してください。"}}
        finding = {
            "schema_version": "1.0.0",
            "finding_id": finding_id,
            "campaign_id": self._campaign_id,
            "run_id": self._run_id,
            "source": "llm",
            "severity": severity,
            "title": title,
            "location": {"path": path, "line": None, "column": None},
            "references": list(refs),
        }
        contracts.validate("finding", finding)
        self._runtime.register_record("finding", finding, run_id=self._run_id)
        self._record("prioritized", {
            "record_type": "agent_tool", "tool": "submit_finding",
            "category": category, "path": path[:_PATH_MAX],
            "role": role[:64], "rationale": rationale[:_TEXT_MAX],
            "generated_by": "llm"}, evidence_refs=tuple(refs),
            finding={"finding_id": finding_id, "severity": severity,
                     "title": title[:_TITLE_MAX]})
        return {"type": "tool_result", "tool": "submit_finding",
                "result": {"finding_id": finding_id, "duplicate": False}}

    # submit_hypothesis --------------------------------------------------------

    def _run_submit_hypothesis(self, args: Any) -> Dict[str, Any]:
        self._require_keys(args, {"hypothesis", "evidence_refs"})
        hypothesis = args["hypothesis"]
        refs = args["evidence_refs"]
        if not isinstance(hypothesis, str) or not (
                1 <= len(hypothesis) <= _TEXT_MAX):
            raise _ArgError("invalid_hypothesis")
        if (not isinstance(refs, list) or not refs
                or len(refs) > _EVIDENCE_REFS_MAX):
            raise _ArgError("invalid_evidence_refs")
        for ref in refs:
            if not _is_hex_ref(ref) or ref not in self._issued_refs:
                raise _ArgError("unknown_evidence_ref")
        record = {"hypothesis": hypothesis, "evidence_refs": list(refs),
                  "created_at": _now()}
        ref = self._runtime.store.put_object(record)
        self._issued_refs.add(ref)
        self._record("state_captured", {
            "record_type": "agent_hypothesis", "hypothesis": hypothesis[:512],
            "evidence_count": len(refs)}, evidence_refs=(ref,))
        return {"type": "tool_result", "tool": "submit_hypothesis",
                "result": {"hypothesis_ref": ref}}

    # list_target_files ---------------------------------------------------------

    def _run_list_target_files(self, args: Any) -> Dict[str, Any]:
        self._require_keys(args, set())
        if self._target_dir is None:
            raise _ArgError("target_unavailable")
        import os
        files: List[str] = []
        for name in sorted(os.listdir(self._target_dir)):
            full = os.path.join(str(self._target_dir), name)
            if os.path.isfile(full) and not os.path.islink(full):
                files.append(name)
        return {"type": "tool_result", "tool": "list_target_files",
                "result": {"files": files}}

    # read_target_file ------------------------------------------------------------

    def _run_read_target_file(self, args: Any) -> Dict[str, Any]:
        self._require_keys(args, {"path"}, optional={"start_line",
                                                    "end_line"})
        path = args["path"]
        start_line = args["start_line"]
        end_line = args["end_line"]
        if self._target_dir is None:
            raise _ArgError("target_unavailable")
        if not isinstance(path, str) or not path or "/" in path or "\\" in path:
            raise _ArgError("invalid_file")
        import os
        full = os.path.join(str(self._target_dir), path)
        if (not os.path.isfile(full) or os.path.islink(full)
                or os.path.dirname(full) != str(self._target_dir)):
            raise _ArgError("unknown_file")
        with open(full, "r", encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()
        total = len(lines)
        if start_line is None:
            start_line = 1
        if end_line is None:
            end_line = total
        for value in (start_line, end_line):
            if not isinstance(value, int) or isinstance(value, bool):
                raise _ArgError("invalid_line_range")
            if not (1 <= value <= 10 ** 6):
                raise _ArgError("invalid_line_range")
        if start_line > end_line:
            raise _ArgError("invalid_line_range")
        if end_line - start_line + 1 > _LINE_WINDOW:
            raise _ArgError("range_too_large")
        selected = lines[start_line - 1:end_line]
        content = "".join(
            "{}: {}".format(start_line + idx, line.rstrip("\n\r"))
            for idx, line in enumerate(selected))
        if len(content.encode("utf-8")) > _LINE_BYTES:
            raise _ArgError("range_too_large")
        return {"type": "tool_result", "tool": "read_target_file",
                "result": {"path": path, "total_lines": total,
                            "start_line": start_line, "end_line": end_line,
                            "content": content}}

    # propose_patch -----------------------------------------------------------------

    def _run_propose_patch(self, args: Any) -> Dict[str, Any]:
        self._require_keys(args, {"root_cause", "rationale", "new_content"})
        root_cause = args["root_cause"]
        rationale = args["rationale"]
        new_content = args["new_content"]
        if not isinstance(root_cause, str) or not (
                1 <= len(root_cause) <= _TEXT_MAX):
            raise _ArgError("invalid_root_cause")
        if not isinstance(rationale, str) or not (
                1 <= len(rationale) <= _TEXT_MAX):
            raise _ArgError("invalid_rationale")
        if not isinstance(new_content, str):
            raise _ArgError("invalid_new_content")
        if self._target_dir is None:
            raise _ArgError("target_unavailable")
        import os
        target_path = os.path.join(str(self._target_dir), "target.py")
        if not os.path.isfile(target_path) or os.path.islink(target_path):
            raise _ArgError("target_missing")
        with open(target_path, "r", encoding="utf-8", errors="replace") as fh:
            before = fh.read()
        cap = min(65536, 2 * len(before.encode("utf-8")) + 16384)
        if len(new_content.encode("utf-8")) > cap:
            raise _ArgError("patch_too_large")
        if new_content == before:
            raise _ArgError("patch_unchanged")
        try:
            ast.parse(new_content)
        except SyntaxError as exc:
            raise _ArgError(
                "patch_syntax_error: {}".format(exc)[:300])
        if (self._max_fix_attempts is not None
                and self._proposed_count >= self._max_fix_attempts):
            raise _ArgError("fix_attempts_exhausted")
        self._proposed_count += 1
        if self._unit_validator is None:
            validation = {"static_passed": True, "unit_checks": [],
                          "unit_passed": None}
        else:
            validation = self._unit_validator(new_content)
            if not isinstance(validation, dict):
                validation = {"static_passed": False, "unit_checks": [],
                              "unit_passed": False}
        proposal = {
            "path": "target.py", "before": before, "after": new_content,
            "root_cause": root_cause, "rationale": rationale,
            "validation": validation, "created_at": _now(),
            "generated_by": "llm",
        }
        proposal_ref = self._runtime.store.put_object(proposal)
        passed = validation.get("unit_passed") is True
        if passed:
            self._issued_refs.add(proposal_ref)
        self._record("remediation_drafted", {
            "record_type": "agent_tool", "tool": "propose_patch",
            "root_cause": root_cause[:512], "generated_by": "llm"},
            evidence_refs=(proposal_ref,))
        self._record("patch_drafted", {
            "record_type": "agent_tool", "tool": "propose_patch",
            "proposal_ref": proposal_ref,
            "unit_passed": validation.get("unit_passed"),
            "generated_by": "llm"}, evidence_refs=(proposal_ref,))
        return {"type": "tool_result", "tool": "propose_patch",
                "result": {"proposal_ref": proposal_ref,
                           "validation": validation, "valid": passed}}

    # stop -------------------------------------------------------------------------

    def _run_stop(self, args: Any) -> Dict[str, Any]:
        self._require_keys(args, {"reason", "report"})
        reason = args["reason"]
        report = args["report"]
        if not isinstance(reason, str) or not (1 <= len(reason) <= _REASON_MAX):
            raise _ArgError("invalid_reason")
        if not isinstance(report, dict):
            raise _ArgError("invalid_report")
        if len(canonical(report)) > _REPORT_CAP:
            raise _ArgError("report_too_large")
        if self._role == "red":
            if set(report.keys()) != {"unauthorized_access",
                                      "evidence_refs"}:
                raise _ArgError("report_invalid")
            unauthorized = report["unauthorized_access"]
            refs = report["evidence_refs"]
            if not isinstance(unauthorized, bool):
                raise _ArgError("report_invalid")
            if (not isinstance(refs, list)
                    or len(refs) > _EVIDENCE_REFS_MAX):
                raise _ArgError("report_invalid")
            for ref in refs:
                if not _is_hex_ref(ref) or ref not in self._issued_refs:
                    raise _ArgError("unknown_evidence_ref")
            if unauthorized and not refs:
                raise _ArgError("report_invalid")
        elif self._role == "blue":
            if set(report.keys()) != {"findings_submitted"}:
                raise _ArgError("report_invalid")
            count = report["findings_submitted"]
            if not isinstance(count, int) or isinstance(count, bool) or count < 0:
                raise _ArgError("report_invalid")
        elif self._role == "purple":
            if set(report.keys()) != {"proposal_ref"}:
                raise _ArgError("report_invalid")
            ref = report["proposal_ref"]
            if not _is_hex_ref(ref) or ref not in self._issued_refs:
                raise _ArgError("unknown_evidence_ref")
            proposal = self._runtime.store.get_object(ref)
            if (not isinstance(proposal, dict)
                    or proposal.get("validation", {}).get(
                        "unit_passed") is not True):
                raise _ArgError("report_invalid")
        return {"type": "stop", "tool": "stop",
                "result": {"reason": reason, "report": report}}
