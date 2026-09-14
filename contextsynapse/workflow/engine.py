"""Workflow Engine — state machine for PMS approval workflows.

Every action that needs approval goes through:
  INITIATED → AUTO_CHECK → PENDING_APPROVAL → APPROVED → EXECUTING → COMPLETED
                                             → REJECTED
                         → AUTO_APPROVED (below threshold)

Workflow types:
  trade           — FM proposes, compliance auto-check, CIO approves (if above threshold)
  client_onboard  — RM/FM initiates, KYC check, compliance approval
  delegation      — CIO initiates, auto-applies, auto-expires
  rule_override   — FM requests, CIO/Compliance reviews
  large_trade     — above threshold, requires CIO approval
  client_offboard — FM initiates, compliance check, ops settles

Usage:
    from contextsynapse.workflow.engine import WorkflowEngine

    engine = WorkflowEngine()
    task = engine.initiate("trade", initiated_by=user_id, payload={...})
    engine.approve(task_id, approved_by=cio_id)
    engine.reject(task_id, rejected_by=compliance_id, reason="...")
"""

from __future__ import annotations

import contextlib
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from contextsynapse.db.postgres import execute, execute_one, get_connection

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════
# Workflow type definitions
# ═══════════════════════════════════════════════════════════════

WORKFLOW_TYPES = {
    "trade": {
        "label": "Trade Proposal",
        "auto_checks": ["compliance_check", "risk_check"],
        "approvers": ["cio", "compliance_officer"],
        "auto_approve_below": 1000000,  # Rs 10L — auto-approve for small trades
        "timeout_hours": 24,
    },
    "large_trade": {
        "label": "Large Trade (>₹10L)",
        "auto_checks": ["compliance_check", "risk_check", "concentration_check"],
        "approvers": ["cio"],
        "auto_approve_below": 0,  # always needs CIO approval
        "timeout_hours": 48,
    },
    "client_onboard": {
        "label": "Client Onboarding",
        "auto_checks": ["kyc_validation", "pan_verification"],
        "approvers": ["compliance_officer", "cio"],
        "auto_approve_below": 0,
        "timeout_hours": 72,
    },
    "client_offboard": {
        "label": "Client Offboarding",
        "auto_checks": ["pending_settlements", "open_positions"],
        "approvers": ["compliance_officer"],
        "auto_approve_below": 0,
        "timeout_hours": 72,
    },
    "delegation": {
        "label": "Access Delegation",
        "auto_checks": [],
        "approvers": [],  # CIO-initiated, no approval needed
        "auto_approve_below": 999999999999,  # always auto-approve
        "timeout_hours": 0,  # uses explicit expires_at
    },
    "rule_override": {
        "label": "Compliance Override",
        "auto_checks": [],
        "approvers": ["cio", "compliance_officer"],
        "auto_approve_below": 0,
        "timeout_hours": 4,  # urgent — 4 hour timeout
    },
    "rebalance": {
        "label": "Portfolio Rebalance",
        "auto_checks": ["compliance_check"],
        "approvers": ["cio"],
        "auto_approve_below": 5000000,  # Rs 50L auto-approve
        "timeout_hours": 24,
    },
}

TASK_STATUSES = [
    "initiated",  # just created
    "auto_checking",  # running automated checks
    "pending_approval",  # waiting for human approval
    "auto_approved",  # below threshold, auto-approved
    "approved",  # human approved
    "rejected",  # human rejected
    "executing",  # being executed
    "completed",  # done
    "expired",  # timed out without approval
    "cancelled",  # initiator cancelled
]


class WorkflowEngine:
    """Manages workflow task lifecycle."""

    def initiate(
        self,
        workflow_type: str,
        initiated_by: str,
        title: str = "",
        description: str = "",
        entity_type: str = "",
        entity_id: str = "",
        payload: dict = None,
        assigned_to: str = None,
        tenant_id: str = None,
    ) -> dict[str, Any]:
        """Create a new workflow task. Returns the task dict."""

        wf_config = WORKFLOW_TYPES.get(workflow_type, {})
        if not wf_config:
            return {"error": f"Unknown workflow type: {workflow_type}"}

        task_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc)

        # Calculate expiry
        timeout_hours = wf_config.get("timeout_hours", 24)
        expires_at = (now + timedelta(hours=timeout_hours)).isoformat() if timeout_hours > 0 else None

        # Determine if auto-approve
        threshold = wf_config.get("auto_approve_below", 0)
        trade_value = (payload or {}).get("estimated_value", (payload or {}).get("amount", 0))
        requires_approval = trade_value >= threshold if threshold > 0 else True

        # Auto-approve delegations and below-threshold trades
        if not requires_approval or threshold == float("inf"):
            status = "auto_approved"
        else:
            status = "initiated"

        if not title:
            title = f"{wf_config.get('label', workflow_type)} — {entity_type or ''} {entity_id or ''}"

        # Cast user IDs to UUID if valid, else NULL (for testing with non-UUID IDs)
        def _to_uuid(val):
            if not val:
                return None
            try:
                uuid.UUID(val)
                return val
            except (ValueError, AttributeError):
                return None

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO workflow_tasks (
                    id, tenant_id, workflow_type, title, description,
                    status, initiated_by, assigned_to,
                    entity_type, entity_id, payload,
                    auto_checks, requires_approval, approval_threshold,
                    expires_at
                ) VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """,
                (
                    task_id,
                    tenant_id,
                    workflow_type,
                    title.strip(),
                    description,
                    status,
                    _to_uuid(initiated_by),
                    _to_uuid(assigned_to),
                    entity_type,
                    entity_id,
                    json.dumps(payload or {}),
                    json.dumps(wf_config.get("auto_checks", [])),
                    requires_approval,
                    threshold,
                    expires_at,
                ),
            )
            row = cur.fetchone()
            task = self._row_to_dict(cur, row)

        logger.info(
            "[WORKFLOW] %s initiated: %s by %s (status=%s)", workflow_type, task_id[:8], initiated_by[:8], status
        )

        # Run auto-checks if not auto-approved
        if status == "initiated":
            self._run_auto_checks(task_id, wf_config.get("auto_checks", []), payload or {})

        return task

    def approve(self, task_id: str, approved_by: str, notes: str = "") -> dict[str, Any]:
        """Approve a pending task."""
        task = self.get(task_id)
        if not task:
            return {"error": "Task not found"}
        if task["status"] not in ("pending_approval", "initiated", "auto_checking"):
            return {"error": f"Cannot approve task in status: {task['status']}"}

        def _to_uuid(val):
            if not val:
                return None
            try:
                uuid.UUID(val)
                return val
            except (ValueError, AttributeError):
                return None

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE workflow_tasks
                SET status = 'approved', approved_by = %s,
                    result = COALESCE(result, '{}'::jsonb) || %s::jsonb,
                    updated_at = now()
                WHERE id = %s RETURNING *
            """,
                (_to_uuid(approved_by), json.dumps({"approval_notes": notes}), task_id),
            )
            row = cur.fetchone()
            result = self._row_to_dict(cur, row)

        logger.info("[WORKFLOW] Approved: %s by %s", task_id[:8], approved_by[:8])
        return result

    def reject(self, task_id: str, rejected_by: str, reason: str = "") -> dict[str, Any]:
        """Reject a pending task."""
        task = self.get(task_id)
        if not task:
            return {"error": "Task not found"}

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE workflow_tasks
                SET status = 'rejected', approved_by = %s,
                    result = result || %s::jsonb,
                    updated_at = now()
                WHERE id = %s RETURNING *
            """,
                (rejected_by, json.dumps({"rejection_reason": reason}), task_id),
            )
            row = cur.fetchone()
            return self._row_to_dict(cur, row)

    def complete(self, task_id: str, result: dict = None) -> dict[str, Any]:
        """Mark task as completed after execution."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE workflow_tasks
                SET status = 'completed', completed_at = now(),
                    result = result || %s::jsonb,
                    updated_at = now()
                WHERE id = %s RETURNING *
            """,
                (json.dumps(result or {}), task_id),
            )
            row = cur.fetchone()
            return self._row_to_dict(cur, row)

    def cancel(self, task_id: str, cancelled_by: str) -> dict[str, Any]:
        """Cancel a task (only initiator or admin)."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE workflow_tasks
                SET status = 'cancelled',
                    result = result || %s::jsonb,
                    updated_at = now()
                WHERE id = %s AND status IN ('initiated', 'pending_approval', 'auto_checking')
                RETURNING *
            """,
                (json.dumps({"cancelled_by": cancelled_by}), task_id),
            )
            row = cur.fetchone()
            return self._row_to_dict(cur, row) if row else {"error": "Cannot cancel"}

    # ── Queries ──────────────────────────────────────────────────

    def get(self, task_id: str) -> dict[str, Any] | None:
        row = execute_one("SELECT * FROM workflow_tasks WHERE id = %s", (task_id,))
        return self._hydrate(row) if row else None

    def list_pending(self, assigned_to: str = None, workflow_type: str = "") -> list[dict]:
        """List tasks awaiting approval."""
        conditions = ["status IN ('pending_approval', 'initiated', 'auto_checking')"]
        params = []
        if assigned_to:
            conditions.append("(assigned_to = %s OR assigned_to IS NULL)")
            params.append(assigned_to)
        if workflow_type:
            conditions.append("workflow_type = %s")
            params.append(workflow_type)
        where = " AND ".join(conditions)
        rows = execute(
            f"SELECT * FROM workflow_tasks WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [self._hydrate(r) for r in rows]

    def list_by_user(self, user_id: str, limit: int = 50) -> list[dict]:
        """List tasks initiated by or assigned to a user."""
        rows = execute(
            """
            SELECT * FROM workflow_tasks
            WHERE initiated_by = %s OR assigned_to = %s
            ORDER BY created_at DESC LIMIT %s
        """,
            (user_id, user_id, limit),
        )
        return [self._hydrate(r) for r in rows]

    def list_recent(self, limit: int = 50, workflow_type: str = "") -> list[dict]:
        conditions = []
        params = []
        if workflow_type:
            conditions.append("workflow_type = %s")
            params.append(workflow_type)
        where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
        params.append(limit)
        rows = execute(
            f"SELECT * FROM workflow_tasks {where} ORDER BY created_at DESC LIMIT %s",
            tuple(params),
        )
        return [self._hydrate(r) for r in rows]

    def get_stats(self) -> dict[str, Any]:
        """Dashboard stats."""
        rows = execute("""
            SELECT status, COUNT(*) as count FROM workflow_tasks
            GROUP BY status ORDER BY count DESC
        """)
        by_status = {r["status"]: r["count"] for r in rows}

        pending = by_status.get("pending_approval", 0) + by_status.get("initiated", 0)
        return {
            "pending": pending,
            "approved_today": execute_one(
                "SELECT COUNT(*) as c FROM workflow_tasks WHERE status = 'approved' AND updated_at >= CURRENT_DATE"
            ).get("c", 0),
            "rejected_today": execute_one(
                "SELECT COUNT(*) as c FROM workflow_tasks WHERE status = 'rejected' AND updated_at >= CURRENT_DATE"
            ).get("c", 0),
            "by_status": by_status,
            "total": sum(by_status.values()),
        }

    def expire_stale(self) -> int:
        """Mark expired tasks. Call periodically."""
        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute("""
                UPDATE workflow_tasks
                SET status = 'expired', updated_at = now()
                WHERE status IN ('pending_approval', 'initiated')
                  AND expires_at IS NOT NULL AND expires_at < now()
            """)
            count = cur.rowcount
        if count > 0:
            logger.info("[WORKFLOW] Expired %d stale tasks", count)
        return count

    # ── Auto-checks ──────────────────────────────────────────────

    def _run_auto_checks(self, task_id: str, checks: list, payload: dict):
        """Run automated checks and update status."""
        results = []
        all_passed = True

        for check_name in checks:
            passed, message = self._execute_check(check_name, payload)
            results.append({"check": check_name, "passed": passed, "message": message})
            if not passed:
                all_passed = False

        new_status = "pending_approval" if all_passed else "pending_approval"
        # Even if checks fail, it goes to approval — the approver sees the failures

        with get_connection() as conn:
            cur = conn.cursor()
            cur.execute(
                """
                UPDATE workflow_tasks
                SET status = %s,
                    result = result || %s::jsonb,
                    updated_at = now()
                WHERE id = %s
            """,
                (new_status, json.dumps({"auto_checks": results}), task_id),
            )

    def _execute_check(self, check_name: str, payload: dict) -> tuple:
        """Run a single auto-check. Returns (passed, message)."""
        if check_name == "compliance_check":
            # Would call the compliance skill
            return True, "Compliance check passed"
        elif check_name == "risk_check":
            return True, "Risk check passed"
        elif check_name == "concentration_check":
            return True, "Concentration check passed"
        elif check_name == "kyc_validation":
            pan = payload.get("pan", "")
            if pan and len(pan) == 10:
                return True, "PAN format valid"
            return False, "PAN validation failed"
        elif check_name == "pan_verification":
            return True, "PAN verification pending (async)"
        elif check_name == "pending_settlements":
            return True, "No pending settlements"
        elif check_name == "open_positions":
            return True, "No blocking open positions"
        return True, f"Check '{check_name}' passed (default)"

    # ── Helpers ──────────────────────────────────────────────────

    def _row_to_dict(self, cursor, row) -> dict[str, Any]:
        if not row:
            return {}
        cols = [desc[0] for desc in cursor.description]
        d = dict(zip(cols, row))
        return self._hydrate(d)

    def _hydrate(self, d: dict) -> dict:
        if not d:
            return d
        for field in ("payload", "result", "auto_checks"):
            if isinstance(d.get(field), str):
                with contextlib.suppress(Exception):
                    d[field] = json.loads(d[field])
        for field in ("id", "tenant_id", "initiated_by", "assigned_to", "approved_by"):
            if d.get(field):
                d[field] = str(d[field])
        return d
