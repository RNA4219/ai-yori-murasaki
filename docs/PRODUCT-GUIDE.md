---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
---

# 製品操作ガイド

## インストールとデータの保存先

[READMEの起動手順](../README.md#起動)でインストールする。wheelからインストールする場合は`py -3 -m pip install <wheelのパス>`を使う。Dockerfileとラボ用スクリプトはwheelにも含まれる。配置先は次のコマンドで確認できる。

```powershell
py -3 -c "from pathlib import Path; import ai_yori_murasaki; print(Path(ai_yori_murasaki.__file__).parent / 'lab_assets')"
```

表示されたディレクトリを`docker build -t ai-yori-murasaki-lab:1 <ディレクトリ>`に渡す。イメージの取得にはネットワークが必要。ラボ実行時はinternal network内でworkerとtargetを起動し、所有するコンテナとnetworkを終了時に片付ける。

| 場所 | 内容 |
| --- | --- |
| AYM_WORKSPACE | Campaign、Run台帳、CAS、投影、検証journal |
| AYM_TRUSTED_ROOT | 独立したcheckpoint、評価の凍結情報、経験の出所、採用registry |
| staging-root | コンテナへ渡す限定されたラボファイル |

3つの場所はリポジトリの外に置き、親子関係を含めて重ねない。trusted rootをworkspaceから自動生成した値で上書きしない。P0試作のイベントやCLIはv1と互換ではない。P0テストは[archive](evidence/p0-test-archive/migration.json)に保存した。旧workspaceは保持し、v1は新しいworkspaceで始める。

## ラボとモニター

`aym lab demo --staging-root <絶対パス>`で、固定された攻撃3件、Blind Blueの検知、ラボ修復、同じ攻撃の再実行、正常系5件、Oracle判定を順に実行する。モデル生成コードは実行しない。ラボの修復は固定されたfixtureの変更である。

`aym lab iterate --staging-root <絶対パス> --max-iterations 2 --max-campaigns 2`は、Campaign単位の上限付きで反復する。成功、環境障害、同じ失敗の反復、残予算なしで停止する。`max-campaigns`をツール呼び出し数と読み替えない。任意の`--routes-file`でロールと登録モデルの対応を指定できる。

`aym monitor serve --bind 127.0.0.1:8765`で画面を配信する。Campaign一覧・詳細、Run、公開可能なイベント、検証、承認、モデル、adapterを表示する。15秒ごとに更新し、タブが非表示なら休止する。操作を伴う承認や反映はCLIで行う。

モデル表示は、指定名、登録状況、開発時の呼び出し、製品経由の接続、固定版の確認を分ける。unknownや未測定を成功へ読み替えない。外部adapterの契約試験成功は、そのサービスへの接続成功ではない。

## CLI応答と検証

CLIの結果はschema_version、command、status、reason_code、campaign_id、run_id、dataを持つJSONで返る。終了コードは0が正常、1が判定失敗、2が入力不正、3が保留、4がpolicyによる拒否。攻撃のOracleがFAILでも、攻撃不成立と正常回帰の両方を確認できた修復は成功となる。

```powershell
aym campaign list
aym campaign show --campaign-id <ID>
aym verify chain --campaign-id <ID> --run-id <ID>
aym verify checkpoint --campaign-id <ID> --run-id <ID>
```

ハッシュ列だけでは、末尾削除や全列の再計算を独立に検知できない。trusted rootのcheckpointと照合する。checkpointがなければ保留であり、成功扱いにしない。検証結果はsealed runへの追記ではなく、verification journalへ保存する。

## RSIの操作

現在の採用評価器は、`require_admin`を持つローカル認可方針を決定的に実行する。trainで原因を分析し候補を作成する。評価用の正解情報はLLMに送信しない。

| 操作 | 入力と結果 |
| --- | --- |
| rsi train | campaign-id、policy-ref、dataset-ref、seed。完了したtrainのCAS参照を返す |
| rsi propose | campaign-id、train-ref。trainの再計算に基づき候補と抽象経験カードを返す |
| rsi memory-add | input、train-ref。検証したtrain由来のカードを登録する |
| rsi freeze | campaign-id、freeze-file。条件・実装・入力・経験を固定する |
| rsi candidate | freeze-id。固定した候補を登録する |
| rsi compare | candidate-id。validation、holdoutを同条件で比較する |
| rsi ready | candidate-id。事前基準と実体の一致を再検証する |
| rsi review | candidate-id、reviewer、decision。採否を別記録にする |
| rsi initialize / apply | 初期registryの作成、承認済み候補の明示的反映 |

policyとdatasetは`Store.put_object`でCASへ格納した参照を使う。datasetはtasks配列を持ち、各taskにtask_id、split、casesを置く。caseはrole、resource、expected_allowedを持つ。具体的な入力形式は[実行に用いたdataset](evidence/rsi-evaluation-20261107-02/dataset.json)を参照する。評価済みholdoutは新しい候補の採用評価へ再利用しない。

freeze-fileは`config`、`manifest`、`bindings`、絶対パスの`artifact_root`の4項目。bindingsはlegacy_source、candidate_source、legacy_input、candidate_input、runner、datasetの6実体を結ぶ。[凍結入力の実例](evidence/rsi-evaluation-20261107-02/freeze-input.json)は既存実験の証跡であり、別環境でそのまま再利用する雛形ではない。新しい実験ではID、未見課題、実体hash、絶対パスを作り直す。

runnerには`rsi_eval.runner_snapshot()`のbytesを保存する。評価に関わる8モジュール、全schema、Pythonと依存ライブラリの版を束ねる。実装や入力が変わると、比較や採用準備の再検証で拒否される。

比較前に[評価identity契約](CONTRACTS.md#evaluation-identity)の上流preflightを通す。比較後は結果manifestを上流postrunで確認する。未完了、通信障害、条件不一致は採用比較に混ぜない。holdout観測後に候補を変えたら、新しい未見集合で評価する。

`promotion-ready`はレビュー待ちであり、反映済みではない。`rsi review`で採否を記録した後、`rsi apply --review-id <ID> --expected-baseline <hash> --expected-candidate <hash> --rollback <baselineのhash>`でregistryを更新する。比較の相手、候補、rollback先の一致と出所を検証し、atomicに置き換える。今回の実証候補は反映していない。

現在のreadyは`not_used:deterministic-local-policy`の評価に限定する。cloud aliasや自称の固定モデル版を、不変版の証明として受け付けない。LLMの汎用的な自己改善を採用判断するには、別の評価器と検証可能なモデル版が必要。

## 停止と回復

モニターはCtrl+Cで停止できる。workerの異常終了やモデルのtimeoutを、攻撃失敗または成功の証明にしない。原因と未完了状態を確認する。

クラッシュしたRunの回復は`aym run resume --run-id <ID>`を使う。新しいRunへ引き継ぎ、旧Runは保持する。同じ旧Runへの再試行は同じ回復先を返す。評価identityも引き継ぐ。sealed台帳を直接修正しない。

ラボが強制終了した場合は`docker ps -a --filter name=aym`と`docker network ls --filter label=ai-yori-murasaki=lab`で残存を調べる。削除前に名称とmount/networkをinspectし、当該実行が所有するものだけを指定する。workspaceとtrusted rootをまとめて消す復旧は行わない。

## 検証と配布

`py -3 -m pytest -q -rs`で通常回帰と故障注入を実行する。`py -3 -m pip wheel . --no-deps --wheel-dir dist`でwheelを生成する。wheel内のschema、HTML、policy、adapter契約、Dockerfileを確認し、リポジトリ外へのインストールで起動を確かめる。

受入の対応と未検証範囲は[製品検収](acceptance/AC-20261106-06.md)を参照する。独自コードはMIT。調査原文や同梱スキルなど第三者の資料は、それぞれの表示と利用条件を保持する。
