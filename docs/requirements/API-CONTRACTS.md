---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-06
---

# API-CONTRACTS.md — ai-yori-murasaki v1 API / CLI 契約（DeepSeek仕様設計）

intent_id: INT-AYM-PRODUCT-20261106 / owner: RNA4219 / status: reviewed

本稿は Core 担当が固定する CLI と read-only HTTP の契約を定める。Eval 担当の adapter 実体・lab 実体は本稿で再定義しない。

## 1. CLI 共通契約

### 1.1 stdout JSON

```json
{"schema_version":"1.0.0","command":"<command>","status":"<ok|fail|hold|denied>","reason_code":"<code>","campaign_id":"<id|null>","run_id":"<id|null>","data":{}}
```

- `status`: `ok`（exit 0）/ `fail`（exit 1 または入力エラー exit 2）/ `hold`（exit 3）/ `denied`（exit 4）。
- `reason_code`: §1.2 列挙。
- stderr は診断のみ。secret 除去。

### 1.2 reason_code 列挙

`input_invalid, usage_error, verdict_fail, hold, incomplete, policy_denied, authorization_insufficient, adapter_unavailable, checkpoint_missing, checkpoint_mismatch, freeze_modified, unpinned_comparison, apply_args_missing, bind_not_loopback, duplicate_key, schema_violation, cross_campaign_ref, stale_hash, approval_expired`

### 1.3 exit code

| code | 意味 |
|---|---|
| 0 | 処理成功 / 検証 PASS |
| 1 | 判定 FAIL |
| 2 | 入力 / 利用法エラー |
| 3 | HOLD または未完了 |
| 4 | policy deny または認可不足 |

### 1.4 コマンド別契約

#### `aym rsi agent-registry-init / agent-status / agent-rollback`

- `agent-registry-init --campaign-id <id> --baseline-ref <sha256>`: schema検証済みbaselineを初期登録する。既存registryの上書きは拒否する。
- `agent-status`: 通常実行で選択する方策の `policy_selection` を返す。破損・未承認状態の読み取りは失敗し、成功としてデフォルトを返さない。
- `agent-rollback --campaign-id <id> --expected-current <sha256> --reason <1..128文字>`: trusted履歴上の直前方策へ戻す。正常な復元先と期待current一致が必要。
- 更新コマンドはverify runに結果refを記録し、chainとcheckpointをsealする。失敗は非ゼロ終了とする。これらは従来の認可方針用 `rsi apply` とは別の、製品agent bundle用コマンドである。
- `lab demo / iterate --policy-bundle-file <絶対パス>` の選択順、拒否条件、証跡は [方策選択契約](QUALITY-INTEGRATION.md#通常実行の方策選択) に従う。

#### `aym campaign create`

- args: `--intent-id <string>`, `--scope-file <path>`, `--authorization-file <path>`, `--idempotency-key <string>`（必須）。
- 成功: exit 0、`data={campaign_id}`。
- 入力不正: exit 2、`reason_code=input_invalid`。
- authorization 期限切れ: exit 4、`reason_code=authorization_insufficient`。
- 同一 idempotency key: 同一結果を返す。

#### `aym run start`

- args: `--campaign-id <id>`, `--phase <enum>`, `--seed <int>`, `--idempotency-key <string>`（必須）。
- 成功: exit 0、`data={run_id}`。
- phase 順序違反: exit 3、`reason_code=hold`。
- 同一 campaign で同時 running: exit 3、`reason_code=hold`。

#### `aym run seal`

- args: `--campaign-id <id>`, `--run-id <id>`。
- 成功: exit 0、`data={sealed_at, last_sequence, last_event_hash}`。
- 既 seal: 冪等、同一結果。

#### `aym checkpoint seal`

- args: `--campaign-id <id>`, `--run-id <id>`。
- 成功: exit 0、`data={checkpoint_digest}`。
- `TRUSTED_ROOT` 未設定: exit 2、`reason_code=input_invalid`。

#### `aym oracle evaluate`

- args: `--run-id <id>`, `--predicate-file <path>`。
- 成功: exit 0、`data={verdict, reason_code, outcome}`。
- LLM 呼出検出: exit 4、`reason_code=policy_denied`。
- 実行障害: exit 3、`reason_code=hold`。

#### `aym rsi freeze`

- args: `--campaign-id <id>`, `--freeze-file <path>`。
- 成功: exit 0、`data={freeze_id, content_digest}`。
- 観測後変更: exit 4、`reason_code=freeze_modified`。

#### `aym rsi ready`

- args: `--candidate-id <id>`。
- 成功: exit 0、`data={ready: true}`。
- unpinned 比較: exit 4、`reason_code=unpinned_comparison`。

#### `aym rsi apply`

- args: `--review-id <id>`, `--expected-baseline <id>`, `--rollback <id>`。
- 成功: exit 0、`data={applied: true}`。
- 引数欠落: exit 2、`reason_code=apply_args_missing`。

#### `aym verify chain`

- args: `--campaign-id <id>`, `--run-id <id>`。
- 成功: exit 0、`data={result: PASS}`。
- 不一致: exit 1、`reason_code=verdict_fail`。
- verification journal に記録。

#### `aym verify checkpoint`

- args: `--campaign-id <id>`, `--run-id <id>`。
- 成功: exit 0、`data={result: PASS}`。
- 不一致: exit 1、`reason_code=checkpoint_mismatch`。
- checkpoint 未提供: exit 3、`reason_code=checkpoint_missing`。
- chain 不正が確認済み: exit 1、`reason_code=verdict_fail`。読み取り不能は exit 3。

#### `aym monitor serve`

- args: `--bind <host:port>`（既定 `127.0.0.1:8765`）。
- `127.0.0.1` 以外: exit 2、`reason_code=bind_not_loopback`。

## 2. read-only HTTP monitor

### 2.1 基本

- bind: `127.0.0.1:8765` のみ。
- read-only。HTML/assets と明示的 snapshot API のみ。raw objects / private events は非公開。
- Host ヘッダ検証: `127.0.0.1:8765` または `localhost:8765` のみ許可。他は 400。
- Origin ヘッダ検証: 同一 origin のみ許可。他は 403。
- projection リスト: `campaigns`, `runs`, `events_public`, `verification`, `approvals`。

### 2.2 endpoints

| method | path | query | 成功 | 失敗 | max size |
|---|---|---|---|---|---|
| GET | `/` | - | HTML index | 500 | 256 KiB |
| GET | `/assets/<path>` | - | static asset | 404 | 1 MiB |
| GET | `/api/campaigns` | - | `{campaigns: [...]}` | 500 | 1 MiB |
| GET | `/api/campaigns/<campaign_id>` | - | `{campaign: {...}}` | 404 | 256 KiB |
| GET | `/api/runs/<run_id>` | - | `{run: {...}}` | 404 | 256 KiB |
| GET | `/api/runs/<run_id>/events` | `?after=<sequence>&limit=<int>` | `{events: [...]}`（public projection） | 404 | 1 MiB |
| GET | `/api/verification/<run_id>` | - | `{verification: [...]}` | 404 | 256 KiB |
| GET | `/api/approvals` | - | `{approvals: [...]}` | 500 | 256 KiB |
| GET | `/api/health` | - | `{status: "ok"}` | - | 1 KiB |

- `limit` 既定 100、上限 1000。
- `after` は sequence。

### 2.3 projection と hidden field 削除

- `events_public` projection は `finding` から `extensions` を削除し、`evidence_refs` を件数のみに置換。
- Blue view（`?view=blue`）は明示 projection で hidden field をサーバー側削除。件数/IDs からも正解漏れしない（`finding.title` は `"[redacted]"`、`references` は空配列）。
- operator 画面のみ。Blue に host service 非到達（worker-blue は `--network none`）。

### 2.4 任意 file path / raw ledger 配信の禁止

- `/assets/<path>` は `monitor/assets/` 配下のみ。`..` を含む path は 400。
- raw ledger（`events.jsonl`）は配信しない。
- raw objects（`Workspace/objects/`）は配信しない。

## 3. モジュール public functions（API 側）

- `server.serve(bind: str) -> None`
- `server.handle_request(method: str, path: str, query: dict, headers: dict) -> tuple[int, dict, bytes]`
- `server.project_event(event: dict, view: str) -> dict`

## 4. 要件追跡

| R-ID | 節 |
|---|---|
| R-044 | 2 |
| R-050 | 2（GLM 設計入力、DeepSeek 実装） |
| R-019 | 2.2（verification 表示） |
| R-024 | 2.2（adapter_status 表示） |


## 5. 製品画面の追加読取投影

監督統合条件:
(1)モデル状態は requested_role_model/registration/development_call/product_connection/pin。GLMのrunning/sealed/holdはRun状態との混同なので使わない。
(2)承認approvedと反映appliedを混同しない。
(3)補助GETを採用:
 /api/models -> {models:[{role,requested_role_model,registration:{status,checked_at,evidence_ref},development_call:{status,evidence_refs},product_connection:{status,checked_at,evidence_ref},pin:{status,version}}]}
 /api/adapters -> {adapters:[{name,status,mode,checked_at}]}
 /api/campaigns/<id>/runs -> {runs:[Run投影]}
 /api/campaigns/<id>/rsi -> {rsi:[{candidate_id,freeze_id,content_digest,status,comparison:{pairs,baseline,candidate,gain}|null,applied:bool}]}
 Campaign投影: campaign_id,intent_id,status,created_at,last_run_at:null|timestamp,finding_count:null|int。
 詳細: {campaign:投影,findings:[{finding_id,severity,title,source}],remediations:[{remediation_id,status}],metrics:object|null}
 Run投影: run_id,campaign_id,phase,status,completion,cause,started_at,ended_at,sealed_at,last_sequence,last_event_hash。
 Approval投影: approval_id,remediation_id,decision,decided_at,expires_at,applied:boolean（無ければfalseにせず未確認）。
(4)hash route campaign/run の一覧導線。初期campaign一覧とmodel/adapters表示。
(5)必要API未提供を代替完成にしない、実装予定のこのcontractへ接続。
(6)15秒ごとの読取更新を追加、hidden時休止、fetch5秒上限/1MiBストリームcap、古い応答無視、stale保持、manualbutton disableでfocus喪失させない。
(7)本画面はoperatorローカル専用。Blueはhost到達不能。

現行実装の検証結果と配置は[製品検収の対応表](../PRODUCT-ACCEPTANCE.md)を参照する。3段階の見直しはCOMPACTION-01〜03に保存した。
