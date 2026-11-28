"""Explicit, bounded read-only MCP ingestion. Never enabled in agent grants."""
import asyncio
import os
from contextlib import AsyncExitStack
from urllib.parse import urlsplit

from .jsonio import MAX_JSON_BYTES, DataError, canonical, loads
from .observations import local_path
from .sources import MAX_TEXT, safe_url
from .storage import read_regular_bytes, sanitize

_LOCAL_HTTP_HOSTS = ("127.0.0.1", "localhost")
READ_TOOLS = {
    "github": {"issue_read", "get_issue", "get_issue_comments"},
    "notion": {"fetch", "notion-fetch", "API-retrieve-a-page", "API-get-block-children"},
    "obsidian": {"read_note", "read_file", "get_file_contents"},
}


def validate_request(provider, tool, arguments):
    if provider not in READ_TOOLS or tool not in READ_TOOLS[provider] or not isinstance(arguments, dict):
        raise DataError("MCP tool is not an allowed source reader", "policy_denied")
    if tool == "issue_read" and arguments.get("method") not in ("get", "get_comments"):
        raise DataError("only issue get/get_comments permitted", "policy_denied")
    if len(canonical(arguments)) > 16384:
        raise DataError("MCP arguments too large")


def read_mcp_json(path):
    """接続設定と引数は、操作者が指定したローカルの通常ファイルだけから読む。"""
    return loads(read_regular_bytes(local_path(path), MAX_JSON_BYTES))


def import_mcp(sources, *, provider, tool, arguments, config_file, source_url, title, links=()):
    validate_request(provider, tool, arguments)
    config = read_mcp_json(config_file)
    if not isinstance(config, dict) or set(config) - {"transport", "command", "args", "env", "url", "bearer_env"}:
        raise DataError("invalid MCP connection config")
    transport = config.get("transport")
    if transport == "stdio":
        if (not isinstance(config.get("command"), str) or not config["command"]
                or not isinstance(config.get("args", []), list)
                or len(config.get("args", [])) > 32
                or not all(isinstance(x, str) and len(x) <= 4096 for x in config.get("args", []))):
            raise DataError("invalid MCP command")
        env_names = config.get("env", {})
        if not isinstance(env_names, dict) or len(env_names) > 16:
            raise DataError("invalid MCP environment mapping")
        env = {}
        for target, name in env_names.items():
            if not isinstance(target, str) or not isinstance(name, str) or not os.environ.get(name):
                raise DataError("MCP credential environment variable missing")
            env[target] = os.environ[name]
    elif transport == "http":
        url = urlsplit(config.get("url", ""))
        if (url.username or url.password or url.query or url.fragment
                or not url.hostname or (url.scheme != "https" and not (
                    url.scheme == "http" and url.hostname in _LOCAL_HTTP_HOSTS))):
            raise DataError("MCP requires HTTPS or an explicit localhost endpoint")
        env = {}
        if config.get("bearer_env"):
            key = config["bearer_env"]
            if not isinstance(key, str) or not os.environ.get(key):
                raise DataError("MCP bearer environment variable missing")
            env["Authorization"] = "Bearer " + os.environ[key]
    else:
        raise DataError("MCP transport must be stdio or http")
    credentials = tuple(value for value in env.values() if value)
    credentials += tuple(value[7:] for value in credentials if value.startswith("Bearer ") and len(value) > 7)
    secrets = (*sources.store.secrets, *credentials)
    safe_url(source_url, secrets)
    if not isinstance(links, (list, tuple)) or len(links) > 32:
        raise DataError("too many source links")
    for link in links:
        safe_url(link, secrets)
    title = sanitize(title, credentials)
    try:
        body = asyncio.run(_read(config, env, tool, arguments))
    except DataError:
        raise
    except Exception as exc:
        # Never persist server/config/error text that could contain credentials.
        raise DataError("MCP read failed; check connection and authorization", "adapter_unavailable") from None
    body = sanitize(body, credentials)
    return sources.add(provider=provider, source_url=source_url, title=title,
                       body=body, links=links, transport="mcp")


async def _read(config, env, tool, arguments):
    try:
        from mcp import Client, StdioServerParameters
        from mcp.client.streamable_http import streamable_http_client
        from mcp.client.stdio import stdio_client
        from .source_mcp_http import http_client
    except ImportError:
        raise DataError("install ai-yori-murasaki[mcp] to use MCP", "adapter_unavailable") from None
    async with asyncio.timeout(30), AsyncExitStack() as stack:
        if config["transport"] == "stdio":
            params = StdioServerParameters(command=config["command"], args=config.get("args", []), env=env)
            errlog = stack.enter_context(open(os.devnull, "w"))
            target = stdio_client(params, errlog=errlog)
        else:
            local = urlsplit(config["url"]).hostname in _LOCAL_HTTP_HOSTS
            http = await stack.enter_async_context(http_client(env, local=local))
            target = streamable_http_client(config["url"], http_client=http, max_sse_event_size=MAX_TEXT)
        async with Client(target, read_timeout_seconds=20) as client:
            result = await client.call_tool(tool, arguments)
            if result.is_error:
                raise DataError("MCP source reader returned an error", "adapter_unavailable")
            texts = [part.text for part in result.content if getattr(part, "type", None) == "text"]
            body = "\n\n".join(texts)
            if not body and result.structured_content:
                body = canonical(result.structured_content).decode("utf-8")
            if not body.strip() or len(body.encode("utf-8")) > MAX_TEXT:
                raise DataError("MCP source text empty or too large")
            return body
