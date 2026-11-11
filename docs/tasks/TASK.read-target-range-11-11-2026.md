---
task_id: 20261111-03
intent_id: INT-AYM-PRODUCT-20261106
owner: RNA4219
status: reviewing
last_reviewed_at: 2026-11-11
next_review_due: 2026-12-11
acceptance_id: AC-20261111-03
---

# 対象ファイル読取の範囲切り詰めと継続位置

## Objective

`read_target_file`で短いファイルに広いend_lineを指定すると`range_too_large`になる問題を解消し、1回の読取で収まらない場合は行単位で安全に継続できるようにする。

## Scope

- In: `ai_yori_murasaki/agents/tools.py`の行範囲処理・返却フィールド、`tests/test_agent_core.py`の回帰試験。
- Out: 実ログ入口、RSI課題追加、oracle判定名、LLMのモデル固定検証。これらは別Taskとし、今回の修正による性能改善を主張しない。

## Requirements

- 既存のpath/grant検証と型・行番号チェックを保持する。
- 指定されたend_lineが実ファイル長や200行上限を超えても、読める範囲を返す。
- 32KiBの出力上限は維持し、行境界で区切り、未読がある場合は`next_start_line`を返す。
- 1行が32KiBを超え、行単位の続きを表現できない場合のみ既存エラーを維持する。
- 空ファイルの読取は空の成功結果として扱う。

## Tests

- 対象: `python3 -m pytest tests/test_agent_core.py -q`
- 全体: `python3 -m pytest tests -q`
- GitHub連携による変更環境にはリポジトリのPython実行基盤がないため、上記は未実行。実行と検収は[AC-20261111-03](../acceptance/AC-20261111-03.md)で追跡。

## Deliverables

- [AC-20261111-03](../acceptance/AC-20261111-03.md)
- [CHANGELOG](../../CHANGELOG.md) 0021
