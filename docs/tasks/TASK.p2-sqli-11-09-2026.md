---
task_id: 20261109-02
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: planned
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# SQLiの実LLM修復検証

## Objective

認可不備以外の最初の1類型について、検知・原因分析・生成修正・適用・再攻撃・正常回帰・独立判定を実証する。

## Scope

[P2計画A](../requirements/P2-VALIDATION-PLAN.md#a-sqliの隔離ラボ修復)を正本とする。隔離SQLiラボ、必要なbrokerとOracleの契約、決定的試験、実モデル試験を対象とする。外部攻撃と汎用能力の保証は対象外。

## Acceptance Criteria

- 脆弱・正常・過剰修復・故障の対照でOracleを検査する。
- 実LLMの観測・根拠・原因分析・生成差分を保存する。
- 同じ攻撃の再実行と正常入力の回帰を独立判定する。
- fixtureのID、split、model、予算、成功・失敗・未実行を区別する。
- 実験前にevaluation identityを固定しpreflight、実測後にpostrunを行う。

## Status

計画のみ完了。製品実装・実験は未着手。追加実装の承認と実行環境の確認後に開始する。
