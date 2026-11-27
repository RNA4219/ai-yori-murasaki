---
task_id: 20261127-07
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-27
next_review_due: 2026-12-27
---

# モニターの HTTPS 起動と再診断で確認した5箇所の修正

R-091 では、ループバックのモニターを TLS 1.2 以上で配信する。通常起動にはサーバー証明書と対応する秘密鍵を必要とし、SSO を使わないローカル確認に限り、HTTP を明示的に選べるようにする。Google ログインも HTTPS の戻り先を使い、認証用の Cookie に `Secure` を付ける。[製品操作ガイド](../PRODUCT-GUIDE.md#モニターのhttps起動)・[Google ログインの設定](../requirements/GOOGLE-MONITOR-SSO.md)

T63 は合成証明書・ローカルの認証サービスで CLI、モニター4画面と API、ログアウト、失効後のアクセスを確認する。[技術検収](../acceptance/AC-20261127-07.md)・[CLI と HTTP の契約](../requirements/API-CONTRACTS.md)・[変更履歴](../../CHANGELOG.md)

## 再診断の対象と修正

- **Blue 表示**　モニターが資料・実行履歴を取得するときも、元の画面の `view=blue` を引き継ぎ、各 API の Blue 向け投影を使う。
- **Obsidian ノート**　vault 配下のどの階層でも `.obsidian` という名前のフォルダーを、大文字と小文字を区別せずに除外する。通常の Markdown は引き続き取り込める。[資料の取り込み](../SOURCES.md)
- **実行台帳のイベント**　モニターからイベントを読むときは台帳の整合性を確認する。整合しない記録を返さず、有効な従来の台帳と初回記録前の実行を区別して読めるようにする。[CLI・HTTP 契約](../requirements/API-CONTRACTS.md#22-endpoints)
- **LLM への HTTP 通信**　応答をチャンク分割して受信するときも、設定した応答サイズの上限を適用する。
- **案件受付の SQLite と PyO3**　受付台帳を読む経路を、更新したネイティブ拡張の SQLite にそろえる。依存を `rusqlite 0.41.1`、`libsqlite3-sys 0.39.1`（同梱 SQLite 3.54.1）、`PyO3 0.29.3` に固定する。PyO3 の該当 API を製品から呼び出す経路は未確認。[SQLite に関する勧告](https://rustsec.org/advisories/RUSTSEC-2026-0459.html)・[PyO3 に関する勧告 0176](https://rustsec.org/advisories/RUSTSEC-2026-0176.html)・[PyO3 に関する勧告 0177](https://rustsec.org/advisories/RUSTSEC-2026-0177.html)

## 検収結果

製品コミット `b6c5895b8325b435ee3bb220e2dd0b4180bcf170` で正式 manual-bb の strict 検収13件と回帰31件がすべて通った。Windows の受付台帳は、初回登録を4並行で25回実施し、100件の呼び出しが成功した。各台帳に残った受付は1件だった。独立レビュー7項目を完了し、未解決の blocker・critical は0件。実運用の証明書と Google アカウントによる接続は未検証。[技術検収の結果](../acceptance/AC-20261127-07.md)・[PR #25](https://github.com/RNA4219/ai-yori-murasaki/pull/25)
