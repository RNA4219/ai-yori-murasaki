"""取り込み済みの課題と観測から、読み取り専用の照合結果を作る。"""

from collections import Counter, defaultdict
from datetime import datetime, timezone
import unicodedata


CLASSIFICATIONS = ("open_absent", "open_resolved", "closed_active", "aligned", "uncomparable")
REASONS = ("out_of_scope", "no_resource", "inferred_only", "no_detections")
TEXT_FIELDS = {
    "summary": "概要", "impact": "影響", "remediation": "是正の案",
    "pending": "確認待ち", "actual_result": "実際の結果",
}


def _time(value):
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value)
            if parsed.utcoffset() is not None:
                return parsed
        except (ValueError, OverflowError):
            pass
    return None


def _text(value):
    if isinstance(value, list):
        return "\n".join(v for v in value if isinstance(v, str))
    return value if isinstance(value, str) else ""


def _continues_identifier(neighbor, edge):
    if neighbor in ("_", "-"):
        return True
    if not neighbor.isalnum():
        return False
    if edge.isascii() and not neighbor.isascii():
        # 和文の中の ASCII 識別子は、助詞や漢字との間に空白が入らないことがある。
        return not unicodedata.name(neighbor, "").startswith((
            "HIRAGANA", "KATAKANA", "HALFWIDTH KATAKANA", "CJK UNIFIED IDEOGRAPH",
            "CJK COMPATIBILITY IDEOGRAPH", "IDEOGRAPHIC ITERATION MARK",
        ))
    return True


def _word(text, term):
    """識別子全体の一致を、日本語の文中でも判定する。"""
    if not term:
        return False
    offset = 0
    while (pos := text.find(term, offset)) >= 0:
        end = pos + len(term)
        before = text[pos - 1] if pos else ""
        after = text[end:end + 1]
        if not (_continues_identifier(before, term[0]) or _continues_identifier(after, term[-1])):
            return True
        offset = pos + 1
    return False


def _catalog(observations):
    """同じ provider・account・地域集合だけを同一の観測範囲として扱う。"""
    scopes = defaultdict(list)
    times = [_time(obs["collected_at"]) for obs in observations]
    for index, obs in enumerate(observations):
        scope = obs["scope"]
        key = (scope["provider"], scope["account"], tuple(sorted(set(scope["regions"]))))
        scopes[key].append(index)

    catalog = []
    for (_, account, _), indices in scopes.items():
        # 同時刻のスナップショットは全て残し、取り込み順に依存しない。
        indices = sorted(indices, key=times.__getitem__)
        current = defaultdict(list)
        last_seen = {}
        descriptions = defaultdict(list)
        for i in indices:
            obs = observations[i]
            kind = obs["kind"]
            if current[kind] and times[current[kind][-1]] != times[i]:
                current[kind].clear()
            current[kind].append(i)
            if kind == "inventory":
                for resource in obs["resources"]:
                    rid = resource["id"]
                    last_seen[rid] = i
                    versions = descriptions[rid]
                    if versions and times[versions[-1][2]] != times[i]:
                        versions.clear()
                    versions.append((resource["name"], resource["type"], i))
            else:
                for detection in obs["detections"]:
                    last_seen[detection["resource_id"]] = i

        inventories = [{row["id"] for row in observations[i]["resources"]}
                       for i in current["inventory"]]
        detections = defaultdict(list)
        for i in current["detections"]:
            for row in observations[i]["detections"]:
                projected = {key: row[key] for key in ("id", "rule", "status", "updated_at")}
                detections[row["resource_id"]].append({**projected, "observation_index": i})
        current_indices = sorted(current["inventory"] + current["detections"])
        resources = []
        for rid in sorted(last_seen):
            versions = descriptions.get(rid, [])
            aliases = sorted({name for name, _, _ in versions if name})
            types = {kind for _, kind, _ in versions}
            presence = {rid in inventory for inventory in inventories}
            in_inventory = presence.pop() if len(presence) == 1 else None
            evidence = detections.get(rid, [])
            refs = set(current_indices)
            # 不在を判定する際、照合した ID や名前をどの過去資料から得たかも示す。
            refs.update(i for _, _, i in versions)
            if not versions and not evidence:
                refs.add(last_seen[rid])
            resources.append((aliases, {
                "id": rid, "name": aliases[0] if len(aliases) == 1 else None,
                "type": types.pop() if len(types) == 1 else None, "in_inventory": in_inventory,
                "detections": evidence, "observation_indices": sorted(refs),
            }))
        catalog.append((account, current_indices, resources))
    return catalog


def _classify(closed, matches):
    if not matches:
        return "uncomparable", "no_resource"
    confirmed = [m for m in matches if m["method"] != "inferred"]
    if not confirmed:
        return "uncomparable", "inferred_only"
    absent = all(m["in_inventory"] is False for m in confirmed)
    if absent and not closed:
        return "open_absent", None
    if any(d["status"] == "active" for m in confirmed for d in m["detections"]):
        return ("closed_active" if closed else "aligned"), None
    if absent:
        return "aligned", None
    # 空の検知一覧や、別の資源だけにある resolved から修正済みとは推論しない。
    if all(m["in_inventory"] is False or m["detections"] for m in confirmed):
        return ("aligned" if closed else "open_resolved"), None
    return "uncomparable", "no_detections"


def _compare(finding, props, notion, mapping, catalog, observations):
    fields = mapping["fields"]
    source_status = props.get(fields.get("status", ""))
    if not isinstance(source_status, str):
        source_status = None
    closed = mapping["status_values"].get(source_status) in ("done", "wont_fix")
    title = _text(finding.get("title"))
    issue_text = "\n".join([title] + [
        _text(props.get(fields.get(key, default))) for key, default in TEXT_FIELDS.items()
    ])
    scope_text = _text(props.get(fields.get("scope", "原典の環境")))
    scoped = [group for group in catalog if _word(scope_text, group[0])]
    resources = [r for _, _, group in scoped for r in group]
    names = Counter(name for aliases, _ in resources for name in aliases)
    matches = []
    refs = set()
    for aliases, resource in resources:
        name = resource["name"]
        if _word(issue_text, resource["id"]):
            method = "id"
        elif name and len(name) >= 4 and names[name] == 1 and _word(issue_text, name):
            method = "name"
        elif any(alias in issue_text for alias in aliases):
            method = "inferred"
        else:
            continue
        matches.append({**resource, "method": method})
        refs.update(resource["observation_indices"])
    if not matches:
        refs.update(i for _, current, _ in scoped for i in current)
    classification, reason = (_classify(closed, matches) if scoped
                              else ("uncomparable", "out_of_scope"))
    collected = sorted({observations[i]["collected_at"] for i in refs}, key=_time)
    times = (props.get(fields.get("updated_at", "最終更新日時")),
             props.get("last_edited_time"), notion.get("last_edited_time"),
             props.get("updated_at"), notion.get("updated_at"))
    edited = next((t for value in times if (t := _time(value)) is not None), None)
    return {
        "finding_id": finding["finding_id"], "title": title, "source_status": source_status,
        "closed": closed, "classification": classification, "reason": reason,
        "stale": edited is not None and any(_time(t) < edited for t in collected),
        "matches": matches, "collected_at": collected,
    }


def build_drift(campaign_id, findings, records, mapping, observations):
    """検証済みの観測と、判断画面と同じ形式の課題・原典の行だけを使う。"""
    catalog = _catalog(observations)
    items = []
    seen = set()
    for finding in findings:
        fid = finding.get("finding_id")
        notion = (finding.get("extensions") or {}).get("notion")
        if not fid or fid in seen or (not isinstance(notion, dict) and fid not in records):
            continue
        seen.add(fid)
        notion = notion or {}
        props = records.get(fid, notion.get("properties", {}))
        items.append(_compare(finding, props if isinstance(props, dict) else {}, notion,
                              mapping, catalog, observations))
    counts = dict.fromkeys(CLASSIFICATIONS, 0)
    reasons = dict.fromkeys(REASONS, 0)
    for item in items:
        counts[item["classification"]] += 1
        if item["reason"]:
            reasons[item["reason"]] += 1
    metadata = []
    for obs in observations:
        scope = {k: obs["scope"][k] for k in ("provider", "account", "regions")}
        metadata.append({k: obs[k] for k in ("kind", "collected_at", "method")} | {"scope": scope})
    return {
        "schema_version": "1.0.0", "campaign_id": campaign_id,
        "generated_at": datetime.now(timezone.utc).isoformat(), "observations": metadata,
        "counts": counts, "reasons": reasons, "items": items,
    }
