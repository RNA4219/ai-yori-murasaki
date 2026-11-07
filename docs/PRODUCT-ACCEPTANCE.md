---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
---

# 製品検収の対応表

現在の判定は**未完成**。2026-11-07に[製品完成の検収](acceptance/AC-20261106-06.md)を撤回した。以下は過去の実行記録と実装の対応表であり、全要件の充足一覧ではない。R-046の通常経路への統合などが不足している。[受領レビューの整理・必要性評価](reviews/REVIEW-INTAKE-20261107.md)で不足と追加提案を分ける。

要件50件と[受入計画](requirements/ACCEPTANCE-PLAN.md)を、実装・試験・実行証跡へ対応させる。実Docker、実HTTP、実モデル呼び出し、fixtureによる契約試験、故障注入を区別する。

## 主な実行結果

| 確認 | 結果 | 証跡 |
| --- | --- | --- |
| 全pytest | 401 passed、2 skipped、22 subtests、43.03秒 | [JUnit](evidence/pytest-20261107.xml) |
| 配布用イメージでの実ラボ | 22.75秒。同一認可不備への3試行が200→403、正常5件成功、証跡53件検証、レビュー待ち | [実行結果](evidence/product-lab-portable-20261107.json) |
| 製品のモデル接続 | Ollama経由でGLM/DeepSeekを実呼び出し | [接続記録](evidence/product-model-connection-20261106.json)、上記ラボの台帳 |
| 隔離・異常終了・通信故障 | 31項目を確認。実Dockerのmount/read-only/SIGKILLと、ネットワーク故障注入を区別 | [境界検証](evidence/product-boundaries-20261107.json) |
| Blue入力の分離 | 実DockerラボのLLM送信先を捕捉用brokerへ差し替え、非公開canaryの漏えいなし | [捕捉結果](evidence/product-leakage-sigma-20261107.json) |
| 固定Sigmaルール | 実観測の再生でTP3、FP0、FN0、TN5 | 同上。5件の正常fixtureのpath/roleメタデータを併用 |
| RSI | train由来候補を3seed×validation/holdoutの6組で比較。promotion-ready、未反映 | [実行](evidence/rsi-evaluation-20261107-02/execution.json)、[postrun](evidence/rsi-evaluation-20261107-02/postrun-check.json) |
| P2反復 | Campaign1回で成功停止、RSS 56,520,704 bytes | [反復記録](evidence/p2-loop-20261107.json) |
| HTML | GLMの修正確認PASS。PC/スマホ、キーボード、未測定/HOLD/不正値を確認 | [画面検証](evidence/frontend-design-review-20261107.json) |
| wheel | 外部フォルダーへインストールし、CLI・同梱資材を確認 | [配布検証](evidence/package-20261107.json) |

pytestの2件のスキップはWindowsでシンボリックリンクを作成できない試験。既存の成功実行49.54秒に対して最終回帰は43.03秒であり、追加の性能最適化は行っていない。ラボ時間にはDocker起動とモデル通信が含まれ、固定した速度SLOはない。

## T01〜T20

| T | 確認方法・結果 | 対応実装／試験 |
| --- | --- | --- |
| T01 | event改変をFAIL、検証journalを元台帳から分離 | storage、tests/test_v1_storage.py、tests/test_v1_cli.py |
| T02/T02b | 中間・末尾削除、全列再計算をchain/checkpointで検知 | tests/test_v1_storage.py、tests/test_v1_transactions.py |
| T03 | event順序・sequenceの不整合を拒否 | tests/test_v1_storage.py |
| T04 | 別ロールのprivate CASをBlue入力に送らず、実mount境界とAPI投影も確認。cacheは同一要求だけを再利用 | leakage-sigma/boundariesの実記録、tests/test_v1_worker.py、tests/test_v1_server.py |
| T05 | 秘密を含むfixtureの正規化と実CAS書き込みで平文を除去。保存後hashを照合 | tests/test_v1_adapters.py、tests/test_v1_storage.py、tests/test_v1_runtime.py |
| T06 | LLMのtool_callsをデータとして保持し実行しない。任意shell argvを拒否 | boundariesの故障注入、tests/test_v1_policy.py |
| T07 | scope外・未知のコマンド・actor偽装を拒否。CLI終了4を確認 | tests/test_v1_policy.py、tests/test_v1_worker.py、tests/test_v1_cli.py |
| T08 | 本番書き込みは既定拒否 | tests/test_v1_policy.py、tests/test_v1_cli.py |
| T09 | TrivyのID/件数/位置/参照/拡張を保持し秘密を除去 | tests/test_v1_adapters.py。実Trivy接続は未検証 |
| T10 | SARIFの件数・識別子を正規化 | tests/test_v1_adapters.py。実CodeQL実行は未検証 |
| T11 | Sigma部分集合の有効・不正・未対応を区別 | tests/test_v1_adapters.py |
| T12 | 固定ルールが実攻撃観測3件で発火 | leakage-sigmaの再生記録。ルール作成イベント名はschemaのsigma_drafted |
| T13 | 同じ固定ルールで事前固定の正常fixture5件のFP0 | leakage-sigma。母集団のprecision保証ではない |
| T14 | 修復後の攻撃が不成立、completed、Oracle FAIL/exploit_not_succeeded | portableラボ実行 |
| T15 | 正常系5件成功 | portableラボ実行、tests/test_v1_lab_assets.py |
| T16 | 保存したCASから非LLM Oracleが独立再計算 | portableラボKPI、tests/test_v1_oracle.py。偽成功フラグ・件数・raw不一致も拒否 |
| T17 | 実コンテナ内child workerをSIGKILL。異常結果を明示的にruntimeへ渡しHOLD、旧Runのcrash記録、新Runへ再開 | boundaries。OS全体の自動障害検知を実証したものではない |
| T18 | LLM通信故障の注入で3試行後adapter_unavailable | boundaries、tests/test_v1_policy.py |
| T19 | Wazuh通信故障の注入で3試行後adapter_unavailable | boundaries。実サービスの障害試験ではない |
| T20 | RustXNG通信故障の注入で3試行後adapter_unavailable、架空の検索結果なし | boundaries |

## 要件50件の対応

| 要件 | 実装・文書 | 確認 |
| --- | --- | --- |
| R-001〜006 | BLUEPRINT、要求・判断、KPI、Campaign.extensions | 仕様と設定、tests/test_v1_metrics.py、未測定の表示 |
| R-007〜012 | runtime、lab、cli | lifecycle/transition、実ラボ終端、CLI |
| R-013〜016 | oracle、runtime、contracts | Oracle独立再計算、FinalVerdict、HOLDと攻撃不成立の分離 |
| R-017〜019 | このT対応表、adapters | 契約試験と実接続を区別 |
| R-020〜024 | policy、worker、HttpBroker、clients | T04〜08、T17〜20 |
| R-025〜028 | schemas/v1、normalizers、DECISIONS | 8契約、SARIF、未指定I/Fを推測しない判断 |
| R-029〜033 | storage、jsonio | chain/CAS/checkpoint、秘匿化、並行append、故障注入 |
| R-034〜037 | policy、worker、runtime | ロール、credential非委譲、承認と適用の分離 |
| R-038〜041 | 7adapter契約、LLMClient、P2 routing | 契約validation、予算、実モデル接続、model_status |
| R-042〜043 | rsi/train/compare/eval、evaluation_identity | 9項目、manifest、経験出所、holdout記録、aliasの非固定扱い |
| R-044 | server、web/index.html | HTML/APIの投影、Host/Origin、PC/スマホ |
| R-045 | LICENSE、BLUEPRINT | 独自コードMIT、第三者資料の表示維持、未公開 |
| R-046 | enrichment | 部品試験あり。ATT&CK/KEV/EPSSのfixture import、CAS/TTLを検証済みだが、通常のラボ実行からFindingへの統合は不足。v1 MUSTの充足とは判定しない |
| R-047 | p2、runtime、resources | 上限付き反復、routing、Sigma候補、crash resume、RSS実測 |
| R-048 | この対応表、AC-20261106-06 | 完成判定を撤回。対応表の存在と全要件の充足を区別して再確認する |
| R-049 | model_status、接続記録、HTML | 指定・登録・開発呼出・製品接続・pinを区別 |
| R-050 | GLMのSkill入力と画面レビュー、DeepSeek実装 | frontend-design-review-20261107.json |

RSIはfreeze、候補の識別、実行証跡、train由来候補、上限付き方針更新、seed対比較、未見分離、経験の固定、レビューと反映の分離を実装した。applyのatomicな反映、拒否、rollback照合、再試行は一時環境で検証し、実証候補のregistryには反映していない。

## レビューと限界

Codexが監督・統合、Ollama登録名deepseek-v4.1-flash:cloudが実装、glm-5.3:cloudが設計・レビューを担当した。モデルの生成物はAPI照合と実行検証で修正した。原応答と適用前のファイルは.ga/goal-20261106に保持する。1017の固定版を使ったという主張はしない。

GLMのRSIレビューは実証との照合後にPASS、画面レビューも修正確認でPASS。KPIレビューの指摘に従い、HOLD理由の保持と各Runの一度のaudit読み取りへ修正した。未使用の集計変数が一つ残るが、数値の重複加算には使われず、結果への影響はない。

P2の自動調整候補はTP3/FP1/FN0/TN4で未反映。T13の固定ルールの結果と混同しない。候補生成機能が動いたことは、候補を採用できることと同義ではない。ATT&CKの実ラボ対応付けは未測定。外部サービス、大規模運用、本番の安全性、LLMの汎用性能向上は、この検収の実証範囲に含めない。

受領レポートは変更していない。製品は未完成であり、公開・本番への適用も行っていない。
