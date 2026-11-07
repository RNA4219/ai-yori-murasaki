---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# Workflow Cookbook 導入対応表

「フル準拠」は導入元 `docs/adoption-tiers.md` の **Tier 3: Full** とする。
構成ファイルの存在だけでなく、このフォルダーに合う本文、Task と検収の実記録、原文と照合した Capsule を整える。
採用判断は [ADR-0001](ADR/0001-workflow-cookbook-tier3.md)。

| 区分 | 要求 | 対応 |
| --- | --- | --- |
| Tier 0 | 共通入口 | [README](../README.md) |
| Tier 1 | 方針と文書ルーティング | [BLUEPRINT](../BLUEPRINT.md)、[HUB](../HUB.codex.md) |
| Tier 2 | 手順・行動指針・検収 | [RUNBOOK](../RUNBOOK.md)、[GUARDRAILS](../GUARDRAILS.md)、[EVALUATION](../EVALUATION.md) |
| Tier 3 | 作業・検収・知識マップ | [tasks](tasks/README.md)、[acceptance](acceptance/INDEX.md)、[index](birdseye/index.json)、[hot](birdseye/hot.json)、Capsule 群 |
| テンプレート追跡 | 版と review メタデータ | 基本5文書の front matter、[snapshot](upstream-snapshot.json) |
| 作業の完了伝播 | Task → Acceptance → 変更履歴 | [Task 20261106-01](tasks/TASK.workflow-adoption-11-06-2026.md)、[検収](acceptance/AC-20261106-01.md)、[CHANGELOG](../CHANGELOG.md) |
| 公開への追跡 | 公開時の版と承認 | [公開運用](releases/README.md)。現時点の公開はなし |
| 保守 | upstream 差分と採否記録 | [UPSTREAM](UPSTREAM.md)、[確認ログ](UPSTREAM_WEEKLY_LOG.md) |
| 障害・判断 | インシデントと意思決定の記録 | [Incident 雛形](INCIDENT_TEMPLATE.md)、[ADR](ADR/README.md) |
| スキル運用 | 原典調査と日本語本文 | [RUNBOOK: Imozuru / Yomiyasu](../RUNBOOK.md#imozuru) |

## 採用したテンプレート版

| 文書 | template_version |
| --- | --- |
| HUB / BLUEPRINT / RUNBOOK / EVALUATION | 1.0.0 |
| GUARDRAILS | 1.1.0 |

章立ての責務は維持し、本文は日本語で本プロジェクト向けに記載した。
導入元の HEAD と未コミット差分は [UPSTREAM](UPSTREAM.md) と snapshot で識別する。
Tier checker の drift 判定は版比較であり、本文の同一性や外部動作の証明ではない。

## 適用範囲

製品コードとローカル試験を実装した。リモートCI、Canary、稼働SLOは未設定。カバレッジ率だけを受入の判定にせず、[受入計画](requirements/ACCEPTANCE-PLAN.md)と[製品検収](acceptance/AC-20261106-06.md)で要求・境界・実行証跡を照合する。
導入元リポジトリの自己運用用 CI・SLO・テスト一式は Tier 3 の必須要件ではなく、このフォルダーへ転記しない。
[governance/policy.yaml](../governance/policy.yaml) で現段階のローカルゲートを定める。
外部連携の onboarding checker は CI の別評価であり、今回その `ready` を達成済みとはしない。
今回の実測結果は [技術検収](acceptance/AC-20261106-01.md) を正本とする。
