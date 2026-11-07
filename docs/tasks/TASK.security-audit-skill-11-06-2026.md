---
task_id: 20261106-05
intent_id: INT-AYM-SECURITY-SKILL-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
acceptance_id: AC-20261106-05
---

# 最適化済み診断スキルの導入

## Objective

ユーザーの依頼に従い、sankaiで分解して最適化したsecurity-auditを、このプロジェクトで使用できる形に組み込む。

## Scope

- In: `.agents/skills/security-audit/`への配置、参照資料・スキーマ・検証器・テスト・LICENSEの同梱、AGENTS・README・HUB・RUNBOOKからの案内、導入元・hash・検収・Birdseyeの記録。
- Out: 製品コード・自動実行器・Finding変換の実装、実ターゲットへの診断、外部サービスへの接続、グローバルスキル配置の変更。

## Requirements

最適化済み作業コピーの23ファイルを変更せず配置し、各SHA-256を記録する。相対参照を維持し、個別調査と完全監査、独立検証、隔離・保存の条件を案内する。検証器CLIのWindows上の制約を残す。
既存の製品契約へCloudflare形式のfindingsを自動投入しない。既存の別作業と原資料を保全する。比較評価・実験ではないため、評価identity manifestは適用外。

## Deliverables

[運用手順](../SECURITY-AUDIT.md)、[導入スナップショット](../evidence/security-audit-skill-snapshot-20261106.json)、[AC-20261106-05](../acceptance/AC-20261106-05.md)、[CHANGELOG 0004](../../CHANGELOG.md)。

## Tests

配置元とのbytes・hash一致、参照・frontmatter、Yomiyasu、Tier 3・Acceptance・Birdseyeを確認した。実行内容と結果は対応するAcceptanceへ記録した。製品の比較評価と診断は行っていない。
