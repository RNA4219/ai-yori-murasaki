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

#### `aym monitor index [--full]`

初回と実行別の検証結果を持たない旧形式からの移行時は全件を検証する。差分更新対応の索引からは、保存済みの実行別集計で系列・比較時刻の索引を作る。以後は実行ごとに台帳・manifest・カタログ上の所属・参照先の検知ファイルを照合する。追加・変更された実行を検証し、削除された実行を除き、影響を受けた案件を再集計する。共通のカタログに変更があったときはカタログ全体を読み直す。

応答の `data` は `mode`（`full` / `incremental`）、`total`（案件数）、`rechecked_runs`（検証を試みた実行数）、`removed_runs`（差分更新で索引から除いた実行数）、`checked_at`（更新成功時刻）を含む。検知数と抜粋、案件順、確認時刻を同時に公開する。途中のI/O障害や書き込み失敗では旧世代を参照できる。台帳の欠損やハッシュ不一致を確認できた場合は、従来の「未集計」「一部未集計」の規則を適用する。

属性の照合にはファイル識別子、モード、サイズ、更新日時、変更日時を使う。全台帳の内容を照合するには `--full` を指定する。

operatorの案件一覧と詳細の `severity_counts` は、同じ案件内の検知IDを一度だけ数えた重要度別件数。キーは `critical`、`high`、`medium`、`low`、`info`、`unknown`（重要度未記録）。未集計なら値はnull、一部未集計なら確認済み分。Blueへの応答からは省く。v2索引は `aym monitor index` で保存済みの実行別集計からv3の内訳を作る。更新前は一覧から詳細に進んで内訳を読める。判断画面への投影は、元記録に有効な重要度がない場合に `severity_unrecorded` を付ける。[重要度別の検収](../acceptance/AC-20261123-08.md)

案件詳細・実行一覧・評価レポート・RSIは、索引がある場合、案件IDで特定した実行の台帳だけを検証する。判断画面は同系列の直前の時刻を索引から引く。同時刻の候補が複数あれば候補IDを返し、適格な `previous` の指定時にその案件を比較する。索引にない案件は404。大規模ストアで索引がない場合、形式が古い場合、対象の実行ファイルの属性が索引と食い違う場合は409と `monitor_index_required` を返す。索引は最終更新時の所属を表し、新規実行は次回の更新で反映する。[技術検収](../acceptance/AC-20261123-07.md)

#### `aym intake register`

`--name <案件名>`（前後の空白を除き1〜160文字）と `--idempotency-key <キー>`（1〜256文字）を受け取る。キーはワークスペース単位。同じキーと案件名なら初回の `data.receipt_id, name, registered_at` をそのまま返す。別名で同じキーを使うと終了コード2、`idempotency_conflict`。同名でもキーが異なれば別の受付となる。受付では `campaign_id` と `run_id` はどちらもnullで、監査の作成や実行は行わない。

#### `aym intake link`

`--receipt-id <受付ID> --campaign-id <案件ID>` で既存の受付を同一ワークスペースの監査に結ぶ。成功時は `data.receipt_id` と `data.campaign_id` を返す。同じ対応は再送できる。別の監査との競合は `intake_link_conflict` で拒否し、元の対応を保持する。存在しないIDも拒否する。受付台帳の検索では連結済みの行に `campaign_id` を含め、未連結の行は以前と同じ形式で返す。[T56 の仕様](SPEC-CASE-DECISIONS.md)

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
| GET | `/architecture` | `?system=aym` | 既定は注文管理の見本。aym指定で製品自身の改修後の図を開く | 500 | 256 KiB |
| GET | `/architecture/<asset>` | - | 同梱した `map.mjs`、`model.mjs`、`js-yaml.mjs`、`sample.json`、`product.json` | 404 | 1 MiB |
| GET | `/assets/<path>` | - | static asset | 404 | 1 MiB |
| GET | `/api/campaigns` | - | `{campaigns: [...]}` | 500 | 1 MiB |
| GET | `/api/intake` | `q` は受付IDの完全一致、または案件名の前方一致 | `receipts, has_more`。最大50件。未指定時は受付順の降順。Blueは `{"status":"withheld"}` | 400 / 500 | 1 MiB |
| GET | `/api/campaigns?page=1` | 1始まり、1ページ50案件 | `campaigns, page, pages, total, checked_at`。確認済み索引の先頭は作成日時降順、同日時は案件ID順。Blueには検知抜粋・未集計の実行IDを含めない | 400 / 409 / 500 | 1 MiB |
| GET | `/api/campaign-relations` | `?view=operator\|blue` | operatorは案件ID、目的名、作成日時、確定できる比較元の `previous_id`。同時刻の複数候補は `previous_candidates` にID順で列挙し、`previous_id` はnull。Blueは `{"status":"withheld"}` | 500 | 1 MiB |
| GET | `/api/campaigns/<campaign_id>/decision-view` | `?previous=<id>` | 複数候補の中で選んだIDを照合して比較。未選択・候補外は比較を保留する。今回の集計が不完全ならhold、前回が不完全なら今回分だけを返す。Blueには比較を公開しない | 404 | 1 MiB |
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
 Campaign投影: campaign_id,intent_id,status,created_at,last_run_at:null|timestamp,finding_count:null|int。finding_count_statusはcomplete（確定）、partial（確認済みの件数）、unavailable（件数null）。verified_run_countとunverified_run_countを併記し、operatorはunverified_run_idsから対象の実行を開ける。
 詳細: {campaign:投影,findings:[{finding_id,severity,title,source}],remediations:[{remediation_id,status}],metrics:object|null}
 Run投影: run_id,campaign_id,phase,status,completion,cause,started_at,ended_at,sealed_at,last_sequence,last_event_hash。
 Approval投影: approval_id,remediation_id,decision,decided_at,expires_at,applied:boolean（無ければfalseにせず未確認）。
(4)hash route campaign/run の一覧導線。初期campaign一覧とmodel/adapters表示。
(5)必要API未提供を代替完成にしない、実装予定のこのcontractへ接続。
(6)15秒ごとの読取更新を追加、hidden時休止、fetch5秒上限/1MiBストリームcap、古い応答無視、stale保持、manualbutton disableでfocus喪失させない。
(7)本画面はoperatorローカル専用。Blueはhost到達不能。

現行実装の検証結果と配置は[製品検収の対応表](../PRODUCT-ACCEPTANCE.md)を参照する。3段階の見直しはCOMPACTION-01〜03に保存した。

## 6. operator用の資料取り込み

CLIは `aym sources list/show/import-obsidian/import-file/import-mcp`。各引数とMCPの許可ツール・接続形式は[資料取り込みガイド](../SOURCES.md)を正本とする。通常のJSON応答・終了コード規則を継承し、成功はexit 0、入力不正はexit 2、MCP未導入・接続失敗はexit 3とする。

取り込み結果は `data={status: imported|updated|unchanged, document_id, ref}`。資料の保存状態は `reference_only` とし、取り込み操作の結果状態と区別する。provider・出典URLでIDを固定し、同一内容は再登録せず、変更時は `previous_ref` で過去のCASを保持する。内容hashには本文、タイトル、出典、関連リンクを含む。Findingの件数やOracleの採点には加えない。

| method | path | operatorの成功応答 |
| --- | --- | --- |
| GET | `/api/sources` | `{sources: [{document_id, provider, source_url, title, links, imported_at, status, transport}]}` |
| GET | `/api/sources/<document_id>` | `{source: {一覧項目, body, content_sha256, previous_ref, ref}}` |

欠落・破損は409 `source_unavailable`。Blue viewでは、存在するIDでも存在しないIDでも内容・件数を含まない同じ `withheld` 応答にする。任意path、raw CAS、資格情報・接続設定は公開しない。既存のHost／Origin検証、GET／HEAD限定、Blueのネットワーク隔離を維持する。資料の出典リンクは操作者指定の所在であり、正しさの認定ではない。
