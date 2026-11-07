---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
template_version: 1.0.0
---

# Runbook

## Environments

Windows / PowerShellとPython 3.11以上を使用する。製品ラボにはDocker LinuxコンテナとOllamaの登録モデルが必要。インストール、専用イメージのビルド、分離ディレクトリの設定は[README](README.md#起動)に従う。文書検査だけなら製品ラボを起動する必要はない。
初回は以下の変数を設定する。配置を変える場合は実在するパスへ変更する。

```powershell
$ProjectRoot = 'C:\Users\ryo-n\Codex_dev\ai-yori-murasaki'
$CookbookRoot = 'C:\Users\ryo-n\Codex_dev\Agent_tools\workflow-cookbook'
py -3 --version
```

導入元の既知の版・ローカル差分は [UPSTREAM](docs/UPSTREAM.md) を参照する。
現在は Git リポジトリではなく、CI / リリース / 本番サービスは未設定。下記は実行できるローカル検証である。

## Execute

1. [HUB](HUB.codex.md)、[GUARDRAILS](GUARDRAILS.md) と対象原文を読む。
2. [Task 雛形](TASK.codex.md) から [docs/tasks](docs/tasks/README.md) に作業を起票し、範囲と成功条件を固定する。
3. 必要な調査では Imozuru、日本語本文の作成・推敲では Yomiyasu を以下の手順で使用する。自己改善の設計・評価は [RSI 手順](#rsi) に従う。
4. 文書と Capsule の要約・参照を照合し、[Birdseye 手順](docs/BIRDSEYE.md) で同期する。
5. 次の3ゲートを実行する。各終了コードを確認し、失敗があれば依存する完了処理を止める。

```powershell
# Tier 3 とテンプレート版
py -3 "$CookbookRoot\tools\ci\check_adoption_tier.py" --repo $ProjectRoot --min-tier 3 --check-drift --check

# Acceptance の必須項目・章立て
py -3 "$CookbookRoot\tools\ci\check_acceptance.py" --check $ProjectRoot

# 原文 hash と Capsule の内容確認記録
Push-Location $CookbookRoot
try {
    py -3 -m tools.ci.check_birdseye_freshness --repo-root $ProjectRoot --index-path "$ProjectRoot\docs\birdseye\index.json" --hot-path "$ProjectRoot\docs\birdseye\hot.json" --require-reviewed --check
} finally {
    Pop-Location
}
```

6. Task / Acceptance / CHANGELOG の ID とリンク、[CHECKLISTS](CHECKLISTS.md) を確認する。検収の内容は [EVALUATION](EVALUATION.md) に従う。
7. 実行結果、未実行、残件を Acceptance へ記録してから Task を `done` にする。記録を変更した場合は該当 Capsule の要約も確認し、再同期する。

## Product Execution

[製品操作ガイド](docs/PRODUCT-GUIDE.md)にCLI、監視、RSI、復旧手順をまとめている。`aym lab demo`はローカルの固定fixtureを実行し、修復後の攻撃不成立と正常回帰を確認してレビュー待ちで停止する。反復する場合は`aym lab iterate --staging-root <絶対パス> --max-iterations 2 --max-campaigns 2`を使う。この予算単位はCampaign回数である。

manual-bbによる手動ブラックボックス試験は[MANUAL-BB-BATCH-20261107](docs/tasks/MANUAL-BB-BATCH-20261107.md)の段階方式（洗い出し→一括修正→最小回帰）で行う。

製品の検証は`py -3 -m pytest -q -rs`。wheelは`py -3 -m pip wheel . --no-deps --wheel-dir dist`で作成する。モデル呼び出し、実Docker、ブラウザーによる検証はpytestの件数と分けて[製品検収](docs/acceptance/AC-20261106-06.md)へ記録する。

## Security Audit

脆弱性診断・セキュリティレビューでは [導入手順](docs/SECURITY-AUDIT.md) と [.agents/skills/security-audit/SKILL.md](.agents/skills/security-audit/SKILL.md) を使う。参照資料・スキーマ・検証器・ライセンスは同じフォルダーに同梱している。
対象、範囲、出力先を依頼に合わせて決め、個別調査と完全監査を区別する。対象コードの実行前にはスキルの実行・証跡保全を読む。Windows版Nodeで検証器CLIの入力保護が使えない場合は、対応するLinux環境を使い、保護を無効化しない。
完全監査は診断専用の外部出力先へ保存し、プロジェクトの文書検収や製品のFinding契約と混同しない。

## Imozuru

原典・先行研究・参考実装を調査する段階では **Imozuru を使用する**。
使用前に [Imozuru の SKILL.md](C:/Users/ryo-n/.codex/skills/imozuru/SKILL.md) を読む。以下は本プロジェクトへの接続方法であり、細則はスキルを正本とする。

1. Task に問いと調査論点を記す。候補技術の事実確認、検知・修復・評価の根拠など、判断に必要な原文を特定する。
2. 原文と検索・取得ログは `C:/Users/ryo-n/research/ai-yori-murasaki/<topic>/` に保存し、`map.md`、`log.md`、`sources/` をスキルの規則で管理する。
3. 用語・人・媒体を探し、引用・著者・参考文献をたどる。調査中は原文を要約で置き換えない。取得失敗と未確認を記録する。
4. 各論点へ `file:行` と短い引用を結ぶ。停止条件は全論点の原文確保、飽和、周回上限などスキルの規則で判定し、理由と未解決を `log.md` に残す。
5. 停止後、Task / 設計判断へ原典の所在、対象版、確認日、採否理由を記録する。外部の原文をプロジェクトへ配布する場合は利用条件を確認する。

ローカルファイル探索、単一事実の確認、既存資料の要約だけの依頼には使用しない。
検索役を利用する場合はスキルの指示に従う。製品要件の調査結果は[判断記録](docs/requirements/DECISIONS.md)から辿る。最初の様式導入の検収と、その後の調査結果は区別する。

## Yomiyasu

日本語の説明文、調査報告、設計資料などの本文を作成・推敲する段階では **Yomiyasu を使用する**。
使用前に [Yomiyasu の SKILL.md](C:/Users/ryo-n/.codex/skills/yomiyasu/SKILL.md) を読み、対象文と資料、読者、文書の立場を確認する。

1. 原文を保持し、スキルに従って別名の最終稿を作る。主張、比重、断定の強さ、文の働き、数値、条件、未確認事項を保つ。
2. 識別子、コード、コマンド、URL、schema、Evidence の値を推敲の都合で変更しない。規範語の義務・許可・可能性を取り違えない。
3. 同梱の `scripts/yomiyasu_check.py` を Bash から CLI で実行し、lint と diff をまとめて検査する。ファイル出力は `run --final`、新規作成は `--material`、推敲は `--source` を使い分ける。提出前にスキルの `verify` 手順も行う。
4. 検出候補を文脈と照合し、警告を消すためだけの言い換えをしない。検査結果と意味の照合結果を Task / Acceptance へ記録する。
5. 確認した最終稿を対象文書へ反映し、関連リンクと Birdseye を同期する。検査環境が不足する場合は未実行理由を記録し、実施済みとはしない。

元の [受領レポート（非公開の保存資料）](<docs/ADR/deep-research-report (16).md>) は保存資料として扱い、依頼なく上書きしない。
製品操作文書の検査結果は製品検収に記録する。受領レポートの全文推敲は行っていない。

## RSI

自己改善を扱う作業は [RSI 方針](docs/RSI.md) の9項目と [RSI 契約](docs/CONTRACTS.md#rsi) を読み、次の段取りで進める。
実装済みCLIは[操作ガイド](docs/PRODUCT-GUIDE.md#rsiの操作)を参照する。以下の原則は実装後も維持する。

1. Task に達成目的、認可範囲、主指標と評価器、予算、反復回数、停止条件、採用基準を記す。結果を見る前に凍結する。
2. baseline / candidate の変更差分、source / config / prompt / input / model / runner、対象初期化、split / seed の識別情報を記録する。Experience Memory は有効・無効と、使う snapshot・取得規則を固定する。
3. train の証跡から原因を分析し、オフライン確認・回帰検査を行う。実行中の支持・反証・方針転換・拒否・停止を記録し、回復作業と残予算がなければ停止する。
4. 比較評価を行う段階では [Evaluation Identity](docs/CONTRACTS.md#evaluation-identity) の preflight を通す。調整を止めた候補を同一対象・seed・モデル条件・予算、同じ対象初期状態で対にして比較する。
5. validation で比較し、未観測 holdout で劣化していないか確認する。正解情報をエージェントへ渡さず、holdout 観測後に変更した候補には別の未見課題を用いる。
6. 比較に受理できない未完了・通信障害は理由付きで隔離する。完了した失敗、除外数、完了率、費用、安全性も記録する。診断用 run を採用評価として数えない。
7. 実測値・artifact hash と postrun を確認する。事前条件を満たした候補だけを `promotion-ready` としてレビューへ回し、採用・不採用の根拠を Acceptance に残す。主指標と異なる採点器だけで判定しない。
8. 既定実行器への反映は別 Task / 操作として、適用条件・レビュー結果・復旧方法を確認して行う。経験カードは完了した train 実行からのみ作り、評価観測を後続評価へ流入させない。

RSI の記録項目と評価条件は、実装時に [EVALUATION](EVALUATION.md#rsi-documentation-criteria) と照合する。
RSI 機能の実装完了と、比較で示した性能向上は別々に報告する。今回の文書追加では実行・学習・昇格は行わない。

## Observability

- 作業状態: [Task 一覧](docs/tasks/README.md) と各 Task の front matter。
- 検証結果: [Acceptance 一覧](docs/acceptance/INDEX.md)。実コマンドと終了コード、未実行理由を残す。
- 文書鮮度: Birdseye の世代・原文 hash・review、各文書の review 期限。
- 変更履歴: [CHANGELOG](CHANGELOG.md)、導入元の確認は [UPSTREAM_WEEKLY_LOG](docs/UPSTREAM_WEEKLY_LOG.md)。
- RSI: 今後の各 Task に凍結した評価条件、baseline / candidate と経験 snapshot、受理・除外 run、停止理由、採用判断と反映履歴を結ぶ。記録項目は [RSI 契約](docs/CONTRACTS.md#rsi)。
- `.ga/` は必要なときの一時ログ用。永続的な判断根拠は Task / Acceptance から辿れる場所へ置く。

異常に長い検証は、工程・経過時間・比較可能な過去記録を確認する。基準がなければ所要時間は不明とする。
対象範囲、処理、ネットワーク・I/O、負荷を確認し、長さだけでコードの非効率と判断しない。

## Confirm

- [ ] 変更範囲の3ゲートが成功し、内容の整合を確認した。
- [ ] Imozuru / Yomiyasu は使用した段階と未実行の範囲を記録した。
- [ ] Task、Acceptance、CHANGELOG の参照が揃い、原資料が保持されている。
- [ ] 比較評価・公開・本番操作と文書検収を区別した。
- [ ] RSI を扱う場合は、事前固定・未見評価・経験の固定・採用判断と反映の分離を確認した。

## Rollback / Retry

変更前の対象ファイルと既存成果を確認し、今回の変更だけを復元する。Git の履歴がない状態で `git reset` 等を復旧手段として使わない。
原資料や他の作業を一括削除しない。Birdseye の破損では原文を優先して索引を再生成し、レビューした Capsule だけを確認済みに戻す。
再現する不整合や判断へ影響した不具合は [Incident 雛形](docs/INCIDENT_TEMPLATE.md) で記録し、再発防止を Task / EVALUATION と結ぶ。
