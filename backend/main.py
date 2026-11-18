"""FastAPI application factory."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from starlette.exceptions import HTTPException as StarletteHTTPException
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from backend.api.routes import token_authorized, router
from backend.config import ROOT, SETTINGS, ensure_dirs
from backend.jobs.queue import QUEUE
from backend.jobs.state import init_db

DIST = ROOT / "frontend/dist"


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    init_db()
    QUEUE.start()
    yield
    QUEUE.shutdown()


def create_app() -> FastAPI:
    app = FastAPI(
        title="Hermit Trading Frontend",
        version="0.1.0",
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        detail = exc.detail
        if isinstance(detail, dict) and {"code", "message"} <= set(detail):
            return JSONResponse(status_code=exc.status_code, content=detail)
        return JSONResponse(
            status_code=exc.status_code,
            content={"code": "http_error", "message": str(detail), "details": None},
        )

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        return JSONResponse(
            status_code=500,
            content={"code": "internal_error", "message": "internal server error", "details": str(exc)},
        )

    app.include_router(router)

    @app.middleware("http")
    async def enforce_app_token(request: Request, call_next):
        # Router dependencies cover every declared route; this also protects
        # framework routes under /api (docs, OpenAPI) and unknown API paths.
        if request.url.path.startswith("/api/") and not token_authorized(
            request.headers.get("authorization")
        ):
            return JSONResponse(
                status_code=401,
                content={
                    "code": "unauthorized",
                    "message": "missing or invalid bearer token",
                    "details": {"scheme": "Bearer"},
                },
            )
        return await call_next(request)

    if DIST.exists():
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        async def spa(full_path: str):
            if full_path.startswith("api/"):
                return JSONResponse(status_code=404, content={"code": "not_found", "message": "route not found", "details": None})
            candidate = DIST / full_path
            if candidate.is_file():
                return FileResponse(candidate)
            index = DIST / "index.html"
            if index.exists():
                return FileResponse(index)
            return JSONResponse(status_code=404, content={"code": "not_found", "message": "frontend not built", "details": None})

    return app


app = create_app()


def main() -> None:
    import uvicorn

    uvicorn.run(app, host=SETTINGS.host, port=SETTINGS.port, log_level="info")
