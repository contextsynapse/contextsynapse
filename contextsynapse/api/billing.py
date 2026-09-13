"""
Billing & Subscription Management
==================================
Plan definitions, subscription tracking, and optional Stripe integration.

Plans:
  - **free**:       3 graphs, 10K API calls/mo, 5 agents
  - **pro**:        50 graphs, 500K API calls/mo, 50 agents  ($49/mo)
  - **enterprise**: unlimited (custom pricing)

Self-hosted deployments skip Stripe entirely — every tenant defaults to
``enterprise`` (unlimited).

Usage::

    billing = BillingManager()
    sub = billing.get_or_create(tenant_id)
    billing.upgrade(tenant_id, "pro", stripe_customer_id, stripe_subscription_id)
    limits = billing.get_limits(tenant_id)
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from contextsynapse.core.db import IS_POSTGRES, PH, connect, dict_cursor, run_ddl, row_to_dict

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Plan definitions
# ---------------------------------------------------------------------------

PLANS = {
    "free": {
        "name": "Free",
        "price_monthly": 0,
        "max_graphs": 3,
        "max_api_calls": 10_000,
        "max_agents": 5,
        "max_nodes": 50_000,
        "features": ["core_db", "aiql", "context", "rest_api", "mcp"],
    },
    "pro": {
        "name": "Pro",
        "price_monthly": 49,
        "max_graphs": 50,
        "max_api_calls": 500_000,
        "max_agents": 50,
        "max_nodes": 5_000_000,
        "features": ["core_db", "aiql", "context", "rest_api", "mcp",
                      "hybrid_search", "advanced_analytics", "priority_support"],
    },
    "enterprise": {
        "name": "Enterprise",
        "price_monthly": None,  # custom
        "max_graphs": -1,       # unlimited
        "max_api_calls": -1,
        "max_agents": -1,
        "max_nodes": -1,
        "features": ["core_db", "aiql", "context", "rest_api", "mcp",
                      "hybrid_search", "advanced_analytics", "priority_support",
                      "sso", "webhooks", "dedicated_support", "sla"],
    },
}


def get_plan(plan_name: str) -> Dict[str, Any]:
    return PLANS.get(plan_name, PLANS["free"])


# ---------------------------------------------------------------------------
# Subscription data
# ---------------------------------------------------------------------------

@dataclass
class Subscription:
    subscription_id: str
    tenant_id: str
    plan: str
    status: str  # active, canceled, past_due
    stripe_customer_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    current_period_end: Optional[str] = None
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["plan_details"] = get_plan(self.plan)
        return d


# ---------------------------------------------------------------------------
# BillingManager
# ---------------------------------------------------------------------------

class BillingManager:
    """Persistent subscription store backed by SQLite or PostgreSQL."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS subscriptions (
        subscription_id TEXT PRIMARY KEY,
        tenant_id TEXT NOT NULL UNIQUE,
        stripe_customer_id TEXT,
        stripe_subscription_id TEXT,
        plan TEXT DEFAULT 'free',
        status TEXT DEFAULT 'active',
        current_period_end TEXT,
        created_at TEXT NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_sub_tenant ON subscriptions(tenant_id);
    """

    def __init__(self, db_path: str = "contextcore_data/billing.db"):
        self._db_path = Path(db_path)
        if not IS_POSTGRES:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(str(self._db_path))
        run_ddl(self._conn, self._SCHEMA)

        # Detect self-hosted mode (no Stripe key = self-hosted = unlimited)
        self.stripe_enabled = bool(os.environ.get("STRIPE_SECRET_KEY"))

    def _exec(self, sql: str, params: tuple = ()):
        cur = dict_cursor(self._conn)
        cur.execute(sql, params)
        return cur

    def _commit(self):
        self._conn.commit()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    def get_or_create(self, tenant_id: str) -> Subscription:
        """Get subscription for tenant, auto-creating a free one if absent."""
        row = row_to_dict(self._exec(
            f"SELECT * FROM subscriptions WHERE tenant_id = {PH}",
            (tenant_id,),
        ).fetchone())

        if row:
            return self._row_to_sub(row)

        # Auto-create
        default_plan = "enterprise" if not self.stripe_enabled else "free"
        sub_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()

        self._exec(
            f"INSERT INTO subscriptions "
            f"(subscription_id, tenant_id, plan, status, created_at) "
            f"VALUES ({PH}, {PH}, {PH}, 'active', {PH})",
            (sub_id, tenant_id, default_plan, now),
        )
        self._commit()

        return Subscription(
            subscription_id=sub_id,
            tenant_id=tenant_id,
            plan=default_plan,
            status="active",
            created_at=now,
        )

    def get(self, tenant_id: str) -> Optional[Subscription]:
        row = row_to_dict(self._exec(
            f"SELECT * FROM subscriptions WHERE tenant_id = {PH}",
            (tenant_id,),
        ).fetchone())
        return self._row_to_sub(row) if row else None

    def upgrade(
        self,
        tenant_id: str,
        plan: str,
        stripe_customer_id: Optional[str] = None,
        stripe_subscription_id: Optional[str] = None,
        period_end: Optional[str] = None,
    ) -> Subscription:
        """Upgrade/change a tenant's plan."""
        sub = self.get_or_create(tenant_id)
        self._exec(
            f"UPDATE subscriptions SET plan = {PH}, stripe_customer_id = {PH}, "
            f"stripe_subscription_id = {PH}, current_period_end = {PH} "
            f"WHERE tenant_id = {PH}",
            (plan, stripe_customer_id, stripe_subscription_id, period_end, tenant_id),
        )
        self._commit()
        sub.plan = plan
        sub.stripe_customer_id = stripe_customer_id
        sub.stripe_subscription_id = stripe_subscription_id
        sub.current_period_end = period_end
        return sub

    def cancel(self, tenant_id: str) -> None:
        """Mark subscription as canceled (downgrade to free at period end)."""
        self._exec(
            f"UPDATE subscriptions SET status = 'canceled' WHERE tenant_id = {PH}",
            (tenant_id,),
        )
        self._commit()

    def update_status(self, tenant_id: str, status: str) -> None:
        self._exec(
            f"UPDATE subscriptions SET status = {PH} WHERE tenant_id = {PH}",
            (status, tenant_id),
        )
        self._commit()

    # ------------------------------------------------------------------
    # Limits
    # ------------------------------------------------------------------

    def get_limits(self, tenant_id: str) -> Dict[str, Any]:
        """Get the quota limits for a tenant based on their plan."""
        sub = self.get_or_create(tenant_id)
        plan = get_plan(sub.plan)
        return {
            "plan": sub.plan,
            "max_graphs": plan["max_graphs"],
            "max_api_calls": plan["max_api_calls"],
            "max_agents": plan["max_agents"],
            "max_nodes": plan["max_nodes"],
            "features": plan["features"],
        }

    def has_feature(self, tenant_id: str, feature: str) -> bool:
        """Check if tenant's plan includes a specific feature."""
        limits = self.get_limits(tenant_id)
        return feature in limits["features"]

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_sub(self, row) -> Subscription:
        d = row_to_dict(row) if not isinstance(row, dict) else row
        return Subscription(
            subscription_id=d["subscription_id"],
            tenant_id=d["tenant_id"],
            plan=d["plan"],
            status=d["status"],
            stripe_customer_id=d.get("stripe_customer_id"),
            stripe_subscription_id=d.get("stripe_subscription_id"),
            current_period_end=d.get("current_period_end"),
            created_at=d["created_at"],
        )

    def close(self):
        if self._conn:
            self._conn.close()
