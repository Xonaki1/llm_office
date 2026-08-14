"""HTTP middleware: request ids, access logs, body-size cap, security headers."""

from __future__ import annotations

import time
import uuid

import structlog
from fastapi import Request, Response, status
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.types import ASGIApp

from core.config import get_settings
from core.logging import bind_request_context, clear_request_context

log = structlog.get_logger("api.access")


class RequestContextMiddleware(BaseHTTPMiddleware):
    """Assign a request id, bind it to the logging context, echo it back.

    Every log line and error response for one request carries the same id, which
    is what makes a production incident traceable from a user's screenshot.
    """

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        request_id = request.headers.get("x-request-id") or uuid.uuid4().hex
        request.state.request_id = request_id
        bind_request_context(request_id=request_id, path=request.url.path)

        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            duration_ms = int((time.perf_counter() - started) * 1000)
            log.exception(
                "request.failed",
                method=request.method,
                path=request.url.path,
                duration_ms=duration_ms,
            )
            clear_request_context()
            raise

        duration_ms = int((time.perf_counter() - started) * 1000)
        response.headers["x-request-id"] = request_id

        # Health and metrics are polled constantly; logging them buries real traffic.
        if request.url.path not in ("/health", "/health/ready", "/metrics"):
            log.info(
                "request",
                method=request.method,
                path=request.url.path,
                status=response.status_code,
                duration_ms=duration_ms,
            )
        clear_request_context()
        return response


class BodySizeLimitMiddleware(BaseHTTPMiddleware):
    """Reject oversized bodies before they are buffered into memory."""

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        super().__init__(app)
        self._max_bytes = max_bytes

    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        declared = request.headers.get("content-length")
        if declared is not None:
            try:
                if int(declared) > self._max_bytes:
                    return JSONResponse(
                        status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        content={"detail": "request body too large"},
                    )
            except ValueError:
                return JSONResponse(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    content={"detail": "invalid content-length"},
                )
        return await call_next(request)


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault(
            "Permissions-Policy", "geolocation=(), microphone=(), camera=()"
        )
        if get_settings().is_production:
            response.headers.setdefault(
                "Strict-Transport-Security", "max-age=31536000; includeSubDomains"
            )
        return response
