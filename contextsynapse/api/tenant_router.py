"""Tenant Management API — superadmin creates/manages tenants."""
import logging
from fastapi import APIRouter, Body, Query

logger = logging.getLogger(__name__)

router = APIRouter(tags=["tenant"])


@router.post("/platform/tenants")
async def create_tenant(body: dict = Body(...)):
    """Superadmin: create a new tenant with vertical + admin user."""
    from contextsynapse.core.tenant_onboard import TenantEngine
    return TenantEngine().create_tenant(
        name=body.get("name", ""),
        slug=body.get("slug", ""),
        plan=body.get("plan", "professional"),
        vertical=body.get("vertical", "pms"),
        admin_email=body.get("admin_email", ""),
        admin_password=body.get("admin_password", "Admin123!"),
        admin_name=body.get("admin_name", ""),
        branding=body.get("branding"),
        sebi_registration=body.get("sebi_registration", ""),
        domain=body.get("domain", ""),
        deployment_mode=body.get("deployment_mode", "saas"),
    )


@router.get("/platform/tenants")
async def list_tenants():
    """Superadmin: list all tenants."""
    from contextsynapse.core.tenant_onboard import TenantEngine
    return {"tenants": TenantEngine().list_tenants()}


@router.get("/platform/tenants/{tenant_id}")
async def get_tenant(tenant_id: str):
    """Get tenant details."""
    from contextsynapse.core.tenant_onboard import TenantEngine
    tenant = TenantEngine().get_tenant(tenant_id)
    if not tenant:
        return {"error": "Tenant not found"}
    tenant["stats"] = TenantEngine().get_tenant_stats(tenant_id)
    return tenant


@router.patch("/platform/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, body: dict = Body(...)):
    """Update tenant settings."""
    from contextsynapse.core.tenant_onboard import TenantEngine
    return TenantEngine().update_tenant(tenant_id, body) or {"error": "Not found"}


@router.post("/platform/tenants/{tenant_id}/suspend")
async def suspend_tenant(tenant_id: str):
    """Suspend a tenant."""
    from contextsynapse.core.tenant_onboard import TenantEngine
    ok = TenantEngine().suspend_tenant(tenant_id)
    return {"suspended": ok}


@router.get("/platform/plans")
async def list_plans():
    """List available plans with limits."""
    from contextsynapse.core.tenant_onboard import PLAN_LIMITS
    return {"plans": PLAN_LIMITS}


# ── Branding endpoint (public, no auth) ─────────────────────

@router.get("/branding")
async def get_branding(slug: str = Query("")):
    """Get white-label branding for a tenant. Called by frontend on load."""
    from contextsynapse.core.tenant_onboard import TenantEngine
    return TenantEngine().get_branding(slug=slug)


# ── Blueprint endpoints ─────────────────────────────────────

@router.post("/platform/blueprints")
async def create_blueprint(body: dict = Body(...)):
    """Create a context blueprint (agent researches + fills template)."""
    from contextsynapse.context.blueprint import BlueprintEngine
    engine = BlueprintEngine()
    if body.get("use_llm", True):
        bp = engine.create_from_llm(
            context_type=body.get("context_type", "stock"),
            entity_key=body.get("entity_key", ""),
            created_by=body.get("created_by", ""),
        )
    else:
        bp = engine.create(
            context_type=body.get("context_type", "stock"),
            entity_key=body.get("entity_key", ""),
            agent_output=body.get("research", {}),
            created_by=body.get("created_by", ""),
        )
    return bp.to_dict()


@router.get("/platform/blueprints")
async def list_blueprints(status: str = Query(""), context_type: str = Query("")):
    """List blueprints."""
    from contextsynapse.context.blueprint import BlueprintEngine
    return {"blueprints": BlueprintEngine().list_all(status, context_type)}


@router.get("/platform/blueprints/{blueprint_id}")
async def get_blueprint(blueprint_id: str):
    """Get blueprint details with validation data."""
    from contextsynapse.context.blueprint import BlueprintEngine
    bp = BlueprintEngine().get(blueprint_id)
    return bp.to_dict() if bp else {"error": "Not found"}


@router.patch("/platform/blueprints/{blueprint_id}")
async def edit_blueprint(blueprint_id: str, body: dict = Body(...)):
    """Edit blueprint fields."""
    from contextsynapse.context.blueprint import BlueprintEngine
    bp = BlueprintEngine().edit(blueprint_id, body.get("edits", {}), body.get("edited_by", ""))
    return bp.to_dict() if bp else {"error": "Not found"}


@router.post("/platform/blueprints/{blueprint_id}/approve")
async def approve_blueprint(blueprint_id: str, body: dict = Body(...)):
    """Approve blueprint — wire everything."""
    from contextsynapse.context.blueprint import BlueprintEngine
    bp = BlueprintEngine().approve(blueprint_id, body.get("approved_by", ""))
    return bp.to_dict() if bp else {"error": "Not found"}
