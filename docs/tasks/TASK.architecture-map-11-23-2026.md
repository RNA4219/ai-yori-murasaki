---
task_id: 20261123-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-23
next_review_due: 2026-12-23
---

# リポジトリとシステムの構成図

## Objective

モニターから構成図を開き、リポジトリ間の依存、システムを構成する部品、指摘が付いた場所をたどる。利用者は注文管理サービスの見本を試した後、自分のファイルで図を置き換えられる。

## Requirements

- 部品を選択すると、その部品に入る線と出る線、接続理由と指摘の根拠・対応案を確認できる。構成全体、リポジトリ間、直接つながる範囲を選べる。
- 指摘は要素IDに結ぶ。ファイル内に指摘がない場合は「指摘の記録がありません」と表示する。
- ローカルファイルをタブ内で読み込み、読み込み失敗時は理由と直前の有効な図を表示する。ページを再読み込みすると見本に戻る。

## 入力ファイル

[編集用の見本](../../ai_yori_murasaki/web/architecture/sample.json)はJSON・YAMLで記述できる。いずれも文字コードはUTF-8。1ファイルは1 MiB、一度の選択は100ファイル・合計4 MiBまで。

| 項目 | 用途 |
| --- | --- |
| `schema`, `name` | 形式名 `aym.architecture/v1` と図のタイトル |
| `nodes` | 一意な `id`、`name`、`kind` が必須。種類は `repository`、`service`、`store`、`external`。任意で `type` と `description` を添える。最大100件 |
| `connections` | `from` から `to` への向きと `label` が必須。任意の `evidence` に宣言箇所などを記載する。最大300件 |
| `findings` | `node`、`severity`、`title`、`evidence`、`action`。重要度は `critical`、`high`、`medium`、`low`、`info`。最大300件 |

CloudFormationは `Resources` を読み、`DependsOn` を「作成時に依存」、リソースを指す `Ref`、`Fn::GetAtt`、`Fn::Sub` を「設定で参照」と表示する。YAMLの短縮記法にも対応する。接続方向はテンプレート上の依存方向である。マクロの展開、パラメーター・条件式の評価、実環境の状態確認は対象外。指摘はS3バージョニングの `Suspended`、DynamoDBポイントインタイムリカバリの明示的な `false`、EFS保存時暗号化の明示的な `false` がある場合に付く。[依存関係](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-attribute-dependson.html)・[S3](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-s3-bucket-versioningconfiguration.html)・[DynamoDB](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-properties-dynamodb-table-pointintimerecoveryspecification.html)・[EFS](https://docs.aws.amazon.com/AWSCloudFormation/latest/TemplateReference/aws-resource-efs-filesystem.html)

`package.json` は `name` と `repository` を使う。読み込んだパッケージ間で `dependencies`、`devDependencies`、`peerDependencies`、`optionalDependencies` の宣言名または `npm:` エイリアスを照合する。同じリポジトリのパッケージは同じ要素にまとめる。結果は宣言に基づく依存関係で、解決済みバージョンや実行時の通信を表すものではない。

## 設計と検収

Obsidianの全体図とローカルグラフの作例から、要素の選択・隣接線の強調・周辺表示を採用した。構成全体はリポジトリ・サービス・データストアに区切り、それぞれの名前と種別を図中に表示する。[ObsidianのGraph view](https://obsidian.md/help/plugins/graph)・[AWSの構成図の資料](https://aws.amazon.com/architecture/icons/)・[技術検収](../acceptance/AC-20261123-01.md)・[変更履歴](../../CHANGELOG.md)
