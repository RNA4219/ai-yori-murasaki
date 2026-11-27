---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-26
next_review_due: 2026-12-26
---

# Notion の課題 CSV と観測を照合する

課題を「修正済」にしたあとも検知が残っているかを、手元のファイルで調べられます。ここでは架空の課題を2件取り込み、1件に残っている検知を判断画面で開きます。取り込みと照合は Notion のアカウントやクラウドの接続情報を使いません。

## 入力ファイルを準備する

以下の4ファイルを同じローカルディレクトリに保存してください。Notion からエクスポートした場合は、展開したデータベースの CSV を使います。文字コードは UTF-8 で、BOM 付きにも対応します。カンマや改行を含むセルは二重引用符で囲めます。CSV の上限は1ファイル16 MiB、データ行10,000件、256列です。

**issues.csv**

```csv
タイトル,棚卸キー,ステータス,原典の環境,概要,最終更新日時
通知設定の見直し,DEMO-01,修正済,demo-east,"対象は node-27, 通知を確認",2026-01-10T11:00:00Z
手順書の更新,DEMO-02,未対応,demo-west,確認先を選ぶ,2026-01-10T11:00:00Z
```

既定では先頭列が題名、「棚卸キー」が案件内で課題を識別するキーです。キーの列がない場合は題名で識別します。同じ CSV 内のキーは重複させず、各行に値を入れてください。「原典の環境」には観測対象のアカウント名、この例なら `demo-east` を指定します。

列名や状態名が違う場合は、ワークスペースの `decision-view/mapping.json` に設定します。[既定の列と状態の対応](../ai_yori_murasaki/decision_mapping.json)をコピーし、`fields` の `title`、`key`、`status`、`scope` などに CSV の列名を指定してください。`status_values` で `done` または `wont_fix` に対応する状態を完了扱いにします。

**inventory.json** は資源一覧です。`collected_at` に取得日時と時差、`scope` に提供元・アカウント・地域、`method` に集め方を記録します。

```json
{
  "schema_version": "1.0.0",
  "kind": "inventory",
  "collected_at": "2026-01-10T10:00:00Z",
  "scope": {"provider": "example", "account": "demo-east", "regions": ["east"]},
  "method": "合成した資源一覧",
  "resources": [{"id": "node-27", "type": "node", "name": "CartWorker", "state": "running"}]
}
```

**detections.json** は検知一覧です。`resource_id` に対象の資源 ID、`status` に `active` または `resolved` を指定します。資源一覧と検知一覧はそれぞれ1ファイル16 MiBまでです。

```json
{
  "schema_version": "1.0.0",
  "kind": "detections",
  "collected_at": "2026-01-10T10:00:00Z",
  "scope": {"provider": "example", "account": "demo-east", "regions": ["east"]},
  "method": "合成した検知一覧",
  "detections": [{"id": "alert-02", "resource_id": "node-27", "rule": "NoticeEnabled",
                  "status": "active", "updated_at": "2026-01-10T09:55:00Z"}]
}
```

**context.json** の `target` は案件に登録した対象の識別子に合わせます。`target_version` は対象の版です。

```json
{"target": "demo-target", "target_version": "v1"}
```

## 案件へ取り込む

下の例は PowerShell 用です。普段と同じ `AYM_WORKSPACE` と `AYM_TRUSTED_ROOT` を設定し、4ファイルを保存したディレクトリで実行してください。`$campaign` と `$run` には対象案件と、その案件で実行中の Run の ID を設定します。

```powershell
aym assess import --format notion --input issues.csv --context context.json --campaign $campaign --run $run
aym assess import --format observation --input inventory.json --context context.json --campaign $campaign --run $run
aym assess import --format observation --input detections.json --context context.json --campaign $campaign --run $run
aym assess drift --campaign $campaign
```

課題の取り込み結果では `data.imported_count` が追加件数、`data.existing_count` が登録済みの件数です。同じ行を再送しても課題は増えません。登録済みのキーに別の内容がある場合は、CSV 全体を拒否します。更新後の課題一覧は別の案件に取り込んでください。

## 一致した根拠を開く

同じワークスペースで[モニターの TLS 証明書と鍵](PRODUCT-GUIDE.md#モニターのhttps起動)を設定して `aym monitor serve --bind 127.0.0.1:8765` を実行し、ブラウザーで `https://127.0.0.1:8765/decision?campaign=<案件ID>` を開きます。索引の更新を求められたら `aym monitor index` を実行してください。

「課題の一覧と実物のずれ」で「ずれの候補を見る」を開き、「通知設定の見直し」を選びます。この例では、課題の概要に書かれた `node-27` と資源 ID が一致します。検知 `alert-02` が `active` のため、分類は「一覧は解決、検知が残る」（`closed_active`）です。課題の最終更新より観測が古いことも表示されます。

「手順書の更新」は `demo-west` の課題です。このアカウントの観測は入力していないため、「照らせない」の「観測の範囲外」に1件と表示されます。名前の部分一致など、結び付きが推定にとどまる課題も照合できない件数に含まれます。

CLI の JSON と同じ内容を `GET /api/campaigns/<案件ID>/drift` で読めます。Blue 表示（`?view=blue`）の応答は `{"status":"withheld"}` です。API の応答上限は1 MiBです。ファイルで読むときは、ワークスペースと信頼済みルートの外を保存先に指定してください。

```powershell
aym assess drift --campaign $campaign --format md --output drift.md
aym assess drift --campaign $campaign --format csv --output drift.csv
```

分類は人が確認するための候補です。結果を読んでも課題の状態は変わりません。観測範囲や取得日時を見て、追加調査や原典での更新を判断してください。

## 利用する検査と観測範囲を指定する

たとえば、同じディスクに「暗号化」と「バックアップ」の検知があるとします。暗号化の課題を閉じたあと、その課題の解決状況は暗号化の検査だけで判断できます。次の例は、空の検証用案件で上と同じ取り込みコマンドを使います。`context.json` の対象と版を案件に合わせてください。

ワークスペースの `decision-view/mapping.json` に次の列の対応を設定します。既存の設定を使う場合は `fields` に利用する列を足し、同じ階層に `drift` を置きます。

```json
{
  "fields": {
    "status": "ステータス", "key": "棚卸キー", "scope": "対象環境",
    "provider": "提供元", "region": "対象地域"
  },
  "status_values": {"未対応": "open", "修正済": "done"},
  "drift": {
    "rule_field": "課題種別",
    "rules_by_value": {"暗号化": ["DiskEncrypted"]},
    "excluded_terms": ["共有ラベル"]
  }
}
```

**issues.csv**

```csv
タイトル,棚卸キー,ステータス,対象環境,提供元,対象地域,課題種別,概要
暗号化の確認,EX-11,修正済,example-team,example,east,暗号化,disk-0012 を確認
廃止申請の確認,EX-12,未対応,example-team,example,east,棚卸,disk-0013 を確認
```

**inventory.json**

```json
{
  "schema_version": "1.0.0", "kind": "inventory", "collected_at": "2026-01-10T10:00:00Z",
  "scope": {"provider": "example", "account": "example-team", "regions": ["east"]},
  "method": "例示用のディスク一覧", "types": ["disk"],
  "id_patterns": {"disk": ["disk-[0-9]{4}"]},
  "resources": [{"id": "disk-0012", "type": "disk", "name": "ArchiveDisk", "state": "ready"}]
}
```

**detections.json**

```json
{
  "schema_version": "1.0.0", "kind": "detections", "collected_at": "2026-01-10T10:00:00Z",
  "scope": {"provider": "example", "account": "example-team", "regions": ["east"]},
  "method": "例示用の検査結果",
  "detections": [
    {"id": "check-21", "resource_id": "disk-0012", "resource_type": "disk",
     "rule": "DiskEncrypted", "status": "resolved", "updated_at": "2026-01-10T09:50:00Z"},
    {"id": "check-22", "resource_id": "disk-0012", "resource_type": "disk",
     "rule": "BackupRecent", "status": "active", "updated_at": "2026-01-10T09:55:00Z"}
  ]
}
```

取り込み後に `aym assess drift --campaign $campaign` を実行します。「暗号化の確認」は `aligned`、使った検査は `DiskEncrypted` です。「廃止申請の確認」は `open_absent` です。提供元・アカウント・地域・収集した種類が一致し、指定した形式に合う `disk-0013` が資源一覧にないことを確認できます。

### 設定を自分のデータに合わせる

- **関係する検査**　`rule_field` は CSV の1列を指します。その列の値から `rules_by_value` で検査名のリストを引きます。列値・検査名は大文字小文字を区別して完全一致で比較します。設定を省くか、列の値に対応がなければ、該当資源のすべての検知を使い、`rules_unfiltered` が `true` になります。対応を設定した課題で該当する検知が0件でも、解決を裏付ける検知には数えません。
- **除外する資源**　観測の `scope.account` と同じ資源 ID は常に除きます。さらに `excluded_terms` に資源の ID または名前を完全一致で指定できます。追加の除外語の既定値は空です。`excluded_resource_count` は、その課題で照合候補になって除かれた資源の件数です。
- **同じ ID の別種の資源**　検知の `resource_type` は対象資源の `type` と同じ値にします。複数の種類が同じ ID を使う場合、型がない検知との結び付きは推定になります。種類を省いても ID が一意な従来の入力は照合できます。
- **資源の在否**　`types` には今回実際に収集した種類を書きます。資源が一覧にないことを確かめるには、その種類が収集対象で、課題の提供元・アカウント・地域に該当する観測範囲が一意に定まる必要があります。収集対象外や範囲不明なら JSON の `in_inventory` は `null`（不明）です。アカウント用の種類名が必要なら収集側で決めます。`scope.account` と同じ ID の除外は種類にかかわらず適用されます。一意な既知の ID は、観測がすべて従来形式なら従来どおり照合します。種類を指定した検知と従来の資源一覧を組み合わせた場合にも、不在の判定には課題の提供元・アカウント・地域の明示が必要です。
- **課題本文にだけある ID**　`id_patterns` は種類ごとの ID の書式です。例の `disk-[0-9]{4}` は、末尾が数字4桁の ID 全体に一致します。課題のアカウント・提供元・地域が明示され、観測の範囲と資源の種類を一意に特定できるときに不在を判定します。書式を省いた場合は本文だけから新しい資源を作りません。パスや ARN の一部分にも適用しません。

ID の書式は ASCII の英数字・`_`・`-`・ドットを含む識別子に使えます。ドットは書式内で `\.`（JSON 文字列内では `\\.`）と書きます。可変部分は1か所で、`[0-9]`、`[a-z]`、`[A-Z]`、`[0-9a-f]`、`[0-9A-F]`、`[a-z0-9]`、`[A-Za-z0-9]` から1つ選び、その後ろに繰り返しの `+`、`{n}`、`{n,m}` のいずれかを付けます。回数を指定する場合は1〜256です。

判断画面では候補一覧のほか「そのほかの照合結果を見る」からも課題を選べます。実際に使った検査名は `rules_used`、検査を絞ったかどうかは `rules_unfiltered` で確認できます。Markdown と CSV の表にも、この2項目と除外件数・資源の種類・在否が並びます。表の `unknown` は在否不明を表します。
