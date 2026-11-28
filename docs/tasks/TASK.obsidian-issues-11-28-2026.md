---
task_id: 20261128-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: in_progress
last_reviewed_at: 2026-11-28
next_review_due: 2026-12-28
---

# Obsidian ノートのチェックボックスを案件の課題として取り込む

R-092 では、操作者が vault 内の Markdown ノートを1つ選び、チェックボックスごとの題名・状態・出典を指定した案件と実行に登録する。Notion CSV の課題と一緒に判断画面と照合レポートに表示し、出典から元のノートを開けるようにする。[操作手順](../SOURCES.md#obsidian-tasks)・[CLI と API の契約](../requirements/API-CONTRACTS.md)

T64 では、架空の vault を使い、同じ入力の再送、異なる内容の衝突、Blue の公開範囲、Notion CSV と参照資料との併用を確認する。元のノートは読み取り専用とする。[技術検収](../acceptance/AC-20261128-01.md)・[変更履歴](../../CHANGELOG.md)
