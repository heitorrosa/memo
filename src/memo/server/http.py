"""HTTP (PRIMARY): bearer gate + /health."""
from __future__ import annotations

import hmac
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from memo import db
from memo.server.shared import VERSION


class BearerAuth:
    def __init__(self, app, token: str):
        self.app, self.token = app, token

    @staticmethod
    def _matches(presented: str, expected: str) -> bool:
        try:
            return hmac.compare_digest(presented, expected)
        except Exception:
            return False

    def _authorized(self, scope) -> bool:
        # Header wins whenever the key is present, even if its value is
        # empty: a malformed Authorization header is not "absent".
        presented_header = None
        for header_key, header_value in scope.get("headers", []):
            if header_key == b"authorization":
                presented_header = header_value.decode("latin-1")
                break
        if presented_header is not None:
            return self._matches(presented_header, f"Bearer {self.token}")
        # Fallback for clients (ChatGPT custom MCP) that only offer OAuth /
        # No-authentication and cannot set an Authorization header.
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        presented_query = (query.get("access_token") or [""])[0]
        return self._matches(presented_query, self.token)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        if not self._authorized(scope):
            body = b'{"error":"unauthorized"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body))
                                     .encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


def build_http_app(server, token: str):
    async def health_check(request):
        return JSONResponse({"ok": True, "version": VERSION,
                             "vec": "vec" if db.HAS_VEC else "bruteforce"})

    mcp_app = server.streamable_http_app()

    @asynccontextmanager
    async def lifespan(app):
        # Mount does not propagate lifespan: run the inner MCP app's
        # lifespan (session_manager.run task group) inside ours, else
        # every /mcp request 500s with "Task group is not initialized".
        async with mcp_app.router.lifespan_context(mcp_app):
            yield

    app = Starlette(lifespan=lifespan,
                    routes=[Route("/health", health_check, methods=["GET"]),
                            Mount("/", app=mcp_app)])
    return BearerAuth(app, token)
