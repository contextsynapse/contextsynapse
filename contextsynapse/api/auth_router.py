"""
Auth Router
===========
Self-service user authentication endpoints for the SaaS layer.

POST /auth/signup   — Create user + personal tenant, return JWT
POST /auth/login    — Email + password -> JWT
GET  /auth/me       — Current user profile + tenant info
PATCH /auth/me      — Update display name / password
POST /auth/refresh  — Refresh JWT

Role & user management (admin/owner only):
POST  /auth/assign-role          — Assign a vertical role to a user
POST  /auth/revoke-role          — Revoke a vertical role from a user
GET   /auth/users                — List all users in the current tenant
GET   /auth/users/{user_id}/roles — Get user's roles across verticals
PATCH /auth/users/{user_id}/scope — Update scoped_ids for a user's role
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from .auth import UserAuth, create_jwt
from .tenants import TenantRegistry
from .users import UserRegistry

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Lazy IdentityService loader (depends on db module which may not be present)
# ---------------------------------------------------------------------------

_identity_service = None


def _get_identity_service():
    """Lazily initialise and return the IdentityService singleton."""
    global _identity_service
    if _identity_service is None:
        try:
            from contextsynapse.security.identity import IdentityService

            _identity_service = IdentityService()
        except Exception as exc:
            logger.warning("IdentityService unavailable: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="Identity service is not available. Check server configuration.",
            )
    return _identity_service


# ---------------------------------------------------------------------------
# Request / Response models
# ---------------------------------------------------------------------------


class SignupRequest(BaseModel):
    email: str = Field(..., description="User email address")
    password: str = Field(..., min_length=6, description="Password (min 8 chars, 1 upper, 1 lower, 1 digit)")
    display_name: str = Field(..., min_length=1, description="Display name")


class VerifyEmailRequest(BaseModel):
    token: str = Field(..., description="Email verification token")


class LoginRequest(BaseModel):
    email: str
    password: str


class UpdateProfileRequest(BaseModel):
    display_name: str | None = None
    password: str | None = None


class AuthResponse(BaseModel):
    token: str
    user: dict
    tenant_id: str


class AssignRoleRequest(BaseModel):
    user_id: str = Field(..., description="Target user ID")
    vertical: str = Field(..., description="Vertical slug (pms, mf, platform)")
    role: str = Field(..., description="Role name to assign")
    scoped_ids: list[str] | None = Field(
        None, description="Optional list of scoped entity IDs (portfolio_ids, scheme_ids, etc.)"
    )


class RevokeRoleRequest(BaseModel):
    user_id: str = Field(..., description="Target user ID")
    vertical: str = Field(..., description="Vertical slug (pms, mf, platform)")
    role: str = Field(..., description="Role name to revoke")


class UpdateScopeRequest(BaseModel):
    vertical: str = Field(..., description="Vertical slug (pms, mf, platform)")
    role: str = Field(..., description="Role name whose scope to update")
    scoped_ids: list[str] = Field(..., description="New list of scoped entity IDs")


class UpdateProfileSettingsRequest(BaseModel):
    display_name: str | None = None
    title: str | None = None
    bio: str | None = None
    phone: str | None = None
    linkedin_url: str | None = None
    sebi_registration_no: str | None = None
    arn_number: str | None = None
    nism_certification: str | None = None
    experience_years: int | None = None
    specialization: str | None = None
    investment_philosophy: str | None = None
    preferred_sectors: list[str] | None = None
    risk_appetite: str | None = None
    benchmark: str | None = None
    notification_preferences: dict | None = None
    timezone: str | None = None


# ---------------------------------------------------------------------------
# Lazy ProfileStore loader
# ---------------------------------------------------------------------------

_profile_store = None


def _get_profile_store():
    """Lazily initialise and return the ProfileStore singleton."""
    global _profile_store
    if _profile_store is None:
        try:
            from contextsynapse.db.profile import ProfileStore

            _profile_store = ProfileStore()
        except Exception as exc:
            logger.warning("ProfileStore unavailable: %s", exc)
            raise HTTPException(
                status_code=503,
                detail="Profile service is not available. Check server configuration.",
            )
    return _profile_store


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------


def create_auth_router(
    user_registry: UserRegistry,
    tenant_registry: TenantRegistry,
) -> APIRouter:
    """Create the /auth router with injected registries."""

    router = APIRouter(prefix="/auth", tags=["auth"])
    user_auth = UserAuth(user_registry)

    def _make_token(user, role: str = "fund_manager") -> str:
        return create_jwt({"type": "user", "sub": user.user_id, "email": user.email, "role": role})

    def _make_token_pair(user) -> tuple:
        """Create access + refresh token pair."""
        try:
            from contextsynapse.security.jwt_refresh import create_token_pair
            # Include role in JWT claims so pms_auth can extract it
            jwt_role = role_data.get("role", "fund_manager") if role_data else "fund_manager"
            return create_token_pair(user.user_id, {
                "email": user.email, "type": "user", "role": jwt_role,
            })
        except Exception:
            return _make_token(user), ""

    # ------------------------------------------------------------------
    # Helper: assert caller is owner or admin on a tenant
    # ------------------------------------------------------------------
    def _require_admin_or_owner(user, tenant_id: str):
        """Raise 403 if the authenticated user is not owner/admin on *tenant_id*."""
        # Super-admins always pass
        if getattr(user, "is_super_admin", False):
            return
        memberships = user_registry.get_user_tenants(user.user_id)
        for m in memberships:
            if m.tenant_id == tenant_id and m.role in ("owner", "admin"):
                return
        raise HTTPException(
            status_code=403,
            detail="Only tenant owners or admins can manage roles.",
        )

    def _resolve_tenant_id(user) -> str:
        """Return the caller's primary tenant_id or raise 400."""
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id:
            raise HTTPException(status_code=400, detail="No tenant associated with your account.")
        return tenant_id

    # ------------------------------------------------------------------
    # POST /auth/signup
    # ------------------------------------------------------------------
    @router.post("/signup", response_model=AuthResponse)
    async def signup(req: SignupRequest):
        """Create a new user account with a personal workspace.

        In production (AICONTEXTDB_ENV != dev), an email verification token
        is created. The account works immediately but email_verified=false
        until POST /auth/verify-email is called.
        """
        try:
            user = user_registry.create(req.email, req.password, req.display_name)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        # Auto-create a personal tenant (workspace)
        tenant_name = f"{req.display_name}'s workspace"
        try:
            tenant, api_key = tenant_registry.create(tenant_name)
        except Exception:
            tenant_name = f"{tenant_name} ({user.user_id[:8]})"
            tenant, api_key = tenant_registry.create(tenant_name)

        # Link user -> tenant as owner
        user_registry.link_tenant(user.user_id, tenant.tenant_id, role="owner")

        # Create email verification token
        verify_token = None
        try:
            verify_token = user_registry.create_verification_token(user.user_id)
        except Exception as vt_err:
            logger.warning("Failed to create verification token: %s", vt_err)

        token = _make_token(user)
        logger.info("New user signup: %s -> tenant %s", user.email, tenant.tenant_id)

        response_user = {
            **user.to_dict(),
            "tenant_id": tenant.tenant_id,
            "tenant_name": tenant.name,
            "tenant_api_key": api_key,
            "email_verified": False,
        }
        if verify_token:
            response_user["verification_token"] = verify_token

        return AuthResponse(
            token=token,
            user=response_user,
            tenant_id=tenant.tenant_id,
        )

    # ------------------------------------------------------------------
    # POST /auth/login
    # ------------------------------------------------------------------
    @router.post("/login", response_model=AuthResponse)
    async def login(req: LoginRequest, request: Request):
        """Authenticate with email and password (with brute-force protection)."""
        ip = request.client.host if request.client else None
        try:
            user = user_registry.authenticate(req.email, req.password, ip_address=ip)
        except ValueError as e:
            # Account locked out
            raise HTTPException(status_code=429, detail=str(e))
        if user is None:
            raise HTTPException(status_code=401, detail="Invalid email or password")

        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        tenant = tenant_registry.get(tenant_id) if tenant_id else None

        # Fetch roles from user_roles table (multi-role support)
        role_data = {}
        try:
            from contextsynapse.db.postgres import execute as db_execute

            role_rows = db_execute(
                "SELECT role, vertical, scoped_ids FROM user_roles WHERE user_id = %s",
                (user.user_id,),
            )
            if role_rows:
                from contextsynapse.security.rbac import RBACManager

                rbac = RBACManager()
                all_roles = [r["role"] for r in role_rows]
                # Merge scoped_ids from all role entries
                all_scoped = []
                for r in role_rows:
                    ids = r.get("scoped_ids") or []
                    if isinstance(ids, list):
                        all_scoped.extend(ids)
                # Get delegated client IDs
                delegated_ids = []
                try:
                    from contextsynapse.workflow.delegation import DelegationManager

                    delegated_ids = DelegationManager().get_delegated_client_ids(user.user_id)
                except Exception:
                    pass
                # Merge permissions from all roles
                merged_perms = rbac.get_effective_permissions(all_roles)
                role_data = {
                    "role": all_roles[0],  # primary role (backward compat)
                    "roles": all_roles,  # all roles
                    "vertical": role_rows[0].get("vertical", "pms"),
                    "scoped_ids": list(set(all_scoped)),
                    "delegated_ids": delegated_ids,
                    "permissions": sorted(p.value for p in merged_perms),
                    "is_multi_role": len(all_roles) > 1,
                }
        except Exception:
            pass

        access_token, refresh_token = _make_token_pair(user)
        return AuthResponse(
            token=access_token,
            user={
                **user.to_dict(),
                "refresh_token": refresh_token,
                "tenant_id": tenant_id or "",
                "tenant_name": tenant.name if tenant else "",
                **role_data,
            },
            tenant_id=tenant_id or "",
        )

    # ------------------------------------------------------------------
    # POST /auth/refresh — get new access token from refresh token
    # ------------------------------------------------------------------
    @router.post("/refresh")
    async def refresh_token(request: Request):
        """Exchange a refresh token for a new access token."""
        body = await request.json()
        rt = body.get("refresh_token", "")
        if not rt:
            raise HTTPException(400, "refresh_token required")
        try:
            from contextsynapse.security.jwt_refresh import refresh_access_token
            new_token = refresh_access_token(rt)
            if not new_token:
                raise HTTPException(401, "Invalid or expired refresh token")
            return {"token": new_token}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(401, str(e))

    # ------------------------------------------------------------------
    # GET /auth/me
    # ------------------------------------------------------------------
    @router.get("/me")
    async def get_me(user=Depends(user_auth)):
        """Get current user profile and tenant memberships."""
        memberships = user_registry.get_user_tenants(user.user_id)
        tenants = []
        for m in memberships:
            t = tenant_registry.get(m.tenant_id)
            if t:
                tenants.append(
                    {
                        "tenant_id": t.tenant_id,
                        "name": t.name,
                        "role": m.role,
                        "status": t.status,
                    }
                )

        return {
            **user.to_dict(),
            "tenants": tenants,
        }

    # ------------------------------------------------------------------
    # PATCH /auth/me
    # ------------------------------------------------------------------
    @router.patch("/me")
    async def update_me(req: UpdateProfileRequest, user=Depends(user_auth)):
        """Update current user's profile."""
        try:
            updated = user_registry.update(
                user.user_id,
                display_name=req.display_name,
                password=req.password,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not updated:
            raise HTTPException(status_code=404, detail="User not found")

        return updated.to_dict()

    # ------------------------------------------------------------------
    # POST /auth/refresh
    # ------------------------------------------------------------------
    @router.post("/refresh")
    async def refresh_token(user=Depends(user_auth)):
        """Get a fresh JWT (extends session)."""
        token = _make_token(user)
        return {"token": token}

    # ------------------------------------------------------------------
    # POST /auth/verify-email
    # ------------------------------------------------------------------
    @router.post("/verify-email")
    async def verify_email(req: VerifyEmailRequest):
        """Verify email address with the token from signup."""
        user_id = user_registry.verify_email(req.token)
        if not user_id:
            raise HTTPException(status_code=400, detail="Invalid or expired verification token")
        return {"status": "verified", "user_id": user_id}

    # ------------------------------------------------------------------
    # GET /auth/password-requirements
    # ------------------------------------------------------------------
    @router.get("/password-requirements")
    async def password_requirements():
        """Return current password requirements (varies by env)."""
        import os

        is_dev = os.environ.get("CONTEXTSYNAPSE_ENV") or os.environ.get("AICONTEXTDB_ENV", "").lower() in (
            "dev",
            "development",
            "test",
        )
        return {
            "min_length": 6 if is_dev else 8,
            "require_uppercase": not is_dev,
            "require_lowercase": not is_dev,
            "require_digit": not is_dev,
            "no_common_passwords": True,
            "mode": "development" if is_dev else "production",
        }

    # ==================================================================
    # Role & user management endpoints (admin/owner only)
    # ==================================================================

    # ------------------------------------------------------------------
    # POST /auth/assign-role
    # ------------------------------------------------------------------
    @router.post("/assign-role")
    async def assign_role(req: AssignRoleRequest, user=Depends(user_auth)):
        """Assign a vertical role to a user. Only tenant owners/admins can do this."""
        tenant_id = _resolve_tenant_id(user)
        _require_admin_or_owner(user, tenant_id)

        identity = _get_identity_service()

        # Verify target user exists
        target = identity.get_user(req.user_id)
        if not target:
            raise HTTPException(status_code=404, detail=f"User {req.user_id} not found.")

        try:
            result = identity.assign_role(
                user_id=req.user_id,
                tenant_id=tenant_id,
                vertical=req.vertical,
                role=req.role,
                scoped_ids=req.scoped_ids,
                granted_by=user.user_id,
            )
        except Exception as e:
            logger.error("Failed to assign role: %s", e)
            raise HTTPException(status_code=400, detail=str(e))

        return {"status": "assigned", "role": result}

    # ------------------------------------------------------------------
    # POST /auth/revoke-role
    # ------------------------------------------------------------------
    @router.post("/revoke-role")
    async def revoke_role(req: RevokeRoleRequest, user=Depends(user_auth)):
        """Revoke a vertical role from a user. Only tenant owners/admins can do this."""
        tenant_id = _resolve_tenant_id(user)
        _require_admin_or_owner(user, tenant_id)

        identity = _get_identity_service()

        deleted = identity.revoke_role(
            user_id=req.user_id,
            tenant_id=tenant_id,
            vertical=req.vertical,
            role=req.role,
        )
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=f"Role {req.vertical}/{req.role} not found for user {req.user_id}.",
            )

        return {"status": "revoked", "user_id": req.user_id, "vertical": req.vertical, "role": req.role}

    # ------------------------------------------------------------------
    # GET /auth/users
    # ------------------------------------------------------------------
    @router.get("/users")
    async def list_users(user=Depends(user_auth)):
        """List all users in the caller's tenant with their vertical roles."""
        tenant_id = _resolve_tenant_id(user)
        _require_admin_or_owner(user, tenant_id)

        identity = _get_identity_service()
        users = identity.list_users(tenant_id)

        return {"users": users, "tenant_id": tenant_id}

    # ------------------------------------------------------------------
    # GET /auth/users/{user_id}/roles
    # ------------------------------------------------------------------
    @router.get("/users/{user_id}/roles")
    async def get_user_roles(user_id: str, user=Depends(user_auth)):
        """Get a user's roles across all verticals in the caller's tenant."""
        tenant_id = _resolve_tenant_id(user)
        _require_admin_or_owner(user, tenant_id)

        identity = _get_identity_service()

        target = identity.get_user(user_id)
        if not target:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

        roles = identity.get_user_roles(user_id, tenant_id=tenant_id)

        return {"user_id": user_id, "tenant_id": tenant_id, "roles": roles}

    # ------------------------------------------------------------------
    # PATCH /auth/users/{user_id}/scope
    # ------------------------------------------------------------------
    @router.patch("/users/{user_id}/scope")
    async def update_scope(user_id: str, req: UpdateScopeRequest, user=Depends(user_auth)):
        """Update scoped_ids for a user's role in a specific vertical.

        This revokes the existing role and re-assigns it with the new scoped_ids.
        """
        tenant_id = _resolve_tenant_id(user)
        _require_admin_or_owner(user, tenant_id)

        identity = _get_identity_service()

        target = identity.get_user(user_id)
        if not target:
            raise HTTPException(status_code=404, detail=f"User {user_id} not found.")

        # Check the role exists before updating
        existing_roles = identity.get_user_roles(user_id, tenant_id=tenant_id, vertical=req.vertical)
        role_exists = any(r["role"] == req.role for r in existing_roles)
        if not role_exists:
            raise HTTPException(
                status_code=404,
                detail=f"Role {req.vertical}/{req.role} not found for user {user_id}.",
            )

        # Revoke and re-assign with new scoped_ids
        identity.revoke_role(user_id, tenant_id, req.vertical, req.role)
        result = identity.assign_role(
            user_id=user_id,
            tenant_id=tenant_id,
            vertical=req.vertical,
            role=req.role,
            scoped_ids=req.scoped_ids,
            granted_by=user.user_id,
        )

        return {"status": "updated", "user_id": user_id, "role": result}

    # ==================================================================
    # Fund Manager Profile endpoints
    # ==================================================================

    # ------------------------------------------------------------------
    # GET /auth/profile — current user's profile
    # ------------------------------------------------------------------
    @router.get("/profile")
    async def get_profile(user=Depends(user_auth)):
        """Get the current user's fund manager profile (creates one if missing)."""
        store = _get_profile_store()
        tenant_id = _resolve_tenant_id(user)
        profile = store.get_or_create(user.user_id, tenant_id, user.name or user.email)
        return profile

    # ------------------------------------------------------------------
    # PUT /auth/profile — update current user's profile
    # ------------------------------------------------------------------
    @router.put("/profile")
    async def update_profile(req: UpdateProfileSettingsRequest, user=Depends(user_auth)):
        """Update the current user's fund manager profile."""
        store = _get_profile_store()
        tenant_id = _resolve_tenant_id(user)

        # Ensure profile exists
        store.get_or_create(user.user_id, tenant_id, user.name or user.email)

        updates = req.dict(exclude_none=True)
        if not updates:
            raise HTTPException(status_code=400, detail="No fields to update.")

        updated = store.update_profile(user.user_id, **updates)
        if not updated:
            raise HTTPException(status_code=404, detail="Profile not found.")

        return updated

    # ------------------------------------------------------------------
    # GET /auth/profile/{user_id} — admin view of another user's profile
    # ------------------------------------------------------------------
    @router.get("/profile/{user_id}")
    async def get_user_profile(user_id: str, user=Depends(user_auth)):
        """Get another user's fund manager profile (admin only)."""
        tenant_id = _resolve_tenant_id(user)
        _require_admin_or_owner(user, tenant_id)

        store = _get_profile_store()
        profile = store.get_profile(user_id)
        if not profile:
            raise HTTPException(status_code=404, detail="Profile not found for this user.")

        return profile

    return router
