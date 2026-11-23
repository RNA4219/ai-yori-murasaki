"""10万案件の一覧を再現するための、独立した合成ストアを作成する。"""
import argparse
import json
from pathlib import Path

from ai_yori_murasaki.jsonio import canonical
from ai_yori_murasaki.storage import ZERO, event_hash


def create(root: Path, count: int) -> None:
    root.mkdir(exist_ok=False)
    runs = root / "workspace" / "runs"
    runs.mkdir(parents=True)
    (root / "trusted").mkdir()
    source = Path(__file__).resolve().parents[2] / "fixtures/v1/contracts.json"
    template = json.loads(source.read_bytes())["evidence_event"]
    stamp = "2026-11-23T00:00:00Z"
    with (runs.parent / "catalog.json").open("wb") as catalog:
        catalog.write(b'{"schema_version":"1.0.0","runs":{')
        for number in range(count):
            cid, rid = f"c{number:06}", f"r{number:06}"
            campaign = dict(campaign_id=cid, created_at=stamp, intent_id=f"case-{number:06}", status="created")
            event = dict(template, campaign_id=cid, run_id=rid, sequence=1, previous_event_hash=ZERO,
                         timestamp=stamp, finding=None, evidence_refs=[],
                         extensions={"campaign_snapshot": campaign, "run_snapshot": {"started_at": stamp}})
            event["event_hash"] = event_hash(event)
            path = runs / rid
            path.mkdir()
            (path / "events.jsonl").write_bytes(canonical(event) + b"\n")
            entry = dict(campaign_id=cid, run_id=rid, campaign=campaign, phase="verify", status="completed")
            if number:
                catalog.write(b",")
            catalog.write(canonical(rid) + b":" + canonical(entry))
        catalog.write(b"}}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path, help="新規作成する空のストアのパス")
    parser.add_argument("--count", type=int, default=100_000)
    args = parser.parse_args()
    if args.count < 1:
        parser.error("件数には正の整数を指定してください")
    create(args.root, args.count)
