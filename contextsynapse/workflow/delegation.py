"""Delegation — temporarily transfer client access between FMs.

CIO delegates FM A's clients to FM B when A is on leave.
Auto-expires after N days. Audit-logged.

Usage:
    from contextsynapse.workflow.delegation import DelegationManager

    mgr = DelegationManager()
    mgr.delegate(from_user="neha_id", to_user="vikram_id",
                 client_ids=["cl_1", "cl_2"], reason="annual leave",
                 expires_days=7, delegated_by="cio_id")
    mgr.revoke(delegation_id, revoked_by="cio_id")
    active = mgr.get_active_for_user("vikram_id")
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from contextsynapse.db.postgres import execute, get_connection

logger = logging.getLogger(__name__)


class DelegationManager:
    def delegate(
        self,
        from_user: str,
        to_user: str,
        client_ids: list[str],
        reason: str = "",
        expires_days: int = 7,
        delegated_by: str = "",
        tenant_id: str = None,
    ) -> dict[str, Any]:
        """Create a delegation — transfer client access temporarily."""
        if from_user == to_user:
            return {"error": "Cannot delegate to yourself"}
        if not client_ids:
            return {"error": "No client IDs specified"}

        delegation_id = str(uuid.uuid4())
        expires_at = (datetime.now(timezone.utc) + timedelta(days=expires_days)).isoformat()

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO delegations (
                    id, tenant_id, from_user_id, to_user_id,
                    client_ids, reason, status, delegated_by, expires_at
                ) VALUES (%s, %s, %s, %s, %s, %s, 'active', %s, %s)
                RETURNING *
            """,
                (
                    delegation_id,
                    tenant_id,
                    from_user,
                    to_user,
                    client_ids,
                    reason,
                    delegated_by or from_user,
                    expires_at,
                ),
            )
            row = cur.fetchone()
            result = self._row_to_dict(cur, row)

        logger.info(
            "[DELEGATION] %s → %s: %d clients for %d days (by %s)",
            from_user[:8],
            to_user[:8],
            len(client_ids),
            expires_days,
            (delegated_by or from_user)[:8],
        )

        # Create workflow task for audit
        from .engine import WorkflowEngine

        WorkflowEngine().initiate(
            workflow_type="delegation",
            initiated_by=delegated_by or from_user,
            title=f"Access delegation: {len(client_ids)} clients for {expires_days} days",
            entity_type="delegation",
            entity_id=delegation_id,
            payload={
                "from_user": from_user,
                "to_user": to_user,
                "client_ids": client_ids,
                "reason": reason,
                "expires_days": expires_days,
            },
        )

        return result

    def revoke(self, delegation_id: str, revoked_by: str) -> dict[str, Any]:
        """Revoke a delegation immediately."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE delegations
                SET status = 'revoked', revoked_at = now()
                WHERE id = %s AND status = 'active'
                RETURNING *
            """,
                (delegation_id,),
            )
            row = cur.fetchone()
            if not row:
                return {"error": "Delegation not found or already revoked"}
            result = self._row_to_dict(cur, row)

        logger.info("[DELEGATION] Revoked: %s by %s", delegation_id[:8], revoked_by[:8])
        return result

    def get_active_for_user(self, user_id: str) -> list[dict[str, Any]]:
        """Get all active delegations TO a user (their extra client access)."""
        rows = execute(
            """
            SELECT * FROM delegations
            WHERE to_user_id = %s AND status = 'active'
              AND (expires_at IS NULL OR expires_at > now())
            ORDER BY created_at DESC
        """,
            (user_id,),
        )
        return [self._hydrate(r) for r in rows]

    def get_delegated_client_ids(self, user_id: str) -> list[str]:
        """Get all client IDs delegated to a user (for scope expansion)."""
        delegations = self.get_active_for_user(user_id)
        client_ids = []
        for d in delegations:
            ids = d.get("client_ids", [])
            if isinstance(ids, list):
                client_ids.extend(ids)
        return list(set(client_ids))

    def get_delegations_from_user(self, user_id: str) -> list[dict[str, Any]]:
        """Get all delegations FROM a user (their clients being managed by someone else)."""
        rows = execute(
            """
            SELECT * FROM delegations
            WHERE from_user_id = %s AND status = 'active'
              AND (expires_at IS NULL OR expires_at > now())
            ORDER BY created_at DESC
        """,
            (user_id,),
        )
        return [self._hydrate(r) for r in rows]

    def list_all(self, status: str = "active") -> list[dict[str, Any]]:
        """List all delegations."""
        rows = execute(
            "SELECT * FROM delegations WHERE status = %s ORDER BY created_at DESC",
            (status,),
        )
        return [self._hydrate(r) for r in rows]

    def expire_stale(self) -> int:
        """Expire delegations past their expiry date. Call periodically."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE delegations
                SET status = 'expired'
                WHERE status = 'active' AND expires_at IS NOT NULL AND expires_at < now()
            """)
            count = cur.rowcount
        if count > 0:
            logger.info("[DELEGATION] Expired %d delegations", count)
        return count

    def _row_to_dict(self, cursor, row):
        if not row:
            return {}
        cols = [desc[0] for desc in cursor.description]
        return self._hydrate(dict(zip(cols, row)))

    def _hydrate(self, d):
        if not d:
            return d
        for field in ("id", "tenant_id", "from_user_id", "to_user_id", "delegated_by"):
            if d.get(field):
                d[field] = str(d[field])
        return d
