---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# 品質修正の実行記録

## 入力と担当

開始HEADは `82001b5f5c1b8384dd070e19f028ddd2afe6d2c3`。ユーザーのP0／P1修正とP2計画の依頼を対象とする。GLMとDeepSeekは月間利用上限で呼び出しが拒否され、今回に限るCodexの代行がユーザーから承認された。既存の未追跡スクリプト4本は変更していない。

## 実測

| 工程 | 実コマンド／入力 | 結果 |
| --- | --- | --- |
| P0修正前 | `py -3 -m pytest tests/test_blue_evidence_identity.py -q --tb=short` | [4 failed / 4.37秒](p0-before.txt)。2事象のうち1件が未被覆 |
| P0回帰 | `py -3 -m pytest tests/test_blue_evidence_identity.py tests/test_agent_core.py tests/test_agent_lab_demo.py tests/test_v1_metrics.py -q --tb=short` | [65 passed / 23.95秒](p0-after.txt)、終了0 |
| P1修正前 | `py -3 -m pytest tests/test_agent_rsi.py::ApplyTest::test_normal_cli_uses_applied_bundle -q --tb=short` | [1 failed / 2.22秒](p1-before.txt)。適用bundleと通常CLIのbundleが不一致 |
| P1 CLI統合 | `py -3 -m pytest tests/test_agent_rsi.py::ApplyTest::test_cli_lifecycle_with_real_policy_resolution_and_audit -q -s --tb=short` | [1 passed / 14.68秒](p1-integration.txt)、終了0。11 CLI操作、適用／復旧のhash、全chain／checkpoint照合 |
| 最終全体回帰 | `py -3 -m pytest -q -rs --tb=short --durations=8` | [637 passed / 3 skipped / 22 subtests passed / 124.39秒](pytest-all.txt)、終了0 |

P1統合で使ったcandidateのhashは `3d5de9678477dbfcbafb57eb23ebd1d5006f1c86b13288faab83442decd63ee6`、復旧したbaselineは `9471add0fef18bf1018a9b863d09a7cfd690ce56301524e290d98a1a86cd2b8e`。ID、選択元、理由、CLIごとの終了状態はログ内の `CLI_LIFECYCLE` に保存した。比較の成功／失敗は試験用executorが与えた固定fixtureであり、モデル性能の測定値ではない。

3 skippedはいずれもWindows上でsymlinkを作れないため。新規1件、既存2件である。実LLM／実Dockerの閉ループ、実WazuhのBlue評価、実候補の昇格は未実行。外部クラウドLLMなしで再現・回帰・状態機械の統合を実行した。

## 途中の失敗

- [P0テスト準備](p0-test-setup-error.txt): 先行Redのseal不足。試験準備を修正してから製品不具合を再現した。
- [P0最初の回帰](p0-first-regression.txt): 3 failed / 62 passed。CASのobservation envelopeとdataの照合を修正した。
- [P1最初の回帰](p1-first-regression.txt): 2 failed / 25 passed。方策情報の追加先がRemediation schemaへ紛れたため、run結果へ移した。
- [P1次の回帰](p1-second-regression.txt): 1 failed / 96 passed / 1 skipped。統合試験のtest module importを修正した。
- [CLI引数](p1-integration-cli-args-error.txt): 比較コマンドへ未対応のfreeze引数を渡した試験を修正した。
- [CLI準備checkpoint](p1-integration-setup-checkpoint-error.txt): 試験準備で作成したcampaign監査runのcheckpointをsealしてから、全chain／checkpointを検証した。

途中の失敗を成功件数へ加算しない。最終回帰で同じ機能と既存の正常系を確認した。

最終差分確認で適用履歴の連続性とKPIの参照欠落時のHOLDを追加確認したため、全体回帰を再実行した。追加前の [635 passed / 128.21秒](pytest-all-before-final-guards.txt) も保持する。最終実装の結果は637 passedである。

文書のTier／Acceptance／Birdseye検査は各終了0。[日本語確認記録](yomiyasu-summary.json)には15文書のlint／diffまたは資料照合と最終稿hash照合の実行結果を残す。既存PRODUCT-ACCEPTANCEのCapsule不一致は原文を読んで要約・参照を修正した。未確認の文書へreview hashを一括付与していない。

## 所要時間

前回の全体回帰は607 passed / 2 skipped、79.79秒。今回は637 passed / 3 skipped、124.39秒で、比較範囲が同一ではない。新規CLI E2Eが13.34秒、実行方策と復旧のE2Eが9.51秒を占める。いずれも実Runtime／CAS／台帳／checkpointを使い、モデル通信はしない。追加試験を含むため、全体時間の増加だけから製品コードの非効率とは判断していない。環境負荷の同条件記録はなく、残差の要因は未特定。必要な検証が成功したため、性能調査や無関係な最適化は追加しない。

## 再現

開始HEADを別のcheckoutへ展開し、今回の `tests/test_blue_evidence_identity.py` と `tests/test_agent_rsi.py` をコピーする。P0は `test_distinct_events_survive_deduplication` の4ケース、P1は `ApplyTest::test_normal_cli_uses_applied_bundle` だけを選んで実行すると、修正前の不整合を確認できる。全試験を旧版へ一括実行すると、新規APIに関する未実装の失敗も混ざるため、修正前の再現には使わない。製品コードとテストの最終hashは [verification.json](verification.json) に記録する。

成果は [検収](../../acceptance/AC-20261109-01.md)、修正方針と棄却案は [契約](../../requirements/QUALITY-INTEGRATION.md)、後続は [P2計画](../../requirements/P2-VALIDATION-PLAN.md) を参照する。
