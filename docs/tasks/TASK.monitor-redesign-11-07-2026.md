---
task_id: 20261107-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
acceptance_id: AC-20261107-01
---

# モニターの再設計と自動更新の改善

## Objective

ユーザーが指摘した画面デザインと、自動更新のたびに本文が読み込み表示へ戻る挙動を改善する。

## Scope

- In: 実製品の画面事例の検索、GLMによる設計・レビュー、DeepSeekによるHTML/CSS/描画更新の実装、ブラウザー検証、配布物の更新。
- Out: バックエンド機能、評価指標、承認・反映操作の追加。

## Acceptance Criteria

- 実例を参照して情報の優先順位とレイアウトを作り直す。
- 既存の指標・未測定・保留・証跡を確認できる。
- 自動更新中も本文を残し、変化のないDOM、スクロール、フォーカス、詳細の開閉を維持する。
- 通信失敗と古い表示を明示し、画面遷移後に別の対象のデータを表示しない。
- デスクトップとモバイル、キーボード操作、配布HTMLを検証する。

## Evidence

作業記録は `.ga/goal-20261106/visual-*`、`silent-refresh-*`、`monitor-*-deepseek`、`monitor-*-glm`。実結果は [AC-20261107-01](../acceptance/AC-20261107-01.md)と[証跡](../evidence/monitor-redesign-20261107.json)へ記録した。[変更履歴0006](../../CHANGELOG.md)へ接続済み。
