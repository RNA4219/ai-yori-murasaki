---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
---

# 残件作業表（2026-11-07）

2026-11-07時点の実測で、5類型benchmark本体、分類・修復・再検証の接続、CVSS4・業務影響、KEV/EPSS、HTML表示は実装済み。15runの正式採点は全splitで合格し、評価identityを凍結した。M-1は非欠陥、H-1は修正済み、sdist/wheel・ライセンス同梱・秘密情報検査も実施済み。本表は未完了の管理用であり、完了の証跡ではない。

| # | 項目 | 現状 | 次の担当 |
| --- | --- | --- | --- |
| 1 | 実ラボ画面の最終確認（現在版の実データでのUI確認） | 完了。実campaign cff103f23a71b4347でB-01〜B-04全pass（`finish-b-evidence.md`） | 完了 |
| 2 | 既存評価のholdout順序の来歴追補 | 完了。旧holdoutは診断扱いで保持、未使用seed 20261115のholdout3run追補で全pass（`finish-a-evidence.md`） | 完了 |
| 3 | 配布物と最終コード・docsの一致確認 | 完了。旧dist-outのREADME陳腐化を検出し`dist-finished-20261107`へ再ビルド、新配布物でコード/assets・収録文書/metadata比較とも不一致0件、秘密情報検査0件（`finish-c-source-compare-finished-result.json`、`finish-c-package-compare-finished-result.json`） | 完了 |
| 4 | 最終同期（文書確定後のBirdseye再同期・review hash・ゲート） | 完了。本バッチで実施 | 完了 |

残件はすべて閉じた。RSI候補の実行器への反映（applied=falseのまま）と公開承認は本表の範囲外。

旧項目1〜5の実装・採点の証跡は、formal-acceptance-final-report.json、formal-cli-api-bridge/bridge-evidence.json、release-final-summary.md、final-review-m1-resolution.md（いずれも`.ga/goal-20261106/`内）を参照。manual-bbのcollector失敗は残件から外し、失敗の履歴として[MANUAL-BB-BATCH-20261107](MANUAL-BB-BATCH-20261107.md)に保持する。

## Birdseye更新対象

本バッチで変更した文書（README.md、TASK.product-clarity-11-07-2026.md、AC-20261107-03.md、REQUIREMENTS-EXTENSION.md、ACCEPTANCE-EXTENSION.md、USER-STUDY-PLAN.md、本ファイル）はCapsule更新対象。手順は[docs/BIRDSEYE.md](../BIRDSEYE.md)のとおり、本文レビュー後にcodemap生成、review hash確定、freshness checker `--require-reviewed`を実行する。製品作業中に無理に最終freshness合格へ上書きしない。
