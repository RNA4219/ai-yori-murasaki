---
task_id: 20261111-02
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewing
last_reviewed_at: 2026-11-11
next_review_due: 2026-12-11
acceptance_id: AC-20261111-02
---

# 運用障害3件: Blue実ログ入口・Oracle CLI・macOS試験

## Objective

実ログの検知作業で空のRed Runを偽造せず、CLIからBlue評価対象を指定してPurpleへ進め、macOS標準TMPDIRでもpytestの保存先が正常に作られるようにする。

## Authorization

2026-11-11に受領した `aym-ops-report-20261111.zip` とユーザーの「これ直してマージまで」の依頼。報告ZIPはリポジトリに追加しない。

## Scope

- `blue_blind`へ`imported_source_ref`を明示できる入口とCLIの`--imported-logs-file`/`--imported-logs-ref`を追加。
- 入力CAS参照をRun manifest／extensions／署名対象の監査イベントに記録し、idempotencyとクラッシュ再開で保持。
- 通常のBlueはRed完了・封印必須、importedモードはBlueのみ、入力CASの存在・非空JSON検証を維持。
- `aym oracle evaluate --evaluated-run-id`を追加。API側の同一Campaign・Blue完了・封印ガードを再利用。
- pytest一時ルートを実パスへ正規化し、macOS `/var -> /private/var`下での偽失敗を防ぐ。製品Storeのシンボリックリンク拒否は緩めない。

## Acceptance Criteria

- Redなしで明示的なCAS入力のBlueが作成でき、台帳から入力元を追える。
- 入力なしのBlue前提条件、Oracleの異Campaign／未封印拒否、Run状態遷移を回帰させない。
- `--evaluated-run-id`指定のPASSをBlueに帰属させ、検証Run封印後`purple_open`へ遷移できる。
- macOS既定TMPDIRで全件pytestが成功することを現地で確認する。

## Verification

検証コード: [tests/test_ops_imported_blue.py](../../tests/test_ops_imported_blue.py)、[tests/conftest.py](../../tests/conftest.py)。

予定: `python3 -m pytest tests/test_ops_imported_blue.py tests/test_v1_runtime.py tests/test_v1_cli.py tests/test_v1_storage.py -q`、`python3 -m pytest tests -q`。GitHub連携環境ではPythonテスト用チェックアウトが取れず、実行未確認。技術検収は [AC-20261111-02](../acceptance/AC-20261111-02.md) にて保留。

## Boundaries

入力固定とRun作成は自動ログ検知の実行を意味しない。実ログの機密・個人情報を公開しない。Red/Blue/Oracleの権限境界と承認スコープは維持する。
