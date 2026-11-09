---
task_id: 20261109-04
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: planned
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# 異なるRSI候補の固定条件比較

## Objective

実差分のあるbaseline／candidateを、正当な計測と未見評価で比較し、採用・通常反映・復旧まで追跡する。

## Scope

[P2計画C](../requirements/P2-VALIDATION-PLAN.md#c-有効なrsi改善実験)を正本とする。計測不良を含む過去出典の隔離、trainでの候補作成、条件凍結、validation／holdout、独立採点、採否と適用を対象とする。閾値緩和と比較不能の成功扱いは禁止する。

## Acceptance Criteria

- 変更仮説、実差分、方策hashと実行証跡が一致する。
- source／runner／input／model／policy、split、seed、経験、予算、停止・採用条件を事前固定する。
- preflight成功後に比較し、実測artifactとpostrunを保存する。
- candidateの失敗と外部故障を区別し、除外・費用・正常回帰・安全性を併記する。
- 未pinモデル、未完了、未見集合不足をpromotion-readyにしない。
- 採用時はreview、apply、通常実行でのhash照合、rollbackを個別に確認する。

## Status

計画のみ完了。実モデル比較・昇格は未実行。モデルの利用枠・版固定と評価入力を確定し、必要な追加実装の承認後に開始する。
