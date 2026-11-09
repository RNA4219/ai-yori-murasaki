---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: investigation
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# 外部レビュー仮説の独立静的調査（2026-11-09）

- 対象: GitHub `RNA4219/ai-yori-murasaki` main の `8dd42e00f431289a9379e37d9a9ffb9a56f5c59e`。
- 調査方法: GitHub経由の原文取得と条件抽出による小規模ロジック再現。製品packageのcheckout・pytest実行・Docker・Ollamaは未実施。GLM/DeepSeekによる検証も未実施。
- 既存の2026-11-08検収の実測は過去の記録として保全し、今回の修正前後の実測値に流用しない。

## P0 observation

1. `ai_yori_murasaki/agents/tools.py:351-359`: dedup_keyおよびfinding IDはpath/role/categoryに依存し、evidence_refを含まない。
2. `tools.py:383-386`: 保存される各Findingのreferencesは最初の単一refのみ。
3. `ai_yori_murasaki/oracle.py:135-200`: 非adminのGET /record HTTP 200行ごとに該当refがいずれかのFinding.referencesにあることを要求する。
4. `ai_yori_murasaki/lab_metrics.py` の `_finding_covers` も参照ベースでTPを判定する。
5. `tests/test_agent_core.py` の既存batchテストは `user` 行と `guest` 行を区別するが、同じroleで別refの試験ではない。

### Isolated logical reproduction (NOT repository pytest)

```json
{
  "case": "2 unauthorized GET /record rows, same role guest, different refs/timestamps",
  "blue_input_count": 2,
  "broker_saved_count": 1,
  "broker_duplicates": 1,
  "oracle_rows_requiring_detection": 2,
  "oracle_uncovered": 1,
  "oracle_pass": false
}
```

上記は当該コードのdedup条件とOracleの照合条件を抜き出したミニマルなPythonロジックで得た結果。CAS、Runtime、ToolBroker、Oracleを含む実パッケージの統合テスト結果ではなく、現在のHEADで本当に再現するかは**未検証**。修正前のpytest実測が得られるまで「確定バグ」と断定しない。

## P1 observation

1. `ai_yori_murasaki/rsi_agent.py:219-233,807-865`: agent registry pathはworkspace配下で、applyはcurrent_bundle_refを更新。レビュー・比較済み・readyの条件は確認する。
2. `ai_yori_murasaki/cli.py:201-211,339-359`: `_resolve_bundle` は明示ファイルが無いとpackaged defaultを返す。registryを読まない。
3. `ai_yori_murasaki/cli_rsi.py:125-155,522-541`: agent-applyはあるがagent-initialize/agent-rollbackのCLI操作はない。通常の`rsi initialize`は別の決定的RSIに作用する。
4. `rsi_agent.py:838-865`: applyはcandidateのbaseline digestを照合するが、registry.current_bundle_refと期待baseとの一致を照合しない。rollback引数は説明としてhistoryへ保存されるが、復元コマンドではない。
5. `tests/test_agent_rsi.py`: state machine testはPython APIからinitialize_registry→applyするが、その後の`lab demo`、rollback、policy provenanceを確認していない。

## Pending verification

- P0/P1いずれも、修正前の実リポジトリに対するfailするpytest・修正差分・修正後回帰結果がない。
- 外部LLMを使わないfixtureベースのテストを優先する。実LLM/Dockerは利用可能かつ承認された場合のみ。
- チェック対象: 役割別ref許可、Oracle非漏洩、Evidence CAS/chain、KPI、RSI preflight/postrun、通常実行bundle provenance。
- P2（SQLi等実LLM修復・Wazuh live Blue・有効なRSI改善比較）はP0/P1の技術承認後にTask化。新規改善成功主張をしない。
