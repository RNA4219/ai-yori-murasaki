---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
---

# SPEC-ASSESSMENT — 評価・分類・修復・再検証の確定仕様（R-051〜R-056, R-060, R-062）

本稿は[要件拡張 R-051〜R-063](REQUIREMENTS-EXTENSION.md)のうち評価系を実装可能な粒度に固定する。既存R-001〜R-050の完成条件を緩めない。製品は未完成であり、本稿は実装済みを意味しない。画面レイアウトはGLMへ別委任するため、本稿は必要表示と情報投影の契約のみを記す。

GLM指摘A-01〜A-08を反映した改訂版。既存 `contracts.KINDS`（campaign, run, evidence_event, oracle_predicate, finding, remediation, approval, final_verdict）と `schemas/v1/*.schema.json` は**変更しない**。新規評価データは単一の **assessment report**（`assessment_version=1.0.0`）とその下位オブジェクトに集約し、別モジュール `assessment_contracts.py` と `schemas/v1/assessment.schema.json` で厳格に検証する。従来 `contracts.validate` へ未登録kindを渡さない。

---

## 0. 契約レイヤの分離（A-01, A-07 対応）

### 0.1 既存契約の不変

- `contracts.KINDS` の8種と `schemas/v1/*.schema.json` は**変更しない**。
- 既存 `remediation` kindはそのまま。本稿の「Remediation」は**別概念**であり、`assessment report` 内の `remediation` サブオブジェクトとして保持する。既存 `remediation` kindとの混同を避けるため、本稿のサブオブジェクトは常に `assessment.remediation` と修飾して呼ぶ。
- Findingは変更しない。新reportから既存FindingのCAS refへ一方向に参照する。finding_idは表示用識別子でありCAS refではない。評価レポートのない旧Findingは「未評価」と表示する。

### 0.2 新規契約モジュール

- `assessment_contracts.py` に `ASSESSMENT_KINDS = {"assessment_report"}` と `validate_assessment(kind, obj)` / `semantic_validate_assessment(kind, obj, store)` を新設。`contracts.validate` とは独立。
- `schemas/v1/assessment.schema.json` に assessment report の JSON Schema（`additionalProperties: false`、必須フィールド明示）を同梱。
- 呼び出し規約: 既存kindは `contracts.validate`、assessment reportは `assessment_contracts.validate_assessment`。**未登録kindを `contracts.validate` に渡すコードは禁止**（CIで静的検査）。

### 0.3 保存とledger接続

- 保存: `store.put_object(report)` → `report_ref = sha256`。
- ledger: `runtime.record_event(active_run, 'state_captured', 'orchestrator', evidence_refs=(report_ref,), extensions={'assessment_version': '1.0.0', 'assessment_ref': report_ref})`。
- 旧report・旧validationは上書きしない。新reportは `previous_report_ref` と `target_version` を持ち、`target_version` が現在のrunの `target_version` と異なる成功は **historical** 表示（§3.3）。

---

## 1. assessment report の型（CAS保存・immutable）

`Workspace/objects/<sha256>.json` に保存。トップレベル構造:

```
assessment_report = {
  schema_version: "1.0.0",
  assessment_version: "1.0.0",
  report_id: string,                 // asc-<UUID hex>。CAS hashとは別
  campaign_id: string,
  run_id: string,
  target: string,
  target_version: string,
  previous_report_ref: string|null,
  created_at: ISO8601,
  actor: string,                    // Run.actor.role。Run.actor全体のdictではない
  coverage: Coverage,
  findings: [FindingAssessment],     // 各finding_idごと
  cvss: [CvssRecord],
  business_impact: [BusinessImpactRecord],
  manifest_ref: string|null,         // §ACCEPTANCE §3 のマニフェスト
  extensions: object                 // 予約。現状空
}
```

`validate_assessment("assessment_report", obj)` は上記必須フィールドと型を検査し、`additionalProperties: false` で未知フィールドを拒否する。`semantic_validate_assessment` は §1.6 の相互参照規則を検査する。

### 1.1 Coverage（R-051）

```
Coverage = {
  categories: [{
    category: string,                // 例: "authz", "sqli", "xss", "path_traversal", "dependency_cve"
    status: "executed"|"not_executed"|"failed",
    reason: string|null,
    evidence_refs: [string]          // CAS ref
  }],
  created_at: ISO8601
}
```

- `status=not_executed` は未検出と別表示。`failed` は未検出にしない。
- `evidence_refs` は `store.get_object` で解決可能でなければならない（§1.6）。

### 1.2 Observation / Claim / Classification（R-052, R-053）

```
Observation = {
  observation_id: string,
  finding_id: string,
  kind: "http_response"|"file_state"|"scanner_output"|"dependency_version",
  payload_ref: string,               // 文字データのみ（§5）
  captured_at: ISO8601
}

Claim = {
  claim_id: string,
  finding_id: string,
  cause_statement: string,
  confidence_axis: "detection"|"cause"|"impact"|"severity",
  status: "confirmed"|"hypothesis"|"refuted",
  basis_refs: [string],              // 根拠となる診断記録のCAS refs
  refuted_by: string|null            // 反証を記録した診断記録のCAS ref
}

Classification = {
  classification_id: string,
  finding_id: string,
  cwe: { status: "confirmed"|"candidate"|"unknown", ids: [string], basis_refs: [string] },
  cve: {
    status: "confirmed"|"candidate"|"not_applicable"|"unknown",
    ids: [string],
    applicability: {
      product: string|null,
      version: string|null,
      condition: string|null,
      basis_refs: [string]
    }
  },
  owasp: { ids: [string], basis_refs: [string] },
  attack: { status: "observed"|"unknown", techniques: [string], observation_refs: [string] }
}
```

- CWE→ATT&CK自動確定禁止。
- CVEは製品/版/条件の適合根拠が揃うときのみ `confirmed`。`basis_refs` は全てCAS解決可能でなければならない（§1.6）。
- LLM確信度（自己申告bool/float）は格納しない。`status` は観測からの判定規則（§1.6）で決まる。

### 1.3 Remediation / Validation（R-055）

```
Remediation = {
  remediation_id: string,
  finding_id: string,
  proposal_ref: string,
  applied: bool,
  applied_env: string|null,
  applied_version: string|null,
  diff_ref: string|null,             // applied=true なら必須（§1.6）
  side_effects: [string]
}

Validation = {
  validation_id: string,
  remediation_id: string,
  checks: {
    attack_blocked: bool,
    benign_continued: bool,
    related_tests_passed: bool,
    oracle_evidence_ref: string,     // CAS解決必須
    version_recorded: bool,
    unverified_scope_recorded: bool
  },
  oracle_verdict: "PASS"|"FAIL"|"undetermined",
  result: "success"|"failed"|"undetermined"
}
```

- 修復成功 = 上記6チェックAND かつ `side_effects` 空 かつ `oracle_verdict=PASS`。HTTP403単独は不可。
- 技術的成功とPR承認ゲート（既存 `approval` kind）は別レコード。
- `oracle_verdict=undetermined` → `result=undetermined`、修復成功にしない。

### 1.4 原因単位の安定IDとdedup（R-051, R-060）

`cause_id = sha256(source\x1fkind\x1ftarget\x1fnormalized_cause_key)`。同一 `cause_id` への複数試行は1 Findingの複数Attempt（`attempt_index`）として保持し、Finding数・類型数に加算しない。`run_demo` の固定3試行は「1原因の3試行例」と明示し、独立3脆弱性と表示しない。

### 1.5 CVSS v4.0 算出（R-056, A-02 対応）

- 外部依存: Python `cvss==3.6`（LGPLv3+）。**第三者依存として明記**し、自作コードのMITへライセンス転記しない。`THIRD_PARTY_NOTICES` に名称・版・ライセンス・用途を記録。FIRST CVSS v4.0仕様の帰属表示を `THIRD_PARTY_NOTICES` と出力メタに記す。

```
CvssRecord = {
  finding_id: string,
  status: "scored"|"invalid"|"unevaluated",
  vector: string|null,              // 正規化済みベクトル文字列。invalid/unevaluated は null
  score: number|null,               // 0.0〜10.0、計算器の小数1位。invalid/unevaluated は null
  nomenclature: "CVSS-B"|"CVSS-BT"|"CVSS-BE"|"CVSS-BTE"|null,
  calculator: "cvss/3.6",
  metric_basis: { [metric: string]: [string] },  // 各指標の根拠CAS ref
  reason: string                    // invalid/unevaluated の理由。scored は空文字
}
```

- 11 base指標が未確定なら `score=null`、`status=unevaluated`。存在するvectorだけで検証済みにしない。
- `nomenclature` は入力指標の実際の指定により決める（base のみ→CVSS-B、threat 指定→CVSS-BT、environmental 指定→CVSS-BE、両方→CVSS-BTE）。環境/脅威未指定の仕様上の既定と、根拠がない業務影響値は混同しない。
- 不正ベクトルは `status=invalid`、`score=null`、`reason` に検証エラー。数値を捏造しない。
- 業務影響は別レコード（§1.5.1）として担当者が記入。CVSS値から業務優先度を自動確定しない。

#### 1.5.1 BusinessImpactRecord

```
BusinessImpactRecord = {
  finding_id: string,
  data_importance: "high"|"medium"|"low"|"unknown",
  external_exposure: "high"|"medium"|"low"|"unknown",
  business_stop_impact: "high"|"medium"|"low"|"unknown",
  reasons: string,
  author: string,
  recorded_at: ISO8601,
  unverified_axes: [string]         // 未確認軸の明示
}
```

各軸は `unknown` 許容。CVSS値から自動確定しない。

### 1.6 semantic_validate_assessment の規則（A-08 対応）

`semantic_validate_assessment("assessment_report", obj, store)` は以下を全て検査し、いずれか失敗で `AssessmentContractError` を送出する:

1. **共通スコープ整合**: `campaign_id`/`run_id`/`target_version`/`actor`/`created_at` が同一report内で一貫。`previous_report_ref` が非nullなら `store.get_object` で解決でき、その `target_version` が本reportの `target_version` と異なる場合は `historical=true` を導出（保存はしない、投影時計算）。
2. **CAS参照解決**: `coverage.categories[*].evidence_refs`、`classification.cwe.basis_refs`、`classification.cve.applicability.basis_refs`、`classification.owasp.basis_refs`、`classification.attack.observation_refs`、`validation.checks.oracle_evidence_ref`、`remediation.proposal_ref`、`remediation.diff_ref`（applied=true時）、`cvss.metric_basis` の全refが `store.get_object` で解決可能。
3. **Claim.refuted_by**: 非nullなら同一scopeの診断記録をCAS解決し、元の主張との関係を確認する。confirmedは§1.9の根拠規則を満たすときのみ。
4. **Classification.cve.status=confirmed**: `applicability.product`/`version`/`condition` が全て非null、かつ `basis_refs` が1件以上。
5. **Classification.cwe.status=confirmed**: `basis_refs` が1件以上。
6. **Remediation.applied=true**: `diff_ref` 非null、`applied_env`/`applied_version` 非null。
7. **Validation.result=success**: 6チェック全てtrue、`side_effects` 空、`oracle_verdict=PASS`。矛盾時は `AssessmentContractError`。
8. **CvssRecord.status=scored**: `vector`/`score`/`nomenclature` 非null、`score` が0.0〜10.0、`calculator="cvss/3.6"`。`status=invalid|unevaluated` は `vector=null`、`score=null`、`nomenclature=null`、`reason` 非空。
9. **FindingAssessment.finding_ref**: CASから既存findingを解決し、finding_id/campaign_id/run_idを照合する。report→Findingの一方向参照のみ。report作成後もFindingは改変しない。

### 1.7 公開関数契約（A-01, A-03, A-06 対応）

```
assessment.build_report(runtime, campaign_id, run_id, observations, context) -> report_ref
assessment.import_artifact(runtime, campaign_id, run_id, format, raw, context) -> {finding_ids, coverage_ref, report_ref}
assessment.load_report(runtime, campaign_id, view='operator') -> report | None
assessment.project_report(report, view, phase) -> dict
severity.score_cvss4(vector, metric_basis, store) -> CvssRecord
```

- `build_report`: `observations` は信頼されたproducerが作るFindingAssessmentのリスト。`context` は `{target, target_version, actor, previous_report_ref}` と任意のcoverage/cvss/business_impact/manifest_ref。actorは既存Run.actor.roleの文字列。外部importには追加4フィールドを許可しない。`validate_assessment` と `semantic_validate_assessment` を通した後 `store.put_object` し、`record_event` でledger接続。失敗時 `AssessmentContractError`。
- `import_artifact`: `format ∈ {"trivy","sarif"}`。既存 `normalize_trivy(raw)` / `normalize_sarif(raw)` を実呼び出し。scanner由来の主張は独立証拠なしに `confirmed` へ昇格しない（`status=hypothesis` または `unknown`）。戻り値の `finding_ids` は新規/更新FindingのID、`coverage_ref` は当該importのCoverage ref。artifact不正は `InputInvalidError`（CLI exit2）。
- `load_report`: 現在の `target_version` に一致する最新reportを返す。`view='operator'` は安全な投影フィールド、`view='blue'` は §2.1 の投影規則で秘密を除去。`phase` は `'blind'|'reveal'` で、`blind` 中はRed/Oracle由来情報を除去。
- `project_report`: 純関数。`view`/`phase` に応じた投影dictを返す。副作用なし。
- `severity.score_cvss4`: `vector` を `cvss==3.6` でパース。11 base指標未確定なら `status=unevaluated`。不正は `status=invalid`。`metric_basis` の各refを `store.get_object` で解決できなければ `AssessmentContractError`。

### 1.8 依存CVEの版適合経路（A-03 対応）

- `enrichment.import_snapshot`/`query_snapshot` は **ATT&CK/KEV/EPSSのみ**を扱う（既存契約維持）。
- CVE→影響版範囲の適合判定は **別モジュール `advisories.py`** が担う:
  - `advisories.import_advisory(store, source_url, raw, fetched_at, ttl) -> advisory_ref`。入力rawはGitHub公式APIのadvisory JSON形式（ghsa_id, cve_id, summary, description, vulnerabilities配列）。任意のHTMLから推測で版を抽出しない。元のJSONと抽出結果を別々にCAS保存し、source_urlはhttps、日時はtimezone必須、ttlは1〜31536000秒、未来日時は拒否。
  - 抽出結果は `advisory_version:"1.0.0", cve_id, source_url, fetched_at, ttl, raw_ref, entries:[{ecosystem,package,affected_range,first_patched_version,conditions}]` の閉じた型。raw_refはCAS解決し、抽出内容を原文JSONと照合する。任意dictやraw_ref不一致を拒否する。
  - 検証器は `advisories.validate_advisory(obj, store, now=None)` に所属する独立契約とする。新kind登録は不要。`advisories.py` 内の閉じたJSON Schemaとsemantic検証で、raw_refのCAS解決・抽出内容の原文照合・fetched_at/ttlの型・PEP440範囲を検査する。import保存前とmatch_version読取時の両方で必須。
  - 版はPyPIだけを対応範囲とし `packaging==26.0` の Version/SpecifierSet で解析する。公式APIの空白を除いたPEP440範囲（例 `<2.32.0`）のみ受理する。未対応ecosystem/範囲はunknown。これは確認済みの機能限界として表示する。
  - `advisories.match_version(store, advisory_ref, package, version, now=None)` は `{version_status:"affected"|"patched"|"not_applicable"|"unknown", applicability:"unknown", basis_refs, reason}` を返す。版一致と実際の条件成立を分ける。期限切れ、パッケージ不一致、不正版はunknownまたはnot_applicableであり、confirmedにしない。
  - CVEの確定は別途§1.9の条件成立根拠を要する。版が修正版でも再検証なしに修復成功としない。
  - 有限ラボではpsf/requests CVE-2024-35195（GHSA-9wx4-h78v-vm56、影響版 `<2.32.0`）を使う。同一Sessionの最初verify=False、その後同originのverify=Trueという条件を実測する。公式API原文の出所とhashを残す。

### 1.9 FindingAssessmentと診断根拠の検証

FindingAssessmentは `{finding_id,finding_ref,cause_id,category,title,attempts,observations,claims,classification,remediation,validation}`。categoryは5類型またはunknown、attemptsは1以上の整数。observations/claimsは配列、classificationは§1.2、remediation/validationは§1.3またはnull。cause_idは§1.4の64桁hash。scanner主張は原因確定前にはsource/ルール/場所単位の候補キーとし、独立原因の実証済み数と混同しない。

診断根拠は先にCASへ保存する。閉じた構造は `{diagnostic_version:"1.0.0", campaign_id,run_id,target,target_version,actor,captured_at,kind,details}`。kindはobservation/root_cause/cve_applicability/attack_observation/remediation_diff/oracle_validation。detailsは種類別に検証する。全refについてhash、同一campaign/target/version、runの所属を確認する。異なるrunを参照する場合は同じcampaign内の履歴として扱い、現在版と一致する証拠だけを採点に使う。

- root_cause: `{category,cwe_ids,cause,observation_refs}`。空の原因説明、空の観測ref、観測不解決はconfirmed不可。
- cve_applicability: `{advisory_ref,package,version,condition,observation_refs}`。新鮮なadvisoryの版範囲一致、条件の説明と観測ref解決を要する。
- attack_observation: `{technique_id,behavior,observation_refs}`。挙動説明と観測が必要。CWEだけから作らない。
- remediation_diff: `{before_version,after_version,diff,environment}`。適用先/版の一致が必要。
- oracle_validation: `{remediation_ref,attack_observation_refs,benign_observation_refs,related_observation_refs,unverified_scope,verdict}`。Oracleが生成し、各観測から再計算する。任意import/LLM自己申告からこのkindを作成しない。
- observation: `{summary,source,origin:"scanner"|"blue"|"red"|"oracle"|"operator",data}`。dataは秘密除去済みの構造化値。生のHTML/credentialを表示しない。

これらの型をassessment_contractsの独立検証関数で検査する。外部importのcontextにclassification/validation/diagnosticを渡してconfirmedを注入する経路を設けない。信頼境界内の診断producerだけが診断記録を作る。LLMの説明はhypothesisとして保存する。

Validationの6チェックのうちoracle_evidence_refはboolではなくrefである。「6チェック全てtrue」は正確には、5個のboolがtrue、Oracle refが解決可能でPASS、攻撃/正常/関連観測が揃い、適用済みremediationと版一致、side_effects空を意味する。

reportのprevious_report_refは同一campaign/targetの過去reportを指す。新reportと旧reportの版差からhistoricalを導出する。supersessionsやvalidation_supersessionを新規kind/fieldとして設けない。現target_versionはRun既存schemaに直下追加せず、report.contextとrun.extensionsの`assessment_target_version`で照合する。未記録の場合は初回assessmentで記録するイベントを正本とする。


---

## 2. 表示・投影契約（レイアウトはGLM委任）

必要表示: 状態（未調査/未検出/確認済み/修正案/再検証済み/判定不能）、各軸確かさ、分類根拠と未特定理由、CVSSと業務影響の分離、修復成功と承認の分離。色のみに依存せず状態名を併記。Red/Oracle秘密はBlue/UIへ投影しない。

### 2.1 投影規則

operatorも安全な表示用フィールドだけを明示列挙して投影する。raw observation data、credential、Red payload、Oracle正解は返さない。安全な要約と証拠hashは返せる。

view=blueの応答は固定値 `{status:"withheld",reason:"評価情報はBlueに非公開"}` とし、phaseや利用者指定queryにかかわらずreport内容・hash・件数・存否を返さない。このassessment APIにはBlueへのreveal経路を設けない。phase=reveal指定でも固定応答を維持する。既存campaign APIのFinding/修復派生情報にも同じ境界を適用する。

### 2.2 旧記録互換

評価レポートのない旧Findingは「未評価」。旧report/Validationは改変しない。previous_report_refとtarget_versionの差により旧版限定のhistorical表示とする。旧版の成功を現版の成功数へ入れない。

---

## 3. エラー例

- reportからのFinding参照が解決不能 → `hold`（未確認）。
- CVSSベクトル不正 → `status=invalid`、`reason_code=input_invalid`。
- 修復Validationで `oracle_verdict=undetermined` → `result=undetermined`、修復成功にしない。
- 旧版Validation参照 → historical表示、現在版の成功に数えない。
- 未登録kindを `contracts.validate` に渡す → CIで静的検査失敗（実行時は `AssessmentContractError`）。

---

## 4. 診断producerの境界

実行コードの正本はインストールされた本パッケージ。`assessment.import_artifact`は外部入力を候補としてのみ保存し、`benchmark`のOracle評価処理だけがラボ観測からroot_cause/cve_applicability/remediation_diff/oracle_validationを構成する。CLI/APIから任意の診断dictを確定レコードとして取り込む機能は設けない。検査では、外部contextによるconfirmed注入を拒否し、該当kindの保存呼出し元がこの列挙と一致することを確認する。

「監督側」はホストで動く`benchmark`プロセスを指す。ユーザーに許可されたローカルラボの実行器として、trusted_rootの凍結マニフェストとOracle期待値を読む。Blue/Redコンテナにはtrusted_rootもホストのCASもマウントしない。ホスト管理者によるソース改変・証跡の偽造への耐性を主張する仕様ではない。

### 4.1 独立評価時の実行ロール

既存Runtimeでは`verify`のロールは`auditor`、`reevaluation`は`oracle`である。独立した有限ラボの評価は、凍結した評価条件を持つ`verify`実行で行う。既存の状態遷移やロール割当ては変更せず、`auditor`を`oracle`と記録しない。

`oracle_validation`の成功は従来の`oracle`ロールに加え、次をすべて満たす`auditor`ロールでも認める。

- 保存済みRunのphaseが`verify`であり、評価マニフェストのrefとIDがRunの実際の保存フィールドに記録されている。
- reportの`manifest_ref`がそのrefと一致し、解決したマニフェストのIDも一致する。マニフェストは`frozen`で、trusted_rootに保存した凍結時のdigestと現在の内容が一致する。
- ホストの`benchmark`が、固定した実装とイメージで実行した記録がある。コンテナのマウント・通信・権限の実測検査に成功し、その検査記録と独立Oracleの再計算結果をtrusted_rootへ保存している。Blueの自己申告をこの記録の代わりにしない。
- 同じcampaign・run・target・versionの参照解決、適用済み修復、空の副作用、正常動作継続、各チェックとOracleのPASS、差分との一致を含む既存の成功条件をすべて満たす。

外部取り込みからこの例外へ到達する経路は設けない。評価条件の欠落・不一致・未凍結・変更・実測検査記録の欠落では成功を認めない。通常の`reevaluation`経路は引き続き`oracle`ロールを必要とする。この修正はGLMの検討記録 `v2-role-resolution-glm` に基づく。実装・回帰試験は別途必要であり、この記述だけで適合済みとはしない。
