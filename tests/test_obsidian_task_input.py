"""架空の vault を使い、参照用と課題用のノート読み取り契約を確認する。"""
import hashlib

import pytest

from ai_yori_murasaki.jsonio import DataError
from ai_yori_murasaki.obsidian_import import task_rows
from ai_yori_murasaki.sources import MAX_TEXT, ObsidianNote, read_obsidian_note


def note(body, uri="obsidian://open?vault=Demo&file=Tasks"):
    return ObsidianNote("Tasks", uri, body, "")


def test_only_markdown_tasks_are_selected():
    source = """---
display:
  - [ ] property
---
# 作業
   - [ ] node-17 を確認 %%メモ%%^work-17
* [x] 記録を照合
+ [X] 完了
- [ ] `<!--` の表示を確認
> - [ ] quoted
    - [ ] indented code
普通の行の - [x] 語句
<!--
- [ ] comment
-->
<!-- first --> <!-- second
- [x] second comment
-->
<pre class="example">
- [x] HTML code example
</pre>
%%
- [ ] Obsidian comment
%%
````markdown
```
- [x] example
````
~~~markdown
 - [ ] second example
~~~~
"""
    rows = task_rows(note(source), "demo-acct")
    assert list(rows.values()) == [
        ("node-17 を確認", {"title": "node-17 を確認", "status": "open", "key": "work-17", "scope": "demo-acct"}),
        ("記録を照合", {"title": "記録を照合", "status": "done", "scope": "demo-acct"}),
        ("完了", {"title": "完了", "status": "done", "scope": "demo-acct"}),
        ("`<!--` の表示を確認", {"title": "`<!--` の表示を確認", "status": "open", "scope": "demo-acct"}),
    ]


def test_identity_depends_on_note_and_block_or_title():
    original = task_rows(note("- [ ] 対象を確認 ^one\n- [ ] 記録を確認"))
    reordered = task_rows(note("* [X] 記録を確認\n+ [x] 題名の変更 ^one"))
    assert list(original) == list(reversed(reordered))
    assert set(original).isdisjoint(task_rows(note("- [ ] 記録を確認", "obsidian://open?vault=Demo&file=Other")))
    assert len(task_rows(note("- [ ] 記録を確認 ^one\n- [ ] 記録を確認 ^two"))) == 2


@pytest.mark.parametrize("body", [
    "# 普通のノート\n- 箇条書き", "- [ ]", "- [ ] ^only-key", "- [x] " + "a" * 513,
    "- [x] duplicate\n- [ ] duplicate", "- [ ] A ^one\n- [x] B ^one",
])
def test_invalid_or_duplicate_tasks_are_rejected(body):
    with pytest.raises(DataError):
        task_rows(note(body))


def test_bounded_utf8_note_and_raw_provenance(tmp_path):
    vault = tmp_path / "架空 vault"
    folder = vault / "調査"
    folder.mkdir(parents=True)
    path = folder / "週次.md"
    raw = "# 確認\r\n- [x] 見本\r\n".encode("utf-8-sig")
    path.write_bytes(raw)
    selected = read_obsidian_note(vault, "調査/週次.md")
    assert selected.sha256 == hashlib.sha256(raw).hexdigest()
    assert selected.body == "# 確認\n- [x] 見本\n"
    assert selected.title == "週次"
    assert selected.uri.startswith("obsidian://open?vault=%E6%9E%B6%E7%A9%BA%20vault&file=")
    assert "%2F" in selected.uri
    assert path.read_bytes() == raw
    path.write_bytes(b"a" * MAX_TEXT)
    assert len(read_obsidian_note(vault, "調査/週次.md").body) == MAX_TEXT
    for invalid in (b"a" * (MAX_TEXT + 1), b"\xff"):
        path.write_bytes(invalid)
        with pytest.raises(DataError):
            read_obsidian_note(vault, "調査/週次.md")


def test_settings_in_selected_vault_path_are_excluded(tmp_path):
    vault = tmp_path / ".OBSIDIAN" / "cache"
    vault.mkdir(parents=True)
    (vault / "draft.md").write_text("- [x] 完了", encoding="utf-8")
    with pytest.raises(DataError):
        read_obsidian_note(vault, "draft.md")
