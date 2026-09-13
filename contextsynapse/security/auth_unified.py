"""UnifiedAuth — single auth dependency for all verticals.

Replaces per-vertical auth modules. JWT contains:
  - sub: user_id
  - tenant: tenant_id
  - roles: {pms: "fund_manager", mf: "compliance_officer"}
  - scoped_ids: {pms: ["pf_123"], mf: ["sch_456"]}

Usage:
    from contextsynapse.security.auth_unified import unified_auth, require_vertical_role

    @router.get("/portfolios")
    def list_portfolios(user = Depends(unified_auth("pms"))):
        # user has PMS-specific role and scoped_ids

    @router.post("/orders")
    def create_order(user = Depends(require_vertical_role("mf", "fund_manager", "dealer"))):
        ...

Backward compatible:
    from contextsynapse.security.auth_unified import pms_auth, mf_auth
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Set

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)

_bearer = HTTPBearer(auto_error=False)


# ── Unified identity ────────────────────────────────────────────────────────

@dataclass
class UnifiedIdentity:
    """Identity extracted from a JWT token — works across all verticals."""
    user_id: str
    tenant_id: str
    email: str = ""
    name: str = ""
    vertical_roles: Dict[str, str] = field(default_factory=dict)
    scoped_ids: Dict[str, List[str]] = field(default_factory=dict)
    current_vertical: str = ""
    current_role: str = ""

    @property
    def is_admin(self) -> bool:
        """Check if user has admin role in the current vertical."""
        return self.current_role == "admin"

    def has_role(self, vertical: str, *roles: str) -> bool:
        """Check if user has one of the specified roles in a vertical."""
        user_role = self.vertical_roles.get(vertical, "")
        return user_role in roles

    def get_scoped_ids(self, vertical: str) -> List[str]:
        """Get scoped IDs for a vertical (portfolio_ids, scheme_ids, etc.)."""
        return self.scoped_ids.get(vertical, [])


# ── RLS Filter ──────────────────────────────────────────────────────────────

@dataclass
class RLSFilter:
    """Row-Level Security filter for a specific vertical.

    Used to filter database queries to only return rows the user can access.
    """
    tenant_id: str
    vertical: str
    role: str
    scoped_ids: List[str] = field(default_factory=list)
    is_global_access: bool = False

    @property
    def has_scope_restriction(self) -> bool:
        """True if the user can only see specific scoped resources."""
        return bool(self.scoped_ids) and not self.is_global_access

    def filter_dict(self) -> Dict[str, Any]:
        """Return a dict suitable for query filtering."""
        result: Dict[str, Any] = {"tenant_id": self.tenant_id}
        if self.has_scope_restriction:
            result["scoped_ids"] = self.scoped_ids
        return result


# ── Role constants per vertical ─────────────────────────────────────────────

# Roles that have global access (can see all resources in the vertical)
GLOBAL_ACCESS_ROLES: Dict[str, Set[str]] = {
    "pms": {"admin", "compliance_officer"},
    "mf": {"admin", "compliance_officer"},
}

# Default dev roles per vertical
DEFAULT_DEV_ROLES: Dict[str, str] = {
    "pms": "admin",
    "mf": "admin",
}


# ── Helpers ─────────────────────────────────────────────────────────────────

def _is_production() -> bool:
    return os.environ.get("CONTEXTSYNAPSE_ENV") or os.environ.get("AICONTEXTDB_ENV", "").lower() == "production"


def _extract_unified_identity(payload: dict, vertical: str) -> UnifiedIdentity:
    """Build a UnifiedIdentity from a JWT payload for a specific vertical."""
    # Support both new-style (roles dict) and legacy (single role string)
    vertical_roles = payload.get("roles", {})
    scoped_ids = payload.get("scoped_ids", {})

    # Legacy fallback: single role field
    if not vertical_roles and "role" in payload:
        role_str = payload["role"]
        if hasattr(role_str, "value"):
            role_str = role_str.value
        vertical_roles = {vertical: role_str}

    # Legacy fallback: portfolio_ids / scheme_ids
    if not scoped_ids:
        if "portfolio_ids" in payload:
            scoped_ids["pms"] = payload["portfolio_ids"]
        if "scheme_ids" in payload:
            scoped_ids["mf"] = payload["scheme_ids"]

    current_role = vertical_roles.get(vertical, "")

    return UnifiedIdentity(
        user_id=payload.get("sub", ""),
        tenant_id=payload.get("tenant", payload.get("tenant_id", "")),
        email=payload.get("email", ""),
        name=payload.get("name", ""),
        vertical_roles=vertical_roles,
        scoped_ids=scoped_ids,
        current_vertical=vertical,
        current_role=current_role,
    )


def _dev_identity(vertical: str, role_header: str = "") -> UnifiedIdentity:
    """Create a dev-mode identity."""
    role = role_header or DEFAULT_DEV_ROLES.get(vertical, "admin")
    return UnifiedIdentity(
        user_id=f"dev_{role}",
        tenant_id="dev",
        email=f"dev_{role}@localhost",
        name=f"Dev {role.replace('_', ' ').title()}",
        vertical_roles={vertical: role},
        scoped_ids={},
        current_vertical=vertical,
        current_role=role,
    )


# ── Main auth dependency factory ────────────────────────────────────────────

def unified_auth(vertical: str) -> Callable:
    """FastAPI dependency factory — extracts JWT, resolves roles for a vertical.

    Args:
        vertical: The vertical to authenticate for ("pms", "mf", etc.)

    Returns:
        A FastAPI dependency that yields UnifiedIdentity.
    """

    async def _resolve(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer),
    ) -> UnifiedIdentity:
        # Try JWT first
        if credentials and credentials.credentials:
            try:
                from ..api.auth import verify_jwt
                payload = verify_jwt(credentials.credentials)
                if payload:
                    identity = _extract_unified_identity(payload, vertical)
                    if not identity.current_role:
                        raise HTTPException(
                            status_code=403,
                            detail=f"No role assigned for vertical '{vertical}'",
                        )
                    request.state.unified_user = identity
                    request.state.tenant_id = identity.tenant_id
                    return identity
            except HTTPException:
                raise
            except Exception as exc:
                logger.debug("JWT extraction failed: %s", exc)

            raise HTTPException(status_code=401, detail="Invalid or expired token")

        # Dev mode fallback
        if not _is_production():
            role_header = request.headers.get("X-User-Role", "")
            identity = _dev_identity(vertical, role_header)
            request.state.unified_user = identity
            request.state.tenant_id = identity.tenant_id
            return identity

        # Production: no token, no access
        raise HTTPException(
            status_code=401,
            detail="Authentication required. Please sign in to continue.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return _resolve


def require_vertical_role(vertical: str, *roles: str) -> Callable:
    """Dependency that checks if user has one of the specified roles in a vertical.

    Args:
        vertical: The vertical to check ("pms", "mf", etc.)
        *roles: Allowed role names (e.g. "fund_manager", "admin")

    Returns:
        A FastAPI dependency that yields UnifiedIdentity (or raises 403).
    """
    allowed = set(roles)

    async def _check(
        user: UnifiedIdentity = Depends(unified_auth(vertical)),
    ) -> UnifiedIdentity:
        if user.current_role not in allowed:
            raise HTTPException(
                status_code=403,
                detail=f"Access denied. Required role: {', '.join(roles)}",
            )
        return user

    return _check


def get_rls(vertical: str) -> Callable:
    """Dependency that returns an RLSFilter configured for the user's scope.

    The RLSFilter can be passed to database queries to enforce row-level security.

    Args:
        vertical: The vertical to scope ("pms", "mf", etc.)

    Returns:
        A FastAPI dependency that yields RLSFilter.
    """

    async def _resolve(
        user: UnifiedIdentity = Depends(unified_auth(vertical)),
    ) -> RLSFilter:
        global_roles = GLOBAL_ACCESS_ROLES.get(vertical, set())
        is_global = user.current_role in global_roles

        return RLSFilter(
            tenant_id=user.tenant_id,
            vertical=vertical,
            role=user.current_role,
            scoped_ids=user.get_scoped_ids(vertical),
            is_global_access=is_global,
        )

    return _resolve


# ── Backward-compatible aliases ─────────────────────────────────────────────
# These allow existing code to keep importing from per-vertical auth modules
# while actually using the unified system.

# PMS backward compat
pms_auth = unified_auth("pms")
pms_require_role = lambda *roles: require_vertical_role("pms", *roles)

# MF backward compat
mf_auth = unified_auth("mf")
mf_require_role = lambda *roles: require_vertical_role("mf", *roles)
