---
task_id: 20261116-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewing
last_reviewed_at: 2026-11-16
next_review_due: 2026-12-16
---

# 判断の画面（独立プレビュー）

## メタデータ

- priority: P2
- repo: RNA4219/ai-yori-murasaki
- base_branch: main
- work_branch: feature/decision-view-20261116

## Objective

既存のオペレーターモニターとは別に、結果を読む人が対応優先順位・進捗・前回差分を判断できる画面を追加する。

## Scope

- In: 添付の `aym-decision-view-20261116b.zip` を `decision_view/` に独立導入し、URLと期限の検証回帰、入口文書・Task/Acceptanceを整備する。
- Out: `ai_yori_murasaki/server.py`、既存モニター、`aym` CLI、新規APIルート、本番配信、既存スキーマ変更、実LLM/RSI評価。

## Requirements

- Behavior: Campaignから書き出したread-only JSONを独立HTMLに読み込み、未完了・期限・KEV/EPSS・前回差分を表示する。
- I/O Contract: `aym.decision-view/1`。参照用JSONの生成に既存のRuntime/Storeの読み取り口を使う。
- Constraints: 元データの公開境界と認証スコープを変更しない。出典リンクの資格情報付きURLを拒否。期限は実在する日付のみ扱う。画面からの書き戻しはしない。
- Acceptance Criteria: 付属テスト成功、JSONスキーマ・見本を維持、既存モニターとAPIに非干渉。全体回帰とブラウザー検証後に技術検収。

### Evaluation Identity

性能比較やRSI採用を扱わず、適用外。

### RSI

対象外。評価性能・自動採用への影響を主張しない。

## Affected Paths

- `decision_view/**`（10ファイル）
- `README.md`、`HUB.codex.md`、`CHANGELOG.md`、索引、`codemap.config.json`
- Task/Acceptance本書

## Local Commands

```sh
python -m pytest -q decision_view/tests/test_aym_decision_view.py
node --test decision_view/tests/decision-core.test.mjs
```

## Deliverables

独立判断ビュー、テスト、使用方法、[AC-20261116-01](../acceptance/AC-20261116-01.md)、GitHub draft PR。

## Plan

添付ZIP検査→独立配置→単体試験→レビュー→別途統合と技術検収。

## Patch

既存ランタイムを変えないadditive変更。Pythonの外部URL検証・日付検証とその回帰を追加。

## Tests

手元の独立ディレクトリで Python 13 passed、Node 13 passed。リポジトリ全体pytest、実Campaign、ブラウザ表示/CSPの再試験、Birdseye checkerは未実行。

## Commands

[AC-20261116-01](../acceptance/AC-20261116-01.md)参照。

## Notes

配布元資料には旧コミットでのブラウザ検証実績があるが今回の統合環境の検証実績と混同しない。先行する未承認の画面分類・ID継承規則と、`?campaign=` APIの契約は要レビュー。GLM/DeepSeekの指定モデル実行は本作業環境から未実施。
