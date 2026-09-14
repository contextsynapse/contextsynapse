"""Per-User Rate Limiting — platform middleware.

Limits API calls per user (identified by JWT sub claim).
Falls back to IP-based limiting for unauthenticated requests.

Usage:
    from contextsynapse.security.rate_limit import UserRateLimitMiddleware
    app.add_middleware(UserRateLimitMiddleware, rpm=60)
"""
from __future__ import annotations

import logging
import os
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)

DEFAULT_RPM = int(os.environ.get("CONTEXTSYNAPSE_USER_RPM", 60))


class UserRateLimitMiddleware(BaseHTTPMiddleware):
    """Per-user rate limiter (sliding window)."""

    def __init__(self, app, rpm: int = DEFAULT_RPM):
        super().__init__(app)
        self.rpm = rpm
        self.window = 60  # seconds
        self._requests: dict[str, list[float]] = defaultdict(list)

    async def dispatch(self, request: Request, call_next):
        # Identify user
        key = self._get_key(request)

        # Clean old entries
        now = time.time()
        cutoff = now - self.window
        self._requests[key] = [t for t in self._requests[key] if t > cutoff]

        # Check limit
        if len(self._requests[key]) >= self.rpm:
            return JSONResponse(
                status_code=429,
                content={"detail": f"Rate limit exceeded ({self.rpm} req/min)"},
                headers={"Retry-After": "60"},
            )

        self._requests[key].append(now)
        return await call_next(request)

    def _get_key(self, request: Request) -> str:
        """Extract user ID from JWT or fall back to IP."""
        try:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                from contextsynapse.security.jwt_refresh import verify_token
                claims = verify_token(auth[7:])
                if claims and claims.get("sub"):
                    return f"user:{claims['sub']}"
        except Exception:
            pass
        return f"ip:{request.client.host if request.client else 'unknown'}"
