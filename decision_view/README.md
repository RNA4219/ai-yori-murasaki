# 判断の画面（aym-decision-view）

作成: Claude (Opus 5.5 → Fable 5.2) / 2026-11-16 / 入力: 作者の依頼「AI オーケストレータの人でなくても判断できるように丸める画面」「無骨すぎる、もう少しやわらかく」、AYM 538e228 の web/index.html（CSP の流儀と紫の色）、storage.Store・runtime.Runtime と取り込み済み finding の形。

AYM のモニターは、記録を回す人のための画面になっている。こちらは結果を読む人のための画面で、決めることを先に見せる。AYM の Campaign から判断に要る列だけを JSON に書き出し、それを 1 枚の HTML で見せる。

## 画面で見えるもの

| 場所 | 出るもの |
| --- | --- |
| 先頭の 1 枚 | 「いま決めることは 8 件です」のように、状況を文で言う。その下に、片付いた割合の帯と、前回から片付いた件数を出す |
| いま決めること | 終わっていないもののうち、悪用が確認されているものと、判断待ちのもの。急ぐ順に並べる |
| 早めに対応すること | 期限を過ぎた重大・高のものと、悪用の見込みが高いもの。最初は 5 件だけ出し、残りはボタンで開く |
| 前回から変わったこと | 片付いた・状態が変わった・新しく入った・なくなった。件数だけ出し、開くと名前が読める |
| すべての課題 | 名前や番号で探せる一覧。「終わっていない」「すべて」「片付いた」で絞れる |
| 言葉の説明 | KEV・EPSS などを 1 行ずつ説明する |

どの課題も、行を開くと急ぐ理由が文で読める（「期限（11/6）を 10 日過ぎています。」など）。元のデータの状態の言葉（「未対応」など）は、そのまま画面に出す。

## 見た目

藤色の地に白い面を置き、色は紫を 1 色だけ使う。急ぐものは、やわらかい紅と琥珀で示す。明るい画面と暗い画面の両方に色を用意してある。

文字は OS に入っている丸ゴシック（macOS ではヒラギノ丸ゴ）を使う。AYM の CSP では外部のフォントを読めないため、丸ゴシックが入っていない OS ではふつうのゴシックになる。

## 使い方

1. 判断用の JSON を作る（読み取りだけ。AYM の環境で動かす）

   ```sh
   python decision_view/tools/aym_decision_view.py --campaign <campaign_id> --previous auto \
     --map decision_view/tools/mapping.example.json --out decision.json \
     --workspace "$AYM_WORKSPACE" --trusted "$AYM_TRUSTED_ROOT"
   ```

   `--previous auto` は、intent_id の末尾の日付（`-YYYYMMDD` と英字 1 つ）を除いた部分が同じで、それより前に作られた最新の Campaign と比べる。`none` で比べない。ID を直接渡してもよい。

2. `decision_view/web/decision.html` を開いて JSON を選ぶか、画面に落とす。同じ配信元に置いたなら `decision.html?data=decision.json` でも読める。

`decision_view/web/decision.html` と同じ場所に、見本の `decision-sample.json`（架空のデータ）がある。配信して開くと「見本のデータで試す」が出る。

## AYM に組み込むとき

画面の `decision_view/web/decision.html` は、外部のファイルを読まない 1 枚の HTML。`style` 属性もインラインのイベントも使っていないので、`server._security_headers()` の CSP（インラインはハッシュで許可）のまま配れる。`_load_index_html()` と同じ読み方で、`/decision` などの経路を足せばよい。

`?campaign=<ID>` を付けて開くと、画面は `GET /api/campaigns/<ID>/decision-view` を読みにいく。この API は現行リポジトリには実装していないので、現時点では `?campaign=` は使えない。足すなら `decision_view/tools/aym_decision_view.py` の `build_view()` をそのまま使える。`build_view()` は I/O を持たない純粋な関数で、AYM に依存するのは `_load()` と `main()` だけ。`aym decision-view` のような CLI の入り口を足すなら、`main()` をそのまま使える。

試験は 2 つある。`node --test decision_view/tests/` は画面の計算を試す（HTML の `<script id="decision-core">` だけを取り出す）。`python -m pytest decision_view/tests/` は JSON の作り方を試す。

## データの形

JSON の形は `decision_view/schema/decision-view.schema.json`（`aym.decision-view/1`）に書いてある。判断に使う列だけを持つ。

`decision_view/tools/mapping.example.json` には、元の行の列名（状態・番号・期限）と、状態の言葉の対応を書く。状態は open・in_progress・waiting・done・wont_fix・other のどれかに寄せる。自分のデータベースに合わせて書き換える。

KEV・EPSS は、AYM の取り込みが付けた `extensions.enrichment.records` から読む。付いていなければ数えず、画面にその旨を出す。

## 判断の決まり（今の実装）

| 言葉 | 決まり |
| --- | --- |
| 終わっていないもの | 状態が open・in_progress・waiting |
| いま決めること | 終わっていないもので、悪用が確認されている（KEV）か、判断待ちのもの |
| 早めに対応すること | 終わっていないもののうち、上に入らず、期限を過ぎていて重大度が重大か高のもの。または、悪用の見込み（EPSS の最大値）が `epss_high` 以上（既定 0.1）のもの |
| 並び | 悪用の確認 → 判断待ち → 期限の過ぎ方（60 日で頭打ち）→ 期限が近い（`due_soon_days`、既定 7 日）→ 重大度 → 悪用の見込み |
| 期限の基準 | 画面を開いたブラウザの今日。データを作った時刻は、画面の下に別に出す |

`rules`（`epss_high`・`due_soon_days`・`top_n`）は mapping に書くと JSON に入り、画面はその値を使う。`top_n` は「早めに対応すること」を最初に何件出すかで、既定は 5。

## 作者に決めてほしいこと

- 「いま決めること」「早めに対応すること」の分け方と、並びの重み（`decision-core` の `assess()`）
- 前回の選び方（intent_id の末尾の日付で寄せている）
- `/decision-view` の API と画面の経路を足すか、足すならどこに置くか
- 画面の言葉と色（普段の言葉とやわらかい色に寄せた。AYM のモニターに合わせ直すかどうか）

## 確かめたこと

- 試験は Python 11 本、JS 13 本が通る
- AYM 538e228 の `_security_headers()` と同じ CSP を付けて配り、CSP の違反なしで表示できる（`style` 属性は使っていない。帯の幅だけ、スクリプトから CSSOM で指定している）
- 375px の幅と暗い画面で崩れない（横にはみ出さない）。行を開いたときの表示も確かめた
- 作者の手元の実データ（266 件、前回あり）でも作れて、画面に出せる（そのデータは同梱していない）
- 画面の言葉とこの README は、yomiyasu の検査に通した

## ファイル

```
README.md
web/decision.html            画面（1 枚。CSS と JS を中に持つ）
web/decision-sample.json     見本（架空）
tools/aym_decision_view.py   判断用の JSON を作る（読み取りだけ）
tools/mapping.example.json   列名と状態の対応の例
schema/decision-view.schema.json
samples/decision-sample.json 見本（架空。web/ のものと同じ）
samples/make_sample.py       見本を作り直す
tests/decision-core.test.mjs 画面の計算の試験（node --test）
tests/test_aym_decision_view.py  JSON の作り方の試験（pytest）
```

## 導入範囲（2026-11-16）

- リポジトリ直下の `decision_view/` に独立して追加。AYMの既存モニター/API/CLIは変更しない。
- `--previous auto` はintent ID末尾の日付規則に依存する。実データのID規則が異なる場合は `--previous <campaign_id>` または `none` を選ぶ。
- 表示数値は出力されたJSON時点の状態であり、ブラウザ画面からAYMへ書き戻しはしない。保存済みのenrichmentの鮮度と元データの権限を確認する。
