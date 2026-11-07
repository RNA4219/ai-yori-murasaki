import argparse
import uuid
from pathlib import Path

from .jsonio import read_json as _json_read, DataError
from .assessment import import_artifact, load_report
from . import assessment_enrichment
from .assessment_contracts import (
    AssessmentContractError,
    validate_assessment,
    semantic_validate_assessment,
)
from .severity import score_cvss4


_MAX_IMPORT_BYTES = 1024 * 1024


def _read_json(path):
    return _json_read(Path(path))


def _read_bounded(path):
    p = Path(path)
    if not p.is_file():
        raise DataError("input_not_regular_file")
    if p.stat().st_size > _MAX_IMPORT_BYTES:
        raise DataError("input_too_large")
    return p.read_bytes()


def add_parser(subparsers):
    p = subparsers.add_parser("assess", help="評価操作")
    p.set_defaults(group="assess")
    sub = p.add_subparsers(dest="action", required=True)

    imp = sub.add_parser("import", help="成果物を取り込む")
    imp.add_argument("--format", required=True, choices=["trivy", "sarif"])
    imp.add_argument("--input", dest="input_path", required=True)
    imp.add_argument("--context", dest="context_path", required=True)
    imp.add_argument("--campaign", dest="campaign_id", required=True)
    imp.add_argument("--run", dest="run_id", required=True)
    imp.add_argument("--kev-meta", dest="kev_meta_path", default=None)
    imp.add_argument("--epss-meta", dest="epss_meta_path", default=None)

    sh = sub.add_parser("show", help="レポートを表示する")
    sh.add_argument("--campaign", dest="campaign_id", required=True)

    cv = sub.add_parser("cvss", help="CVSSを評価する")
    cv.add_argument("--vector", required=True)
    cv.add_argument("--basis", dest="basis_path", required=True)
    cv.add_argument("--campaign", dest="campaign_id", default=None)
    cv.add_argument("--run", dest="run_id", default=None)

    lab = sub.add_parser("lab", help="評価ラボを実行する")
    lab.add_argument("--staging-root", dest="staging_root", required=True)
    lab.add_argument("--manifest", dest="manifest", required=True)
    lab.add_argument("--campaign", dest="campaign_id", default=None)
    lab.add_argument("--run", dest="run_id", default=None)

    st = sub.add_parser("study", help="評価スタディを実行する")
    st.add_argument("--plan", dest="plan_path", required=True)
    st.add_argument("--results", dest="results_path", required=True)
    st.add_argument("--campaign", dest="campaign_id", default=None)
    st.add_argument("--run", dest="run_id", default=None)

    return p


_META_REQUIRED_KEYS = (
    "raw_sha256", "content_digest", "kind", "source_url",
    "source_version", "fetched_at", "as_of", "expires_at",
)


def _load_meta(path, expected_kind):
    meta = _read_json(path)
    if not isinstance(meta, dict):
        raise DataError("meta_invalid", "input_invalid")
    for key in _META_REQUIRED_KEYS:
        if key not in meta:
            raise DataError(f"meta_missing_{key}", "input_invalid")
    if meta.get("kind") != expected_kind:
        raise DataError("meta_kind_mismatch", "input_invalid")
    return meta


def _do_import(args, rt):
    raw = _read_bounded(args.input_path)
    ctx = _read_json(args.context_path)
    if not isinstance(ctx, dict):
        raise DataError("context_invalid")
    kev_meta = None
    epss_meta = None
    if getattr(args, "kev_meta_path", None):
        kev_meta = _load_meta(args.kev_meta_path, "kev")
    if getattr(args, "epss_meta_path", None):
        epss_meta = _load_meta(args.epss_meta_path, "epss")
    result = import_artifact(
        rt, args.campaign_id, args.run_id, args.format, raw, ctx,
    )
    if kev_meta is None and epss_meta is None:
        return args.campaign_id, args.run_id, result, 0

    report = rt.store.get_object(result["report_ref"])
    if not isinstance(report, dict):
        raise DataError("report_unresolved", "hold")
    findings = [
        {"finding_id": f["finding_id"], "classification": f["classification"]}
        for f in report.get("findings") or []
        if isinstance(f, dict)
    ]
    try:
        records = assessment_enrichment.build_enrichment_records(
            rt.store, findings, kev_ref=kev_meta, epss_ref=epss_meta)
        assessment_enrichment.validate_enrichment_records(
            records, {f["finding_id"] for f in findings})
    except assessment_enrichment.EnrichmentError as exc:
        raise DataError(f"enrichment_invalid: {exc}", "input_invalid") from exc

    new_report = dict(report)
    new_report["enrichment"] = records
    try:
        validate_assessment("assessment_report", new_report)
        semantic_validate_assessment("assessment_report", new_report, rt.store)
    except AssessmentContractError as exc:
        raise DataError(f"enrichment_invalid: {exc}", "input_invalid") from exc

    new_ref = rt.store.put_object(new_report)
    rt.record_event(
        args.run_id, "state_captured", "orchestrator",
        evidence_refs=(new_ref,),
        extensions={
            "assessment_version": report["assessment_version"],
            "assessment_ref": new_ref,
            "assessment_target_version": report["target_version"],
        },
    )
    result = dict(result)
    result["enrichment_ref"] = new_ref
    return args.campaign_id, args.run_id, result, 0


def _do_show(args, rt):
    report = load_report(rt, args.campaign_id, view="operator")
    if report is None:
        return args.campaign_id, None, {"report": None}, 3
    return args.campaign_id, None, {"report": report}, 0


def _do_cvss(args, rt):
    basis = _read_json(args.basis_path)
    if not isinstance(basis, dict):
        raise DataError("basis_invalid")
    try:
        record = score_cvss4(args.vector, basis, rt.store)
    except DataError:
        raise
    except ValueError:
        return args.campaign_id, args.run_id, {"status": "invalid"}, 2
    status = record.get("status")
    if status not in ("scored", "invalid", "unevaluated"):
        return args.campaign_id, args.run_id, {"status": "unevaluated"}, 3
    ref = rt.store.put_object(record)
    code = {"scored": 0, "invalid": 2, "unevaluated": 3}[status]
    return args.campaign_id, args.run_id, {
        "cvss_ref": ref, "status": status, "record": record,
    }, code


def _do_lab(args, rt):
    try:
        from .benchmark import run_assessment_lab
    except ImportError:
        raise DataError("評価ラボは未実装です", "hold")
    result = run_assessment_lab(rt, args.staging_root, args.manifest)
    status = result.get("status")
    code = {"pass": 0, "fail": 1, "hold": 3}.get(status, 3)
    return (
        result.get("campaign_id", args.campaign_id),
        result.get("run_id", args.run_id),
        result, code,
    )


def _do_study(args, rt):
    try:
        from .study import evaluate_study
    except ImportError:
        raise DataError("評価スタディは未実装です", "hold")
    result = evaluate_study(rt, args.plan_path, args.results_path)
    status = result.get("status")
    code = {"pass": 0, "fail": 1, "hold": 3}.get(status, 3)
    return (
        result.get("campaign_id", args.campaign_id),
        result.get("run_id", args.run_id),
        result, code,
    )


def dispatch(args, rt):
    action = args.action
    if action == "import":
        return _do_import(args, rt)
    if action == "show":
        return _do_show(args, rt)
    if action == "cvss":
        return _do_cvss(args, rt)
    if action == "lab":
        return _do_lab(args, rt)
    if action == "study":
        return _do_study(args, rt)
    raise DataError("unknown_action")
