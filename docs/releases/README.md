---
intent_id: INT-AYM-WORKFLOW-20261106
owner: RNA4219
status: active
last_reviewed_at: 2026-11-06
next_review_due: 2026-12-06
---

# 公開記録

現在の公開版は v0.1.0（[GitHub Release](https://github.com/RNA4219/ai-yori-murasaki/releases/tag/v0.1.0)、RA-20261108-01）。文書導入の技術検収をリリースとして数えない。
今後、ユーザーから公開を依頼された場合は版、対象 Task / Acceptance、成果物 hash、検証結果、承認者、復旧方法を公開記録へ残す。
公開判断が必要なときは `RA-YYYYMMDD-xx` を採番し、Acceptance の `release_approval_id` と相互参照する。承認記録は [RA-20261107-01](RA-20261107-01.md) を参照。
比較評価を含む場合は [評価 identity](../CONTRACTS.md#evaluation-identity) の postrun を先に通す。
共通雛形ファイルは未整備であるため、当面は本READMEの各RA節の書式（承認者・対象・対象Task/Acceptance・除外・成果物・検証根拠・approval状態・公開実行状態・CI・復旧方法）を雛形として使う。

## RA-20261107-01 — GitHub Public公開の承認

正式な承認記録は [RA-20261107-01.md](RA-20261107-01.md) を参照。以下は要約である。

- 承認者: RNA4219（ユーザー、2026-11-07「ではやってください」＝公開承認の明示指示）
- 対象: GitHub Publicリポジトリ `RNA4219/ai-yori-murasaki`（未作成、gh API 404確認済み）と、バージョン0.1.0の配布範囲（自作MITコード・文書、第三者表示はTHIRD_PARTY_NOTICES.md）
- 対象Task / Acceptance: [AC-20261107-03](../acceptance/AC-20261107-03.md)（技術検収合格、release_approval_id相互リンク）
- 除外: 再配布権未確認の受領レポート・外部レビュー原文、実行時状態ファイル（[公開対象選定](../../.ga/publication-20261107/public-selection.md)※非公開）
- approval状態: approved（承認済み）
- 公開実行: pending（Codexがgit add→write-tree→archiveの秘密検査・ファイル照合を経てrepo作成・pushを実行）
- CI: なし（`.github/workflows` 不存在、新規CI基盤は追加しない）
- 復旧方法: 公開treeはステージ済みtreeで固定し、不適切ファイル検出時は該当ファイルをgitignoreへ追加して再ステージする
- 成果物: バージョン0.1.0のsdist/wheel（`.ga/publication-20261107/dist/`、非公開の作業領域）。README.md確定後にビルドした。sha256: sdist `3b5f5c10105bfa730857aa6b97b62f6270f93b35998c7946f5f2cd37e4fb96cb`（267613 bytes）、wheel `c219919db9adcee3b2c6bcc00c3417fa1ac3c089d8edf8ae002aec2f7a7b6c4b`（234445 bytes）。配布一致比較（package 112ファイル、sdistルート文書、PKG-INFO/METADATA Description、LICENSE/THIRD_PARTY_NOTICES.md）は不一致0件・欠落0件、readme_in_sdist=true。秘密検査はsdist 110ファイル・wheel 76ファイルとも0件（`.ga/publication-20261107/public-dist-compare-result.json`、`public-secret-scan-sdist.json`、`public-secret-scan-wheel.json`、非公開）
- 検証根拠: [AC-20261107-03](../acceptance/AC-20261107-03.md)の技術検収（合格済み、再実行しない）と、公開treeに対する `tools/ci/check_release_secrets.py` による秘密検査（公開実行時に実施）

## RA-20261108-01 — GitHub Release v0.1.0 の作成

正式な承認記録は [RA-20261108-01.md](RA-20261108-01.md) を参照。以下は要約である。

- 承認者: RNA4219（ユーザー、2026-11-08「代わりにその辺り仕上げてもらえたら嬉しいですね」＝ README整備とリリース作成の実施指示）
- 対象: GitHub Release `v0.1.0`（タグ `v0.1.0`）と、その前提となる README・CHANGELOG の整備。初回公開そのものの承認は RA-20261107-01
- 対象Task / Acceptance: [AC-20261108-02](../acceptance/AC-20261108-02.md)（[Task 20261108-02](../tasks/TASK.readme-release-11-08-2026.md)）
- 除外: 配布物（sdist/wheel）の再ビルド、Birdseye カプセルの再生成、モニターのスクリーンショット
- approval状態: approved（承認済み）
- 公開実行: pending（タグと Release の作成後に RA-20261108-01 へ結果を追記する）
- CI: なし（新規CI基盤は追加しない）
- 復旧方法: `gh release delete v0.1.0` と `git push origin :refs/tags/v0.1.0` で Release とタグを取り消す
- 検証根拠: AC-20261108-02（相対リンク検査 0 件、変更差分の秘密情報パターン検査 0 件）
