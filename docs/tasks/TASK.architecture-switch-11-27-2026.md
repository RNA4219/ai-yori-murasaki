---
task_id: 20261127-02
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewing
last_reviewed_at: 2026-11-27
next_review_due: 2026-12-27
---

# ワークスペース内の構成図を切り替える

[R-086 と T58](../requirements/ARCHITECTURE-MAP-SELECTION.md) に従い、構成図の取得 API と、モニターから開ける図の選択欄を提供する。画面の既存の操作列に「図」ラベル付きの標準の選択欄を置く。図と URL を同期し、未保存の編集があれば切替前に確認する。Blue の公開範囲と幅375 pxでの操作を検収する。

依頼者は今回の構成図切替に限り、R-050 と作業規約の GLM／DeepSeek 担当分を、同一 PC の Codex チームが設計・実装・独立レビューすることを承認した。参照した `ai-instructions-frontend` の各ファイルの SHA-256 を記す。

| ファイル | SHA-256 |
| --- | --- |
| `SKILL.md` | `87c0a7f0a835c9e8522ba419cbd618b2287a631fa5fa9f048a05ae9ca91ac73d` |
| `references/layout-code.md` | `011e4b9f7a4958bb6c44670a37c8f414116fada4baa86d1dffd0491c9194b503` |
| `references/interaction.md` | `6c20af66c422f0ae6d38c2e37dc071200c5b108ab0642d97493366c2a6873123` |
| `references/delivery-quality.md` | `3ad7555beaf64b99b5c5bf28dc4fc4f449bc28e884de8172a372a954a92194ef` |

[技術検収](../acceptance/AC-20261127-02.md)・[ソース反映](../releases/RA-20261127-02.md)・[変更履歴](../../CHANGELOG.md)
