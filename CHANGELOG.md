---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# Changelog

Keep a Changelog の区分で管理する。記録には既存最大番号 +1 の4桁通番を付け、Task / Acceptance を追跡の正本にする。

## [Unreleased]

### Added

- 0005 — 2026-11-07: 3段階の要件・仕様レビューを経て、ローカルDockerラボ、証跡・独立Oracle、RSI、P2、HTMLモニター、MITのwheelを実装。GLMが画面設計とレビュー、DeepSeekが実装を担当。
  [Task 20261106-06](docs/tasks/TASK.product-completion-11-06-2026.md) / [AC-20261106-06](docs/acceptance/AC-20261106-06.md)。外部公開・本番変更は含まない。

- 0004 — 2026-11-06: 最適化済み security-audit をプロジェクトの `.agents/skills/` に配置し、診断の入口、運用条件、導入元とファイル hash を記録。
  [Task 20261106-05](docs/tasks/TASK.security-audit-skill-11-06-2026.md) / [AC-20261106-05](docs/acceptance/AC-20261106-05.md)。実製品への診断・外部サービス接続は未実施。

- 0001 — 2026-11-06: Workflow Cookbook Tier 3 の文書・作業追跡・Birdseye を導入。RUNBOOK に Imozuru と Yomiyasu の使用手順を追加。
  [Task 20261106-01](docs/tasks/TASK.workflow-adoption-11-06-2026.md) / [AC-20261106-01](docs/acceptance/AC-20261106-01.md)。製品の実装・実証・公開は含まない。

- 0002 — 2026-11-06: RSI の9項目を収録し、事前固定、旧版との比較、未見課題、任意の経験メモリ、採用と反映の分離を設計・運用・評価契約へ接続。
  [Task 20261106-02](docs/tasks/TASK.rsi-policy-11-06-2026.md) / [AC-20261106-02](docs/acceptance/AC-20261106-02.md)。RSI 機能の実装・性能実証は未実施。

### Changed

- 0009 — 2026-11-07: 5類型評価、分類と修復・再検証の接続、CVSS・業務影響、KEV/EPSS、HTML表示を実装。15runの正式採点で全splitが合格し評価identityを凍結、sdist/wheelビルドと秘密情報検査（186ファイル0件）を実施。未使用seed 20261115のholdout追補3run全pass、実ラボ画面の最終確認（実campaignでB-01〜B-04全pass）、配布物と現行コードの一致を確認（旧dist-outのREADME陳腐化を検出したため`dist-finished-20261107`へ再ビルドし、新配布物でコード/assets・収録文書/metadata比較とも不一致0件）し、AC-20261107-03の技術検収を合格、Task 20261107-03を完了した。実利用者試験は撤回維持、RSI候補の反映と公開承認は含まない。
  [Task 20261107-03](docs/tasks/TASK.product-clarity-11-07-2026.md) / [AC-20261107-03](docs/acceptance/AC-20261107-03.md)。

- 0008 — 2026-11-07: CVSS・業務影響、最低5類型、実利用者試験の追加をユーザーが承認。追加要件と評価仕様を作成し、GLMレビューを経てDeepSeek実装を開始。製品検収は未完了。
  [Task 20261107-03](docs/tasks/TASK.product-clarity-11-07-2026.md) / [AC-20261107-03](docs/acceptance/AC-20261107-03.md)。

- 0007 — 2026-11-07: 外部レビューを原文保存し、Sankaiで条件付き整理と追加提案の必要性評価を作成。製品完成の判定を撤回し、既存の個別試験証跡を保持した。追加提案は採否未決定。
  [Task 20261107-02](docs/tasks/TASK.review-intake-11-07-2026.md) / [AC-20261107-02](docs/acceptance/AC-20261107-02.md)。

- 0006 — 2026-11-07: LinearとVercelの画面を参考に、モニターの配置と配色を再設計。主要4指標と詳細を分け、自動更新時は表示・フォーカス・スクロールを保持する。GLMが設計とレビュー、DeepSeekが主要実装を担当。
  [Task 20261107-01](docs/tasks/TASK.monitor-redesign-11-07-2026.md) / [AC-20261107-01](docs/acceptance/AC-20261107-01.md)。

- 0003 — 2026-11-06: RSI 文書と記録から特定の参照元名を削除。名称を含む絶対パスを資料 ID・相対名へ変更し、9項目と資料 hash を保持。
  [Task 20261106-03](docs/tasks/TASK.rsi-naming-11-06-2026.md) / [AC-20261106-03](docs/acceptance/AC-20261106-03.md)。
