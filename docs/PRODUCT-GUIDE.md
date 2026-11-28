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

<a id="monitor-index-update"></a>

### 既存の案件索引を更新する

更新後に案件一覧で索引の更新を求められたとき、または API が 409 `monitor_index_required` を返したときは、モニターと同じ `AYM_WORKSPACE` と `AYM_TRUSTED_ROOT` を設定した端末で `aym monitor index` を実行し、画面を再読み込みする。通常の更新で、旧形式の索引にある課題を台帳から読み直し、Blue に表示する件数を再集計する。

### モニターのHTTPS起動

通常の起動には、サーバー証明書と対応する秘密鍵の PEM ファイルが必要になる。ブラウザーが信頼する発行元の証明書を用意し、証明書の Subject Alternative Name（SAN）の IP アドレス欄に `127.0.0.1` があることを確認する。`localhost` でも開きたい場合は DNS 名の SAN に `localhost` も必要になる。Google ログインの戻り先には常に `127.0.0.1` を使う。

作業ディレクトリ（`AYM_WORKSPACE`、`AYM_TRUSTED_ROOT`）を設定した PowerShell で、PEM ファイルのパスを指定する。秘密鍵はモニターの実行ユーザーが読み取れる、リポジトリ外の非公開の場所に置く。証明書の発行と利用端末での信頼設定は、端末を管理する組織の手順に従う。

```powershell
$env:AYM_MONITOR_TLS_CERT = Read-Host 'サーバー証明書 PEM のパス'
$env:AYM_MONITOR_TLS_KEY = Read-Host '対応する秘密鍵 PEM のパス'
aym monitor serve --bind 127.0.0.1:8765
```

ブラウザーで `https://127.0.0.1:8765/` を開く。コマンドに直接指定する場合は `--tls-cert <証明書PEMのパス> --tls-key <秘密鍵PEMのパス>` を使う。TLS 1.2 以上で接続する。PEM の不足・読取不能や証明書と鍵の不一致は起動エラーとなる。鍵のパスワードを対話入力する処理はない。ブラウザーで証明書エラーが出た場合は、有効期限、信頼チェーン、URL と SAN の一致を確認する。

SSO を使わずローカル HTTP で確認する場合に限り、TLS の環境変数を設定していないターミナルで `aym monitor serve --allow-http-loopback` を実行する。この場合の入口は `http://127.0.0.1:8765/`。このオプションと証明書・秘密鍵の指定、または `--google-sso` を併用すると起動に失敗する。[Google SSO の HTTPS 設定](requirements/GOOGLE-MONITOR-SSO.md)

モニターではCampaign一覧・詳細、Run、公開可能なイベント、検証、承認、モデル、adapterを表示する。15秒ごとに更新し、タブが非表示なら休止する。操作を伴う承認や反映はCLIで行う。

モデル表示は、指定名、登録状況、開発時の呼び出し、製品経由の接続、固定版の確認を分ける。unknownや未測定を成功へ読み替えない。外部adapterの契約試験成功は、そのサービスへの接続成功ではない。

## Wazuh の接続先を証明書で確認する

リポジトリの `scripts/wazuh_live_smoke.py` は、認可された Wazuh manager の認証とアラート取得を確認する。接続先のホスト名とサーバー証明書の信頼チェーンは両方のリクエストで検証される。標準の信頼ストアに CA があれば `--ca-file` は省略できる。組織や検証環境で発行した証明書を使う場合は、その CA 証明書を PEM ファイルで指定する。

次は、`localhost` を証明書のホスト名として使うローカル環境の例。リポジトリ直下で、接続を許可されたアカウントの `WAZUH_USER` と `WAZUH_PASS` を環境変数に設定して使う。ファイル名とポートは環境に合わせる。

```powershell
py -3 scripts/wazuh_live_smoke.py --host localhost --port 55000 --ca-file ./ca/wazuh-lab.pem
```

CA ファイルを読めない場合は `tls_trust` が失敗する。証明書の署名やホスト名を検証できない接続では資格情報を送信せず、スクリプトは終了コード1を返す。接続後、サーバーがアラート検索に 404 を返した場合、その機能の確認結果は `skipped` となる。

## CLI応答と検証

CLIの結果はschema_version、command、status、reason_code、campaign_id、run_id、dataを持つJSONで返る。終了コードは0が正常、1が判定失敗、2が入力不正、3が保留、4がpolicyによる拒否。攻撃のOracleがFAILでも、攻撃不成立と正常回帰の両方を確認できた修復は成功となる。

```powershell
aym campaign list
aym campaign show --campaign-id <ID>
aym verify chain --campaign-id <ID> --run-id <ID>
aym verify checkpoint --campaign-id <ID> --run-id <ID>
```

ハッシュ列だけでは、末尾削除や全列の再計算を独立に検知できない。trusted rootのcheckpointと照合する。checkpointがなければ保留であり、成功扱いにしない。検証結果はsealed runへの追記ではなく、verification journalへ保存する。

### チェックポイントを発行して履歴を見る

`aym checkpoint seal --campaign-id <案件ID> --run-id <実行ID> --operator-id <操作者ID>` は、封印済みの実行台帳の各イベントが指定した案件に属することを確認する。案件が異なる場合は `cross_campaign_ref` を返し、その案件にチェックポイントを発行しない。

`aym verify chain` は実行台帳を、`aym verify checkpoint` は台帳と独立したチェックポイントの対応を検証する。モニターで対象の実行を選び、検証履歴で結果とその理由を読む。operator は検証方法、検証時に確認できた台帳末尾の連番とハッシュ、照合したチェックポイントのダイジェストも確認できる。古い履歴でこれらの値が未記録の場合、現在の台帳から補うことはしない。

履歴は新しい記録から表示する。「以前の記録」「新しい記録」で移動できる。読み取れなかった行があるときは件数を確認できる。[履歴 API の取得方法](requirements/API-CONTRACTS.md#22-endpoints)

## 実ログを使った Blind Blue と Oracle CLI

攻撃役を動かさず、operatorが認可済みCampaignへJSONのログ書き出しを取り込む場合は、`red`の代わりに明示的な入力を指定する。通常の`blue_blind`は従来どおり、完了・封印済み`red`が必須。

```sh
aym campaign transition --campaign-id <campaign-id> --to running
aym run start --campaign-id <campaign-id> --phase blue_blind \
  --idempotency-key blue-import-001 --imported-logs-file ./logs.json
# 応答に含まれるblue run_idを保存する
aym run finish --run-id <blue-run-id> --completion completed
aym run seal --campaign-id <campaign-id> --run-id <blue-run-id>
aym checkpoint seal --campaign-id <campaign-id> --run-id <blue-run-id> --operator-id <operator-id>
aym campaign transition --campaign-id <campaign-id> --to blind_completed
aym run start --campaign-id <campaign-id> --phase verify --idempotency-key verify-import-001
aym oracle evaluate --run-id <verify-run-id> \
  --evaluated-run-id <blue-run-id> --predicate-file ./predicate.json
aym run finish --run-id <verify-run-id> --completion completed
aym run seal --campaign-id <campaign-id> --run-id <verify-run-id>
aym campaign transition --campaign-id <campaign-id> --to purple_open
```

`logs.json`は空でないJSONオブジェクトまたは配列。JSONLの場合は先にJSONへ整形する。`--imported-logs-ref <CAS-SHA256>`で同じStoreへ保存済みのデータを指定してもよい。取り込み元はrun manifest、run extensions、イベント証跡のCAS参照に記録され、idempotency keyの再利用では参照一致を要求する。クラッシュ再開時も参照を引き継ぐ。

この入口が行うのは**入力の固定・記録とBlue Runの作成**であり、ログに対する検知アルゴリズムの自動実行は行わない。Blue workerへの入力引き渡し、Finding記録、Oracle predicateの準備は利用側の実行手順が必要。独立Oracleは`verify`/`reevaluation`の実行Runに結果を保存し、`--evaluated-run-id`で同じCampaignの完了・封印済みBlue Runを指定したときだけBlue評価として扱う。PASSの自己申告では`purple_open`を開かない。

実データに含まれる機密・個人情報を除外した上でローカルStoreへ取り込む。認可scope、Blue/Oracleの権限分離、封印・checkpointの条件は変更しない。既存`--evaluated-run-id`省略時は評価Run自身を対象にする。

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
