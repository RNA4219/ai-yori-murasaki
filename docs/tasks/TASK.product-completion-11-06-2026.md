---
task_id: 20261106-06
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewing
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-06
acceptance_id: AC-20261106-06
---

# 要件の熟成から製品完成まで

## Objective

2026-11-06 の明示的な Goal に従い、Sankai による整理、Ollama を使った Imozuru 調査、要件・仕様の作成と約3回の要約保存・見直しを経て、Blue / Purple Enablement 製品を完成させる。ARTEX の画面を参考に、HTML で進捗・判断・実行証跡を監視できるようにする。MIT での公開を予定する。

## Scope

- In: 原レポートの条件付き分解、根拠収集、要件・仕様・受入条件、GLM によるレビュー、DeepSeek による実装、統合検証、ローカル HTML モニター、RSI の評価・反映境界。
- Out: 第三者の実ターゲットへの攻撃、本番接続・変更、ユーザーに代わる公開操作。原資料や他作業の削除。

## Requirements

- 監督・統合は Codex、調査は Ollama、レビューは `glm-5.3:cloud`、実装は `deepseek-v4.1-flash:cloud`。ユーザーが登録済み Ollama モデルの使用を確認した。1017 固定版を使用したとは報告しない。
- [原資料（非公開の保存資料）](<../ADR/deep-research-report (16).md>) は改変しない。推奨・未指定・未検証を承認済み仕様と混同しない。
- Sankai の条件・依存・例外・引用を保持し、Imozuru は原文保存と停止条件に従う。外部原文は調査フォルダーで保持する。
- 要件・仕様の見直しを3段階で記録し、各段階で判断・根拠・未解決・次の作業を圧縮保存する。実際の会話 Compaction が起きた回数とは区別する。
- 実装済みのオフライン試作を要件の根拠にせず、確定した仕様との適合を後で判断する。
- 完成条件は調査・レビューで具体化する。機能実装、外部連携の実接続、性能向上実証を区別する。

## Plan

Sankai整理 → Imozuru調査 → 要件レビューと要約1 → 仕様レビューと要約2 → 受入・境界レビューと要約3 → 実装 → 統合検証 → 文書・索引・検収の確定。

## Affected Paths

調査・要件・仕様・Task・Acceptance・モニター・製品コード・テスト・文書索引。詳細は各段階で記録する。

## Deliverables

Sankai HTML、原典付き調査資料、要件・仕様書、3段階の要約とレビュー記録、製品と HTML モニター、[AC-20261106-06](../acceptance/AC-20261106-06.md)。

## Tests

文書検査、要件と受入条件の追跡、機能・障害・権限境界・画面の検証。実際に実行した結果を検収へ記録する。比較評価は評価 identity 契約に従う。

2026-11-07のローカル方針比較は [EV-20261107-RSI-01](../evaluation-manifests/EV-20261107-RSI-01.json) に条件を固定する。[実装・入力bundle](../evidence/rsi-evaluation-20261107/runner-bundle.json) と学習由来の候補を使い、上流preflight成功後に比較を実行する。対象は決定的なローカル認可方針であり、LLMの汎用性能や外部製品の有効性の評価には使わない。

## Notes

EV-20261107-RSI-01は凍結処理の引数漏れで比較開始前に停止した。結果を保存し、[EV-20261107-RSI-02](../evaluation-manifests/EV-20261107-RSI-02.json) で修正後の評価器bundleを固定してpreflightからやり直す。01ではvalidation/holdoutを実行していない。

先行した [P0試作](TASK.p0-core-11-06-2026.md) は未検収で保全中。今回の Goal で実装までの権限が与えられたが、要件成熟の順序を守る。

## Completion Evidence

[製品検収の対応表](../PRODUCT-ACCEPTANCE.md)に全50要件とT01〜T20、RSI、P2の実結果を接続した。401テスト成功、2件はWindows symlink制約でスキップ。実Docker/実モデル閉ループと配布wheelを確認した。RSI評価02はpromotion-ready、未反映。Tier 3/drift、Acceptance、原文確認を伴うBirdseyeの各ゲートは当時成功した。

2026-11-07の再評価で、これらを製品完成の根拠とした判断を撤回した。本Taskはreviewingへ戻し、AC-20261106-06をrejectedとした。R-046の実行経路への統合、類型と分類根拠、複数類型の評価、初心者向け判断支援が不足している。[受領レビューの整理と必要性評価](../reviews/REVIEW-INTAKE-20261107.md)に基づいて既存要求の不足と追加提案を分ける。既存の試験証跡は変更していない。
