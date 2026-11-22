"""判断用ビューの JSON（aym.decision-view/1）を、AYM の保存データから読み取りだけで作る。

作成: Claude (Opus 5.5) / 2026-11-16 / 入力: 作者の依頼「読む人が判断できるように丸める画面」、
AYM 538e228 の storage.Store・runtime.Runtime と、取り込み済み finding の形（extensions.notion・extensions.enrichment）。

使い方:
    python tools/aym_decision_view.py --campaign <campaign_id> --previous auto \
        --map tools/mapping.example.json --out decision.json

- 書き込みはしない（Store・Runtime の読み取りだけ）。
- build_view() は I/O を持たない純粋な関数。サーバーの API から返すときもこれを使える。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from importlib import resources
from .jsonio import DataError
from .storage import sanitize
from urllib.parse import urlsplit
from typing import Any

SCHEMA = "aym.decision-view/1"
STATES = ("open", "in_progress", "waiting", "done", "wont_fix", "other")
SEVERITIES = ("critical", "high", "medium", "low", "info")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")
_INTENT_DATE = re.compile(r"-\d{8}[A-Z]?$")


def _http_link(*candidates: Any) -> str | None:
    for c in candidates:
        if not isinstance(c, str):
            continue
        try:
            parsed = urlsplit(c)
            if parsed.scheme in ("https", "http") and parsed.hostname and not parsed.username and not parsed.password:
                return c
        except ValueError:
            continue
    return None


def _date_only(value: Any) -> str | None:
    if isinstance(value, str) and _DATE.match(value):
        candidate = value[:10]
        try:
            datetime.strptime(candidate, "%Y-%m-%d")
            return candidate
        except ValueError:
            pass
    return None


def _enrichment(finding: dict) -> dict:
    recs = ((finding.get("extensions") or {}).get("enrichment") or {}).get("records") or []
    kev = sorted({r.get("cve") for r in recs if r.get("kind") == "kev" and r.get("status") == "listed" and r.get("cve")})
    epss = [r for r in recs if r.get("kind") == "epss" and isinstance(r.get("epss"), (int, float))]
    top = max(epss, key=lambda r: r["epss"]) if epss else None
    cves = ((finding.get("extensions") or {}).get("notion") or {}).get("cve") or {}
    ids = cves.get("ids") if isinstance(cves.get("ids"), list) else sorted({r.get("cve") for r in recs if r.get("cve")})
    return {
        "cve_count": len(ids),
        "kev": bool(kev),
        "kev_cves": kev,
        "epss_max": round(top["epss"], 5) if top else None,
        "epss_cve": top.get("cve") if top else None,
        "epss_percentile": round(top["percentile"], 5) if top and isinstance(top.get("percentile"), (int, float)) else None,
    }


def _freshness(findings: list[dict], now: datetime) -> dict:
    """種類（kev・epss）ごとに、期限がいちばん早い記録で古さを判断する（取得の時期が混ざっていても見落とさない）。"""
    oldest: dict[str, dict] = {}
    for f in findings:
        for r in ((f.get("extensions") or {}).get("enrichment") or {}).get("records") or []:
            kind = r.get("kind")
            if kind not in ("kev", "epss"):
                continue
            exp = r.get("expires_at")
            key = exp if isinstance(exp, (int, float)) else float("-inf")  # 期限が読めない記録は古いものとして扱う
            if kind not in oldest or key < oldest[kind][0]:
                oldest[kind] = (key, r)
    out: dict[str, dict] = {}
    for kind, (key, r) in oldest.items():
        known = key != float("-inf")
        out[kind] = {
            "source_version": r.get("source_version"),
            "fetched_at": r.get("fetched_at"),
            "expires_at": datetime.fromtimestamp(key, timezone.utc).isoformat() if known else None,
            "expired": (not known) or key < now.timestamp(),
        }
    return out


def _item(finding: dict, record: dict | None, mapping: dict) -> dict:
    ext = (finding.get("extensions") or {}).get("notion") or {}
    props = record if isinstance(record, dict) else (ext.get("properties") or {})
    fields = mapping.get("fields") or {}
    label = props.get(fields.get("status", ""))
    state = (mapping.get("status_values") or {}).get(label, "other") if label is not None else "other"
    if state not in STATES:
        state = "other"
    severity = finding.get("severity") if finding.get("severity") in SEVERITIES else "info"
    item = {
        "key": ((finding.get("location") or {}).get("path")) or finding.get("finding_id"),
        "ref": props.get(fields.get("key", "")) if fields.get("key") else None,
        "title": finding.get("title") or "",
        "severity": severity,
        "state": state,
        "state_label": label if isinstance(label, str) else None,
        "due": _date_only(props.get(fields.get("due", ""))) if fields.get("due") else None,
        "link": _http_link(*(finding.get("references") or []), ext.get("url")),
    }
    item.update(_enrichment(finding))
    return item



# モニターの deriveFindingStatus() / nextActionFor() と同一の評価規則。
ASSESSMENT_STATUSES = ("confirmed", "candidate", "refuted", "retested", "proposed", "undetermined", "historical", "unknown")

def derive_assessment_status(f: dict | None, historical: bool = False) -> str:
    # An empty finding object is still a finding: JS treats {} as truthy.
    # Preserve the report-level historical flag even when its fields are empty.
    if f is None:
        return "unknown"
    if historical or f.get("historical"):
        return "historical"
    val = f.get("validation") or {}
    if val.get("result") == "success":
        return "retested"
    if val.get("result") == "undetermined":
        return "undetermined"
    if f.get("remediation"):
        return "proposed"
    claims = f.get("claims") or []
    if any(isinstance(c, dict) and c.get("status") == "confirmed" for c in claims):
        return "confirmed"
    if claims and all(isinstance(c, dict) and c.get("status") == "refuted" for c in claims):
        return "refuted"
    if any(isinstance(c, dict) and c.get("status") == "hypothesis" for c in claims):
        return "candidate"
    if ((f.get("classification") or {}).get("cve") or {}).get("status") == "candidate":
        return "candidate"
    return "unknown"


def _unique_findings(findings: list[dict]) -> list[dict]:
    seen: set[str] = set()
    out = []
    for f in findings:
        fid = f.get("finding_id")
        if isinstance(fid, str):
            if fid in seen:
                continue
            seen.add(fid)
        out.append(f)
    return out


def _effective_enrichment(finding: dict, report: dict | None) -> dict:
    if not report:
        return finding
    records = [r for r in (report.get("enrichment") or [])
               if isinstance(r, dict) and r.get("finding_id") == finding.get("finding_id")
               and r.get("kind") in ("kev", "epss")]
    if not records:
        return finding
    ext = dict(finding.get("extensions") or {})
    ext["enrichment"] = {"records": records}
    return {**finding, "extensions": ext}


def _assessment_summary(report: dict | None) -> dict:
    cats = (report.get("coverage") or {}).get("categories") or [] if report else []
    counts = {"executed": 0, "not_executed": 0, "failed": 0}
    for entry in cats:
        if not isinstance(entry, dict):
            continue
        status = entry.get("status")
        if status in counts:
            counts[status] += 1
        else:
            counts["not_executed"] += 1
    # モニターが表示時に補う未実施類型と合わせる。
    existing = {r.get("category") for r in cats if isinstance(r, dict)}
    for category in ("authz", "sqli", "xss", "path_traversal", "dependency_cve", "unknown"):
        if category not in existing:
            counts["not_executed"] += 1
    return {"present": bool(report and report.get("status") == "ready"),
            "historical": bool(report and report.get("historical")),
            "coverage": counts}


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def load_mapping(store) -> dict:
    """Workspaceの明示設定が壊れていれば既定にフォールバックしない。"""
    path = store.workspace / "decision-view" / "mapping.json"
    try:
        if path.parent.is_symlink() or path.is_symlink():
            raise ValueError("mapping symlink")
        if path.exists():
            raw = path.read_bytes()
        else:
            raw = resources.files("ai_yori_murasaki").joinpath("decision_mapping.json").read_bytes()
        if len(raw) > 1024 * 1024:
            raise ValueError("mapping too large")
        mapping = json.loads(raw, object_pairs_hook=_strict_pairs,
                             parse_constant=lambda _: (_ for _ in ()).throw(ValueError("nonfinite")))
        if not isinstance(mapping, dict) or not isinstance(mapping.get("fields"), dict) or not isinstance(mapping.get("status_values"), dict):
            raise ValueError("mapping shape")
        if any(not isinstance(v, str) for v in mapping["fields"].values()):
            raise ValueError("mapping field names")
        if any(not isinstance(k, str) or v not in STATES for k, v in mapping["status_values"].items()):
            raise ValueError("mapping status values")
        rules = mapping.get("rules", {})
        if not isinstance(rules, dict) or set(rules) - {"epss_high", "due_soon_days", "top_n"}:
            raise ValueError("mapping rules")
        if "epss_high" in rules and (type(rules["epss_high"]) not in (int, float) or
                                     not 0 <= rules["epss_high"] <= 1):
            raise ValueError("mapping epss_high")
        for key, lower in (("due_soon_days", 0), ("top_n", 1)):
            if key in rules and (type(rules[key]) is not int or rules[key] < lower):
                raise ValueError("mapping rule bounds")
        return mapping
    except (OSError, ValueError, UnicodeDecodeError, TypeError) as exc:
        raise DataError("decision-view mapping unavailable", "hold") from exc


def read_decision_view(runtime, campaign_id: str, selected_previous: str | None = None, summaries=None) -> dict:
    from .assessment import load_report
    campaign, findings, records = _load(runtime, runtime.store, campaign_id, summaries)
    report = load_report(runtime, campaign_id)
    previous = None
    campaigns = ([entry.campaign for entry in summaries.values() if entry.campaign]
                 if summaries is not None else runtime.list_campaigns())
    options = previous_candidates(campaigns).get(campaign_id, ())
    prev_id = options[0] if len(options) == 1 else None
    if selected_previous in options:
        prev_id = selected_previous
    incomplete = bool(prev_id and summaries is not None and
                      (prev_id not in summaries or summaries[prev_id].status != "complete"))
    if prev_id and not incomplete:
        pc, pf, pr = _load(runtime, runtime.store, prev_id, summaries)
        previous = {"campaign": pc, "findings": pf, "records": pr}
    result = build_view(campaign, findings, records,
                        mapping=load_mapping(runtime.store), now=datetime.now(timezone.utc),
                        previous=previous, assessment=report if report is not None else {})
    if incomplete:
        result["comparison_status"] = "incomplete"
    elif len(options) > 1 and not prev_id:
        result["comparison_status"] = "ambiguous"
        result["previous_candidates"] = list(options)
    return sanitize(result, runtime.store.secrets)


def build_view(campaign: dict, findings: list[dict], records: dict[str, dict] | None = None, *,
               mapping: dict, now: datetime, previous: dict | None = None,
               name: str | None = None, assessment: dict | None = None) -> dict:
    """campaign・findings（と元の行 records: finding_id → 行）から判断用ビューを作る。

    previous は {"campaign": {...}, "findings": [...], "records": {...}} か None。
    """
    records = records or {}
    findings = _unique_findings(findings)
    effective = [_effective_enrichment(f, assessment) for f in findings]
    items = [_item(f, records.get(f.get("finding_id")), mapping) for f in effective]
    if assessment is not None:
        by_id = {f.get("finding_id"): f for f in (assessment.get("findings") or []) if isinstance(f, dict)}
        for f, it in zip(findings, items):
            match = by_id.get(f.get("finding_id"))
            it["assessment_status"] = derive_assessment_status(match, bool(assessment.get("historical"))) if match else None
    by_key: dict[str, dict] = {}
    for it in items:
        by_key.setdefault(it["key"], it)
    removed: list[dict] = []
    prev_info = None
    if previous:
        prev_recs = previous.get("records") or {}
        prev_items = {}
        for f in _unique_findings(previous.get("findings") or []):
            it = _item(f, prev_recs.get(f.get("finding_id")), mapping)
            prev_items.setdefault(it["key"], it)
        for it in items:
            p = prev_items.get(it["key"])
            if p is None:
                it["change"] = "new"
            elif p["state"] != it["state"] or p["state_label"] != it["state_label"]:
                it["change"] = "state_changed"
                it["prev_state"] = p["state"]
                it["prev_state_label"] = p["state_label"]
            else:
                it["change"] = "unchanged"
        removed = [{"key": k, "ref": p["ref"], "title": p["title"], "prev_state": p["state"],
                    "prev_state_label": p["state_label"]} for k, p in prev_items.items() if k not in by_key]
        pc = previous.get("campaign") or {}
        prev_info = {"id": pc.get("campaign_id"), "created_at": pc.get("created_at")}
    result = {
        "schema": SCHEMA,
        "generated_at": now.astimezone(timezone.utc).isoformat(),
        "campaign": {
            "id": campaign.get("campaign_id"),
            "name": name or campaign.get("intent_id") or campaign.get("campaign_id"),
            "created_at": campaign.get("created_at"),
            **({"status": campaign.get("status")} if "status" in campaign else {}),
        },
        "previous": prev_info,
        "freshness": _freshness(effective, now),
        "rules": mapping.get("rules") or {},
        "items": items,
        "removed": removed,
    }
    if assessment is not None:
        result["assessment"] = _assessment_summary(assessment)
    return result


# --- AYM の保存データから読む部分（ここから下だけが AYM に依存する） ---

def _load(runtime, store, campaign_id: str, summaries=None) -> tuple[dict, list[dict], dict[str, dict]]:
    if summaries is None:
        campaign = runtime.get_campaign(campaign_id)
        findings = _unique_findings([store.get_object(ref) for ref in runtime.list_records(campaign_id, "finding")])
    else:
        campaign = summaries[campaign_id].campaign
        findings = list(summaries[campaign_id].findings.values())
    records: dict[str, dict] = {}
    for f in findings:
        ref = ((f.get("extensions") or {}).get("notion") or {}).get("source_record_ref")
        if not ref:
            continue
        try:
            obj = store.get_object(ref)
        except Exception:  # 元の行が読めなくても、finding に残っている列で続ける
            continue
        if isinstance(obj, dict) and isinstance(obj.get("record"), dict):
            records[f["finding_id"]] = obj["record"]
    return campaign, findings, records


def _series(campaign: dict) -> str:
    return _INTENT_DATE.sub("", campaign.get("intent_id") or "")


def previous_candidates(campaigns: list[dict]) -> dict[str, tuple[str, ...]]:
    """直前の時刻にある全候補を返す。同時刻の案件の間には辺を張らない。"""
    families: dict[str, dict[tuple[datetime, str], list[dict]]] = {}
    for campaign in campaigns:
        name = _series(campaign)
        stamp = campaign.get("created_at")
        if not name or not campaign.get("campaign_id") or not isinstance(stamp, str):
            continue
        try:
            moment = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        except ValueError:
            continue
        if moment.tzinfo is None:
            moment = moment.replace(tzinfo=timezone.utc)
        # 契約が許す小数秒9桁のうち、datetimeに入らない末尾3桁を保持する。
        extra = re.search(r"\.\d{6}(\d{1,3})", stamp)
        key = (moment, extra[1].ljust(3, "0") if extra else "000")
        families.setdefault(name, {}).setdefault(key, []).append(campaign)
    parents: dict[str, tuple[str, ...]] = {}

    for name, family in families.items():
        previous = ()
        for moment in sorted(family):
            same_time = family[moment]
            for campaign in same_time:
                if previous and campaign["intent_id"] != name:
                    parents[campaign["campaign_id"]] = previous
            previous = tuple(sorted(c["campaign_id"] for c in same_time))
    return parents


def previous_campaign_ids(campaigns: list[dict]) -> dict[str, str]:
    return {cid: candidates[0] for cid, candidates in previous_candidates(campaigns).items()
            if len(candidates) == 1}


def pick_previous(campaigns: list[dict], current: dict) -> str | None:
    name = _series(current)
    if not name or name == current.get("intent_id"):
        return None
    cid = current.get("campaign_id")
    family = [c for c in campaigns if c.get("campaign_id") != cid and _series(c) == name]
    return previous_campaign_ids([*family, current]).get(cid)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="AYM の Campaign から判断用ビューの JSON を作る（読み取りだけ）")
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--previous", default="auto", help="比べる Campaign の ID、auto（既定）、none")
    ap.add_argument("--map", required=True, help="列名と状態の対応（mapping.example.json を参照）")
    ap.add_argument("--name", help="画面に出す名前（既定は intent_id）")
    ap.add_argument("--out", required=True)
    ap.add_argument("--workspace", default=os.environ.get("AYM_WORKSPACE"))
    ap.add_argument("--trusted", default=os.environ.get("AYM_TRUSTED_ROOT"))
    args = ap.parse_args(argv)
    if not args.workspace or not args.trusted:
        ap.error("--workspace と --trusted（または AYM_WORKSPACE・AYM_TRUSTED_ROOT）が要る")
    from ai_yori_murasaki.runtime import Runtime
    from ai_yori_murasaki.storage import Store
    store = Store(Path(args.workspace), Path(args.trusted))
    runtime = Runtime(store)
    mapping = json.loads(Path(args.map).read_text(encoding="utf-8"))
    campaign, findings, records = _load(runtime, store, args.campaign)
    prev_id = None
    if args.previous == "auto":
        prev_id = pick_previous(runtime.list_campaigns(), campaign)
    elif args.previous != "none":
        prev_id = args.previous
    previous = None
    if prev_id:
        pc, pf, pr = _load(runtime, store, prev_id)
        previous = {"campaign": pc, "findings": pf, "records": pr}
    from .assessment import load_report
    report = load_report(runtime, args.campaign)
    view = sanitize(build_view(campaign, findings, records, mapping=mapping,
                               now=datetime.now(timezone.utc), previous=previous, name=args.name,
                               assessment=report if report is not None else {}), store.secrets)
    out = Path(args.out)
    out.write_text(json.dumps(view, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
    print(json.dumps({"out": str(out), "items": len(view["items"]), "previous": prev_id,
                      "removed": len(view["removed"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
