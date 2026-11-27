---
task_id: 20261127-04
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewing
last_reviewed_at: 2026-11-27
next_review_due: 2026-12-27
---

# ローカルモニターの Google ログイン

ログインを明示的に有効にしたモニターでは、許可した Google アカウントにだけ4画面とデータ API・配信ファイルを公開する。既定の起動方法と Blue の表示範囲を保つ。利用者が初めて自分のアカウントを許可するときの設定手順も示す。[R-089 と設定手順](../requirements/GOOGLE-MONITOR-SSO.md)

この依頼について、同一 PC の Codex チームが設計、実装、試験、修正、独立レビューを担当する。

| 資料 | 確認すること |
| --- | --- |
| Anything Frontend Product Design | 4画面のどれを開いてもログインでき、キャンセルや許可外アカウントから再試行できる。ログイン後は元の画面へ戻れる。 |
| Anything Frontend Art Direction | PC と幅375 pxで操作と結果を読み取れる。ログイン画面は保護対象の画像やスタイルの読込に依存しない。 |
| ai-instructions-frontend | キーボードのフォーカス、ボタンの名称と役割、ログアウトした後の表示を確認する。 |
| [security-audit](../../.agents/skills/security-audit/SKILL.md) | 認証に伴う変更箇所を静的に点検する。対象は画面とデータのアクセス制御、トークンの照合、セッションとログに記録する値。 |

最初の利用者の `sub` をどう確認するかは、Google の OpenID Connect 文書、Discovery 文書、公式 Playground の操作手順を照合した。設定手順は Yomiyasu の lint・資料照合・verify を実行し、5分と最長1時間の期限を実装と照合した。

[T61 の技術検収](../acceptance/AC-20261127-04.md)・[変更履歴](../../CHANGELOG.md)
