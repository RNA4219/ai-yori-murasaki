---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
template_version: 1.0.0
---

# Blueprint

## 1. Problem Statement

検知と修復の効果を、エージェントの自己申告だけで判断しない。Red、Blind Blue、Oracleの入力と権限を分け、実行、証跡、独立判定、修復後の回帰検査を結び付ける。

受領した[調査レポート（非公開の保存資料）](<docs/ADR/deep-research-report (16).md>)をSankaiで整理し、Imozuruによる原典調査、3段階の要約とGLMレビューを経て[要件](docs/requirements/REQUIREMENTS.md)を定めた。

## 2. Scope

- In: ローカルDockerラボ、Campaign/Run管理、Evidence/CAS/checkpoint、非LLM Oracle、修復と正常回帰、7種のadapter契約、Ollama実接続、RSI、KPI、P2反復、読み取り専用HTMLモニター、MIT配布物、Workflow Cookbook Tier 3の運用。
- Out: 第三者への攻撃、本番接続・変更、外部サービスの運用導入、外部公開、Git初期化、リモートCI。

外部Trivy/CodeQL/Wazuh/Sigma CLI/RustXNG/rdshの実接続は未検証。Sigmaは文書化したJSON表現の部分集合を扱う。大規模ホスト数やSLAは実証していない。

## 3. Constraints / Assumptions

- 原資料を保持し、原典の所在と採否理由を[判断記録](docs/requirements/DECISIONS.md)に残す。
- workspace、trusted root、stagingをリポジトリ外で分離する。workerへ台帳・正解・Dockerソケットを渡さない。
- ローカルラボの修復と本番適用を分ける。既定のpolicyは本番書き込みを拒否する。
- RSIは評価条件、実装・入力のhash、seed、分割、予算、経験集合を比較前に固定する。採用準備、レビュー、実行器への反映を別操作にする。
- cloudモデルの登録名は不変の版ではない。決定的なローカル方針評価とLLMの性能評価を分ける。

## 4. I/O Contract

| 種別 | 入出力 | 正本 |
| --- | --- | --- |
| 入力 | 認可scope、policy、固定したfixture、評価manifest | [Core仕様](docs/requirements/SPEC-CORE.md)、[評価仕様](docs/requirements/SPEC-EVALUATION.md) |
| 記録 | Campaign、Run、EvidenceEvent、Finding、Remediation、FinalVerdict等 | package内のschemas/v1と[API契約](docs/requirements/API-CONTRACTS.md) |
| 出力 | JSON CLI応答、監視用API、KPI、比較結果 | [操作ガイド](docs/PRODUCT-GUIDE.md) |
| 作業証跡 | Task、Acceptance、CHANGELOG、Birdseye | [文書契約](docs/CONTRACTS.md) |

RSIの原則は[9項目の方針](docs/RSI.md)に従う。Experience Memoryには完了したtrainの抽象カードだけを保存し、今回の観測証拠として扱わない。

## 5. Interfaces

- CLI: `aym campaign/run/checkpoint/oracle/verify/monitor/lab/rsi`。
- モニター: `127.0.0.1:8765`、読み取り専用HTTP。raw CASやprivate ledgerを配信しない。
- Ollama: 製品adapter経由でBlue/レビューにGLM、Red/PurpleにDeepSeekを割り当てる。
- 操作: [RUNBOOK](RUNBOOK.md)、[製品操作ガイド](docs/PRODUCT-GUIDE.md)。

## 6. Verification

[受入計画](docs/requirements/ACCEPTANCE-PLAN.md)と[製品検収](docs/acceptance/AC-20261106-06.md)で、実動、契約試験、故障注入、未検証を区別する。Workflow Cookbookの文書ゲートは[RUNBOOK](RUNBOOK.md#execute)に従う。
