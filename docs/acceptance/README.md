---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# Acceptance 運用

[ACCEPTANCE_TEMPLATE](ACCEPTANCE_TEMPLATE.md) を `AC-YYYYMMDD-xx.md` へ複製し、対象 Task と Intent を一致させる。
必須フィールドは `acceptance_id`、`task_id`、`intent_id`、`owner`、`status`、`reviewed_at`、`reviewed_by`。
必須見出しは `Scope`、`Acceptance Criteria`、`Evidence`、`Verification Result`。

状態は `draft`、`approved`、`rejected`。`approval_type: technical` は検証可能な技術条件の確認を表す。
担当エージェントが文書の技術検収を記録しても、人間の公開承認・セキュリティ審査・リスク受容を代行したことにはしない。

実行コマンド、終了コード、確認した内容、未実行理由を Evidence へ記す。
[RUNBOOK](../../RUNBOOK.md#execute) の checker は必須構造を検証するため、内容と ID の整合も別途確認する。
結果は [INDEX](INDEX.md)、Task、[CHANGELOG](../../CHANGELOG.md) と相互参照する。
