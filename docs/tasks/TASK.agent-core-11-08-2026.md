---
task_id: 20261108-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-08
next_review_due: 2026-12-08
acceptance_id: AC-20261108-01
---

# エージェント実行基盤・エージェントRSIの中核機能実装

## Objective

原要求（エージェント実行基盤、Blueの検知・調査・修復、Purpleの反復評価、非LLM Oracle）に対する実装の不足を是正する。LLMを説明生成に限定しない、観測から行動を決めbrokerの許可執行を経て結果を再入力するエージェントの実動作と、その方針bundleを改善対象とするRSIを、CLIの通常実行経路に接続する。

## Authorization

2026-11-07の引き継ぎ依頼による。新規追加要件ではなく、原要求と実装の食い違いの是正。既存資産（Campaign/Run、Docker隔離ラボ、Evidence/CAS/checkpoint、非LLM Oracle、broker、adapter契約、HTMLモニター）は再利用し、固定文字列置換による修正は回帰試験用途に留める。

## Scope

- In: エージェントループ（AgentLoop・ToolBroker・policy bundle）、lab.py通常経路のエージェント化、シナリオfixture（train/validation/holdout分離）、blind検知照合、エージェントRSI（freeze/train/compare/ready/review/apply）、モニターのagent表示、帰属是正。
- Out: Redの新規汎用攻撃エンジン、完全自律SOC、本番接続、5類型のうち4類型へのエージェント拡張（追加シナリオfixtureとして将来課題）、公開物の更新。

## Plan

エージェント基盤の設計 → 実装 → 偽LLMによる試験 → 実機反復（失敗の原因分析と修正） → RSIの実機実行 → モニター確認 → 文書是正 → 検収。

## Responsibilities

監督はCodex。設計・レビューは`glm-5.3:cloud`。実装の基本分担は`deepseek-v4.1-flash:cloud`だが、同サブエージェントが3回連続で実产出なしに終了し1回は並行書き込みで競合したため、中核実装の大部分を監督が直接行い、DeepSeekには権限一致化の限定修正を委任した。経緯は[AC-20261108-01](../acceptance/AC-20261108-01.md)に記録する。

## Deliverables

`ai_yori_murasaki/agents/`（ループ・broker・bundle・シナリオ）、`rsi_agent.py`、lab.pyのagent駆動通常経路、workerの`probe_path`/`unit_authz`モード、`blind_detection_check`、CLIの`aym lab demo --scenario`と`aym rsi agent-*`、モニターのagent活動・RSI候補表示、[AC-20261108-01](../acceptance/AC-20261108-01.md)。

## Tests

偽LLM・偽ラボによる単体・統合試験（593件緑）、実機でのRed/Blue/Purple実動作、失敗時fail-closed、Oracleの独立再検証、RSIの事前固定・比較・不採用判断、モニターAPI投影の一致。
