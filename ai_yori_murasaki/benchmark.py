"""評価ラボ（Phase 1）: manifest検証・fixture実行・混同行列・判定。

Phase 1 で実装する範囲:

- manifest の読み込みと検証（``_validate_manifest``）
- blue/oracle の fixture 実行（``assessment_sandbox.run_fixture``）
- 観測評価（``benchmark_evaluation.evaluate_observations``）
- 混同行列（``benchmark_evaluation.confusion``）
- 判定（``_decide_status``）
- 結果 dict の組み立てと任意の CAS 保存

未実装（Phase 2 以降、今回やらない）:

- ``evaluation_identity.preflight`` / ``postrun`` による凍結 manifest 検証と
  trusted_root digest 照合
- ``assessment.build_report`` への診断 producer 接続
- Runtime の状態遷移・campaign/run の作成
- holdout 管理・splits・metrics_def

このモジュールは Runtime の状態遷移を迂回しない。campaign_id/run_id は
manifest の値をそのまま報告に用い、Runtime 側の run 作成は行わない。
"""

from __future__ import annotations

from pathlib import Path

from .jsonio import DataError, read_json
from .assessment_sandbox import run_fixture
from .benchmark_evaluation import CATEGORIES, confusion, evaluate_observations
from .benchmark_freeze import finalize_formal, verify_freeze


ALLOWED_IMAGE_IDS = frozenset({
    "sha256:991b10cf0619c626209f5c47159a8f614c8aa31f738e8c2e4e5528362eeee9e8",
})

_ALLOWED_DEPS = ("old", "fixed")
_ALLOWED_DEPENDENCY_VERSIONS = ("2.31.0", "2.32.5")
_SCHEMA_VERSION = "1.0"
_MAX_SEED = 2147483647


def _is_nonempty_str(value) -> bool:
    return isinstance(value, str) and value != ""


def _validate_manifest(manifest) -> dict:
    """manifest を検証し、正規化済みパラメータを返す。

    不正は ``DataError("input_invalid")`` を送出する。
    """
    if not isinstance(manifest, dict):
        raise DataError("manifest must be an object", "input_invalid")

    required = (
        "schema_version",
        "image_id",
        "seed",
        "deps",
        "campaign_id",
        "run_id",
        "dependency_version",
    )
    for key in required:
        if key not in manifest:
            raise DataError("manifest missing key: %s" % key, "input_invalid")

    schema_version = manifest["schema_version"]
    if schema_version != _SCHEMA_VERSION:
        raise DataError("unsupported schema_version", "input_invalid")

    image_id = manifest["image_id"]
    if not isinstance(image_id, str) or image_id not in ALLOWED_IMAGE_IDS:
        raise DataError("image_id not allowed", "input_invalid")

    seed = manifest["seed"]
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise DataError("seed must be an integer", "input_invalid")
    if seed < 0 or seed > _MAX_SEED:
        raise DataError("seed out of range", "input_invalid")

    deps = manifest["deps"]
    if deps not in _ALLOWED_DEPS:
        raise DataError("deps not allowed", "input_invalid")

    campaign_id = manifest["campaign_id"]
    if not _is_nonempty_str(campaign_id):
        raise DataError("campaign_id must be a non-empty string", "input_invalid")

    run_id = manifest["run_id"]
    if not _is_nonempty_str(run_id):
        raise DataError("run_id must be a non-empty string", "input_invalid")

    dependency_version = manifest["dependency_version"]
    if dependency_version not in _ALLOWED_DEPENDENCY_VERSIONS:
        raise DataError("dependency_version not allowed", "input_invalid")

    return {
        "schema_version": schema_version,
        "image_id": image_id,
        "seed": seed,
        "deps": deps,
        "campaign_id": campaign_id,
        "run_id": run_id,
        "dependency_version": dependency_version,
    }


def _decide_status(observed, conf) -> str:
    """観測と混同行列から status を決める。

    - totals.invalid > 0 または benign が False の類型があれば ``hold``
    - FN > 0 または FP > 0 なら ``fail``
    - それ以外（TP+TN==5, invalid==0, benign 全 True）は ``pass``
    """
    totals = conf.get("totals") if isinstance(conf, dict) else None
    if not isinstance(totals, dict):
        return "hold"

    invalid = totals.get("invalid", 0)
    if not isinstance(invalid, int) or isinstance(invalid, bool):
        return "hold"
    if invalid > 0:
        return "hold"

    if not isinstance(observed, dict):
        return "hold"
    for cat in CATEGORIES:
        entry = observed.get(cat)
        if not isinstance(entry, dict):
            return "hold"
        if entry.get("benign") is not True:
            return "hold"

    fn = totals.get("FN", 0)
    fp = totals.get("FP", 0)
    if not isinstance(fn, int) or isinstance(fn, bool):
        return "hold"
    if not isinstance(fp, int) or isinstance(fp, bool):
        return "hold"
    if fn > 0 or fp > 0:
        return "fail"

    tp = totals.get("TP", 0)
    tn = totals.get("TN", 0)
    if not isinstance(tp, int) or isinstance(tp, bool):
        return "hold"
    if not isinstance(tn, int) or isinstance(tn, bool):
        return "hold"
    if tp + tn == len(CATEGORIES):
        return "pass"
    return "hold"


def _store_result(rt, result: dict) -> dict:
    """rt が store を持つ場合に CAS 保存し、lab_result_ref を付与する。

    put_object の失敗は握りつぶさず、lab_result_ref=None にして続行する。
    """
    store = getattr(rt, "store", None) if rt is not None else None
    if store is None:
        return result
    try:
        ref = store.put_object(result)
    except Exception:
        result["lab_result_ref"] = None
        return result
    result["lab_result_ref"] = ref
    return result


def run_assessment_lab(rt, staging_root, manifest_path) -> dict:
    """評価ラボ Phase 1 を実行する。

    ``rt`` は Runtime（``store`` 属性を持つ場合のみ CAS 保存に使う）。
    ``staging_root`` 配下の ``blue`` / ``oracle`` を stage として使う。
    ``manifest_path`` は JSON manifest へのパス。

    Docker 不在・資材不足など ``run_fixture`` の ``DataError`` は
    ``reason_code="hold"`` で再送出する。
    """
    manifest = read_json(Path(manifest_path))
    params = _validate_manifest(manifest)

    formal = manifest.get("formal") is True
    if formal:
        trusted_root = Path(manifest.get("trusted_root", ""))
    else:
        trusted_root = Path(".")
    freeze_info = verify_freeze(manifest, trusted_root)

    image_id = params["image_id"]
    seed = params["seed"]
    deps = params["deps"]
    dependency_version = params["dependency_version"]

    root = Path(staging_root)
    blue_stage = root / "blue"
    oracle_stage = root / "oracle"

    if not blue_stage.is_dir():
        raise DataError("blue stage missing", "hold")
    if not oracle_stage.is_dir():
        raise DataError("oracle stage missing", "hold")

    try:
        blue_result = run_fixture(blue_stage, image_id, "blue")
    except DataError as exc:
        raise DataError("blue fixture failed: %s" % exc, "hold") from exc

    try:
        oracle_result = run_fixture(oracle_stage, image_id, "oracle", seed, deps)
    except DataError as exc:
        raise DataError("oracle fixture failed: %s" % exc, "hold") from exc

    observed = evaluate_observations(oracle_result, seed, dependency_version)
    conf = confusion(observed, blue_result)
    status = _decide_status(observed, conf)

    result = {
        "status": status,
        "campaign_id": params["campaign_id"],
        "run_id": params["run_id"],
        "image_id": image_id,
        "seed": seed,
        "deps": deps,
        "dependency_version": dependency_version,
        "observed": observed,
        "confusion": conf,
        "blue_result": blue_result,
        "oracle_result": oracle_result,
    }

    result = finalize_formal(manifest, freeze_info, result, trusted_root)

    if not formal and result.get("status") == "pass":
        result["status"] = "hold"
        result["reason"] = "non_formal_run"

    result = _store_result(rt, result)
    _connect_producer(rt, manifest, params, result, freeze_info)
    return result


def _campaign_target_identifier(campaign) -> str:
    """assessment._campaign_target と同じ規則で先頭 target の identifier を返す。"""
    scope = campaign.get("scope") if isinstance(campaign, dict) else None
    targets = scope.get("targets") if isinstance(scope, dict) else None
    if not isinstance(targets, list) or len(targets) == 0:
        raise DataError("campaign has no targets", "input_invalid")
    first = targets[0]
    if isinstance(first, dict):
        ident = first.get("identifier")
    else:
        ident = first
    if not _is_nonempty_str(ident):
        raise DataError("campaign target identifier missing", "input_invalid")
    return ident


def _connect_producer(rt, manifest, params, result, freeze_info) -> None:
    """lab 結果を診断 producer（build_lab_report）へ接続する。

    ``result`` は既に CAS 保存済み（lab_result_ref 付与済み）であり、
    この関数は保存後に ``result["diagnostics"]`` を追加する。保存済みの
    lab_result には diagnostics は含まれない点に注意。

    - rt が None なら何もしない。
    - run が存在しない/ running でない場合は skipped を記録する。
    - build_lab_report の DataError は lab 本体の status を変えず failed を記録する。
    """
    if rt is None:
        return

    target_version = manifest.get("target_version")
    if not _is_nonempty_str(target_version):
        raise DataError(
            "target_version required for report connection", "input_invalid"
        )

    try:
        campaign = rt.get_campaign(params["campaign_id"])
        target = _campaign_target_identifier(campaign)
    except DataError:
        raise
    except Exception as exc:  # noqa: BLE001
        raise DataError("campaign lookup failed: %s" % exc, "input_invalid") from exc

    try:
        run = rt.get_run(params["run_id"])
    except DataError:
        result["diagnostics"] = {"status": "skipped", "reason": "run not active"}
        return
    if not isinstance(run, dict) or run.get("status") != "running":
        result["diagnostics"] = {"status": "skipped", "reason": "run not active"}
        return

    from .benchmark_diagnostics import build_lab_report

    try:
        built = build_lab_report(rt, result, freeze_info, target, target_version)
    except DataError as exc:
        result["diagnostics"] = {
            "status": "failed",
            "reason_code": exc.reason_code,
            "message": str(exc),
        }
        return

    result["diagnostics"] = {
        "status": "built",
        "report_ref": built["report_ref"],
        "diagnostic_refs": built["diagnostic_refs"],
        "auditor_exception": built.get("auditor_exception") or None,
    }
