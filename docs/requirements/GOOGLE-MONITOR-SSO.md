---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-27
next_review_due: 2026-12-27
---

# モニターを Google ログインで開く

## R-089　ログインを有効にする

`aym monitor serve --google-sso` で起動すると、モニター、判断の画面、システム構成図、案件受付の4画面と、同じサーバーのデータ API・配信ファイルにログインが必要になる。通常の `aym monitor serve` は引き続き Google の設定なしで使える。

対象は自分の PC の `127.0.0.1` で使うモニターで、同じホスト上で動かす他の HTTP サービスも信頼できることを前提とする。ブラウザーの Cookie はポートだけでは分離されない。

### 1. クライアントと戻り先を登録する

1. モニター用の Python 環境に AYM の任意依存 `sso` を追加する。ソースから導入する場合は、リポジトリ直下で `python -m pip install ".[sso]"` を実行する。
2. Google Cloud の対象プロジェクトで認証対象のユーザーと同意画面を設定し、種類が「ウェブ アプリケーション」の OAuth クライアントを用意する。
3. 承認済みのリダイレクト URI に `http://127.0.0.1:8765/auth/google/callback` を登録する。別のポートで使う場合は登録値と起動時の `--bind` のポートを合わせる。ホスト名、スキーム、末尾のスラッシュまで照合される。[Google の OpenID Connect 設定手順](https://developers.google.com/identity/openid-connect/openid-connect#settingup)

### 2. 最初の利用者の識別子を確認する

AYM は Google がアカウントごとに発行する `sub` を許可リストと照合する。メールアドレスを変更しても `sub` は変わらない。管理している既存の Google ログインで署名・発行元・宛先を検証済みなら、そのアカウントの `sub` を使える。[Google の ID トークンの説明](https://developers.google.com/identity/openid-connect/openid-connect#obtainuserinfo)

自分のアカウントを初めて登録する場合は、モニターの起動前に Google の OAuth 2.0 Playground でも確認できる。

1. Google 公式の Playground を開き、設定で OAuth endpoints を Google、Access type を Online にする。Playground の既定クライアントを使い、AYM 用のシークレットは手元に保管する。[Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)
2. Step 1 のスコープ入力欄に `openid profile` を指定し、許可する本人のアカウントを選ぶ。Step 2 で認可コードをトークンに交換する。
3. Step 3 で `GET https://openidconnect.googleapis.com/v1/userinfo` を実行する。Google が返した JSON から `sub` の値だけを控える。認可コード・トークン・応答全文を設定や共有資料に貼り付ける必要はない。[Google の UserInfo 手順](https://developers.google.com/identity/openid-connect/openid-connect#obtaininguserprofileinformation)・[Google の公開エンドポイント一覧](https://accounts.google.com/.well-known/openid-configuration)

### 3. モニターを起動する

通常のモニターで使う `AYM_WORKSPACE` と `AYM_TRUSTED_ROOT` を設定した PowerShell で、次のコマンドを実行する。許可したいアカウントが複数あれば、それぞれ確認した `sub` をカンマで区切る。[作業用ディレクトリの設定](../../README.md#起動)

```powershell
$env:AYM_GOOGLE_CLIENT_ID = Read-Host 'Google クライアント ID'
$env:AYM_GOOGLE_CLIENT_SECRET = [System.Net.NetworkCredential]::new('', (Read-Host 'クライアント シークレット' -AsSecureString)).Password
$env:AYM_GOOGLE_ALLOWED_SUBJECTS = Read-Host '許可する Google sub（カンマ区切り）'
aym monitor serve --bind 127.0.0.1:8765 --google-sso
```

ブラウザーで `http://127.0.0.1:8765/` を開いてログインする。たとえば `/decision?view=blue` から開いた場合、認証後は元の Blue 画面に戻り、その画面に適用される表示制限も維持される。許可リストを変更する場合はモニターを停止し、環境変数を更新してから起動し直す。

### ログアウトと再試行

4画面の「ログアウト」を押すとモニターのセッションが終了する。ブラウザーの Google アカウントには引き続きログインしているため、再度入る際は選択したアカウントを確認する。Google の同意画面でキャンセルした場合や許可対象外のアカウントを選んだ場合は、モニターのログイン画面からやり直せる。

ログインの開始からコールバックまでは5分以内に完了する。モニターのセッションはログインから最長1時間で、Google の ID トークンが先に失効する場合はその有効期限まで。サーバーを停止するとセッションは消える。

モニターが要求するスコープは `openid profile` で、アカウントの許可判定には `sub` を使う。認可コードの交換には PKCE（S256）を使う。開始したブラウザーとコールバックを `state` で照合し、ID トークンの署名、発行元、宛先、有効期限、ログイン要求に対応する `nonce`、許可対象の `sub` を確認してからモニターのセッションを発行する。[CLI・HTTP の契約](API-CONTRACTS.md#21-基本)

## T61　検証範囲

合成アカウントを用意し、CLI から起動したローカルモニターで4画面へのログイン、Blue の表示、ログアウトと期限切れ後の再認証を確認する。実際に Google 側で登録したクライアントと実アカウントでの同意は別の確認項目とする。[技術検収 T61](../acceptance/AC-20261127-04.md)
