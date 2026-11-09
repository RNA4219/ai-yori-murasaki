---
task_id: 20261109-02
intent_id: INT-AYM-RSI-20261106
owner: RNA4219
status: planned
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# P1 — 承認済みRSI方策を通常実行に適用・監査・復旧

## Metadata
- priority: P1
- repo: RNA4219/ai-yori-murasaki
- base_branch: main
- work_branch: fix/blue-evidence-rsi-apply-20261109
- inspected_base_sha: `8dd42e00f431289a9379e37d9a9ffb9a56f5c59e`
- 依存: P0 `20261109-01` の検収approvedを先に確認。P0未完了時は調査・設計のみ可。
- 役割: Codex=司令塔、GLM-5.3=作業分解・レビュー・検証結果整理、DeepSeek-v4.1-Flash=コードとテストの実装。例外は既存承認規約による。

## Objective
`aym rsi agent-apply`の承認済みbundleが次回の`aym lab demo`と`lab iterate`で実際に使われ、識別子・hash・選択理由を監査可能にし、異常時はfail-closed、明示的にロールバックできる。

## Scope
- In: `ai_yori_murasaki/rsi_agent.py`, `cli.py`, 必要最小限の`cli_rsi.py`, `lab.py`, `agents/policies.py`, registry/CAS関連の契約と回帰テスト。
- Out: RSI採用閾値の緩和、model pinningの迂回、holdoutの再利用、未知の成功主張、production適用。

## 現行HEADの静的確認（実行証跡ではない）
- `AgentRSI.apply` は承認review等を確認し`workspace/agent_rsi/registry.json`の`current_bundle_ref`を変更する。
- CLI `_resolve_bundle` は明示fileか`load_default_bundle()`のみ。`lab demo/iterate`はregistryを参照せず直接このbundleを渡す。
- `--rollback` は現状、空でない説明文字列として履歴へ保存する。以前のbundleへの切替API/CLIはない。
- 既存`test_apply_state_machine`はregistry存在・apply状態を確認するが、通常lab実行が採用bundleを使用したことの確認ではない。
- `initialize_registry`は現在のregistryを無条件に新規値で上書き可能。applyの前提currentとbaselineの不一致、並行更新とreplayも契約化する。

## Requirements / Contract
1. 先に回帰テストで現行の不一致を再現する。fake LLM/labで使われたrole_cfg/prompt/budgetを採集し、承認済み適用後にもdefaultが選ばれることを before として保存する。
2. 選択規則を**明示指定された有効bundle > 承認・適用済みで検証可能なregistry bundle > パッケージdefault**とする。選択処理は単一関数にまとめ、`demo`と`iterate`で共通利用。未初期化のみdefaultを許可し、registryが存在するが不正・未承認・破損・CAS不一致の場合はdefaultへ無言フォールバックしない。
3. registryの参照は64桁digest、CAS実体digest、`validate_bundle`、適用済みcandidateの承認review・readyと出所、必要なfreeze/runner identityを照合する。明示指定fileもschema、許容パス・権限境界を検証する。期限・pinning・採用ゲートの既存規則を緩めない。
4. applyはexpected current refの照合と原子的更新を行い、不正状態・stale更新を拒否。`initialize_registry`は既存の採用状態を黙って初期化しない。採用時の有効化と元へ戻す操作を明確に分ける。
5. 通常runの証跡に`bundle_id`, `bundle_digest`, `bundle_ref`（該当時）, `source=explicit|approved_registry|packaged_default`, `selection_reason`を記録し、runで実際に使う同一bundleから算出する。外部pathや秘密情報は公開投影しない。イベント・CASと実行設定hashの整合を検証。
6. 履歴上の実体が確認された復旧先だけを明示的rollbackで選択できる操作を追加。適用・rollbackの双方を監査記録し、再実行しても同じ方策を再現できる。破損時は復旧不能と区別して停止する。
7. baseline/candidateの評価条件を固定したRSI比較は別操作として保持。評価目的以外で既存holdout、threshold、Experience Memoryの境界を変更しない。

## Regression matrix (DeepSeek担当)
- registry未初期化→default
- 採用ready・approved・適用→次回demo/iterateの実利用bundleがcandidate
- 明示した正当bundleが優先し、選択ソースも記録される
- 未承認・ready=false・破損CAS・digest mismatch・不正path・異常registry・不明ref→拒否
- 記録bundle id/ref/hashと実際のagent role_cfg/promptが一致
- current refのstale競合、二重apply、二重initializeは冪等か明示拒否
- rollback→previous有効bundleへ復元、実行・証跡照合、replay
- 権限境界、既存RSI holdout/pinning、従来の明示bundle指定

## Local commands / Evidence
1. `python -m pytest -q tests/test_agent_rsi.py tests/test_agent_lab_demo.py` の既存基準、追加テストのbefore失敗を実測保存。
2. 修正後に対象テスト、`python -m pytest -q -rs`、実行設定と証跡hash照合を実行する。
3. 可能な場合のみ認可済みDocker/ローカルLLMで apply→通常run→rollback→通常run を検証。環境がなければ未実行とし、単体検証を実機成功に読み替えない。
4. 性能比較を新規実施するときはEvaluation Identityのpreflight/postrunを通し、train/validation/holdoutを固定。外部故障のpairをgainとして扱わない。

## Acceptance Criteria
- [ ] P0先行検収を確認し、before実測を保存。
- [ ] 採用bundleが次の通常runで使用され、実行証跡が実物と一致。
- [ ] 無効・未承認・破損の自動適用を拒否し、原因と停止理由を追跡可能。
- [ ] rollbackが実動し、replayのbundle同一性を確認。
- [ ] 既存安全・予算・holdout・採用ゲートの非劣化を検証。
- [ ] GLMレビュー、Acceptance、CHANGELOG、Birdseye同期を完了してからdone。

## Deliverables / Notes
- [AC-20261109-02](../acceptance/AC-20261109-02.md)（現時点draft）
- P2（SQLi追加実LLM修復／意味保持WazuhイベントBlue評価／RSI有意改善）は**P0・P1完了後のみ**設計・Task化する。現時点では性能成功主張と追加実装をしない。
