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

同じワークスペースで `aym monitor serve --bind 127.0.0.1:8765` を実行し、ブラウザーで `http://127.0.0.1:8765/decision?campaign=<案件ID>` を開きます。索引の更新を求められたら `aym monitor index` を実行してください。

「課題の一覧と実物のずれ」で「ずれの候補を見る」を開き、「通知設定の見直し」を選びます。この例では、課題の概要に書かれた `node-27` と資源 ID が一致します。検知 `alert-02` が `active` のため、分類は「一覧は解決、検知が残る」（`closed_active`）です。課題の最終更新より観測が古いことも表示されます。

「手順書の更新」は `demo-west` の課題です。このアカウントの観測は入力していないため、「照らせない」の「観測の範囲外」に1件と表示されます。名前の部分一致など、結び付きが推定にとどまる課題も照合できない件数に含まれます。

CLI の JSON と同じ内容を `GET /api/campaigns/<案件ID>/drift` で読めます。Blue 表示（`?view=blue`）の応答は `{"status":"withheld"}` です。API の応答上限は1 MiBです。ファイルで読むときは、ワークスペースと信頼済みルートの外を保存先に指定してください。

```powershell
aym assess drift --campaign $campaign --format md --output drift.md
aym assess drift --campaign $campaign --format csv --output drift.csv
```

分類は人が確認するための候補です。結果を読んでも課題の状態は変わりません。観測範囲や取得日時を見て、追加調査や原典での更新を判断してください。
