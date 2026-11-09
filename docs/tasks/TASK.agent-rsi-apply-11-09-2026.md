---
task_id: 20261109-02
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: blocked
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
acceptance_id: AC-20261109-02
---

# P1: 承認済みAgent RSI bundleの通常実行反映とロールバック

## Objective

agent-applyで採用したbundleが、次の`aym lab demo`/iterateで検証可能に適用され、そのbundleを実際に使った証拠、承認・復旧の出所、rollbackを追跡できるようにする。

## Prerequisite

[P0 Task 20261109-01](TASK.blue-evidence-oracle-11-09-2026.md) の検収承認が必要。現在は**blocked / 未着手**。

## Root-cause hypothesis (HEAD 8dd42e0)

- `rsi_agent.AgentRSI.apply` は `workspace/agent_rsi/registry.json` の `current_bundle_ref` を書き換える。
- `cli._resolve_bundle` は明示ファイルがなければ常に同梱デフォルトを返し、registryを参照しない。
- CLIに `agent-initialize` や `agent-rollback` がなく、`agent-apply --rollback` は説明文字列を保存するだけ。
- `apply` は expected baseline/candidate digest を候補記録と比較するが、現行registryのcurrent refを期待baselineと比較していない。
- いずれも静的確認。通常実行での採用反映/不反映の実証は未実行。[調査証跡](../evidence/quality-control-20261109.md)を参照。

## Responsibilities

Codex: 完了順序・停止・承認境界。GLM-5.3: 設計比較・作業分解・レビュー。DeepSeek-v4.1-Flash: 製品コード/テストの実装。モデルが利用可能になるまで実装を代行せず、必要な判断を記録する。

## Requirements

1. 現行HEADで`agent-apply→lab demo`の実際のbundle識別子差異を再現し、先に回帰テストを作る。
2. 選択順を **明示指定（許可・検証済み） > 承認済み現在bundle（許可・検証済み） > デフォルト** とし、評価実行で明示されたbaseline/candidateを汚染しない。破損registryや不正参照は黙ってdefaultに落とさずfail-closed/HOLD。
3. AgentRSI用のregistry初期化・apply・rollbackを明示的なCLI操作として設計。競合更新の防止（expected current ref）、CAS実体hash、`validate_bundle`、承認/review/ready/pinning/holdout条件との照合、履歴整合を必須とする。任意のパス・symlink・権限越境を拒否。
4. 本番適用ではなく認可済み隔離ラボの方策選択のみを扱う。既存のRed/Blue/Purple権限・予算・停止条件を緩めない。
5. 実行時に`bundle_id`、content hash/ref、source（explicit/approved/default）、selection reason、registry revision/適用参照を証拠付き記録。保存記録と実際にAgentLoopに渡したオブジェクトのhashを照合する。
6. 適用・検証・失敗・rollbackを再実行で追えるようにし、失敗をapplied成功として記録しない。過去のimmutableなrun証跡を後書きしない。

## Required regression matrix

- 未適用ではdefault。
- 承認済みcandidateをapplyした次回通常実行でcandidate。
- 明示した正当なbundleが優先。評価executorの明示bundleがregistryに汚染されない。
- 未承認、未ready、未pin、破損CAS、hash不一致、不正参照、パストラバーサル、registry競合を拒否。
- rollback後の通常実行で以前のbundle。
- 実際に渡したbundleと実行証跡の識別子/hash一致、chain/checkpoint整合。
- 同じ変更を二重適用しない。失敗時は成功・不採用を取り違えない。

## Evaluation Identity

機能回帰と性能比較は別。採用可能性は凍結済み評価manifest・preflight/postrun・train/validation/holdout分離の下でのみ判定する。未pin cloud aliasを新規promotion-readyとして扱わない。性能改善値の成功主張を禁止する。

## Local Commands

`py -3 -m pytest -q tests/test_agent_rsi.py tests/test_agent_lab_demo.py tests/test_agent_core.py`
`py -3 -m pytest -q -rs`
認可済みローカルfixtureで `freeze→train→compare→ready→review→apply→demo→証跡照合→rollback→demo` を必要な条件が成立したときだけ検証する。

## Deliverables

diff、CLI/運用契約とRUNBOOK、前後の検証記録、[AC-20261109-02](../acceptance/AC-20261109-02.md)、CHANGELOG、Birdseye同期。P2の実装は含めない。

## Tests / Commands / Notes

- 現時点: **未実行・blocked**。ローカル実行環境および指定実装モデルを利用できず、P0の検収も未了。
- P2は本Taskの完了後、別Taskで設計・優先順位付けする。
