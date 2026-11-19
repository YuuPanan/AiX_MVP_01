from os import environ
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from . import attachments, catalog, fulfillment, identity, messages, operations, procurement, supplier_monitor, token
from .audit import request_context
from .db import DB
from .portal import install_portal


def create_app() -> FastAPI:
    app = FastAPI(title="AIX Marketplace API", version="0.1.0")

    origins = [origin.strip() for origin in environ.get("AIX_CORS_ORIGINS", "").split(",") if origin.strip()]
    if "*" in origins:
        raise ValueError("AIX_CORS_ORIGINS requires explicit frontend origins")
    if origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_methods=["GET", "POST", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "X-Filename"],
            expose_headers=["X-Request-ID"],
        )

    @app.middleware("http")
    async def correlate(request: Request, call_next):
        request_id = str(uuid4())
        context = request_context.set((request_id, request.client.host if request.client else None))
        try:
            response = await call_next(request)
            response.headers["X-Request-ID"] = request_id
            if "authorization" in request.headers or request.method not in {"GET", "HEAD", "OPTIONS"}:
                response.headers["Cache-Control"] = "no-store"
            return response
        finally:
            request_context.reset(context)

    @app.get("/healthz")
    def health(db: DB):
        try:
            db.execute(text("SELECT 1"))
        except OperationalError:
            raise HTTPException(503, "Database unavailable") from None
        return {"status": "ok"}

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request: Request, error: RequestValidationError):
        errors = [
            {"loc": issue["loc"], "type": issue["type"], "msg": issue["msg"]}
            for issue in error.errors()
        ]
        return JSONResponse(status_code=422, content={"detail": errors})

    for module in (identity, catalog, procurement, fulfillment, token, messages, attachments, operations, supplier_monitor):
        app.include_router(module.router, prefix="/api/v1")
    return app


app = create_app()
install_portal(app)
