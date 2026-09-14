"""Tenant Onboarding — platform-level tenant lifecycle.

Flow:
  1. Superadmin creates tenant (name, plan, deployment mode)
  2. Platform sets up vertical (PMS/MF) with templates + rules
  3. Creates tenant admin user
  4. Loads stock universe for tenant
  5. Tenant admin takes over — creates team, clients, portfolios

Usage:
    from contextsynapse.core.tenant_onboard import TenantEngine

    engine = TenantEngine()

    # Superadmin creates a new PMS firm
    result = engine.create_tenant(
        name="Marcellus Capital",
        slug="marcellus",
        plan="professional",
        vertical="pms",
        admin_email="admin@marcellus.com",
        admin_password="SecurePass123!",
        branding={"app_name": "Marcellus PMS", "primary_color": "#1a365d"},
        sebi_registration="PMS/INV/2024/12345",
    )

    # List tenants
    engine.list_tenants()

    # Get tenant config
    engine.get_tenant("ten_abc")
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# Plan limits
PLAN_LIMITS = {
    "free": {
        "max_clients": 5, "max_portfolios": 5, "max_stocks": 10,
        "max_users": 3, "max_api_rpm": 60, "storage_gb": 1,
        "features": ["fusion"],
    },
    "starter": {
        "max_clients": 50, "max_portfolios": 50, "max_stocks": 30,
        "max_users": 10, "max_api_rpm": 120, "storage_gb": 10,
        "features": ["fusion", "rules", "reports"],
    },
    "professional": {
        "max_clients": 500, "max_portfolios": 500, "max_stocks": 100,
        "max_users": 20, "max_api_rpm": 300, "storage_gb": 50,
        "features": ["fusion", "rules", "reports", "backtesting", "global_events",
                      "mf_lab", "ai_skills", "custom_sensors", "api_access"],
    },
    "enterprise": {
        "max_clients": -1, "max_portfolios": -1, "max_stocks": -1,
        "max_users": -1, "max_api_rpm": 1000, "storage_gb": 500,
        "features": ["*"],  # everything
    },
}

DEFAULT_BRANDING = {
    "app_name": "PMS",
    "logo_url": "",
    "primary_color": "#5b8af0",
    "accent_color": "#22c55e",
    "tagline": "Alpha Release",
    "favicon_url": "",
}


class TenantEngine:
    """Manages tenant lifecycle — create, configure, suspend, delete."""

    def create_tenant(
        self,
        name: str,
        slug: str,
        plan: str = "professional",
        vertical: str = "pms",
        admin_email: str = "",
        admin_password: str = "Admin123!",
        admin_name: str = "",
        branding: dict = None,
        sebi_registration: str = "",
        domain: str = "",
        deployment_mode: str = "saas",
    ) -> dict[str, Any]:
        """Create a new tenant with vertical, admin user, and stock universe.

        Returns full tenant setup result.
        """
        tenant_id = str(uuid.uuid4())
        now = datetime.now(timezone.utc).isoformat()
        plan_limits = PLAN_LIMITS.get(plan, PLAN_LIMITS["professional"])

        result = {
            "tenant_id": tenant_id,
            "name": name,
            "slug": slug,
            "plan": plan,
            "vertical": vertical,
            "steps": {},
        }

        # ── 1. Create tenant record ─────────────────────────────
        try:
            from contextsynapse.db.postgres import get_connection
            with get_connection() as conn:
                cur = conn.cursor()
                settings = {
                    "plan": plan,
                    "vertical": vertical,
                    "deployment_mode": deployment_mode,
                    "domain": domain or f"{slug}.contextsynapse.com",
                    "sebi_registration": sebi_registration,
                    "branding": {**DEFAULT_BRANDING, **(branding or {})},
                    "limits": plan_limits,
                    "features": plan_limits.get("features", []),
                    "created_at": now,
                }
                cur.execute("""
                    INSERT INTO tenants (id, name, slug, plan, status, settings)
                    VALUES (%s, %s, %s, %s, 'active', %s)
                    ON CONFLICT (slug) DO UPDATE SET name = EXCLUDED.name, settings = EXCLUDED.settings
                    RETURNING id
                """, (tenant_id, name, slug, plan, json.dumps(settings)))
                row = cur.fetchone()
                tenant_id = str(row[0])
                result["tenant_id"] = tenant_id
                result["steps"]["create_tenant"] = "done"
        except Exception as e:
            result["steps"]["create_tenant"] = f"failed: {e}"
            return result

        # ── 2. Create admin user ────────────────────────────────
        if admin_email:
            try:
                import bcrypt
                from contextsynapse.db.postgres import get_connection
                user_id = str(uuid.uuid4())
                pw_hash = bcrypt.hashpw(admin_password.encode(), bcrypt.gensalt()).decode()
                display_name = admin_name or f"{name} Admin"

                with get_connection() as conn:
                    cur = conn.cursor()
                    cur.execute("""
                        INSERT INTO users (id, tenant_id, email, name, password_hash, status,
                                           user_id, display_name, is_super_admin)
                        VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, 0)
                        ON CONFLICT (email) DO UPDATE SET tenant_id = EXCLUDED.tenant_id
                        RETURNING id
                    """, (user_id, tenant_id, admin_email, display_name, pw_hash,
                          user_id, display_name))
                    uid = str(cur.fetchone()[0])

                    # Assign pms_admin role scoped to this tenant
                    cur.execute("""
                        INSERT INTO user_roles (user_id, tenant_id, vertical, role, scoped_ids)
                        VALUES (%s, %s, %s, 'pms_admin', '{}')
                        ON CONFLICT DO NOTHING
                    """, (uid, tenant_id, vertical))

                result["admin_user_id"] = uid
                result["admin_email"] = admin_email
                result["steps"]["create_admin"] = "done"
            except Exception as e:
                result["steps"]["create_admin"] = f"failed: {e}"

        # ── 3. Setup vertical (register roles, workflows, PII) ──
        try:
            from verticals.pms.backend.register import register_pms_vertical
            register_pms_vertical()
            result["steps"]["register_vertical"] = "done"
        except Exception as e:
            result["steps"]["register_vertical"] = f"failed: {e}"

        # ── 4. Load stock universe ──────────────────────────────
        try:
            from contextsynapse.db.stock_master import StockMaster
            sm = StockMaster()
            count = sm.count()
            if count == 0:
                count = sm.load_nse_listing()
                result["steps"]["stock_universe"] = f"loaded {count}"
            else:
                result["steps"]["stock_universe"] = f"already loaded ({count})"
        except Exception as e:
            result["steps"]["stock_universe"] = f"failed: {e}"

        # ── 5. Run DB migrations ────────────────────────────────
        try:
            from contextsynapse.db.postgres import run_migrations
            run_migrations()
            result["steps"]["migrations"] = "done"
        except Exception as e:
            result["steps"]["migrations"] = f"failed: {e}"

        result["status"] = "active"
        result["branding"] = {**DEFAULT_BRANDING, **(branding or {})}
        result["domain"] = domain or f"{slug}.contextsynapse.com"
        result["limits"] = plan_limits

        logger.info("[TENANT] Created: %s (%s) plan=%s vertical=%s admin=%s",
                     name, tenant_id[:8], plan, vertical, admin_email)
        return result

    def get_tenant(self, tenant_id: str = "", slug: str = "") -> dict | None:
        """Get tenant by ID or slug."""
        try:
            from contextsynapse.db.postgres import execute_one
            if tenant_id:
                row = execute_one("SELECT * FROM tenants WHERE id = %s", (tenant_id,))
            elif slug:
                row = execute_one("SELECT * FROM tenants WHERE slug = %s", (slug,))
            else:
                return None
            if row:
                settings = row.get("settings", {})
                if isinstance(settings, str):
                    settings = json.loads(settings)
                row["settings"] = settings
                row["id"] = str(row["id"])
            return row
        except Exception:
            return None

    def list_tenants(self) -> list[dict]:
        """List all tenants."""
        try:
            from contextsynapse.db.postgres import execute
            rows = execute("SELECT id, name, slug, plan, status, created_at FROM tenants ORDER BY created_at DESC")
            for r in rows:
                r["id"] = str(r["id"])
            return rows
        except Exception:
            return []

    def update_tenant(self, tenant_id: str, updates: dict) -> dict | None:
        """Update tenant settings (plan, branding, limits)."""
        tenant = self.get_tenant(tenant_id)
        if not tenant:
            return None

        settings = tenant.get("settings", {})
        settings.update(updates)

        try:
            from contextsynapse.db.postgres import get_connection
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("""
                    UPDATE tenants SET settings = %s, updated_at = now()
                    WHERE id = %s RETURNING *
                """, (json.dumps(settings, default=str), tenant_id))
                return dict(cur.fetchone()) if cur.rowcount else None
        except Exception:
            return None

    def suspend_tenant(self, tenant_id: str) -> bool:
        """Suspend a tenant (data preserved, access blocked)."""
        try:
            from contextsynapse.db.postgres import get_connection
            with get_connection() as conn:
                cur = conn.cursor()
                cur.execute("UPDATE tenants SET status = 'suspended' WHERE id = %s", (tenant_id,))
                return cur.rowcount > 0
        except Exception:
            return False

    def get_tenant_stats(self, tenant_id: str) -> dict:
        """Get usage stats for a tenant."""
        try:
            from contextsynapse.db.postgres import execute_one
            clients = execute_one(
                "SELECT COUNT(*) as c FROM pms_clients WHERE tenant_id = %s AND status = 'active'",
                (tenant_id,)
            )
            portfolios = execute_one(
                "SELECT COUNT(*) as c FROM pms_portfolios WHERE tenant_id = %s AND status = 'active'",
                (tenant_id,)
            )
            users = execute_one(
                "SELECT COUNT(*) as c FROM users WHERE tenant_id = %s AND status = 'active'",
                (tenant_id,)
            )
            return {
                "clients": (clients or {}).get("c", 0),
                "portfolios": (portfolios or {}).get("c", 0),
                "users": (users or {}).get("c", 0),
            }
        except Exception:
            return {"clients": 0, "portfolios": 0, "users": 0}

    def get_branding(self, tenant_id: str = "", slug: str = "") -> dict:
        """Get white-label branding for a tenant."""
        tenant = self.get_tenant(tenant_id, slug)
        if not tenant:
            return DEFAULT_BRANDING
        settings = tenant.get("settings", {})
        return settings.get("branding", DEFAULT_BRANDING)
