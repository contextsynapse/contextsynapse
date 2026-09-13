"""
Federation API Router
======================
Endpoints for managing cross-namespace context sharing.
"""

from __future__ import annotations

import logging
from typing import Optional
from fastapi import APIRouter, Body, Depends, HTTPException, Query

logger = logging.getLogger(__name__)


def create_federation_router(graph_registry, user_auth, require_admin):
    """Create the federation router with injected dependencies."""

    router = APIRouter(prefix="/federation", tags=["federation"])

    # Lazy-init federation registry (uses Redis if available)
    _fed_registry = None

    def _get_fed():
        nonlocal _fed_registry
        if _fed_registry is None:
            import os
            from ..core.federation import FederationRegistry
            redis_client = None
            try:
                redis_url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
                if redis_url:
                    import redis
                    redis_client = redis.from_url(redis_url, decode_responses=True)
            except Exception:
                pass
            _fed_registry = FederationRegistry(redis_client)
        return _fed_registry

    _fed_search = None

    def _get_search():
        nonlocal _fed_search
        if _fed_search is None:
            from ..core.federation import FederatedSearchEngine
            _fed_search = FederatedSearchEngine(graph_registry, _get_fed())
        return _fed_search

    @router.post("/share")
    async def create_grant(req: dict = Body(...), user=Depends(require_admin)):
        """Share a namespace's public nodes with another namespace."""
        source = req.get("source_namespace", "")
        target = req.get("target_namespace", "")
        labels = req.get("node_labels")  # optional list
        if not source or not target:
            raise HTTPException(400, "source_namespace and target_namespace required")
        try:
            grant = _get_fed().create_grant(
                source, target,
                created_by=getattr(user, "user_id", ""),
                node_labels=labels,
            )
            return grant.to_dict()
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.delete("/share/{grant_id}")
    async def revoke_grant(grant_id: str, user=Depends(require_admin)):
        """Revoke a federation grant."""
        ok = _get_fed().revoke_grant(grant_id)
        if not ok:
            raise HTTPException(404, "Grant not found")
        return {"revoked": True}

    @router.get("/grants")
    async def list_grants(
        namespace: str = Query("", description="Filter by target namespace"),
        user=Depends(user_auth),
    ):
        """List federation grants."""
        fed = _get_fed()
        if namespace:
            grants = fed.get_grants_for(namespace)
        else:
            grants = fed.list_all_grants()
        return {"grants": [g.to_dict() for g in grants]}

    @router.get("/search")
    async def federated_search(
        query: str = Query("", description="Search query"),
        namespace: str = Query("", description="Source namespace to search from"),
        include_federated: bool = Query(True),
        label: str = Query("", description="Filter by node label"),
        limit: int = Query(20, ge=1, le=100),
        user=Depends(user_auth),
    ):
        """Search across local + federated namespaces. Only PUBLIC nodes cross boundaries."""
        if not namespace:
            raise HTTPException(400, "namespace parameter required")
        engine = _get_search()
        result = engine.search(
            query=query, source_namespace=namespace,
            include_federated=include_federated,
            limit=limit, label=label or None,
        )
        return result.to_dict()

    return router
