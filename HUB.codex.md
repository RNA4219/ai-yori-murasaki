---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
template_version: 1.0.0
---

# ai-yori-murasaki 文書ハブ

`HUB_SCOPE_DECLARATION`: このフォルダー全体の要件・製品実装・運用・作業証跡に適用する。

## Purpose

[README](README.md) を共通入口とし、作業に必要な最小の原文へ案内する。
共通様式は Workflow Cookbook、プロジェクトの現在の適用範囲は [BLUEPRINT](BLUEPRINT.md)、導入元の版は [UPSTREAM](docs/UPSTREAM.md) を正本とする。

## Read Order

1. [AGENTS](AGENTS.md) と [GUARDRAILS](GUARDRAILS.md) で作業境界を確認する。
2. [BLUEPRINT](BLUEPRINT.md) で現在の範囲を確認する。
3. 必要に応じて [Birdseye index](docs/birdseye/index.json) → Capsule → 原文へ進む。
4. 操作は [RUNBOOK](RUNBOOK.md)、検収は [EVALUATION](EVALUATION.md) を使う。

## Task Routing

| 依頼 | 入口 | 続けて読むもの |
| --- | --- | --- |
| 案件を先に受け付ける・再送する | [案件受付と登録索引](docs/tasks/TASK.case-intake-11-23-2026.md) | [10万件からの登録と再送](docs/acceptance/AC-20261123-05.md) |
| 大規模な案件一覧を構築・更新する | [台帳の検証と一覧の索引](docs/tasks/TASK.monitor-index-11-23-2026.md) | [10万案件の検収](docs/acceptance/AC-20261123-04.md) |
| 案件一覧を差分更新する | [変更の検出と再集計](docs/tasks/TASK.incremental-monitor-11-23-2026.md) | [再構築との照合・更新失敗時の一覧](docs/acceptance/AC-20261123-06.md) |
| 大規模な案件一覧から詳細・前回比較を開く | [案件と実行の特定](docs/tasks/TASK.indexed-case-detail-11-23-2026.md) | [両画面で開いた台帳の検収](docs/acceptance/AC-20261123-07.md) |
| 案件ごとの重要度別件数から該当する検知を開く | [案件の検知と重要度](docs/tasks/TASK.case-severity-11-23-2026.md) | [集計と明細の検収](docs/acceptance/AC-20261123-08.md) |
| 文書索引と過去の検収状態を確認する | [整合性のTask](docs/tasks/TASK.document-consistency-11-23-2026.md) | [形式・鮮度の検収](docs/acceptance/AC-20261123-03.md) |
| 案件の集計状況・前回比較・改修後の構成 | [改修要件](docs/tasks/TASK.monitor-counts-11-23-2026.md) | [技術検収](docs/acceptance/AC-20261123-02.md) |
| リポジトリ・AWS構成の関係と指摘をたどる | [構成図と入力仕様](docs/tasks/TASK.architecture-map-11-23-2026.md) | [構成図の検収](docs/acceptance/AC-20261123-01.md) |
| 課題の優先順位と証跡を画面で確認する | [2画面の改修要件](docs/tasks/TASK.frontend-review-11-22-2026.md) | [画面と操作の検収](docs/acceptance/AC-20261122-01.md) |
| 結果を読む人向けの判断の画面 | [判断ビュー](decision_view/README.md) | [Task 20261116-01](docs/tasks/TASK.decision-view-11-16-2026.md)、[AC-20261116-01](docs/acceptance/AC-20261116-01.md)（未検収） |
| RSIシナリオ起動・Red課題文・失敗時呼び出し計測 | [Task 20261112-01](docs/tasks/TASK.rsi-scenarios-11-12-2026.md) | [AC-20261112-01](docs/acceptance/AC-20261112-01.md) |
| 実ログのBlind Blue／Oracle CLI／macOS試験 | [製品操作ガイド](docs/PRODUCT-GUIDE.md#実ログを使った-blind-blue-と-oracle-cli) | [Task](docs/tasks/TASK.ops-imported-blue-11-11-2026.md)、[検収](docs/acceptance/AC-20261111-02.md) |
| 資料の取り込み・出典リンク・明るい画面 | [資料取り込み](docs/SOURCES.md) | [Task](docs/tasks/TASK.sources-light-ui-11-09-2026.md)、[検収](docs/acceptance/AC-20261109-02.md) |
| Blue評価とRSI通常適用の品質修正 | [整合性契約](docs/requirements/QUALITY-INTEGRATION.md) | [検収](docs/acceptance/AC-20261109-01.md)、[P2計画](docs/requirements/P2-VALIDATION-PLAN.md) |
| 承認済み追加要件の実装 | [追加要件](docs/requirements/REQUIREMENTS-EXTENSION.md) | [仕様](docs/requirements/SPEC-ASSESSMENT.md)、[追加検収](docs/acceptance/AC-20261107-03.md) |
| 製品要件の再評価 | [受領レビューと必要性評価](docs/reviews/REVIEW-INTAKE-20261107.md) | [完成判定の撤回](docs/acceptance/AC-20261106-06.md) |
| モニターの画面と更新挙動 | [再設計](docs/requirements/MONITOR-REDESIGN.md) | [画面検収](docs/acceptance/AC-20261107-01.md) |
| 製品の起動・操作 | [製品操作ガイド](docs/PRODUCT-GUIDE.md) | [製品検収](docs/acceptance/AC-20261106-06.md) |
| OpenAI互換LLMの認証付き接続 | [導入手順](docs/INSTALL-DGX.md) | [検証計画](docs/acceptance/AC-20261109-07.md) |
| 目的・境界の確認 | [BLUEPRINT](BLUEPRINT.md) | [原資料（非公開の保存資料）](<docs/ADR/deep-research-report (16).md>) |
| 調査・原典収集 | [RUNBOOK: Imozuru](RUNBOOK.md#imozuru) | [契約](docs/CONTRACTS.md) |
| 自己改善の設計・評価 | [RSI 方針](docs/RSI.md) | [RUNBOOK: RSI](RUNBOOK.md#rsi)、[RSI 契約](docs/CONTRACTS.md#rsi) |
| 脆弱性診断・セキュリティレビュー | [診断スキルの運用](docs/SECURITY-AUDIT.md) | [security-audit](.agents/skills/security-audit/SKILL.md)、[RUNBOOK](RUNBOOK.md#security-audit) |
| 日本語本文の作成・推敲 | [RUNBOOK: Yomiyasu](RUNBOOK.md#yomiyasu) | 対象文と根拠資料 |
| 文書・様式の保守 | [RUNBOOK](RUNBOOK.md) | [導入対応表](docs/workflow-adoption.md)、[UPSTREAM](docs/UPSTREAM.md) |
| 作業の起票・完了 | [Task 運用](docs/tasks/README.md) | [Task 雛形](TASK.codex.md)、[Acceptance](docs/acceptance/README.md) |
| 品質確認 | [EVALUATION](EVALUATION.md) | [CHECKLISTS](CHECKLISTS.md)、[検収一覧](docs/acceptance/INDEX.md) |
| 破損・証跡不整合 | [RUNBOOK: 復旧](RUNBOOK.md#rollback--retry) | [Incident 雛形](docs/INCIDENT_TEMPLATE.md) |
| 方針変更・公開 | [ADR 索引](docs/ADR/README.md) | [governance](governance/policy.yaml)、[release 運用](docs/releases/README.md) |

## Update Rule

主要文書・作業導線を追加したらこのハブと [codemap 設定](codemap.config.json) を確認し、Birdseye を同期する。
完了概要は [CHANGELOG](CHANGELOG.md) に記録する。共通様式の本文は複製を増やさず、導入元への参照と採用差分を残す。
