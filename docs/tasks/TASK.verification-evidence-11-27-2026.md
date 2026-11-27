---
task_id: 20261127-06
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-27
next_review_due: 2026-12-27
---

# Wazuh の接続確認と検証証跡の修正

1. ローカルの Wazuh 接続確認では、認証とアラート取得のどちらでも信頼する CA とホスト名を照合する。運用者が組織の CA を指定できるようにする。
2. チェックポイントを発行する前に、指定した案件と実行台帳の各イベントの案件が一致することを確認する。
3. 台帳またはチェックポイントを検証した時点の対象を履歴に残す。モニターは大きな履歴もページ単位で読み、古い記録や読めない行があるときはその状態を伝える。

製品コミット `398214ff015dd7d296de2e8292540a90c5c052a5` で、合成証明書・台帳・旧形式の履歴を使った技術検収8件と4領域の独立レビューを完了した。実際の Wazuh と顧客の台帳は未検証。[技術検収の結果](../acceptance/AC-20261127-06.md)・[運用手順](../PRODUCT-GUIDE.md)・[CLI と HTTP の契約](../requirements/API-CONTRACTS.md)・[変更履歴](../../CHANGELOG.md)・[PR #24](https://github.com/RNA4219/ai-yori-murasaki/pull/24)
