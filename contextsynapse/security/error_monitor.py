"""Error Monitoring — captures unhandled exceptions for alerting.

Supports: Sentry (if DSN configured), file-based logging (always), PostgreSQL (if available).
Platform capability — verticals don't need to handle this.

Usage:
    from contextsynapse.security.error_monitor import init_error_monitoring, capture_exception
    init_error_monitoring()  # call once on startup
    capture_exception(e, context={"user_id": "...", "action": "..."})
"""
from __future__ import annotations

import logging
import os
import traceback
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

_sentry_initialized = False


def init_error_monitoring():
    """Initialize error monitoring. Call once on server startup."""
    global _sentry_initialized
    sentry_dsn = os.environ.get("SENTRY_DSN", "")
    if sentry_dsn:
        try:
            import sentry_sdk
            sentry_sdk.init(dsn=sentry_dsn, traces_sample_rate=0.1)
            _sentry_initialized = True
            logger.info("[ERROR-MONITOR] Sentry initialized")
        except ImportError:
            logger.info("[ERROR-MONITOR] sentry_sdk not installed — file logging only")
    else:
        logger.info("[ERROR-MONITOR] No SENTRY_DSN — file logging only")


def capture_exception(
    exception: Exception,
    context: dict = None,
    level: str = "error",
):
    """Capture an exception for monitoring."""
    # Always log to file
    tb = traceback.format_exception(type(exception), exception, exception.__traceback__)
    logger.error("[ERROR] %s: %s\nContext: %s\n%s",
                  type(exception).__name__, exception,
                  context or {}, "".join(tb[-3:]))

    # Send to Sentry if available
    if _sentry_initialized:
        try:
            import sentry_sdk
            with sentry_sdk.push_scope() as scope:
                for k, v in (context or {}).items():
                    scope.set_extra(k, v)
                sentry_sdk.capture_exception(exception)
        except Exception:
            pass

    # Log to PostgreSQL error_log (best effort)
    try:
        from contextsynapse.db.postgres import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO audit_actions (user_id, method, path, status_code, user_agent, duration_ms)
                VALUES (%s, 'ERROR', %s, 500, %s, 0)
            """, (
                (context or {}).get("user_id", ""),
                f"{type(exception).__name__}: {str(exception)[:200]}",
                str(context or {})[:200],
            ))
    except Exception:
        pass
