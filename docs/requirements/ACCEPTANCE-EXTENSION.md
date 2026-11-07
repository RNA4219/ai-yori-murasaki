---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewed
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
---

# ACCEPTANCE-EXTENSION（追加受入・CLI/API契約・試験・完成阻止条件、R-051〜R-063）

本稿は[要件拡張](REQUIREMENTS-EXTENSION.md)と[SPEC-ASSESSMENT](SPEC-ASSESSMENT.md)を検収可能な契約に固定する。既存R-001〜R-050の完成条件を緩めない。製品は未完成。R-061（実利用者試験）は2026-11-07のユーザー訂正により撤回し、試験未実施を完成阻止理由としない。

## 1. CLI契約（既存 `API-CONTRACTS.md` §1 を継承、A-06 対応）

stdout envelope・exit code・reason_code列挙は既存契約を維持。追加コマンド:

| コマンド | 必須args | 成功 | 失敗例 |
|---|---|---|---|
| `aym assess import` | `--campaign ID`, `--run ID`, `--format trivy\|sarif`, `--input FILE`, `--context FILE` | exit0 `data={finding_ids, coverage_ref, report_ref}` | artifact不正→exit2 `input_invalid`。個別Findingが `unknown` でも保存成功はexit0（発見の確定と処理成功は別） |
| `aym assess show` | `--campaign ID` | exit0 `data={report}`（operator view） | report不在→exit3 `hold` |
| `aym assess cvss` | `--vector VECTOR`, `--basis FILE` | exit0 `data={cvss_ref, status}` | 不正ベクトル→exit2 `input_invalid`、根拠欠損→exit3 `hold` |
| `aym assess lab` | `--staging-root PATH`, `--manifest FILE` | exit0 `data={lab_result_ref}` | 資材不足→exit3 `hold` |
| `aym assess study` | `--plan FILE`, `--results FILE` | exit0 `data={study_ref}` | 未実施→exit3 `incomplete` |

### 1.1 hold時のexit code統一（A-06 対応）

- **処理の成功と発見の確定は別**。import保存成功はexit0でも個別Findingの `status=unknown` を保持。
- 命令全体の未完了は **exit3**、`reason_code ∈ {hold, incomplete}`。
- 入力不正は **exit2** `input_invalid`。
- policy違反は **exit4** `policy_violation`。
- `reason_code` 列挙への追加: `hold`、`incomplete`、`input_invalid`、`policy_violation`、`freeze_modified`。既存 `API-CONTRACTS.md` §1 の列挙に追記する（既存値は変更しない）。
- `enrichment.import_snapshot`/`query_snapshot` を実呼び出しする。scanner contract≠live。ソースに書かれた指示は実行しない。
- 未知CVE適合は `unknown`（未確認）とし、適合済みと表示しない。

## 2. read-only API追加（既存 §2 を継承、operatorローカル専用、A-02 対応）

| method | path | 成功 | 失敗 |
|---|---|---|---|
| GET | `/api/campaigns/<id>/assessment` | `{report}`（operator view、各ref解決済み投影） | 404 / 解決不能は `status:"hold"` |

- 集約エンドポイントは `/api/campaigns/<id>/assessment` の1本のみ。個別 `/api/findings/<id>/assessment` は設けない（A-01の契約肥大を避ける）。
- `cvss` は `report.cvss` に含まれる `CvssRecord` の配列として返す。各要素は §1.5 の型（`status`/`vector`/`score`/`nomenclature`/`calculator`/`metric_basis`/`reason`）。
- Blue viewではphase queryによらず固定withheld応答とする。raw objects/ledgerは配信しない。
- 15秒読取更新・hidden休止・fetch5秒/1MiB cap・stale保持は既存 §5 を維持。

## 3. 評価マニフェスト事前固定（R-059, R-062, A-05 対応）

- 実行前に `{manifest_id, campaign_id, owner, hypothesis, action_delta, negative_control, freeze_date, splits:{train, validation, holdout}, metrics_def:{recall, precision, misclassification, basis_sufficiency, remediation_success, benign_retention, undetermined_rate}, denominators, artifact_versions:[{path, sha256}]}` をCAS保存し、freeze digestをtrusted rootへ別管理。
- train/validation/holdoutは事前snapshot digestで固定。holdout accessはpolicy eventとして記録、candidate hash変更でcontamination判定、一度見たholdoutは再使用禁止。
- 型別TP/FP/FN/TN/invalid/coverageを別計上。失敗・未計測を分母から隠さない。Oracleと検知器は独立。
- 正常系破壊・証拠偽造は総合点にかかわらず不合格。

### 3.1 Oracle独立性の検証（A-05 対応）

- Oracle期待値は **trusted_root配下の別ストア**（`trusted_root/oracle/<manifest_id>/`）に保存し、検知器実行コンテナにはマウントしない。
- 検知器実行は `network-none` の別コンテナで行い、`trusted_root` を参照できない。検知器が読めるのは `approved source` と通常telemetryのみ。
- 独立性はマニフェストの自己宣言boolに記録せず、**実行時検査結果**だけで判定する: 検知器コンテナのマウント一覧に `trusted_root` が含まれないことを `aym assess lab` が検査し、含まれれば exit4 `policy_violation`。
- 応答の生値から独立Oracleが再計算する。実行成功のboolを返すだけは禁止。
- 既存auth demoは保持。

## 4. 試験T21〜T35

T21〜T34は受領番号を保持（[要件拡張 §2](REQUIREMENTS-EXTENSION.md)の表を正とする）。T35を追加:

- **T35（CVSS）**: 有効ベクトルで `CvssRecord` が `status=scored`、`score` が0.0〜10.0の小数1位、`nomenclature` が入力指標に応じてCVSS-B/BT/BE/BTE、`metric_basis` と `calculator="cvss/3.6"` が残る。不正ベクトルは `status=invalid`、`score=null`。未評価は `status=unevaluated`、`score=null`。CVSS閾値から業務優先度が自動確定されない。KEV/EPSS統合が維持される。

### 4.1 有限ローカル検査の具体契約（自動テスト実行可能）

- fixtureと実スキャナの主張を区別: `fixture` は固定入力、`live` は実接続。契約通過をliveと表示しない。
- 隔離Dockerの実ラボを拡張し、legacy auth demoも保持。任意外部攻撃は不要。
- 各類型の正例/負例/正常回帰を自動テストで実行し、`invalid`/`not_executed` を別計上。XSSで実ブラウザ不可時は `unverified` で完了不可。

### 4.2 実行評価器の境界（A-03, A-05 対応）

- `benchmark.py` は隔離Dockerを使用。`assessment_assets/Dockerfile`、`assessment_assets/runner.py`、`assessment_assets/target.py` に有限の5類型を置く。
- 新image `aym-assessment:1` を明示allowlistに追加。既存imageは変更しない。
- 各類型の実装:
  - **認可**: 権限が異なる利用者による実HTTPアクセスと正常管理者アクセス。
  - **SQL**: 実sqliteへの実クエリ実行と結果差分。
  - **path traversal**: tmp領域の実ファイル境界でのread試行。
  - **XSS**: コンテナ内の実Chromiumで実レンダリング。
  - **dependency**: 公式GHSAの条件を局所TLS試験で再現。`requests==2.31.0` と `requests==2.32.5` を別Python依存領域に置き、ネットワーク外部禁止。CVE-2024-35195の条件（同一Sessionで最初verify=False後同originでverify=Trueでも検証されない）を実行する。期待値は2.31.0で再現、2.32.5で非再現。期待値と実測の一致を合格条件とする。
- Chrome/依存資材不足は **HOLD**（exit3 `hold`）。成功をハードコードしない。
- Oracle期待値は `trusted_root` のmanifestから監督側のみ読む。Blueは別 `network-none` コンテナにapproved source/通常telemetryのみ、ground_truth/Red payload/session/期待配列はmountしない。応答の生値から独立Oracleが再計算する。実行成功のboolを返すだけは禁止。
- 既存auth demoは保持。

## 5. 実利用者試験（R-061撤回、R-057維持）

- R-061は2026-11-07のユーザー訂正で撤回した。実利用者試験は必須要件ではなく、未実施を完成阻止理由にしない。
- 実施する場合は任意の検証として扱う。準備HTML、課題7件、回答記録、匿名ID、採点ルーブリック、事前凍結、別画面の正解、結果集計の資料は[USER-STUDY-PLAN](USER-STUDY-PLAN.md)に履歴として残す。AI代役は実測としない。参加者募集は行わない。
- R-057の初心者向け表示要件は維持する。

## 6. 完成阻止条件（明示）

以下いずれかで最終完成を阻止する:
1. 5類型のいずれかで実行確認が `unverified`。
2. 正常系破壊・証拠偽造・認可境界突破。
3. 旧version成功の現version転用。
4. CVSS未評価/不正入力の数値捏造。
5. 未登録kindを `contracts.validate` に渡す実装（CI静的検査で阻止）。
6. Oracle独立性の実行時検査失敗（`trusted_root` が検知器コンテナにマウント）。

実利用者試験の未実施・基準未達は、2026-11-07のユーザー訂正により完成阻止条件から外した。

## 7. エラー例

- `aym assess import` で未知CVE適合 → 保存成功はexit0、個別Findingは `status=unknown`。命令全体の未完了はexit3 `incomplete`。
- 既存enrichment queryで期限切れ → 既存missing/expired契約に従い、優先度へ反映しない。
- `aym assess cvss` で根拠欠損 → exit3 `hold`、`status=unevaluated`。
- `aym assess lab` でChrome不足 → exit3 `hold`。
- 評価マニフェストfreeze後のidentity変更 → exit4 `freeze_modified`。
- 検知器コンテナに `trusted_root` マウント検出 → exit4 `policy_violation`。

## 8. 不一致と利用者試験の進め方

TLS・Chromium・依存資材の欠落、タイムアウト、観測不完全はexit3 holdとしinvalidへ計上する。観測は揃ったが検知器が脆弱版を検出しなければFN、安全な版を検出すればFPとして記録し、事前基準に従ってexit1 verdict_failとする。環境障害を性能の失敗・成功へ混入させず、未実行類型を完成扱いにしない。

利用者試験は2026-11-07のユーザー訂正により本Taskの完成条件から外した。実施する場合は任意の検証として扱い、AI代役を実測と記録しない。参加者募集は行わない。
