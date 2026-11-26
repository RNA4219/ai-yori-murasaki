"""取り込み済みの課題と観測から、読み取り専用の照合結果を作る。"""

from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
import re
import unicodedata

from .drift_policy import applicable_rules, excluded_resource
from .observations import id_pattern


CLASSIFICATIONS = ("open_absent", "open_resolved", "closed_active", "aligned", "uncomparable")
REASONS = ("out_of_scope", "no_resource", "inferred_only", "no_detections")
TEXT_FIELDS = {
    "summary": "概要", "impact": "影響", "remediation": "是正の案",
    "pending": "確認待ち", "actual_result": "実際の結果",
}
_QUALIFIERS = frozenset(":/@\\%?#=&+~")
_IDENTIFIER_TOKEN = re.compile(r"[A-Za-z0-9_.:/@\\%?#=&+~-]+")
_FLAT_ID = re.compile(r"[A-Za-z0-9_.-]{1,256}")


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


def _word(text, term, *, resource_id=False):
    """識別子全体の一致を、日本語の文中でも判定する。"""
    if not term:
        return False
    offset = 0
    while (pos := text.find(term, offset)) >= 0:
        end = pos + len(term)
        before = text[pos - 1] if pos else ""
        after = text[end:end + 1]
        qualified = resource_id and (
            before in _QUALIFIERS or after in _QUALIFIERS
            or (before == "." and pos > 1 and text[pos - 2].isascii() and text[pos - 2].isalnum())
            or (after == "." and text[end + 1:end + 2].isascii() and text[end + 1:end + 2].isalnum())
        )
        if not (qualified or _continues_identifier(before, term[0]) or _continues_identifier(after, term[-1])):
            return True
        offset = pos + 1
    return False


@dataclass
class _Scope:
    provider: str
    account: str
    regions: tuple
    current: list
    inventories: list
    resources: list
    typed: bool


def _scope_catalog(key, indices, observations, times):
    current = defaultdict(list)
    descriptions = defaultdict(list)
    last_seen = {}
    types_by_id = defaultdict(set)
    latest_inventory_type = {}
    typed = False
    for i in indices:
        obs = observations[i]
        kind = obs["kind"]
        if current[kind] and times[current[kind][-1]] != times[i]:
            current[kind].clear()
        current[kind].append(i)
        if kind == "inventory":
            typed |= "types" in obs or "id_patterns" in obs
            for row in obs["resources"]:
                identity = row["id"], row["type"]
                previous = latest_inventory_type.get(row["id"])
                if previous and previous[0] == times[i] and previous[1] != row["type"]:
                    typed = True
                latest_inventory_type[row["id"]] = times[i], row["type"]
                types_by_id[row["id"]].add(row["type"])
                last_seen[identity] = i
                versions = descriptions[identity]
                if versions and times[versions[-1][1]] != times[i]:
                    versions.clear()
                versions.append((row["name"], i))
        else:
            for row in obs["detections"]:
                kind_hint = row.get("resource_type")
                last_seen[row["resource_id"], kind_hint] = i
                if kind_hint:
                    typed = True
                    types_by_id[row["resource_id"]].add(kind_hint)

    if not typed:
        # 種類が同時に複数なければ、旧形式では最新の資源名・種類が以前の説明を置き換える。
        for rid, (_, kind) in latest_inventory_type.items():
            types_by_id[rid] = {kind}
        last_seen = {(rid, hint): i for (rid, hint), i in last_seen.items()
                     if hint is None or hint in types_by_id[rid]}

    # 旧形式の検知は、観測済みの種類が一意ならその種類にだけ結べる。
    def resource_type(rid, hint):
        types = types_by_id[rid]
        return next(iter(types)) if hint is None and len(types) == 1 else hint

    detections = defaultdict(list)
    for i in current["detections"]:
        for row in observations[i]["detections"]:
            rid = row["resource_id"]
            projected = {k: row[k] for k in ("id", "rule", "status", "updated_at", "resource_type") if k in row}
            detections[rid, resource_type(rid, row.get("resource_type"))].append(
                {**projected, "observation_index": i})

    current_indices = sorted(current["inventory"] + current["detections"])
    inventories = []
    for i in current["inventory"]:
        obs = observations[i]
        inventory = {(r["id"], r["type"]) for r in obs["resources"]}
        coverage = set(obs["types"]) if "types" in obs else None
        inventories.append((inventory, coverage))
    identities = {}
    for (rid, hint), i in last_seen.items():
        kind = resource_type(rid, hint)
        if kind is None and len(types_by_id[rid]) > 1:
            continue
        identity = rid, kind
        if identity not in identities or times[i] > times[identities[identity]]:
            identities[identity] = i
    resources = []
    for rid, kind in sorted(identities, key=lambda key: (key[0], key[1] or "")):
        identity = rid, kind
        versions = descriptions[identity]
        aliases = sorted({name for name, _ in versions if name})
        presence = {identity in inventory for inventory, coverage in inventories
                    if coverage is None or kind in coverage or identity in inventory}
        evidence = detections[identity]
        ambiguous = detections.get((rid, None), []) if len(types_by_id[rid]) > 1 else []
        refs = set(current_indices)
        refs.update(i for _, i in versions)
        if not versions and not evidence:
            refs.add(identities[identity])
        resources.append((aliases, ambiguous, {
            "id": rid, "name": aliases[0] if len(aliases) == 1 else None,
            "type": kind, "in_inventory": presence.pop() if len(presence) == 1 else None,
            "detections": evidence, "observation_indices": sorted(refs),
        }))
    return _Scope(*key, current_indices, current["inventory"], resources, typed)


def _catalog(observations):
    """同じ provider・account・地域集合だけを同一の観測範囲として扱う。"""
    scopes = defaultdict(list)
    times = [_time(obs["collected_at"]) for obs in observations]
    for index, obs in enumerate(observations):
        scope = obs["scope"]
        key = (scope["provider"], scope["account"], tuple(sorted(set(scope["regions"]))))
        scopes[key].append(index)

    return [_scope_catalog(key, sorted(indices, key=times.__getitem__), observations, times)
            for key, indices in scopes.items()]


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


def _flat_ids(text):
    # 修飾付きの識別子や URL の内部から短い ID を切り出さない。
    tokens = set()
    for match in _IDENTIFIER_TOKEN.finditer(text):
        token = match.group().rstrip(".")
        if not _FLAT_ID.fullmatch(token):
            continue
        before = text[match.start() - 1] if match.start() else ""
        end = match.start() + len(token)
        if not (_continues_identifier(before, token[0]) or _continues_identifier(text[end:end + 1], token[-1])):
            tokens.add(token)
    return tokens


def _synthetic_resources(tokens, group, observations, known_terms):
    docs = [observations[i] for i in group.inventories]
    if not any(doc.get("id_patterns") for doc in docs):
        return
    local_terms = {term for aliases, _, row in group.resources for term in (row["id"], *aliases)}
    for rid in sorted(tokens - local_terms):
        kinds = {kind for doc in docs for kind, patterns in doc.get("id_patterns", {}).items()
                 if any(id_pattern(pattern).fullmatch(rid) for pattern in patterns)}
        if not kinds:
            continue
        kind = next(iter(kinds)) if len(kinds) == 1 else None
        covered = [doc for doc in docs if kind in doc.get("types", ())]
        # 同時刻にその種類を収集した全資料で書式が一致することを確認する。
        verified = (rid not in known_terms and bool(covered) and all("types" in doc for doc in docs)
                    and all(any(id_pattern(p).fullmatch(rid) for p in doc.get("id_patterns", {}).get(kind, ()))
                            for doc in covered))
        yield [], [], {"id": rid, "name": None, "type": kind, "in_inventory": False if verified else None,
                       "detections": [], "observation_indices": sorted(group.inventories)}


def _identifier_matches(text, term, tokens):
    if tokens is not None and _FLAT_ID.fullmatch(term):
        return term in tokens
    return _word(text, term, resource_id=tokens is not None)


def _match_method(text, aliases, resource, names, ids, tokens):
    rid = resource["id"]
    by_id = _identifier_matches(text, rid, tokens)
    if by_id and (tokens is None or ids[rid] == 1):
        return "id"
    name = resource["name"]
    if name and len(name) >= 4 and names[name] == 1 and _identifier_matches(text, name, tokens):
        return "name"
    if by_id or any(alias in text for alias in aliases):
        return "inferred"
    return None


def _compare(finding, props, notion, mapping, catalog, observations, known_terms):
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
    provider = props.get(fields["provider"]) if "provider" in fields else None
    region = props.get(fields["region"]) if "region" in fields else None
    scoped = [g for g in catalog if _word(scope_text, g.account)
              and (not provider or provider == g.provider) and (not region or region in g.regions)]
    coverage_scope = (scoped[0] if len(scoped) == 1 and "scope" in fields and scope_text == scoped[0].account
                      and provider == scoped[0].provider and region in scoped[0].regions else None)
    allowed_rules = applicable_rules(mapping, props)
    candidates = []
    names = Counter()
    ids = Counter()
    excluded_count = 0
    typed = any(group.typed for group in scoped)
    tokens = _flat_ids(issue_text) if typed else None
    for group in scoped:
        resources = group.resources
        if group is coverage_scope and typed:
            resources = [*resources, *_synthetic_resources(tokens, group, observations, known_terms)]
        enforce_scope = group is not coverage_scope and typed
        for aliases, ambiguous, resource in resources:
            names.update(aliases)
            ids[resource["id"]] += 1
            if excluded_resource(mapping, group.account, resource["id"], aliases):
                if _identifier_matches(issue_text, resource["id"], tokens) or any(a in issue_text for a in aliases):
                    excluded_count += 1
                continue
            candidates.append((aliases, ambiguous, resource, enforce_scope))
    matches = []
    refs = set()
    for aliases, ambiguous, resource, enforce_scope in candidates:
        method = _match_method(issue_text, aliases, resource, names, ids, tokens)
        if not method:
            continue
        evidence = [d for d in resource["detections"] if allowed_rules is None or d["rule"] in allowed_rules]
        if not evidence and any(allowed_rules is None or d["rule"] in allowed_rules for d in ambiguous):
            method = "inferred"
        presence = resource["in_inventory"]
        if presence is False and (enforce_scope or (typed and method == "inferred")):
            presence = None
        matches.append({**resource, "method": method, "in_inventory": presence, "detections": evidence})
        refs.update(resource["observation_indices"])
    if not matches:
        refs.update(i for group in scoped for i in group.current)
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
        "rules_used": sorted({d["rule"] for m in matches if m["method"] != "inferred" for d in m["detections"]}),
        "rules_unfiltered": allowed_rules is None, "excluded_resource_count": excluded_count,
    }


def build_drift(campaign_id, findings, records, mapping, observations):
    """検証済みの観測と、判断画面と同じ形式の課題・原典の行だけを使う。"""
    catalog = _catalog(observations)
    known_terms = {term for g in catalog for aliases, _, row in g.resources for term in (row["id"], *aliases)}
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
                              mapping, catalog, observations, known_terms))
    counts = dict.fromkeys(CLASSIFICATIONS, 0)
    reasons = dict.fromkeys(REASONS, 0)
    for item in items:
        counts[item["classification"]] += 1
        if item["reason"]:
            reasons[item["reason"]] += 1
    metadata = []
    for obs in observations:
        scope = {k: obs["scope"][k] for k in ("provider", "account", "regions")}
        projected = {k: obs[k] for k in ("kind", "collected_at", "method")}
        if obs["kind"] == "inventory":
            projected.update({k: obs[k] for k in ("types", "id_patterns") if k in obs})
        metadata.append({**projected, "scope": scope})
    return {
        "schema_version": "1.0.0", "campaign_id": campaign_id,
        "generated_at": datetime.now(timezone.utc).isoformat(), "observations": metadata,
        "counts": counts, "reasons": reasons, "items": items,
    }
