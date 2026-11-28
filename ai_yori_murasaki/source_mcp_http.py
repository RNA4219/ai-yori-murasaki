"""MCP SDK へ渡す HTTP 応答を、JSON や SSE の展開前に制限する。"""
from collections.abc import AsyncGenerator

import httpx2

from .jsonio import DataError
from .sources import MAX_TEXT

# 本文の JSON エスケープ（最大6倍）とプロトコルのメタデータを許容する。
MAX_HTTP_BYTES = 6 * MAX_TEXT + 64 * 1024


class _LimitedStream(httpx2.AsyncByteStream):
    def __init__(self, stream):
        self.stream = stream

    async def __aiter__(self):
        received = 0
        chunks = self.stream.__aiter__()
        try:
            async for chunk in chunks:
                received += len(chunk)
                if received > MAX_HTTP_BYTES:
                    raise DataError("MCP response exceeds size limit")
                yield chunk
        finally:
            if isinstance(chunks, AsyncGenerator):
                await chunks.aclose()

    async def aclose(self):
        await self.stream.aclose()


async def _limit_response(response):
    if response.headers.get("content-encoding", "identity").strip().lower() != "identity":
        raise DataError("MCP requires an uncompressed HTTP response")
    response.stream = _LimitedStream(response.stream)


def http_client(headers, *, local):
    # ローカル接続は直接配送し、システムのHTTPプロキシに認証値や資料を渡さない。
    transport = httpx2.AsyncHTTPTransport(proxy=None) if local else None
    return httpx2.AsyncClient(headers={**headers, "Accept-Encoding": "identity"}, timeout=20,
                             follow_redirects=False, transport=transport, trust_env=not local,
                             event_hooks={"response": [_limit_response]})
