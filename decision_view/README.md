# 判断の画面（AYMの2画面構成）

2026-11-16 / 適用: [R-064〜R-070](../docs/requirements/REQUIREMENTS-DUAL-VIEW.md)・[SPEC-DUAL-VIEW](../docs/requirements/SPEC-DUAL-VIEW.md)。

AYMには、結果を判断する人向けの**判断の画面**と、実行・証拠を詳しく調べる人向けの**モニター**があります。両方とも既存のAYM monitor serveと同じloopbackサーバーに置き、読み取りだけで行き来します。判断画面に承認・実行・編集・Run/RSIの管理機能は設けていません。

## 起動と画面

ルートの[README](../README.md)の起動手順に従い、AYM monitorを起動します。画面用の追加ポートやコマンドは不要です。

| URL（127.0.0.1:8765） | 内容 |
| --- | --- |
| / | モニターのCampaign一覧 |
| /#/campaign/＜ID＞ | モニターのCampaign詳細 |
| /#/run/＜ID＞ | モニターのRun詳細 |
| /decision | 判断の画面のCampaign選択 |
| /decision?campaign=＜ID＞ | 指定Campaignの判断の画面 |

どちらにも先頭に切り替えがあり、CampaignとRunから切り替えた場合は対象Campaignを保ちます。モニターの一覧や資料からはCampaign選択へ移ります。戻る操作は同じタブの通常のページ遷移です。

## 判断の画面

- **先頭の文**: 「いま決めることは N 件、終わっていない課題は全部で M 件です」など。**進捗率と割合の帯は出しません**。
- **いま決めること**: KEV収録済み、評価「確認済み」「修正提案」「再検証失敗」、未評価で元の状態が「判断待ち」の課題。最初は10件。
- **早めに対応すること**: 上記以外で、重大/高かつ期限切れ、またはEPSSが基準以上。最初は5件（設定で変更可）。
- **前回から変わったこと**: 片付いた課題、状態が変わった課題、新規の課題、今回の記録にない課題の件数。
- **すべての課題**: 検索・絞り込み・続きを表示。
- **言葉の説明**: KEV/EPSSや判断の基準。

評価レポートが対象課題を含む場合はモニターと同じ評価状態（確認済み、候補、反証済み、再検証済み、再検証失敗、修正提案、判定不能、古い証跡、未確認）および同じ次の行動を使います。再検証済みと反証済みだけ完了側に数えます。本番への反映や承認は別途確認します。再検証に失敗したときは、モニターの評価欄で失敗した検査を確かめ、修正内容を見直せます。

評価がない場合は「未評価」を出し、元の状態のdone/wont_fixのみ完了側に数えます。それ以外（読めないものを含む）は未完了側です。古い評価は全部「古い証跡」とし、完了扱いしません。未実施/失敗の類型があればその件数と注意を出し、安全だと誤解させない文にします。並びは判断補助であり業務の優先度ではありません。

識別子・内部の状態値は課題の「技術の詳細」折りたたみに表示し、詳しい調査はモニターへ案内します。

## 読み取り専用 API

| method/path | 結果 |
| --- | --- |
| GET /api/campaigns/＜ID＞/decision-view | 正常200、decision_viewオブジェクトを返す |
| 同じAPIのview=blue | 200、status=withheld。Campaignの存在も開示しない |
| Campaignなし | 404 |
| 保存データ・評価・列の対応が破損 | HTTP 200、status=hold |
| 応答1MiB超 | 500 |
| POSTなど | 405 |

課題はfinding_idで重複除去し、評価はassessment.load_reportから投影します。KEV/EPSSは評価レポートを優先します。元の証拠やログ・プロンプトの全文は出しません。保存内容も書き換えません。

列名の対応はWorkspaceの decision-view/mapping.json があればそれを使用します。ファイルがない場合だけ同梱のdecision_mapping.jsonにフォールバックします。破損・リンク・1MiB超の場合はholdです。

## 以前のスタンドアロン運用

以前の書き出しCLIは decision_view/tools/aym_decision_view.py として残し、JSON生成処理を ai_yori_murasaki/decision_view.py に一本化しています。引数は旧版と互換です（campaign / previous / map / out / workspace / trusted）。出力したJSONは、静的HTTPサーバーの同じ配信元にHTMLと置き、ファイル選択やdataクエリで読み込めます。

**HTMLの正本は ai_yori_murasaki/web/decision.html の1か所**です。以前の decision_view/web/decision.html は削除しました。見本JSONは配布するHTMLの隣に置けます。AYM外でcampaignクエリを指定してもAYMのAPIは呼びません。

## 検証

Python: python -m pytest -q tests/ decision_view/tests/test_aym_decision_view.py

JavaScript: node --test decision_view/tests/*.mjs

実ブラウザー: GitHub Actionsのdual-view validationでChromiumによる1440px/390pxの表示と切り替え、本文、技術の詳細を確認します。

[Task](../docs/tasks/TASK.dual-view-implementation-11-16-2026.md) / [Acceptance](../docs/acceptance/AC-20261116-02.md)。

以前の独立プレビューの実測は[AC-20261116-01](../docs/acceptance/AC-20261116-01.md)に残します。今回の統合版は別のAcceptanceで管理します。
