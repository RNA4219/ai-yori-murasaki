---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-06
---

# 評価・アダプター仕様

### A-0. 前提と参照境界

- 本仕様は Eval 担当の所有範囲（7 adapter の**外部 I/O 契約と正規化写像**、lab 実体、RSI/KPI/enrichment/P2、受入）を定める。
- Core が固定するもの（本仕様で再定義しない）: `Workspace/runs/<run_id>/events.jsonl` 単一正本、`Workspace/objects/<sha256>.json` CAS、catalog の実体（SPEC-CORE に従い JSON）、`aym-json-v1` canonical、event hash 式、exit code 統一、CLI stdout envelope、policy engine の decision log 形式、RunContext/ArtifactRef/Observation/OracleEvaluation の型。
- Eval は Core の型を**参照**し、フィールド追加が必要な場合は Core 担当へ変更要求を出す（本仕様では追加しない）。

### A-1. 7 adapter の外部 I/O 契約（R-038〜R-040, D-042, D-053, D-054）

各 adapter は Core の `adapters/<name>/contract.json` 形式に従い、以下を**外部 I/O 契約**として固定する。内部実装（subprocess 起動方法等）は Core の adapter 基盤に委ねる。

| adapter | 入力（外部） | 出力（正規化写像先） | timeout | retry cap | max bytes | 未取得/失敗時 |
|---|---|---|---|---|---|---|
| trivy | `trivy fs --format json <path>` の stdout JSON | Finding（lossless: ID/件数/位置/参照/拡張保持、secret は種類/位置/ルール版のみ） | 120s | 1 | 50MB | `adapter_unavailable` |
| codeql-sarif | SARIF 2.1.0 ファイル | Finding（count/identifiers 一致） | 60s | 1 | 50MB | `adapter_unavailable` |
| wazuh | alert JSON（HTTP optional） | 正規化 event | 30s | 2 | 10MB | `adapter_unavailable`（PASS にしない） |
| sigma | Sigma rule（**v1 は JSON 表現の bounded subset**） | validate 結果 / 発火結果 | 30s | 1 | 5MB | unsupported syntax は HOLD |
| rustxng | `GET /search?q=...&format=json&max_results=5`, `GET /healthz` | 検索結果 / health | 10s | 2 | 5MB | health は upstream 成功保証でない |
| rdsh | 設定済み実行 CLI（未確認フラグ発明しない） | 実行結果 | 300s | 0 | 10MB | `adapter_unavailable` |
| llm | Ollama native `/api/chat` および OpenAI compatible chat completions | 応答 | 60s | 2 | 5MB | `adapter_unavailable` |

**Sigma v1 の bounded subset（AYM2-07 反映）**:
- 表現: **JSON 表現の Sigma**（YAML テキストは v1 対応外、入力時 HOLD）。新パッケージ追加は行わず stdlib で parse する。YAML テキストを「Sigma 互換」と誇張しない。
- wrapper は `{schema_version:"1.0.0", rule:{...}}`。rule は `title`（1..200文字）、UUID `id`、`status:"experimental"`、`logsource:{category:string}`、`detection` を必須とする。任意メタデータは description / author / references / tags / level に限定する。
- 実 Sigma の JSON 表現として `detection:{selection:{field:value, "field|contains":"text"}, condition:"selection"}` を使う。独自の eq オブジェクト DSL は作らない。等値は string / boolean / integer、contains は string。選択内の field は AND。
- 複数の named selection（identifier）を許し、condition は既知 selection 名と小文字 `and` / `or` / `not`、括弧の有限文法。precedence は not > and > or。eval は使わない。
- 非対応（HOLD）: wildcard、regex、aggregation、contains 以外の modifier、list modifier、pipeline、all of / 1 of。YAML テキストも HOLD。壊れた JSON・型・未知 selection 参照・括弧不一致は FAIL。
- 必須試験: `status:403` 等値、`path|contains:"/record"`、`selection and not benign` の正負例。攻撃は発火、固定 benign 集合は不発火。外部 Sigma CLI 全機能互換は主張しない。
- T11/T12 fixture は上記範囲に限定。実 CLI は将来運用条件。

**LLM adapter 記録項目（D-054）**: `prompt_hash`, `requested_model`, `effective_model`, `version/pin`, `temperature`, `seed`, `timeout_ms`, `token_cap`, `tool_calls`, `response_metadata`, `token_count`。モデル出力から command/env/network の権限追加不可。**メタデータは自己申告で採点しない**: `effective_model` と `prompt_hash` は orchestrator/Oracle が実体（request body と response body）から計算して照合する（AYM2-12 反映）。

**RustXNG（R-022, D-053）**: Blue のみ。credential/private hostname/source secret を query へ送らない。query の機密部分を redact し、source URL hash と取得時刻を保存。standard/clean 優先、provider 条件の個別確認を落とさない。

**rdsh（D-053）**: HARNESS-ADAPTER 原典は compile-time trait 接合であり runtime adapter 選択ではない。Python から公開 CLI を呼ぶ adapter と Rust 内部 trait を混同しない。未確認 CLI フラグは仕様として捏造しない。

### A-2. lab 実体（R-012, AYM2-08 反映）

**lab-target**: stdlib Python HTTP サーバ。有限 endpoint のみ。

| endpoint | method | 認可 | 応答 |
|---|---|---|---|
| `/health` | GET | 不要 | 200 `{"status":"ok"}` |
| `/public` | GET | 不要 | 200 公開 fixture 値 |
| `/record` | GET | `actor` header（`guest`/`admin`） | baseline: guest にも保護レコード返却（**不良**）。patch: guest 403 / admin 200 |

- body カナリア（保護レコード値）は **Oracle のみ**が期待値管理。Blue/Red の context へ渡さない。
- 認可記録: scope/authorization に lab-target を含める（無害・認可済み）。

**閉ループの実 diff 検証（AYM2-08 (b)(c)(d)）**:
- (b) 修正は**実ファイルへの実 diff**（`/record` ハンドラの認可分岐）。一時 workspace へ限定適用し、before/after source hash を記録。
- (c) baseline で guest 200（攻撃成立）、patch 後 guest 403（攻撃拒否）、admin は両方 200（正常機能）を**独立 Oracle** が実 response で判定。
- (d) lab adapter は**有限 HTTP 操作**（特定 endpoint への特定 method）のみ許可。任意ファイル変更・実攻撃 engine は作らない。
- Red lab adapter は finite HTTP requests、既存 rdsh adapter は許可された command 仕様への接続口。

**Blue worker の入力経路（AYM2-03 反映）**: Blue worker は network none。入力は orchestrator が事前取得・投影した観測データ（fixture/telemetry snapshot）のみ。外部 HTTP（RustXNG/Wazuh/Ollama）は orchestrator の broker 経由のみ、allowlist 先・credential は broker のみ。host API・Red context は非 mount。

### A-3. RSI（R-042, R-043, D-058, AYM2-09 反映）

**state 機械**: `frozen` → `candidate_created` → `compared` → `ready` → `applied`。各遷移は JSON fields で記録。

**freeze ファイル**: `{campaign_id, objective, thresholds, stop_conditions, adoption_criteria, seed_pairs, order_policy, frozen_at, freeze_hash}`。**freeze_hash は checkpoint と同じ別管理経路（trusted root）に operator 操作で保存**し、照合する（自己申告にしない）。

**predefined criteria（数値）**: local policy candidates で `success_gain > 0`、`no benign regression`、`budget <= fixed`、`安全違反 0`、`完全性 100%`。

**比較条件**: 旧版/新版 policy 候補は同初期状態・同 seed・同モデル identity。finite float metric の空値は `null`。

**seed 対と実行順**: baseline→candidate と交互順を**事前 freeze** 可能。`order_policy` に記録。

**分母と無効化**: candidate 起因 incomplete/deny は **loss（失敗分母）**。external timeout で pair 無効（除外理由 counts を出す）。観測後閾値変更拒否。

**train/validation/holdout**:
- train memory は completed only の抽象 schema。
- 事前 snapshot digest / selection deterministic。
- validation freeze 後の変更は再評価。
- **holdout access を policy event として記録**し、candidate hash changed で contamination 判定。一度見た holdout は他候補採用で再使用禁止。
- unpinned cloud label は exploratory、not ready。

**reflection**: 原因分類→候補 proposal を生成する実機能。LLM は助言のみ。

**apply**: `review_id` / `baseline` / `expected_candidate` / `rollback` / `path_guard` と atomic registry swap。default 実行器へ自動反映しない。

#### 比較評価の identity 契約（第3回レビュー反映）

RSI比較・昇格判断に用いる測定campaignは [評価identity契約](../CONTRACTS.md#evaluation-identity) を適用し、schema_version `1.1`、profile `workflow`、measurement_unit `task` のmanifestを使う。単に現在のKPIを表示する集計と、効果を判断する比較評価は区別する。

- 実行前に owner、task_id、目的、単一仮説、action_delta、負の対照、freeze_date を固定する。
- Legacy/Candidateごとに source_path、source_revision（Gitが無い場合は明示したbundle識別子）、source_sha256、input_sha256 を記録する。runnerのid/hash、datasetのid/hash、実行設定、model_version、policy_version、主指標を固定する。モデル不使用は理由付き `not_used`、cloud aliasは固定版と扱わない。
- freezeに `evaluation_manifest_ref`（CAS digest）と `evaluation_manifest_id` を含め、manifest実体のhashとfreeze digestを別管理trusted rootへ保存する。Run.manifest_refが指す実体にもこの2項目を含める。
- status=frozenのpreflight成功前に比較runを開始しない。製品は必要identity、実体hash、凍結条件を検査する。上流checkerによる適合検査も検収時に実施する。
- 実行後はstatus=completedとし、outcomeのartifact_path / artifact_sha256 / records / metrics、およびerror_count / crash_count / timeout_countを記録する。主指標は有限値、recordsは正の整数。postrunはartifact実在/hash一致を検証する。
- preflight後のidentity変更は拒否。障害が残る比較をpostrun成功にしない。診断は保存し、新規比較IDで再実行する。candidateの完了した不成功は性能上のlossであり、通信障害と混同しない。
- `rsi ready` と `rsi apply` は対象比較のpostrun成功記録とimmutable identityを要求する。postrun成功は性能向上や反映許可の代わりにならず、既存の採用基準・review・rollbackを別に要求する。
- 実装bundle、入力、manifest、結果をID/hashで結ぶ。機能検査用の単体fixture試験は比較実験と区別する。KPI数値そのものの目標達成はv1検収外。

### A-4. KPI（R-002, R-003, D-043, D-050）

各 KPI に numerator / denominator / expected set / null / invalid / skipped / pair_fail を定義。**execution_error を一律除外しない**。監査違反件数を 0 固定しない。ledger missing 数は期待 manifest を分母に。FP/TP は重複 stable finding id で集計。det time clock 不整合は diagnostic。

| KPI | numerator | denominator | expected set | null 条件 | invalid | skipped | pair_fail |
|---|---|---|---|---|---|---|---|
| 証拠完全性 | verify PASS event 数 | event 総数 | run manifest | 未実行 | hash 不一致 | なし | なし |
| Oracle 再実行一致率 | 一致 verdict 数 | 再実行対象 run 数 | 再実行可能 run | adapter_unavailable | 実行障害 | 対象外 run | なし |
| Blue Recall | 検知 attack 数 | attack fixture 数 | Red fixture 集合 | 未実行 | 未検知 | なし | なし |
| Blue Precision | TP 数 | TP+FP 数（stable id 重複排除） | benign+attack | 未実行 | 未検知 | なし | なし |
| 修正成功率 | 修復成功数 | 修復試行数 | 修復対象 finding | execution_error は invalid で除外せず別記録 | 実行障害 | なし | なし |
| 過剰修復率 | 正常系 regression 失敗数 | 修復適用数 | 修復適用 run | 未実行 | 未実行 | なし | なし |
| Evidence 欠落率 | 欠落 event 数 | 期待 manifest event 数 | run manifest | なし | なし | なし | なし |
| Red→検知時間 | 検知 pair の時間差 | 検知 pair 数 | 検知済み pair | 未検知 | negative/out-of-order は diagnostic で別記録 | なし | なし |
| 未検知 ATT&CK technique | 未検知 technique 数 | technique 集合 | Red fixture | 未実行 | 未実行 | なし | なし |
| 本番自動適用件数 | audit 件数 | 1 | 全 run | 0 固定しない（実件数） | なし | なし | なし |
| 承認無し privileged action 件数 | audit 件数 | 1 | 全 run | 0 固定しない（実件数） | なし | なし | なし |

未測定時は達成表示禁止。測定済みは対象集合/条件/限界を併記。

### A-5. enrichment（R-046, D-047, AYM2-12 反映）

- ATT&CK techniques / KEV vulnerabilities / EPSS CSV・JSON それぞれ **import + CAS + digest/URL/date/TTL**。
- TTL 具体 default / version attrs mandatory。invalid snapshot は reject。unknown score は `null`。
- **snapshot は CAS に実体を保存し、申告 hash と実体 hash の一致を verifier が照合**（自己申告にしない）。
- 期限切れ時と未取得時の挙動（未確認表示、優先付けへの影響範囲）を定義しテストで確認。

### A-6. P2（R-047, D-046）

- budget bounded Purple iteration、role model routing、Sigma candidate tuning、crash resume を**各 1 動作**。
- RAM/RSS は実測プラットフォーム API、未取得は `null`。
- 実装 batch から漏らさない（B-Eval-P2 に配置）。

### A-7. 受入（R-050 含む）

- T01〜T20 + RSI 9 + P2 + API/UI boundary の input / observations / expected / 根拠 / real vs contract vs fault を用意。
- テストは別人 GLM が検証可能。**expected は LLM から生成/提供しない**。
- 外部 live absent と製品 lab real を区別。
- 全 50 R の coverage 役割指定。
- R-050: 適用スキルファイル hash、GLM 入出力記録、DeepSeek 実装、GLM レビュー、PC/スマホ確認記録の保管先・記録形式を定める。

---

現行実装の検証結果と配置は[製品検収の対応表](../PRODUCT-ACCEPTANCE.md)を参照する。3段階の見直しはCOMPACTION-01〜03に保存した。
