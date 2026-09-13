"""
API v1 Router
=============
Aggregates all routers under a single /api/v1 prefix.
Current unversioned paths remain as aliases during the deprecation period.
"""

from __future__ import annotations

import logging
from fastapi import APIRouter

logger = logging.getLogger(__name__)

# The v1 router — mounted at /api/v1 in api.py
v1_router = APIRouter(prefix="/api/v1")


def mount_v1_routers(
    v1: APIRouter,
    *,
    tenant_registry,
    user_registry,
    graph_registry,
    usage_meter,
    agent_registry=None,
    session_manager=None,
    audit_log=None,
    billing_manager=None,
    quota_enforcer=None,
    integration_registry=None,
):
    """
    Include every sub-router into the /api/v1 router.

    Each sub-router keeps its own path prefix (e.g. /dashboard/graphs),
    so the final URL becomes /api/v1/dashboard/graphs.
    """

    # ── Admin ────────────────────────────────────────────────────────
    try:
        from .admin_router import create_admin_router
        v1.include_router(create_admin_router(tenant_registry))
    except Exception as e:
        logger.warning(f"v1: Admin router not loaded: {e}")

    # ── Auth ─────────────────────────────────────────────────────────
    try:
        from .auth_router import create_auth_router
        if user_registry:
            v1.include_router(create_auth_router(user_registry, tenant_registry))
    except Exception as e:
        logger.warning(f"v1: Auth router not loaded: {e}")

    # ── Dashboard ────────────────────────────────────────────────────
    try:
        from .dashboard_router import create_dashboard_router
        if user_registry and usage_meter:
            v1.include_router(create_dashboard_router(
                user_registry=user_registry,
                tenant_registry=tenant_registry,
                graph_registry=graph_registry,
                usage_meter=usage_meter,
                agent_registry=agent_registry,
                session_manager=session_manager,
                audit_log=audit_log,
            ))
    except Exception as e:
        logger.warning(f"v1: Dashboard router not loaded: {e}")

    # ── Billing ──────────────────────────────────────────────────────
    try:
        from .billing_router import create_billing_router
        if user_registry and billing_manager:
            v1.include_router(create_billing_router(
                user_registry=user_registry,
                tenant_registry=tenant_registry,
                billing_manager=billing_manager,
                usage_meter=usage_meter,
            ))
    except Exception as e:
        logger.warning(f"v1: Billing router not loaded: {e}")

    # ── Integrations ─────────────────────────────────────────────────
    try:
        from .integrations_router import create_integrations_router
        if user_registry and integration_registry:
            v1.include_router(create_integrations_router(
                user_registry=user_registry,
                tenant_registry=tenant_registry,
                integration_registry=integration_registry,
            ))
    except Exception as e:
        logger.warning(f"v1: Integrations router not loaded: {e}")

    # ── Queries ──────────────────────────────────────────────────────
    try:
        from .queries_router import create_queries_router
        if user_registry:
            v1.include_router(create_queries_router(
                user_registry=user_registry,
                tenant_registry=tenant_registry,
                graph_registry=graph_registry,
                usage_meter=usage_meter,
            ))
    except Exception as e:
        logger.warning(f"v1: Queries router not loaded: {e}")

    # ── Pipelines ────────────────────────────────────────────────────
    try:
        from .pipeline_router import create_pipeline_router
        if user_registry:
            v1.include_router(create_pipeline_router(
                user_registry=user_registry,
                tenant_registry=tenant_registry,
                graph_registry=graph_registry,
                usage_meter=usage_meter,
            ))
    except Exception as e:
        logger.warning(f"v1: Pipeline router not loaded: {e}")

    # ── Search ───────────────────────────────────────────────────────
    try:
        from .search_router import create_search_router
        if user_registry:
            v1.include_router(create_search_router(
                user_registry=user_registry,
                tenant_registry=tenant_registry,
                graph_registry=graph_registry,
            ))
    except Exception as e:
        logger.warning(f"v1: Search router not loaded: {e}")

    # ── Monitoring ───────────────────────────────────────────────────
    try:
        from .monitoring_router import create_monitoring_router
        if user_registry:
            v1.include_router(create_monitoring_router(
                user_registry=user_registry,
                tenant_registry=tenant_registry,
                graph_registry=graph_registry,
                agent_registry=agent_registry,
                usage_meter=usage_meter,
            ))
    except Exception as e:
        logger.warning(f"v1: Monitoring router not loaded: {e}")

    # ── Context ──────────────────────────────────────────────────────
    try:
        from .context_router import create_context_router
        v1.include_router(create_context_router(graph_registry))
    except Exception as e:
        logger.warning(f"v1: Context router not loaded: {e}")

    # ── Graph Algorithms ─────────────────────────────────────────────
    try:
        from .algorithms_router import create_algorithms_router
        if user_registry:
            v1.include_router(create_algorithms_router(
                user_registry=user_registry,
                tenant_registry=tenant_registry,
                graph_registry=graph_registry,
            ))
    except Exception as e:
        logger.warning(f"v1: Algorithms router not loaded: {e}")

    return v1
