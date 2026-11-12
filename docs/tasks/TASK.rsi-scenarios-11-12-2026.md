---
task_id: 20261112-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewing
last_reviewed_at: 2026-11-12
next_review_due: 2026-12-12
acceptance_id: AC-20261112-01
---

# Agent RSI シナリオ起動・入口情報・失敗時LLM呼び出し数の是正

## Objective

2026-11-12の外部再現報告（commit `538e228`）の3件を修正し、RSI評価に必要な起動条件と費用計測の整合を戻す。

## Scope

- In: `path-traversal-read` の読み取り専用起動、4問の `surface`、`run_demo(failure_mode="record")` の失敗時の各role呼び出し数、回帰テスト。
- Out: モデルそのものの成功率改善、SQLi/XSS/Path/CVEの修復Oracle一般化、評価用holdoutの新規作成、別環境での実測。
- ZIP中の報告・再現スクリプトはローカル受領資料とし、リポジトリには収録しない。

## Requirements

1. 対象コンテナのread-only bind mountを緩和せず、正常ファイルの取得を継続する。
2. Redの課題文に対象URLと正常応答例を載せる。悪用の正解文字列は渡さない。
3. Red、Blue、Purpleで途中終了しても、完了済みroleと失敗したroleの `llm_calls` を保持し、RSI outcomeへ反映する。
4. 旧結果を再採点したり、有利な候補だけ選別したりしない。surface変更後の試験は新しい評価identityとfreezeで実施する。

## Verification

- `python3 -m pytest tests/test_rsi_scenario_regressions.py tests/test_agent_lab_demo.py tests/test_agent_scenarios.py tests/test_agent_rsi.py -q`
- `python3 -m pytest tests -q`
- `sh repro_1b_target_readonly_docker.sh <checkout>` と `python3 repro_1_target_readonly.py <checkout>` をDockerのある環境で実施。旧再現スクリプトは**終了コード0が不具合再現**なので、修正確認では期待する終了コードが反転する。
- LLM実実行を伴うRSI比較は別の新しいfreezeで実施。

このGitHub連携作業環境ではリポジトリ全体をPythonへ配置できずDockerも無いため、上記は未実行。機能とテストの実装、実行検収を区別する。

## Deliverables

- [AC-20261112-01](../acceptance/AC-20261112-01.md)
- [CHANGELOG](../../CHANGELOG.md) 0022
