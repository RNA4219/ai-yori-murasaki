"""オプトインのモニター認証で扱う、同一ホストの戻り先と Cookie。"""
from __future__ import annotations

import hmac
import re
import secrets
from urllib.parse import parse_qsl, urlencode
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .google_sso import GoogleSSO


PAGES = {"/", "/decision", "/architecture", "/intake"}
_IDENTIFIER = re.compile(r"[a-z0-9][a-z0-9-]{0,63}")


def page_target(path: str, query: dict[str, str]) -> str:
    if path not in PAGES:
        return "/"
    selected = {}
    if query.get("view") in ("blue", "operator"):
        selected["view"] = query["view"]
    if path == "/architecture":
        if _IDENTIFIER.fullmatch(query.get("map", "")):
            selected["map"] = query["map"]
        elif query.get("system") == "aym":
            selected["system"] = "aym"
    if path == "/decision" and _IDENTIFIER.fullmatch(query.get("campaign", "")):
        selected["campaign"] = query["campaign"]
    return path + ("?" + urlencode(selected) if selected else "")


def return_target(raw: str) -> str:
    path, _, search = raw.partition("?")
    if path not in PAGES or "#" in raw or len(raw) > 512:
        return "/"
    pairs = parse_qsl(search, keep_blank_values=True)
    query = dict(pairs)
    return page_target(path, query) if len(query) == len(pairs) else "/"


def login_path(target: str) -> str:
    return "/auth/google/start?" + urlencode({"next": target})


def read_cookie(header: str, name: str) -> str | None:
    # Cookie はポート間で共有される。受信した中からこのモニターの名前を一つだけ読む。
    values = []
    for item in header.split(";"):
        key, sep, value = item.strip().partition("=")
        if sep and key == name:
            values.append(value)
    if len(values) == 1 and re.fullmatch(r"[A-Za-z0-9_-]{1,256}", values[0]):
        return values[0]
    return None


def cookie(name: str, value: str, path: str, max_age: int) -> str:
    # Google からのトップレベル遷移を許す。Domain を付けず、このホストだけに限定する。
    return f"{name}={value}; Path={path}; Max-Age={max_age}; Secure; HttpOnly; SameSite=Lax"


class MonitorAuth:
    def __init__(self, provider: GoogleSSO) -> None:
        self.provider = provider
        self.key = secrets.token_bytes(32)

    def for_session(self, session: str) -> str:
        return hmac.digest(self.key, session.encode("ascii"), "sha256").hex()

    def accepts(self, session: str, form_token: str | None) -> bool:
        return (form_token is not None and re.fullmatch(r"[a-f0-9]{64}", form_token) is not None
                and hmac.compare_digest(self.for_session(session), form_token))
