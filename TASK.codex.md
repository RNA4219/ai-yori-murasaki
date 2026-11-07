---
task_id: YYYYMMDD-xx
intent_id: INT-REPLACE
owner: RNA4219
status: draft
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# Task Seed 雛形

これは雛形であり、未実行の作業を完了済みとは扱わない。[Task 運用](docs/tasks/README.md) に従って複製し、ID を確定する。

## メタデータ

- priority: 作業に応じて P1 / P2 / P3 を指定する。
- repo / base_branch / work_branch: Git を利用する場合に実在する値を記録する。

## Objective

依頼の目的と完了時の具体的な状態を記載する。

## Scope

- In: 対象ファイル・機能。
- Out: 今回扱わない対象。

## Requirements

- Behavior: 期待する動作・文書内容。
- I/O Contract: 入力、出力、ID、型、版。
- Constraints: 互換性、権限、原資料の保持。
- Acceptance Criteria: 検証可能な成功条件。

### Evaluation Identity

比較評価・実験・昇格・提出を含む場合は [契約](docs/CONTRACTS.md#evaluation-identity) に従い、manifest パス、preflight / postrun コマンドを記す。
文書検査だけなら適用外と理由を記す。

### RSI

自己改善を含む場合は [RSI 方針](docs/RSI.md) と [RSI 契約](docs/CONTRACTS.md#rsi) に従い、baseline / candidate、変更仮説、目的別評価器、split / seed、予算・停止・採用条件、経験の有効 / 無効と snapshot を記す。
実装確認と性能向上の実証、診断と採用評価、`promotion-ready` と実行器への反映を分ける。文書追加だけなら実行評価は適用外と明記する。

## Affected Paths

変更予定パスを列挙する。

## Local Commands

[RUNBOOK](RUNBOOK.md#execute) から適用するコマンドを選ぶ。製品コマンドは実在するものだけ追加する。

## Deliverables

成果物、`docs/acceptance/AC-YYYYMMDD-xx.md`、CHANGELOG、必要時の PR と公開記録。

## Plan

現状確認 → 範囲内の変更 → 適切な検証 → 記録・索引の同期。

## Patch

変更したパスと要点。Git を利用する場合はレビュー可能な diff を添える。

## Tests

必要な検査、実コマンド、終了コード、未実行理由を記録する。

## Commands

実行結果の正本となる検収記録へリンクする。

## Notes

判断理由、リスク、未解決事項、必要な後続作業を記載する。
