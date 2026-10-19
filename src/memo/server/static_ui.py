"""Static mount for the built dashboard (contract §3).

Resolution order: ``MEMO_UI_DIR`` env, then ``<memo package>/static/ui``, then
``<cwd>/src/memo/static/ui``. When none holds an ``index.html`` the mount
answers 404 with a plain-text hint instead of a stack trace.
"""

from __future__ import annotations

import os
from pathlib import Path

from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

NOT_BUILT = "dashboard not built - run 'make ui'"


def ui_dir() -> Path | None:
    candidates = []
    configured = os.getenv("MEMO_UI_DIR")
    if configured:
        candidates.append(Path(configured))
    package = Path(__file__).resolve().parent.parent
    candidates.append(package / "static" / "ui")
    candidates.append(Path.cwd() / "src" / "memo" / "static" / "ui")
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


class _NotBuilt:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        body = NOT_BUILT.encode()
        await send({"type": "http.response.start", "status": 404,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class _UiCache:
    """Cache policy for the built dashboard.

    Starlette's StaticFiles sets no cache headers at all, so a browser happily
    reuses a cached ``index.html`` and keeps running the previous bundle after
    a rebuild - which reads as "the ui is not updated". HTML revalidates on
    every load; content-hashed assets are immutable.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        html = scope.get("path", "").endswith(("/ui/", ".html"))

        async def send_wrapper(message):
            if message["type"] == "http.response.start" and message.get("status") == 200:
                header = b"no-cache" if html else b"public, max-age=31536000, immutable"
                message = {**message, "headers": [
                    *message.get("headers", []), (b"cache-control", header)]}
            await send(message)

        await self.app(scope, receive, send_wrapper)


def ui_mount() -> Mount:
    directory = ui_dir()
    app = (StaticFiles(directory=str(directory), html=True)
           if directory else _NotBuilt())
    return Mount("/ui", app=_UiCache(app))


async def _root(request):
    return RedirectResponse("/ui/", status_code=307)


def ui_routes() -> list:
    """``/ui`` itself is a bare redirect: a Mount only matches ``/ui/...``."""
    return [Route("/ui", _root), ui_mount()]
