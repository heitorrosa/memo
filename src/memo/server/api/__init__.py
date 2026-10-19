from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError

from memo.server.shared import internal_error

from . import agents, graph, memories, skills, stats
from .common import error

__all__ = ["create_api_app"]


def create_api_app(server, lifespan=None) -> FastAPI:
    app = FastAPI(title="memo", version="3.0.0", docs_url="/api/docs",
                   openapi_url="/api/openapi.json", redoc_url=None,
                   swagger_ui_oauth2_redirect_url=None, lifespan=lifespan)

    @app.exception_handler(RequestValidationError)
    async def validation_failed(request: Request, exc: RequestValidationError):
        errors = exc.errors()
        roots = {str(err["loc"][0]) for err in errors if err["loc"]}
        status = 400 if roots and roots <= {"query", "path"} else 422
        message = "; ".join(
            f"{'.'.join(str(part) for part in err['loc'])}: {err['msg']}"
            for err in errors)
        return error("invalid_input", message, status)

    @app.exception_handler(Exception)
    async def internal_failed(request: Request, exc: Exception):
        detail = internal_error(exc).get("detail", "internal error")
        return error("internal", detail, 500)

    for module in (stats, memories, skills, agents, graph):
        module.register(app, server)
    return app
