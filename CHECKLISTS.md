---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# 作業チェックリスト

## 開始

- [ ] [AGENTS](AGENTS.md)、[HUB](HUB.codex.md)、[GUARDRAILS](GUARDRAILS.md) と対象原文を確認した。
- [ ] Task の目的、範囲、成功条件を記載し、依頼外の追加実装を混ぜていない。
- [ ] 調査・推敲の段階なら [RUNBOOK](RUNBOOK.md) の Imozuru / Yomiyasu の条件を確認した。
- [ ] 比較評価を含む場合は評価 identity の preflight を通した。
- [ ] RSI の実行評価を含む場合は [事前条件](docs/RSI.md) と経験 snapshot を固定し、未見課題と目的別評価器を用意した。

## 完了

- [ ] 適用する検査が成功し、原文・リンク・数値・条件を確認した。
- [ ] 原資料を保持し、未確認事項と提案を区別した。
- [ ] Capsule の要約と参照を原文に照らし、Birdseye の厳格検査を通した。
- [ ] Acceptance に実結果を記録し、Task の ID・状態・リンクを揃えた。
- [ ] CHANGELOG に最大通番 +1 の4桁番号で記録した。
- [ ] 公開を行う場合のみ [公開記録](docs/releases/README.md) と承認を確認した。
- [ ] RSI では受理・除外 run と採用理由を記録し、機能実装・性能実証・既定実行器への反映を区別した。
