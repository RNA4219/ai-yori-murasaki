---
task_id: 20261108-02
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-08
next_review_due: 2026-12-08
---

# Task — README整備とv0.1.0まわりの文書整合

## メタデータ

- priority: P2
- repo / base_branch / work_branch: RNA4219/ai-yori-murasaki / main / main（文書のみの小規模変更のため作業ブランチは切らない）

## Objective

公開リポジトリの外面を仕上げる。READMEから最短で起動へ進めるようにし、モニターの内容を文章で伝える。CHANGELOGの蓄積分を版として確定し、GitHub Release v0.1.0まわりを整える。あわせて公開記録（RA）と検収（AC）を残す。

依頼根拠：2026-11-08、ユーザー（RNA4219）から「代わりにその辺り仕上げてもらえたら嬉しいですね」と、READMEまわり（クイックスタート、モニター紹介）とリリース整備の実施指示があった。

## Scope

- In: README.md（クイックスタート節、モニター節、公開状態の追記）、CHANGELOG.md（[0.1.0] 節の新設と通番0013）、docs/releases/RA-20261108-01.md の新規作成と docs/releases/README.md の同期、docs/tasks/README.md と docs/acceptance/INDEX.md の索引同期、GitHub のタグ v0.1.0 と Release の確認とノート整備（実施時に既存と判明したため新規作成はしていない。Notes 参照）。
- Out: 製品コード・テストの変更、モニターのスクリーンショット（実データの state.json がリポジトリに無く、空画面や擬似データの画像は証跡として不適切なため付けない）、CI の追加、Birdseye カプセルの再生成（生成基盤が本環境に無いため未実施として記録する）。

## Requirements

- Behavior: README の「起動」詳細は保持したまま、その前に最短手順のクイックスタートを置く。モニター節は画面で見える内容（主要指標、agent活動と帰属、検知・修復・再検証の結果、RSI候補、証跡）を、既存の記録（CHANGELOG 0006、AC-20261108-01）と矛盾しない範囲でのみ記述する。
- I/O Contract: 版は pyproject.toml の version = "0.1.0" と RA-20261107-01 の release_version: v0.1.0 に合わせる。タグ名は `v0.1.0`。
- Constraints: 製品の事実（テスト件数、対応範囲、限界）を変更しない。CIバッジは付けない（README「公開状態」の方針どおり）。秘密情報を含めない。
- Acceptance Criteria:
  - [ ] README にクイックスタート節とモニター節があり、相対リンクが実在する。
  - [ ] CHANGELOG に [0.1.0] - 2026-11-08 の節があり、従来の [Unreleased] 記載（0001–0012）が過不足なく移っている。
  - [ ] GitHub にタグ v0.1.0 と Release v0.1.0 が存在する。
  - [ ] 公開記録 RA-20261108-01 があり、docs/releases/README.md の要約と一致する。
  - [ ] 変更ファイルに対して秘密検査（tools/ci/check_release_secrets.py の対象範囲）と相対リンク検査を実施し、結果を AC-20261108-02 に記録する。

### Evaluation Identity

比較評価・実験・昇格は含まない。文書と公開手続きのみのため適用外。

### RSI

自己改善は含まない。適用外。

## Affected Paths

- README.md
- CHANGELOG.md
- docs/releases/RA-20261108-01.md（新規）
- docs/releases/README.md
- docs/tasks/TASK.readme-release-11-08-2026.md（本書）
- docs/tasks/README.md
- docs/acceptance/AC-20261108-02.md（新規）
- docs/acceptance/INDEX.md

## Local Commands

- `python3 tools/ci/check_release_secrets.py --help` で用法を確認してから対象へ実行する。
- 相対リンク検査は簡易スクリプトで実施する（AC に方法と結果を記録）。
- `gh release view v0.1.0` で Release の存在を確認する。

## Deliverables

本書、AC-20261108-02、CHANGELOG 通番0013、RA-20261108-01、既存 GitHub Release v0.1.0 のノート整備。

## Plan

現状確認 → 範囲内の変更 → 適切な検証 → 記録・索引の同期 → commit・push → タグと Release の作成 → RA の実行状態を更新。

## Patch

実施後に AC-20261108-02 と CHANGELOG 0013 へ記録する。

## Tests

製品テストは変更対象外のため未実行（コードを変更していない）。文書の検証はリンク検査と秘密検査を実施し、AC に記録する。

## Commands

実行結果の正本は AC-20261108-02。

## Notes

- Birdseye カプセル（docs/birdseye/caps/）の再生成は本環境に生成基盤がなく未実施。次回、通常の Codex 環境で作業する際に同期が必要。
- スクリーンショットは、実ラボ実行時の state.json を使ってモニターを表示した状態で撮るのが適切。実データが用意できた時点で別 Task とする。
- 追補（2026-11-08）: ユーザーの確認を受け、スクリーンショットと Birdseye 再生成を本 Task の追補として実施した。モニターは state.json ではなく API 応答で描画されるため、証跡として保存された実ランの API 応答を再生して撮影できた。Birdseye は導入元（workflow-cookbook）の Codemap をこの環境へ clone して再生成した。詳細は AC-20261108-02 の追補と CHANGELOG 0014 に記録した。
- 実施時の訂正（2026-11-08）: タグ v0.1.0 と GitHub Release は初回公開時（2026-11-07、RA-20261107-01 の実行、タグ対象 commit 8f38777）に作成済みだった。作業開始前の確認が不足していた。新規作成は行わず、Release ノート本文の整備と文書整合のみを実施した。経緯は RA-20261108-01 に記録した。
