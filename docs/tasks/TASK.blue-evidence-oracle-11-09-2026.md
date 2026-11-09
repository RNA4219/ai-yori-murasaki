---
task_id: 20261109-01
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: planned
last_reviewed_at: 2026-11-09
next_review_due: 2026-12-09
---

# P0 — Blue finding重複処理とOracleの行単位判定の整合

## Metadata
- priority: P0
- repo: RNA4219/ai-yori-murasaki
- base_branch: main
- work_branch: fix/blue-evidence-rsi-apply-20261109
- inspected_base_sha: `8dd42e00f431289a9379e37d9a9ffb9a56f5c59e`
- 依存: なし。P1の実装・検収を始める前にP0の検証を完了する。
- 役割: Codex=優先順位・停止判断・完成判定、GLM-5.3=分解・指示・レビュー・検証整理、DeepSeek-v4.1-Flash=コード・テストの実装。モデルを利用できない場合は代行したように記録せず停止する。

## Objective
Blueが観測された異なる不正アクセス行を正しく報告したとき、findingが重複破棄されず、非LLM Oracleの行単位判定と保存証跡が一致する。誤判定がRSI採用指標へ流れることを防止する。

## Scope
- In: `ai_yori_murasaki/agents/tools.py`, `oracle.py`, finding schema/契約、`tests/test_agent_core.py`, `tests/test_agent_lab_demo.py`、必要最小限のKPI/検証経路。
- Out: 別類型の検知能力の増強、未認可ターゲット、既存安全境界の緩和、新規性能成功主張。

## 現行HEADの静的確認（再現テスト結果ではない）
- `tools.py` の `_run_submit_findings`: 同一batchは `(path,role,category)` で重複除去し、`finding_id` もcampaign/run/path/role/categoryだけで導出する。異なるrefでも同じキーに落ちる。
- `oracle.py` の `blind_detection_check`: GET /record のstatus 200かつnon-adminの各行について、その行のrefを引用したfindingが必要。
- 既存`test_submit_findings_batch_registers_each_row`はroleが異なる2行であり、同一role・異なるrefの衝突を覆っていない。
- `blind_detection_check` のreportは `rows_requiring_detection`, `uncovered`, `passed` を出すが、この関数単体にFP件数の確定出力はない。既存`kpi.py`のprecision入力契約と混同しない。

## Requirements / Contract
1. **テストを先に追加し、変更前に失敗を記録**: CASで異なる2件を作成（同一GET /record, role=guest, category=authz, status=200、タイムスタンプまたは固有値を変える）。Blueに2件とも正規に発行し、2件を1batchで提出。保存findingとOracle判定を確認する。現状のコードからはsubmitted=1, duplicates=1, requiring=2, uncovered=1, passed=falseを予測するが、実行するまで予測値と記録する。
2. finding保存のidentityは**異なる観測行の証拠参照を失わない**単位へ統一。候補A: `(campaign,run,path,role,category,evidence_ref)` でevent-levelのstable idを作り、同一refのみ冪等。候補B: issue-level findingにrefsを集約する。既存1入力1finding契約と最大32 referencesを鑑み、まずAを優先して比較・レビューし、影響を検証する。ID変更の既存証跡・KPI・monitor影響を明記。
3. Oracleは1行単位のTP/FNを独立算出する。同一ref二重提出でTPを増やさない。benign行へのfinding、不正・未発行ref、参照と報告されたpath/role/methodの不一致をFP/不正参照として区別する。未計測のFPを0として成功表示しない。KPIのfinding単位precisionと行単位recallの分母を明示し、整合を確認する。
4. Red/Blue/Purpleの発行済みref集合、CAS実体、run帰属の境界を維持する。正解・hidden Oracle・他agentのprivate refをBlueへ開放しない。
5. RSIへの測定供給は検証済み、対象・分母・identityを固定した評価結果だけを受理する。不一致はfail/HOLDとし改善成功へ変換しない。

## Regression matrix (DeepSeek担当)
- 同一path/role/category、異なる2 refの不正アクセス
- 同一refの2回提出（同一batch、別呼出）
- benign + unauthorized混在、FPとFNの区別
- 時刻順・逆順・別batch順
- 他agentの未発行ref・他campaign/runのref・改ざんref
- evidence_ref欠落、形式不正、存在しないCAS
- 修正前に正常な既存認可シナリオ、従来の複数roleケース、既存KPI契約

## Local commands / Evidence
1. `python -m pytest -q tests/test_agent_core.py tests/test_agent_lab_demo.py` （赤を先に観測）
2. コード・テスト実装後に同コマンドを再実行し、before/afterのstdout、exit code、HEAD SHA、fixture refsとOracle reportを保存。
3. `python -m pytest -q -rs` と安全境界の対象回帰を実行。Docker/実LLMは環境がある場合のみ。外部クラウド呼出は原則不要。
4. Taskに実コマンド、GLMレビュー、独立Oracle再計算、境界テストの結果を記録。評価実験を始める場合は `docs/CONTRACTS.md#evaluation-identity` のpreflight/postrun必須。

## Acceptance Criteria
- [ ] beforeの最小再現が実測として保存された。
- [ ] 異なる2行のfindingと証拠が欠落せず、Oracleで正しくカバーされる。
- [ ] 同一行の二重報告は冪等、benign/FP/FNと正常系は期待どおり。
- [ ] Red/Blue/Purpleの証拠参照権限に回帰なし。
- [ ] 実行可能な対象回帰と全体回帰がPASS、未実行を別記録。
- [ ] GLMレビュー、Task・Acceptance・CHANGELOG・Birdseyeの同期を終えてからdoneへ移行。

## Deliverables / Notes
- [AC-20261109-01](../acceptance/AC-20261109-01.md)（現時点draft）
- before/afterの検証証跡（未作成。実測時に追加）。
- P0がdone/approvedとなるまでP1の着手を停止し、検知性能改善を主張しない。
