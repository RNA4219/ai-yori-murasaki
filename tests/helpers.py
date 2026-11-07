from copy import deepcopy
from pathlib import Path

from ai_yori_murasaki.jsonio import read_json

ROOT = Path(__file__).resolve().parents[1]
BUNDLE = read_json(ROOT / "fixtures/p0/contracts.json")


def example(kind):
    return deepcopy(BUNDLE[kind])


def record(event_id="event-1", actor="blue", visibility="blue"):
    event = example("evidence_event")
    for field in ("sequence", "previous_event_hash", "event_hash", "result_digest"):
        event.pop(field)
    event.update(event_id=event_id, actor=actor, visibility=visibility)
    return event
