"""判断画面と照合で使う、取り込み元別の列と状態の契約。"""


def tracker_source(finding):
    extensions = finding.get("extensions") or {}
    obsidian = extensions.get("obsidian")
    if (finding.get("source") == "obsidian" and isinstance(obsidian, dict)
            and obsidian.get("source_kind") == "obsidian_md/1"):
        return "obsidian", obsidian
    notion = extensions.get("notion")
    if isinstance(notion, dict):
        return "notion", notion
    return None, {}


def tracker_mapping(source, mapping):
    if source == "obsidian":
        return {**mapping, "fields": {"status": "status", "key": "key", "scope": "scope"},
                "status_values": {"open": "open", "done": "done"}}
    return mapping
