---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# Upstream 管理

- 正本: `C:/Users/ryo-n/Codex_dev/Agent_tools/workflow-cookbook/HUB.codex.md`。
- 導入元: `C:/Users/ryo-n/Codex_dev/Agent_tools/workflow-cookbook`。
- 確認した base HEAD: `b53256c8ad9c44f32367795d8ac5be94af8c48bb`。
- 確認日: 2026-11-06（日本時間）。
- 状態: 導入元に未コミット変更・未追跡ファイルがある。HEAD だけで採用内容を再現できないため、実際に読んだ定義・テンプレート・検査関連ファイルの SHA-256 を [snapshot](upstream-snapshot.json) に保持する。

## 更新手順

1. 必要な作業時と月次 review を目安に、HUB、adoption-tiers、adoption-guide、テンプレートと検証 CLI の差分を確認する。
2. 対象 revision とローカル差分を確認し、互換性・有用性・保守リスクに基づき採否を [確認ログ](UPSTREAM_WEEKLY_LOG.md) へ記録する。
3. 採用する差分だけを Task 化し、本文の確認後に template_version / review 日を更新する。版だけ合わせない。
4. [RUNBOOK](../RUNBOOK.md) のゲートを通し、Acceptance と Birdseye を同期する。

snapshot は導入時の識別記録で、導入元更新に追随して自動上書きしない。将来は別の dated snapshot を作り、Task / Acceptance から結ぶ。
今回上流ファイルは変更していない。テンプレートの帰属と利用条件は [上流 LICENSE](upstream-workflow-cookbook-LICENSE.txt) を保持する。
