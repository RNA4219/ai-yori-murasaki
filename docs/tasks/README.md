---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# Task 運用

[TASK.codex.md](../../TASK.codex.md) を複製し、`TASK.<slug>-MM-DD-YYYY.md` としてこのディレクトリに保存する。
`task_id: YYYYMMDD-xx` は同日に重複しない値とし、Intent、owner、review 日・期限を記す。

状態は `planned → active → in_progress → reviewing → done` を基本とする。外部依存で停止した場合は `blocked` と理由を記し、解除後は作業中へ戻す。
完了には当該作業の受入条件、必要な検証、対応する Acceptance が必要。
未実行の検証や製品実装を、文書の整備だけで完了へ進めない。

Task、[Acceptance](../acceptance/README.md)、[CHANGELOG](../../CHANGELOG.md)、必要時の [公開記録](../releases/README.md) を相互参照する。
作業を追加・完了したら [HUB](../../HUB.codex.md)、一覧と Birdseye を更新する。

## 一覧

| task_id | 作業 | 検収 |
| --- | --- | --- |
| 20261127-07 | [モニターの HTTPS とセキュリティ再診断](TASK.monitor-https-11-27-2026.md) | [AC-20261127-07](../acceptance/AC-20261127-07.md)（検収中） |
| 20261127-06 | [Wazuh の接続確認と検証証跡](TASK.verification-evidence-11-27-2026.md) | [AC-20261127-06](../acceptance/AC-20261127-06.md)（技術検収合格） |
| 20261127-05 | [モニターの Google ログインをメールドメインで許可する](TASK.google-sso-email-domains-11-27-2026.md) | [AC-20261127-05](../acceptance/AC-20261127-05.md)（技術検収合格） |
| 20261127-04 | [ローカルモニターの Google ログイン](TASK.google-monitor-sso-11-27-2026.md) | [AC-20261127-04](../acceptance/AC-20261127-04.md)（技術検収合格） |
| 20261127-03 | [構成図の可読性と独自の種類](TASK.architecture-readability-11-27-2026.md) | [AC-20261127-03](../acceptance/AC-20261127-03.md)（技術検収合格） |
| 20261127-02 | [ワークスペースの構成図切替](TASK.architecture-switch-11-27-2026.md) | [AC-20261127-02](../acceptance/AC-20261127-02.md)（技術検収合格） |
| 20261127-01 | [案件の状態、前回比較、受付後の結果確認](TASK.case-decisions-11-27-2026.md) | [AC-20261127-01](../acceptance/AC-20261127-01.md) |
| 20261126-02 | [課題に適用する検査と資源の観測範囲](TASK.tracker-evidence-11-26-2026.md) | [AC-20261126-02](../acceptance/AC-20261126-02.md)（技術検収合格） |
| 20261126-01 | [課題一覧と観測の照合](TASK.offline-tracker-drift-11-26-2026.md) | [AC-20261126-01](../acceptance/AC-20261126-01.md) |
| 20261124-01 | [製品4画面のデザインと画面移動](TASK.frontend-workspace-11-24-2026.md) | [AC-20261124-01](../acceptance/AC-20261124-01.md) |
| 20261123-09 | [構成と接続の図を直接操作する](TASK.architecture-canvas-11-23-2026.md) | [AC-20261123-09](../acceptance/AC-20261123-09.md) |
| 20261123-08 | [案件の重要度別件数と検知](TASK.case-severity-11-23-2026.md) | [AC-20261123-08](../acceptance/AC-20261123-08.md) |
| 20261123-07 | [10万案件の詳細と判断画面](TASK.indexed-case-detail-11-23-2026.md) | [AC-20261123-07](../acceptance/AC-20261123-07.md) |
| 20261123-06 | [モニター索引の差分更新](TASK.incremental-monitor-11-23-2026.md) | [AC-20261123-06](../acceptance/AC-20261123-06.md) |
| 20261123-05 | [案件受付と登録索引](TASK.case-intake-11-23-2026.md) | [AC-20261123-05](../acceptance/AC-20261123-05.md) |
| 20261123-04 | [10万案件のモニター一覧](TASK.monitor-index-11-23-2026.md) | [AC-20261123-04](../acceptance/AC-20261123-04.md) |
| 20261123-03 | [文書索引と過去の検収記録](TASK.document-consistency-11-23-2026.md) | [AC-20261123-03](../acceptance/AC-20261123-03.md) |
| 20261123-02 | [検知件数と案件の比較元](TASK.monitor-counts-11-23-2026.md) | [AC-20261123-02](../acceptance/AC-20261123-02.md) |
| 20261123-01 | [リポジトリ・システム構成図](TASK.architecture-map-11-23-2026.md) | [AC-20261123-01](../acceptance/AC-20261123-01.md) |
| 20261122-01 | [判断の画面と記録モニターの改修](TASK.frontend-review-11-22-2026.md) | [AC-20261122-01](../acceptance/AC-20261122-01.md) |
| 20261116-01 | [判断の画面（独立プレビュー）](TASK.decision-view-11-16-2026.md) | [AC-20261116-01](../acceptance/AC-20261116-01.md)（未検収） |
| 20261112-01 | [RSIシナリオ起動・課題文・失敗時呼び出し数](TASK.rsi-scenarios-11-12-2026.md) | [AC-20261112-01](../acceptance/AC-20261112-01.md)（未検収） |
| 20261111-03 | [対象ファイル読取の範囲切り詰め・継続](TASK.read-target-range-11-11-2026.md) | [AC-20261111-03](../acceptance/AC-20261111-03.md)（未検収） |
| 20261111-02 | [運用障害3件（実ログBlue・Oracle CLI・macOS試験）](TASK.ops-imported-blue-11-11-2026.md) | [AC-20261111-02](../acceptance/AC-20261111-02.md)（未検収） |
| 20261111-01 | [Agent RSI no-opとモデル固定検証](TASK.agent-rsi-pin-11-11-2026.md) | [AC-20261111-01](../acceptance/AC-20261111-01.md)（未検収） |
| 20261109-05 | [資料インポート・出典リンク・明るい画面](TASK.sources-light-ui-11-09-2026.md) | [AC-20261109-02](../acceptance/AC-20261109-02.md) |
| 20261109-01 | [Blue評価とRSI適用の整合性修正](TASK.quality-integration-11-09-2026.md) | [AC-20261109-01](../acceptance/AC-20261109-01.md) |
| 20261109-02 | [SQLiの実LLM修復検証（計画）](TASK.p2-sqli-11-09-2026.md) | 未着手 |
| 20261109-03 | [実WazuhイベントのBlue評価（計画）](TASK.p2-wazuh-blue-11-09-2026.md) | 未着手 |
| 20261109-04 | [異なるRSI候補の固定条件比較（計画）](TASK.p2-rsi-experiment-11-09-2026.md) | 未着手 |
| 20261109-07 | [OpenAI互換LLM接続の汎用化](TASK.llm-remote-11-09-2026.md) | [AC-20261109-07](../acceptance/AC-20261109-07.md)（未検収） |
| 20261108-03 | [v0.1.1 リリース（版上げと配布物の再ビルド）](TASK.version-011-11-08-2026.md) | [AC-20261108-03](../acceptance/AC-20261108-03.md) |
| 20261108-02 | [README整備とv0.1.0まわりの文書整合](TASK.readme-release-11-08-2026.md) | [AC-20261108-02](../acceptance/AC-20261108-02.md) |
| 20261108-01 | [エージェント実行基盤・RSI・Wazuh追補](TASK.agent-core-11-08-2026.md) | [AC-20261108-01](../acceptance/AC-20261108-01.md) |
| 20261107-03 | [分類・判断支援・追加検証](TASK.product-clarity-11-07-2026.md) | [AC-20261107-03](../acceptance/AC-20261107-03.md) |
| 20261107-02 | [外部レビューの整理と完成判定の訂正](TASK.review-intake-11-07-2026.md) | [AC-20261107-02](../acceptance/AC-20261107-02.md) |
| 20261107-01 | [モニター再設計と自動更新](TASK.monitor-redesign-11-07-2026.md) | [AC-20261107-01](../acceptance/AC-20261107-01.md) |
| 20261106-01 | [Workflow Cookbook Tier 3 導入](TASK.workflow-adoption-11-06-2026.md) | [AC-20261106-01](../acceptance/AC-20261106-01.md) |
| 20261106-02 | [RSI の方針と評価契約](TASK.rsi-policy-11-06-2026.md) | [AC-20261106-02](../acceptance/AC-20261106-02.md) |
| 20261106-03 | [RSI 文書の名称整理](TASK.rsi-naming-11-06-2026.md) | [AC-20261106-03](../acceptance/AC-20261106-03.md) |
| 20261106-05 | [最適化済み診断スキルの導入](TASK.security-audit-skill-11-06-2026.md) | [AC-20261106-05](../acceptance/AC-20261106-05.md) |
| 20261106-06 | [要件熟成から製品完成](TASK.product-completion-11-06-2026.md) | [AC-20261106-06](../acceptance/AC-20261106-06.md) |
