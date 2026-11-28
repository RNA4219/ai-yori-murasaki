"""選んだ Obsidian ノートの Markdown チェックリストを案件の課題として取り込む。"""
import re

from .jsonio import DataError, digest
from .sources import read_obsidian_note


_TASK = re.compile(r" {0,3}[-+*][ \t]+\[([ xX])\](?:[ \t]+(.*))?")
_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})(.*)")
_BLOCK_ID = re.compile(r"(?:^|[ \t]+)\^([A-Za-z0-9-]+)$")
_TEXT_MARKER = re.compile(r"<!--|%%|`+")
_RAW_HTML = re.compile(r" {0,3}<(pre|script|style|textarea)(?=[ \t>]|$)", re.IGNORECASE)


def _without_comments(line, pending):
    if not pending and "<!--" not in line and "%%" not in line:
        return line, None
    # インラインコード内のコメント記号は題名の文字。等長の閉じ記号を一度だけ探す。
    next_tick = {}
    last = {}
    for tick in reversed(list(re.finditer(r"`+", line))):
        width = tick.end() - tick.start()
        next_tick[tick.start()] = last.get(width, tick.end())
        last[width] = tick.end()
    visible = []
    pos = 0
    while True:
        if pending:
            end = line.find(pending, pos)
            if end == -1:
                return "".join(visible), pending
            pos = end + len(pending)
            pending = None
        opening = _TEXT_MARKER.search(line, pos)
        if not opening:
            return "".join(visible) + line[pos:], None
        if opening[0].startswith("`"):
            end = next_tick[opening.start()]
            visible.append(line[pos:end])
            pos = end
            continue
        visible.append(line[pos:opening.start()])
        pending = "-->" if opening[0] == "<!--" else "%%"
        pos = opening.end()


def _task_lines(body):
    """本文のタスクを返し、プロパティ・コメント・コード例は読み飛ばす。"""
    fence = None
    raw_end = None
    comment_end = None
    properties = False
    for index, line in enumerate(body.split("\n")):
        if index == 0 and line.rstrip() == "---":
            properties = True
            continue
        if properties:
            if line.rstrip() in ("---", "..."):
                properties = False
            continue
        if fence:
            marker = _FENCE.fullmatch(line)
            if (marker and marker[1][0] == fence[0] and len(marker[1]) >= len(fence)
                    and not marker[2].strip()):
                fence = None
            continue
        if raw_end:
            if raw_end in line.lower():
                raw_end = None
            continue
        if comment_end:
            _, comment_end = _without_comments(line, comment_end)
            continue
        marker = _FENCE.fullmatch(line)
        if marker and (marker[1][0] == "~" or "`" not in marker[2]):
            fence = marker[1]
            continue
        if line.startswith(("    ", "\t")) or line.lstrip(" ").startswith(">"):
            continue
        raw = _RAW_HTML.match(line)
        if raw:
            end = "</" + raw[1].lower() + ">"
            if end not in line.lower():
                raw_end = end
            continue
        visible, comment_end = _without_comments(line, None)
        match = _TASK.fullmatch(visible) if _TASK.fullmatch(line) else None
        if match:
            yield (match[2] or "").strip(), "open" if match[1] == " " else "done"


def task_rows(note, scope=None):
    if scope is not None and (not scope.strip() or scope != scope.strip() or len(scope) > 256
                              or any(ord(c) < 32 or ord(c) == 127 for c in scope)):
        raise DataError("invalid Obsidian observation scope")
    rows = {}
    for text, status in _task_lines(note.body):
        block = _BLOCK_ID.search(text)
        title = text[:block.start()].rstrip() if block else text
        if not title or len(title) > 512 or "\x00" in title:
            raise DataError("missing or invalid Obsidian task title")
        if len(rows) >= 10000:
            raise DataError("Obsidian note exceeds 10000 tasks")
        if block:
            identity = [note.uri, "block", block[1]]
        else:
            identity = [note.uri, "title", title]
        fid = "obsidian-" + digest(identity)[:48]
        if fid in rows:
            raise DataError("duplicate Obsidian task identity")
        row = {"title": title, "status": status}
        if block:
            row["key"] = block[1]
        if scope is not None:
            row["scope"] = scope
        rows[fid] = (title, row)
    if not rows:
        raise DataError("no Markdown tasks in selected note")
    return rows


def import_obsidian(runtime, campaign_id, run_id, vault, note, context_path, scope=None):
    from .notion_import import import_issue_rows

    selected = read_obsidian_note(vault, note)
    return import_issue_rows(runtime, campaign_id, run_id, context_path, provider="obsidian",
                             source_hash=selected.sha256, rows=task_rows(selected, scope),
                             source_uri=selected.uri)
