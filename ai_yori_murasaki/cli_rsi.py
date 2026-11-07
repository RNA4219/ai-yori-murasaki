import argparse
import uuid
from pathlib import Path

from .rsi import RSI
from . import rsi_eval
from . import rsi_train
from .jsonio import read_json as _json_read
from .policy import DataError as PolicyDataError


def _read_json(path):
    return _json_read(Path(path))


def _campaign_of(rt, freeze_id=None, candidate_id=None):
    if candidate_id is not None:
        cand = RSI(rt.store).get_candidate(candidate_id)
        return cand.get("campaign_id")
    if freeze_id is not None:
        frozen = RSI(rt.store).load_freeze(freeze_id)
        return frozen.get("campaign_id")
    return None


def _run_verify(rt, campaign_id, op, event_type, evidence_fn, extensions,
                evaluation_context=None, op_takes_run=False):
    run = rt.start_run(campaign_id, "verify", 0, str(uuid.uuid4()),
                       evaluation_context=evaluation_context)
    run_id = run["run_id"]
    try:
        result = op(run_id) if op_takes_run else op()
        refs = [rt.store.put_object(result)]
        for ref in evidence_fn(result):
            if isinstance(ref, str) and len(ref) == 64:
                rt.store.get_object(ref)
                if ref not in refs:
                    refs.append(ref)
        rt.record_event(run_id, event_type, "orchestrator", tuple(refs),
                        extensions=extensions)
        rt.finish_run(run_id, "completed")
        rt.seal_run(campaign_id, run_id)
        try:
            camp = rt.get_campaign(campaign_id)
            operator_id = camp["authorization"]["operator_id"]
        except Exception:
            operator_id = "operator"
        rt.store.seal_checkpoint(campaign_id, run_id, operator_id)
        if isinstance(result, dict):
            result.setdefault("campaign_id", campaign_id)
            result.setdefault("audit_run_id", run_id)
        return result
    except Exception as exc:
        try:
            rt.finish_run(run_id, "incomplete", "crash")
        except Exception:
            pass
        try:
            rt.seal_run(campaign_id, run_id)
        except Exception:
            pass
        try:
            camp = rt.get_campaign(campaign_id)
            operator_id = camp["authorization"]["operator_id"]
        except Exception:
            operator_id = "operator"
        try:
            rt.store.seal_checkpoint(campaign_id, run_id, operator_id)
        except Exception:
            pass
        raise exc


def _agent_executor(rt, staging_root, routes):
    """Build the real lab executor for agent RSI (record-mode outcomes)."""
    from pathlib import Path as _Path
    from . import lab
    from .adapters.llm import LLMClient
    from .adapters.base import HttpBroker
    from .rsi_agent import outcome_from_demo
    llm = LLMClient(HttpBroker(rt.policy), timeout_ms=90000, retry_cap=0)

    def executor(scenario_id, bundle, seed):
        result = lab.run_demo(rt, _Path(staging_root), seed,
                              llm_client=llm, routes=routes,
                              scenario_id=scenario_id,
                              policy_bundle=bundle, failure_mode="record")
        return outcome_from_demo(result)
    return executor


def add_parser(subparsers):
    p = subparsers.add_parser("rsi", help="RSI operations")
    p.set_defaults(group="rsi")
    sub = p.add_subparsers(dest="action", required=True)

    f = sub.add_parser("freeze")
    f.add_argument("--campaign-id", required=True)
    f.add_argument("--freeze-file", required=True)

    c = sub.add_parser("candidate")
    c.add_argument("--freeze-id", required=True)

    cmp_ = sub.add_parser("compare")
    cmp_.add_argument("--candidate-id", required=True)

    r = sub.add_parser("ready")
    r.add_argument("--candidate-id", required=True)

    rv = sub.add_parser("review")
    rv.add_argument("--candidate-id", required=True)
    rv.add_argument("--reviewer", required=True)
    rv.add_argument("--decision", required=True, choices=["approved", "rejected"])

    i = sub.add_parser("initialize")
    i.add_argument("--baseline-ref", required=True)

    a = sub.add_parser("apply")
    a.add_argument("--review-id", required=True)
    a.add_argument("--expected-baseline", required=True)
    a.add_argument("--expected-candidate", required=True)
    a.add_argument("--rollback", required=True)

    s = sub.add_parser("show")
    s.add_argument("--candidate-id", required=True)

    rf = sub.add_parser("reflect")
    rf.add_argument("--input", required=True)

    m = sub.add_parser("memory-add")
    m.add_argument("--input", required=True)
    m.add_argument("--train-ref", required=True)

    tr = sub.add_parser("train")
    tr.add_argument("--campaign-id", required=True)
    tr.add_argument("--policy-ref", required=True)
    tr.add_argument("--dataset-ref", required=True)
    tr.add_argument("--seed", type=int, default=0)

    pr = sub.add_parser("propose")
    pr.add_argument("--campaign-id", required=True)
    pr.add_argument("--train-ref", required=True)

    # ---- agent RSI (policy-bundle improvement for the lab agents) ----
    af = sub.add_parser("agent-freeze")
    af.add_argument("--campaign-id", required=True)
    af.add_argument("--freeze-file", required=True)
    af.add_argument("--baseline-bundle-file", default=None)

    at = sub.add_parser("agent-train")
    at.add_argument("--freeze-id", required=True)
    at.add_argument("--staging-root", required=True)
    at.add_argument("--routes-file", default=None)

    ac = sub.add_parser("agent-compare")
    ac.add_argument("--candidate-id", required=True)
    ac.add_argument("--staging-root", required=True)
    ac.add_argument("--routes-file", default=None)

    ar = sub.add_parser("agent-ready")
    ar.add_argument("--candidate-id", required=True)

    arv = sub.add_parser("agent-review")
    arv.add_argument("--candidate-id", required=True)
    arv.add_argument("--reviewer", required=True)
    arv.add_argument("--decision", required=True,
                     choices=["approved", "rejected"])

    aap = sub.add_parser("agent-apply")
    aap.add_argument("--review-id", required=True)
    aap.add_argument("--expected-baseline", required=True)
    aap.add_argument("--expected-candidate", required=True)
    aap.add_argument("--rollback", required=True)

    ash = sub.add_parser("agent-show")
    ash.add_argument("--candidate-id", required=True)

    return p


def dispatch(args, rt):
    rsi = RSI(rt.store)
    action = args.action

    if action == "freeze":
        cid = args.campaign_id
        rt.get_campaign(cid)
        bundle = _read_json(args.freeze_file)
        if not isinstance(bundle, dict) or set(bundle.keys()) != {"config", "manifest", "bindings", "artifact_root"}:
            raise PolicyDataError("freeze_bundle_invalid")
        config = bundle["config"]
        manifest = bundle["manifest"]
        bindings = bundle["bindings"]
        artifact_root = Path(bundle["artifact_root"])
        if not artifact_root.is_absolute():
            raise PolicyDataError("artifact_root_must_be_absolute")

        def op():
            return rsi.freeze(cid, config=config, manifest=manifest,
                              bindings=bindings, artifact_root=artifact_root)

        def ev(res):
            return [res["content_digest"]]

        return _run_verify(rt, cid, op, "rsi_frozen", ev,
                           {"record_type": "rsi_freeze", "phase": "verify"})

    if action == "candidate":
        cid = _campaign_of(rt, freeze_id=args.freeze_id)

        def op():
            return rsi.create_candidate(args.freeze_id)

        def ev(res):
            return [res["content_digest"]]

        return _run_verify(rt, cid, op, "rsi_candidate_created", ev,
                           {"record_type": "rsi_candidate", "phase": "verify"})

    if action == "compare":
        cand = rsi.get_candidate(args.candidate_id)
        cid = cand.get("campaign_id")
        frozen = rsi.load_freeze(cand["freeze_id"])
        eval_ctx = None
        mref = frozen.get("evaluation_manifest_ref")
        mid = frozen.get("evaluation_manifest_id")
        if isinstance(mref, str) and mref and isinstance(mid, str) and mid:
            eval_ctx = {"evaluation_manifest_ref": mref,
                        "evaluation_manifest_id": mid}

        def op(run_id):
            def on_holdout(audit):
                rt.record_event(
                    run_id, "state_captured", "orchestrator",
                    evidence_refs=(audit["audit_ref"],),
                    extensions={"record_type": "holdout_access",
                                "policy_operation": "holdout_access"})
            return rsi_eval.compare(rsi, args.candidate_id,
                                    on_holdout=on_holdout)

        def ev(res):
            refs = []
            if isinstance(res, dict):
                for key in ("comparison_ref", "audit_ref",
                            "holdout_access_ref"):
                    val = res.get(key)
                    if isinstance(val, str) and val:
                        try:
                            rt.store.get_object(val)
                        except Exception:
                            continue
                        refs.append(val)
            return refs

        return _run_verify(rt, cid, op, "rsi_compared", ev,
                           {"record_type": "rsi_compare", "phase": "verify"},
                           evaluation_context=eval_ctx, op_takes_run=True)

    if action == "ready":
        cid = _campaign_of(rt, candidate_id=args.candidate_id)

        def op():
            return rsi.ready(args.candidate_id)

        def ev(res):
            refs = []
            if isinstance(res, dict):
                for key in ("comparison_ref", "completed_manifest_ref"):
                    val = res.get(key)
                    if isinstance(val, str) and val:
                        try:
                            rt.store.get_object(val)
                        except Exception:
                            continue
                        refs.append(val)
            return refs

        def wrapped():
            res = op()
            out = {"ready": True}
            if isinstance(res, dict):
                out.update(res)
            return out

        return _run_verify(rt, cid, wrapped, "rsi_ready", ev,
                           {"record_type": "rsi_ready", "phase": "verify"})

    if action == "review":
        cid = _campaign_of(rt, candidate_id=args.candidate_id)

        def op():
            return rsi.record_review(args.candidate_id, args.reviewer,
                                     args.decision)

        def ev(res):
            refs = []
            if isinstance(res, dict):
                for key in ("content_digest", "review_digest"):
                    val = res.get(key)
                    if isinstance(val, str) and val:
                        try:
                            rt.store.get_object(val)
                        except Exception:
                            continue
                        refs.append(val)
            return refs

        return _run_verify(rt, cid, op, "state_captured", ev,
                           {"record_type": "rsi_review", "phase": "verify"})

    if action == "initialize":
        return rsi.initialize_registry(args.baseline_ref)

    if action == "apply":
        state = rsi._load_state()
        rev = state["reviews"].get(args.review_id)
        if not isinstance(rev, dict):
            raise PolicyDataError("review_not_found")
        candidate_id = rev.get("candidate_id")
        if not candidate_id:
            raise PolicyDataError("review_mismatch")
        cand = rsi.get_candidate(candidate_id)
        cid = cand.get("campaign_id")
        if not cid:
            raise PolicyDataError("cross_campaign_ref")

        def op():
            return rsi.apply(args.review_id, args.expected_baseline,
                             args.expected_candidate, args.rollback)

        def ev(res):
            refs = []
            if isinstance(res, dict):
                for key in ("content_digest", "apply_digest"):
                    val = res.get(key)
                    if isinstance(val, str) and val:
                        try:
                            rt.store.get_object(val)
                        except Exception:
                            continue
                        refs.append(val)
            return refs

        return _run_verify(rt, cid, op, "rsi_applied", ev,
                           {"record_type": "rsi_apply", "phase": "verify"})

    if action == "show":
        return rsi.get_candidate(args.candidate_id)

    if action == "reflect":
        return rsi.reflect(_read_json(args.input))

    if action == "memory-add":
        return rsi.add_experience(_read_json(args.input), train_ref=args.train_ref)

    if action == "train":
        cid = args.campaign_id
        rt.get_campaign(cid)

        def op():
            return rsi_train.train(rsi, args.policy_ref, args.dataset_ref, args.seed)

        def ev(res):
            refs = []
            if isinstance(res, dict):
                val = res.get("train_ref")
                if isinstance(val, str) and val:
                    try:
                        rt.store.get_object(val)
                    except Exception:
                        pass
                    else:
                        refs.append(val)
            return refs

        return _run_verify(rt, cid, op, "state_captured", ev,
                           {"record_type": "rsi_train", "phase": "verify"})

    if action == "propose":
        cid = args.campaign_id
        rt.get_campaign(cid)

        def op():
            return rsi_train.propose(rsi, args.train_ref)

        def ev(res):
            refs = []
            if isinstance(res, dict):
                for key in ("train_ref", "proposal_ref"):
                    val = res.get(key)
                    if isinstance(val, str) and val:
                        try:
                            rt.store.get_object(val)
                        except Exception:
                            continue
                        refs.append(val)
            return refs

        return _run_verify(rt, cid, op, "state_captured", ev,
                           {"record_type": "rsi_propose", "phase": "verify"})

    if action == "agent-freeze":
        cid = args.campaign_id
        rt.get_campaign(cid)
        bundle_doc = _read_json(args.freeze_file)
        if not isinstance(bundle_doc, dict) or set(
                bundle_doc.keys()) != {"config", "manifest", "artifact_root"}:
            raise PolicyDataError("agent_freeze_bundle_invalid")
        from pathlib import Path as _P
        artifact_root = _P(bundle_doc["artifact_root"])
        if not artifact_root.is_absolute():
            raise PolicyDataError("artifact_root_must_be_absolute")
        from .agents import load_default_bundle
        from .rsi_agent import AgentRSI
        baseline = load_default_bundle()
        if args.baseline_bundle_file is not None:
            baseline = _read_json(args.baseline_bundle_file)

        def op():
            return AgentRSI(rt.store).freeze(
                cid, bundle_doc["config"], bundle_doc["manifest"],
                baseline, artifact_root)

        def ev(res):
            return [res["manifest_ref"], res["baseline_ref"]]

        return _run_verify(rt, cid, op, "state_captured", ev,
                           {"record_type": "agent_rsi_freeze",
                            "phase": "verify"})

    if action == "agent-train":
        from .rsi_agent import AgentRSI
        rsi_agent = AgentRSI(rt.store)
        record = rsi_agent.load_freeze(args.freeze_id)
        cid = record["campaign_id"]
        routes = None
        if args.routes_file is not None:
            routes = _read_json(args.routes_file)
        executor = _agent_executor(rt, args.staging_root, routes)

        def op():
            return rsi_agent.train(args.freeze_id, executor)

        def ev(res):
            refs = []
            for key in ("train_ref", "candidate_bundle_ref"):
                val = res.get(key)
                if isinstance(val, str) and val:
                    refs.append(val)
            return refs

        return _run_verify(rt, cid, op, "state_captured", ev,
                           {"record_type": "agent_rsi_train",
                            "phase": "verify"})

    if action == "agent-compare":
        from .rsi_agent import AgentRSI
        rsi_agent = AgentRSI(rt.store)
        cand = rsi_agent.get_candidate(args.candidate_id)
        cid = cand.get("campaign_id")
        routes = None
        if args.routes_file is not None:
            routes = _read_json(args.routes_file)
        executor = _agent_executor(rt, args.staging_root, routes)

        def op(run_id):
            def on_holdout(audit):
                audit_ref = rt.store.put_object(audit)
                rt.record_event(
                    run_id, "state_captured", "orchestrator",
                    evidence_refs=(audit_ref,),
                    extensions={"record_type": "holdout_access",
                                "policy_operation": "holdout_access"})
            return rsi_agent.compare(args.candidate_id, executor,
                                      on_holdout=on_holdout)

        def ev(res):
            refs = []
            for key in ("comparison_ref", "holdout_access_ref"):
                val = res.get(key)
                if isinstance(val, str) and val:
                    refs.append(val)
            return refs

        return _run_verify(rt, cid, op, "rsi_compared", ev,
                           {"record_type": "agent_rsi_compare",
                            "phase": "verify"}, op_takes_run=True)

    if action == "agent-ready":
        from .rsi_agent import AgentRSI
        rsi_agent = AgentRSI(rt.store)
        cand = rsi_agent.get_candidate(args.candidate_id)
        cid = cand.get("campaign_id")

        def op():
            return rsi_agent.ready(args.candidate_id)

        def ev(res):
            return []

        return _run_verify(rt, cid, op, "rsi_ready", ev,
                           {"record_type": "agent_rsi_ready",
                            "phase": "verify"})

    if action == "agent-review":
        from .rsi_agent import AgentRSI
        rsi_agent = AgentRSI(rt.store)
        cand = rsi_agent.get_candidate(args.candidate_id)
        cid = cand.get("campaign_id")

        def op():
            return rsi_agent.record_review(args.candidate_id, args.reviewer,
                                            args.decision)

        def ev(res):
            return [cand["bundle_ref"]]

        return _run_verify(rt, cid, op, "state_captured", ev,
                           {"record_type": "agent_rsi_review",
                            "phase": "verify"})

    if action == "agent-apply":
        from .rsi_agent import AgentRSI
        rsi_agent = AgentRSI(rt.store)
        state = rsi_agent._load_state()
        review = state["reviews"].get(args.review_id)
        if not isinstance(review, dict):
            raise PolicyDataError("review_not_found")
        cand = rsi_agent.get_candidate(review["candidate_id"])
        cid = cand.get("campaign_id")

        def op():
            return rsi_agent.apply(args.review_id, args.expected_baseline,
                                   args.expected_candidate, args.rollback)

        def ev(res):
            return [cand["bundle_ref"]]

        return _run_verify(rt, cid, op, "rsi_applied", ev,
                           {"record_type": "agent_rsi_apply",
                            "phase": "verify"})

    if action == "agent-show":
        from .rsi_agent import AgentRSI
        return AgentRSI(rt.store).get_candidate(args.candidate_id)

    raise ValueError(f"unknown rsi action: {action}")
