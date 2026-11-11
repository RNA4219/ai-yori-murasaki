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
