---
task_id: 20261109-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: planned
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
acceptance_id: AC-20261109-01
---

# P0: Blue finding の証拠粒度と Oracle 採点の整合

## Objective

同一 `path/role/category` で異なる証拠参照を持つ複数の不正アクセスを、Blueが正しく報告したときに重複排除で失わないようにする。保存したFinding、blind Oracle、lab_metrics、RSIへの評価入力が一致することを独立して検証する。

## Scope

- In: `ai_yori_murasaki/agents/tools.py`、`oracle.py`、`lab_metrics.py`、Finding schema/意味契約、`tests/test_agent_core.py`、関連回帰。
- Out: 新規検知器、検知性能の成功主張、第三者への攻撃、無関係な変更。
- 作業順: 本Taskを完了し、AC-20261109-01が技術承認されるまではP1を着手・完了扱いにしない。

## Root-cause hypothesis (HEAD 8dd42e0)

`ToolBroker._run_submit_findings` の `dedup_key=(path,role,category)` と finding_id生成には `evidence_ref` が含まれない。一方 `Oracle.blind_detection_check` と `lab_metrics._finding_covers` は、各不正アクセス行の `ref` が Finding.references に含まれるかを採点する。そのため同一キー・異なるrefの正当な2報告のうち後者が消える可能性がある。

本記載はコード静的確認と、判定条件を抜き出したロジックの簡易再現であり、現行HEAD本体での再現テスト結果ではない。[調査証跡](../evidence/quality-control-20261109.md)を参照。

## Responsibilities

- Codex: 境界・順序・停止・完了判定。
- GLM-5.3: 再現手順のレビュー、修正案比較、DeepSeekへの実装指示、結果整理。
- DeepSeek-v4.1-Flash: 再現テスト、製品コード、回帰テストの実装。
- 担当モデル未接続のため現時点で製品コード変更は未着手。モデル変更・例外は既存規約と利用者承認に従う。

## Requirements

1. 先に現行HEADで再現する失敗テストを作り、入力・実コマンド・終了コード・出力を保存する。
2. 事象粒度を証拠参照で固定する。候補: (a) `(path,role,category,evidence_ref)` を重複キーとFinding IDに採用、(b) 1 Findingの `references` を合併。既存 Finding 契約と過去の証拠追跡・上限を比較し、GLMレビューで最小変更を決定する。選ばなかった方式の理由を記録する。
3. 同じ事象の重複送信は冪等とし、異なるrefは保全する。呼び出し内だけでなく複数回呼び出しを検証する。
4. brokerに発行されたrefの許可範囲、CAS存在/hash、証拠のrole/path/methodとの整合性、他ロール参照の拒否を検証する。必要以上にrole間の可視性を増やさない。
5. 保存Finding・Oracleの行単位検知/未検知・KPIのTP/FP/FNの意味を揃える。正常行だけの提出をTP扱いせず、根拠不在はHOLD/失敗として記録する。
6. 既存の認可正常回帰を保持する。自己採点でOracleを置き換えない。

## Required regression matrix

- 同じpath/role/category、異なる時刻・refを持つ不正アクセス2行。
- 同一refを2度提出、および別のsubmit呼び出しで再送。
- 正常行と不正行の混在、報告の逆順・時刻順の相違。
- 他ロールへのref漏洩・偽造ref・CAS欠落・不正hash・refと行属性の不一致。
- 修正前にPASSだった認可シナリオの回帰。
- Oracleのrows requiring/covered/uncovered、KPIのTP/FP/FN、保存Findingのref集合の一致。

## Evaluation Identity

これは固定fixtureの回帰試験であり性能比較・RSI昇格実験ではない。現行HEADで再現した結果と修正後の同条件結果、参照コミット、fixture hash、seed、実行環境を記録する。RSIの比較実験を行う場合は別manifestとpreflight/postrunが必須。

## Local Commands

`py -3 -m pytest -q tests/test_agent_core.py tests/test_agent_lab_demo.py tests/test_agent_rsi.py`
`py -3 -m pytest -q -rs`
実行環境に依存しない失敗→成功の差分を保存する。Docker/Ollamaは可能な場合に限り実施する。

## Deliverables

実装diff、事前/事後の機械可読証跡、[AC-20261109-01](../acceptance/AC-20261109-01.md)、CHANGELOG、必要な契約とBirdseyeの同期。

## Tests / Commands / Notes

- 現時点: **未実行**（接続可能な環境はGitHub読み書きのみ。ローカルプロジェクト、Docker、指定Ollama実装モデルへ直接アクセスできない）。
- 非リポジトリ実行の独立ロジック再現は[調査証跡](../evidence/quality-control-20261109.md)に分離している。成功扱いにしない。
- P1へ進む条件: AC-20261109-01でテスト結果・境界・数値整合が確認されること。
