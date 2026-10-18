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


def ui_mount() -> Mount:
    directory = ui_dir()
    app = (StaticFiles(directory=str(directory), html=True)
           if directory else _NotBuilt())
    return Mount("/ui", app=app)


async def _root(request):
    return RedirectResponse("/ui/", status_code=307)


def ui_routes() -> list:
    """``/ui`` itself is a bare redirect: a Mount only matches ``/ui/...``."""
    return [Route("/ui", _root), ui_mount()]
