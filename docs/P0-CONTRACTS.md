---
intent_id: INT-AYM-P0-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# P0 の契約とオフライン基盤

[原資料](<ADR/deep-research-report (16).md>) の「実行可能な次のアクション」の最初を実装する。
契約の版は `blue-purple/v1`。JSON Schema 2020-12 の8契約と共通定義を Python package に同梱し、実行時の外部参照取得は拒否する。

## 契約

| 契約 | 主な境界 |
| --- | --- |
| Campaign | owner、認可参照、lab / CI の対象、予算、状態 |
| Run | campaign、role / mode、model・入力・policy の版、context / credential namespace |
| EvidenceEvent | 操作・観測、可視性、保存前の秘匿化、artifact、前後の hash |
| OraclePredicate | JSON artifact の digest、JSON Pointer、期待値。コード・shell は実行しない |
| Finding | 発見元、重要度、確認状態、証拠 digest |
| Remediation | Finding 参照、patch digest、PR-only、静的検査・replay・正常系の結果 |
| Approval | 対象 digest、判断者、判断・理由・期限。deployment の承認には human を要求 |
| FinalVerdict | 非空の成功した checks がある場合だけ pass。欠損や判定不能は hold |

## 証跡と秘匿化

JSON は重複キーと NaN / Infinity を拒否し、UTF-8・キー順・空白を固定して hash 化する。
`event_hash = SHA256(canonical_event || previous_event_hash)`。canonical_event は前 hash と自身の hash を除く。
JSON artifact は SHA-256 で格納し、読み出すたびに内容を照合する。

verify には信頼された `event_count` / `head_hash` / campaign / run の seal が必要。末尾削除も検出する。
seal と ledger の両方を書き換えられる攻撃者に対する真正性、外部署名、OS レベルの append-only は今回保証しない。実運用では seal を独立した信頼境界へ固定する。
書込みは協調するローカルプロセス間の排他を行い、直前の seal と一致した状態だけに追記する。

秘密のキーと、呼出し側が登録した secret 文字列を保存前に除去する。任意の機密情報を自動で完全発見する DLP ではない。
CAS と ledger には秘匿化後の JSON を保存する。原本・暗号化・retention・資格情報の実運用管理は後続工程。

## Oracle と Blind Blue

Oracle は指定 digest の JSON artifact から JSON Pointer で値を取得し、canonical JSON の一致を確認する。
観測がない場合は hold。空の checks も hold。モデルの自己申告や自然文を実行可能なコードとして扱わない。
観測データの取得元そのものの正しさは、将来の独立した adapter / Oracle runner の責任とする。

Blind Blue 用の抽出では Red と Oracle の event を除き、blue / shared の可視性だけを渡す。hash chain は抽出後の view ではなく元の ledger で検証する。
raw ledger へアクセスできる同一 OS ユーザーを隔離する機能ではない。セッション、資格情報、ファイル権限と外側の container / VM は後続の実装項目。

## 後続工程

1. rustdsh / DSH の実契約を原典確認し、profile・権限・session と OS 隔離を接続する。
2. Trivy / CodeQL の実結果を schema へ正規化する。
3. disposable fixture で Red → Blind Blue → 修復案 → Oracle replay を検証する。
4. P1 の telemetry・検知ルール・検索サービスと、[RSI](RSI.md) の実行・評価系を接続する。

現時点の fixture は JSON データであり、実脆弱性の exploit・修復・検知能力を実証したものではない。

## 仕様の参照

- [JSON Schema 2020-12](https://json-schema.org/draft/2020-12/json-schema-validation)
- [jsonschema のローカル参照登録](https://python-jsonschema.readthedocs.io/en/stable/referencing/)

実装・試験結果は [Task 20261106-04](tasks/TASK.p0-core-11-06-2026.md) と [技術検収](acceptance/AC-20261106-04.md) で追跡する。
