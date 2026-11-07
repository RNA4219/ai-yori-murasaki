---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# Task 運用

[TASK.codex.md](../../TASK.codex.md) を複製し、`TASK.<slug>-MM-DD-YYYY.md` としてこのディレクトリに保存する。
`task_id: YYYYMMDD-xx` は同日に重複しない値とし、Intent、owner、review 日・期限を記す。

状態は `planned → active → in_progress → reviewing → done` を基本とする。外部依存で停止した場合は `blocked` と理由を記し、解除後は作業中へ戻す。
完了には当該作業の受入条件、必要な検証、対応する Acceptance が必要。
未実行の検証や製品実装を、文書の整備だけで完了へ進めない。

Task、[Acceptance](../acceptance/README.md)、[CHANGELOG](../../CHANGELOG.md)、必要時の [公開記録](../releases/README.md) を相互参照する。
作業を追加・完了したら [HUB](../../HUB.codex.md)、一覧と Birdseye を更新する。

## 一覧

| task_id | 作業 | 検収 |
| --- | --- | --- |
| 20261107-03 | [分類・判断支援・追加検証](TASK.product-clarity-11-07-2026.md) | [AC-20261107-03](../acceptance/AC-20261107-03.md) |
| 20261107-02 | [外部レビューの整理と完成判定の訂正](TASK.review-intake-11-07-2026.md) | [AC-20261107-02](../acceptance/AC-20261107-02.md) |
| 20261107-01 | [モニター再設計と自動更新](TASK.monitor-redesign-11-07-2026.md) | [AC-20261107-01](../acceptance/AC-20261107-01.md) |
| 20261106-01 | [Workflow Cookbook Tier 3 導入](TASK.workflow-adoption-11-06-2026.md) | [AC-20261106-01](../acceptance/AC-20261106-01.md) |
| 20261106-02 | [RSI の方針と評価契約](TASK.rsi-policy-11-06-2026.md) | [AC-20261106-02](../acceptance/AC-20261106-02.md) |
| 20261106-03 | [RSI 文書の名称整理](TASK.rsi-naming-11-06-2026.md) | [AC-20261106-03](../acceptance/AC-20261106-03.md) |
| 20261106-05 | [最適化済み診断スキルの導入](TASK.security-audit-skill-11-06-2026.md) | [AC-20261106-05](../acceptance/AC-20261106-05.md) |
| 20261106-06 | [要件熟成から製品完成](TASK.product-completion-11-06-2026.md) | [AC-20261106-06](../acceptance/AC-20261106-06.md) |
