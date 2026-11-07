---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
template_version: 1.0.0
---

# Evaluation

## Product Acceptance

製品の正本は[受入計画](docs/requirements/ACCEPTANCE-PLAN.md)と[AC-20261106-06](docs/acceptance/AC-20261106-06.md)。機能、故障注入、Ollama実接続、Docker閉ループ、画面、配布物、RSIのpreflight/postrunを個別の証跡で確認する。

KPIは証跡完全性、Oracle再実行一致率、検知率・適合率、修復率、過剰修復率、欠落率、検知遅延、未検知ATT&CK技法、本番自動適用件数、未承認特権操作件数の11種類。分母と対象を表示し、未測定はnull、証跡の検証保留はHOLDとする。ラボでの結果を母集団の性能保証にしない。

以下の文書導入条件は初期Taskの検収条件として保持する。現在の製品試験の結果とは区別する。

## Acceptance Criteria

| ID | 文書導入の受入条件 | 根拠 |
| --- | --- | --- |
| WF-01 | Tier 3 必須文書・非空の Task / Acceptance / Capsule がある | [導入対応表](docs/workflow-adoption.md)、公式 Tier checker |
| WF-02 | 5テンプレートの章立てと `template_version`、所有者・review 期限を更新している | [UPSTREAM](docs/UPSTREAM.md)、drift checker |
| WF-03 | Task と Acceptance が同じ `task_id` / Intent を持ち、CHANGELOG から辿れる | [Task](docs/tasks/TASK.workflow-adoption-11-06-2026.md)、[検収](docs/acceptance/AC-20261106-01.md) |
| WF-04 | index / hot の世代、Capsule と原文 hash・review hash が一致する | 公式 freshness checker `--require-reviewed` |
| WF-05 | Imozuru と Yomiyasu の使用段階、正本、成果物、制約が RUNBOOK にある | [RUNBOOK](RUNBOOK.md#imozuru) |
| WF-06 | 受領レポートの SHA-256 が導入前と同一で、未確認の外部仕様を断定しない | [snapshot](docs/upstream-snapshot.json)、[BLUEPRINT](BLUEPRINT.md) |
| WF-07 | 検証コマンドが実在し、成功・未実行・適用外を区別している | [RUNBOOK](RUNBOOK.md#execute)、Acceptance の Evidence |

## RSI Documentation Criteria

今回の RSI 文書追加は次の条件で技術検収する。対象は [Task 20261106-02](docs/tasks/TASK.rsi-policy-11-06-2026.md) と [AC-20261106-02](docs/acceptance/AC-20261106-02.md)。

| ID | 受入条件 |
| --- | --- |
| RSI-DOC-01 | [RSI 方針](docs/RSI.md) に提示された9項目と適用上の注意がある |
| RSI-DOC-02 | Blueprint、Runbook、契約、評価基準、Task 雛形と作業導線が接続している |
| RSI-DOC-03 | 目的と条件の事前固定、同条件比較、train / validation / holdout、経験 snapshot、採用と反映の分離を明記している |
| RSI-DOC-04 | finding recall と目的達成、診断と採用評価、機能実装と性能実証を区別し、既存の数値閾値を流用していない |
| RSI-DOC-05 | 参照3文書を hash で識別し、受領レポートと以前の検収・証跡を保持している |
| RSI-DOC-06 | Tier / drift、Acceptance、Birdseye の厳格検査と、文書の参照・ID照合が成功している |

RSIの比較評価では、目的に対応する独立した評価器、事前に凍結した改善・非劣化基準、比較に受理できる run、誤検出・見逃し・完了・費用・安全性を検証する。
数値と反復回数は各評価manifestで事前に固定する。評価器がない、条件が一致しない、未見課題がない場合は改善実証・採用準備完了を宣言しない。
今回の文書検収成功を、それらの性能条件の達成と扱わない。

## KPIs

| 指標 | 対象・分母 | 今回の基準 |
| --- | --- | --- |
| 必須構成の充足 | Tier 0〜3 の必須パス | すべて存在。各追跡ディレクトリは雛形だけにしない |
| 検収記録の整合 | 今回完了する Task | 各 Task に検証結果と対応 Acceptance がある |
| 原文・要約の鮮度 | index に登録した原文 | hash / review 不整合 0件 |
| 原資料の保全 | 受領レポート1件 | SHA-256 一致 |

原資料の50〜500ホストや工数は提案値であり、実証していない。ローカルラボの検知・修復・遅延の実測は製品検収に記録する。
製品評価では測定集合、分母、版、Oracle、許容誤差を確定し、[Evaluation Identity](docs/CONTRACTS.md#evaluation-identity) に従う。

## Test Outline

- 文書構造: 公式 Tier / drift checker、Acceptance checker。
- 整合: 相対リンク、Task と検収の ID・状態、テンプレート章立て、原資料 hash の照合。
- Birdseye: index / hot / caps の構造と公式 freshness checker。
- 初期の文書導入では製品試験を実施していない。その後の製品Taskでunit/integration、権限境界、実ラボ、モデル接続を検証した。受領レポートの全文推敲は対象外。

## Verification Checklist

- [ ] WF-01〜WF-07 を満たす。
- [ ] [CHECKLISTS](CHECKLISTS.md) を確認し、実コマンドと結果を検収記録へ残した。
- [ ] 未実行・残件を記録し、文書導入の成功を製品評価として報告していない。
