"""
Admin API Router
=================
Tenant management and system administration endpoints.

All routes require the ``X-Admin-Key`` header matching the
``AICONTEXTDB_ADMIN_KEY`` environment variable.

Mount on the main FastAPI app::

    from contextsynapse.api.admin_router import create_admin_router
    app.include_router(create_admin_router(tenant_registry))
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .auth import AdminAuth, create_admin_jwt
from .tenants import TenantRegistry, TenantIdentity

logger = logging.getLogger(__name__)


# ── Request / Response models ─────────────────────────────────────────

class AdminLoginRequest(BaseModel):
    admin_key: str


class TenantCreateRequest(BaseModel):
    name: str
    config: Dict[str, Any] = Field(default_factory=dict)


class TenantResponse(BaseModel):
    tenant_id: str
    name: str
    status: str
    created_at: str
    config: Dict[str, Any]


class TenantCreateResponse(TenantResponse):
    api_key: str  # only returned at creation time


class TenantUpdateRequest(BaseModel):
    status: Optional[str] = None  # active, suspended


# ── Router factory ────────────────────────────────────────────────────

def create_admin_router(tenant_registry: TenantRegistry) -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["Admin"])
    require_admin = AdminAuth()

    # ── Login ──────────────────────────────────────────────────────

    @router.post("/login")
    async def admin_login(req: AdminLoginRequest):
        """Exchange the admin key for a JWT token (used by the dashboard)."""
        import os
        admin_key = os.environ.get("CONTEXTSYNAPSE_ADMIN_KEY") or os.environ.get("AICONTEXTDB_ADMIN_KEY")
        if not admin_key:
            raise HTTPException(503, "Admin API not configured (AICONTEXTDB_ADMIN_KEY not set)")
        if req.admin_key != admin_key:
            raise HTTPException(403, "Invalid admin key")
        token = create_admin_jwt(admin_key)
        return {"token": token, "expires_in": 86400}

    # ── Tenant CRUD ───────────────────────────────────────────────

    @router.post("/tenants", response_model=TenantCreateResponse)
    async def create_tenant(req: TenantCreateRequest, _=Depends(require_admin)):
        # Check for duplicate name
        existing = tenant_registry.get_by_name(req.name)
        if existing:
            raise HTTPException(409, f"Tenant '{req.name}' already exists")

        tenant, api_key = tenant_registry.create(name=req.name, config=req.config)
        return TenantCreateResponse(**tenant.to_dict(), api_key=api_key)

    @router.get("/tenants", response_model=List[TenantResponse])
    async def list_tenants(
        include_inactive: bool = Query(False),
        _=Depends(require_admin),
    ):
        tenants = tenant_registry.list_tenants(include_inactive=include_inactive)
        return [TenantResponse(**t.to_dict()) for t in tenants]

    @router.get("/tenants/{tenant_id}", response_model=TenantResponse)
    async def get_tenant(tenant_id: str, _=Depends(require_admin)):
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        return TenantResponse(**tenant.to_dict())

    @router.patch("/tenants/{tenant_id}", response_model=TenantResponse)
    async def update_tenant(tenant_id: str, req: TenantUpdateRequest, _=Depends(require_admin)):
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(404, "Tenant not found")
        if req.status:
            if req.status not in ("active", "suspended"):
                raise HTTPException(400, "Status must be 'active' or 'suspended'")
            tenant_registry.update_status(tenant_id, req.status)
        updated = tenant_registry.get(tenant_id)
        return TenantResponse(**updated.to_dict())

    @router.delete("/tenants/{tenant_id}")
    async def delete_tenant(tenant_id: str, _=Depends(require_admin)):
        ok = tenant_registry.delete(tenant_id)
        if not ok:
            raise HTTPException(404, "Tenant not found")
        return {"success": True, "message": f"Tenant {tenant_id} deleted"}

    @router.post("/tenants/{tenant_id}/rotate-key")
    async def rotate_tenant_key(tenant_id: str, _=Depends(require_admin)):
        new_key = tenant_registry.rotate_key(tenant_id)
        if not new_key:
            raise HTTPException(404, "Tenant not found")
        return {"success": True, "tenant_id": tenant_id, "api_key": new_key}

    # ── System info ───────────────────────────────────────────────

    @router.get("/system")
    async def system_info(_=Depends(require_admin)):
        tenants = tenant_registry.list_tenants(include_inactive=True)
        return {
            "total_tenants": len(tenants),
            "active_tenants": sum(1 for t in tenants if t.status == "active"),
            "suspended_tenants": sum(1 for t in tenants if t.status == "suspended"),
        }

    return router
