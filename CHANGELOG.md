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

## [0.1.1] - 2026-11-08

### Added

- 0016 — 2026-11-08: v0.1.1 リリース（ユーザーの実施指示による）。pyproject.toml と ai_yori_murasaki/__init__.py の版を 0.1.0 から 0.1.1 へ上げ、配布物（sdist/wheel）を再ビルドして GitHub Release v0.1.1 に添付した。v0.1.0 の配布物（2026-11-07 ビルド）以降の主な変更は、エージェント実行基盤・RSI・Wazuh 意味保持（0010–0012、[0.1.0] 節に記録）と README・モニター整備（0013–0015）。成果物の hash と検証結果は RA-20261108-02 と AC-20261108-03 に記録する。Birdseye は生成00027へ再生成し、鮮度検査（--require-reviewed）を通過した。
  [Task 20261108-03](docs/tasks/TASK.version-011-11-08-2026.md) / [AC-20261108-03](docs/acceptance/AC-20261108-03.md)。

- 0015 — 2026-11-08: モニターのスクリーンショットを README 冒頭へ配置した（ユーザーの指示による）。従来はモニター節の中だけにあった画像を、冒頭の製品説明の直後にも置き、モニター節の画像は重複を避けて出典説明のみに整理した。Birdseye は生成00026へ再生成した。
  [Task 20261108-02](docs/tasks/TASK.readme-release-11-08-2026.md) / [AC-20261108-02](docs/acceptance/AC-20261108-02.md)。

- 0014 — 2026-11-08: 製品モニターのスクリーンショットを README へ追加し、Birdseye を再生成した（Task 20261108-02 の追補）。実ラボの実行はこの作業環境に Docker と Ollama が無いため行わず、2026-11-08 の実ランの証跡として保存されたモニター API 応答（campaign 詳細・RSI、[docs/evidence/agent-core-20261108/monitor/](docs/evidence/agent-core-20261108/monitor/)）をローカルで再生し、製品モニター（ai_yori_murasaki/web/index.html）の実画面を撮影した。run 一覧・モデル・アダプタ・評価は再生に使える記録が無く空表示で、その旨を README のキャプションに明記した。撮影は Chrome（headless）で実施し、接続エラー表示のない画面であることを目視で確認した。Birdseye は導入元の Codemap で再生成し、変更・新規ファイルの要約確認と review 更新を行い、鮮度検査（--require-reviewed）を通過した。
  [Task 20261108-02](docs/tasks/TASK.readme-release-11-08-2026.md) / [AC-20261108-02](docs/acceptance/AC-20261108-02.md)。

## [0.1.0] - 2026-11-08

### Added

- 0013 — 2026-11-08: README整備とv0.1.0まわりの文書整合（ユーザーの実施指示による）。READMEにクイックスタート節とモニター節を追加し、「公開状態」にReleaseへの参照を追記した。CHANGELOGの[Unreleased]蓄積分（0001–0012）を[0.1.0] - 2026-11-08として確定し、空の[Unreleased]を新設した。タグv0.1.0とGitHub Releaseは初回公開時（2026-11-07、RA-20261107-01の実行、タグ対象commit 8f38777）に作成済みだったため新規作成はせず、Releaseノートの本文だけを整備した。経緯は公開記録RA-20261108-01と検収AC-20261108-02に残した。製品コード・テストは変更していない。モニターのスクリーンショットは実データ（state.json）がリポジトリに無いため付けていない。Birdseyeカプセルの再生成は生成基盤が本環境に無く未実施。
  [Task 20261108-02](docs/tasks/TASK.readme-release-11-08-2026.md) / [AC-20261108-02](docs/acceptance/AC-20261108-02.md)。

- 0012 — 2026-11-08: Wazuh取り込みの意味保持（外部レビュー対応）。`wazuh_ingest.ingest_alerts_file`の既定出力を、正規化イベント（`kind`/`stable_id`/`timestamp`/`rule_id`/`rule_level`/`agent_id`/`agent_name`/`data`）をそのまま残すJSONLへ変更した（R-008「正規化event」）。従来の5項目telemetry行（`rule_level>=3→200`、`agent_id→role`）は実測HTTPではない擬似観測値のため、`compat_rows=True`かつ`out_compat_jsonl`指定のオプトインへ格下げし、labデモのリプレイ専用と明記した。読み戻しは意味保持側`normalized_events_from_jsonl`（`kind=wazuh_alert`でない行、`method`/`path`/`status`/`role`を搬入する行を拒否）とlab互換側`rows_from_jsonl`（5フィールド外を拒否）へ分離し、相互に誤流用できないことをテストで固定した。スモークは既定で意味保持取り込みを行い、`--compat-rows --compat-out-jsonl`でのみ擬似行を出す。テストは実行済み（2026-11-08 10:42–10:44 JST、関連14 passed in 0.24s、全体607 passed / 2 skipped / 22 subtests in 79.79s、従来の604件に回帰なし）。実WazuhデータでのBlue検知性能の測定は今回の範囲外で未実施のまま。[docs/evidence/wazuh-live-20261108.md](docs/evidence/wazuh-live-20261108.md)
  [Task 20261108-01](docs/tasks/TASK.agent-core-11-08-2026.md) / [AC-20261108-01](docs/acceptance/AC-20261108-01.md)。Wazuh専用イベント型のBlue入力schema新設と未知パターン検知評価は含まない（別課題）。

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
