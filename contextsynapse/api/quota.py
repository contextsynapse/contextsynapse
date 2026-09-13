"""
Quota Enforcer
==============
FastAPI dependency that checks tenant usage against plan limits before
allowing resource creation.

Usage::

    quota = QuotaEnforcer(billing_manager, usage_meter)

    @app.post("/graphs")
    async def create_graph(..., _=Depends(quota.check_graphs(tenant))):
        ...
"""

from __future__ import annotations

import logging
from typing import Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)


class QuotaEnforcer:
    """Checks current usage against plan limits and raises 429 if over quota."""

    def __init__(self, billing_manager, usage_meter):
        self._billing = billing_manager
        self._meter = usage_meter

    def check(self, tenant_id: str, resource: str, current_count: int = 0) -> None:
        """
        Check if tenant is within quota for a given resource.

        Args:
            tenant_id: The tenant to check.
            resource: One of 'graphs', 'api_calls', 'agents', 'nodes'.
            current_count: Current count of the resource (for graphs/agents/nodes).

        Raises:
            HTTPException 429 if over quota.
        """
        limits = self._billing.get_limits(tenant_id)

        limit_map = {
            "graphs": "max_graphs",
            "api_calls": "max_api_calls",
            "agents": "max_agents",
            "nodes": "max_nodes",
        }

        limit_key = limit_map.get(resource)
        if not limit_key:
            return  # unknown resource, allow

        max_val = limits.get(limit_key, -1)
        if max_val == -1:
            return  # unlimited

        # For api_calls, check monthly usage
        if resource == "api_calls":
            usage = self._meter.get_summary(tenant_id)
            current_count = usage.get("api_call", 0)

        if current_count >= max_val:
            plan = limits.get("plan", "free")
            raise HTTPException(
                status_code=429,
                detail={
                    "error": "quota_exceeded",
                    "resource": resource,
                    "current": current_count,
                    "limit": max_val,
                    "plan": plan,
                    "message": f"You've reached the {resource} limit ({max_val}) on the {plan} plan. "
                               f"Upgrade your plan to increase limits.",
                },
            )

    def check_feature(self, tenant_id: str, feature: str) -> None:
        """Check if tenant's plan includes a feature. Raises 403 if not."""
        if not self._billing.has_feature(tenant_id, feature):
            plan = self._billing.get_or_create(tenant_id).plan
            raise HTTPException(
                status_code=403,
                detail={
                    "error": "feature_not_available",
                    "feature": feature,
                    "plan": plan,
                    "message": f"The '{feature}' feature is not available on the {plan} plan. "
                               f"Upgrade to access this feature.",
                },
            )
