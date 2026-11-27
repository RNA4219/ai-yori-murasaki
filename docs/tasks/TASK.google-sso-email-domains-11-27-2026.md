---
task_id: 20261127-05
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-27
next_review_due: 2026-12-27
---

# モニターの Google ログインをメールドメインで許可する

利用者を一人ずつ登録せずに、許可した Google Workspace ドメインの所属者がモニターに入れるようにする。個別の `sub` による登録も引き続き利用できる。個人とドメインを併用した場合は両方の条件を照合する。[R-090 と3通りの設定例](../requirements/GOOGLE-MONITOR-SSO.md)

メールアドレスの所有者や組織への所属をどの情報で確認できるか、Google 公式の ID トークン検証手順を確認した。AYM は署名検証済みのメール、確認済みフラグ、所属ドメインを照合し、Workspace、個人用 Gmail、第三者のメールアドレスを使う Google アカウントを区別する。[Google の検証手順](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)

製品コミット `b50355739c49e611adeed3a935e1b6df6399e3af` で、合成データを使った T62 の技術検収8件と4領域の独立レビューを完了した。実アカウントでのログインは未検証。[T62 の技術検収](../acceptance/AC-20261127-05.md)・[変更履歴](../../CHANGELOG.md)・[PR #23](https://github.com/RNA4219/ai-yori-murasaki/pull/23)
