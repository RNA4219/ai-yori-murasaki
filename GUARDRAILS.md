---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
template_version: 1.1.0
---

# Guardrails

## Principles

- [BLUEPRINT](BLUEPRINT.md) の範囲内で、小さく確認可能な変更を行う。既存資料・他の作業を保全する。
- 原文、提案、確認済みの事実、実行結果を区別する。原文と生成索引の整合を保つ。
- 変更に必要な検証を選び、結果と未実行理由を記録する。文書導入に製品の試験結果を作らない。
- 依頼外の追加実装は、要件と必要性を報告して明示的な承認を得てから進める。
- RSI は [固定した目的と評価条件](docs/RSI.md) で旧版と比較する。結果を見た後で採用閾値を緩めず、未見課題・正解・経験集合の分離を保つ。採用準備完了と既定実行器への反映を別に扱う。

## Safety Boundaries

- 認証情報・個人情報・内部ターゲットの機密を外部検索や公開資料へ流さない。
- Red / Blueの実行は明示された認可scopeと隔離ラボに限る。第三者の実ターゲットは対象外。
- Red / Blue / Oracleの権限・セッション・証跡公開範囲の分離を維持する。変更時には漏えい・権限境界の回帰検査を行う。
- 本番適用、権限操作、公開の判断を LLM の自己採点や文書ゲートの成功だけに委ねない。
- policy や公開条件の変更は [governance](governance/policy.yaml) と根拠を確認する。今回の新規文書整備はユーザーの依頼範囲で進める。

## Context Intake

[README](README.md)、[HUB](HUB.codex.md) と必要な原文から読む。Birdseye が対象に合えば索引と必要な Capsule を使う。
索引の未登録・破損・形式不適合・鮮度不明は `rg` 等で原文へ戻る。生成世代だけで内容確認済みにしない。
原典調査と日本語推敲の適用条件は [RUNBOOK](RUNBOOK.md) に従う。

## Output Contract

変更範囲、検証結果、残件を日本語で伝える。ネイティブツールの結果を正本とし、架空の実行結果や本文 JSON による二重実行を作らない。
Task → [Acceptance](docs/acceptance/README.md) → [CHANGELOG](CHANGELOG.md) を辿れるようにする。
文書の技術検収と人間のリスク受容・公開承認を分け、比較評価時は [評価 identity](docs/CONTRACTS.md#evaluation-identity) を固定する。
