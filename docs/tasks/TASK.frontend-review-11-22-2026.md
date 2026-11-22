---
task_id: 20261122-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-22
next_review_due: 2026-12-22
---

# 判断の画面と記録モニターの改修

## Objective

結果を読む人は最初の画面で判断待ちの量と評価の限界をつかめる。調査担当者は同じ案件の記録を、案件名と最終実行日時で識別できる。[2画面の製品要件](../requirements/REQUIREMENTS-DUAL-VIEW.md)と[モニターの設計](../requirements/MONITOR-REDESIGN.md)に基づく。

## Requirements

| 利用場面 | 改修前に確認した問題 | 受入条件 |
| --- | --- | --- |
| 案件を選ぶ | モニターでは内部IDが主見出しで、390px幅では検知件数が横にはみ出した | 目的名、状態、検知件数、最終実行を横スクロールせずに読める |
| 判断する | 件数、内訳、前回比較、再読込が同じ順序と強さで並んだ | 判断待ち、未完了の総数、検査範囲を先頭に置く。課題行を開くと理由と次の行動に進める |
| 課題を探す | 検索欄は長い一覧の下にあり、先頭から直接移れなかった | 件数付きの「すべての課題」から、絞り込みを解除した一覧へ移れる |
| 証跡を読む | モデルと接続先の表が承認記録より先に並んだ | 案件と承認記録を先に置き、接続先の詳細を開ける。更新で開閉状態を保つ |
| 再読込する | 保留・非公開の応答でも前回の課題が表示された | 保留・非公開では件数と一覧を閉じる。通信失敗で前回の内容を表示するときは古い記録であることを示す |

## 設計の根拠

判断画面は藤色の紙面と明朝見出しを使い、件数・課題名・開閉の操作を別々に読めるようにした。モニターは白地と罫線を使い、同じ属性が縦に揃う台帳にした。両画面は共通の紫の見出し帯で切り替える。画像・フォントの外部配信を必要としない。[anything-frontend-art-direction](https://github.com/RNA4219/anything-frontend-art-direction)

GOV.UKの実例では、項目名と状態が同じ行にあり、必要な説明だけが項目名の下に入る。この構成を案件の一覧に採用した。完了までの手順を示す意味は移植していない。[Task listの解説](https://design-system.service.gov.uk/components/task-list/)・[制作元の実例](https://design-system.service.gov.uk/components/task-list/default/)

Motion AtlasのAccordionを参考に、課題を開いた行で矢印と本文が短く動く。キーボードでは標準の開閉操作を使い、動きを減らす設定では即時に内容が出る。[Motion Atlasの作例](https://github.com/RNA4219/motion-atlas/blob/main/src/lab-ui.js)

## Scope

変更対象は同梱の製品HTMLと2画面のブラウザー回帰試験。配信は既存の読み取り専用APIを使う。基点は main の 3b6f744。指定モデルの月間上限を確認し、利用者の承認を受けて今回はCodexが設計・実装・レビューを担当した。[検収記録](../acceptance/AC-20261122-01.md)・[変更履歴](../../CHANGELOG.md)
