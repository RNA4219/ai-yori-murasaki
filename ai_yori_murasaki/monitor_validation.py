"""モニターの台帳読み取りに使う、ローカルの証跡スキーマ検証。"""
import jsonschema_rs

from .contracts import FORMAT, load_schema, validate
from .jsonio import canonical


def _reject_external(uri: str):
    raise ValueError("external schema retrieval is disabled")


_common = load_schema("common")
_registry = jsonschema_rs.Registry([(_common["$id"], _common)], retriever=_reject_external)
_validator = jsonschema_rs.Draft202012Validator(
    load_schema("evidence_event"), registry=_registry, retriever=_reject_external,
    validate_formats=True,
    formats={"aym-timestamp": lambda value: FORMAT.conforms(value, "aym-timestamp")},
)


def validate_monitor_event(event: dict) -> None:
    canonical(event)
    try:
        if _validator.is_valid(event):
            return
    except (OverflowError, ValueError):
        # ネイティブ側で表現できない値にも元の契約を適用する。
        pass
    validate("evidence_event", event)
