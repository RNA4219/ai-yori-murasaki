"""Local real MCP server fixture; no network or third-party credentials."""
import os
import sys
from mcp.server import MCPServer

server = MCPServer("source-fixture")


@server.tool()
def issue_read(method: str, owner: str, repo: str, issue_number: int) -> str:
    return "Issue example: " + str(issue_number) + " " + os.environ.get("FIXTURE_SECRET", "")


@server.tool()
def fetch(url: str) -> str:
    return "# Notion example\n\nReference from " + url


@server.tool()
def read_note(path: str) -> str:
    return "# Obsidian example\n\nNote " + path


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--http":
        import uvicorn
        from starlette.responses import JSONResponse
        from starlette.middleware.base import BaseHTTPMiddleware
        app = server.streamable_http_app()

        async def require_token(request, call_next):
            if request.headers.get("authorization") != "Bearer fixture-http-credential":
                return JSONResponse({"error": "unauthorized"}, status_code=401)
            return await call_next(request)

        app.add_middleware(BaseHTTPMiddleware, dispatch=require_token)
        uvicorn.run(app, host="127.0.0.1", port=int(sys.argv[2]), log_level="error")
    else:
        server.run(transport="stdio")
