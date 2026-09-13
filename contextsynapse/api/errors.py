"""
Standardized Error Responses
=============================
Provides a consistent error envelope for all API responses and a global
exception handler that catches unhandled errors.

Response format::

    {
        "error_code": "DUPLICATE_NODE",
        "message": "A node with name 'Alice' already exists",
        "detail": "...",          # only when AICONTEXTDB_DEBUG=true
        "request_id": "abc123"    # when request-id middleware is active
    }
"""

import logging
import os
import traceback
import uuid
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

logger = logging.getLogger(__name__)

_DEBUG = os.environ.get("CONTEXTSYNAPSE_DEBUG") or os.environ.get("AICONTEXTDB_DEBUG", "false").lower() in ("true", "1", "yes")


# ── Standard error codes ────────────────────────────────────────────────

class ErrorCode:
    # Client errors
    BAD_REQUEST = "BAD_REQUEST"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN = "FORBIDDEN"
    NOT_FOUND = "NOT_FOUND"
    CONFLICT = "CONFLICT"
    DUPLICATE_NODE = "DUPLICATE_NODE"
    DUPLICATE_EDGE = "DUPLICATE_EDGE"
    RATE_LIMITED = "RATE_LIMITED"

    # Server errors
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"
    QUERY_ERROR = "QUERY_ERROR"


class APIErrorResponse(BaseModel):
    error_code: str
    message: str
    detail: Optional[str] = None
    request_id: Optional[str] = None


def _get_request_id(request: Request) -> str:
    """Extract or generate a request ID."""
    return getattr(request.state, "request_id", None) or request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]


# ── Global exception handlers ───────────────────────────────────────────

def install_error_handlers(app: FastAPI):
    """Install global exception handlers on a FastAPI app."""

    @app.middleware("http")
    async def add_request_id(request: Request, call_next):
        request.state.request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:12]
        # Bind request context for structured logging
        try:
            import structlog
            structlog.contextvars.clear_contextvars()
            structlog.contextvars.bind_contextvars(request_id=request.state.request_id)
        except ImportError:
            pass
        # Set provenance write context for this request
        from contextsynapse.core.write_context import set_write_context, clear_write_context
        set_write_context(agent_id="", origin="api", verified=True)
        try:
            response = await call_next(request)
        finally:
            clear_write_context()
        response.headers["X-Request-ID"] = request.state.request_id
        return response

    def _handle_http_exc(request: Request, exc):
        """Wrap HTTPExceptions in standard envelope."""
        request_id = _get_request_id(request)
        code_map = {
            400: ErrorCode.BAD_REQUEST,
            401: ErrorCode.UNAUTHORIZED,
            403: ErrorCode.FORBIDDEN,
            404: ErrorCode.NOT_FOUND,
            409: ErrorCode.CONFLICT,
            429: ErrorCode.RATE_LIMITED,
            503: ErrorCode.SERVICE_UNAVAILABLE,
        }
        error_code = code_map.get(exc.status_code, ErrorCode.INTERNAL_ERROR)
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error_code": error_code,
                "message": exc.detail if isinstance(exc.detail, str) else str(exc.detail),
                "request_id": request_id,
            },
        )

    @app.exception_handler(StarletteHTTPException)
    async def starlette_http_handler(request: Request, exc: StarletteHTTPException):
        return _handle_http_exc(request, exc)

    @app.exception_handler(HTTPException)
    async def fastapi_http_handler(request: Request, exc: HTTPException):
        return _handle_http_exc(request, exc)

    # Import custom exceptions lazily to avoid circular imports
    from .api import DuplicateNodeError, DuplicateEdgeError

    @app.exception_handler(DuplicateNodeError)
    async def duplicate_node_handler(request: Request, exc: DuplicateNodeError):
        request_id = _get_request_id(request)
        return JSONResponse(
            status_code=409,
            content={
                "error_code": ErrorCode.DUPLICATE_NODE,
                "message": exc.message,
                "request_id": request_id,
            },
        )

    @app.exception_handler(DuplicateEdgeError)
    async def duplicate_edge_handler(request: Request, exc: DuplicateEdgeError):
        request_id = _get_request_id(request)
        return JSONResponse(
            status_code=409,
            content={
                "error_code": ErrorCode.DUPLICATE_EDGE,
                "message": exc.message,
                "request_id": request_id,
            },
        )

    # QueueFullError -> 503 Service Unavailable
    try:
        from ..jobs.queue import QueueFullError

        @app.exception_handler(QueueFullError)
        async def queue_full_handler(request: Request, exc: QueueFullError):
            request_id = _get_request_id(request)
            return JSONResponse(
                status_code=503,
                content={
                    "error_code": ErrorCode.SERVICE_UNAVAILABLE,
                    "message": str(exc),
                    "request_id": request_id,
                },
            )
    except ImportError:
        pass

    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception):
        """Catch-all for unhandled exceptions — log full trace, return sanitized response."""
        request_id = _get_request_id(request)
        logger.error(
            "Unhandled exception [request_id=%s] %s: %s\n%s",
            request_id,
            type(exc).__name__,
            exc,
            traceback.format_exc(),
        )
        body = {
            "error_code": ErrorCode.INTERNAL_ERROR,
            "message": "An internal error occurred",
            "request_id": request_id,
        }
        if _DEBUG:
            body["detail"] = traceback.format_exc()
        return JSONResponse(status_code=500, content=body)
