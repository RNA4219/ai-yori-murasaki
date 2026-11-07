---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
---

# 評価ビュー拡張設計（既存モニターへの差分）

## 方針
既存 `web/index.html` のチャコール基調・フラット構成・DOM差分更新(reconcile)をそのまま再利用する。新規トークン・新規寸法は追加しない。追加するのはCampaign詳細内の「評価」タブ1つと、検知行への状態バッジ拡張のみ。APIは `GET /api/campaigns/<id>/assessment`（operator投影）を1つ追加取得する。応答が404/未提供なら「評価レポートなし（未評価）」表示。

## Campaign詳細の変更
1. タブ: `概要 | 実行履歴 | RSI | 評価` に1つ追加。`CAMPAIGN_VIEWS` へ `{id:'assessment'}` を追加し、`data-campaign-view="assessment"` のセクションを追加。タブ状態は既存 `campaignViews` 機構で保持。
2. 検知行（incident-row）: 既存severityの右に「評価状態」を1つ追加。状態は states 一覧の語彙のみ。`data-key="finding-<id>"` は既存どおり。

## 評価タブの内容（上から）
### A. Coverage表（R-051）
5類型（認可/SQLi/XSS/パストラバーサル/既知依存）+unknownの行。列: 類型名（日本語）| 実施状態 | 理由 | 対象版。`not_executed`→「未実施」、`failed`→「実行失敗」、`executed`→「実施済み」。未実施・実行失敗は「未検出」と表示しない。表の直前に固定文言「未実施は安全ではありません。未検出（実施して何も見つからなかった）とは別の状態です。」
### B. Finding評価リスト
検知ごとに折りたたみ行（details、既存details-block流用）。閉時: タイトル | 状態バッジ | CVSS（未評価なら「未評価」）。開時の順序は固定:
1. **問題とは**（title、平易な日本語1文）
2. **根拠**（観測の要約と証拠hash短縮。生データ・payloadは表示しない）
3. **対策**（remediation提案の有無と適用状態）
4. **次の行動**（状態から導く1文。例: 確認済み→「修正案を確認してください」）
5. 技術詳細（details入れ子）: 分類（CWE/CVE/OWASP/ATT&CK各状態と未特定理由）、CVSS（スコア・ベクター・nomenclature・invalid/unevaluated理由）、業務影響（3軸+unknown+未確認軸）、修復（applied/env/version/side_effects）、再検証（6チェックとoracle_verdict、result）。
CVSSと業務影響は別ブロックに並列表示し、「CVSSは技術的な重大度、業務影響は担当者の判断であり、片方から自動決定されません」を固定文言で添える。
### C. 測定の限界
既存KPIパネルの「測定の限界」の下に「5類型は今回の評価対象です。表にない種類を検査できるとは限りません」を常時表示。

## 用語説明（初心者向け）
評価タブ末尾にdetails「用語の説明」: 未調査/未検出/確認済み/候補/修正提案/再検証済み/判定不能/古い証跡の各1行定義。色のみで区別せず全状態に状態名テキストを併記（既存badge方式を踏襲）。

## 自動更新
assessmentは既存loadDetailの第4リクエストとして `detail-assessment` キーで追加。失敗時は既存runsStaleと同じく `assessmentStale` フラグで前回値保持+警告バナー。reconcileの `data-key` 付与を全追加ノードへ行い、detailsの `open` 属性は既存保存ロジックで維持。scroll/focus/tabsは既存機構のまま追加コード不要。

## 表示しないもの
Red/Oracle由来の生観測データ、credential、payload、Blue向け情報（view=blueはサーバーが `{status:"withheld"}` を返すのでその文言をそのまま表示）。LLM確信度は受信しない（投影契約準拠）。