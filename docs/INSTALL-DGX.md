# 認証付きOpenAI互換LLMの導入

環境固有のホスト・アカウント・秘密情報はリポジトリに含めない。接続先やモデルは手元の環境変数で指定する。モデルの接続先がOpenAI互換Chat Completions APIを提供することが前提。

## 設定

端末の実値で設定する（以下はダミーの例であり接続先ではない）。

```sh
export AYM_LLM_URL='https://llm.example.test/v1/chat/completions'
export AYM_LLM_MODEL='example-model'
export AYM_LLM_KEYCHAIN_SERVICE='your-local-service-name'
export AYM_LLM_KEYCHAIN_ACCOUNT='your-local-account-name'
python3 tools/aym_dgx.py lab demo
```

macOS Keychainを使わない環境では、`AYM_LLM_API_KEY`をプロセス環境に渡すこともできる。ただしシェル履歴、CIログ、プロセス環境の取り扱いに注意し、リポジトリ・設定ファイルに書き込まない。Keychainを使う場合、設定したservice/accountのgeneric passwordをmacOSの`security`コマンドで読む。認証なしのLLM接続はループバックのみに限定する。

設定可能な値：`AYM_LLM_MAX_TOKENS`（1〜131072）、`AYM_LLM_TIMEOUT_MS`（1〜300000ミリ秒）、`AYM_LLM_REASONING_EFFORT`（low/medium/high）。必要に応じて`AYM_RUNTIME_ROOT`でローカル保存先を変更する。生成上限とコンテキスト長は別の値。サーバーの実上限はモデル側で別途確認する。

## セキュリティ

- 通常のOllama起動は環境変数未設定時に維持する。
- `lab` / `rsi` 実行時に、指定URL・モデル・認証を読み込み、OpenAI互換形式で送る。明示のrole routes-file設定はモデル全体の既定より優先する。
- 生成したbroker policyは設定した宛先、ポート、Chat CompletionsのPOSTだけを許可する。既存のbroker policyが異なる場合は起動を中止する。接続先やDNSが変わったら手動確認後、ローカルのpolicyを再生成する。
- runtimeの`workspace` / `trusted` / `staging` / `config` / `logs` はリポジトリ外に作る。Keychainトークン値は構成ファイルには保存しない。証跡用Storeには伏せ字対象を登録するが、外部のログ・ダンプ・環境収集までを保証するものではない。
- `--policy`で明示した別のポリシーは利用者自身で安全性を確認する。

## 検証

```sh
python3 -m pytest -q tests/test_llm_remote_config.py
```

実接続、Keychain、Dockerラボ、モニターについては実行環境で別途検証する。テスト未実施の項目を検収済みとはしない。
