"""RSI 凍結・比較・レビュー・適用の状態管理。

外部 API は Store と jsonio、identity モジュールのみを利用する。
すべての判定は保存済み CAS と実ファイルから再計算し、自己申告値を信用しない。
"""

from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping

from . import evaluation_identity as identity
from .jsonio import DataError, canonical, digest, loads
from .rsi_compare import _validate_comparison, _evaluate_pairs


_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_SHA_RE = re.compile(r"^[0-9a-f]{64}$")
_FAILURE_CLASSES = {
    "input_format",
    "search",
    "state",
    "tool",
    "budget",
    "environment",
}
_OUTCOMES = {"supported", "refuted", "pivot", "unverified"}
_ACTIONS = {
    "validate_input",
    "change_search",
    "restore_state",
    "change_tool",
    "reduce_scope",
    "stop",
}
_CAUSE_ACTIONS = {
    "input_format": ("validate_input", "入力形式の不整合を先に検証する"),
    "search": ("change_search", "探索方針を変更して再試行する"),
    "state": ("restore_state", "状態復元を確認してから再開する"),
    "tool": ("change_tool", "ツール選択を見直す"),
    "budget": ("reduce_scope", "予算内に収まる範囲へ縮小する"),
    "environment": ("stop", "環境要因のため停止し人間へ引き継ぐ"),
}
_EMPTY_STATE = {
    "freezes": {},
    "candidates": {},
    "reviews": {},
    "memory": [],
    "holdout_used": {},
}


def _fail(reason: str, message: str) -> None:
    raise DataError(message, reason)


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _require_id(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        _fail("input_invalid", f"{name} は小文字英数字とハイフンの識別子が必要")
    return value


def _require_sha(value: Any, name: str) -> str:
    if not isinstance(value, str) or not _SHA_RE.match(value):
        _fail("input_invalid", f"{name} は 64 桁の sha256 が必要")
    return value


def _require_str(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        _fail("input_invalid", f"{name} は非空文字列が必要")
    return value


def _require_bool(value: Any, name: str) -> bool:
    if not isinstance(value, bool):
        _fail("input_invalid", f"{name} は真偽値が必要")
    return value


def _require_int(value: Any, name: str, lo: int, hi: int) -> int:
    if not _is_int(value) or value < lo or value > hi:
        _fail("input_invalid", f"{name} は {lo}..{hi} の整数が必要")
    return value


def _require_finite(value: Any, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _fail("input_invalid", f"{name} は有限数値が必要")
    number = float(value)
    if not math.isfinite(number):
        _fail("input_invalid", f"{name} は有限数値が必要")
    return number


def _require_mapping(value: Any, name: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        _fail("input_invalid", f"{name} はマッピングが必要")
    return value


def _require_list(value: Any, name: str) -> list[Any]:
    if not isinstance(value, list):
        _fail("input_invalid", f"{name} はリストが必要")
    return value


def _exact_keys(value: Mapping[str, Any], keys: Iterable[str], name: str) -> None:
    expected = set(keys)
    if set(value.keys()) != expected:
        _fail("input_invalid", f"{name} のキー集合が不正")


def _reject_symlink(path: Path) -> None:
    current = path
    while True:
        if current.is_symlink():
            _fail("path_invalid", "シンボリックリンク経路は拒否")
        if current.parent == current:
            break
        current = current.parent


def _validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    _exact_keys(
        config,
        [
            "objective",
            "seed_pairs",
            "order_policy",
            "budget",
            "thresholds",
            "stop_conditions",
            "memory_refs",
        ],
        "config",
    )
    objective = _require_str(config.get("objective"), "config.objective")
    seeds = _require_list(config.get("seed_pairs"), "config.seed_pairs")
    if not seeds or len(seeds) > 32:
        _fail("input_invalid", "config.seed_pairs は 1..32 件が必要")
    seen: set[int] = set()
    for item in seeds:
        if not _is_int(item):
            _fail("input_invalid", "config.seed_pairs は整数のみ")
        if item in seen:
            _fail("input_invalid", "config.seed_pairs は重複不可")
        seen.add(item)
    policy = config.get("order_policy")
    if policy not in ("alternating", "baseline_first"):
        _fail("input_invalid", "config.order_policy が不正")
    budget = _require_mapping(config.get("budget"), "config.budget")
    _exact_keys(budget, ["max_pairs", "max_calls"], "config.budget")
    max_pairs = _require_int(budget.get("max_pairs"), "budget.max_pairs", 1, 256)
    max_calls = _require_int(budget.get("max_calls"), "budget.max_calls", 1, 1024)
    thresholds = _require_mapping(config.get("thresholds"), "config.thresholds")
    _exact_keys(
        thresholds,
        ["success_gain_min", "no_benign_regression", "max_safety_violations"],
        "config.thresholds",
    )
    gain_min = _require_finite(thresholds.get("success_gain_min"), "thresholds.success_gain_min")
    if gain_min < 0:
        _fail("input_invalid", "thresholds.success_gain_min は 0 以上")
    if thresholds.get("no_benign_regression") is not True:
        _fail("input_invalid", "thresholds.no_benign_regression は true 固定")
    if thresholds.get("max_safety_violations") != 0 or not _is_int(
        thresholds.get("max_safety_violations")
    ):
        _fail("input_invalid", "thresholds.max_safety_violations は 0 固定")
    stop = _require_mapping(config.get("stop_conditions"), "config.stop_conditions")
    _exact_keys(stop, ["max_iterations"], "config.stop_conditions")
    max_iter = _require_int(stop.get("max_iterations"), "stop_conditions.max_iterations", 1, 32)
    refs = _require_list(config.get("memory_refs"), "config.memory_refs")
    memory_refs = [_require_sha(item, "config.memory_refs[]") for item in refs]
    return {
        "objective": objective,
        "seed_pairs": list(seeds),
        "order_policy": policy,
        "budget": {"max_pairs": max_pairs, "max_calls": max_calls},
        "thresholds": {
            "success_gain_min": gain_min,
            "no_benign_regression": True,
            "max_safety_violations": 0,
        },
        "stop_conditions": {"max_iterations": max_iter},
        "memory_refs": memory_refs,
    }


def _validate_policy(policy: Any) -> dict[str, Any]:
    mapping = _require_mapping(policy, "policy")
    _exact_keys(mapping, ["require_admin"], "policy")
    return {"require_admin": _require_bool(mapping.get("require_admin"), "policy.require_admin")}






class RSI:
    """RSI の状態機械。Store の lock/atomic_json/CAS のみを利用する。"""

    def __init__(self, store: Any) -> None:
        self._store = store
        self._workspace = Path(store.workspace)
        self._trusted = Path(store.trusted_root)
        self._state_path = self._workspace / "rsi" / "state.json"
        self._registry_path = self._workspace / "rsi" / "registry.json"

    # ---- 内部ヘルパ ----

    def _load_state(self) -> dict[str, Any]:
        _reject_symlink(self._state_path)
        if not self._state_path.exists():
            return {k: (v.copy() if isinstance(v, dict) else list(v)) for k, v in _EMPTY_STATE.items()}
        raw = self._state_path.read_text(encoding="utf-8")
        try:
            data = loads(raw)
        except ValueError as exc:
            raise DataError("state.json を解析できない", "state_invalid") from exc
        if not isinstance(data, dict):
            _fail("state_invalid", "state.json がマッピングでない")
        for key, default in _EMPTY_STATE.items():
            if key not in data:
                data[key] = default.copy() if isinstance(default, dict) else list(default)
                continue
            value = data[key]
            if isinstance(default, dict):
                if not isinstance(value, dict):
                    _fail("state_invalid", f"state.{key} がマッピングでない")
            elif isinstance(default, list):
                if not isinstance(value, list):
                    _fail("state_invalid", f"state.{key} がリストでない")
        candidates = data["candidates"]
        for candidate_id, entry in candidates.items():
            if not isinstance(entry, dict):
                _fail("state_invalid", "candidates の要素がマッピングでない")
            for field in (
                "candidate_id",
                "freeze_id",
                "campaign_id",
                "content_digest",
                "candidate_ref",
                "applied",
            ):
                if field not in entry:
                    _fail("state_invalid", f"candidates に {field} が無い")
            if entry["candidate_id"] != candidate_id:
                _fail("state_invalid", "candidate_id がキーと不一致")
            _require_id(entry["candidate_id"], "candidate_id")
            _require_id(entry["freeze_id"], "freeze_id")
            _require_id(entry["campaign_id"], "campaign_id")
            _require_sha(entry["content_digest"], "content_digest")
            _require_sha(entry["candidate_ref"], "candidate_ref")
            if not isinstance(entry["applied"], bool):
                _fail("state_invalid", "applied が真偽値でない")
        for key in ("reviews", "freezes"):
            for entry in data[key].values():
                if not isinstance(entry, dict) or not entry:
                    _fail("state_invalid", f"{key} の要素が非空マッピングでない")
        for ref in data["memory"]:
            _require_sha(ref, "memory")
        return data

    def _save_state(self, state: dict[str, Any]) -> None:
        _reject_symlink(self._state_path)
        self._state_path.parent.mkdir(parents=True, exist_ok=True)
        self._store.atomic_json(self._state_path, state)

    def _pin_path(self, freeze_id: str) -> Path:
        return self._trusted / "rsi" / f"{freeze_id}.json"

    def _load_pin(self, freeze_id: str) -> dict[str, Any]:
        path = self._pin_path(freeze_id)
        _reject_symlink(path)
        if not path.exists():
            _fail("freeze_missing", "信頼済み凍結ピンが存在しない")
        data = loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            _fail("freeze_modified", "凍結ピンがマッピングでない")
        return data

    def _verify_freeze(self, freeze_id: str) -> dict[str, Any]:
        pin = self._load_pin(freeze_id)
        ref = _require_sha(pin.get("content_ref"), "pin.content_ref")
        frozen = self._store.get_object(ref)
        if not isinstance(frozen, dict):
            _fail("freeze_modified", "凍結 CAS がマッピングでない")
        if digest(frozen) != pin.get("content_digest"):
            _fail("freeze_modified", "凍結 CAS ダイジェストがピンと不一致")
        if frozen.get("freeze_id") != freeze_id:
            _fail("freeze_modified", "凍結 ID がピンと不一致")
        config = frozen.get("config")
        if not isinstance(config, dict):
            _fail("freeze_modified", "凍結設定がマッピングでない")
        if digest(config) != frozen.get("config_digest"):
            _fail("freeze_modified", "凍結設定ダイジェストが不一致")
        manifest_ref = _require_sha(frozen.get("manifest_ref"), "frozen.manifest_ref")
        manifest = self._store.get_object(manifest_ref)
        if not isinstance(manifest, dict):
            _fail("freeze_modified", "凍結マニフェストがマッピングでない")
        if digest(manifest) != frozen.get("manifest_digest"):
            _fail("freeze_modified", "凍結マニフェストダイジェストが不一致")
        if frozen.get("evaluation_manifest_ref") != manifest_ref:
            _fail("freeze_modified", "評価マニフェスト参照が不一致")
        if frozen.get("evaluation_manifest_id") != manifest.get("manifest_id"):
            _fail("freeze_modified", "評価マニフェスト ID が不一致")
        baseline_ref = _require_sha(frozen.get("baseline_ref"), "frozen.baseline_ref")
        candidate_ref = _require_sha(frozen.get("candidate_ref"), "frozen.candidate_ref")
        baseline_policy = self._verify_policy(baseline_ref)
        candidate_policy = self._verify_policy(candidate_ref)
        if baseline_policy != frozen.get("baseline_policy"):
            _fail("freeze_modified", "ベースラインポリシーが不一致")
        if candidate_policy != frozen.get("candidate_policy"):
            _fail("freeze_modified", "候補ポリシーが不一致")
        return frozen

    def _trusted_path(self, memory_ref: str) -> Path:
        return self._trusted / "rsi" / "memory" / (memory_ref + ".json")

    def _verify_memory(self, refs: list[str]) -> list[dict[str, Any]]:
        from . import rsi_train
        cards: list[dict[str, Any]] = []
        for ref in refs:
            card = self._store.get_object(ref)
            if not isinstance(card, dict):
                _fail("memory_invalid", "記憶カードがマッピングでない")
            if set(card.keys()) != {
                "split",
                "completion",
                "failure_class",
                "outcome",
                "action",
                "evidence_count",
            }:
                _fail("memory_invalid", "記憶カードのキーが不正")
            if card.get("split") != "train" or card.get("completion") != "completed":
                _fail("memory_invalid", "記憶カードは train/completed のみ許可")
            if card.get("failure_class") not in _FAILURE_CLASSES:
                _fail("memory_invalid", "failure_class が不正")
            if card.get("outcome") not in _OUTCOMES:
                _fail("memory_invalid", "outcome が不正")
            if card.get("action") not in _ACTIONS:
                _fail("memory_invalid", "action が不正")
            evidence_count = card.get("evidence_count")
            if isinstance(evidence_count, bool) or not isinstance(evidence_count, int) or evidence_count < 0:
                _fail("memory_invalid", "evidence_count が不正")
            if digest(card) != ref:
                _fail("memory_invalid", "記憶カードのダイジェストが参照と不一致")
            path = self._trusted_path(ref)
            _reject_symlink(path)
            try:
                pin = loads(path.read_bytes())
            except Exception:
                _fail("memory_invalid", "trusted pin が存在しない")
            if not isinstance(pin, dict) or set(pin.keys()) != {"memory_ref", "train_ref"}:
                _fail("memory_invalid", "trusted pin が不正")
            if pin.get("memory_ref") != ref:
                _fail("memory_invalid", "trusted pin の memory_ref が不一致")
            train_ref = pin.get("train_ref")
            if not isinstance(train_ref, str) or len(train_ref) != 64:
                _fail("memory_invalid", "trusted pin の train_ref が不正")
            try:
                verified = rsi_train.verify_train(self, train_ref)
            except Exception:
                _fail("memory_invalid", "train_ref の検証に失敗")
            try:
                proposed = rsi_train.propose(self, train_ref)
            except Exception:
                _fail("memory_invalid", "propose に失敗")
            expected_card = proposed.get("experience_card")
            if not isinstance(expected_card, dict) or canonical(expected_card) != canonical(card):
                _fail("memory_invalid", "記憶カードが train 結果と不一致")
            cards.append(card)
        return cards

    def _verify_policy(self, ref: str) -> dict[str, Any]:
        policy = self._store.get_object(ref)
        return _validate_policy(policy)

    def _check_artifact(
        self, comparison: dict[str, Any], completed: dict[str, Any], frozen: dict[str, Any]
    ) -> None:
        frozen_manifest = self._store.get_object(frozen["manifest_ref"])
        identity.postrun(
            completed,
            frozen_manifest,
            Path(frozen["artifact_root"]),
            frozen["bindings"],
        )
        outcome = completed.get("outcome")
        if not isinstance(outcome, dict):
            _fail("artifact_mismatch", "完了マニフェストに outcome が無い")
        artifact_path = outcome.get("artifact_path")
        if not isinstance(artifact_path, str) or not artifact_path:
            _fail("artifact_mismatch", "artifact_path が不正")
        resolved = identity._resolve_within(Path(frozen["artifact_root"]), artifact_path, "outcome.artifact_path")
        actual_bytes = identity._read_bounded(resolved, "outcome.artifact")
        actual = loads(actual_bytes)
        expected = canonical(comparison)
        if canonical(actual) != expected:
            _fail("artifact_mismatch", "成果物が比較と不一致")
        pairs = comparison.get("pairs")
        if not isinstance(pairs, list):
            _fail("artifact_mismatch", "pairs が不正")
        if outcome.get("records") != len(pairs):
            _fail("artifact_mismatch", "records 件数が不一致")
        result = _evaluate_pairs(pairs, frozen)
        if outcome.get("metrics", {}).get(frozen_manifest["evaluation"]["primary_metric"]) != result["gain"]:
            _fail("artifact_mismatch", "primary_metric が不一致")

    def _verify_ready(self, candidate_id: str) -> dict[str, Any]:
        state = self._load_state()
        candidate = state["candidates"].get(candidate_id)
        if not isinstance(candidate, dict):
            _fail("candidate_missing", "候補が存在しない")
        freeze = self.load_freeze(candidate["freeze_id"])
        from .rsi_eval import runner_snapshot
        from . import evaluation_identity as identity
        from pathlib import Path as _Path
        _root = _Path(freeze["artifact_root"])
        _bound = identity._resolve_within(
            _root, freeze["bindings"]["runner"], "runner"
        )
        if identity._read_bounded(_bound, "runner") != runner_snapshot():
            _fail("freeze_modified", "評価器bundleが変更されています")
        frozen_manifest = self._store.get_object(freeze["manifest_ref"])
        if candidate.get("content_digest") != freeze.get("candidate_ref"):
            _fail("not_ready", "候補参照が凍結と不一致")
        if candidate.get("candidate_ref") != freeze.get("candidate_ref"):
            _fail("not_ready", "候補参照が凍結と不一致")
        comparison_ref = candidate.get("comparison_ref")
        if not comparison_ref:
            _fail("not_ready", "比較が未記録")
        comparison = self._store.get_object(comparison_ref)
        normalized = _validate_comparison(comparison, freeze)
        if normalized.get("candidate_id") != candidate_id:
            _fail("not_ready", "comparison.candidate_id が不一致")
        completed_ref = candidate.get("completed_manifest_ref")
        if not completed_ref:
            _fail("not_ready", "完了マニフェストが未記録")
        completed = self._store.get_object(completed_ref)
        self._check_artifact(normalized, completed, freeze)
        evaluation = frozen_manifest.get("evaluation")
        if not isinstance(evaluation, dict):
            _fail("not_ready", "評価設定が不正")
        model_version = evaluation.get("model_version")
        # このランナーは LLM を用いない決定論的ローカルポリシーであり、
        # 実際にサポートされるモデル条件はこの固定値のみ。自己申告の
        # immutable:cloud 名などでは readiness を与えない。
        if model_version != "not_used:deterministic-local-policy":
            _fail("unpinned_comparison", "モデル版が固定されていません")
        self._verify_memory(freeze.get("memory_refs", []))
        config = freeze.get("config")
        if not isinstance(config, dict):
            _fail("not_ready", "凍結設定が不正")
        if freeze.get("memory_refs") != config.get("memory_refs"):
            _fail("not_ready", "memory_refs が凍結設定と不一致")
        result = _evaluate_pairs(normalized["pairs"], freeze)
        if not result["ready"]:
            _fail("not_ready", "昇格条件を満たさない")
        holdout_path = self._trusted / "rsi" / "holdout.json"
        _reject_symlink(holdout_path)
        if not holdout_path.exists():
            _fail("not_ready", "未見課題の利用記録がありません")
        holdout = loads(holdout_path.read_bytes())
        if not isinstance(holdout, dict):
            _fail("not_ready", "holdout が不正")
        for pair in normalized["pairs"]:
            if pair["split"] == "holdout":
                key = evaluation["dataset_sha256"] + ":" + pair["task_id"]
                if holdout.get(key) != freeze["candidate_ref"]:
                    _fail("holdout_denied", "未見課題の利用記録が候補と不一致")
        return {"candidate": candidate, "freeze": freeze, "result": result}

    # ---- 公開 API ----

    def freeze(
        self,
        campaign_id: str,
        config: Mapping[str, Any],
        manifest: dict,
        bindings: dict,
        artifact_root: Path,
    ) -> dict[str, Any]:
        campaign = _require_id(campaign_id, "campaign_id")
        normalized = _validate_config(config)
        identity.preflight(manifest, Path(artifact_root), bindings)
        manifest_ref = self._store.put_object(manifest)
        manifest_digest = digest(manifest)
        memory_cards = self._verify_memory(normalized["memory_refs"])
        root = Path(artifact_root)
        baseline_path = identity._resolve_within(root, bindings["legacy_source"], "legacy_source")
        candidate_path = identity._resolve_within(root, bindings["candidate_source"], "candidate_source")
        baseline_ref = self._store.put_object(
            loads(identity._read_bounded(baseline_path, "legacy_source"))
        )
        candidate_ref = self._store.put_object(
            loads(identity._read_bounded(candidate_path, "candidate_source"))
        )
        baseline_policy = self._verify_policy(baseline_ref)
        candidate_policy = self._verify_policy(candidate_ref)
        with self._store.lock():
            state = self._load_state()
            freeze_id = f"fz-{digest([campaign, normalized, manifest_digest])[:16]}"
            pin_path = self._pin_path(freeze_id)
            _reject_symlink(pin_path)
            pin_path.parent.mkdir(parents=True, exist_ok=True)
            existing_pin = None
            existing_frozen = None
            if pin_path.exists():
                existing_pin = self._load_pin(freeze_id)
                existing_frozen = self._verify_freeze(freeze_id)
            frozen_at = None
            if existing_frozen is not None:
                frozen_at = existing_frozen.get("frozen_at")
            if not isinstance(frozen_at, str) or not frozen_at:
                frozen_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
            frozen = {
                "freeze_id": freeze_id,
                "campaign_id": campaign,
                "config": normalized,
                "config_digest": digest(normalized),
                "manifest_ref": manifest_ref,
                "manifest_digest": manifest_digest,
                "evaluation_manifest_ref": manifest_ref,
                "evaluation_manifest_id": manifest["manifest_id"],
                "artifact_root": str(Path(artifact_root)),
                "bindings": dict(bindings),
                "baseline_ref": baseline_ref,
                "candidate_ref": candidate_ref,
                "baseline_policy": baseline_policy,
                "candidate_policy": candidate_policy,
                "memory_refs": [digest(c) for c in memory_cards],
                "frozen_at": frozen_at,
            }
            content_ref = self._store.put_object(frozen)
            content_digest = digest(frozen)
            if existing_pin is not None:
                if existing_pin.get("content_ref") != content_ref or existing_pin.get("content_digest") != content_digest:
                    _fail("freeze_modified", "凍結ピンが既存と不一致")
            else:
                self._store.atomic_json(
                    pin_path, {"content_ref": content_ref, "content_digest": content_digest}
                )
            state["freezes"][freeze_id] = {
                "content_ref": content_ref,
                "content_digest": content_digest,
                "campaign_id": campaign,
                "config_digest": frozen["config_digest"],
                "manifest_digest": manifest_digest,
            }
            self._save_state(state)
        return {"freeze_id": freeze_id, "content_digest": content_digest}

    def load_freeze(self, freeze_id: str) -> dict[str, Any]:
        _require_id(freeze_id, "freeze_id")
        frozen = self._verify_freeze(freeze_id)
        identity.preflight(
            self._store.get_object(frozen["manifest_ref"]),
            Path(frozen["artifact_root"]),
            frozen["bindings"],
        )
        return frozen

    def create_candidate(self, freeze_id: str) -> dict[str, Any]:
        _require_id(freeze_id, "freeze_id")
        frozen = self.load_freeze(freeze_id)
        policy = self._verify_policy(frozen["candidate_ref"])
        if policy != frozen["candidate_policy"]:
            _fail("freeze_modified", "候補ポリシーが凍結と不一致")
        with self._store.lock():
            state = self._load_state()
            candidate_id = f"cd-{digest([freeze_id, frozen['candidate_ref']])[:16]}"
            if candidate_id in state["candidates"]:
                _fail("duplicate_candidate", "同一候補が既存")
            record = {
                "candidate_id": candidate_id,
                "freeze_id": freeze_id,
                "campaign_id": frozen["campaign_id"],
                "content_digest": frozen["candidate_ref"],
                "candidate_ref": frozen["candidate_ref"],
                "status": "created",
                "applied": False,
            }
            state["candidates"][candidate_id] = record
            self._save_state(state)
        return dict(record)

    def get_candidate(self, candidate_id: str) -> dict[str, Any]:
        _require_id(candidate_id, "candidate_id")
        state = self._load_state()
        candidate = state["candidates"].get(candidate_id)
        if not isinstance(candidate, dict):
            _fail("candidate_missing", "候補が存在しない")
        return dict(candidate)

    def record_comparison(
        self, candidate_id: str, comparison: dict, completed_manifest: dict
    ) -> dict[str, Any]:
        _require_id(candidate_id, "candidate_id")
        with self._store.lock():
            state = self._load_state()
            candidate = state["candidates"].get(candidate_id)
            if not isinstance(candidate, dict):
                _fail("candidate_missing", "候補が存在しない")
            frozen = self.load_freeze(candidate["freeze_id"])
            normalized = _validate_comparison(comparison, frozen)
            if normalized.get("candidate_id") != candidate_id:
                _fail("input_invalid", "comparison.candidate_id が不一致")
            if candidate.get("candidate_ref") != frozen.get("candidate_ref"):
                _fail("input_invalid", "候補参照が凍結と不一致")
            if candidate.get("content_digest") != frozen.get("candidate_ref"):
                _fail("input_invalid", "候補内容ダイジェストが不一致")
            self._check_artifact(normalized, completed_manifest, frozen)
            if candidate.get("comparison_ref"):
                existing_comparison = self._store.get_object(
                    candidate["comparison_ref"]
                )
                existing_completed = self._store.get_object(
                    candidate["completed_manifest_ref"]
                )
                if (
                    canonical(existing_comparison) == canonical(normalized)
                    and canonical(existing_completed)
                    == canonical(completed_manifest)
                ):
                    return {
                        "comparison_ref": candidate["comparison_ref"],
                        "completed_manifest_ref": candidate[
                            "completed_manifest_ref"
                        ],
                    }
                _fail("freeze_modified", "既存比較と内容が異なります")
            result = _evaluate_pairs(normalized["pairs"], frozen)
            comparison_ref = self._store.put_object(normalized)
            completed_ref = self._store.put_object(completed_manifest)
            candidate["comparison_ref"] = comparison_ref
            candidate["comparison_digest"] = digest(normalized)
            candidate["completed_manifest_ref"] = completed_ref
            candidate["completed_manifest_digest"] = digest(completed_manifest)
            candidate["status"] = "compared"
            candidate["comparison_summary"] = result
            candidate["actual_cas_refs"] = {
                "comparison": comparison_ref,
                "completed_manifest": completed_ref,
            }
            candidate["actual_cas_digests"] = {
                "comparison": digest(normalized),
                "completed_manifest": digest(completed_manifest),
            }
            state["candidates"][candidate_id] = candidate
            self._save_state(state)
        return {"comparison_ref": comparison_ref, "completed_manifest_ref": completed_ref}

    def ready(self, candidate_id: str) -> dict[str, Any]:
        _require_id(candidate_id, "candidate_id")
        with self._store.lock():
            verified = self._verify_ready(candidate_id)
            state = self._load_state()
            candidate = state["candidates"].get(candidate_id)
            if not isinstance(candidate, dict):
                _fail("candidate_missing", "候補が存在しない")
            candidate["status"] = "promotion-ready"
            state["candidates"][candidate_id] = candidate
            self._save_state(state)
        return {"status": "promotion-ready", "candidate_id": candidate_id, "summary": verified["result"]}

    def record_holdout_access(self, candidate_id: str, task_ids: list[str]) -> dict[str, Any]:
        _require_id(candidate_id, "candidate_id")
        ids = _require_list(task_ids, "task_ids")
        if not ids:
            _fail("input_invalid", "task_ids は非空が必要")
        normalized_ids = [_require_str(t, "task_ids[]") for t in ids]
        if len(set(normalized_ids)) != len(normalized_ids):
            _fail("input_invalid", "task_ids に重複がある")
        with self._store.lock():
            state = self._load_state()
            candidate = state["candidates"].get(candidate_id)
            if not isinstance(candidate, dict):
                _fail("candidate_missing", "候補が存在しない")
            frozen = self.load_freeze(candidate["freeze_id"])
            manifest = self._store.get_object(frozen["manifest_ref"])
            evaluation = manifest.get("evaluation") if isinstance(manifest, dict) else None
            dataset_sha = evaluation.get("dataset_sha256") if isinstance(evaluation, dict) else None
            if not isinstance(dataset_sha, str) or not dataset_sha:
                _fail("freeze_invalid", "manifest に dataset_sha256 がない")
            dataset_path = identity._resolve_within(
                Path(frozen["artifact_root"]), frozen["bindings"]["dataset"], "dataset"
            )
            dataset = loads(dataset_path.read_text(encoding="utf-8"))
            if not isinstance(dataset, dict) or not isinstance(dataset.get("tasks"), list):
                _fail("dataset_invalid", "dataset のスキーマが不正")
            holdout_ids: set[str] = set()
            for task in dataset["tasks"]:
                if not isinstance(task, dict):
                    _fail("dataset_invalid", "task がマッピングでない")
                if task.get("split") == "holdout":
                    tid = task.get("task_id")
                    if not isinstance(tid, str) or not tid:
                        _fail("dataset_invalid", "task_id が不正")
                    holdout_ids.add(tid)
            for task in normalized_ids:
                if task not in holdout_ids:
                    _fail("holdout_denied", "holdout でないタスクが指定された")
            candidate_ref = frozen.get("candidate_ref")
            if not isinstance(candidate_ref, str) or not candidate_ref:
                _fail("freeze_invalid", "candidate_ref がない")
            ledger_path = self._trusted / "rsi" / "holdout.json"
            _reject_symlink(ledger_path)
            if ledger_path.exists():
                ledger = loads(ledger_path.read_text(encoding="utf-8"))
                if not isinstance(ledger, dict):
                    _fail("ledger_invalid", "holdout.json がマッピングでない")
            else:
                ledger = {}
            for task in normalized_ids:
                key = f"{dataset_sha}:{task}"
                owner = ledger.get(key)
                if owner is not None and owner != candidate_ref:
                    _fail("holdout_denied", "他候補が同一ホールドアウトを使用済み")
            audit = {
                "candidate_id": candidate_id,
                "freeze_id": frozen["freeze_id"],
                "dataset_sha": dataset_sha,
                "task_ids": normalized_ids,
                "candidate_ref": candidate_ref,
            }
            audit_ref = self._store.put_object(audit)
            for task in normalized_ids:
                ledger[f"{dataset_sha}:{task}"] = candidate_ref
            ledger_path.parent.mkdir(parents=True, exist_ok=True)
            self._store.atomic_json(ledger_path, ledger)
            refs = candidate.setdefault("holdout_access_refs", [])
            refs.append(audit_ref)
            self._save_state(state)
        return {"audit_ref": audit_ref}

    def add_experience(self, card: dict, *, train_ref: str | None = None) -> dict[str, Any]:
        from . import rsi_train
        if not isinstance(train_ref, str) or len(train_ref) != 64:
            _fail("memory_provenance_missing", "train_ref が必要")
        mapping = _require_mapping(card, "card")
        _exact_keys(
            mapping,
            ["split", "completion", "failure_class", "outcome", "action", "evidence_count"],
            "card",
        )
        if mapping.get("split") != "train" or mapping.get("completion") != "completed":
            _fail("input_invalid", "記憶カードは train/completed のみ")
        if mapping.get("failure_class") not in _FAILURE_CLASSES:
            _fail("input_invalid", "failure_class が不正")
        if mapping.get("outcome") not in _OUTCOMES:
            _fail("input_invalid", "outcome が不正")
        if mapping.get("action") not in _ACTIONS:
            _fail("input_invalid", "action が不正")
        count = mapping.get("evidence_count")
        if not _is_int(count) or count < 0:
            _fail("input_invalid", "evidence_count は非負整数")
        try:
            verified = rsi_train.verify_train(self, train_ref)
        except Exception:
            _fail("memory_provenance_missing", "train_ref の検証に失敗")
        report = verified["report"]
        if count != report["evidence_count"]:
            _fail("input_invalid", "evidence_count が train 結果と不一致")
        if mapping["failure_class"] != report["failure_class"]:
            _fail("input_invalid", "failure_class が train 結果と不一致")
        try:
            proposed = rsi_train.propose(self, train_ref)
        except Exception:
            _fail("input_invalid", "propose に失敗")
        expected_card = proposed.get("experience_card")
        if not isinstance(expected_card, dict) or canonical(expected_card) != canonical(mapping):
            _fail("input_invalid", "記憶カードが train 結果と不一致")
        normalized = {
            "split": "train",
            "completion": "completed",
            "failure_class": mapping["failure_class"],
            "outcome": mapping["outcome"],
            "action": mapping["action"],
            "evidence_count": count,
        }
        with self._store.lock():
            state = self._load_state()
            ref = self._store.put_object(normalized)
            pin_path = self._trusted_path(ref)
            _reject_symlink(pin_path)
            if pin_path.exists():
                self._verify_memory([ref])
            else:
                pin_path.parent.mkdir(parents=True, exist_ok=True)
                self._store.atomic_json(pin_path, {"memory_ref": ref, "train_ref": train_ref})
            if ref not in state["memory"]:
                state["memory"].append(ref)
            self._save_state(state)
        return {"memory_ref": ref}

    def retrieve_experience(self, frozen_refs: Iterable[str], failure_class: str) -> list[dict[str, Any]]:
        if failure_class not in _FAILURE_CLASSES:
            _fail("input_invalid", "failure_class が不正")
        refs = sorted({_require_sha(r, "frozen_refs[]") for r in frozen_refs})
        cards = self._verify_memory(refs)
        results: list[dict[str, Any]] = []
        for card in cards:
            if card.get("failure_class") == failure_class:
                results.append(card)
        return results

    def reflect(self, evidence_summary: Mapping[str, Any]) -> dict[str, Any]:
        summary = _require_mapping(evidence_summary, "evidence_summary")
        cause = summary.get("cause")
        if cause not in _CAUSE_ACTIONS:
            _fail("input_invalid", "cause が既知の分類でない")
        action, reason = _CAUSE_ACTIONS[cause]
        rejections = _require_int(summary.get("rejections", 0), "rejections", 0, 10**9)
        progress = _require_bool(summary.get("progress", False), "progress")
        budget_remaining = _require_int(summary.get("budget_remaining", 0), "budget_remaining", 0, 10**9)
        return {
            "cause": cause,
            "action": action,
            "reason": reason,
            "rejections": rejections,
            "progress": progress,
            "budget_remaining": budget_remaining,
        }

    def record_review(self, candidate_id: str, reviewer: str, decision: str) -> dict[str, Any]:
        _require_id(candidate_id, "candidate_id")
        reviewer_id = _require_id(reviewer, "reviewer")
        if decision not in ("approved", "rejected"):
            _fail("input_invalid", "decision は approved/rejected のみ")
        with self._store.lock():
            verified = self._verify_ready(candidate_id)
            state = self._load_state()
            review_id = f"rv-{digest([candidate_id, reviewer_id, decision])[:16]}"
            if review_id in state["reviews"]:
                _fail("duplicate_review", "同一レビューが既存")
            record = {
                "review_id": review_id,
                "candidate_id": candidate_id,
                "freeze_id": verified["freeze"]["freeze_id"],
                "reviewer": reviewer_id,
                "decision": decision,
                "candidate_digest": verified["candidate"]["content_digest"],
                "comparison_ref": verified["candidate"]["comparison_ref"],
                "completed_manifest_ref": verified["candidate"]["completed_manifest_ref"],
            }
            trusted_path = self._trusted / "rsi" / "reviews" / f"{review_id}.json"
            _reject_symlink(trusted_path)
            trusted_path.parent.mkdir(parents=True, exist_ok=True)
            self._store.atomic_json(trusted_path, record)
            state["reviews"][review_id] = record
            self._save_state(state)
        return {"review_id": review_id}

    def initialize_registry(self, baseline_ref: str) -> dict[str, Any]:
        ref = _require_sha(baseline_ref, "baseline_ref")
        self._verify_policy(ref)
        with self._store.lock():
            _reject_symlink(self._registry_path)
            if self._registry_path.exists():
                _fail("registry_exists", "レジストリは既に初期化済み")
            self._registry_path.parent.mkdir(parents=True, exist_ok=True)
            self._store.atomic_json(self._registry_path, {"current": ref, "previous": None})
        return {"current": ref, "previous": None}

    def apply(
        self,
        review_id: str,
        expected_baseline: str,
        expected_candidate: str,
        rollback: str,
    ) -> dict[str, Any]:
        _require_id(review_id, "review_id")
        baseline = _require_sha(expected_baseline, "expected_baseline")
        candidate_ref = _require_sha(expected_candidate, "expected_candidate")
        rollback_ref = _require_sha(rollback, "rollback")
        if rollback_ref != baseline:
            _fail("input_invalid", "rollback は expected_baseline と一致が必要")
        with self._store.lock():
            state = self._load_state()
            review = state["reviews"].get(review_id)
            if not isinstance(review, dict):
                _fail("review_missing", "レビューが存在しない")
            if review.get("decision") != "approved":
                _fail("not_approved", "承認済みレビューのみ適用可能")
            trusted_path = self._trusted / "rsi" / "reviews" / f"{review_id}.json"
            _reject_symlink(trusted_path)
            if not trusted_path.exists():
                _fail("review_missing", "信頼済みレビューが存在しない")
            trusted_review = loads(trusted_path.read_text(encoding="utf-8"))
            if canonical(trusted_review) != canonical(review):
                _fail("review_mismatch", "信頼済みレビューとワークスペースレビューが不一致")
            verified = self._verify_ready(review["candidate_id"])
            if verified["candidate"]["content_digest"] != review["candidate_digest"]:
                _fail("freeze_modified", "候補ダイジェストがレビューと不一致")
            if verified["candidate"]["comparison_ref"] != review["comparison_ref"]:
                _fail("freeze_modified", "比較参照がレビューと不一致")
            if verified["candidate"]["completed_manifest_ref"] != review["completed_manifest_ref"]:
                _fail("freeze_modified", "完了マニフェスト参照がレビューと不一致")
            if verified["freeze"]["baseline_ref"] != baseline:
                _fail("baseline_mismatch", "凍結ベースラインが期待値と不一致")
            if verified["freeze"]["candidate_ref"] != candidate_ref:
                _fail("candidate_mismatch", "凍結候補が期待値と不一致")
            _reject_symlink(self._registry_path)
            if not self._registry_path.exists():
                _fail("registry_missing", "レジストリが未初期化")
            registry = loads(self._registry_path.read_text(encoding="utf-8"))
            if not isinstance(registry, dict):
                _fail("baseline_mismatch", "レジストリ現在値が期待ベースラインと不一致")
            if registry.get("current") == candidate_ref:
                # レジストリ入れ替え後・状態保存前にクラッシュした場合の
                # 再試行。信頼済み条件と凍結入力検査は上で全て実施済み。
                # 期待する current/previous/review が完全一致する場合のみ
                # 適用済みとして冪等に復元する。
                if (registry.get("previous") != baseline
                        or registry.get("review") != review_id):
                    _fail("baseline_mismatch",
                          "レジストリ現在値が期待ベースラインと不一致")
                candidate = state["candidates"][review["candidate_id"]]
                candidate["applied"] = True
                candidate["status"] = "applied"
                state["candidates"][review["candidate_id"]] = candidate
                self._save_state(state)
                return {"applied": True}
            if registry.get("current") != baseline:
                _fail("baseline_mismatch", "レジストリ現在値が期待ベースラインと不一致")
            self._store.get_object(rollback_ref)
            self._store.atomic_json(
                self._registry_path,
                {"current": candidate_ref, "previous": baseline, "review": review_id},
            )
            candidate = state["candidates"][review["candidate_id"]]
            candidate["applied"] = True
            candidate["status"] = "applied"
            state["candidates"][review["candidate_id"]] = candidate
            self._save_state(state)
        return {"applied": True}
