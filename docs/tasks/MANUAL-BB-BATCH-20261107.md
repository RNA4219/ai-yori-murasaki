---
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-07
next_review_due: 2026-12-07
---

# MANUAL-BB-BATCH-20261107（manual-bb一括洗い出し→一括修正バッチ）

本Taskは調査設計フェーズの記録である。実行・修正は未実施であり、本稿に完了主張はない。

## 1. 方針（ユーザー2026-11-07指示）

1. テストはmanual-bb（manual-bb-test-harness）を使う。無駄なテストを繰り返さない。
2. **洗い出し中は修正しない**。発見を都度修正せず、洗い出し完了後にGLMが重複/根因/優先度を集約し、**一括修正フェーズ**でDeepSeekへ修正指示を出す。
3. 変更した点と必要な最小回帰だけを再確認する。既存の大規模自動試験（pytest全体）を繰り返さない。
4. 実行証跡がない時のno_goは正常。pass自己申告で合格にしない。

## 2. 役割

- **Codex**: 指揮官のみ。manual-bb CLI実行、成果物の受領、判断。
- **GLM**: 要約・整理・観点・レビュー。intake作成、洗い出し結果の集約、修正指示の設計、検証結果レビュー。
- **DeepSeek（Ollama deepseek-v4.1-flash:cloud）**: 一括修正フェーズでの実装のみ。

## 3. フェーズと移行条件

| フェーズ | 内容 | 移行条件 |
| --- | --- | --- |
| P1 調査設計（本稿） | manual-bb-intake.md作成、要件・証跡の整理 | intakeをCodexが受理 |
| P2 manual-bbケース設計 | CodexがCLI `local-design`へintakeを渡す（コマンドはintake固定）。local-designは**ケース設計のみ**で、実製品観測を含まない | schema artifact一式が生成される |
| P3 black-box観測実行 | 設計されたケースを現製品で実際に観測実行する。自動操作を使う場合は「エージェント操作」と正直に記録し、人の操作・利用者試験へ読み替えない。未実施ケースを実施済みとしない | 観測証跡（実測/推測/未実施/道具不具合の分離）が揃う |
| P4 洗い出し集約 | GLMがartifactと観測証跡を読み、重複/根因/優先度を集約 | 集約表がCodexに承認される |
| P5 一括修正 | GLMが修正指示をDeepSeekへ一括で渡す。洗い出し中の都度修正はしない | 修正指示がDeepSeekへ投入完了 |
| P6 最小回帰 | 変更した点と必要な回帰のみ再確認。manual-bbのschema artifactと観測証跡を根拠とする | GLMが実測を確認しCodexへ報告 |

## 4. 固定事項

- source/build版とcase版はP2開始時に固定し記録する。実測・推測・未実施・道具の不具合を分離して記録する。
- 製品ソース・既存tests・パッケージはP4まで凍結。過去ログ・原資料の書換禁止。
- 既知の証跡の位置づけ: 537pass/2skip、その後image pin typo修正で対象55passは現版manual合格ではない。過去UI19checks/KEV-EPSSはsynthetic fixture。CLI/API report-nullはbridge unpack誤りで製品無変更。旧formal評価のholdout来歴（combined validation gate記録前の20261114 holdout再利用の疑い）は未確認のまま観点として残し、root-cause推測で再評価を開始しない。
- 正式5類型評価は未実施のまま。本バッチで新しい数値受入条件・追加機能・人間被験者試験は持ち込まない。

## 5. 成果物

- 要求入力: [.ga/goal-20261106/manual-bb-intake.md](../../.ga/goal-20261106/manual-bb-intake.md)（harnessのgoldens形式: Feature/Acceptance Criteria/Business Rules/Changed Areas/Existing Evidence/Environments見出しに再構成済み）
- ケース設計: [.ga/goal-20261106/manual-bb-discovery-plan.md](../../.ga/goal-20261106/manual-bb-discovery-plan.md) / .json（TC-01〜TC-12。CLI local-designはglm-5.3:cloudでfinish_reason=lengthによりblocked、native作成のためschema validatedではない）
- 観測collector: [.ga/goal-20261106/manual-bb-collect.py](../../.ga/goal-20261106/manual-bb-collect.py)（DeepSeek作成、public CLI/HTTPのみ）
- 観測証跡: .ga/goal-20261106/manual-bb-discovery-evidence/（actor=agent、2026-11-07実行1回）
- 洗い出し集約: [.ga/goal-20261106/manual-bb-findings.md](../../.ga/goal-20261106/manual-bb-findings.md) / .json
- 一括修正指示: 未作成（findings §3の案を次フェーズで確定）

## 6. 現時点の状態

- P1完了、P2（local-design）はblockedでnative planに代替、P3（black-box観測）を1回実施済み。ただし実質有効観測はTC-03/10/12のみで、TC-01/02/04/05/06/07/08/09/11はaym entry point不在（PATH）とHTML excerpt打ち切りにより未観測（道具・環境起因、製品指摘0件）。AC-1〜AC-10の本体判定は未実施のまま。gate=no_go。P4以降（集約承認→一括修正→最小回帰）未実施。完了主張なし。collectorの修理は残件から外した。aym entry point不在とHTML excerpt打ち切りによる未観測は道具・環境起因の失敗として本稿に記録を保持し、製品欠陥や残件には含めない。
