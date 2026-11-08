---
task_id: 20261108-03
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: done
last_reviewed_at: 2026-11-08
next_review_due: 2026-12-08
---

# Task — v0.1.1 リリース（版上げと配布物の再ビルド）

## メタデータ

- priority: P2
- repo / base_branch / work_branch: RNA4219/ai-yori-murasaki / main / main

## Objective

v0.1.0 の配布物（2026-11-07 ビルド）から製品が大きく変わったため、版を 0.1.1 へ上げ、配布物（sdist/wheel）を再ビルドして GitHub Release v0.1.1 として公開する。公開記録（RA-20261108-02）と検収（AC-20261108-03）を残す。

依頼根拠：2026-11-08、ユーザー（RNA4219）から「リリースパッケージ結構変えたから v0.1.1にして」と実施指示があった。

## Scope

- In: pyproject.toml と ai_yori_murasaki/__init__.py の版を 0.1.1 へ更新、sdist/wheel のビルドと秘密検査、CHANGELOG の [0.1.1] 節と通番0016、README「公開状態」の更新、docs/releases/RA-20261108-02 と docs/releases/README.md の同期、docs/tasks/README.md と docs/acceptance/INDEX.md の索引同期、タグ v0.1.1 と GitHub Release（配布物を添付）の作成、Birdseye 再生成。
- Out: 製品コードの変更（版文字列を除く）、CI の追加、v0.1.0 の Release やタグの変更。

## Requirements

- Behavior: 配布物は版確定後の tree からビルドし、版（0.1.1）が METADATA / PKG-INFO に入ることを確認する。秘密検査（tools/ci/check_release_secrets.py）を両アーカイブへ実施する。
- Constraints: v0.1.0 の記録（RA-20261107-01、CHANGELOG [0.1.0] 節）を書き換えない。秘密情報を含めない。
- Acceptance Criteria:
  - [ ] pyproject.toml と __init__.py の版が 0.1.1 で一致する。
  - [ ] sdist/wheel がビルドでき、秘密検査で該当 0 件。
  - [ ] GitHub にタグ v0.1.1 と Release v0.1.1（配布物2点の添付）がある。
  - [ ] RA-20261108-02 に成果物 hash と検証結果が記録される。
  - [ ] テストの実施結果（この環境で実行可能な範囲）を AC-20261108-03 に正直に記録する。

### Evaluation Identity

比較評価・実験・昇格は含まない。適用外。

### RSI

自己改善は含まない。適用外。

## Affected Paths

- pyproject.toml
- ai_yori_murasaki/__init__.py
- README.md
- CHANGELOG.md
- docs/releases/RA-20261108-02.md（新規）
- docs/releases/README.md
- docs/tasks/TASK.version-011-11-08-2026.md（本書）
- docs/tasks/README.md
- docs/acceptance/AC-20261108-03.md（新規）
- docs/acceptance/INDEX.md
- docs/birdseye/（再生成）

## Deliverables

本書、AC-20261108-03、CHANGELOG 通番0016、RA-20261108-02、GitHub Release v0.1.1（sdist/wheel 添付）。

## Notes

- この作業環境には Docker がないため、ラボ実行に依存するテストは本環境では検証できない。テスト結果の扱いは AC-20261108-03 に明記する。
