"""初期監査から独立した案件受付とモニター向けの読み取り。"""
from hashlib import sha256
from pathlib import Path
import re

from .jsonio import DataError
from .storage import ID_RE, Store, _assert_no_ancestor_reparse, _is_reparse, redaction_order, sanitize

PAGE_SIZE = 50
RECEIPT_ID = re.compile(r"int-[0-9a-f]{32}")


def _path(store: Store) -> Path:
    # 台帳と補助ファイルは固定名の直下の子だけ。初回の並行作成で親の有無が
    # 変わるため、Windows の非実在パスに対する resolve() の表記には依存しない。
    _assert_no_ancestor_reparse(store.workspace)
    filename = "case-intake.sqlite3"
    for suffix in ("", "-journal", "-wal", "-shm"):
        if _is_reparse(store.workspace / (filename + suffix)):
            raise DataError("受付台帳のパスにリンクは指定できません")
    return store.workspace / filename


def _valid_text(value: str, limit: int) -> bool:
    return bool(value and len(value) <= limit and value.replace("　", " ").isprintable())


def register(store: Store, name: str, key: str) -> dict:
    from . import _intake_index

    name = name.strip()
    if not _valid_text(name, 160) or sanitize(name, store.secrets) != name:
        raise DataError("案件名が不正です")
    if not _valid_text(key, 256) or not key.strip():
        raise DataError("再送キーが不正です")
    path = _path(store)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        record = _intake_index.register(path, sha256(key.encode()).digest(), name)
    except OSError as exc:
        raise DataError("受付台帳を更新できません", "intake_unavailable") from exc
    if record is None:
        raise DataError("同じ再送キーに異なる案件名が指定されました", "idempotency_conflict")
    receipt_id, name, registered_at = record
    return {"receipt_id": receipt_id, "name": name, "registered_at": registered_at}


def link(store: Store, receipt_id: str, campaign_id: str) -> dict:
    from . import _intake_index
    from .runtime import Runtime

    if not RECEIPT_ID.fullmatch(receipt_id) or not ID_RE.fullmatch(campaign_id):
        raise DataError("受付IDまたは監査案件IDが不正です")
    if sanitize([receipt_id, campaign_id], store.secrets) != [receipt_id, campaign_id]:
        raise DataError("受付IDまたは監査案件IDを公開できません")
    path = _path(store)
    if not path.exists():
        raise DataError("受付が見つかりません", "not_found")
    # 書き込みの前に、このワークスペースの検証済み台帳に監査が存在することを確かめる。
    Runtime(store).get_campaign(campaign_id)
    try:
        saved = _intake_index.link(path, receipt_id, campaign_id)
    except OSError as exc:
        raise DataError("受付台帳を更新できません", "intake_unavailable") from exc
    if saved is None:
        raise DataError("受付が見つかりません", "not_found")
    if saved != campaign_id:
        raise DataError("受付は別の監査に連結されています", "intake_link_conflict")
    return {"receipt_id": receipt_id, "campaign_id": campaign_id}


def search(store: Store, query: str) -> dict:
    query = query.strip()
    if query and not _valid_text(query, 160):
        raise DataError("検索条件が不正です", "invalid_query")
    path = _path(store)
    page = {"receipts": [], "has_more": False}
    if not path.exists():
        return page

    from . import _intake_index

    try:
        rows = _intake_index.search(path, query, bool(RECEIPT_ID.fullmatch(query)), PAGE_SIZE + 1,
                                    redaction_order(store.secrets))
    except OSError as exc:
        raise DataError("受付台帳を読み取れません", "intake_unavailable") from exc
    for receipt_id, name, registered_at, campaign_id in rows[:PAGE_SIZE]:
        record = {"receipt_id": receipt_id, "name": name, "registered_at": registered_at}
        if campaign_id is not None:
            record["campaign_id"] = campaign_id
        page["receipts"].append({key: sanitize(value, store.secrets) for key, value in record.items()})
    page["has_more"] = len(rows) > PAGE_SIZE
    return page
