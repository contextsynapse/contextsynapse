"""
Rate Limiting
=============
Per-tenant rate limiting using slowapi.  Reads ``rate_limit_rpm`` from the
tenant's config dict (default: 60 req/min).

Usage::

    from .rate_limit import install_rate_limiter

    install_rate_limiter(app)
"""

import logging
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

logger = logging.getLogger(__name__)

# Default rates (requests per minute)
_DEFAULT_RPM = int(os.environ.get("CONTEXTSYNAPSE_RATE_LIMIT_RPM") or os.environ.get("AICONTEXTDB_RATE_LIMIT_RPM", "300"))
_PRO_RPM = 600


def _key_func(request: Request) -> str:
    """Rate-limit key: tenant_id if authenticated, else client IP."""
    tenant = getattr(request.state, "tenant", None)
    if tenant:
        return f"tenant:{tenant.tenant_id}"
    user = getattr(request.state, "user", None)
    if user:
        return f"user:{user.user_id}"
    return f"ip:{request.client.host if request.client else 'unknown'}"


def install_rate_limiter(app: FastAPI):
    """Install rate limiting middleware on the FastAPI app."""
    try:
        from slowapi import Limiter, _rate_limit_exceeded_handler
        from slowapi.errors import RateLimitExceeded
    except ImportError:
        logger.warning("slowapi not installed — rate limiting is disabled. Run: pip install slowapi")
        return

    limiter = Limiter(key_func=_key_func, default_limits=[f"{_DEFAULT_RPM}/minute"])
    app.state.limiter = limiter

    @app.exception_handler(RateLimitExceeded)
    async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
        return JSONResponse(
            status_code=429,
            content={
                "error_code": "RATE_LIMITED",
                "message": f"Rate limit exceeded. Try again later.",
                "detail": str(exc.detail) if hasattr(exc, "detail") else None,
            },
        )

    # Apply limiter as middleware
    from slowapi.middleware import SlowAPIMiddleware
    app.add_middleware(SlowAPIMiddleware)
    logger.info(f"Rate limiting enabled: {_DEFAULT_RPM} req/min default")
