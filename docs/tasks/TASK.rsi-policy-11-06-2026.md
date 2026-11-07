---
task_id: 20261106-02
intent_id: INT-AYM-RSI-20261106
owner: RNA4219
status: done
priority: P1
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
acceptance_id: AC-20261106-02
---

# RSI の設計・運用・評価規約を追加

## Objective

ユーザーの9項目を [RSI 方針](../RSI.md) へ収録し、既存の Workflow Cookbook 様式の中で運用・評価・作業追跡へ接続する。

## Scope

- In: RSI 方針、Blueprint / Runbook / 評価契約・基準、Task 雛形、チェックリスト、governance、導線・検収・Birdseye の同期。
- Out: 実行器・評価器・メモリの実装、参照元の変更、攻撃・実験、性能実証、公開。

## Requirements

- Behavior: 9項目と適用上の注意を保持し、実行中の適応と run 間の改善を区別する。
- I/O Contract: 提示文と参照3文書を入力とし、本プロジェクトの設計・運用規約を出力する。
- Constraints: 原資料、以前の技術検収・証跡を変更しない。数値閾値を機械的に継承しない。
- Acceptance Criteria: [EVALUATION の RSI-DOC-01〜06](../../EVALUATION.md#rsi-documentation-criteria)。
- Evaluation Identity: 文書追加と参照確認のみ。比較評価を行わないため manifest は適用外。

## Affected Paths

`docs/RSI.md`、`docs/CONTRACTS.md`、ルート運用文書、`governance/policy.yaml`、Task / Acceptance / ADR、Birdseye。

## Local Commands

[RUNBOOK Execute](../../RUNBOOK.md#execute) の3ゲートと、文書の参照・ID・9項目・原資料 hash の照合。

## Deliverables

[AC-20261106-02](../acceptance/AC-20261106-02.md)、[CHANGELOG 0002](../../CHANGELOG.md)、[参照元記録](../evidence/rsi-reference-snapshot-20261106.json)。

## Plan

参照確認 → 提示文の収録と規約への接続 → 文書と Birdseye の照合 → 検証・技術検収。

## Patch

既存の様式・履歴を保ち、RSI の正本と参照を追加する。

## Tests

実コマンドと結果の正本は [検収記録](../acceptance/AC-20261106-02.md#evidence)。
公式3ゲートと文書の補助照合は成功した。提示9項目と適用上の注意の保持、以前の資料・証跡の保全も確認した。

## Commands

[RUNBOOK](../../RUNBOOK.md#execute) と検収記録を参照する。

## Notes

文書上の受入成功を、RSI 機能の実装完了や性能向上の証明として扱わない。
