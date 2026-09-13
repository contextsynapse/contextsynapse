"""RulesStore — PostgreSQL-backed compliance rules CRUD.

Replaces graph-node-based rule storage with indexed SQL queries.
Graph scan O(n) → SQL indexed O(1).
"""

import json
import uuid
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from contextsynapse.db import execute, execute_one, insert, update
from contextsynapse.db.postgres import _USE_PG

log = logging.getLogger(__name__)


def _serialize_json(value: Any) -> str:
    """Serialize a Python object to JSON string for storage."""
    if isinstance(value, str):
        return value
    return json.dumps(value)


def _parse_json(value: Any) -> Any:
    """Parse a JSON string back to Python objects.  Handles both PG (auto-parsed) and SQLite (text)."""
    if value is None:
        return []
    if isinstance(value, (list, dict)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except (json.JSONDecodeError, TypeError):
            return []
    return value


def _hydrate_rule(row: dict) -> dict:
    """Convert a raw DB row into a clean rule dict with parsed JSON fields."""
    if row is None:
        return None
    rule = dict(row)
    for field in ("conditions", "actions", "triggers"):
        rule[field] = _parse_json(rule.get(field))
    # Ensure enabled is a proper bool (SQLite stores as 0/1)
    if "enabled" in rule:
        rule["enabled"] = bool(rule["enabled"])
    return rule


class RulesStore:
    """PostgreSQL / SQLite-backed compliance rules storage."""

    # ------------------------------------------------------------------
    # Create
    # ------------------------------------------------------------------

    def create_rule(
        self,
        tenant_id: Optional[str],
        name: str,
        description: str = "",
        category: str = "custom",
        severity: str = "medium",
        conditions: Optional[list] = None,
        conditions_operator: str = "AND",
        actions: Optional[list] = None,
        scope: str = "all",
        triggers: Optional[list] = None,
        enabled: bool = True,
        created_by: Optional[str] = None,
    ) -> dict:
        """Insert a new compliance rule and return it with generated rule_id."""
        rule_id = f"rule_{uuid.uuid4().hex[:8]}"
        data = {
            "rule_id": rule_id,
            "name": name,
            "description": description,
            "category": category,
            "severity": severity,
            "conditions": _serialize_json(conditions or []),
            "conditions_operator": conditions_operator,
            "actions": _serialize_json(actions or []),
            "scope": scope,
            "triggers": _serialize_json(triggers or ["on_demand"]),
            "enabled": enabled,
            "version": 1,
        }
        if tenant_id is not None:
            data["tenant_id"] = tenant_id
        if created_by is not None:
            data["created_by"] = created_by

        row = insert("compliance_rules", data)
        return _hydrate_rule(row)

    # ------------------------------------------------------------------
    # Read
    # ------------------------------------------------------------------

    def get_rule(self, rule_id: str) -> Optional[dict]:
        """Fetch a single rule by its human-readable rule_id."""
        row = execute_one(
            "SELECT * FROM compliance_rules WHERE rule_id = %s",
            (rule_id,),
        )
        if row is None:
            return None
        return _hydrate_rule(row)

    def list_rules(
        self,
        tenant_id: Optional[str] = None,
        vertical: str = "pms",
        category: Optional[str] = None,
        enabled: Optional[bool] = None,
    ) -> List[dict]:
        """List rules with optional filters, ordered by created_at DESC."""
        clauses = ["vertical = %s"]
        params: list = [vertical]

        if tenant_id is not None:
            clauses.append("tenant_id = %s")
            params.append(tenant_id)
        if category is not None:
            clauses.append("category = %s")
            params.append(category)
        if enabled is not None:
            clauses.append("enabled = %s")
            params.append(enabled)

        where = " AND ".join(clauses)
        rows = execute(
            f"SELECT * FROM compliance_rules WHERE {where} ORDER BY created_at DESC",
            tuple(params),
        )
        return [_hydrate_rule(r) for r in rows]

    # ------------------------------------------------------------------
    # Update
    # ------------------------------------------------------------------

    def update_rule(self, rule_id: str, **updates) -> Optional[dict]:
        """Update specified fields on a rule.  Increments version and sets updated_at."""
        if not updates:
            return self.get_rule(rule_id)

        # Serialize JSON fields if present
        for field in ("conditions", "actions", "triggers"):
            if field in updates and not isinstance(updates[field], str):
                updates[field] = _serialize_json(updates[field])

        # Always bump version and updated_at
        existing = self.get_rule(rule_id)
        if existing is None:
            return None

        updates["version"] = existing.get("version", 1) + 1
        updates["updated_at"] = datetime.now(timezone.utc).isoformat()

        affected = update(
            "compliance_rules",
            updates,
            "rule_id = %s",
            (rule_id,),
        )
        if affected == 0:
            return None
        return self.get_rule(rule_id)

    def toggle_rule(self, rule_id: str, enabled: bool) -> Optional[dict]:
        """Enable or disable a rule."""
        return self.update_rule(rule_id, enabled=enabled)

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    def delete_rule(self, rule_id: str) -> bool:
        """Delete a rule and its portfolio assignments.  Returns True if deleted."""
        # Check existence first (needed for SQLite which lacks RETURNING)
        existing = self.get_rule(rule_id)
        if existing is None:
            return False
        # Remove portfolio assignments first
        execute(
            "DELETE FROM rule_portfolios WHERE rule_id = %s",
            (rule_id,),
        )
        if _USE_PG:
            rows = execute(
                "DELETE FROM compliance_rules WHERE rule_id = %s RETURNING rule_id",
                (rule_id,),
            )
            return len(rows) > 0
        else:
            execute(
                "DELETE FROM compliance_rules WHERE rule_id = %s",
                (rule_id,),
            )
            return True

    # ------------------------------------------------------------------
    # Portfolio assignments
    # ------------------------------------------------------------------

    def assign_to_portfolio(self, rule_id: str, portfolio_id: str) -> dict:
        """Link a rule to a portfolio."""
        return insert("rule_portfolios", {
            "rule_id": rule_id,
            "portfolio_id": portfolio_id,
        })

    def unassign_from_portfolio(self, rule_id: str, portfolio_id: str) -> bool:
        """Remove a rule-portfolio link."""
        execute(
            "DELETE FROM rule_portfolios WHERE rule_id = %s AND portfolio_id = %s",
            (rule_id, portfolio_id),
        )
        return True

    def get_rule_portfolios(self, rule_id: str) -> List[str]:
        """Return list of portfolio_ids assigned to a rule."""
        rows = execute(
            "SELECT portfolio_id FROM rule_portfolios WHERE rule_id = %s",
            (rule_id,),
        )
        return [r["portfolio_id"] for r in rows]

    # ------------------------------------------------------------------
    # Execution history
    # ------------------------------------------------------------------

    def record_execution(
        self,
        rule_id: str,
        portfolio_id: Optional[str],
        status: str,
        severity: Optional[str] = None,
        message: Optional[str] = None,
        details: Optional[dict] = None,
        tenant_id: Optional[str] = None,
    ) -> dict:
        """Record a rule execution result."""
        data: Dict[str, Any] = {
            "rule_id": rule_id,
            "status": status,
        }
        if portfolio_id is not None:
            data["portfolio_id"] = portfolio_id
        if severity is not None:
            data["severity"] = severity
        if message is not None:
            data["message"] = message
        if details is not None:
            data["details"] = _serialize_json(details)
        if tenant_id is not None:
            data["tenant_id"] = tenant_id

        return insert("rule_executions", data)

    def get_execution_history(
        self,
        rule_id: Optional[str] = None,
        portfolio_id: Optional[str] = None,
        days: int = 30,
        limit: int = 100,
    ) -> List[dict]:
        """Fetch execution history with optional filters."""
        clauses = []
        params: list = []

        if rule_id is not None:
            clauses.append("rule_id = %s")
            params.append(rule_id)
        if portfolio_id is not None:
            clauses.append("portfolio_id = %s")
            params.append(portfolio_id)

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        clauses.append("executed_at >= %s")
        params.append(cutoff)

        where = " AND ".join(clauses) if clauses else "1=1"
        params.append(limit)
        rows = execute(
            f"SELECT * FROM rule_executions WHERE {where} "
            f"ORDER BY executed_at DESC LIMIT %s",
            tuple(params),
        )
        for r in rows:
            r["details"] = _parse_json(r.get("details"))
        return rows

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    def get_rule_stats(self) -> dict:
        """Return aggregate rule statistics."""
        total = execute_one(
            "SELECT COUNT(*) AS cnt FROM compliance_rules"
        )
        enabled = execute_one(
            "SELECT COUNT(*) AS cnt FROM compliance_rules WHERE enabled = %s",
            (True,),
        )

        today_start = datetime.now(timezone.utc).replace(
            hour=0, minute=0, second=0, microsecond=0
        ).isoformat()
        violations = execute_one(
            "SELECT COUNT(*) AS cnt FROM rule_executions "
            "WHERE status = %s AND executed_at >= %s",
            ("violated", today_start),
        )
        last_exec = execute_one(
            "SELECT MAX(executed_at) AS last_check FROM rule_executions"
        )

        return {
            "total_rules": (total or {}).get("cnt", 0),
            "enabled_rules": (enabled or {}).get("cnt", 0),
            "violations_today": (violations or {}).get("cnt", 0),
            "last_check": (last_exec or {}).get("last_check"),
        }
