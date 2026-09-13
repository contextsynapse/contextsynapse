"""TenantIsolation — ensures data separation between AMCs/organizations.

Every resource belongs to a tenant. Users can only access resources
within their tenant. Cross-tenant access is never allowed (even for admins).

In dev mode: a single default tenant is used.
In production: tenant is extracted from JWT and enforced on every query.

Namespace isolation (legacy):
    ti = TenantIsolation()
    ns = ti.namespace("alpha_capital", "portfolio_growth")
    # -> "alpha_capital_portfolio_growth"

    ti.validate_access("alpha_capital", ns)  # True
    ti.validate_access("beta_wealth", ns)    # False
    ti.validate_access("anyone", "market_nifty50")  # True (shared)

Tenant management (new — PostgreSQL-backed):
    tm = TenantManager()
    tenant = tm.create_tenant("Alpha Capital", "alpha-capital", plan="pro")
    tm.get_tenant(tenant["id"])
    tm.suspend_tenant(tenant["id"], reason="Non-payment")
"""
from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


# ── Shared namespace prefixes (platform-level data) ──────────────────────────
# Namespace prefixes that are shared across all tenants
SHARED_PREFIXES: List[str] = [
    "market_",
    "stock_",
    "macro_",
    "regulatory_",
    "fund_nav_",
    "sector_",
    "index_",
    "currency_",
    "fii_",
]

# Known tenant IDs (populated at runtime)
_known_tenants: Set[str] = set()

# Default tenant for dev mode
DEFAULT_TENANT_SLUG = "dev"
DEFAULT_TENANT_NAME = "Development"


# ── Namespace isolation (legacy, kept for backward compat) ───────────────────

class TenantIsolation:
    """Enforces tenant-scoped namespace isolation."""

    def __init__(self):
        self._known_tenants: Set[str] = set()

    def namespace(self, tenant_id: str, resource: str) -> str:
        """Create a tenant-scoped namespace."""
        safe_tenant = re.sub(r'[^a-zA-Z0-9_]', '_', tenant_id)
        safe_resource = re.sub(r'[^a-zA-Z0-9_]', '_', resource)
        self._known_tenants.add(safe_tenant)
        return f"{safe_tenant}_{safe_resource}"

    def is_shared(self, namespace: str) -> bool:
        """Check if a namespace is shared (platform-level market data)."""
        return any(namespace.startswith(prefix) for prefix in SHARED_PREFIXES)

    def extract_tenant(self, namespace: str) -> Optional[str]:
        """Extract tenant ID from a namespaced string."""
        if self.is_shared(namespace):
            return None

        # Try to match against known tenants
        for tenant in self._known_tenants:
            if namespace.startswith(f"{tenant}_"):
                return tenant

        return None

    def validate_access(
        self, tenant_id: str, namespace: str
    ) -> bool:
        """Check if a tenant can access a namespace."""
        # Shared namespaces accessible to all
        if self.is_shared(namespace):
            return True

        # Tenant can access their own namespaces
        safe_tenant = re.sub(r'[^a-zA-Z0-9_]', '_', tenant_id)
        return namespace.startswith(f"{safe_tenant}_")


# ── Tenant Manager (PostgreSQL-backed) ───────────────────────────────────────

class TenantManager:
    """Manages tenants in the database (PostgreSQL or SQLite).

    Uses contextsynapse.core.db for database access, so it works with both
    backends seamlessly.
    """

    _DDL_PG = """
    CREATE TABLE IF NOT EXISTS tenants (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        slug        TEXT NOT NULL UNIQUE,
        plan        TEXT NOT NULL DEFAULT 'free',
        settings    TEXT NOT NULL DEFAULT '{}',
        is_active   BOOLEAN NOT NULL DEFAULT TRUE,
        suspended   BOOLEAN NOT NULL DEFAULT FALSE,
        suspend_reason TEXT,
        created_at  TIMESTAMP NOT NULL DEFAULT NOW(),
        updated_at  TIMESTAMP NOT NULL DEFAULT NOW()
    );
    CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
    CREATE INDEX IF NOT EXISTS idx_tenants_active ON tenants(is_active);
    """

    _DDL_SQLITE = """
    CREATE TABLE IF NOT EXISTS tenants (
        id          TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        slug        TEXT NOT NULL UNIQUE,
        plan        TEXT NOT NULL DEFAULT 'free',
        settings    TEXT NOT NULL DEFAULT '{}',
        is_active   INTEGER NOT NULL DEFAULT 1,
        suspended   INTEGER NOT NULL DEFAULT 0,
        suspend_reason TEXT,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_tenants_slug ON tenants(slug);
    CREATE INDEX IF NOT EXISTS idx_tenants_active ON tenants(is_active);
    """

    def __init__(self):
        from ..core.db import IS_POSTGRES
        self._is_pg = IS_POSTGRES
        self._ensure_table()

    def _ensure_table(self):
        """Create tenants table if it doesn't exist."""
        from ..core.db import transaction, run_ddl, IS_POSTGRES
        try:
            with transaction() as (conn, _cur):
                ddl = self._DDL_PG if IS_POSTGRES else self._DDL_SQLITE
                run_ddl(conn, ddl)
        except Exception as exc:
            logger.warning("Could not create tenants table: %s", exc)

    # ── CRUD ─────────────────────────────────────────────────────────────────

    def create_tenant(
        self,
        name: str,
        slug: str,
        plan: str = "free",
        settings: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """Create a new tenant. Returns the tenant dict."""
        import json
        from ..core.db import transaction, PH, integrity_error

        tenant_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        settings_json = json.dumps(settings or {})

        try:
            with transaction() as (conn, cur):
                cur.execute(
                    f"INSERT INTO tenants (id, name, slug, plan, settings, created_at, updated_at) "
                    f"VALUES ({PH}, {PH}, {PH}, {PH}, {PH}, {PH}, {PH})",
                    (tenant_id, name, slug, plan, settings_json, now, now),
                )
        except integrity_error():
            raise ValueError(f"Tenant with slug '{slug}' already exists")

        return {
            "id": tenant_id,
            "name": name,
            "slug": slug,
            "plan": plan,
            "settings": settings or {},
            "is_active": True,
            "suspended": False,
            "suspend_reason": None,
            "created_at": now,
            "updated_at": now,
        }

    def get_tenant(self, tenant_id: str) -> Optional[Dict[str, Any]]:
        """Get a tenant by ID."""
        from ..core.db import transaction, PH, row_to_dict
        with transaction() as (_conn, cur):
            cur.execute(
                f"SELECT * FROM tenants WHERE id = {PH}", (tenant_id,)
            )
            row = cur.fetchone()
            return self._parse_row(row_to_dict(row))

    def get_tenant_by_slug(self, slug: str) -> Optional[Dict[str, Any]]:
        """Get a tenant by slug."""
        from ..core.db import transaction, PH, row_to_dict
        with transaction() as (_conn, cur):
            cur.execute(
                f"SELECT * FROM tenants WHERE slug = {PH}", (slug,)
            )
            row = cur.fetchone()
            return self._parse_row(row_to_dict(row))

    def list_tenants(self) -> List[Dict[str, Any]]:
        """List all tenants."""
        from ..core.db import transaction, row_to_dict
        with transaction() as (_conn, cur):
            cur.execute("SELECT * FROM tenants ORDER BY created_at")
            rows = cur.fetchall()
            return [
                self._parse_row(row_to_dict(r))
                for r in rows
                if row_to_dict(r) is not None
            ]

    def update_tenant(self, tenant_id: str, **updates) -> Optional[Dict[str, Any]]:
        """Update tenant fields. Allowed: name, plan, settings, is_active."""
        import json
        from ..core.db import transaction, PH

        allowed = {"name", "plan", "settings", "is_active"}
        filtered = {k: v for k, v in updates.items() if k in allowed}
        if not filtered:
            return self.get_tenant(tenant_id)

        # Serialize settings to JSON if present
        if "settings" in filtered and isinstance(filtered["settings"], dict):
            filtered["settings"] = json.dumps(filtered["settings"])

        now = datetime.now(timezone.utc).isoformat()
        filtered["updated_at"] = now

        set_clauses = ", ".join(f"{k} = {PH}" for k in filtered)
        values = list(filtered.values()) + [tenant_id]

        with transaction() as (_conn, cur):
            cur.execute(
                f"UPDATE tenants SET {set_clauses} WHERE id = {PH}",
                tuple(values),
            )

        return self.get_tenant(tenant_id)

    def suspend_tenant(self, tenant_id: str, reason: str = "") -> Optional[Dict[str, Any]]:
        """Suspend a tenant (block all access)."""
        from ..core.db import transaction, PH

        now = datetime.now(timezone.utc).isoformat()
        with transaction() as (_conn, cur):
            cur.execute(
                f"UPDATE tenants SET suspended = {PH}, suspend_reason = {PH}, "
                f"updated_at = {PH} WHERE id = {PH}",
                (True if self._is_pg else 1, reason, now, tenant_id),
            )

        logger.warning("Tenant %s suspended: %s", tenant_id, reason)
        return self.get_tenant(tenant_id)

    def get_or_create_default(self) -> Dict[str, Any]:
        """Get or create the default 'dev' tenant (for development mode)."""
        existing = self.get_tenant_by_slug(DEFAULT_TENANT_SLUG)
        if existing:
            return existing

        return self.create_tenant(
            name=DEFAULT_TENANT_NAME,
            slug=DEFAULT_TENANT_SLUG,
            plan="free",
        )

    # ── Middleware helper ────────────────────────────────────────────────────

    def ensure_tenant_context(self, tenant_id: str) -> Dict[str, Any]:
        """Validate that the tenant exists and is active. Returns tenant dict.

        Raises HTTPException if tenant is not found or suspended.
        Called by middleware to set the current tenant on the request.
        """
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Tenant not found")
        if tenant.get("suspended"):
            raise HTTPException(
                status_code=403,
                detail=f"Tenant suspended: {tenant.get('suspend_reason', 'Contact support')}",
            )
        if not tenant.get("is_active"):
            raise HTTPException(status_code=403, detail="Tenant is inactive")
        return tenant

    # ── Internal helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _parse_row(row: Optional[Dict]) -> Optional[Dict[str, Any]]:
        """Parse a raw DB row into a clean tenant dict."""
        if row is None:
            return None
        import json
        result = dict(row)
        # Parse settings JSON
        if isinstance(result.get("settings"), str):
            try:
                result["settings"] = json.loads(result["settings"])
            except (json.JSONDecodeError, TypeError):
                result["settings"] = {}
        # Normalize booleans (SQLite stores as 0/1)
        for key in ("is_active", "suspended"):
            if key in result:
                result[key] = bool(result[key])
        return result


# ── Tenant middleware ────────────────────────────────────────────────────────

_tenant_manager: Optional[TenantManager] = None


def _get_tenant_manager() -> TenantManager:
    """Lazy singleton for TenantManager."""
    global _tenant_manager
    if _tenant_manager is None:
        _tenant_manager = TenantManager()
    return _tenant_manager


def _is_production() -> bool:
    return os.environ.get("CONTEXTSYNAPSE_ENV") or os.environ.get("AICONTEXTDB_ENV", "").lower() == "production"


async def tenant_middleware(request: Request, call_next):
    """Extract tenant from JWT and set on request state.

    In dev mode: use default tenant.
    In production: extract from JWT, validate tenant is active.
    """
    # Skip tenant check for health/docs endpoints
    path = request.url.path
    if path in ("/health", "/docs", "/openapi.json", "/redoc"):
        response = await call_next(request)
        return response

    tm = _get_tenant_manager()

    if not _is_production():
        # Dev mode: use default tenant
        default = tm.get_or_create_default()
        request.state.tenant_id = default["id"]
        request.state.tenant = default
        response = await call_next(request)
        return response

    # Production: extract tenant from JWT claims (set by auth middleware)
    tenant_id = None

    # Check if auth middleware already set the tenant
    if hasattr(request.state, "tenant_id") and request.state.tenant_id:
        tenant_id = request.state.tenant_id
    else:
        # Try extracting from Authorization header JWT
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            token = auth_header[7:]
            try:
                from ..api.auth import verify_jwt
                payload = verify_jwt(token)
                if payload:
                    tenant_id = payload.get("tenant") or payload.get("tenant_id")
            except Exception:
                pass

    if not tenant_id:
        raise HTTPException(
            status_code=401,
            detail="Tenant context required. Include tenant in JWT.",
        )

    # Validate tenant is active and not suspended
    tenant = tm.ensure_tenant_context(tenant_id)
    request.state.tenant_id = tenant_id
    request.state.tenant = tenant

    response = await call_next(request)
    return response
