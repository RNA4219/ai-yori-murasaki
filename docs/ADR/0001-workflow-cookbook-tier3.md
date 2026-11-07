---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# ADR-0001: Workflow Cookbook Tier 3 の採用

## 背景

受領レポート1件の状態から、ユーザーが Workflow Cookbook へのフル準拠と RUNBOOK のスキル使用規約を依頼した。

## 判断と理由

導入元の正式な Tier 3（Full）を採用する。基本5テンプレートの責務と版を保ち、Task / Acceptance / Birdseye を追加する。
Imozuru は原典調査、Yomiyasu は日本語本文の作成・推敲へ接続する。
保守 CLI は既存のローカル Cookbook を参照し、製品未実装の段階で上流の実行基盤を複製しない。

## 影響

原文・検証・判断の追跡と月次 review を行う。導入元へのローカル依存と未コミット差分を [UPSTREAM](../UPSTREAM.md) で追跡する。
Git 初期化、CI、製品実装、実ターゲット検証、公開は今回の作業に含まない。

## 追跡

[Task 20261106-01](../tasks/TASK.workflow-adoption-11-06-2026.md) / [AC-20261106-01](../acceptance/AC-20261106-01.md) / [導入対応表](../workflow-adoption.md)。
