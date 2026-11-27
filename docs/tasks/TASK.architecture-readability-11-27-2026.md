---
task_id: 20261127-03
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-27
next_review_due: 2026-12-27
---

# 構成図の等倍表示と独自の種類

変更前のモニターから40要素の合成図を開いたところ、幅1440 pxの Chromium で図の領域が1034 px、右の詳細欄が340 px、初期倍率は23%だった。幅375 pxで図の領域は357 pxだった。図と詳細を縦に並べて初期倍率を等倍にし、多数の要素から検索して詳細へ移動できるようにする。利用者が図ごとに要素の種類を定め、画面で割り当てて保存できるようにする。[R-087・R-088 と操作例](../requirements/ARCHITECTURE-READABILITY.md)

今回の依頼で、同一 PC 上の Codex チームが構成図の設計・実装・試験・独立レビューを担当する。設計では次の2つの資料を適用した。

| 資料 | 今回確認すること | SKILL.md の SHA-256 |
| --- | --- | --- |
| Anything Frontend Product Design | モニターから図を開き、検索、選択、詳細確認、図への復帰、種類の編集と保存まで進めるか | `428a706df98dab56a677608e4db339070f40cda67c51a3269b7595416e8a8fd9` |
| Anything Frontend Art Direction | PC・狭幅で等倍の図と詳細を縦に読めるか。長い名称や詳細が図の操作を妨げないか | `f1bc815f6b0765a9df35efd7498caa4448ec7b3ff193b25a6322b0f40ee5e4f5` |

[T59・T60 の技術検収](../acceptance/AC-20261127-03.md)・[変更履歴](../../CHANGELOG.md)
