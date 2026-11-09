---
task_id: 20261109-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
acceptance_id: AC-20261109-01
---

# Blue評価とRSI適用の整合性修正

## Objective

正しく提出されたBlueの証拠を保持し、Oracleの評価単位を揃える。承認済みRSI方策を通常実行へ反映し、使用方策の追跡と復旧を可能にする。検知能力やRSI性能の向上は、この実装確認から主張しない。

## Authorization

2026-11-09の品質修正依頼による。GLMとDeepSeekは月間利用上限で呼び出しを拒否されたため、ユーザーは今回に限るCodexの調査・実装・レビュー・検証の代行を明示的に承認した。開始HEADは `82001b5f5c1b8384dd070e19f028ddd2afe6d2c3`。既存の未追跡スクリプト4本は変更しない。

## Scope

- P0: findingの事象識別、非LLM Oracle、検知数・見逃し数・誤検知数、証拠権限の回帰。
- P1: registryと通常lab実行の接続、適用条件、方策の識別証跡、ロールバック。
- P2: SQLi修復、実Wazuhイベント評価、異なるRSI候補の比較について、必要性評価・設計・後続Task化まで。
- 対象外: 追加類型の製品実装、外部ターゲット、本番変更、公開・push、採用基準の緩和。

## Plan

P0の最小再現を保存 → P0修正・必要な回帰・検収 → P1の現状再現 → P1修正・統合検証 → P2設計 → 文書と索引の同期・検収。

## Acceptance Criteria

- 同一path・role・categoryの異なる証拠が保持され、同一証拠の再送は冪等になる。
- 正常／不正アクセスの混在、時刻・提出順、欠落・不正・他エージェントの証拠参照を検査する。
- 明示指定、承認済み適用bundle、既定方策の優先順位を安全条件とともに定める。
- 未承認・破損・不正参照は自動適用せず、失敗を記録して停止する。
- 候補作成・比較・承認・適用・通常実行・証跡照合・ロールバックをクラウドLLMなしで検証する。
- 修正前後の実測、代替案、安全境界、未実行、残件をAcceptanceへ結ぶ。

## Evaluation Identity

checker・状態機械・coordinatorの決定的fixture試験は契約上の適用外。実モデルの性能比較や実候補の昇格は行わず、単体試験を性能改善の実証にしない。実比較を追加する場合はpreflight/postrunを先行する。

## Evidence

開始時のモデル利用可否と文書hashは、リポジトリ外の `_runtime/aym-quality-20261109/preflight.json` に保存した。実装・検証記録は [AC-20261109-01](../acceptance/AC-20261109-01.md) に集約する。

## Result

後続のmain反映は、ユーザーの「問題ないならマージまでやって」に基づく [RA-20261109-01](../releases/RA-20261109-01.md) に従う。先行するREADMEとv0.1.1の更新を保持して統合し、統合後の検証とPRのmerge結果を別に記録する。

P0修正前4 failed、P1修正前1 failedを保存した。P0の65件の回帰と、P1の11 CLI操作による統合を確認し、最終全体回帰は637 passed / 3 skipped / 22 subtests passed（124.39秒）で終了0だった。[実装差分](../evidence/quality-integration-20261109/implementation.patch)と[実行記録](../evidence/quality-integration-20261109/README.md)を保持する。

P2は [計画](../requirements/P2-VALIDATION-PLAN.md)とTask 20261109-02／03／04を作成した。追加実装は未着手。実モデルの能力・RSI改善は新たに主張していない。文書の3ゲート、日本語本文の検査、索引の内容照合を検収記録へ確定して完了した。
