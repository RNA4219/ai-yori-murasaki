---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# 要約保存・見直し 2：仕様

[Core仕様](SPEC-CORE.md)、[API契約](API-CONTRACTS.md)、[評価仕様](SPEC-EVALUATION.md)、[受入計画](ACCEPTANCE-PLAN.md)、[実装分担](IMPLEMENTATION-PLAN.md)を作成した。DeepSeekが作成し、GLMの修正確認はpass、未解決の重大指摘は0件。[レビュー記録](../evidence/specification-review-02-20261106.json)に文書hashと判断を保存した。

証跡はrun単位の単一ledger、CAS、別管理checkpointとする。sealed chainを検証記録で変更しない。workerは正本・checkpoint・他役割の入力へ直接到達できない。Oracleはモデルを呼ばず、攻撃成立と修復成功を分けて導出する。

実行中のcompletionはnullで、完了結果の分母に入れない。candidate起因の不成功は失敗に数え、外部障害による無効pairは理由と件数を残す。SigmaはJSONで表す有限の構文を定義し、非対応をHOLD、壊れた入力をFAILに分けた。

実装はまだ開始していない。次は3回目の受入・境界・依存順レビューを行う。Core/Evalの役割は作業分担であり、実装モデルはいずれもDeepSeek。画面の設計とレビューは実際のフロントエンドスキルを読んだGLMが行う。

外部製品の契約試験と実接続、モデル登録・開発時呼出・製品接続・バージョン固定を区別する。ラボ閉ループとOllama製品adapterの実接続は後続で検証する。会話の実Compaction回数と、この要約保存の番号は区別する。
