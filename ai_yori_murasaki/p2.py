"""P2: role model routing, budget-bounded purple loop, Sigma candidate tuning, platform RSS.

Local lab component. No network or docker calls are made here. The purple loop
delegates actual execution to a supplied callback and never executes arbitrary
code itself.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

from .jsonio import DataError

_DEFAULT_ROUTES = {
    "red": "deepseek-v4.1-flash:cloud",
    "purple": "deepseek-v4.1-flash:cloud",
    "blue": "glm-5.3:cloud",
    "reviewer": "glm-5.3:cloud",
}

_KNOWN_ROLES = frozenset(_DEFAULT_ROUTES)

_FAILURE_CLASSES = frozenset(
    {
        "input_format",
        "search",
        "state",
        "tool",
        "budget",
        "environment",
    }
)

_SHA64_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def route_model(role, routes=None):
    """Return the registration name for a role. Overrides apply to known roles only."""
    if not isinstance(role, str) or role not in _KNOWN_ROLES:
        raise DataError("未知のロールです: %r" % (role,))
    table = dict(_DEFAULT_ROUTES)
    if routes is not None:
        if not isinstance(routes, dict):
            raise DataError("routes は辞書である必要があります")
        for key, value in routes.items():
            if key not in _KNOWN_ROLES:
                raise DataError("未知のロール上書きです: %r" % (key,))
            if not isinstance(value, str) or not value or len(value) > 200:
                raise DataError("モデル名は1〜200文字の非空文字列である必要があります")
            table[key] = value
    model = table[role]
    if not isinstance(model, str) or not model:
        raise DataError("モデル名が空です")
    return model


def rss_bytes():
    """Return actual process RSS in bytes, or None if unavailable."""
    from .resources import measure_resources
    return measure_resources()["rss_bytes"]


def _check_int(name, value, low, high):
    if isinstance(value, bool) or not isinstance(value, int):
        raise DataError("%s は整数である必要があります" % name)
    if value < low or value > high:
        raise DataError("%s は %d〜%d の範囲である必要があります" % (name, low, high))
    return value


def _validate_step_result(result):
    if not isinstance(result, dict):
        raise DataError("step の戻り値は辞書である必要があります")
    progress = result.get("progress")
    success = result.get("success")
    if not isinstance(progress, bool):
        raise DataError("progress は真偽値である必要があります")
    if not isinstance(success, bool):
        raise DataError("success は真偽値である必要があります")
    calls = result.get("calls")
    if isinstance(calls, bool) or not isinstance(calls, int) or calls < 1:
        raise DataError("calls は1以上の整数である必要があります")
    failure_class = result.get("failure_class")
    if failure_class is not None:
        if not isinstance(failure_class, str) or failure_class not in _FAILURE_CLASSES:
            raise DataError("failure_class が不正です: %r" % (failure_class,))
    evidence = result.get("evidence_refs")
    if not isinstance(evidence, list):
        raise DataError("evidence_refs はリストである必要があります")
    for ref in evidence:
        if not isinstance(ref, str) or not _SHA64_RE.match(ref):
            raise DataError("evidence_refs はSHA-256の16進文字列である必要があります")
    return {
        "progress": progress,
        "success": success,
        "calls": calls,
        "failure_class": failure_class,
        "evidence_refs": list(evidence),
    }


def purple_loop(step, *, max_iterations, max_calls, repeat_limit=2):
    """Run a budget-bounded purple loop using the supplied execution callback."""
    if not callable(step):
        raise DataError("step は呼び出し可能である必要があります")
    _check_int("max_iterations", max_iterations, 1, 32)
    _check_int("max_calls", max_calls, 1, 1024)
    _check_int("repeat_limit", repeat_limit, 1, 8)

    history = []
    used_calls = 0
    iterations = 0
    stop_reason = None
    last_failure = None
    repeat_count = 0

    while True:
        if iterations >= max_iterations:
            stop_reason = "max_iterations"
            break
        remaining = max_calls - used_calls
        if remaining <= 0:
            stop_reason = "no_budget"
            break

        iteration = iterations + 1
        result = step(iteration, remaining)
        validated = _validate_step_result(result)

        if validated["calls"] > remaining:
            raise DataError(
                "budget_exceeded: step が残り予算を超える呼び出しを報告しました"
            )

        used_calls += validated["calls"]
        iterations = iteration
        history.append(
            {
                "iteration": iteration,
                "progress": validated["progress"],
                "success": validated["success"],
                "calls": validated["calls"],
                "failure_class": validated["failure_class"],
                "evidence_refs": validated["evidence_refs"],
            }
        )

        if validated["success"]:
            stop_reason = "success"
            break

        failure_class = validated["failure_class"]
        if failure_class == "environment":
            stop_reason = "environment_failure"
            break

        if validated["progress"]:
            last_failure = None
            repeat_count = 0
        else:
            if failure_class is not None and failure_class == last_failure:
                repeat_count += 1
            else:
                last_failure = failure_class
                repeat_count = 1
            if failure_class is not None and repeat_count >= repeat_limit:
                stop_reason = "repeat_failure"
                break

        if used_calls >= max_calls:
            stop_reason = "no_budget"
            break

    if stop_reason is None:
        stop_reason = "max_iterations"

    return {
        "iterations": iterations,
        "used_calls": used_calls,
        "stop_reason": stop_reason,
        "history": history,
        "rss_bytes": rss_bytes(),
    }


def _validate_rule(rule):
    if not isinstance(rule, dict):
        raise DataError("rule は辞書である必要があります")
    allowed = {"title", "id", "logsource", "detection"}
    for key in rule:
        if key not in allowed:
            raise DataError("rule に未知のフィールドがあります: %r" % (key,))
    title = rule.get("title")
    rule_id = rule.get("id")
    if not isinstance(title, str) or not title:
        raise DataError("rule.title は非空文字列である必要があります")
    if not isinstance(rule_id, str) or not rule_id:
        raise DataError("rule.id は非空文字列である必要があります")
    logsource = rule.get("logsource")
    if not isinstance(logsource, dict) or logsource.get("category") != "webserver":
        raise DataError("logsource.category は 'webserver' である必要があります")
    if set(logsource.keys()) != {"category"}:
        raise DataError("logsource に未知のフィールドがあります")
    detection = rule.get("detection")
    if not isinstance(detection, dict):
        raise DataError("detection は辞書である必要があります")
    if set(detection.keys()) != {"selection", "condition"}:
        raise DataError("detection に未知のフィールドがあります")
    if detection.get("condition") != "selection":
        raise DataError("detection.condition は 'selection' である必要があります")
    selection = detection.get("selection")
    if not isinstance(selection, dict):
        raise DataError("detection.selection は辞書である必要があります")
    if set(selection.keys()) != {"status", "path"}:
        raise DataError("selection は status と path のみを許可します")
    if isinstance(selection.get("status"), bool) or not isinstance(
        selection.get("status"), int
    ):
        raise DataError("selection.status は整数である必要があります")
    if not isinstance(selection.get("path"), str):
        raise DataError("selection.path は文字列である必要があります")
    return title, rule_id, selection


def _normalize_path(path):
    if not isinstance(path, str):
        raise DataError("path は文字列である必要があります")
    parts = urlsplit(path)
    return parts.path


def _validate_observations(observations):
    if not isinstance(observations, list):
        raise DataError("observations はリストである必要があります")
    if len(observations) > 256:
        raise DataError("observations は最大256件です")
    if not observations:
        raise DataError("observations が空です")
    cleaned = []
    for idx, obs in enumerate(observations):
        if not isinstance(obs, dict):
            raise DataError("observations[%d] は辞書である必要があります" % idx)
        if set(obs.keys()) != {"status", "path", "expected_detection"}:
            raise DataError("observations[%d] に未知のフィールドがあります" % idx)
        status = obs["status"]
        if isinstance(status, bool) or not isinstance(status, int):
            raise DataError("observations[%d].status は整数である必要があります" % idx)
        path = obs["path"]
        if not isinstance(path, str):
            raise DataError("observations[%d].path は文字列である必要があります" % idx)
        expected = obs["expected_detection"]
        if not isinstance(expected, bool):
            raise DataError(
                "observations[%d].expected_detection は真偽値である必要があります" % idx
            )
        cleaned.append(
            {
                "status": status,
                "path": _normalize_path(path),
                "expected_detection": expected,
            }
        )
    return cleaned


def _matrix(selection, observations):
    tp = fp = fn = tn = 0
    for obs in observations:
        matched = (
            obs["status"] == selection["status"] and obs["path"] == selection["path"]
        )
        expected = obs["expected_detection"]
        if matched and expected:
            tp += 1
        elif matched and not expected:
            fp += 1
        elif not matched and expected:
            fn += 1
        else:
            tn += 1
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn}


def tune_sigma(rule, observations):
    """Tune a Sigma candidate for the supported local HTTP metadata subset."""
    title, rule_id, selection = _validate_rule(rule)
    cleaned = _validate_observations(observations)

    positives = [o for o in cleaned if o["expected_detection"]]
    if not positives:
        raise DataError("正例の観測がありません")

    unique_pairs = {(o["status"], o["path"]) for o in positives}
    if len(unique_pairs) > 1:
        raise DataError("正例の (status, path) が複数あり、安全な単一選択にできません")

    status, path = next(iter(unique_pairs))
    candidate_selection = {"status": status, "path": path}

    candidate = {
        "title": title,
        "id": rule_id,
        "logsource": {"category": "webserver"},
        "detection": {
            "selection": candidate_selection,
            "condition": "selection",
        },
    }

    matrix = _matrix(candidate_selection, cleaned)
    baseline_matrix = _matrix(selection, cleaned)
    changed = candidate_selection != {
        "status": selection["status"],
        "path": selection["path"],
    }

    return {
        "rule": candidate,
        "matrix": matrix,
        "baseline_matrix": baseline_matrix,
        "changed": changed,
        "scope": "local-http-sigma-subset",
        "applied": False,
    }
