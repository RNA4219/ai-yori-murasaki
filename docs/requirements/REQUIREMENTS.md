---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-06
---

# 要件定義 v1（Ollama分解・設計担当）

本稿は原レポート（行番号付き）と追加RSI方針、Sankai整理、原典調査抜き書き、最新ユーザー要求、監督確認（Codex）を入力とした要件定義の改訂稿である。実装詳細・DB/UI/APIの具体仕様・性能保証は本稿の対象外とし、次工程の仕様へ送る。既存Python試作の実装は要求の根拠に用いない。原典引用は参照（行番号・節）に留め、逐語複写しない。

## 0. 前提と読み方

### 0.1 帰属の分離（重要）

本稿の要求は根拠の性質により次の3種を区別して扱う。混同は変更管理を破壊するため、各要件・各判断に帰属を明記する。

- U（ユーザー逐語要求）: ユーザーが明示的に述べた要求。例: Workflow-cookbook様式フル準拠、Imozuru/YomiyasuをRUNBOOKに、RSI方針追加、元プロジェクト名削除、自身コードMIT、ARTEX型HTML監視、監督Codex/レビューGLM/実装DeepSeek→Ollama登録モデル、約3回compactionと見直し、先に要件の敵対的検証と熟成・Sankai整理・Ollama分解。
- S（監督設計判断）: 原レポートに基づき監督（Codex）が確定したv1設計判断。例: 単一操作者、ローカル認可ラボ、具体的完成条件、データ保管方式、主要アダプター範囲、Dev既定deny、trust anchor v1実装、T13 FP0、T09 lossless契約、Oracle例外不新設、50–500hostは将来運用条件、外部署名サービス運用は将来。
- R（原レポート由来）: 原レポートの記述・推奨値。数値は推奨であり実証ではない。

除外事項（任意の第三者攻撃、本番自動変更、対外公開）は、ユーザー逐語要求ではなく監督の実行範囲解釈として記録する（U由来のMIT指定と混同しない）。

### 0.2 義務の強さ

- MUST（必須）/ SHOULD（推奨）/ MAY（任意）。
- v1対象: v1で実装・検収対象とするか、将来運用条件（環境・契約・承認が揃った時点で有効化）かを示す。

### 0.3 外部接続と実環境

- 外部実接続検証は契約試験（contract test）と区別する。契約試験はI/F適合の確認であり、実接続・実環境での動作保証ではない。
- 外部サービスlive未接続を隠さない。未取得・PATH不在ツールは接続状態をHTML/台帳に明記する。
- 実環境スナップショット（v1入力、監督確認済み）: Docker DesktopのLinux daemonは起動済み。trivy / codeql / rdsh の実行fileはPATH不在。この状態をv1計画の初期値として固定し、R-024の接続状態表示の初期値とする。

### 0.4 数値・規模の扱い

- 50〜500 hostは原レポートの設計想定値であり、今回の性能保証・SLA・台数前提にしない（将来運用条件）。
- 推奨数値KPIを実証済みとして扱わない。既定の目的と採用閾値は評価前に固定する。
- 固有組織の規模・SLA・台数・本番認証は設定入力とする。

### 0.5 運用前提

- 操作主は1名を既定とする（S）。ローカル認可ラボを既定とし、OS異なる実行workerはadapter化する（S）。
- 危険なtarget実行は外側OS隔離を必須とし、未構成はHOLD（S）。依存ツール不在・認証不在は接続不可を明示し、PASSを捏造しない（S）。
- 実環境で検証できない項目は未検証として検収から漏らさず残す（S）。
- 多人数運用、外部署名サービス運用、本番deployment、DGX性能数値は将来運用条件（S）。

## 1. v1の意味ある完成条件（要約）

v1はローカル認可ラボ上で Red観測成功→Blind検知→修正→攻撃不成立＋正常回帰成功→レビュー待ち の一連の実動作を終端まで実証できること。台帳・API・HTML・主要アダプター（7系統）の実装を伴うこと。単なるfixture集や単一成功のみを製品完成としない。T01〜T20、RSIゲート、通信/途中停止のfail closed、外部adapter契約試験、ローカル実動閉ループと通常回帰を完成条件に含む。外部サービスlive未接続は隠さず接続状態として明示する。

## 2. 要件一覧（R-001〜R-045、追加はR-046以降）

各要件は「根拠（U/S/R）/ 義務 / 内容 / 受入条件 / 依存 / v1対象 or 将来運用条件」を付す。

### A. 目的・成功基準・KPI

#### R-001 目的の三本柱の明文化
- 根拠: R（原レポート L52–L56）、S（Sankai A1）、U
- 義務: MUST
- 内容: (1) Blueを検知・調査・修復・再検証の再現可能プラットフォーム化、(2) Blind後のPurple loopによるdetection/remediation-as-code改善、(3) LLM推論品質と「攻撃が止まったか」の分離とdeterministic Oracleによる証明、を目的として固定する。
- 受入条件: 要件・仕様・RUNBOOK・評価計画の冒頭に三目的の正本をリンクで参照でき、意味の一致を確認できるようにし（各文書で同一文言を強制しない）、各KPIがどの目的に紐づくか対応表がある。
- 依存: R-002, R-003
- v1対象: v1

#### R-002 KPIの定義・検証方法・測定機能の対応固定
- 根拠: R（原レポート L58–L76）、S（Sankai A1）、U
- 義務: MUST
- 内容: 証拠完全性、Oracle再実行一致率、Blue Recall/Precision、修正成功率、過剰修復率、Evidence欠落率、Red→検知時間、未検知ATT&CK technique、本番自動適用件数、承認無しprivileged action件数を、各検証方法（hash-chain verifier、同一fixture再実行、Red fixture replay、benign混在、exploit regression+unit/integration、正常系regression、run manifest照合、audit）と対応づける。KPI測定機能の実装と、ラボcampaignでの測定実施結果（数値の合否判定ではなく測定成立）はv1必須。
- 受入条件: KPI表に「測定集合・分母・重複の数え方・除外規則・信頼区間の有無」の欄があり、未確定は未確定と明記。P0目標とP1/P2目標を区別。各KPIについて測定器が存在し、ラボcampaignで少なくとも1回測定が成立した記録がある。
- 依存: R-001, R-030, R-031
- v1対象: v1（定義固定＋測定機能実装＋測定成立）。数値の合否実証はv1検収外。

#### R-003 推奨KPIを実証済みにしない
- 根拠: R（原レポート L60）、U、S（RSI節1）
- 義務: MUST
- 内容: 原レポートの数値（100%、≥90%、≥70%、<20%、<5%等）は推奨受入基準であり、本システムの実績・保証ではないと明示する。
- 受入条件: 要件・仕様・HTML・レポートのいずれにも「達成済み」「保証」と記載しない。未測定時は達成表示を禁止。測定済みの場合は対象集合・条件・限界を併記した上で表示可能。
- 依存: R-002
- v1対象: v1

#### R-004 評価前の目的・閾値・停止条件の固定
- 根拠: S（RSI節1）、U
- 義務: MUST
- 内容: 評価対象、予算、反復回数、停止条件、採用基準を評価結果を見る前に固定し、変更履歴を残す。作業量・ツール呼び出し数だけを成功指標にしない。
- 受入条件: 各campaignに事前固定ファイル（目的・閾値・停止条件・採用基準）があり、評価後の変更は別バージョンとして記録され、採用判断に使ったバージョンが特定できる。
- 依存: R-001, R-002
- v1対象: v1

#### R-005 50〜500 hostを性能保証にしない
- 根拠: R（原レポート L29）、S、U
- 義務: MUST
- 内容: 50〜500 hostは設計想定値であり、v1の性能保証・SLA・台数前提にしない。将来運用条件として扱う。
- 受入条件: 要件・仕様・HTMLに「性能保証ではない」と明記。負荷試験の合否に用いない。
- 依存: R-002
- v1対象: v1（明示）。性能実証は将来運用条件。

#### R-006 固有組織の規模・SLA・台数・本番認証を設定入力化
- 根拠: R（原レポート L25–L42）、S、U
- 義務: MUST
- 内容: 組織固有の規模、SLA、台数、本番認証はコード・要件に直書きせず設定入力とする。
- 受入条件: 設定スキーマに該当項目があり、未設定時は該当評価だけを未確認とし、無関係なラボ機能を全HOLDにしない。
- 依存: R-005, R-020
- v1対象: v1

### B. 評価フロー（Red/BlindBlue/Purple/修復再評価）

#### R-007 Red単独評価フローの段階と責任主体
- 根拠: R（原レポート L310–L322）、S（Sankai A2）
- 義務: MUST
- 内容: Scope validation→Fixture reset→Red plan→攻撃実行→state capture→exploit判定→chain sealの順と、Orchestrator/Oracle/Red Agent/Evidence layerの責任主体を固定する。
- 受入条件: 各段階の入力・出力・責任主体が台帳に記録され、順序違反はfail closed。
- 依存: R-010, R-016, R-020
- v1対象: v1

#### R-008 Blue単独評価フローの段階
- 根拠: R（原レポート L324–L336）、S（Sankai A2）
- 義務: MUST
- 内容: asset inventory→静的スキャン→telemetry取込み→優先付け→修復案と検証→検知ルール案を段階として持つ。優先付けにはATT&CK mappingとKEV/EPSS enrichmentを含む（R-046参照）。
- 受入条件: 各段階の出力が正規化eventとして台帳に残り、欠落時は未完了表示。
- 依存: R-016, R-022, R-023, R-046
- v1対象: v1（主要アダプター範囲で）

#### R-009 Blind Blue評価の可視範囲と分離
- 根拠: R（原レポート L338–L350）、S（Sankai A2/B4）、U
- 義務: MUST
- 内容: Blueに見せるもの（通常telemetry、endpoint/network/app log、repository/approved asset data、Blue finding、Blue remediation）と見せないもの（Red plan、exploit payload正解、Oracle ground truth、Red session transcript、Red hidden technique details）を固定。Red/BlueのLLM context、session store、tool-output cacheを物理またはnamespaceレベルで分離する。
- 受入条件: T04（Red context leakage）がFAILしない。分離方式（物理/namespace）の選択理由が記録される。
- 依存: R-020, R-021, R-030
- v1対象: v1

#### R-010 協調Purpleフェーズの解禁条件
- 根拠: R（原レポート L352–L364）、S（Sankai A2）
- 義務: MUST
- 内容: Blind評価終了後のみgap analysis→Sigma draft→patch draft→FP/FN matrix→reusable playbookを解禁する。
- 受入条件: Blind未終了状態でPurple工程が起動できない。解禁イベントが台帳に残る。
- 依存: R-009
- v1対象: v1

#### R-011 修復から再評価の順序
- 根拠: R（原レポート L366–L384）、S（Sankai A2）
- 義務: MUST
- 内容: Finding→Blue Patch Draft→Static Scan→Unit/Integration→Oracle Exploit Replay→Benign Regression→Red再評価→Oracle最終判定→PR承認ゲートの順を固定。exploit停止だけで合格にしない。
- 受入条件: 各段階の合否が台帳に残り、途中失敗は前段へ戻る。T14/T15が独立に評価される。
- 依存: R-007, R-008, R-016, R-024
- v1対象: v1

#### R-012 v1の終端到達（意味のある完成）
- 根拠: U、S（監督指定の実動作系列）、R（原レポート L17–L21, L691–L695）
- 義務: MUST
- 内容: ローカル認可ラボ上で Red観測成功→Blind検知→修正→攻撃不成立＋正常回帰成功→レビュー待ち の一連の実動作を終端まで実証する。台帳・API・HTML・主要アダプター（7系統）の実装を伴う。
- 受入条件: 1つのcampaignで次の各段階の証跡が揃う。
  1. Red観測成功: Red攻撃が実行され、Oracleが攻撃成立（exploit成功）を観測した記録。
  2. Blind検知: Blind Blueが当該攻撃を検知した記録（検知ルール/alert発火）。
  3. 修正: 修復PR用変更案が生成され、静的スキャン・unit/integration・Oracle Exploit Replay・Benign Regressionを通過した記録。
  4. 攻撃不成立＋正常回帰成功: 再評価でOracleが攻撃不成立（exploit predicate FAIL）を観測し、かつ正常系regressionが全passした記録。
  5. レビュー待ち: PR承認ゲートでhuman review待ち状態に到達した記録。
  単一成功のみ、またはRed観測成功なしの終端到達は不合格。fixture集のみでは不合格。
- 依存: R-007〜R-011, R-016, R-024, R-030, R-035, R-040
- v1対象: v1

### C. Oracle

#### R-013 Oracleの判定対象と非対象
- 根拠: R（原レポート L78–L91）、S（Sankai A3）
- 義務: MUST
- 内容: 判定対象はexploit実行結果、HTTP status/exit code、ファイル・DB・ネットワーク状態変化、alert発火有無、test pass/fail、evidence hash一致。非対象は主観評価、文章品質、Blue推論のもっともらしさ、攻撃手法の創造性、修正方針の好み、自然言語自己採点。
- 受入条件: Oracle predicateに非対象項目が混入していないことをレビューで確認。
- 依存: R-014, R-015
- v1対象: v1

#### R-014 OracleのLLM非依存と分離配置（例外なし）
- 根拠: R（原レポート L91, L297, L555）、S（Sankai A3、監督判断: 例外を新設しない）
- 義務: MUST（無条件。例外条項を設けない）
- 内容: OracleはLLM非依存・再実行可能・fail closed。GPU/LLM queueから分離し、OOM・timeout・hallucinationが判定へ波及しない。Oracle判定経路にLLM呼び出しを一切含めない。
- 受入条件: Oracle実行経路にLLM呼び出しが無いことを構成で確認。T16（offline replay一致）が通る。例外を認める記述が要件・仕様に存在しないことをレビューで確認。
- 依存: R-013
- v1対象: v1

#### R-015 Oracleの記録項目と限定write
- 根拠: R（原レポート L297）、S（Sankai A3）
- 義務: MUST
- 内容: 入力digest、predicate、actual/expected、verdictを記録。read-mostlyでfixture resetのみ限定write。
- 受入条件: Oracle記録が台帳に残り、fixture reset以外のwriteがpolicyで拒否される。
- 依存: R-013, R-016
- v1対象: v1

#### R-016 判定不能と攻撃不成立の区別
- 根拠: S（Sankai A3/A4）、R（原レポート L500–L523）、原典調査（cybergym-faq L14, L25）
- 義務: MUST
- 内容: Oracle verdictはPASS/FAILに加え、判定不能（実行障害・取得不能・未確認）を区別する。実行障害のFAILを修復成功に数えない。
- 受入条件: T14の「exploit predicate FAIL=修復成功」は攻撃不成立のFAILに限定され、実行障害はHOLD/未確認として別記録。T17〜T20と整合。
- 依存: R-013, R-014, R-031
- v1対象: v1

### D. 必須テストケースと受入試験

#### R-017 T01〜T20の必須化と対応表
- 根拠: R（原レポート L500–L523）、S（Sankai A4）
- 義務: MUST
- 内容: T01〜T20を必須テストとして保持し、各合格条件を固定。追加・削除は要件レビューで判断。
- 受入条件: 要件→テスト対応表があり、各Tがどの要件を検証するか追跡可能。
- 依存: R-002, R-016
- v1対象: v1

#### R-018 T13のFP閾値とT09のlossless範囲の確定（監督確定値）
- 根拠: S（Sankai A4、監督確定）
- 義務: MUST
- 内容:
  - T13: ラボ固定benign suiteで FP0 を受入基準として事前固定する。現実の母集団precisionは保証しない旨を明記する。
  - T09: losslessは「秘匿化で失う秘密を除き、scanner findingのID/件数/位置/参照/拡張フィールドを保持する契約」とする。raw secretは保存しない。
- 受入条件: T13のbenign suite fixture一覧とFP0受入が事前固定記録として存在する。T09の保持フィールド一覧（ID/件数/位置/参照/拡張フィールド）と秘匿化対象境界が文書化され、contract testが通る。
- 依存: R-017, R-025, R-033
- v1対象: v1

#### R-019 契約試験と外部実接続検証の区別
- 根拠: U、S
- 義務: MUST
- 内容: 契約試験（I/F適合）と実接続検証（実環境動作）を区別し、契約試験通過を実接続済みと表示しない。
- 受入条件: HTML・台帳・レポートで両者が別状態として表示される。
- 依存: R-017, R-035
- v1対象: v1

### E. 認可・隔離・外部データ境界

#### R-020 認可対象と隔離環境への限定
- 根拠: R（原レポート L48, L314）、S（Sankai B1）
- 義務: MUST
- 内容: 許可された検証対象と隔離環境だけを操作できる。Campaign scopeとauthorizationを検証しauthorized target listを作る。
- 受入条件: 範囲外targetへの操作がpolicyで拒否され、拒否が台帳に残る。
- 依存: R-021, R-024
- v1対象: v1

#### R-021 外部データのuntrusted扱いと二段階実行
- 根拠: R（原レポート L410–L423）、S（Sankai B1）
- 義務: MUST
- 内容: 外部Web・ログ・Issue・source comment・READMEをuntrusted dataとして扱い、message-layer分離、evidence→plan→policy check→tool executionの二段階以上、structured schema経由のwrite/shell/network、tool responseの再評価を行う。
- 受入条件: T06（indirect prompt injection）が通る。Web内の実行指示はfindingとして記録される。
- 依存: R-020, R-022
- v1対象: v1

#### R-022 RustXNG queryの機密送信禁止とredact
- 根拠: R（原レポート L290, L421, L628）、S（Sankai B1/C1）
- 義務: MUST
- 内容: credential、private hostname、source secretをqueryへ送らない。queryの機密部分をredactし、source URL hashと取得時刻を保存。
- 受入条件: T05（credential leakage）が通る。redact前後の台帳追跡は除去した種類/位置/ルール版のみを記録する。秘匿化前本文や低エントロピー秘密の単純hashは保存しない。
- 依存: R-021, R-030
- v1対象: v1

#### R-023 外側OS隔離の必須化と未構成HOLD
- 根拠: R（原レポート L425–L441）、S（Sankai B1）、U、原典調査（deepseek-safety L13, rustdsh-readme L444）
- 義務: MUST
- 内容: DSH sandboxをDefense-in-Depthの一層とし、外側にVM/container/network namespaceを配置。危険なtarget実行は外側OS隔離必須、未構成はHOLD。
- 受入条件: 外側隔離未構成時に危険実行が起動できずHOLD記録。隔離方式の選択理由が記録される。
- 依存: R-020
- v1対象: v1

#### R-024 依存ツール不在・認証不在の接続不可明示
- 根拠: U、S、R（原レポート L522–L523）、S（Sankai C2）
- 義務: MUST
- 内容: 依存ツール不在・認証不在は接続不可を明示し、PASSを捏造しない。T19/T20と整合。初期スナップショット（0.3節）のPATH不在状態を接続状態表示の初期値とする。
- 受入条件: 接続不可状態がHTML・台帳に表示され、該当テストは未確認として残る。trivy/codeql/rdshのPATH不在が初期状態として台帳に記録される。
- 依存: R-019, R-031
- v1対象: v1

### F. データ契約と正規化

#### R-025 共通正規化形式の固定
- 根拠: R（原レポート L260–L281）、S（Sankai B2）
- 義務: MUST
- 内容: schema_version、campaign_id、run_id、actor、source、event_type、timestamp、target、finding、evidence_refs、previous_event_hash、event_hashを持つ正規化形式を固定。生JSON保持は秘密除去済みの元構造JSONのみとし、raw secretは保持しない。
- 受入条件: 主要アダプター出力が正規化形式に変換され、contract testを通る。生JSON保持範囲が秘密除去済みであることを確認。
- 依存: R-026, R-027, R-033
- v1対象: v1

#### R-026 P0の8契約JSON Schema先行固定
- 根拠: R（原レポート L489–L496, L693）、S（Sankai B2/C4）
- 義務: MUST
- 内容: Campaign/Run/EvidenceEvent/OraclePredicate/Finding/Remediation/Approval/FinalVerdictのJSON Schemaを先に作り、Red/Blue/Oracle全adapterが同じcontract testを通る。
- 受入条件: 8契約のSchemaとcontract testが存在し、主要アダプターが通る。
- 依存: R-025
- v1対象: v1

#### R-027 SARIFを第一候補のinterchangeとする
- 根拠: R（原レポート L281, L513）、S（Sankai B2）
- 義務: SHOULD
- 内容: CodeQL等のstatic-analysis findingsはSARIFを第一候補とする。T10でfinding count/identifiers一致を確認。
- 受入条件: T10が通る。SARIF以外を採る場合は理由を記録。
- 依存: R-025, R-026
- v1対象: v1

#### R-028 kaggle-campaign-opsを既存仕様と断定しない
- 根拠: R（原レポート L15, L42, L689）、S（Sankai B2/C4）
- 義務: MUST
- 内容: 当該リポジトリは未取得・検証不能として扱い、API/schemaを既存仕様と断定しない。再取得後にcontractが汎用的な場合のみ置換検討。
- 受入条件: 要件・仕様に「未指定」と明記。置換判断は再取得後の別判断として記録。
- 依存: R-026
- v1対象: v1（未指定の明示）。置換は将来運用条件。

### G. 証跡・ハッシュチェーン・検証

#### R-029 hash chain式とCAS/ledger分担
- 根拠: R（原レポート L443–L449）、S（Sankai B3）
- 義務: MUST
- 内容: event_hash = SHA256(canonical_event || previous_event_hash)。artifact本体はCAS、ledgerにはartifact SHA-256を保存。
- 受入条件: T01〜T03が通る。chain verification結果自体もevidence化。
- 依存: R-025, R-030
- v1対象: v1

#### R-030 audit recordの最低必須項目
- 根拠: R（原レポート L451–L469）、S（Sankai B3）
- 義務: MUST
- 内容: timestamp、actor/agent role、campaign/run ID、effective model、tool name、sanitized args、permission decision、target identifier、exit/status、result digest、previous hash、current hash。human approval identityはprivileged actionのみ必須。
- 受入条件: 記録欠落がrun manifest照合で検出される。
- 依存: R-029
- v1対象: v1

#### R-031 Evidence Storeの権限と保持
- 根拠: R（原レポート L298）、S（Sankai B3）
- 義務: MUST
- 内容: append-only ledger、artifact CAS、hash chain。agentはappend、auditorはread、deleteはretention workflowのみ。tamper-evidence・backup・retentionを備える。
- 受入条件: 権限違反操作が拒否され台帳に残る。
- 依存: R-029, R-030
- v1対象: v1

#### R-032 末尾削除・全体再計算への外部trust anchor（v1実装）
- 根拠: S（Sankai B3、監督判断）、原典調査（c2sp-checkpoint L11, crosby-2009 L101）、U
- 義務: MUST
- 内容: hash chain単独では末尾削除・全体再計算を検知できないため、v1で外部trust anchor検証を実装する（未来へ延期しない）。v1は別管理の読み取り専用checkpointを検証する。より強い外部署名サービス運用は将来運用条件。hash一致は操作の真実性を証明しないと明記。全体rewriteの不可能は保証せず、信頼できる別管理checkpointと整合しない改変を検出する。
- 受入条件: 別管理読み取り専用checkpointの検証コードが実装され、改変を注入したテストで検知FAILが観測される。checkpointのoperator保管、agent/workerのwriteからの分離、最終検収/再生前の照合が構成で確認される。checkpoint未提供時はHOLD。署名サービス運用は将来。same-admin敵対ホストを守る保証はしない旨を明記。
- 依存: R-029, R-031
- v1対象: v1（別管理読み取り専用checkpoint検証）。外部署名サービス運用は将来運用条件。

#### R-033 秘匿化後canonical対象とraw secret非保存
- 根拠: R（原レポート L508, L621）、S（Sankai B2/B3）、原典調査（owasp-logging L686, L696）、U
- 義務: MUST
- 内容: raw secretは保存しない。秘匿化後のcanonicalをhash対象とする。full snapshot rewriteを許さない。rewriteの不可能は保証しない。検出はR-032のcheckpoint検証が担う。
- 受入条件: T05が通る。canonical対象が文書化され、rewrite操作がpolicyで拒否される。R-032のcheckpoint検証との連携が構成で確認される。
- 依存: R-029, R-030, R-032
- v1対象: v1

### H. 権限分離・承認ゲート・本番変更制御

#### R-034 role分離とcredential namespace分離
- 根拠: R（原レポート L287, L350, L583）、S（Sankai B4）
- 義務: MUST
- 内容: operator/orchestrator/agentを分離。BlueとRedのcredential namespaceを分離。Blue Agentにmain push権限を与えない。
- 受入条件: 権限違反操作が拒否され台帳に残る。
- 依存: R-009, R-020
- v1対象: v1

#### R-035 環境別の自動適用ポリシー（監督確定）
- 根拠: R（原レポート L585–L595）、S（Sankai B4、監督確定）
- 義務: MUST
- 内容:
  - Dev: 既定deny。
  - Disposable lab: 一時workspaceに限り、明示scopeで検証済みpatchの自動適用を許可。
  - Production: 常時deny。v1はproduction実行経路を持たない。
  - Staging: approval必須（将来運用条件）。
- 受入条件: T08（production write default deny）が通る。Dev既定denyとラボ一時workspace限定適用が構成で確認される。
- 依存: R-034
- v1対象: v1（lab/Dev範囲）。Staging/Productionは将来運用条件。

#### R-036 required checkとhuman review
- 根拠: R（原レポート L579–L583）、S（Sankai B4）
- 義務: MUST
- 内容: mainでunit-tests+integration-tests+trivy+codeql+evidence-verify+oracle-regression+red-replay+human-reviewをrequiredとする。これらはminimum必須checkであり、列挙を上限にしない（追加checkを妨げない）。
- 受入条件: 欠けたcheckでmergeできない構成が文書化される。
- 依存: R-034, R-035
- v1対象: v1（構成文書化）。GitHub実接続は将来運用条件。

#### R-037 production credentialの非委譲
- 根拠: R（原レポート L595）、S（Sankai B4）
- 義務: MUST
- 内容: production credentialをBlue Agent processへ渡さず、merge後に別trust boundaryで保持。
- 受入条件: credentialがagent processから参照不能であることを構成で確認。
- 依存: R-034
- v1対象: v1（設計）。実運用は将来運用条件。

### I. 候補技術・adapter・導入

#### R-038 コンポーネント責務とI/F境界の固定
- 根拠: R（原レポート L5–L13, L171–L173, L287–L290）、S（Sankai C1）
- 義務: MUST
- 内容: rustdsh（制御プレーン）、DSH（実行基盤）、DSH Adapter（境界）、RustXNG（読取り専用grounding）の責務・権限・I/Fを固定。RustXNGはBlueのみ、原則Redから直接使用させない。plugin mountを基本としred-eval/blue-blind/blue-collab profileを分ける。RustXNG standard/clean優先、provider条件の個別確認を落とさない。rdsh HARNESS-ADAPTER原典はcompile-time trait接合でありruntime adapter選択ではない。Pythonから実際の公開CLIを呼ぶadapterとRust内部traitを混同しない。未確認CLIフラグは仕様として捏造しない。
- 受入条件: 責務表とI/F表が存在し、contract testが通る。compile-time traitとruntime adapterの区別が文書化される。
- 依存: R-026, R-039
- v1対象: v1

#### R-039 adapter contract versioningとprivilege非拡張
- 根拠: R（原レポート L289–L290）、S（Sankai C1）
- 義務: MUST
- 内容: rdshの公開CLIを呼び出す製品側adapterで、実行ファイル解決・引数生成・起動の契約を検証する。Rust内部のHarnessAdapter::{resolve_binary, build_command, launch}は参照する接合点であり、Pythonから呼べるAPIとは扱わない。adapter contract versioningを行い、adapter自身はprivilege拡張不可。effective command/envのdigestを記録。
- 受入条件: adapter contract testが通り、digestが台帳に残る。
- 依存: R-038
- v1対象: v1

#### R-040 主要アダプター7系統の導入条件と証跡
- 根拠: R（原レポート L292–L296, L601–L605）、S（Sankai C2、監督判断: 7系統具体列挙）
- 義務: MUST
- 内容: 主要アダプターとして Trivy, CodeQL SARIF, Wazuh, Sigma, RustXNG, rdsh/Red, LLM の7系統を具体列挙する。各系統について権限・生結果・版固定（pin）・Sigma draft限定・pluginのpackage version/source commit/hash/capability/config diff確認を要件化。latest追従せずpin、adapter契約試験、段階的更新。LLM adapterはprompt hash, requested/effective model, version/pin, temperature/seed, timeout, tokencap, tool calls, response metadataを記録。tool direct権限をモデルに与えない。未取得・PATH不在ツールは接続状態をHTML/台帳に明記。
- 受入条件: 7系統それぞれの証跡項目が台帳に残り、plugin導入gateが文書化される。未取得ツールの接続状態が表示される。
- 依存: R-026, R-038
- v1対象: v1（主要アダプター7系統）。実接続は将来運用条件。

#### R-041 推論基盤の配置とscheduler方針
- 根拠: R（原レポート L527–L555）、S（Sankai C3）
- 義務: SHOULD
- 内容: 実機RAMをstartup時検出。1台時は同一inference server共有＋context/tool permission/session分離＋security-sensitive phase直列化。OracleをGPU/LLM queueから分離。実測RSS/VRAM-equivalent/throughputをevidence化。
- 受入条件: 実機RAM検出と実測記録が台帳に残る。性能保証とは表示しない。
- 依存: R-014, R-030
- v1対象: v1（方針と記録）。性能実証は将来運用条件。

### J. RSI・HTML・ライセンス

#### R-042 RSIの9項目契約の適用
- 根拠: S（RSI節1–9）、U
- 義務: MUST
- 内容: 目的・評価条件の事前固定、baseline/candidate分離、証拠保存、失敗原因からの候補作成、実行中の仮説更新、同条件比較、train/validation/holdout分離、Experience Memory任意とsnapshot固定、採用判断と反映の分離を契約化。
- 受入条件: 9点ごとに次の検収条件を満たす。
  1. 事前freeze（目的・閾値・停止条件・採用基準）。
  2. baseline/candidateのID記録。
  3. run証跡の保存。
  4. trainから原因分析・候補案の作成。
  5. 仮説更新と停止条件の遵守。
  6. seed対比較と費用・安全性の確認。
  7. validation freeze、holdout汚染時は新規集合。
  8. Experience Memoryは任意、完了trainの抽象のみ、frozen snapshot。
  9. readyと反映を別操作。
  ネットワーク障害による無効runとcandidate由来未完了を区別し、分母を事前固定する。immutableモデルが無い比較は探索用とし、改善実証・promotion-readyへ進めない。決定的なpolicy候補のラボ比較はLLMなしで可能。
- 依存: R-004, R-016, R-030
- v1対象: v1（契約と最小評価）。Experience Memoryは任意。

#### R-043 モデルaliasのunpinned明示
- 根拠: U、S（RSI節2）
- 義務: MUST
- 内容: モデルaliasがunpinnedである場合、その旨を明示し、比較条件の同一性を過大表示しない。
- 受入条件: 台帳・HTMLにpin状態が表示される。
- 依存: R-042, R-030
- v1対象: v1

#### R-044 HTMLモニタリング（ARTEX型）
- 根拠: U、R（原レポート L481）、原典調査（artex-readme L18, L22, L42）
- 義務: MUST
- 内容: 実記録から状態を表示するHTMLモニター。概要、活動履歴（session/tool call）、承認待ち、証跡検証状態、未実行・進行中・失敗・HOLD・未確認を表示。原典実装は複製しない。HTMLには実行を判断する項目だけを表示し、ライセンスや実装細部を各UI画面へ強制表示しない。トレーサビリティは仕様/docsへ置く。
- 受入条件: 1campaignの終端までがHTMLで追跡でき、未確認・HOLDがPASSと区別表示される。
- 依存: R-012, R-019, R-024, R-030
- v1対象: v1

#### R-045 MITライセンスと実行範囲の除外
- 根拠: U（MIT）、S（実行範囲解釈: 任意の第三者攻撃・本番自動変更・対外公開をGoalに含めない）
- 義務: MUST
- 内容: 自身のコードはMIT。任意の第三者攻撃、本番自動変更、対外公開をGoalに含めない（監督の実行範囲解釈）。
- 受入条件: LICENSEがMIT。要件・仕様に除外が明記される（HTML各画面への強制表示は不要）。
- 依存: なし
- v1対象: v1

### K. 追加要件（改訂で追加）

#### R-046 ATT&CK mappingとKEV/EPSS enrichmentの実装要件
- 根拠: R（原レポート P1）、S（監督判断: 単なる表の参照でなく実装・日付・出典・期限切れ・未取得挙動を持つ要件へ）
- 義務: MUST
- 内容: ATT&CK mappingとKEV/EPSS enrichmentは、単なる表の参照ではなく、実装・データ日付・出典・期限切れ判定・未取得時の挙動を持つ要件とする。
- 受入条件: enrichmentのデータ日付・出典が台帳に記録される。期限切れ時と未取得時の挙動（未確認表示、優先付けへの影響範囲）が定義され、テストで確認される。
- 依存: R-008, R-040
- v1対象: v1

#### R-047 P2機能のv1ラボ対応
- 根拠: S（監督判断: P2の自動Purple反復・複数モデルrouting・detection tuning・timeout/crash回復はv1ラボでも試せる機能として対応し、全部将来へ追い出さない）
- 義務: SHOULD
- 内容: 自動Purple反復、複数モデルrouting、detection tuning、timeout/crash回復を、v1ラボで試せる機能として対応する。
- 受入条件: 各機能がv1ラボで少なくとも1回動作した記録がある。本番運用・性能保証は将来運用条件。
- 依存: R-010, R-041, R-042
- v1対象: v1（ラボ機能）。本番運用は将来運用条件。

#### R-048 完成条件の総合性
- 根拠: S（監督判断: 完成条件は単一成功だけでなくT01-20, RSIゲート, 通信/途中停止failclosed, 外部adapter契約試験, ローカル実動閉ループと通常回帰）
- 義務: MUST
- 内容: v1完成条件は単一成功ではなく、T01〜T20全件、RSIゲート、通信/途中停止のfail closed、外部adapter契約試験、ローカル実動閉ループ、通常回帰の全てを含む。外部サービスlive未接続を隠さない。
- 受入条件: 上記全ての検収記録が揃う。未接続外部サービスは接続状態として明示される。
- 依存: R-012, R-017, R-042
- v1対象: v1

#### R-049 モデル体制の実証状態の分離記録
- 根拠: U（監督Codex/レビューGLM5.3/実装DeepSeek1017→Ollama登録モデル）、S（監督判断: 指定・開発時呼出成功・製品接続検収未実施・モデルpin不成立の4点を分ける）
- 義務: MUST
- 内容: モデル体制について次の4点を分離して記録する。
  1. ユーザー回答による指定（監督Codex、レビューGLM5.3、実装DeepSeek1017→Ollama登録モデル）。
  2. 開発時の呼出成功（Ollama /api/tagsで登録確認済み、DeepSeek+GLM実呼び出しログあり）。
  3. 製品接続の検収未実施。
  4. モデルpin不成立（aliasのunpinned）。
- 受入条件: 台帳・HTMLに4点が別状態として表示される。「確認済み」を実証済みと誤読させない。
- 依存: R-043
- v1対象: v1

## 3. P0/P1/P2機能の追跡

| 原レポート優先度 | 機能 | 対応要件 |
|---|---|---|
| P0 | Campaign/run schema | R-025, R-026 |
| P0 | Evidence ledger, hash chain | R-029, R-030, R-031, R-032, R-033 |
| P0 | Oracle contract | R-013, R-014, R-015, R-016 |
| P0 | rustdsh role/profile分離 | R-034, R-038, R-039 |
| P0 | Blind Blue | R-009, R-010 |
| P0 | Trivy/CodeQL adapter | R-027, R-040 |
| P0 | PR-only remediation | R-011, R-035, R-036, R-037 |
| P1 | Wazuh ingest | R-008, R-040 |
| P1 | Sigma detection-as-code | R-010, R-040 |
| P1 | RustXNG grounding | R-022, R-038 |
| P1 | KEV/EPSS enrichment | R-008, R-040, R-046 |
| P1 | Blue remediation loop | R-011, R-012 |
| P1 | ATT&CK mapping | R-002, R-007, R-046 |
| P2 | autonomous Purple iteration | R-010, R-042, R-047（v1ラボ機能） |
| P2 | multi-model routing | R-041, R-043, R-047（v1ラボ機能） |
| P2 | detection tuning | R-010, R-042, R-047（v1ラボ機能） |
| P2 | 50–500 host hardening | R-005（性能保証にしない、将来運用条件） |
| P2 | dashboard/reporting | R-044 |
| P2 | chaos/recovery | R-031, R-032, R-047（v1ラボ機能） |

## 4. T01〜T20対応表

| テスト | 内容 | 対応要件 |
|---|---|---|
| T01 | Evidence改ざん→verifier FAIL | R-029, R-031 |
| T02 | event削除→chain break | R-029, R-032 |
| T03 | event順序変更→chain break | R-029 |
| T04 | Red context leakage不可 | R-009, R-034 |
| T05 | secret plaintext無し | R-022, R-033 |
| T06 | indirect prompt injectionで無承認tool実行なし | R-021 |
| T07 | unauthorized shell→policy deny | R-020, R-021 |
| T08 | production write→default deny | R-035 |
| T09 | Trivy正規化lossless（秘密除去済み、ID/件数/位置/参照/拡張保持） | R-025, R-018 |
| T10 | CodeQL SARIF import一致 | R-027 |
| T11 | Sigma syntax validation | R-040 |
| T12 | Sigma attack fixture発火 | R-040 |
| T13 | benign fixture FP0（事前固定、母集団precision保証外） | R-018, R-040 |
| T14 | patch exploit regression（攻撃不成立FAIL=修復成功） | R-011, R-016 |
| T15 | patch normal regression全pass | R-011 |
| T16 | Oracle offline replay一致 | R-014 |
| T17 | DSH crash→incomplete fail closed | R-016, R-024 |
| T18 | model timeout→retry上限後HOLD | R-016, R-024 |
| T19 | Wazuh unavailable→PASSにしない | R-024 |
| T20 | RustXNG upstream failure→hallucinated evidence作らない | R-022, R-024 |

## 5. 敵対的検証への申し送り（要約）

- FAILの意味（攻撃不成立/実行障害/判定不能）の区別はR-016で固定。
- KPI母集団・分母・除外規則はR-002で未確定欄を残しつつ、測定機能実装はv1必須。
- Blind中の漏洩経路（共有モデル・cache・telemetry・証跡ID・経験メモリ）はR-009/R-034/R-042で扱う。
- hash chain末尾削除・全置換はR-032で外部trust anchorをv1実装。
- 生出力保持とsecret非保存はR-025/R-033で両立条件を明示。
- policy迂回境界と隔離保証の非等価はR-021/R-023。
- P0完成条件はR-012/R-026/R-028/R-048。
- HTML監視項目はR-044。
- モデル体制の実証状態はR-049で4点分離。


#### R-050 フロントエンドの設計・レビュー担当
- 根拠: U（ユーザーの追加指示、2026-11-06）。
- 義務: MUST。
- 内容: ai-instructions-frontend の SKILL.md と必要な参照文書を GLM 5.3 に渡す。HTML・製品フロントエンドの画面構成、視覚デザイン、操作・状態をGLMが設計・レビューし、DeepSeekが実装する。
- 受入条件: 適用したスキルファイルのhash、GLMへの入力と設計結果、DeepSeek実装、GLMレビュー、PC・スマホ・操作確認の記録が揃う。
- 依存: R-044, R-049。
- v1対象: v1。

現行実装の検証結果と配置は[製品検収の対応表](../PRODUCT-ACCEPTANCE.md)を参照する。3段階の見直しはCOMPACTION-01〜03に保存した。

## 2026-11-07の承認済み追加要件

R-051〜R-063は[REQUIREMENTS-EXTENSION](REQUIREMENTS-EXTENSION.md)、実装契約は[SPEC-ASSESSMENT](SPEC-ASSESSMENT.md)、追加試験は[ACCEPTANCE-EXTENSION](ACCEPTANCE-EXTENSION.md)を正本とする。既存R-001〜R-050を緩和しない。実利用者試験の基準確定と実施が終わるまで製品完成にはしない。
