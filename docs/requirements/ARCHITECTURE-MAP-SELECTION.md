---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: approved
last_reviewed_at: 2026-11-27
next_review_due: 2026-12-27
---

# ワークスペースに置いた構成図を切り替える

## R-086　複数の構成図を選ぶ

モニターの「システム構成図」で、担当者がワークスペースに用意した図を、同梱の見本・製品自身の図と切り替えられるようにする。選んだ図を URL に残し、再読み込み後も同じ図を開く。

構成図切替の原案での番号は R-081／T53 である。[案件判断の R-081〜R-085](REQUIREMENTS-CASE-DECISIONS.md) と区別するため、製品では R-086／T58 として扱う。

## 図を用意して開く

1. モニターが使うワークスペース直下に `architecture` ディレクトリを作る。
2. 次の JSON を UTF-8 で `architecture/dispatch.json` に保存する。もう1枚はファイル名を `inventory.json` にし、構成名 `配送受付` を `在庫照会` に変えて保存する。

   ```json
   {
     "schema": "aym.architecture/v1",
     "name": "配送受付",
     "nodes": [{"id": "desk", "name": "窓口", "kind": "service"}],
     "connections": []
   }
   ```

3. 同じワークスペースで[モニターの TLS 証明書と鍵](../PRODUCT-GUIDE.md#モニターのhttps起動)を設定し、`aym monitor serve --bind 127.0.0.1:8765` を起動する。モニターで「システム構成図」を開き、「図」で「配送受付」を選ぶ。アドレスが `/architecture?map=dispatch` になり、再読み込みしても「配送受付」が開く。
4. 「図」で「製品自身の図」を選ぶと `?system=aym`、見本に戻ると `/architecture` になる。未保存の編集があるときは切替前に確認でき、キャンセルすると表示・選択・編集・アドレスを保つ。「構成ファイルを開く」で手元のファイルを開くと、ワークスペースの図を指定するパラメーターは外れる。

一覧の対象は `architecture/` 直下にある `<ID>.json` という名前の通常ファイルで、1枚の上限は1 MiB。ID の先頭には英小文字か数字を使い、2文字目以降はハイフンも使用できる。最大64文字。リンク、読み取れない JSON、`nodes` 配列を欠く文書は一覧から除く。条件に合う先頭100枚をファイル名順に表示し、表示名には `name` の先頭200文字を使う。JSON に書き出すときは元の有効な `name` を全文保存する。名前を省くと ID が表示される。

図が1枚もないときは選択欄を表示せず、既存の見本を開く。存在しない ID をアドレスで指定したときは理由を知らせて見本を開く。Blue ビューでは作業場所の図の選択欄を表示せず、一覧・図の API も非公開とする。画面上の切替や編集でワークスペースの原本は更新されない。

## API で確かめる

上記2枚を用意した場合、次の操作で一覧・原本・Blue の応答を確認できる。

```powershell
Invoke-RestMethod 'https://127.0.0.1:8765/api/architecture/maps' | ConvertTo-Json -Depth 3
Invoke-RestMethod 'https://127.0.0.1:8765/api/architecture/maps/dispatch' | ConvertTo-Json -Depth 4
Invoke-RestMethod 'https://127.0.0.1:8765/api/architecture/maps?view=blue'
```

一覧には `dispatch`、`inventory` の順で ID と表示名が返る。個別取得は保存した構成図の JSON を返し、Blue の両 API は `{"status":"withheld"}` を返す。HTTP ステータスは [API 契約](API-CONTRACTS.md#22-endpoints) に記す。

## T58　構成図の取得と切替

合成した2枚の図を通常のモニターから開く。一覧・個別取得、切替と再読込、編集中のキャンセル、不明 ID と空一覧、Blue、幅375 px、操作後の原本保存状態を確認する。[7項目の検収](../acceptance/AC-20261127-02.md)
