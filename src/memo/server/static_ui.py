from __future__ import annotations

from pathlib import Path

from starlette.responses import RedirectResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

NOT_BUILT = "dashboard not built - run 'make ui'"


def ui_dir() -> Path | None:
    package = Path(__file__).resolve().parent.parent
    candidates = [package / "static" / "ui",
                  Path.cwd() / "src" / "memo" / "static" / "ui"]
    for candidate in candidates:
        if (candidate / "index.html").is_file():
            return candidate
    return None


class NotBuilt:
    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return
        body = NOT_BUILT.encode()
        await send({"type": "http.response.start", "status": 404,
                    "headers": [
                        (b"content-type", b"text/plain; charset=utf-8"),
                        (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


class UiCache:
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
           if directory else NotBuilt())
    return Mount("/ui", app=UiCache(app))


async def root(request):
    return RedirectResponse("/ui/", status_code=307)


def ui_routes() -> list:
    return [Route("/ui", root), ui_mount()]
