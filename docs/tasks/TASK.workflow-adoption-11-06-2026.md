---
task_id: 20261106-01
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: done
priority: P1
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
acceptance_id: AC-20261106-01
---

# Workflow Cookbook Tier 3 導入

## Objective

ユーザーの依頼に従い、このフォルダーへ Workflow Cookbook の Full 様式を導入し、RUNBOOK に Imozuru / Yomiyasu の使用を記載する。

## Scope

- In: 基本文書、作業・検収・変更履歴、Birdseye、導入元追跡、運用雛形。
- Out: 製品実装、原典調査、受領レポートの全文推敲、実ターゲット検証、Git 初期化、公開。

## Requirements

- Behavior: Tier 3 必須構成を実内容で揃え、2スキルの用途と手順を記載する。
- I/O Contract: 受領レポートとローカル Cookbook を入力とし、[導入対応表](../workflow-adoption.md) と文書体系を出力する。
- Constraints: 原資料と導入元は変更しない。導入元のローカル差分は hash で識別する。
- Acceptance Criteria: [EVALUATION](../../EVALUATION.md) の WF-01〜WF-07。
- Evaluation Identity: 文書検査のみで比較評価は行わないため、manifest は適用外。

## Affected Paths

ルート運用文書、`docs/`、`governance/`、`.github/`、`codemap.config.json`、`.gitignore`。

## Local Commands

[RUNBOOK Execute](../../RUNBOOK.md#execute) の Tier / Acceptance / Birdseye 検査。

## Deliverables

[Acceptance AC-20261106-01](../acceptance/AC-20261106-01.md)、[CHANGELOG 0001](../../CHANGELOG.md)、[採用判断 ADR-0001](../ADR/0001-workflow-cookbook-tier3.md)。公開版は作成しない。

## Plan

1. Cookbook の Tier 3 定義、テンプレート、対象資料を確認する。
2. 日本語で文書と作業追跡を作成する。
3. Birdseye を生成し、原文と Capsule を照合する。
4. 必要な検査を実行し、技術検収と完了記録を確定する。

## Patch

既存レポートは保持し、新規文書・設定のみ追加する。

## Tests

実コマンド、結果、未実行の正本は [検収記録](../acceptance/AC-20261106-01.md#evidence)。
公式3ゲートは終了コード0。本文・リンク・ID・原資料 hash の補助照合も成功した。

## Commands

[RUNBOOK](../../RUNBOOK.md#execute) と検収の Evidence を参照する。

## Notes

製品コード、Git 履歴、リモート、CI は未設定。外部仕様の再確認は今後、依頼された調査・実装の範囲で行う。
