---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# エージェント作業規約

日本語で記載する。親ディレクトリの AGENTS.md とユーザーの指示を引き継ぐ。

1. README → HUB → GUARDRAILS → 作業に必要な原文の順に読む。共有様式の正本は `C:/Users/ryo-n/Codex_dev/Agent_tools/workflow-cookbook/HUB.codex.md`。
2. 永続的な作業は [Task 運用](docs/tasks/README.md) に従い、完了時に Task、Acceptance、CHANGELOG の ID・状態・リンクを揃える。
3. 2026-11-06のGoalに基づき、確定した要件の製品実装とローカル検証を行う。範囲はBLUEPRINTと製品Taskで確認する。依頼外の追加実装は親の Supplemental Implementation Approval に従う。
4. 原典・先行研究を調べる段階では [RUNBOOK の Imozuru 手順](RUNBOOK.md#imozuru) を使う。既存レポートの要約やローカルファイル探索だけの依頼には適用しない。
5. 日本語本文の作成・推敲を依頼された段階では [RUNBOOK の Yomiyasu 手順](RUNBOOK.md#yomiyasu) を使う。コード、設定、識別子、原資料を文章校正の都合で変更しない。
6. 比較評価・実験・昇格・提出に入る前に [評価 identity 契約](docs/CONTRACTS.md#evaluation-identity) と導入元の契約を読み、preflight/postrun を実施する。文書検査だけなら評価実験は未実施と記録する。
7. 独立した読み取りは `Promise.allSettled` でまとめ、すべての結果を確認する。依存する処理、書き込み、検証記録の確定は直列に行う。
8. Birdseye は原文への案内。原文・要約を確認せず review hash や確認日だけを更新しない。詳細は [docs/BIRDSEYE.md](docs/BIRDSEYE.md)。
9. 検証は変更範囲に必要なものを実行し、成功・失敗・未実行を分ける。必要な確認が通ったら終了する。根拠なく試験範囲を広げない。
10. 自己改善を扱う場合は [RSI 方針](docs/RSI.md)、[RUNBOOK](RUNBOOK.md#rsi)、[RSI 契約](docs/CONTRACTS.md#rsi) を読む。評価条件と経験集合を固定し、未見評価・採用判断・実行器への反映を分ける。機能の実装、ローカル比較の結果、汎用的な性能向上の主張を分ける。
11. 脆弱性診断・セキュリティレビューでは、[導入手順](docs/SECURITY-AUDIT.md) とプロジェクト内の [security-audit](.agents/skills/security-audit/SKILL.md) を使う。個別調査と完全監査の条件、対象コードの隔離、独立検証を維持する。スキルの導入・更新だけでは診断を開始しない。
12. 2026-11-07のユーザー指示により、Codexは司令塔に限定する。Ollama登録モデル`glm-5.3:cloud`が作業分解、DeepSeekへの実装指示、フロントエンド設計、レビュー、検証結果の整理、修正指示、要約を担当する。製品コードとテストの実装・修正は`deepseek-v4.1-flash:cloud`に任せる。
13. コード全文、差分、長い実行ログ、モデルの回答全文は保存先からGLMへ渡し、通常はCodexの文脈へ展開しない。GLMからCodexへの報告は「結論・重大な問題・検証結果・必要な判断・証跡の場所」の短い要約とする。検証結果は実際のツール実行記録に基づき、成功・失敗・未実行を区別する。
14. Codexは優先順位、依頼範囲、停止・再開、ユーザーへの確認、完成判定を担当する。同じ失敗が続く場合も、GLMが原因と選択肢を要約して報告する。要約に重大な矛盾や証跡不足がある場合に限り、判断に必要な該当箇所を取得する。停止中のGoalを、この分担変更だけで再開しない。
