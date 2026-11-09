"""ai_yori_murasaki.cli — v1 command line interface."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import cli_assessment, cli_rsi
from .contracts import KINDS, validate
from .evaluation_identity import DataError as EvalDataError
from .jsonio import DataError, read_json
from .policy import load as load_policy
from .policy import DataError as PolicyDataError
from .runtime import Runtime
from .storage import Store, sanitize

SCHEMA_VERSION = "1.0.0"

DENIED = {
    "policy_denied", "authorization_insufficient", "freeze_modified",
    "unpinned_comparison", "holdout_denied", "review_mismatch", "not_approved",
    "policy_violation",
}
HOLD = {
    "hold", "not_ready", "checkpoint_missing", "adapter_unavailable",
    "incomplete",
}
FAIL = {"verdict_fail", "checkpoint_mismatch"}


class _Parser(argparse.ArgumentParser):
    def error(self, message: str) -> None:  # type: ignore[override]
        raise DataError("引数が不正です", "usage_error")


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reason_code(exc: BaseException) -> str:
    code = getattr(exc, "reason_code", None)
    if code is None:
        code = getattr(exc, "code", None)
    if isinstance(code, str) and code:
        return code
    return "input_invalid"


def _status_for(reason: str) -> int:
    if reason in DENIED:
        return 4
    if reason in HOLD:
        return 3
    if reason in FAIL:
        return 1
    return 2


def _emit(command: str, status: str, reason_code: str, campaign_id: Any,
          run_id: Any, data: Any) -> None:
    envelope = {
        "schema_version": SCHEMA_VERSION,
        "command": command,
        "status": status,
        "reason_code": reason_code,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "data": data,
    }
    try:
        safe = sanitize(envelope)
    except Exception:
        safe = {
            "schema_version": SCHEMA_VERSION,
            "command": command,
            "status": status,
            "reason_code": reason_code,
            "campaign_id": None,
            "run_id": None,
            "data": None,
        }
    sys.stdout.write(json.dumps(safe, ensure_ascii=False, sort_keys=True) + "\n")
    sys.stdout.flush()


def _build_parser() -> _Parser:
    parser = _Parser(prog="aym", add_help=True)
    parser.add_argument("--workspace", type=Path,
                        default=os.environ.get("AYM_WORKSPACE"))
    parser.add_argument("--trusted-root",
                        default=os.environ.get("AYM_TRUSTED_ROOT"))
    parser.add_argument("--policy", type=Path, default=None)
    sub = parser.add_subparsers(dest="group", required=True)

    p = sub.add_parser("validate")
    p.add_argument("kind", choices=sorted(KINDS))
    p.add_argument("inputpath", type=Path)

    camp = sub.add_parser("campaign").add_subparsers(dest="action", required=True)
    c = camp.add_parser("create")
    c.add_argument("--intent-id", required=True)
    c.add_argument("--scope-file", type=Path, required=True)
    c.add_argument("--authorization-file", type=Path, required=True)
    c.add_argument("--idempotency-key", required=True)
    camp.add_parser("list")
    c = camp.add_parser("show")
    c.add_argument("--campaign-id", required=True)
    c = camp.add_parser("transition")
    c.add_argument("--campaign-id", required=True)
    c.add_argument("--to", required=True)

    run = sub.add_parser("run").add_subparsers(dest="action", required=True)
    r = run.add_parser("start")
    r.add_argument("--campaign-id", required=True)
    r.add_argument("--phase", required=True,
                   choices=["red", "blue_blind", "purple", "remediation",
                            "reevaluation", "verify"])
    r.add_argument("--seed", type=int, default=0)
    r.add_argument("--idempotency-key", required=True)
    r = run.add_parser("finish")
    r.add_argument("--run-id", required=True)
    r.add_argument("--completion", required=True,
                   choices=["completed", "incomplete", "cancelled",
                            "adapter_unavailable"])
    r.add_argument("--cause", default="none")
    r = run.add_parser("seal")
    r.add_argument("--campaign-id", required=True)
    r.add_argument("--run-id", required=True)
    r = run.add_parser("resume")
    r.add_argument("--run-id", required=True)

    cp = sub.add_parser("checkpoint").add_subparsers(dest="action", required=True)
    c = cp.add_parser("seal")
    c.add_argument("--campaign-id", required=True)
    c.add_argument("--run-id", required=True)
    c.add_argument("--operator-id", required=True)

    orc = sub.add_parser("oracle").add_subparsers(dest="action", required=True)
    o = orc.add_parser("evaluate")
    o.add_argument("--run-id", required=True)
    o.add_argument("--predicate-file", type=Path, required=True)

    ver = sub.add_parser("verify").add_subparsers(dest="action", required=True)
    for name in ("chain", "checkpoint"):
        v = ver.add_parser(name)
        v.add_argument("--campaign-id", required=True)
        v.add_argument("--run-id", required=True)

    mon = sub.add_parser("monitor").add_subparsers(dest="action", required=True)
    m = mon.add_parser("serve")
    m.add_argument("--bind", default="127.0.0.1:8765")

    lab = sub.add_parser("lab").add_subparsers(dest="action", required=True)
    d = lab.add_parser("demo")
    d.add_argument("--staging-root", type=Path, required=True)
    d.add_argument("--seed", type=int, default=1)
    d.add_argument("--routes-file", type=Path, default=None)
    d.add_argument("--scenario", default="authz-flag-false")
    d.add_argument("--policy-bundle-file", type=Path, default=None)
    it = lab.add_parser("iterate")
    it.add_argument("--staging-root", type=Path, required=True)
    it.add_argument("--seed", type=int, default=1)
    it.add_argument("--max-iterations", type=int, default=2)
    it.add_argument("--max-campaigns", type=int, default=2)
    it.add_argument("--routes-file", type=Path, default=None)
    it.add_argument("--scenario", default="authz-flag-false")
    it.add_argument("--policy-bundle-file", type=Path, default=None)

    cli_rsi.add_parser(sub)
    cli_assessment.add_parser(sub)
    from . import cli_sources
    cli_sources.add_parser(sub)
    return parser


def _command_name(args: argparse.Namespace) -> str:
    parts = [args.group]
    action = getattr(args, "action", None)
    if action:
        parts.append(action)
    return " ".join(parts)


def _resolve_routes(routes_file: Any) -> Any:
    """Resolve and load the optional routes file (explicit path only)."""
    if routes_file is None:
        return None
    path = Path(routes_file)
    if not path.is_file():
        raise DataError("routes ファイルが見つかりません: {}".format(path),
                        "input_invalid")
    routes = read_json(path)
    if not isinstance(routes, dict):
        raise DataError("routes はオブジェクトである必要があります",
                        "input_invalid")
    return routes


def _resolve_bundle(bundle_file: Any, rt: Runtime) -> Any:
    from .rsi_agent import AgentRSI
    return AgentRSI(rt.store).resolve_bundle(bundle_file)


def _resolve_scenario(scenario: Any) -> str:
    """Resolve a scenario id against the packaged scenario catalog."""
    from .agents import list_scenarios
    if scenario is None:
        scenario = "authz-flag-false"
    if not isinstance(scenario, str) or not scenario:
        raise DataError("scenario が不正です", "input_invalid")
    known = {meta["scenario_id"] for meta in list_scenarios()}
    if scenario not in known:
        raise DataError("未知の scenario です: {}".format(scenario),
                        "input_invalid")
    return scenario


def _make_runtime(args: argparse.Namespace) -> Runtime:
    if args.workspace is None:
        raise DataError("workspace が指定されていません", "usage_error")
    if not args.trusted_root:
        raise DataError("trusted-root が指定されていません", "usage_error")
    store = Store(str(args.workspace), str(args.trusted_root))
    policy_path = args.policy
    if policy_path is None:
        policy_path = Path(__file__).with_name("default-policy.json")
    policy = load_policy(str(policy_path))
    return Runtime(store, policy)


def _dispatch(args: argparse.Namespace, rt: Runtime) -> tuple[Any, Any, Any, int]:
    """Return (campaign_id, run_id, data, exit_code)."""
    group = args.group
    action = getattr(args, "action", None)

    if group == "sources":
        from .cli_sources import dispatch
        return None, None, dispatch(args, rt), 0

    if group == "assess":
        return cli_assessment.dispatch(args, rt)

    if group == "validate":
        validate(args.kind, read_json(args.inputpath))
        return None, None, {"valid": True, "kind": args.kind}, 0


    if group == "campaign":
        if action == "create":
            data = rt.create_campaign(
                args.intent_id, read_json(args.scope_file),
                read_json(args.authorization_file), args.idempotency_key)
            return data.get("campaign_id"), None, data, 0
        if action == "list":
            return None, None, {"campaigns": rt.list_campaigns()}, 0
        if action == "show":
            data = rt.get_campaign(args.campaign_id)
            return args.campaign_id, None, data, 0
        if action == "transition":
            rt.transition(args.campaign_id, args.to)
            data = rt.get_campaign(args.campaign_id)
            return args.campaign_id, None, data, 0

    if group == "run":
        if action == "start":
            data = rt.start_run(args.campaign_id, args.phase, args.seed,
                                args.idempotency_key)
            return args.campaign_id, data.get("run_id"), data, 0
        if action == "finish":
            data = rt.finish_run(args.run_id, args.completion, args.cause)
            return data.get("campaign_id"), args.run_id, data, 0
        if action == "seal":
            data = rt.seal_run(args.campaign_id, args.run_id)
            return args.campaign_id, args.run_id, data, 0
        if action == "resume":
            data = rt.resume_after_crash(args.run_id)
            return data.get("campaign_id"), args.run_id, data, 0

    if group == "checkpoint" and action == "seal":
        data = rt.store.seal_checkpoint(args.campaign_id, args.run_id,
                                        args.operator_id)
        return args.campaign_id, args.run_id, data, 0

    if group == "oracle" and action == "evaluate":
        data = rt.record_oracle(args.run_id, read_json(args.predicate_file))
        verdict = data.get("verdict")
        code = {"PASS": 0, "FAIL": 1, "HOLD": 3}.get(verdict, 2)
        return None, args.run_id, data, code

    if group == "verify" and action in ("chain", "checkpoint"):
        run = rt.get_run(args.run_id)
        if run.get("campaign_id") != args.campaign_id:
            raise DataError("cross campaign reference", "cross_campaign_ref")
        if action == "chain":
            result = rt.store.verify_chain(args.run_id)
        else:
            result = rt.store.verify_checkpoint(args.campaign_id, args.run_id)
        entry = {
            "verify_id": args.run_id,
            "run_id": args.run_id,
            "campaign_id": args.campaign_id,
            "checked_at": _now(),
            "result": result.get("result"),
            "reason_code": result.get("reason_code"),
        }
        rt.store.append_verification(entry)
        outcome = result.get("result")
        if outcome == "PASS":
            code = 0
        elif outcome == "FAIL":
            code = 1
        elif outcome == "HOLD":
            code = 3
        else:
            code = 2
        return args.campaign_id, args.run_id, result, code

    if group == "monitor" and action == "serve":
        from .server import create_server
        server = create_server(rt, args.bind)
        try:
            host, port = server.server_address[:2]
            url = "http://%s:%d" % (host, port)
            _emit("monitor serve", "ok", "ok", None, None,
                  {"url": url, "bind": args.bind})
            server.serve_forever()
        finally:
            server.server_close()
        return None, None, {"stopped": True}, 0


    if group == "lab" and action == "demo":
        from . import lab
        routes = _resolve_routes(getattr(args, "routes_file", None))
        bundle, selection = _resolve_bundle(getattr(args, "policy_bundle_file", None), rt)
        scenario_id = _resolve_scenario(getattr(args, "scenario", None))
        data = lab.run_demo(rt, args.staging_root, args.seed, routes=routes,
                            scenario_id=scenario_id,
                            policy_bundle=bundle, policy_selection=selection)
        cid = data.get("campaign_id") if isinstance(data, dict) else None
        rid = data.get("run_id") if isinstance(data, dict) else None
        return cid, rid, data, 0

    if group == "lab" and action == "iterate":
        from . import lab
        routes = _resolve_routes(getattr(args, "routes_file", None))
        bundle, selection = _resolve_bundle(getattr(args, "policy_bundle_file", None), rt)
        scenario_id = _resolve_scenario(getattr(args, "scenario", None))
        data = lab.run_iterations(
            rt, args.staging_root, seed=args.seed,
            max_iterations=args.max_iterations,
            max_campaigns=args.max_campaigns, routes=routes,
            scenario_id=scenario_id, policy_bundle=bundle, policy_selection=selection)
        cid = None
        rid = None
        return cid, rid, data, 0

    raise DataError("未対応のコマンドです", "usage_error")


def main(argv: list[str] | None = None) -> int:
    if argv is None:
        argv = sys.argv[1:]
    command = "unknown"
    try:
        parser = _build_parser()
        args = parser.parse_args(argv)
        command = _command_name(args)
        if args.group == "validate":
            validate(args.kind, read_json(args.inputpath))
            _emit(command, "ok", "ok", None, None,
                  {"valid": True, "kind": args.kind})
            return 0
        rt = _make_runtime(args)
        if args.group == "rsi":
            data = cli_rsi.dispatch(args, rt)
            cid = data.get("campaign_id") if isinstance(data, dict) else None
            rid = data.get("audit_run_id", data.get("run_id")) if isinstance(data, dict) else None
            _emit(command, "ok", "ok", cid, rid, data)
            return 0
        campaign_id, run_id, data, code = _dispatch(args, rt)
        if args.group == "monitor" and args.action == "serve":
            return code
        if code == 0:
            status, reason = "ok", "ok"
        elif code == 1:
            status, reason = "fail", "verdict_fail"
        elif code == 3:
            status = "hold"
            reason = "hold"
            if isinstance(data, dict) and data.get("reason_code"):
                reason = data["reason_code"]
        elif code == 4:
            status, reason = "denied", "policy_denied"
        else:
            status, reason = "fail", "input_invalid"
        _emit(command, status, reason, campaign_id, run_id, data)
        return code
    except KeyboardInterrupt:
        _emit(command, "hold", "interrupted", None, None, None)
        return 3
    except (DataError, EvalDataError, PolicyDataError) as exc:
        reason = _reason_code(exc)
        code = _status_for(reason)
        status = {1: "fail", 2: "fail", 3: "hold", 4: "denied"}[code]
        _emit(command, status, reason, None, None,
              {"message": "処理を完了できませんでした"})
        return code
    except (OSError, ValueError):
        _emit(command, "fail", "input_invalid", None, None,
              {"message": "入力または環境に問題があります"})
        return 2
    except Exception:
        _emit(command, "fail", "internal_error", None, None,
              {"message": "内部エラーが発生しました"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
