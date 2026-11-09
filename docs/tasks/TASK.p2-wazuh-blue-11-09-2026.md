---
task_id: 20261109-03
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: planned
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# 実WazuhイベントのBlue評価

## Objective

認可ローカル環境の実イベントを用い、検知、誤検知、証拠参照、調査コストを測定する。

## Scope

[P2計画B](../requirements/P2-VALIDATION-PLAN.md#b-実wazuhイベントのblue評価)を正本とする。意味を保持した専用Blue入力、独立ラベル、参照権限、分割・計測を対象とする。擬似HTTP行への変換と未承認の外部送信は行わない。Indexer接続は必要性を別途判断する。

## Acceptance Criteria

- 原観測CASと正規化eventの対応、重複と別事象の識別を確認する。
- モデルから独立した正解とラベル不明の扱いを固定する。
- recall、precision、誤検知、参照正確性、調査コスト、未完了・除外を併記する。
- 同一実事象がtrain／validation／holdoutをまたがない。
- 秘密除去・送信許可・予算・評価identityを実行前に確定する。

## Status

計画のみ完了。専用入力の製品実装・実Blue評価は未着手。追加実装の承認とデータの利用条件確認後に開始する。
