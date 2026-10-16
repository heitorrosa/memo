from __future__ import annotations

import contextvars
import hmac
from contextlib import asynccontextmanager
from urllib.parse import parse_qs

import mcp.server.streamable_http as streamable_http
import mcp.server.streamable_http_manager as session_manager
from mcp.server.streamable_http import (
    CONTENT_TYPE_JSON,
    CONTENT_TYPE_SSE,
    StreamableHTTPServerTransport,
)
from mcp.server.transport_security import (
    TransportSecurityMiddleware,
    TransportSecuritySettings,
)
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from memo import db
from memo.server.shared import VERSION


JSON_ONLY: contextvars.ContextVar[bool] = contextvars.ContextVar(
    "memo_json_only", default=False)


class NegotiatingTransport(StreamableHTTPServerTransport):
    @property
    def is_json_response_enabled(self) -> bool:
        return JSON_ONLY.get()

    @is_json_response_enabled.setter
    def is_json_response_enabled(self, value: bool) -> None:
        pass


class AcceptNegotiation:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not is_json_only(scope):
            await self.app(scope, receive, send)
            return
        token = JSON_ONLY.set(True)
        try:
            await self.app(scope, receive, send)
        finally:
            JSON_ONLY.reset(token)


def accept_types(scope) -> list[str]:
    for key, value in scope.get("headers", []):
        if key == b"accept":
            return [t.strip() for t in value.decode("latin-1").split(",")]
    return []


def is_json_only(scope) -> bool:
    types = accept_types(scope)
    has_json = any(t.startswith(CONTENT_TYPE_JSON) for t in types)
    has_sse = any(t.startswith(CONTENT_TYPE_SSE) for t in types)
    return has_json and not has_sse


class BearerAuth:
    def __init__(self, app, token: str):
        self.app, self.token = app, token

    @staticmethod
    def matches(presented: str, expected: str) -> bool:
        try:
            return hmac.compare_digest(presented, expected)
        except Exception:
            return False

    def authorized(self, scope) -> bool:
        presented_header = None
        for header_key, header_value in scope.get("headers", []):
            if header_key == b"authorization":
                presented_header = header_value.decode("latin-1")
                break
        if presented_header is not None:
            return self.matches(presented_header, f"Bearer {self.token}")
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        presented_query = (query.get("access_token") or [""])[0]
        return self.matches(presented_query, self.token)

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("path") == "/health":
            await self.app(scope, receive, send)
            return
        if not self.authorized(scope):
            body = b'{"error":"unauthorized"}'
            await send({"type": "http.response.start", "status": 401,
                        "headers": [(b"content-type", b"application/json"),
                                    (b"content-length", str(len(body))
                                     .encode())]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)


TRUST_ANY_HOST = "*"
DEFAULT_ORIGINS = ["http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*"]


class TrustedHostMiddleware(TransportSecurityMiddleware):
    def _validate_host(self, host: str | None) -> bool:
        return bool(host)


def trusted_security() -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[TRUST_ANY_HOST],
        allowed_origins=list(DEFAULT_ORIGINS),
    )


def build_http_app(server, token: str):
    async def health_check(request):
        return JSONResponse({"ok": True, "version": VERSION,
                             "vec": "vec" if db.HAS_VEC else "bruteforce"})

    session_manager.StreamableHTTPServerTransport = NegotiatingTransport
    streamable_http.TransportSecurityMiddleware = TrustedHostMiddleware
    server.settings.transport_security = trusted_security()
    inner = server.streamable_http_app()
    mcp_app = AcceptNegotiation(inner)

    @asynccontextmanager
    async def lifespan(app):
        async with inner.router.lifespan_context(inner):
            yield

    app = Starlette(lifespan=lifespan,
                    routes=[Route("/health", health_check, methods=["GET"]),
                            Mount("/", app=mcp_app)])
    return BearerAuth(app, token)
