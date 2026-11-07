---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# 契約と正本

## 文書・作業契約

`intent_id` は目的、`task_id` は作業、`acceptance_id` は技術検収を識別する。
Task の `done` と Acceptance の `approved` は対象作業の条件を満たしたときだけ記す。
Evidence には実コマンド、入力版、結果、成果物の所在を残す。未実行や提案を成功として記録しない。
共通スキーマ連携が必要になった場合は [UPSTREAM](UPSTREAM.md) の導入元と `agent-protocols` の正本を確認してから接続する。今回外部 tracker や Evidence API への接続はない。

## Evaluation Identity

比較評価・実験・昇格・提出を始める前に、導入元の
`docs/contracts/evaluation-identity-contract.md` を必ず読む。
非 game の作業・性能評価は `templates/evaluation-measurement-manifest.template.json` を基に、仮説、Legacy / Candidate、source / input / runner / dataset / model / policy の版と hash、測定単位、負の対照を固定する。

```powershell
# RUNBOOK の環境変数を設定し、実在する manifest を指定して実行する
$ManifestPath = Join-Path $ProjectRoot 'docs/evaluation-manifests/EV-YYYYMMDD-01.json'
Push-Location $CookbookRoot
try {
    py -3 -m tools.ci.check_evaluation_identity_manifest --manifest $ManifestPath --stage preflight --check
    # preflight 成功後に、承認された評価を実行する。
    # 同じ manifest に実測値・正の records・成果物 hash・障害数を記録してから postrun を行う。
    py -3 -m tools.ci.check_evaluation_identity_manifest --manifest $ManifestPath --stage postrun --check
} finally {
    Pop-Location
}
```

preflight が失敗した場合は実験を開始しない。postrun 成功前に昇格・提出・公開を行わない。
文書検査・read-only 診断・checker の fixture 試験は適用外。製品比較の実記録は[EV-20261107-RSI-02](evaluation-manifests/EV-20261107-RSI-02.json)と[完了manifest](evidence/rsi-evaluation-20261107-02/results/cd-42be8e587eadc664-manifest.json)に分けて保持する。

## RSI

[RSI 方針](RSI.md) を自己改善の正本とし、比較評価には上記 Evaluation Identity も適用する。
次は論理的な記録項目である。実装済みschemaとAPIの正本は[Core仕様](requirements/SPEC-CORE.md)、[評価仕様](requirements/SPEC-EVALUATION.md)、[API契約](requirements/API-CONTRACTS.md)とする。

| 記録 | 固定・保存する内容 |
| --- | --- |
| 目的・条件 | 達成目的、認可範囲、主指標・評価器の版、採用・非劣化基準、予算、反復回数、停止条件、凍結時点 |
| baseline / candidate | 識別子、変更仮説と差分、source・設定・prompt・input・model・runner の版 / hash |
| 比較入力 | train / validation / holdout の集合識別と hash、seed の対、対象初期状態・reset 方法、残る非決定性 |
| run の証跡 | 実行操作、観測、仮説、支持・反証・方針転換・拒否、停止理由、使用予算、完了状態、artifact と hash |
| 受理・結果 | 記録の完全性、保存前の secret 除去、受理可否と除外理由、成功・誤検出・見逃し・完了・費用・安全性 |
| 経験 | 有効 / 無効、完了した train 実行の出典、抽象カードの ID・結果・出典参照、snapshot hash、取得規則と使用カード |
| 採用と反映 | 事前基準との照合、採用・不採用理由、`promotion-ready` のレビュー、反映先の版・別操作・復旧方法 |

評価用の正解・hidden Oracle はエージェントと経験集合から分離する。validation / holdout の結果で候補を調整した場合は、新しい未見課題を採用評価へ用いる。
経験集合を固定できない run は同条件比較として受理しない。経験カードの「支持・反証」は以前の train の結果で、今回の対象の証拠ではない。
無効化・通信障害・未完了の診断記録は残し、完了した不成功や candidate 由来の停止を成功率の分母から都合よく消さない。
参照資料の採点器・反復回数・予算消化率・数値閾値は、この契約の既定値にしない。

## 将来の製品契約

受領レポートの Campaign / Run / EvidenceEvent / OraclePredicate / Finding / Remediation / Approval / FinalVerdict は設計候補。
JSON schema、API、既存 OSS の互換性は未確定で、実装済み契約として利用しない。
原典調査と、依頼された実装範囲の確定後に、版・入力・出力・失敗時動作・受入条件を個別に定義する。
