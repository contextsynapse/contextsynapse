"""
Feature Gate
============
Plan-based feature gating for open-core model.

Core features (always free, including self-hosted):
  - core_db, aiql, context, rest_api, mcp

Pro features ($49/mo):
  - hybrid_search, advanced_analytics, priority_support

Enterprise features (custom pricing):
  - sso, webhooks, dedicated_support, sla

Self-hosted deployments bypass all gates (enterprise plan by default).

Usage::

    gate = FeatureGate(billing_manager)

    @app.post("/search/hybrid")
    async def hybrid_search(..., tenant=Depends(tenant_auth)):
        gate.require(tenant.tenant_id, "hybrid_search")
        ...
"""

from __future__ import annotations

import logging
from typing import Set

from fastapi import HTTPException

logger = logging.getLogger(__name__)


# Features grouped by tier
CORE_FEATURES: Set[str] = {"core_db", "aiql", "context", "rest_api", "mcp"}
PRO_FEATURES: Set[str] = {"hybrid_search", "advanced_analytics", "priority_support"}
ENTERPRISE_FEATURES: Set[str] = {"sso", "webhooks", "dedicated_support", "sla"}


class FeatureGate:
    """Check tenant plan before allowing access to gated features."""

    def __init__(self, billing_manager):
        self._billing = billing_manager

    def require(self, tenant_id: str, feature: str) -> None:
        """
        Check that the tenant's plan includes this feature.
        Raises 403 if not.
        No-op for self-hosted (enterprise plan = all features).
        """
        if not self._billing.stripe_enabled:
            return  # Self-hosted = everything unlocked

        if feature in CORE_FEATURES:
            return  # Always available

        if not self._billing.has_feature(tenant_id, feature):
            plan = self._billing.get_or_create(tenant_id).plan
            needed = "Pro" if feature in PRO_FEATURES else "Enterprise"
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "feature_gated",
                    "feature": feature,
                    "current_plan": plan,
                    "required_plan": needed.lower(),
                    "message": f"'{feature.replace('_', ' ')}' requires the {needed} plan. "
                               f"Upgrade at /dashboard/billing to unlock.",
                },
            )

    def check(self, tenant_id: str, feature: str) -> bool:
        """Non-raising check. Returns True if the feature is available."""
        if not self._billing.stripe_enabled:
            return True
        if feature in CORE_FEATURES:
            return True
        return self._billing.has_feature(tenant_id, feature)

    def available_features(self, tenant_id: str) -> Set[str]:
        """Return the set of features available to the tenant."""
        if not self._billing.stripe_enabled:
            return CORE_FEATURES | PRO_FEATURES | ENTERPRISE_FEATURES
        limits = self._billing.get_limits(tenant_id)
        return set(limits.get("features", []))
