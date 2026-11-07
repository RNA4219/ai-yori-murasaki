---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# Birdseye 運用

[index.json](birdseye/index.json) は文書ノードと依存、[hot.json](birdseye/hot.json) は入口、`caps/*.json` は原文への案内を保持する。
`generated_at` は5桁の世代番号、`mtime` はノードの世代識別値で、日付や内容確認の証明ではない。
`source_sha256` は原文の bytes の SHA-256。`review.source_sha256` と `review.summary_sha256` は内容確認時の原文・要約を指す。

## 参照

README / HUB → index → 必要な Capsule → 原文の順で必要な範囲を読む。0/1/2 hop は質問に応じて選ぶ。
索引がない、対象が未登録、形式が合わない、破損・鮮度不明の場合は `rg` 等で原文へ戻る。
未掲載を「資料が存在しない」と解釈しない。

## 更新

1. 原文を変更したら、Capsule の `summary`、`role`、`public_api`、`risks`、`tests`、`deps_in`、`deps_out` を照合して必要な箇所を更新する。
2. 新規ファイルは [codemap.config.json](../codemap.config.json) の include / exclude と発見対象を確認する。
3. [RUNBOOK](../RUNBOOK.md#environments) の変数を設定し、導入元の Codemap を実行する。

```powershell
Push-Location $CookbookRoot
try {
    py -3 -m tools.codemap.update --repo-root $ProjectRoot --targets docs/birdseye/index.json,docs/birdseye/hot.json --emit index+caps
} finally {
    Pop-Location
}
```

4. 生成は hash・依存・世代を更新するが、原文や要約の正しさの確認は別に行う。各要約と参照を原文に照らしてから review を確定する。
5. review は `reviewed_at`（タイムゾーン付き時刻）、`reviewed_by`、原文 hash、要約 hash、確認範囲を保持する。
   要約 hash は導入元 `tools.codemap.source_freshness.summary_digest` の規則で求める。確認していないノードの review を一括更新しない。
6. [RUNBOOK Execute](../RUNBOOK.md#execute) の freshness checker を `--require-reviewed` で通す。新規発見された Capsule の review がない場合も未確認として扱う。

原文と索引の循環 hash を避けるため、Birdseye 自身の JSON を原文ノードへ収録しない。
導入時のレビューは文書・要約・リンクの整合が対象で、レポートの外部事実や製品の性能を実証したものではない。
