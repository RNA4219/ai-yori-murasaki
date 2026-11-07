---
task_id: 20261106-04
intent_id: INT-AYM-P0-20261106
owner: RNA4219
status: in_progress
priority: P1
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
acceptance_id: AC-20261106-04
---

# レポートに基づく P0 契約とオフライン基盤

## Objective

ユーザーの「レポートを読んで進めて」という依頼に基づき、[原資料（非公開の保存資料）](<../ADR/deep-research-report (16).md>) の最初のアクションである P0 の8契約と、契約を検証するローカル基盤を実装する。

## Scope

- In: Campaign / Run / EvidenceEvent / OraclePredicate / Finding / Remediation / Approval / FinalVerdict の JSON Schema、ローカル検証 CLI、JSON CAS、証跡チェーン、決定論的 Oracle、Blind Blue 用の情報抽出、fixture と境界テスト。
- Out: rustdsh / DSH の起動・変更、実ターゲット・ネットワークへの検証、Trivy / CodeQL / Wazuh / Sigma / 検索サービス接続、実パッチ生成・公開、本番適用、RSI の性能評価。

## Requirements

- Python 3.11 以上。JSON Schema 2020-12 をローカル登録し、参照解決で外部通信を行わない。
- 8契約は不明フィールド、不正型、未指定の必須項目を拒否する。契約の構造と run 間の namespace の分離を検証する。
- 証跡は保存前に指定 secret と機密キーを除去し、内容アドレスの JSON と hash chain で管理する。
- 信頼された件数・最終 hash を照合し、改変・途中削除・末尾削除・並べ替え・artifact の破損を検出する。seal 自体の署名や外部保管は今回実装しない。
- Oracle は保存済み JSON の明示的な値を比較する。未観測・不正入力・欠損 artifact を PASS にしない。
- Blind Blue の情報抽出は Red / Oracle の証跡を渡さない。これは OS 権限・実行基盤による隔離とは区別する。
- 原資料と RSI の9項目、削除済みの名称を保全する。P0 全体や性能向上が完了したとは報告しない。

## Affected Paths

`pyproject.toml`、Python package と schema、`fixtures/`、`tests/`、運用・設計・検収文書、Birdseye。

## Local Commands

依存を固定してローカル検査を行う。実コマンドと結果は検収へ記録する。

## Deliverables

[P0 契約](../P0-CONTRACTS.md)、[AC-20261106-04](../acceptance/AC-20261106-04.md)、実行可能な CLI と fixture。

## Plan

契約固定 → 証跡・Oracle・閲覧制御の実装 → 境界テストと CLI → 文書・索引・検収の同期。

## Tests

原資料 T01〜03 / T05 / T16 に対応するオフライン検査と、T04 のうち情報抽出の契約検査。実際のプロセス隔離・exploit 再現は未実施として記録する。
unit / fixture 試験のみで比較評価・性能実験は行わないため、評価 identity manifest は適用外。
