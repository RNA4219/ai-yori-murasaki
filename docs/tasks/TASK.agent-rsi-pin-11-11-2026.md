---
task_id: 20261111-01
intent_id: INT-AYM-RSI-20261106
owner: RNA4219
status: reviewing
last_reviewed_at: 2026-11-11
next_review_due: 2026-12-11
acceptance_id: AC-20261111-01
---

# エージェントRSIのno-op評価とモデル固定検証の修正

## Objective

変化のない候補でholdoutを消費せず、自己申告された `pinned` の文字列だけで採用準備に進めない。

## Authorization

2026-11-11の添付不具合報告およびユーザーの「確認してマージまでやって」という指示。認可ラボ以外の外部実行はしない。

## Scope

- 同一bundle digestをcompareの実行前に拒否する。
- freeze時に `model_pinning.expected_models` （Ollamaモデル名とSHA-256 digest）を固定し、train、validation、holdoutの各runで検証する。
- Ollamaの `/api/tags` を実行前後に取得し、実LLM応答のモデル名とdigestが一致するか観測する。OpenAI互換API、cloud alias、証跡欠落は探索扱いとする。
- fixtureの回帰テストを追加する。実モデルの性能改善は主張しない。

## Acceptance Criteria

- no-op候補のcompareでexecutorが呼ばれず、holdoutアクセス監査も発生しない。
- `pinned` 自己申告だけでは `promotion-ready` にならない。
- freezeしたdigestと全runの観測が一致した場合に限り、既存の採用閾値による評価に進む。
- digest不一致、cloud、モデル証跡の欠落、train中のモデル変更を拒否する。

## Verification

回帰テストは `tests/test_agent_rsi.py` に追加。GitHub連携経由の編集環境にはテスト実行用のチェックアウトがないため、ローカル実行は未実施。技術検収は [AC-20261111-01](../acceptance/AC-20261111-01.md) で保留。

## Boundaries

Ollama digestの前後一致はローカルサーバーの返答を信頼する運用上の検証であり、暗号学的な実行時重みの証明ではない。実モデルでの採用成績は未検証。
