from __future__ import annotations

from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, PlainTextResponse
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest
from sqlalchemy import text
from starlette.middleware.trustedhost import TrustedHostMiddleware

from api.deps import close_queue
from api.middleware import (
    BodySizeLimitMiddleware,
    RequestContextMiddleware,
    SecurityHeadersMiddleware,
)
from api.routers import admin, agents, auth, keys, models, orgs, runs, workflows
from api.schemas import HealthOut
from core.config import get_settings
from core.crypto import CryptoError
from core.db import dispose_engine, engine
from core.events import close_redis, get_redis
from core.llm.registry import UnknownModelError
from core.logging import configure_logging
from core.orchestration.presets import WorkflowConfigError

VERSION = "1.0.0"

configure_logging()
log = structlog.get_logger("api")
settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("api.starting", env=settings.env, version=VERSION)
    # Schema is owned by Alembic (`alembic upgrade head` runs in the entrypoint);
    # the app never creates tables, so a running service cannot silently diverge
    # from the migration history.
    yield
    await close_queue()
    await close_redis()
    await dispose_engine()
    log.info("api.stopped")


app = FastAPI(
    title="Agents Office API",
    version=VERSION,
    lifespan=lifespan,
    docs_url=None if settings.is_production else "/docs",
    redoc_url=None,
    openapi_url=None if settings.is_production else "/openapi.json",
)

app.add_middleware(SecurityHeadersMiddleware)
app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
app.add_middleware(RequestContextMiddleware)
if settings.trusted_hosts and settings.trusted_hosts != ["*"]:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.trusted_hosts)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Idempotency-Key", "Last-Event-ID"],
    expose_headers=["X-Request-Id"],
)

for router in (
    auth.router,
    orgs.router,
    agents.router,
    workflows.router,
    workflows.meta_router,
    runs.router,
    keys.router,
    models.router,
    admin.router,
):
    app.include_router(router)


# --- error handling ------------------------------------------------------


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    location = ".".join(str(part) for part in first.get("loc", ())[1:])
    detail = f"{location}: {first.get('msg', 'invalid request')}" if location else "invalid request"
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": detail, "request_id": _request_id(request)},
    )


@app.exception_handler(WorkflowConfigError)
async def workflow_config_error(request: Request, exc: WorkflowConfigError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": str(exc), "request_id": _request_id(request)},
    )


@app.exception_handler(UnknownModelError)
async def unknown_model_error(request: Request, exc: UnknownModelError) -> JSONResponse:
    return JSONResponse(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        content={"detail": str(exc), "request_id": _request_id(request)},
    )


@app.exception_handler(CryptoError)
async def crypto_error(request: Request, exc: CryptoError) -> JSONResponse:
    # Never surface the underlying message: it describes key material.
    log.error("crypto.failure", error=str(exc))
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={
            "detail": "could not process the stored credential",
            "request_id": _request_id(request),
        },
    )


@app.exception_handler(Exception)
async def unhandled_error(request: Request, exc: Exception) -> JSONResponse:
    log.exception("api.unhandled", path=request.url.path)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "internal server error", "request_id": _request_id(request)},
    )


# --- operational endpoints ----------------------------------------------


@app.get("/health", response_model=HealthOut, tags=["ops"])
async def health() -> HealthOut:
    """Liveness. Deliberately dependency-free so a database blip does not cause
    the orchestrator to restart otherwise-healthy processes."""
    return HealthOut(
        status="ok", env=settings.env, version=VERSION, database="skipped", redis="skipped"
    )


@app.get("/health/ready", response_model=HealthOut, tags=["ops"])
async def readiness() -> JSONResponse:
    """Readiness. Checks the dependencies a request actually needs, so a failing
    instance is pulled out of the load balancer instead of serving errors."""
    database = redis_status = "ok"
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - the reason goes to logs, not the client
        log.warning("health.database_unavailable", error=str(exc))
        database = "unavailable"
    try:
        await get_redis().ping()
    except Exception as exc:  # noqa: BLE001
        log.warning("health.redis_unavailable", error=str(exc))
        redis_status = "unavailable"

    healthy = database == "ok" and redis_status == "ok"
    body = HealthOut(
        status="ok" if healthy else "degraded",
        env=settings.env,
        version=VERSION,
        database=database,
        redis=redis_status,
    )
    return JSONResponse(
        status_code=200 if healthy else status.HTTP_503_SERVICE_UNAVAILABLE,
        content=body.model_dump(),
    )


@app.get("/metrics", include_in_schema=False)
async def metrics() -> PlainTextResponse:
    return PlainTextResponse(generate_latest().decode(), media_type=CONTENT_TYPE_LATEST)
