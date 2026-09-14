"""CSRF Protection — double-submit cookie pattern.

For state-changing requests (POST/PUT/PATCH/DELETE):
  1. Server sets a CSRF cookie on login
  2. Client sends cookie value in X-CSRF-Token header
  3. Server verifies header matches cookie

Safe methods (GET, HEAD, OPTIONS) are exempt.

Usage:
    from contextsynapse.security.csrf import CSRFMiddleware
    app.add_middleware(CSRFMiddleware)
"""
from __future__ import annotations

import logging
import os
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

CSRF_COOKIE = "contextsynapse_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}
EXEMPT_PATHS = {"/auth/login", "/auth/signup", "/health", "/ready"}
ENABLED = os.environ.get("CONTEXTSYNAPSE_CSRF_ENABLED", "false").lower() == "true"


class CSRFMiddleware(BaseHTTPMiddleware):
    """CSRF protection via double-submit cookie."""

    async def dispatch(self, request: Request, call_next):
        if not ENABLED:
            return await call_next(request)

        # Safe methods — exempt
        if request.method in SAFE_METHODS:
            response = await call_next(request)
            # Set CSRF cookie if not present
            if CSRF_COOKIE not in request.cookies:
                token = secrets.token_urlsafe(32)
                response.set_cookie(CSRF_COOKIE, token, httponly=False, samesite="lax")
            return response

        # Exempt paths
        if request.url.path in EXEMPT_PATHS:
            return await call_next(request)

        # State-changing: verify CSRF token
        cookie_token = request.cookies.get(CSRF_COOKIE, "")
        header_token = request.headers.get(CSRF_HEADER, "")

        if not cookie_token or not header_token or cookie_token != header_token:
            return JSONResponse(
                status_code=403,
                content={"detail": "CSRF token missing or mismatch"},
            )

        return await call_next(request)
