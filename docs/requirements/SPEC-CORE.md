---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-06
---

# SPEC-CORE.md — ai-yori-murasaki v1 Core 仕様（DeepSeek仕様設計）

intent_id: INT-AYM-PRODUCT-20261106 / owner: RNA4219 / status: reviewed / last_reviewed_at: 2026-11-07 / next_review_due: 2026-12-06

本仕様は要件定義 v1（R-001〜R-050）、GLM レビュー2（AYM2-01〜12, coverage_gaps）、共通インターフェース固定指示を入力とする。Core 担当の所有範囲は **data / CLI / policy / store / worker / API** に限定する。Eval 担当（7 adapter 実体、lab 実体、RSI/KPI/enrichment/P2、受入）の内部仕様は本稿で再定義しない。Core→Eval の受け渡しインターフェースのみ固定する。コードは本稿に含めない。

## 0. 製品前提（固定）

- 製品 package: `ai_yori_murasaki`、Python 3.11+。
- データ形式: JSON。既存 `jsonschema` / `referencing` を使用。
- 単一正本: `Workspace/runs/<run_id>/events.jsonl`（append-only ledger）。
- CAS: `Workspace/objects/<sha256>.json`。
- catalog: `Workspace/catalog.json`（JSON 単一ファイルに固定。SQLite は採用しない。理由: 単一正本の可搬性・差分監査・checkpoint 照合の単純化。SQLite 採用は将来運用条件）。
- trusted root: `Workspace` および repo 外の別絶対パス（設定入力 `AYM_TRUSTED_ROOT`）。workers には非公開（mount しない）。
- run 作成時、client 指定 actor の権限をそのまま採用しない。actor は orchestrator が policy から解決する。
- 仕様変更前の試作実装との互換は保証しない。

## 1. 正本・CAS・catalog（AYM2-01 反映）

### 1.1 単一正本の固定

- chain verification の対象は `Workspace/runs/<run_id>/events.jsonl` **のみ**。
- `Workspace/catalog.json` は run 索引（run_id, campaign_id, phase, status, sealed_at, last_sequence, last_event_hash, checkpoint_ref）を持つ投影。catalog は events.jsonl から再生成可能であり、catalog 単独を正本としない。
- `Workspace/objects/<sha256>.json` は CAS。ledger には artifact の sha256 のみ保存。
- `Workspace/` 配下に `checkpoints/` を置かない。checkpoint は §6 の trusted root 側に置く。

### 1.2 ディレクトリ（Core 所有分）

```
Workspace/                       # AYM_WORKSPACE（既定: repo 外の絶対パス）
  runs/<run_id>/events.jsonl     # 単一正本
  runs/<run_id>/manifest.json    # run manifest（§4.3）
  objects/<sha256>.json          # CAS
  catalog.json                   # 投影索引
  verification/<verify_id>.jsonl # 検証 journal（§6.3、sealed chain 外）
<TRUSTED_ROOT>/                  # AYM_TRUSTED_ROOT（repo/Workspace 外の別絶対パス）
  checkpoints/<campaign_id>/<run_id>.json
  freeze/<campaign_id>/<freeze_id>.json
  policy_intent/<campaign_id>.json
```

- `Workspace` と `TRUSTED_ROOT` は別絶対パスで、互いに他方の子でないことを起動時に検証する。同一または親子関係なら起動拒否（exit 2）。
- workers は `Workspace` も `TRUSTED_ROOT` も mount しない。

## 2. canonical 形式 `aym-json-v1`（AYM2-04 反映）

JCS 準拠を主張しない。独自 versioned canonical を固定する。

- 入力: Python の `dict`/`list`/`str`/`int`/`bool`/`None`/`float`（有限のみ）。
- エンコード: UTF-8、`sort_keys=True`、`separators=(',', ':')`、`ensure_ascii=False`、`allow_nan=False`。
- 重複 key は入力段階で拒否（`object_pairs_hook` で検出、`DuplicateKeyError`）。
- 数値: Python 3.11+ の `json` 数値表現を契約とする。`1` と `1.0` は区別する（`1` は `1`、`1.0` は `1.0`）。`NaN`/`Infinity`/`-Infinity` は拒否。負ゼロ `-0.0` は `-0.0` として出力（Python 標準表現）。指数表記は Python 標準表現（例 `1e+20`）。
- Unicode: NFC 正規化は行わない。入力バイト列をそのまま UTF-8 で扱う。
- 深さ上限: 32。サイズ上限: 単一 event で 1 MiB。
- known vector（contract test で固定）: `{"a":1,"b":1.0}` → `{"a":1,"b":1.0}`、`{"x":-0.0}` → `{"x":-0.0}`、`{"s":"あ"}` → `{"s":"あ"}`、`{"n":1e+20}` → `{"n":1e+20}`、`{"k":null}` → `{"k":null}`。
- metric 空値は `null`（`0` や `""` で代替しない）。

## 3. hash chain（AYM2-02 反映）

- `event_hash = SHA256( aym-json-v1(event から event_hash を除いたもの) || previous_event_hash の ASCII 64 bytes )`。
- `previous_event_hash` は **record にも含まれる**（canonical 対象に含まれる）。先頭 event は `"0"*64`。
- `sequence` は run 内で 1 始まり単調増加。
- seal 後の chain は不変。seal 後の append は policy で拒否（`policy_denied`）。
- verify 結果は **別 verification journal**（`Workspace/verification/<verify_id>.jsonl`）に記録し、対象 ledger へ append しない。journal レコードは `{verify_id, target_run_id, target_last_sequence, target_last_event_hash, checkpoint_digest, result, reason_code, verified_at, verifier_id}` を持つ。

## 4. 8 契約 JSON Schema（AYM2-06 反映）

`contracts/` に 8 ファイル。`$schema: https://json-schema.org/draft/2020-12/schema`、`additionalProperties: false`、拡張は `extensions` オブジェクトに限定（`additionalProperties: true`、深さ 8、サイズ 64 KiB 上限）。`$data` は禁止。相関は Python semantic validator で検査する。

### 4.1 共通型

- `schema_version`: `"1.0.0"` 固定。
- `id`: `^[a-z0-9][a-z0-9-]{0,63}$`。
- `sha256`: `^[0-9a-f]{64}$`。
- `timestamp`: RFC3339 UTC（`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,9})?Z$`）。
- `actor`: `{role: enum[operator,orchestrator,oracle,worker-red,worker-blue,monitor,auditor], agent_id: id, model: string|null}`。

### 4.2 Campaign（`campaign.schema.json`）

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| schema_version | string | ○ | × | `"1.0.0"` | - |
| campaign_id | string | ○ | × | id | 64 |
| intent_id | string | ○ | × | `^INT-[A-Z0-9-]{1,64}$` | 80 |
| scope | object | ○ | × | `{targets: array<{kind: enum[lab-target,fixture], identifier: string}>, max_targets: int}` | targets ≤ 32 |
| authorization | object | ○ | × | `{operator_id: id, granted_at: timestamp, expires_at: timestamp, scope_digest: sha256}` | - |
| created_at | timestamp | ○ | × | - | - |
| operator_id | id | ○ | × | - | - |
| status | string | ○ | × | enum[created,running,blind_completed,purple_open,remediation_in_progress,review_pending,completed,cancelled,incomplete] | - |
| extensions | object | × | × | - | 64 KiB |

相関: `authorization.expires_at > created_at`。`authorization.scope_digest == sha256(aym-json-v1(scope))`。

### 4.3 Run（`run.schema.json`）

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| schema_version | string | ○ | × | `"1.0.0"` | - |
| campaign_id | id | ○ | × | - | - |
| run_id | id | ○ | × | - | - |
| phase | string | ○ | × | enum[red,blue_blind,purple,remediation,reevaluation,verify] | - |
| actor | actor | ○ | × | - | - |
| started_at | timestamp | ○ | × | - | - |
| ended_at | timestamp\|null | ○ | ○ | - | - |
| status | string | ○ | × | enum[pending,running,completed,incomplete,cancelled,adapter_unavailable] | - |
| completion | string\|null | ○ | ○ | enum[completed,incomplete,cancelled,adapter_unavailable] | - |
| cause | string\|null | ○ | ○ | enum[none,policy_denied,adapter_unavailable,crash,timeout,evidence_missing,evidence_mismatch,not_executed] | - |
| seed | integer | ○ | × | 0 ≤ seed < 2^63 | - |
| identity | object | ○ | × | `{operator_id: id, worker_id: id|null}` | - |
| budget | object | ○ | × | `{max_tool_calls: int, max_wall_seconds: int, max_tokens: int}` | 各 ≤ 10^9 |
| manifest_ref | sha256 | ○ | × | - | - |
| extensions | object | × | × | - | 64 KiB |

相関（status→completion の全対応表、機械可読）:

| status | completion | ended_at | cause |
|---|---|---|---|
| pending | null | null | null |
| running | null | null | null |
| completed | completed | timestamp | none |
| incomplete | incomplete | timestamp | enum[none,policy_denied,adapter_unavailable,crash,timeout,evidence_missing,evidence_mismatch,not_executed] |
| cancelled | cancelled | timestamp | none |
| adapter_unavailable | adapter_unavailable | timestamp | adapter_unavailable |

- `completion` は terminal 状態でのみ非 null。pending/running 中は `completion=null` かつ `ended_at=null`。
- `failure_cause`（= `cause`）は terminal 状態でのみ非 null。pending/running 中は `cause=null`。
- KPI 集計（A-4）は `completion != null` の run のみを分母に計上する。pending/running の run は分母に含めない。
- 締切（deadline）時点で未完了の run は `status=incomplete` / `completion=incomplete` / `cause=timeout` で確定する。candidate 起因の未完了は loss（失敗分母）に計上し、external pair 無効（pair_fail）は除外理由 counts を出す。
- `ended_at >= started_at`。

### 4.4 EvidenceEvent（`evidence_event.schema.json`）

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| schema_version | string | ○ | × | `"1.0.0"` | - |
| campaign_id | id | ○ | × | - | - |
| run_id | id | ○ | × | - | - |
| sequence | integer | ○ | × | ≥ 1 | - |
| actor | actor | ○ | × | - | - |
| source | string | ○ | × | enum[trivy,codeql,wazuh,sigma,rustxng,rdsh,llm,oracle,orchestrator,broker] | - |
| event_type | string | ○ | × | §4.5 列挙 | - |
| timestamp | timestamp | ○ | × | - | - |
| target | object | ○ | × | `{kind: enum[lab-target,fixture], identifier: string}` | 256 |
| finding | object\|null | ○ | ○ | §4.4.1 embedded finding 部分集合 | 16 KiB |
| evidence_refs | array<sha256> | ○ | × | - | ≤ 32 |
| previous_event_hash | sha256 | ○ | × | - | - |
| event_hash | sha256 | ○ | × | - | - |
| extensions | object | × | × | - | 64 KiB |

相関: `sequence == 1` のとき `previous_event_hash == "0"*64`。`event_hash` は §3 の式で再計算一致。

#### 4.4.1 EvidenceEvent.finding embedded schema（固定）

`finding` は `null` または以下の 3 field object 固定（`additionalProperties: false`）。Finding 完全情報は CAS ref（`evidence_refs`）に置き、embedded には持たない。

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| finding_id | id | ○ | × | §4.1 id | 64 |
| severity | string | ○ | × | enum[critical,high,medium,low,info]（§4.7 Finding と一致） | - |
| title | string | ○ | × | - | 512（§4.7 Finding と一致） |

- 型・enum・上限は §4.7 Finding の同名 field と一致させる（semantic validator が一致を検査）。
- `finding != null` のとき、`finding_id` が同一 `campaign_id`/`run_id` の Finding（CAS 実体）に存在することを semantic validator が確認する。存在しない場合は `policy_denied`（exit 4）。

### 4.5 event_type 列挙

`scope_validated, fixture_reset, red_plan, attack_executed, state_captured, exploit_judged, chain_sealed, asset_inventory, static_scan, telemetry_ingested, prioritized, remediation_drafted, detection_rule_drafted, blind_completed, purple_opened, gap_analyzed, sigma_drafted, patch_drafted, fp_fn_matrix, playbook_created, static_scan_result, unit_result, integration_result, oracle_replay_result, benign_regression_result, red_reevaluated, oracle_final_verdict, approval_gate, policy_denied, adapter_unavailable, rsi_frozen, rsi_candidate_created, rsi_compared, rsi_ready, rsi_applied`

（`checkpoint_verified`/`checkpoint_mismatch` は sealed chain に append しない。§6.3 の verification journal に記録する。）

### 4.6 OraclePredicate（`oracle_predicate.schema.json`）

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| schema_version | string | ○ | × | `"1.0.0"` | - |
| predicate_id | id | ○ | × | - | - |
| kind | string | ○ | × | enum[http_status,exit_code,file_state,db_state,network_state,alert_fired,test_pass,evidence_hash] | - |
| input_digest | sha256 | ○ | × | - | - |
| expected | object | ○ | × | kind 依存（§4.6.1） | 4 KiB |
| actual | object\|null | ○ | ○ | kind 依存 | 4 KiB |
| outcome | boolean\|null | ○ | ○ | - | - |
| verdict | string | ○ | × | enum[PASS,FAIL,HOLD] | - |
| reason_code | string | ○ | × | enum[exploit_succeeded,exploit_not_succeeded,execution_error,evidence_missing,evidence_mismatch,not_executed,adapter_unavailable,cancelled] | - |
| evaluated_at | timestamp | ○ | × | - | - |
| extensions | object | × | × | - | 64 KiB |

#### 4.6.1 kind 別 expected/actual

- `http_status`: `{method: enum[GET,POST,PUT,DELETE,HEAD], path: string, status: int}`。
- `exit_code`: `{code: int}`。
- `file_state`: `{path: string, sha256: sha256|null, exists: bool}`。
- `db_state`: `{query_id: id, row_count: int|null}`。
- `network_state`: `{host: string, port: int, reachable: bool}`。
- `alert_fired`: `{rule_id: id, fired: bool}`。
- `test_pass`: `{suite_id: id, passed: bool}`。
- `evidence_hash`: `{ref: sha256}`。

相関: `verdict=PASS` のとき `outcome=true`。`verdict=FAIL` かつ `reason_code=exploit_not_succeeded` のとき `outcome=false`。`verdict=HOLD` のとき `outcome=null` かつ `reason_code ∈ {execution_error,evidence_missing,evidence_mismatch,not_executed,adapter_unavailable,cancelled}`。

### 4.7 Finding（`finding.schema.json`）

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| schema_version | string | ○ | × | `"1.0.0"` | - |
| finding_id | id | ○ | × | - | - |
| campaign_id | id | ○ | × | - | - |
| run_id | id | ○ | × | - | - |
| source | string | ○ | × | enum[trivy,codeql,wazuh,sigma,rustxng,rdsh,llm] | - |
| severity | string | ○ | × | enum[critical,high,medium,low,info] | - |
| title | string | ○ | × | - | 512 |
| location | object | ○ | × | `{path: string, line: int|null, column: int|null}` | 1024 |
| references | array<string> | ○ | × | - | ≤ 32, 各 2048 |
| extensions | object | × | × | - | 64 KiB |

### 4.8 Remediation（`remediation.schema.json`）

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| schema_version | string | ○ | × | `"1.0.0"` | - |
| remediation_id | id | ○ | × | - | - |
| finding_id | id | ○ | × | - | - |
| campaign_id | id | ○ | × | - | - |
| run_id | id | ○ | × | - | - |
| patch_bundle_ref | sha256 | ○ | × | - | - |
| static_scan_result | object | ○ | × | `{passed: bool, ref: sha256}` | - |
| unit_result | object | ○ | × | `{passed: bool, ref: sha256}` | - |
| integration_result | object | ○ | × | `{passed: bool, ref: sha256}` | - |
| oracle_replay_result | object | ○ | × | `{verdict: enum[PASS,FAIL,HOLD], reason_code: string, ref: sha256}` | - |
| benign_regression_result | object | ○ | × | `{passed: bool, ref: sha256}` | - |
| status | string | ○ | × | enum[draft,static_scan_passed,unit_passed,integration_passed,oracle_replay_passed,benign_regression_passed,review_pending,approved,rejected,failed] | - |
| extensions | object | × | × | - | 64 KiB |

相関: `finding_id` が同一 `campaign_id`/`run_id` の Finding に存在すること（semantic validator）。

### 4.9 Approval（`approval.schema.json`）

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| schema_version | string | ○ | × | `"1.0.0"` | - |
| approval_id | id | ○ | × | - | - |
| remediation_id | id | ○ | × | - | - |
| reviewer_id | id | ○ | × | - | - |
| decision | string | ○ | × | enum[approved,rejected,pending] | - |
| decided_at | timestamp | ○ | ○ | - | - |
| required_checks | array<{name: string, passed: bool, ref: sha256}> | ○ | × | - | ≤ 32 |
| expires_at | timestamp | ○ | × | - | - |
| extensions | object | × | × | - | 64 KiB |

相関: `decision=approved` のとき `decided_at != null` かつ `expires_at > decided_at`。`required_checks` に `unit-tests,integration-tests,trivy,codeql,evidence-verify,oracle-regression,red-replay,human-review` を最低含む（R-036 minimum）。

### 4.10 FinalVerdict（`final_verdict.schema.json`）

| field | type | required | nullable | enum/pattern | 上限 |
|---|---|---|---|---|---|
| schema_version | string | ○ | × | `"1.0.0"` | - |
| campaign_id | id | ○ | × | - | - |
| run_id | id | ○ | × | - | - |
| oracle_verdict | string | ○ | × | enum[PASS,FAIL,HOLD] | - |
| oracle_reason_code | string | ○ | × | §4.6 reason_code | - |
| run_completion | string | ○ | × | enum[completed,incomplete,cancelled,adapter_unavailable] | - |
| adapter_status | array<object> | ○ | × | `{name: enum[trivy,codeql,wazuh,sigma,rustxng,rdsh,llm], status: enum[ok,unavailable,not_configured]}` の配列 | ≤ 7 |
| cancellation | object\|null | ○ | ○ | `{cause: string, at: timestamp}` | - |
| remediation_success | boolean | ○ | × | - | - |
| benign_regression_passed | boolean\|null | ○ | ○ | - | - |
| remediation_id | string\|null | ○ | ○ | §4.1 id | 64 |
| test_report_refs | array<sha256> | ○ | × | - | ≤ 32 |
| decided_at | timestamp | ○ | × | - | - |
| extensions | object | × | × | - | 64 KiB |

相関（semantic validator が導出・検証。agent 自己申告不可）:

- `remediation_success` は独立 Oracle が以下から導出する: `oracle_verdict=FAIL` かつ `oracle_reason_code=exploit_not_succeeded` かつ `run_completion=completed` かつ `benign_regression_passed=true` かつ `remediation_id != null` かつ `test_report_refs` が空でない。上記を満たすときのみ `true`、それ以外は `false`。
- `benign_regression_passed` は正常系 regression の全 pass を独立 Oracle が判定した結果。未実行は `null`。`null` のとき `remediation_success=false`。修復判定を要求した場合のみ `oracle_verdict=HOLD`（`reason_code=not_executed`）とする。baseline の攻撃成立判定は正常系回帰が未実行でも独立に PASS/FAIL を返せる。
- `remediation_id` は同一 `campaign_id` の Remediation に存在すること。`remediation_success=true` のとき非 null 必須。
- `test_report_refs` の各 sha256 は CAS に実体が存在し、実体 hash と一致すること（stale hash 拒否）。`remediation_success=true` のとき空配列不可。
- `remediation_success` / `benign_regression_passed` / `remediation_id` / `test_report_refs` は agent の自己申告を受け付けず、Oracle が実体（test report CAS、OraclePredicate 結果）から計算して照合する。
- §7.1 の `review_pending` 遷移 guard は `benign_regression_passed=true` かつ `oracle_verdict=FAIL` かつ `oracle_reason_code=exploit_not_succeeded` を要求し、FinalVerdict の `remediation_success` と突合する。

### 4.11 cross-campaign / stale hash / approval expiry 拒否規則（機械可読）

semantic validator が以下を検査し、違反は `policy_denied`（exit 4）:

1. `Remediation.finding_id` が同一 `campaign_id`/`run_id` の Finding に存在しない → 拒否。
2. `Approval.remediation_id` が同一 `campaign_id` の Remediation に存在しない → 拒否。
3. `Approval.expires_at <= now` → 拒否（stale approval）。
4. `Remediation.patch_bundle_ref` の CAS 実体 sha256 が `patch_bundle_ref` と一致しない → 拒否（stale hash）。
5. `EvidenceEvent.evidence_refs` の各 sha256 が CAS に存在しない → 拒否（stale hash）。
6. `Campaign.authorization.expires_at <= now` → 拒否（expired authorization）。
7. `Campaign.authorization.scope_digest != sha256(aym-json-v1(scope))` → 拒否。

## 5. Policy engine（AYM2-05 反映）

- policy は **JSON**（`policies/policy.json`）。YAML は採用しない。理由: parse 失敗・alias bomb・未知 key の挙動を単純化し、`aym-json-v1` と同じ制約で一意に再現可能にするため。
- スキーマ: `{schema_version, scope, authorized_targets, role_permissions, credential_namespaces, auto_apply, broker_allowlist}`。
- parse 失敗・未知 key・型不一致は **fail closed**（既定 deny、exit 4、`policy_denied` event）。
- `auto_apply`: `{dev: "deny", lab: "allow_verified_patch_in_ephemeral_workspace", production: "deny", staging: "approval_required"}`。
- `broker_allowlist`: `{host: string, ip: string, port: int, path_prefix: string, verbs: array<enum[GET,POST,PUT,DELETE,HEAD]>}` の配列。

### 5.1 broker 及び policy engine の allowlist 照合（R-020, R-021, R-023）

- 照合順: (1) host 完全一致、(2) 解決 IP が allowlist IP と一致、(3) port 一致、(4) path_prefix 前方一致、(5) verb 一致。
- redirect は deny（3xx は追跡しない）。
- DNS rebind 考慮: 解決 IP を照合し、allowlist IP と不一致なら deny。解決結果はキャッシュしない。
- 計画 schema 外の操作は拒否。
- ラボ target のみ scope 原則。
- 引数は array、`shell=False`、host arbitrary execution 禁止。
- Windows 上 context / Docker path 通過基準: `Workspace` と `TRUSTED_ROOT` は絶対パス、`os.path.realpath` で正規化、`..` を含む path は拒否。

## 6. Checkpoint / freeze / verification journal（AYM2-01, AYM2-02, AYM2-09 反映）

### 6.1 checkpoint 配置

- `TRUSTED_ROOT/checkpoints/<campaign_id>/<run_id>.json`。`TRUSTED_ROOT` は repo/Workspace 外の別絶対パス（`AYM_TRUSTED_ROOT`）。
- 形式: `{campaign_id, run_id, last_sequence, last_event_hash, sealed_at, operator_id, signature?}`。
- 発行: 独立 operator seal CLI（`aym checkpoint seal`）。runtime worker から書換不可（mount しない）。
- executor/broker は read only。
- same-admin 攻撃耐性は保証しない（docs 明記）。

### 6.2 freeze 基準 hash

- `TRUSTED_ROOT/freeze/<campaign_id>/<freeze_id>.json` に operator 操作で保存。
- 形式: `{campaign_id, freeze_id, purpose, thresholds, stop_conditions, adoption_criteria, frozen_at, operator_id, content_digest}`。
- `content_digest = sha256(aym-json-v1(freeze から content_digest を除いたもの))`。
- 観測後の変更は新 freeze_id として記録し、採用判断に使った freeze_id を特定可能にする。

### 6.3 verification journal（sealed chain 外）

- `Workspace/verification/<verify_id>.jsonl` に append。
- レコード: `{verify_id, target_run_id, target_last_sequence, target_last_event_hash, checkpoint_digest, result: enum[PASS,FAIL,HOLD], reason_code, verified_at, verifier_id}`。
- 照合対象は seal 時点の `last_sequence`/`last_event_hash` に固定。sealed chain へ append しない。
- journal レコードの `reason_code` enum: `verdict_fail`（chain hash 不整合）、`checkpoint_mismatch`（checkpoint 照合不一致）、`checkpoint_missing`（checkpoint 未提供）、`evidence_mismatch`（chain 不正で checkpoint 内部照合不能）、`hold`（その他 HOLD）。
- `aym verify chain` の hash 不整合は exit 1 / `verdict_fail`。`aym verify checkpoint` の mismatch は exit 1 / `checkpoint_mismatch`。chain 不正が確認できた場合は checkpoint 検証でも `result=FAIL` / `reason_code=verdict_fail` とする。読み取り自体ができない場合のみ HOLD とし、完全性不成立を HOLD に弱めない。
- 初回 seal / 照合 / 全体 rewrite / tail deletion 検出:
  - 初回 seal: `aym checkpoint seal` が `last_sequence`/`last_event_hash` を記録。
  - 照合: `aym verify checkpoint` が ledger の `last_sequence`/`last_event_hash` と checkpoint を比較。不一致は `result=FAIL`。
  - 全体 rewrite: checkpoint と整合しない改変を検出（不可能保証はしない）。
  - tail deletion: `last_sequence` 不一致で検出。
- checkpoint 未提供時は `result=HOLD`。

## 7. State machine（R-007〜R-012, R-047）

### 7.1 campaign 遷移と guards

| from | to | guard |
|---|---|---|
| created | running | scope_validated event 存在、authorization 有効 |
| running | blind_completed | blind_completed event 存在、Blue 評価終了 |
| blind_completed | purple_open | blind_completed event 存在、seal 済み、評価終了 |
| purple_open | remediation_in_progress | patch_drafted event 存在 |
| remediation_in_progress | review_pending | benign_regression_result passed、oracle_replay_result FAIL(exploit_not_succeeded) |
| review_pending | completed | approval_gate approved |
| any | cancelled | operator 明示 |
| any | incomplete | crash/timeout/policy_denied/adapter_unavailable |

- Blind unlock は「完了 + seal 済み + 評価終了」の 3 条件。欠落情報は HOLD。
- Blind 未終了で Purple 工程起動不可。

### 7.2 crash resume

- 未完了 run は診断保存（`Workspace/runs/<run_id>/manifest.json` に `cause=crash` 記録）。
- 新 run は新 `run_id` と新 idempotency key で開始。
- 既存 sealed run は復元しない。

## 8. Worker / broker（AYM2-03, AYM2-11 反映）

### 8.1 worker 構成

- `worker-blue`: `--network none`。秘密/Red context/正解を mount しない。入力は orchestrator が事前取得・投影した観測データのみ。
- `worker-red` / target: 専用 internal Docker lab network（`labnet`, `internal=true`）。
- orchestrator の broker だけが設定 allowlist 先へ network 可。credential は broker のみ。
- agent 出力から shell/hostpath/env/URL スコープ拡張不可。
- Docker CLI 信頼は orchestrator 限定。worker は `--read-only` rootfs、`--cap-drop ALL`、`--security-opt no-new-privileges`、`--pids-limit 256`、`--memory 1g`、`--cpus 1.0`、非 root、`docker.sock` なし。作業用 scratch のみ write。

### 8.2 agent append の代理（AYM2-11）

- agent の append 権限は **orchestrator broker 経由で代理行使**される。worker は直接 filesystem append を行わない。
- worker は broker に `Observation` を送り、broker が `EvidenceEvent` を生成して ledger に append する。
- worker は fake actor / sequence / privileged approval を付けられない。actor は broker が policy から解決。

### 8.3 ロック / atomic writes / 排他 / 復旧 / retention / backup

- ledger append は `fcntl.flock`（POSIX）/ `msvcrt.locking`（Windows）で排他。
- atomic write は `os.replace`（同一 filesystem 内）。
- concurrent run 排他: 同一 `campaign_id` で同時 running は 1 のみ（catalog で検査）。
- 復旧: crash 時は manifest に `cause=crash` を記録し、新 run で再開。
- retention: `retention workflow` のみ delete 可。期間は設定入力（既定 90 日）。
- backup: `Workspace` 全体の snapshot を `TRUSTED_ROOT/backup/` に operator 操作で保存。

## 9. CLI（AYM2-10 反映）

### 9.1 exit code 統一

| code | 意味 |
|---|---|
| 0 | 処理成功 / 検証 PASS |
| 1 | 判定 FAIL |
| 2 | 入力 / 利用法エラー |
| 3 | HOLD または未完了 |
| 4 | policy deny または認可不足 |

- JSON `reason_code` で細分（`input_invalid`, `usage_error`, `verdict_fail`, `hold`, `incomplete`, `policy_denied`, `authorization_insufficient`, `adapter_unavailable`, `checkpoint_missing`, `checkpoint_mismatch`, `freeze_modified`, `unpinned_comparison`, `apply_args_missing`, `bind_not_loopback`）。
- `bind_not_loopback` は入力/利用法エラー（exit 2）。policy 実行拒否（exit 4 `policy_denied`）と分離する。monitor の非 loopback bind は入力検証段階で拒否し、policy event を発行しない。
- 全 CLI stdout は `{schema_version, command, status, reason_code, campaign_id?, run_id?, data}`。stderr は診断（secret 除去）。
- 拒否は成功扱いしない。
- run 未作成の拒否は audit 別記。run 後は `incomplete` cause=`policy_denied`。
- RSI で candidate 由来 policy deny は failure 分母。

### 9.2 コマンド一覧

| command | args | default | example | output | retry/idempotency |
|---|---|---|---|---|---|
| `aym campaign create` | `--intent-id`, `--scope-file`, `--authorization-file` | - | `aym campaign create --intent-id INT-AYM-PRODUCT-20261106 --scope-file scope.json --authorization-file auth.json` | `{campaign_id}` | idempotency key 必須、同一 key は同一結果 |
| `aym run start` | `--campaign-id`, `--phase`, `--seed` | seed=0 | `aym run start --campaign-id c1 --phase red --seed 42` | `{run_id}` | idempotency key 必須 |
| `aym run seal` | `--campaign-id`, `--run-id` | - | `aym run seal --campaign-id c1 --run-id r1` | `{sealed_at, last_sequence, last_event_hash}` | 冪等（既 seal は同一結果） |
| `aym checkpoint seal` | `--campaign-id`, `--run-id` | - | `aym checkpoint seal --campaign-id c1 --run-id r1` | `{checkpoint_digest}` | 冪等 |
| `aym oracle evaluate` | `--run-id`, `--predicate-file` | - | `aym oracle evaluate --run-id r1 --predicate-file p.json` | `{verdict, reason_code, outcome}` | 冪等（同一 input_digest は同一結果） |
| `aym rsi freeze` | `--campaign-id`, `--freeze-file` | - | `aym rsi freeze --campaign-id c1 --freeze-file f.json` | `{freeze_id, content_digest}` | 冪等 |
| `aym rsi ready` | `--candidate-id` | - | `aym rsi ready --candidate-id cand1` | `{ready: true}` | 冪等 |
| `aym rsi apply` | `--review-id`, `--expected-baseline`, `--rollback` | - | `aym rsi apply --review-id rev1 --expected-baseline b1 --rollback rb1` | `{applied: true}` | 冪等 |
| `aym verify chain` | `--campaign-id`, `--run-id` | - | `aym verify chain --campaign-id c1 --run-id r1` | `{result, reason_code}` | 冪等 |
| `aym verify checkpoint` | `--campaign-id`, `--run-id` | - | `aym verify checkpoint --campaign-id c1 --run-id r1` | `{result, reason_code}` | 冪等 |
| `aym monitor serve` | `--bind` | `127.0.0.1:8765` | `aym monitor serve --bind 127.0.0.1:8765` | HTTP | - |

- `aym monitor serve` の非 loopback bind は入力エラー: exit 2、`reason_code=bind_not_loopback`。policy 実行拒否（exit 4 `policy_denied`）とは分離する。

### 9.3 policy deny の 3 点独立定義

- (a) `policy_denied` event を ledger に append。
- (b) run status 遷移: `running` → `incomplete`、`cause=policy_denied`。
- (c) exit code: 4。

## 10. Core→Eval インターフェース（固定）

Core は以下を Eval に渡す。Eval は Core を再定義せず参照する。

### 10.1 RunContext

`{campaign_id, run_id, role, scope, target_digest, policy_digest, budget, deadline}`

- `role`: enum[worker-red,worker-blue,oracle,orchestrator]。
- `scope`: Campaign.scope の投影。
- `target_digest`: sha256(aym-json-v1(target))。
- `policy_digest`: sha256(aym-json-v1(policy))。
- `budget`: Run.budget。
- `deadline`: timestamp。

### 10.2 ArtifactRef

`{sha256}`。CAS 実体は `Workspace/objects/<sha256>.json`。

### 10.3 Observation

`{operation_id, source, started_at, ended_at, completion, status_code, exit_code, body_ref, error_code}`

- `completion`: enum[completed,incomplete,cancelled,adapter_unavailable]。
- `body_ref`: ArtifactRef。
- `error_code`: string|null。

### 10.4 event append via trusted orchestrator

Eval は `Observation` を broker に送る。broker が `EvidenceEvent` を生成して ledger に append する。Eval は直接 append しない。

### 10.5 OracleEvaluation

`{Outcome: boolean|null, checks: array<{kind, expected, actual, passed}>}`

- `Outcome`: `true`（攻撃成立）/ `false`（攻撃不成立）/ `null`（判定不能）。
- `checks`: OraclePredicate の評価結果。

## 11. モジュール構成と public functions（固定）

```
ai_yori_murasaki/
  contracts.py    # 8契約 schema load, semantic validator
  storage.py      # ledger append, CAS, catalog, verification journal
  runtime.py      # state machine, run lifecycle, crash resume
  policy.py       # policy load, allowlist 照合, fail closed
  worker.py       # worker 起動, broker 経由 append
  server.py       # read-only HTTP monitor
  cli.py          # CLI エントリ
  adapters/base.py # adapter 実行基盤（Core 所有）
```

### 11.0 adapter 実行基盤（Core 所有、AYM3-05 反映）

- Core は adapter 実行基盤を所有する: subprocess 起動、timeout 適用、retry cap 適用、max bytes 適用、失敗の `adapter_unavailable` への正規化、`contract.json` の schema 定義。
- Eval は外部 I/O 契約（`adapters/<name>/contract.json` の内容）と正規化写像のみを所有する。subprocess 起動方法・timeout/retry/max bytes の適用・`adapter_unavailable` 正規化は Core が実装する。
- `adapters/<name>/contract.json` の schema（固定）: `{name: enum[trivy,codeql,wazuh,sigma,rustxng,rdsh,llm], version: string, input_ref: sha256, output_ref: sha256, timeout_ms: int (1..300000), retry_cap: int (0..2), response_cap: int (1..52428800)}`。`additionalProperties: false`。
- HTTP broker と Docker command runner は Core 共通実行基盤。各 adapter の normalizer と client request construction は Eval 所有。
- Core の `adapters/base.py` と Eval の `adapters/<name>/contract.json` は同一 schema を参照し、双方の依存記述を一致させる。

### 11.1 public functions（引数・戻り値固定）

- `contracts.load_schema(name: str) -> dict`
- `contracts.validate(name: str, instance: dict) -> None`（違反は `ContractError`）
- `contracts.semantic_validate(name: str, instance: dict, ctx: dict) -> None`（cross-campaign/stale/expiry 検査）
- `storage.append_event(run_id: str, event: dict) -> str`（event_hash を返す）
- `storage.read_events(run_id: str) -> Iterator[dict]`
- `storage.put_object(data: bytes) -> str`（sha256 を返す）
- `storage.get_object(sha256: str) -> bytes`
- `storage.update_catalog(run_id: str, entry: dict) -> None`
- `storage.append_verification(record: dict) -> None`
- `runtime.start_run(campaign_id: str, phase: str, seed: int) -> str`（run_id を返す）
- `runtime.seal_run(campaign_id: str, run_id: str) -> dict`
- `runtime.transition(campaign_id: str, to: str, guard_ctx: dict) -> None`
- `runtime.resume_after_crash(run_id: str) -> str`（新 run_id を返す）
- `policy.load(path: str) -> dict`（fail closed）
- `policy.check_allowlist(request: dict, allowlist: list) -> bool`
- `policy.resolve_actor(request: dict, policy: dict) -> dict`
- `worker.launch(role: str, ctx: dict) -> dict`
- `worker.submit_observation(observation: dict) -> str`（broker 経由 append）
- `server.serve(bind: str) -> None`（`127.0.0.1` のみ。非 loopback bind は入力エラー exit 2 `bind_not_loopback`）
- `cli.main(argv: list[str]) -> int`（exit code）
- `adapters.base.run_adapter(name: str, request: dict) -> dict`（timeout/retry cap/max bytes 適用、失敗は `adapter_unavailable` に正規化）
- `adapters.base.load_contract(name: str) -> dict`（`contract.json` schema 検証）

## 12. 要件追跡（Core 範囲）

| R-ID | 節 |
|---|---|
| R-007〜R-012 | 7, 9 |
| R-013〜R-016 | 4.6, 10.5 |
| R-020, R-021, R-023 | 5, 8 |
| R-025, R-026 | 2, 4 |
| R-029〜R-033 | 1, 3, 6 |
| R-034, R-035, R-037 | 5, 8 |
| R-038, R-039 | 10, 11 |
| R-044 | API-CONTRACTS.md |
| R-045 | 0 |
| R-048 | 7, 9 |

Eval 範囲（R-002, R-008, R-017〜R-019, R-022, R-024, R-027, R-036, R-040〜R-043, R-046, R-047, R-049, R-050）は Eval 仕様で扱う。Core は §10 のインターフェースのみ固定する。


現行実装の検証結果と配置は[製品検収の対応表](../PRODUCT-ACCEPTANCE.md)を参照する。3段階の見直しはCOMPACTION-01〜03に保存した。
