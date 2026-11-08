# Wazuh 実接続スモーク記録（2026-11-08）

認可済みのローカル環境`aym-wazuh-manager`（`wazuh/wazuh-manager:4.9.2`、API `https://localhost:55000`）への読み取り専用検証記録。関連する検収は[AC-20261108-01](../acceptance/AC-20261108-01.md)。認証情報はメモリー上だけで扱い、JWT値や生アラートはこのリポジトリに保存しない。

## 実行環境とコマンド

Windows、Python 3.13.7、pytest 9.0.2。依存は既存の`pyproject.toml`に従い、検証用にpytestを導入した。コンテナを起動し直したり、Indexerを追加したりしていない。

```powershell
.venv\Scripts\python.exe -m pytest tests/test_wazuh_live.py -q
.venv\Scripts\python.exe -m pytest tests/test_wazuh_live.py tests/test_wazuh_smoke.py -q
.venv\Scripts\python.exe -m pytest tests -q
docker ps --filter name=aym-wazuh-manager
docker exec aym-wazuh-manager gzip -dc /var/ossec/logs/alerts/2026/Nov/ossec-alerts-07.json.gz > <capture-path>
.venv\Scripts\python.exe scripts/wazuh_live_smoke.py --host localhost --port 55000 --allow-default-creds --alerts-file <capture-path>
```

`<capture-path>`は作業用の一時ファイルを表す。`--allow-default-creds`は認可済みローカルラボの既定認証を明示的に許可するフラグ。通常は`WAZUH_USER`・`WAZUH_PASS`を環境から供給する。

## テスト結果

| 検証 | 実測結果 | 時間 |
| --- | --- | --- |
| 初回のWazuh取り込みテスト | 6 passed / 1 failed | 失敗は全行不正ファイルの検証 |
| 修正後の取り込みテスト | 7 passed | 0.11秒 |
| 取り込みとスモーク判定 | 11 passed | 0.15秒 |
| 最終の全体回帰 | 604 passed / 2 skipped / 22 subtests passed | 80.70秒 |

初回は`ingest_alerts_file`が全行不正のファイルで例外を返さなかった。`events`はリストであるため、`events == 0`を`not events`へ修正した。空ファイルと空白行だけのファイルは正常な空結果として通ることも確認した。

スモークには404を未対応として記録する変更を加えた。追加4テストは404・401・500と不正ファイルを扱い、401・500・取り込み失敗が終了コード1のままであることを確認した。製品側のクエリ例外やフォールバック方針は変えていない。

取り込み7件では認証のHTTPメソッドとBasicヘッダー、クエリのBearerヘッダー・`pretty=true`・`limit`の1..1000境界、`health`の200/401/403を到達可能とする扱い、リトライ上限での`AdapterUnavailable`、不正行の計上、全行不正時の例外、許可外キー拒否を確認した。

最終回帰は2026-11-08 09:55:24〜09:56:45 JSTに実行し、終了コード0。pytestの表示時間は80.70秒、プロセスの経過時間は81.321秒だった。スモーク変更前の回帰は600 passed / 2 skipped / 22 subtests passed、80.89秒。追加4件を含む再実行であり、性能改善の比較ではない。[最終出力](wazuh-live-20261108-results/pytest-all.txt)、[関連11件の出力](wazuh-live-20261108-results/pytest-wazuh-smoke.txt)、[実行時刻](wazuh-live-20261108-results/verification.json)

## 実接続結果

スモークは2026-11-08 09:55:24 JSTに実行し、終了コード0、経過0.590秒だった。

| 項目 | 結果 | 実測値 |
| --- | --- | --- |
| TCP到達 | pass | localhost:55000へ接続成功 |
| API認証 | pass | JWT長398、値は非保存 |
| manager状態取得（別プローブ） | pass | HTTP 200、10サービスがrunning |
| `/alerts`検索 | skipped | HTTP 404、標準manager APIに同パスなし |
| 実アラートファイル取り込み | pass | 187 events / 187 rows / 0 skipped |

[スモーク出力](wazuh-live-20261108-results/live-smoke.txt)、[状態取得と404応答の要約](wazuh-live-20261108-results/wazuh-api-observations.json)。現在の`/var/ossec/logs/alerts/alerts.json`は0バイトだったため、ローテーション済みの`2026/Nov/ossec-alerts-07.json.gz`を読み取った。取り込み元は実ログであり、生成したテスト用アラートへの置換はしていない。一時的な生ログのコピーは確認後に除去した。

初回スモークは同じ404を`fail`として終了コード1だった。この失敗を[修正前出力](wazuh-live-20261108-results/live-smoke-before-capability-fix.txt)として保持する。スモーク判定の修正後も検索成功とは扱わず、`skipped`の理由にIndexer検索未検証と記録する。従来文書の「500・Indexer停止」「実アラート2件」「JWT長256」は今回の環境では再現しておらず、現在の確認済み事実には用いない。

設置済みの4.9.2 API仕様は142パスを持ち、`/manager/status`は存在するが`/alerts`は存在しなかった。[仕様確認の要約](wazuh-live-20261108-results/wazuh-api-spec-metadata.json)。アラートを`wazuh-alerts*`から検索する方法は[Wazuh公式のIndexer API資料](https://documentation.wazuh.com/current/user-manual/indexer-api/use-case.html)にある。Indexer検索の接続実装や検証は今回行っていない。

## Blue経路と制限

`ingest_alerts_file`は正規化したアラートを`timestamp`・`method`・`path`・`status`・`role`の5項目へ変換する。`rows_from_jsonl`で許可外キーを拒否し、既存の`stage_worker(root, role="blue", telemetry=rows)`へ渡す経路を使える。今回の実接続スモークは取り込みと行生成までで、新たなBlueの実モデル実行や検知性能の評価は行っていない。

APIクエリの失敗からファイル取り込みへの自動切り替えはない。ファイルは操作者が`--alerts-file`で明示指定する。製品の`query_alerts`は404でも例外を返し、固定値へ置き換えない。

GLM・DeepSeekのOllama cloud呼び出しは月間利用枠の429で停止し、DeepSeekサブエージェントも現在のアカウントでは利用できなかった。ユーザーの続行承認により、この追補の最小修正・検証・文書更新・commit/pushをCodexが直接担当する。比較実験や新規の実モデル検証は今回の範囲外。

日本語の更新文にはYomiyasuのlint・差分検査と最終稿照合を実行し、出力を作業証跡へ保存した。変更した数値・状態・リンクを実測結果と照合し、過去の失敗と未確認範囲を保持した。既存箇所の指摘は、履歴の通番書式、追跡文書を指す用語「正本」、診断を開始する手順を指す「入口」、比較の範囲を明確にする否定文、項目別の検収記録という役割を確認して保持した。件数の増減を読みやすさの合否とは扱わない。ソース更新のcommit・push後、完了報告にリモート確認済みのSHAを記す。新規タグ・Release・配備は行わない。
