---
intent_id: INT-AYM-SECURITY-SKILL-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# 脆弱性診断スキル

Cloudflareのsecurity-auditをsankaiで分解して最適化した版を、プロジェクトの [.agents/skills/security-audit/](../.agents/skills/security-audit/SKILL.md) に配置した。入口は日本語で、監査計画と実行・証跡保全の詳細は必要な時点で読む構成になっている。分野別資料、スキーマ、検証器、テスト、MITライセンスを含む23ファイルを、最適化した作業コピーからそのまま取り込んだ。

## 使う場面

このプロジェクトで脆弱性診断・セキュリティレビューを行うときは、[SKILL.md](../.agents/skills/security-audit/SKILL.md) を入口にする。相談や特定箇所の調査では関係する指示だけを使う。コードベースの監査・ペンテスト、包括的レビュー、監査報告書の依頼では完全監査の6工程を行う。

依頼の例

```text
$security-audit を使って ai_yori_murasaki/evidence.py の信頼境界をレビューして。
```

```text
$security-audit でこのプロジェクトを監査して。出力先は対象外のディレクトリを使って。
```

配置先は [OpenAI公式のプロジェクト用スキルの配置規則](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills) に従う。プロジェクトを作業ディレクトリとして使う。見つからない場合は、上記SKILL.mdのパスを明示して読む。

## 実行と成果物

- 完全監査の開始前に [AUDIT-PLANNING.md](../.agents/skills/security-audit/AUDIT-PLANNING.md) を読み、対象・範囲・プロファイル・予算・前回結果を確認する。
- 対象コードの実行・証跡保存・委任の書き込み先設定の前に [EXECUTION-SAFETY.md](../.agents/skills/security-audit/EXECUTION-SAFETY.md) を読む。必要なOS隔離や証跡保全が使えなければ、実行せず正確な障害を残す。
- 独立検証、`confirmed`・`needs_validation`・`rejected`の区別、確定だけに重大度を付ける条件を維持する。
- Windows版Nodeでno-follow・nonblocking入力保護が使えない場合、検証器CLIは入力を拒否する。対応するLinux環境を使い、保護を弱めない。
- 完全監査の既定出力は対象外の `~/security-audit-skill/<repo-name>/run-<N>`。診断の報告書と、このプロジェクトのTask・Acceptanceは別に保存する。

スキルのfindings.jsonは同梱の [report-schema.json](../.agents/skills/security-audit/report-schema.json) を契約とする。製品側の [Findingスキーマ](../ai_yori_murasaki/schemas/v1/finding.schema.json) は必須項目・状態・重大度が異なるため、そのまま投入しない。今回の組み込みは開発・レビュー用スキルの配置と運用接続であり、製品の自動実行器や変換アダプターの追加を含まない。

## 導入元と更新

上流は [cloudflare/security-audit-skill](https://github.com/cloudflare/security-audit-skill)、比較元は `c1c8a8c1471069fb0e188eeaff69b8e8db6564a8`。ローカル最適化版のファイルごとのSHA-256とサイズは [導入スナップショット](evidence/security-audit-skill-snapshot-20261106.json)、利用条件は [同梱LICENSE](../.agents/skills/security-audit/LICENSE) に保存した。

更新時は同じフォルダー内の参照をそろえ、最適化した作業コピーと照合する。上流の最新版へ無条件で上書きするとローカル最適化が失われるため、差分を確認してから更新する。導入記録・スナップショット・Birdseyeも同期する。

導入前のLinux環境では既存テスト65件が通過している。今回の配置検査と、実製品への診断・検出精度の評価は区別する。実際の導入検収は [AC-20261106-05](acceptance/AC-20261106-05.md) を参照する。
