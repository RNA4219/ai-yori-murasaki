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

- 0011 — 2026-11-08: P1/P2軽量拡張とWazuh実接続。BlueツールへSigma draft rule生成・Advisory検索（クエリ秘匿）を追加し、ATT&CKマッピングをfindingへ接続、シナリオを8本（権限3類型＋SQLi/XSS/Path Traversal/依存CVE＋benign）へ拡張した。エージェントRSIのExperience Memoryはtrain完了カードの検証を通る形状へ修正し trusted memory への追記を確認。Wazuh追補では`_request()`へリトライ処理を統一し、クエリの`limit`を検証する。`wazuh_ingest.py`はJSONLから5項目のsanitized telemetry行を生成し、全行不正なら例外とする。初回テストで見つかった空リスト判定を修正後、取り込み7件とスモーク判定4件が成功し、全体は604 passed / 2 skipped / 22 subtests passed（80.70秒）。認可ローカルラボのmanager 4.9.2で認証と状態取得が成功し、ローテーション済み実アラート187件の取り込みを確認した。標準manager APIの`/alerts`は404のためスモークでは`skipped`と記録し、Indexer検索は未検証。製品側は例外を返し、フォールバックは追加していない。[実行記録](docs/evidence/wazuh-live-20261108.md)
  [Task 20261108-01](docs/tasks/TASK.agent-core-11-08-2026.md) / [AC-20261108-01](docs/acceptance/AC-20261108-01.md)。Indexer側運用・本番接続・新規タグ・Release・配備は含まない。ユーザー承認済みのソース更新としてcommit・pushを行う。

- 0010 — 2026-11-08: エージェント実行基盤とエージェントRSIの中核機能を実装。`aym lab demo`の通常経路で、Redが観測からプローブを決定し、BlueがBlindでtelemetryのみから検知を報告し、Purpleが対象コードの原因特定から修正案を生成し、非LLM Oracleが攻撃不成立と正常系回帰を再検証する閉ループを実Docker・実モデルで確認した。policy bundle（プロンプト・権限・予算）を改善対象とするRSI（freeze/train/compare/ready/review/apply）を接続し、train 2campaign・比較4campaignの実行と不採用判断（gain 0、未pinモデルは探索扱い）を記録した。モニターにagent活動・帰属・RSI候補の表示を追加した。pytest 593件緑。
  [Task 20261108-01](docs/tasks/TASK.agent-core-11-08-2026.md) / [AC-20261108-01](docs/acceptance/AC-20261108-01.md)。5類型のうち4類型へのエージェント拡張、Experience Memoryのagent経路接続、promotion-ready・反映、Birdseye同期は含まない。

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
