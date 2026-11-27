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

利用者を個別に指定するときは、Google がアカウントごとに発行する `sub` を使う。メールアドレスを変更しても `sub` は変わらない。ドメイン単位で許可する場合は、この手順を省いて次の「利用者の許可範囲を選ぶ」へ進む。[Google の ID トークンの説明](https://developers.google.com/identity/openid-connect/openid-connect#obtainuserinfo)

管理している既存の Google ログインで署名・発行元・宛先を検証済みなら、そのアカウントの `sub` を使える。自分のアカウントを初めて登録する場合は、モニターの起動前に Google の OAuth 2.0 Playground でも確認できる。

1. Google 公式の Playground を開き、設定で OAuth endpoints を Google、Access type を Online にする。Playground の既定クライアントを使い、AYM 用のシークレットは手元に保管する。[Google OAuth 2.0 Playground](https://developers.google.com/oauthplayground/)
2. Step 1 のスコープ入力欄に `openid profile` を指定し、許可する本人のアカウントを選ぶ。Step 2 で認可コードをトークンに交換する。
3. Step 3 で `GET https://openidconnect.googleapis.com/v1/userinfo` を実行する。Google が返した JSON から `sub` の値だけを控える。認可コード・トークン・応答全文を設定や共有資料に貼り付ける必要はない。[Google の UserInfo 手順](https://developers.google.com/identity/openid-connect/openid-connect#obtaininguserprofileinformation)・[Google の公開エンドポイント一覧](https://accounts.google.com/.well-known/openid-configuration)

## R-090　利用者の許可範囲を選ぶ

モニターを起動する PowerShell で、次の3通りから1つを選んで実行する。

**個別のアカウントを許可する**　確認した `sub` をカンマで区切って入力する。メールに関する追加の同意は求められない。

```powershell
Remove-Item Env:AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS -ErrorAction SilentlyContinue
$env:AYM_GOOGLE_ALLOWED_SUBJECTS = Read-Host '許可する Google sub（カンマ区切り）'
```

**ドメイン内の利用者を許可する**　次の例では `example.com` または `example.org` に所属すると確認できたアカウントでログインできる。個別アカウントのリストは環境変数ごと外す。

```powershell
Remove-Item Env:AYM_GOOGLE_ALLOWED_SUBJECTS -ErrorAction SilentlyContinue
$env:AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS = 'example.com,example.org'
```

**ドメイン内の特定のアカウントだけを許可する**　両方の環境変数を設定する。許可された `sub` を持ち、かつ指定したドメインのいずれかに所属する利用者だけがログインできる。

```powershell
$env:AYM_GOOGLE_ALLOWED_SUBJECTS = Read-Host '許可する Google sub（カンマ区切り）'
$env:AYM_GOOGLE_ALLOWED_EMAIL_DOMAINS = 'example.com,example.org'
```

いずれのリストも未設定の場合、SSO は起動しない。使わない環境変数は削除する。空文字、末尾のカンマなどによる空項目、不正なドメインがあると、もう一方のリストを設定していてもエラーになる。

### ドメインの照合

ドメインは `example.com` のように、ドットで2つ以上の部分をつないだ ASCII の DNS 名で指定する。大文字と小文字は同一とし、名前全体が一致するものを許可する。`example.com` だけを指定した場合、`team.example.com` は対象外で、許可するにはその名前もリストに加える。ワイルドカード、先頭の `@`、末尾のドット、IP アドレスは指定できない。

Google Workspace では、Google が署名した ID トークンを検証したうえで、`email_verified` が真であり、メールアドレスのドメインと Google が示す所属ドメイン（`hd`）が同じ許可名に一致することを確かめる。ドメインの別名（エイリアス）は自動で同一視しない。たとえばメール側が `example.org`、`hd` が `example.com` なら、両方をリストに入れても通らない。Workspace に属さない外部メールの Google アカウントは、メール確認済みでもドメイン条件を満たさない。[Google による ID トークンの検証](https://developers.google.com/identity/gsi/web/guides/verify-google-id-token)

ドメインだけで `gmail.com` を許可すると、一般の Gmail 利用者全員が対象になる。個人用 Gmail を特定の人に限る場合は、その人の `sub` で絞る。Gmail でも `email_verified` が真であることを確認する。`hd` が付いている場合は `gmail.com` との一致も必要になる。

### 3. モニターを起動する

許可リストと通常のモニターの `AYM_WORKSPACE`、`AYM_TRUSTED_ROOT` を設定した PowerShell で、クライアントの情報を入力して起動する。[作業用ディレクトリの設定](../../README.md#起動)

```powershell
$env:AYM_GOOGLE_CLIENT_ID = Read-Host 'Google クライアント ID'
$env:AYM_GOOGLE_CLIENT_SECRET = [System.Net.NetworkCredential]::new('', (Read-Host 'クライアント シークレット' -AsSecureString)).Password
aym monitor serve --bind 127.0.0.1:8765 --google-sso
```

ブラウザーで `http://127.0.0.1:8765/` を開いてログインする。たとえば `/decision?view=blue` から開いた場合、認証後は元の Blue 画面に戻り、その画面に適用される表示制限も維持される。許可リストを変更する場合はモニターを停止し、環境変数を更新してから起動し直す。

### ログアウトと再試行

4画面の「ログアウト」を押すとモニターのセッションが終了する。ブラウザーの Google アカウントには引き続きログインしているため、再度入る際は選択したアカウントを確認する。Google の同意画面でキャンセルした場合や許可対象外のアカウントを選んだ場合は、モニターのログイン画面からやり直せる。

ログインの開始からコールバックまでは5分以内に完了する。モニターのセッションはログインから最長1時間で、Google の ID トークンが先に失効する場合はその有効期限まで。サーバーを停止するとセッションは消える。

モニターが要求するスコープは、個別の `sub` のみなら `openid profile`、ドメインを指定した場合は `openid email`。認可コードの交換には PKCE（S256）を使う。開始したブラウザーとコールバックを `state` で照合し、ID トークンの署名、発行元、宛先、有効期限、ログイン要求に対応する `nonce` と設定した許可条件を確認してからセッションを発行する。[CLI・HTTP の契約](API-CONTRACTS.md#21-基本)

## T61　検証範囲

合成アカウントを用意し、CLI から起動したローカルモニターで4画面へのログイン、Blue の表示、ログアウトと期限切れ後の再認証を確認する。実際に Google 側で登録したクライアントと実アカウントでの同意は別の確認項目とする。[技術検収 T61](../acceptance/AC-20261127-04.md)

T62 では個別指定、ドメイン指定、両方の指定での許可と拒否を、署名済みの合成 ID トークンとモニターの画面から確認する。[ドメイン許可の技術検収 T62](../acceptance/AC-20261127-05.md)
