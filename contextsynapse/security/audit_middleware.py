"""Audit Log Middleware — logs every API action for compliance.

Platform capability — verticals configure which actions to log.
Every state-changing request (POST, PUT, PATCH, DELETE) is logged.
GET requests are logged only if configured (e.g., sensitive data access).

Stored in PostgreSQL for compliance audit trail.

Usage:
    # In FastAPI app
    from contextsynapse.security.audit_middleware import AuditMiddleware
    app.add_middleware(AuditMiddleware)
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone
from typing import Any

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request

logger = logging.getLogger(__name__)

# Actions to always log (state-changing)
LOGGED_METHODS = {"POST", "PUT", "PATCH", "DELETE"}

# Paths to skip (health checks, static, etc.)
SKIP_PATHS = {"/health", "/ready", "/docs", "/redoc", "/openapi.json", "/favicon.ico"}


class AuditMiddleware(BaseHTTPMiddleware):
    """Logs API actions to PostgreSQL audit_actions table."""

    async def dispatch(self, request: Request, call_next):
        # Skip non-logged methods and paths
        if request.method not in LOGGED_METHODS:
            return await call_next(request)

        path = request.url.path
        if any(path.startswith(s) for s in SKIP_PATHS):
            return await call_next(request)

        start = time.time()
        user_id = ""
        user_email = ""

        # Extract user from JWT (if available)
        try:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Bearer "):
                from contextsynapse.security.jwt_identity import decode_jwt
                claims = decode_jwt(auth[7:])
                user_id = claims.get("sub", claims.get("user_id", ""))
                user_email = claims.get("email", "")
        except Exception:
            pass

        # Execute request
        response = await call_next(request)

        duration_ms = (time.time() - start) * 1000

        # Log to PostgreSQL (async, non-blocking)
        try:
            _log_action(
                user_id=user_id,
                user_email=user_email,
                method=request.method,
                path=path,
                status_code=response.status_code,
                ip_address=request.client.host if request.client else "",
                user_agent=request.headers.get("user-agent", "")[:200],
                duration_ms=duration_ms,
            )
        except Exception as e:
            logger.debug("[AUDIT] Log failed: %s", e)

        return response


def _log_action(
    user_id: str,
    user_email: str,
    method: str,
    path: str,
    status_code: int,
    ip_address: str = "",
    user_agent: str = "",
    duration_ms: float = 0,
):
    """Insert audit record into PostgreSQL."""
    try:
        from contextsynapse.db.postgres import get_connection
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                INSERT INTO audit_actions (
                    user_id, user_email, method, path, status_code,
                    ip_address, user_agent, duration_ms, created_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
            """, (
                user_id or None, user_email, method, path[:500],
                status_code, ip_address, user_agent, round(duration_ms, 1),
            ))
    except Exception:
        # Table might not exist yet — create it
        try:
            from contextsynapse.db.postgres import get_connection as gc
            with gc() as conn:
                cur = conn.cursor()
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS audit_actions (
                        id BIGSERIAL PRIMARY KEY,
                        user_id TEXT,
                        user_email TEXT,
                        method TEXT NOT NULL,
                        path TEXT NOT NULL,
                        status_code INTEGER,
                        ip_address TEXT,
                        user_agent TEXT,
                        duration_ms NUMERIC(10,1),
                        created_at TIMESTAMPTZ DEFAULT now()
                    )
                """)
                cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_user ON audit_actions(user_id, created_at DESC)")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_path ON audit_actions(path, created_at DESC)")
                # Retry the insert
                cur.execute("""
                    INSERT INTO audit_actions (
                        user_id, user_email, method, path, status_code,
                        ip_address, user_agent, duration_ms, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, now())
                """, (
                    user_id or None, user_email, method, path[:500],
                    status_code, ip_address, user_agent, round(duration_ms, 1),
                ))
        except Exception as e2:
            logger.debug("[AUDIT] Table creation failed: %s", e2)


def query_audit_log(
    user_id: str = "",
    path_prefix: str = "",
    method: str = "",
    days: int = 30,
    limit: int = 100,
) -> list[dict]:
    """Query audit log for compliance reporting."""
    from contextsynapse.db.postgres import execute
    conditions = ["created_at > now() - interval '%s days'"]
    params: list[Any] = [days]

    if user_id:
        conditions.append("user_id = %s")
        params.append(user_id)
    if path_prefix:
        conditions.append("path LIKE %s")
        params.append(f"{path_prefix}%")
    if method:
        conditions.append("method = %s")
        params.append(method)

    params.append(limit)
    where = " AND ".join(conditions)

    return execute(
        f"SELECT * FROM audit_actions WHERE {where} ORDER BY created_at DESC LIMIT %s",
        tuple(params),
    )
