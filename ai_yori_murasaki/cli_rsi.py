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

    raise ValueError(f"unknown rsi action: {action}")
