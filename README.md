---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-08
next_review_due: 2026-12-08
---

# ai-yori-murasaki

ローカルの隔離ラボで、LLMエージェントによるRedの観測、BlueのBlind検知、Purpleの原因調査と修正案生成、修復後の再検証を実行するPython製品です。エージェントはbrokerが許可したツールだけを実行し、その結果を次の判断に戻します。証跡を保存し、LLMを使わないOracleが判定します。HTMLモニターから実行状態、検知、修復、KPI、RSI候補を確認できます。独自コードのライセンスは[MIT](LICENSE)です。

2026-11-07に、一度出していた製品完成の判定を撤回しました（[再評価と追加提案](docs/reviews/REVIEW-INTAKE-20261107.md)）。その後、最低5類型の評価、根拠付き分類と修復・再検証の接続、CVSS・業務影響、KEV/EPSS、HTML表示を実装しました。15runの正式採点では全splitが合格して評価identityを凍結し、旧holdoutの順序来歴疑義は診断扱いとしました。未使用seedで追補したholdout3runも全件が合格です。実ラボ画面の最終確認では、実campaignで全ケースが合格し、配布物と現行コードの一致も確認しました。技術検収は[AC-20261107-03](docs/acceptance/AC-20261107-03.md)にまとめています。

2026-11-08に、原要求の中核機能を成すエージェント実行基盤を実装しました（[AC-20261108-01](docs/acceptance/AC-20261108-01.md)）。`aym lab demo`の通常経路では、Redエージェントが観測から次のプローブを決め、Blueエージェントがtelemetryだけから検知を報告し、Purpleエージェントが対象コードを読んで原因を特定し、修正案を生成します。LLMは検知・調査・修復案の主体であり、検知漏れや予算切れは例外なく失敗として記録され、固定の正解へ切り替わりません。policy bundle（プロンプト・権限・予算）を改善対象とするRSIも接続し、実行・比較・採否判断を記録します。

追加項目のうちCVSS・業務影響と最低5類型の評価は承認済みです。実利用者試験は2026-11-07のユーザー訂正で必須条件から撤回しました（[追加要件](docs/requirements/REQUIREMENTS-EXTENSION.md)、[利用者試験計画](docs/requirements/USER-STUDY-PLAN.md)）。[実装仕様](docs/requirements/SPEC-ASSESSMENT.md)に従い、[Task 20261107-03](docs/tasks/TASK.product-clarity-11-07-2026.md)で対応しています。

## 起動

Python 3.11以上、DockerのLinuxコンテナ実行環境、Ollamaを使用します。Ollamaには`glm-5.3:cloud`と`deepseek-v4.1-flash:cloud`を登録し、cloudモデルを利用できる状態にします。

```powershell
# リポジトリをcloneした場所に移動する
Set-Location <clone先のリポジトリルート>
py -3 -m pip install .
docker build -t ai-yori-murasaki-lab:1 ai_yori_murasaki/lab_assets

# リポジトリの外に、互いに重ならない作業用ディレクトリを用意する
$RuntimeRoot = '<リポジトリ外の作業用ルート>'
$env:AYM_WORKSPACE = Join-Path $RuntimeRoot 'workspace'
$env:AYM_TRUSTED_ROOT = Join-Path $RuntimeRoot 'trusted'
$StagingRoot = Join-Path $RuntimeRoot 'staging'
New-Item -ItemType Directory -Force -Path $env:AYM_WORKSPACE,$env:AYM_TRUSTED_ROOT,$StagingRoot | Out-Null
aym lab demo --staging-root $StagingRoot
aym monitor serve --bind 127.0.0.1:8765
```

[製品実行モニター](http://127.0.0.1:8765)を開きます。`workspace`、`trusted`、`staging`はリポジトリの外に、互いに重ならないディレクトリとして用意します。モニターは読み取り専用です。ラボを再実行する場合は、同じ環境変数を設定した別の端末を使います。シナリオは`--scenario`で選べ、既定は認可旗標のシナリオです。

詳細は[RUNBOOK](RUNBOOK.md)と[製品操作ガイド](docs/PRODUCT-GUIDE.md)を参照してください。

## 検証した範囲

2026-11-08に、エージェント駆動の閉ループを実DockerとOllamaの実モデル（`deepseek-v4.1-flash:cloud`、`glm-5.3:cloud`）で確認しました。認可不備の2シナリオ（認可旗標の誤設定と比較の反転）で、Redが自力でプローブを選択し、Blueが未許可アクセス行を検知し、Purpleがシナリオごとに異なる原因を特定して修正案を生成しました。修正後の攻撃は403で不成立になり、正常系5件がすべて通りました。比較反転シナリオの修正案は`REQUIRE_ADMIN`を持たないコード形状を対象にしており、事前登録した置換では作れない内容です。pytestは593件が緑です。

RSIでは、trainから方針bundleの候補を作り、凍結した条件でvalidationとholdoutを比較します。実行ではtrain両シナリオにbaselineが成功したため候補は不変で、比較はgain 0の不採用としました。cloudのモデル名は固定版の証明にならないため、比較は探索扱いです（D-058）。決定的なローカル認可方針の評価（`require_admin`）は従来どおり別経路で実施できます。promotion-readyと既定実行器への反映は行っていません。

5類型の正式採点ではtrain/validation/holdoutの全splitでrecall 1.0・正常系保持1.0・判定不能0.0となり、評価identity（イメージdigest一致・ソース不変）を凍結しました。配布用のsdist/wheelをビルドし、MITライセンスと第三者表示を同梱しています。秘密情報検査は186ファイルで0件、配布物（sdist/wheel）と現行コード112ファイルのsha256比較で不一致0件、評価identityの13artifactも全件一致です。

[製品検収](docs/acceptance/AC-20261106-06.md)にテスト、画面、モデル呼び出し、比較評価の証跡をまとめています。外部Trivy、CodeQL、Wazuh、Sigma CLI、RustXNG、rdshは契約試験までです。ATT&CKの実ラボ対応付けは未測定です。cloudモデル名から固定版を保証しません。

## 文書と要件

Workflow CookbookのTier 3（Full）を採用しています。[導入対応表](docs/workflow-adoption.md)に適用範囲とテンプレート版を記録しています。

<!-- LLM-BOOTSTRAP v1 -->
作業開始時は[HUB.codex.md](HUB.codex.md)と[GUARDRAILS.md](GUARDRAILS.md)を読み、必要な原文へ進みます。Birdseyeは[index.json](docs/birdseye/index.json)から参照します。未登録・破損・鮮度不明の場合は通常検索で原文に戻ります。
<!-- /LLM-BOOTSTRAP -->

| 文書 | 内容 |
| --- | --- |
| [要件](docs/requirements/REQUIREMENTS.md)・[判断](docs/requirements/DECISIONS.md) | 原典調査と要件の採否 |
| [Core仕様](docs/requirements/SPEC-CORE.md)・[評価仕様](docs/requirements/SPEC-EVALUATION.md) | データ、権限、Oracle、RSI、測定 |
| [API契約](docs/requirements/API-CONTRACTS.md) | CLIと監視API |
| [BLUEPRINT](BLUEPRINT.md)・[EVALUATION](EVALUATION.md) | 範囲と検証条件 |
| [RSI方針](docs/RSI.md) | 自己改善の9項目 |
| [Task](docs/tasks/README.md)・[検収](docs/acceptance/INDEX.md)・[CHANGELOG](CHANGELOG.md) | 作業と証跡の追跡 |

要件の起点となった受領レポートは、再配布権が未確認のため非公開の保存資料としてローカルで保持しています（リポジトリには含まれません）。原文の提案値と、この製品で確認した値は区別します。

## 公開状態

このリポジトリはGitHub Publicで公開しています（https://github.com/RNA4219/ai-yori-murasaki ）。リモートCIは設定しておらず、CIバッジ等は表示しません。本番環境は対象外です。公開の承認と実行状態は[公開記録](docs/releases/README.md)（RA-20261107-01）に残します。
