---
task_id: 20261106-03
intent_id: INT-AYM-RSI-NAMING-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
acceptance_id: AC-20261106-03
---

# RSI 文書の参照元名称を削除

## Objective

ユーザーの指定に従い、特定の参照元名を本文・参照パス・記録から削除し、一般的な RSI の説明にする。

## Scope

- In: 名称の削除・一般化、参照元記録の資料 ID 化、Task / Acceptance / CHANGELOG / Birdseye の同期。
- Out: RSI の9項目や評価条件の変更、参照元ファイルの変更、製品の実装・実験。

## Requirements

対象名称の残存0件、9項目の本文・原資料・参照資料 hash の保持、JSON とリンク・索引の整合を確認する。
絶対パスを架空のパスへ置換せず、資料 ID と相対名を使う。比較評価は行わないため manifest は適用外。

## Deliverables

[AC-20261106-03](../acceptance/AC-20261106-03.md)、[CHANGELOG 0003](../../CHANGELOG.md)。

## Tests

結果の正本は [技術検収](../acceptance/AC-20261106-03.md#evidence)。
