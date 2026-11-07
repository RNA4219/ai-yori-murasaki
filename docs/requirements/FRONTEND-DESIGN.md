---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# ai-yori-murasaki フロントエンド設計（開発モニター改修＋製品画面仕様）

2026-11-07の製品モニター再設計は [MONITOR-REDESIGN](MONITOR-REDESIGN.md)を参照する。

これは2026-11-06の初期設計記録。製品画面はその後に実装し、読み取り専用とした。現行画面の状態・GLMレビュー・ブラウザー確認は[製品検収](../PRODUCT-ACCEPTANCE.md)と[画面証跡](../evidence/frontend-design-review-20261107.json)を参照する。以下の「未実装」等は初期設計時の状態を示す。

## 0. 適用方針
- 既存CSS tokens（`--color-*`, `--space-*`, `--radius*`, `--font-*`）を起点とし、Spacingは閉じた許可リスト（0,2,4,6,8,12,16,20,24,32,40,44,48,56,64,72,80,96,128,140,144,160px）のみ使用。新規Primitive値は追加しない。[L03]
- viewport区分: Mobile 0–767px / Tablet 768–1024px / Desktop 1025px以上。既存の760px/420pxブレークポイントは768px/360px超の狭幅差分へ修正し、区分の隙間をなくす。[L04]
- 単一HTML+JSON、外部依存・画像・Webフォント追加なし。system-uiスタック維持。
- API内部名（state.json, phases, jobs等）は通常UI文言から排除し、details補足へ集約。
- 数値は実記録値のみ。進捗率・完成率・捏造グラフは作らない。「ジョブ応答完了」と「機能検収」を別状態として表示。

## 1. 開発モニター（実装対象）

### 1.1 画面目的
最初のviewportで「何を作業中か／どこで止まったか／最終更新」を把握させ、phase/work/roles/jobs/decisions/artifactsへ1操作で移動できること。

### 1.2 情報構造（ARTEX構造を参考にした再構成）
1. **作業中サマリ（新設・ファーストビュー）**: 現在の段階(stage)＋該当phaseの状態バッジ＋最新の非完了ジョブ1件（ID/種別/状態）＋重要な未確認（status=reviewing または blocked の最新decision/job）を1行バナーで。最終更新日時と取得方法（HTTP/file）を右寄せ常時表示。
2. **段階一覧（phases）**: 表。状態バッジ＋状態文字（色以外に必ず文字と形：既存badgeの●/○形状差分を維持）。
3. **担当モデル（roles）**: 表。
4. **ジョブ履歴（jobs）**: 表。所要時間・tokensは「未記録」を明示（実測のみ）。
5. **決定事項（decisions）**: 表。未確認（reviewing）を表の上位へソートせず、サマリで拾う。
6. **資料（artifacts）**: リスト。
7. **補足（details/summary）**: データ形式（schema等）、取得方法、file://制約、リンク許可範囲を格納。

### 1.3 配置
- **1440px**: 2カラム（sidebar 260px＋main max-width 1100px）維持。サマリはmain冒頭、横overflowなしを維持。
- **768px**: sidebarをmain直上の横並びnav（flex-wrap、各項目min-height 44px）へ切替。データ状態dlはnav直下に1行。
- **390px**: navは`details/summary`「メニュー」折りたたみ（閉時は見出し＋サマリのみでviewport内、開閉はsummary 44px）。サマリが初期viewportに収まること。表は内部横スクロール維持（min-width 560px、`overflow-x:auto`の`.table-wrap`）。

### 1.4 状態・操作・読み上げ
- 状態: loading（「読み込み中…」status-line＋`output`要素）、empty（「未取得」）、error（role=alert banner）、stale（warn banner＋前回データ明示）、unknown（badge「不明」）。HTTPエラーと通信断・タイムアウトを文言で区別（既存JSの分岐維持）。
- 操作: 手動更新button、ファイル読込label+input（44px、focus-within表示）。skip-link、`:focus-visible` 3px outline維持。
- 読み上げ: banner-area `aria-live=polite`、error時role=alert。状態バッジは可視文字をそのまま名前にする。再読込時はスクロール・フォーカスを移動させない（動的挿入時の先頭フォーカス移動は行わない）。
- 文言: 日本語敬体。内部名を除き「記録されたデータ」「履歴」等へ置換。

## 2. 製品画面（仕様のみ・未実装）

### 2.1 Campaign一覧
- 目的: 対象の俯瞰と詳細への導線。表示: 名前、Blind状態、最終実行日時（実記録）、検知件数（実記録）。状態: loading/empty/error。操作: 詳細へa[href]。390px: 1列カード、768/1440px: 2/3列grid。

### 2.2 Campaign詳細
- 目的: 実行状態と証跡の確認。表示: 権限別にBlind中は非開示項目を「権限により非表示」と明示（空白にしない）。検知と修復を別セクションに分離（混在禁止）。RSI比較は実測ペアのみ、未測定は「未測定」表示。

### 2.3 証跡verify
- 目的: 成果物の検証状態提示。表示: 各証跡のverify結果（成功/失敗/未検証＋理由文字）。操作: 再verify（確認ダイアログは`<dialog>`+request-close経由）。

### 2.4 Promotion-ready
- 目的: レビュー待ちと反映済みを混ぜない。表示: 「レビュー待ち」と「反映済み」を別リスト/別タブ相当の見出しで分離。数は実件数のみ。

### 2.5 共通
- 全画面で状態は色＋文字＋形、操作44px、focus可視、skip-link、semantic HTML、native button/output。エラーは問題と復旧方法を近接テキストで。

## 監督による整合確認

監督統合条件（GLM設計を尊重して矛盾を解消）:
1 モバイルのnavは768px未満すべてで折りたたむ。768–1024px横並び、1025px以上sidebar。390pxだけ折りたたむという記述は採らない。main initialviewportでstageとupdatedが見える。
2 focus中にrenderAllを丸ごとskipしてデータを更新したふりにしない。フォーカス可能な資料linkは同じキー（path+title）のnodeを再利用する。消滅するリンクだけは適切な既存見出しへフォーカスを復旧し、更新を告知。summaryは固定nodeのtextのみ更新。既存通信制御は維持。
3 URL許可は実際の既定: ../docs/安全な子パス、同階層はindex.html/state.jsonのみ。GLMのmonitor*.htmlは古い認識なので採らない。
4 製品verifyは読取再検査なので確認dialogを必須にしない。影響のある承認/反映操作だけを別操作として扱う。
5 stageとphasesの関連を名前から推測しない。phaseのin_progress/reviewing/blocked実値を列挙または件数を表示。非完了job最新は入力配列を記録順として最後から探す。進捗の推計はしない。
6 現在は開発モニターだけ改修、製品画面は仕様に留める。
