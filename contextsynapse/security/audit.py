"""AuthAudit — logs all authentication and authorization events.

Events logged:
  - Login success/failure
  - Token issued/refreshed/revoked
  - Permission denied (403)
  - Role assigned/revoked
  - Password changed
  - Suspicious activity (multiple failed logins)

All write methods are non-blocking: exceptions are caught silently so
that audit logging never slows down or breaks the request path.
"""
from __future__ import annotations

import json
import logging
import threading
import uuid
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from contextsynapse.core.db import (
    IS_POSTGRES, PH, connect, dict_cursor, run_ddl, row_to_dict,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Fallback DDL for SQLite / dev environments.
# Production PostgreSQL uses contextcore/db/schema.sql (audit_log table).
# ---------------------------------------------------------------------------

_ENSURE_TABLE = """
CREATE TABLE IF NOT EXISTS audit_log (
    id TEXT PRIMARY KEY,
    tenant_id TEXT,
    user_id TEXT,
    action TEXT NOT NULL,
    vertical TEXT,
    resource_type TEXT,
    resource_id TEXT,
    details TEXT DEFAULT '{}',
    ip_address TEXT,
    user_agent TEXT,
    status TEXT DEFAULT 'success',
    created_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_audit_tenant_time ON audit_log(tenant_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_user_time ON audit_log(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_audit_action ON audit_log(action);
"""


class AuthAudit:
    """Append-only auth event audit log.

    Every method silently swallows exceptions so that audit logging
    never degrades the request path. Heavy writes are dispatched to a
    background thread.
    """

    def __init__(self, db_path: Optional[str] = None):
        self._db_path = db_path or "contextsynapse.db"
        if not IS_POSTGRES:
            try:
                conn = connect(self._db_path)
                run_ddl(conn, _ENSURE_TABLE)
            except Exception:
                logger.debug("audit: DDL setup skipped (non-critical)")

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _conn(self):
        return connect(self._db_path)

    def _insert_bg(self, sql: str, params: tuple):
        """Run an INSERT in a background thread so the caller is never blocked."""
        def _do():
            try:
                conn = self._conn()
                cur = dict_cursor(conn)
                cur.execute(sql, params)
                conn.commit()
            except Exception as exc:
                logger.debug("audit: background insert failed: %s", exc)

        thread = threading.Thread(target=_do, daemon=True)
        thread.start()

    def _fetchall(self, sql: str, params: tuple = ()) -> List[dict]:
        conn = self._conn()
        cur = dict_cursor(conn)
        cur.execute(sql, params)
        return [row_to_dict(r) for r in cur.fetchall() if r is not None]

    # ------------------------------------------------------------------
    # Core logging
    # ------------------------------------------------------------------

    def log_event(
        self,
        tenant_id: str,
        user_id: str,
        action: str,
        vertical: Optional[str] = None,
        resource_type: Optional[str] = None,
        resource_id: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        status: str = "success",
    ) -> None:
        """Insert an audit event (non-blocking)."""
        try:
            event_id = str(uuid.uuid4())
            now = datetime.now(timezone.utc).isoformat()
            details_str = json.dumps(details) if details else "{}"

            self._insert_bg(
                f"INSERT INTO audit_log "
                f"(id, tenant_id, user_id, action, vertical, resource_type, "
                f"resource_id, details, ip_address, user_agent, status, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
                (event_id, tenant_id, user_id, action, vertical,
                 resource_type, resource_id, details_str, ip, user_agent,
                 status, now),
            )
        except Exception as exc:
            logger.debug("audit: log_event failed: %s", exc)

    # ------------------------------------------------------------------
    # Convenience methods
    # ------------------------------------------------------------------

    def log_login(
        self,
        tenant_id: str,
        user_id: str,
        ip: Optional[str] = None,
        user_agent: Optional[str] = None,
        success: bool = True,
    ) -> None:
        """Log a login attempt."""
        self.log_event(
            tenant_id=tenant_id,
            user_id=user_id,
            action="login",
            ip=ip,
            user_agent=user_agent,
            status="success" if success else "failure",
        )

    def log_permission_denied(
        self,
        tenant_id: str,
        user_id: str,
        vertical: str,
        resource_type: str,
        resource_id: str,
        required_role: str,
    ) -> None:
        """Log a 403 permission-denied event."""
        self.log_event(
            tenant_id=tenant_id,
            user_id=user_id,
            action="permission_denied",
            vertical=vertical,
            resource_type=resource_type,
            resource_id=resource_id,
            details={"required_role": required_role},
            status="denied",
        )

    def log_role_change(
        self,
        tenant_id: str,
        user_id: str,
        vertical: str,
        role: str,
        action: str,
        granted_by: Optional[str] = None,
    ) -> None:
        """Log a role assignment or revocation.

        *action* should be ``"assigned"`` or ``"revoked"``.
        """
        self.log_event(
            tenant_id=tenant_id,
            user_id=user_id,
            action=f"role_{action}",
            vertical=vertical,
            details={"role": role, "granted_by": granted_by},
        )

    def log_token_event(
        self,
        tenant_id: str,
        user_id: str,
        event_type: str,
        ip: Optional[str] = None,
    ) -> None:
        """Log a token lifecycle event.

        *event_type*: ``"issued"``, ``"refreshed"``, or ``"revoked"``.
        """
        self.log_event(
            tenant_id=tenant_id,
            user_id=user_id,
            action=f"token_{event_type}",
            ip=ip,
        )

    # ------------------------------------------------------------------
    # Query / reporting
    # ------------------------------------------------------------------

    def get_audit_log(
        self,
        tenant_id: str,
        user_id: Optional[str] = None,
        action: Optional[str] = None,
        days: int = 30,
        limit: int = 100,
    ) -> List[dict]:
        """Query the audit log with optional filters.

        Returns most-recent-first, capped at *limit* rows.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        sql = f"SELECT * FROM audit_log WHERE tenant_id = {PH} AND created_at >= {PH}"
        params: list = [tenant_id, cutoff]

        if user_id is not None:
            sql += f" AND user_id = {PH}"
            params.append(user_id)
        if action is not None:
            sql += f" AND action = {PH}"
            params.append(action)

        sql += f" ORDER BY created_at DESC LIMIT {PH}"
        params.append(limit)

        try:
            rows = self._fetchall(sql, tuple(params))
            # Parse details JSON string back to dict
            for row in rows:
                det = row.get("details")
                if isinstance(det, str):
                    try:
                        row["details"] = json.loads(det)
                    except (ValueError, TypeError):
                        pass
            return rows
        except Exception as exc:
            logger.warning("audit: get_audit_log failed: %s", exc)
            return []

    def get_suspicious_activity(
        self,
        tenant_id: str,
        hours: int = 24,
        threshold: int = 5,
    ) -> List[dict]:
        """Return users with more than *threshold* failed logins in the given period.

        Each entry: ``{"user_id": ..., "failed_count": ..., "last_attempt": ...}``
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()

        sql = (
            f"SELECT user_id, COUNT(*) as failed_count, MAX(created_at) as last_attempt "
            f"FROM audit_log "
            f"WHERE tenant_id = {PH} AND action = 'login' AND status = 'failure' "
            f"AND created_at >= {PH} "
            f"GROUP BY user_id "
            f"HAVING COUNT(*) > {PH} "
            f"ORDER BY failed_count DESC"
        )

        try:
            rows = self._fetchall(sql, (tenant_id, cutoff, threshold))
            return [
                {
                    "user_id": r["user_id"],
                    "failed_count": r["failed_count"],
                    "last_attempt": r.get("last_attempt"),
                }
                for r in rows
            ]
        except Exception as exc:
            logger.warning("audit: get_suspicious_activity failed: %s", exc)
            return []
