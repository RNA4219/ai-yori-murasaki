---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# 資料の取り込みと出典リンク

Obsidianのノート、Notionのページ、GitHub Issueを、操作者用の参考資料として取り込める。本文と出典リンク、任意の関連リンクを保存し、モニターの「資料と出典」で確認する。外部サービスへの書き込みや自動同期は行わない。

資料は常に `reference_only` として保存する。Finding、検知の証拠、Oracleの正解、RSIの経験データへ自動変換しない。出典リンクは操作者が指定した所在であり、その内容の正しさやMCP応答との同一性を保証するものではない。

## Obsidianのノート

通常の起動と同じ `AYM_WORKSPACE` と `AYM_TRUSTED_ROOT` を設定して実行する。取り込みだけならDockerやLLMは不要。

```powershell
aym sources import-obsidian --vault 'C:/Notes' --note '調査/認可.md' --link 'https://example.com/spec#authorization'
aym sources list
aym sources show --document-id '<listが返したdocument_id>'
aym monitor serve --bind 127.0.0.1:8765
```

`--vault` は絶対パス、`--note` はvault内の相対Markdownパスを指定する。UTF-8の本文を読み、`obsidian://open?vault=...&file=...` を出典にする。同名vaultが複数ある場合は、Obsidian側で目的のvaultが開くかを確認する。vault外参照、`.obsidian`、symlink／reparse pointは拒否する。

## 保存済みのテキスト

NotionやGitHubから保存したUTF-8のMarkdown／テキストも、元ページのリンクを付けて取り込める。

```powershell
aym sources import-file --provider github --file './issue.md' --source-url 'https://github.com/OWNER/REPO/issues/123' --title '認可の調査' --link 'https://example.com/design' --link 'https://example.com/test'
```

`--provider` は `obsidian`、`notion`、`github`。`--link` は最大32件まで繰り返せる。リンクはHTTPSまたはObsidianのノート用URLに限る。認証情報をURLへ含めない。本文はプレーンテキストで表示し、HTMLや埋め込みスクリプトを実行しない。

## MCPによる読み取り

追加依存をインストールし、操作者が認可したMCPサーバーの接続設定とツール引数をJSONファイルに用意する。

```powershell
py -3 -m pip install '.[mcp]'
aym sources import-mcp --provider github --config-file './github-mcp.json' --tool issue_read --arguments-file './issue-arguments.json' --source-url 'https://github.com/OWNER/REPO/issues/123' --title '認可の調査'
```

GitHubのStreamable HTTP接続設定例。`AYM_GITHUB_MCP_TOKEN` には操作者が利用権限を持つ認証値を設定する。設定ファイルに保存するのは環境変数名だけにする。

```json
{"transport":"http","url":"https://api.githubcopilot.com/mcp/readonly","bearer_env":"AYM_GITHUB_MCP_TOKEN"}
```

`issue-arguments.json` の例。

```json
{"method":"get","owner":"OWNER","repo":"REPO","issue_number":123}
```

Notionは `https://mcp.notion.com/mcp` のHTTP接続と、`fetch` または `notion-fetch` を使う。引数の形は接続先の公開schemaに合わせる。Notion MCPの認可済みbearer値、または既に認証されたstdioブリッジが必要。通常のNotion REST integration tokenをMCPの認証値として扱うことは保証しない。本機能にはブラウザーでのOAuthログインやtoken更新の画面はない。

stdio接続では、認可したローカルコマンドの `command` と `args` を指定する。`env` は「子プロセス側の変数名: 操作者側の環境変数名」の対応表にする。接続設定は実行可能なコマンドを指定するため、信頼できる操作者が管理し、取り込んだ資料から生成して実行しない。

```json
{"transport":"stdio","command":"github-mcp-server","args":["stdio","--read-only"],"env":{"GITHUB_PERSONAL_ACCESS_TOKEN":"AYM_GITHUB_MCP_TOKEN"}}
```

許可するツールは次に限定する。サーバーが別名や別schemaを使う場合は、許可リストを迂回せず、保存済みテキストの取り込みを使う。

| 情報源 | 許可する読取ツール |
| --- | --- |
| GitHub | `issue_read`（`method=get/get_comments`のみ）、`get_issue`、`get_issue_comments` |
| Notion | `fetch`、`notion-fetch`、`API-retrieve-a-page`、`API-get-block-children` |
| Obsidian | `read_note`、`read_file`、`get_file_contents` |

HTTPはHTTPS、または明示指定されたlocalhost／127.0.0.1へのHTTPに限定する。1回の取り込み上限は30秒、本文256KiB、ツール引数16KiB。ページ本文とコメント等をまとめる自動巡回は行わず、指定したツールの1応答を保存する。認証値と接続設定は資料へ記録しない。渡した認証値が本文に現れた場合はマスクする。サーバーの安全性をツール名だけで保証するものではない。

## 保存と表示

同じprovider・出典URLの資料は同じIDになる。同じ内容の再取り込みは `unchanged`、本文・タイトル・リンクの変更は `updated`。更新前のCAS参照を `previous_ref` に残す。出典URLの変更は別資料になる。最大1000資料を保存できる。

保存内容はprovider、出典URL、タイトル、本文、関連リンク、取得時刻、本文を含む内容hash、取得方法、過去版の参照。operator用のGET APIとCLIから読める。Blueには本文・件数・存在を公開せず、既存のネットワーク隔離も維持する。資格情報以外の本文に含まれる個人情報等は、取り込み前に操作者が確認する。

白基調のモニターは本文18px、補助文・表・ボタン16px以上、見出し24pxを基本とする。資料一覧で出典、詳細で本文と関連リンクを開ける。

## 確認範囲と原典

今回の検証は、ローカルSDKサーバーへのstdio／HTTP接続、ファイル取り込み、保存と表示、権限境界まで。実際の利用者vaultとNotion／GitHubアカウントへの接続は、認証・接続設定がないため未実行。[検収](acceptance/AC-20261109-02.md)と[証跡](evidence/sources-light-ui-20261109/README.md)に区別して記録する。

- [Obsidianの保存形式](https://help.obsidian.md/data-storage)
- [GitHub MCPの接続設定](https://github.com/github/github-mcp-server/blob/main/docs/server-configuration.md)
- [Notion MCPの接続](https://developers.notion.com/guides/mcp/get-started-with-mcp)と[対応ツール](https://developers.notion.com/guides/mcp/mcp-supported-tools)
- [MCP公式Python SDK](https://github.com/modelcontextprotocol/python-sdk)（追加依存は2.3.0に固定）
