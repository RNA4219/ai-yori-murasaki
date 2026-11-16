"""見本のデータ（samples/decision-sample.json と web/decision-sample.json）を作る。中身はすべて架空。

作成: Claude (Opus 5.5) / 2026-11-16 / 入力: なし（乱数の種を固定して作る）。
tools/aym_decision_view.py の build_view() に、AYM の finding と同じ形の架空データを通して作る。
    python samples/make_sample.py
"""
import importlib.util
import json
import random
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
spec = importlib.util.spec_from_file_location("aym_decision_view", ROOT / "tools" / "aym_decision_view.py")
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

NOW = datetime(2026, 11, 16, 3, 0, tzinfo=timezone.utc)
TODAY = NOW.date()
MAPPING = json.loads((ROOT / "tools" / "mapping.example.json").read_text(encoding="utf-8"))

HOSTS = ["web-01", "web-02", "api-gw", "batch-03", "report-db", "bastion", "mail-relay", "cache-01", "worker-07", "admin-ui"]
THEMES = [
    ("の SSH（22 番）がインターネットに全開放", "critical"),
    ("の OS がサポート終了", "critical"),
    ("の古い TLS 設定", "medium"),
    ("のライブラリに既知の脆弱性", "high"),
    ("の管理画面に多要素認証がない", "high"),
    ("のバケットが公開読み取り", "critical"),
    ("のログが 7 日で消える", "low"),
    ("の IMDSv1 が使える", "medium"),
    ("の default SG にルールが残っている", "high"),
    ("の監視が止まっている", "info"),
]
STATUS = ["未対応"] * 6 + ["対応中"] * 2 + ["判断待ち"] + ["修正済"] * 3


def finding(i: int, title: str, sev: str, status: str, due: str | None, cves: list[str], kev: set[str], epss: dict) -> tuple[dict, dict]:
    fid = f"f-sample-{i:04d}"
    url = f"https://example.invalid/backlog/{i:04d}"
    recs = []
    for c in cves:
        recs.append({"kind": "kev", "cve": c, "status": "listed" if c in kev else "not_listed",
                     "source_version": "2026.11.13", "fetched_at": "2026-11-16T01:57:42+00:00",
                     "expires_at": (NOW + timedelta(days=1)).timestamp()})
        recs.append({"kind": "epss", "cve": c, "status": "scored", "epss": epss[c], "percentile": min(0.99999, epss[c] * 3 + 0.4),
                     "source_version": "model v2026.06.15 / score_date 2026-11-15", "fetched_at": "2026-11-16T01:57:43+00:00",
                     "expires_at": (NOW + timedelta(days=1)).timestamp()})
    f = {"finding_id": fid, "title": title, "severity": sev, "source": "notion",
         "location": {"path": url, "line": None, "column": None}, "references": [url],
         "extensions": {"notion": {"url": url, "properties": {"ステータス": status, "棚卸キー": f"EX-{i:03d}"},
                                   "cve": {"ids": cves}} if cves else {"url": url, "properties": {"ステータス": status, "棚卸キー": f"EX-{i:03d}"}}}}
    if recs:
        f["extensions"]["enrichment"] = {"records": recs}
    row = {"ステータス": status, "棚卸キー": f"EX-{i:03d}", "date:対応期限:start": due}
    return f, row


def build(n: int, shift: int):
    findings, rows = [], {}
    for i in range(1, n + 1):
        r = random.Random(20261116 + i)  # 項目ごとに固定。前回と今回で同じ値になる
        host = HOSTS[i % len(HOSTS)]
        theme, sev = THEMES[(i * 7 + shift) % len(THEMES)]
        status = r.choice(STATUS)
        due = None if i % 9 == 0 else (TODAY + timedelta(days=r.randint(-40, 20))).isoformat()
        cves, kev, epss = [], set(), {}
        if "ライブラリ" in theme or "サポート終了" in theme:
            for k in range(r.randint(1, 4)):
                c = f"CVE-2099-{i:02d}{k:02d}"
                cves.append(c)
                epss[c] = round(r.random() ** 3, 5)
            if i % 3 == 0:
                kev.add(cves[0])
                epss[cves[0]] = round(0.5 + r.random() * 0.45, 5)
        f, row = finding(i, host + " " + theme, sev, status, due, cves, kev, epss)
        findings.append(f)
        rows[f["finding_id"]] = row
    return findings, rows


prev_f, prev_r = build(34, 0)
cur_f, cur_r = build(36, 0)
# 前回からの変化を作る: いくつか片付ける、状態を変える、1 件なくす
for fid in ("f-sample-0003", "f-sample-0011", "f-sample-0017"):
    cur_r[fid]["ステータス"] = "修正済"
    next(f for f in cur_f if f["finding_id"] == fid)["extensions"]["notion"]["properties"]["ステータス"] = "修正済"
cur_r["f-sample-0006"]["ステータス"] = "判断待ち"
cur_f = [f for f in cur_f if f["finding_id"] != "f-sample-0030"]
view = mod.build_view({"campaign_id": "c-sample-current", "intent_id": "INT-EXAMPLE-BACKLOG-20261116A", "created_at": "2026-11-16T02:00:00Z"},
                      cur_f, cur_r, mapping=MAPPING, now=NOW, name="見本: 架空の課題台帳（11/16）",
                      previous={"campaign": {"campaign_id": "c-sample-previous", "created_at": "2026-11-13T02:00:00Z"},
                                "findings": prev_f, "records": prev_r})
text = json.dumps(view, ensure_ascii=False, indent=1) + "\n"
(ROOT / "samples" / "decision-sample.json").write_text(text, encoding="utf-8")
(ROOT / "web" / "decision-sample.json").write_text(text, encoding="utf-8")
print("items", len(view["items"]), "removed", len(view["removed"]))
