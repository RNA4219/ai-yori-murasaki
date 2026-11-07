---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-06
---

# IMPLEMENTATION-PLAN.md — ai-yori-murasaki v1 実装 batch 配置（DeepSeek仕様設計）

intent_id: INT-AYM-PRODUCT-20261106 / owner: RNA4219 / status: reviewed

本稿は既存 Core/Eval batch を統合し、所有 file / depends_on / public interface / covered R / 検証を明記する。実装対象と外部 live 未検証を分離する。3 回目レビューで受入と順序を確認するまで実装を開始しない。

## 0. 前提

- 所有範囲: Core は data / CLI / policy / store / worker / API / adapter 実行基盤。Eval は adapter 外部 I/O 契約と正規化写像、lab 実体、RSI/KPI/enrichment/P2、受入。
- 実装対象: ローカルで実プロセス実行可能な範囲。
- 外部 live 未検証: 実 Trivy/CodeQL/Wazuh/Sigma CLI/RustXNG/rdsh の実接続は contract 試験に留め、live 接続は将来運用条件。

Ollama は製品 adapter 経由で GLM / DeepSeek の実接続を確認する。エンドポイント障害時は未検証を明示し、開発時呼出成功で代替しない。

## 1. Core batches

| id | owner | files | responsibility | depends_on | public interface | covered R | verification |
|---|---|---|---|---|---|---|---|
| C01 | Core | `ai_yori_murasaki/contracts.py`, `contracts/*.schema.json`, `tests/contract/` | 8 契約 schema、aym-json-v1 canonical、semantic validator（cross-campaign/stale/expiry、FinalVerdict 導出、EvidenceEvent.finding 所属） | - | `contracts.load_schema`, `contracts.validate`, `contracts.semantic_validate` | R-025, R-026 | known vector 一致、duplicate key 拒否、cross-campaign ref 拒否、stale hash 拒否、approval expiry 拒否、FinalVerdict 導出一致、finding 所属一致 |
| C02 | Core | `ai_yori_murasaki/storage.py`, `tests/storage/` | ledger append、CAS、catalog、verification journal、hash chain | C01 | `storage.append_event`, `storage.read_events`, `storage.put_object`, `storage.get_object`, `storage.update_catalog`, `storage.append_verification` | R-029〜R-033 | T01/T02/T03 故障注入で FAIL、verification journal が sealed chain 外、checkpoint 未提供 HOLD |
| C03 | Core | `ai_yori_murasaki/runtime.py`, `tests/runtime/` | state machine、run lifecycle、crash resume、seal、Run status→completion 対応 | C02 | `runtime.start_run`, `runtime.seal_run`, `runtime.transition`, `runtime.resume_after_crash` | R-007〜R-012 | Blind unlock 3 条件、crash resume 新 run_id、sealed run 復元拒否、pending/running で completion=null |
| C04 | Core | `ai_yori_murasaki/policy.py`, `policies/policy.json`, `tests/policy/` | policy load（fail closed）、allowlist 照合、actor 解決 | C01 | `policy.load`, `policy.check_allowlist`, `policy.resolve_actor` | R-020, R-021, R-023, R-034, R-035 | T07/T08 policy deny、redirect deny、DNS rebind deny、parse 失敗 fail closed |
| C05 | Core | `ai_yori_murasaki/worker.py`, `tests/worker/` | worker 起動、broker 経由 append、fake actor 拒否 | C02, C04 | `worker.launch`, `worker.submit_observation` | R-034, R-038, R-039 | worker 直接 append 不可、fake actor 拒否、privileged approval 付与不可 |
| C06 | Core | `ai_yori_murasaki/server.py`, `monitor/`, `tests/server/` | read-only HTTP monitor、projection、Host/Origin 検証、model_status projection | C02, C03 | `server.serve`, `server.handle_request`, `server.project_event` | R-044, R-049, R-050 | 127.0.0.1 bind のみ、非 loopback bind exit 2 `bind_not_loopback`、raw ledger/objects 非配信、blue view hidden field 削除、model_status 4 点分離 |
| C07 | Core | `ai_yori_murasaki/cli.py`, `tests/cli/` | CLI エントリ、exit code 統一、stdout JSON | C03, C04, C06 | `cli.main` | R-007〜R-012 | exit code 0/1/2/3/4 統一、reason_code 分離、policy deny 3 点独立、bind_not_loopback exit 2 |
| C08 | Core | `ai_yori_murasaki/adapters/base.py`, `tests/adapters/base/` | adapter 実行基盤（subprocess 起動、timeout/retry cap/max bytes 適用、`adapter_unavailable` 正規化、`contract.json` schema 定義、HTTP broker、Docker command runner） | C01, C04 | `adapters.base.run_adapter`, `adapters.base.load_contract` | R-038, R-039 | contract.json schema 検証、timeout/retry cap 適用、max bytes 超過拒否、失敗 `adapter_unavailable` 正規化 |

## 2. Eval batches

| id | owner | files | responsibility | depends_on | public interface | covered R | verification |
|---|---|---|---|---|---|---|---|
| B-Eval-01 | Eval | `adapters/*/contract.json`, `tests/contract/adapters/` | 7 adapter 外部 I/O 契約と正規化写像（Core C08 の schema に従う） | C08 | `adapters/<name>/contract.json` | R-038〜R-040 | T09/T10/T11 contract pass |
| B-Eval-02 | Eval | `fixtures/lab-target/`, `fixtures/red/`, `fixtures/blue/`, `fixtures/benign/` | lab 実体、attack 3 件、benign 5 件以上 | B-Eval-01 | fixture 集合 | R-012, R-018 | T12/T13 real pass |
| B-Eval-03 | Eval | `rsi/`, `cli/rsi.py` | RSI 9 項目、freeze/ready/apply 分離、holdout access 記録 | C03, C04 | RSI state 機械 | R-042, R-043 | B-2 全 pass |
| B-Eval-04 | Eval | `kpi/`, `tests/kpi/` | KPI 測定器実装とラボ測定成立記録（completion != null のみ分母） | C02, C03 | KPI 測定器 | R-002 | 各 KPI 1 回測定成立、pending/running 分母除外 |
| B-Eval-05 | Eval | `enrichment/` | ATT&CK/KEV/EPSS import+CAS+digest/TTL、実体 hash 照合 | C02 | enrichment import | R-046 | A-5 テスト pass |
| B-Eval-06 | Eval | `p2/` | Purple iteration/routing/Sigma tuning/crash resume 各 1 動作、RAM/RSS 実測 | B-Eval-03 | P2 機能 | R-047 | B-3 pass、RAM/RSS 実測記録 |
| B-Eval-07 | Eval | `tests/acceptance/` | T01〜T20 + RSI + P2 + API/UI boundary 検収 | B-Eval-01〜06 | 受入テスト | R-017, R-018, R-036, R-041, R-049, R-050 | B-5 全チェック |
| B-Eval-08 | Eval | `docs/acceptance/`, `docs/evidence/` | R-050 記録、R-049 4 点分離表示、coverage 役割、required check 構成文書 | B-Eval-07 | 受入記録 | R-036, R-041, R-049, R-050 | B-4 pass |

## 3. 実装順序

1. C01 → C02 → C03 → C04 → C08 → C05 → C06 → C07
2. B-Eval-01 → B-Eval-02 → B-Eval-03 → B-Eval-04 → B-Eval-05 → B-Eval-06 → B-Eval-07 → B-Eval-08
3. 3 回目レビューで受入と順序を確認するまで実装を開始しない。

## 4. 外部 live 未検証の分離

- 実 Trivy/CodeQL/Wazuh/Sigma CLI/RustXNG/rdsh の live 接続は contract 試験に留める。
- live 接続は将来運用条件として docs に明記し、受入の PASS に含めない。

## 5. R-049 model_status projection（C06 所有）

monitor projection に `model_status` object を追加する:

```
model_status: {
  requested_role_model: string|null,
  registration: {status: string, checked_at: timestamp|null, evidence_ref: sha256|null},
  development_call: {status: string, evidence_refs: array<sha256>},
  product_connection: {status: string, checked_at: timestamp|null, evidence_ref: sha256|null},
  pin: {status: enum[unverified,immutable], version: string|null}
}
```

- 未実施は `null` / `unknown`。
- cloud alias は `immutable` としない（`unverified`）。
- HTML と台帳に 4 点（指定/開発時呼出成功/製品接続の実測状態/pin の確認状態）を別状態で表示する。

## 6. R-050 記録（B-Eval-08 所有）

- 保管先: `docs/evidence/frontend-design-review-YYYYMMDD.json`。
- schema: `{scope, designer, implementer, skill_manifest: {files: [{path, sha256}]}, reviews, final_review, verification, html_sha256, limitations}`。
- raw req/res は `.ga/goal-*/` に置き非配信。
- GLM 入力スキル hash と生成実装 hash を照合する。
- 現に `docs/evidence/frontend-design-review-20261106.json` 記録あり。

## 7. T04 検証手順（B-Eval-07 所有）

- filesystem 検査に加え、fake LLM 受信 broker が Blue payload を capture し、Red canary / Oracle canary が存在しないことを assert する。
- 共有 cache key に role + campaign + run + model + prompt_digest を含め、cross-role cache 不可を assert する。
- 正解 store を mount しないことを assert する。
- これらの test canary は攻撃の回答に使わず検収のみに使う。
- ログと monitor API にも blue projection canary 漏れが無いことを検査する。

## 8. 配置・配布・統合の補足

- B-Eval-03は`ai_yori_murasaki/evaluation_identity.py`を所有し、RSI比較のmanifest実体固定・preflight/postrun・ready/applyとの接続を実装する。B-Eval-04の比較測定も同じ契約を使い、B-Eval-07が上流checker実行と拒否例を検収する。
- kaggle-campaign-opsはI/F未指定であり推測して実装しない。規模・SLA・台数・本番認証は`Campaign.extensions.operational_context`の任意設定として保持し、未設定はnullと未確認表示にする。本番実行経路は追加しない。

- schema の配布先は既存 package 内 `ai_yori_murasaki/schemas/v1/`。仕様中の contracts/*.schema.json は論理名であり、wheel から読める package data とする。
- Eval の rsi/kpi/enrichment/p2 は `ai_yori_murasaki/` 配下の module。CLI は C07 が統合し、独立したトップレベル cli package を作らない。
- C05 は C08 に依存する。実装順は C01→C02→C03 と C04→C08→C05 の依存を守り、C06/C07 は integration 時に接続する。
- Core の既存 Oracle を SPEC-CORE に合わせて更新し、非 LLM の判定・FinalVerdict 導出を C03 と統合する。
- 最終統合は MIT LICENSE、wheel/package data、README/RUNBOOK/BLUEPRINT/EVALUATION/AGENTS の現在状態、Task/Acceptance/CHANGELOG、Birdseye の原文確認と同期、Workflow Cookbook Tier 3 検査を含む。公開操作はこの Goal に含めない。
- HTML は開発モニターと製品実行モニターを区別する。製品画面は GLM がフロントエンドスキルを読んで設計・レビューし、DeepSeek が実装する。

現行実装の検証結果と配置は[製品検収の対応表](../PRODUCT-ACCEPTANCE.md)を参照する。3段階の見直しはCOMPACTION-01〜03に保存した。
