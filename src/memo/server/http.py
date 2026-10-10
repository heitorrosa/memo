"""HTTP (PRIMARY): bearer gate + /health + Accept negotiation."""
from __future__ import annotations

import contextvars
import hmac
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import mcp.server.streamable_http_manager as _session_manager
from mcp.server.streamable_http import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_SSE,
    StreamableHTTPServerTransport,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from memo import db
from memo.server.shared import VERSION


# Per-request content negotiation. The MCP StreamableHTTP transport picks its
# response mode from one instance flag set once at construction (`json_response`),
# so a JSON-only client (ChatGPT: `Accept: application/json`) is 406-rejected with
# "Client must accept both application/json and text/event-stream". We make that
# flag per-request instead: a contextvar, set by the middleware below from the
# request's Accept header and read through a property override on the transport.
# Requests that accept text/event-stream keep the default (False) and behave
# exactly as before; only JSON-only requests flip to a plain-JSON response.
_JSON_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "memo_json_only", default=False)


class _NegotiatingTransport(StreamableHTTPServerTransport):
    """Transport whose JSON/SSE response mode follows the current request."""

    @property
    def is_json_response_enabled(self) -> bool:
        return _JSON_ONLY.get()

    @is_json_response_enabled.setter
    def is_json_response_enabled(self, value: bool) -> None:
        # Construction passes FastMCP's static `json_response` setting; the
        # per-request contextvar supersedes it. Accept the write, ignore it.
        pass


class _AcceptNegotiation:
    """ASGI middleware: mark JSON-only requests for the transport."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not _is_json_only(scope):
            await self.app(scope, receive, send)
            return
        token = _JSON_ONLY.set(True)
        try:
            await self.app(scope, receive, send)
        finally:
            _JSON_ONLY.reset(token)


def _accept_types(scope) -> list[str]:
    for key, value in scope.get("headers", []):
        if key == b"accept":
            return [t.strip() for t in value.decode("latin-1").split(",")]
    return []


def _is_json_only(scope) -> bool:
    types = _accept_types(scope)
    has_json = any(t.startswith(CONTENT_TYPE_JSON) for t in types)
    has_sse = any(t.startswith(CONTENT_TYPE_SSE) for t in types)
    return has_json and not has_sse


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

    # FastMCP builds its StreamableHTTPSessionManager with the module-level
    # transport class; swap in ours so the per-request flag is honoured.
    _session_manager.StreamableHTTPServerTransport = _NegotiatingTransport
    inner = server.streamable_http_app()
    mcp_app = _AcceptNegotiation(inner)

    @asynccontextmanager
    async def lifespan(app):
        # Mount does not propagate lifespan: run the inner MCP app's
        # lifespan (session_manager.run task group) inside ours, else
        # every /mcp request 500s with "Task group is not initialized".
        async with inner.router.lifespan_context(inner):
            yield

    app = Starlette(lifespan=lifespan,
                    routes=[Route("/health", health_check, methods=["GET"]),
                            Mount("/", app=mcp_app)])
    return BearerAuth(app, token)
