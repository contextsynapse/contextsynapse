"""
Authentication Middleware
=========================
Provides FastAPI dependencies for:

- ``UserAuth``    — validates ``Authorization: Bearer <jwt>`` with a user JWT.
  Returns a ``UserIdentity``.
- ``TenantAuth``  — validates ``Authorization: Bearer <tenant_api_key>`` on
  all data routes.  Returns a ``TenantIdentity``.
- ``AgentAuth``   — validates ``Authorization: Bearer <agent_id>:<secret>`` on
  ``/context/`` endpoints.  Returns an ``AgentIdentity``.
- ``AdminAuth``   — validates ``X-Admin-Key`` header against the
  ``AICONTEXTDB_ADMIN_KEY`` env var.  Used on ``/admin/`` routes.
"""

import hashlib
import hmac
import json
import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

logger = logging.getLogger(__name__)


def _get_jwt_secret() -> str:
    """JWT signing secret.  Falls back to AICONTEXTDB_ADMIN_KEY for compat.

    Raises ``RuntimeError`` if neither env var is set, preventing the server
    from starting with an insecure default.
    """
    secret = os.environ.get("CONTEXTSYNAPSE_JWT_SECRET") or os.environ.get("AICONTEXTDB_JWT_SECRET") or os.environ.get("CONTEXTSYNAPSE_ADMIN_KEY") or os.environ.get("AICONTEXTDB_ADMIN_KEY")
    if not secret:
        raise RuntimeError(
            "AICONTEXTDB_JWT_SECRET (or AICONTEXTDB_ADMIN_KEY) must be set. "
            "Generate one with: python -c \"import secrets; print(secrets.token_urlsafe(32))\""
        )
    return secret


# ======================================================================
# Lightweight JWT helpers (HMAC-SHA256, no external dependency)
# ======================================================================

def _b64url_encode(data: bytes) -> str:
    import base64
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _b64url_decode(s: str) -> bytes:
    import base64
    s += "=" * (4 - len(s) % 4)
    return base64.urlsafe_b64decode(s)


def create_jwt(payload: Dict[str, Any], secret: Optional[str] = None,
               expires_in: int = 86400) -> str:
    """Create a signed JWT with arbitrary payload."""
    secret = secret or _get_jwt_secret()
    payload = {**payload, "iat": int(time.time()), "exp": int(time.time()) + expires_in}
    header = _b64url_encode(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    payload_b64 = _b64url_encode(json.dumps(payload).encode())
    signing_input = f"{header}.{payload_b64}"
    sig = hmac.new(secret.encode(), signing_input.encode(), hashlib.sha256).digest()
    return f"{signing_input}.{_b64url_encode(sig)}"


def verify_jwt(token: str, secret: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """Verify a JWT and return the payload dict, or None if invalid/expired.

    Tries JWT_SECRET first, then falls back to ADMIN_KEY if different.
    """
    secrets_to_try = []
    if secret:
        secrets_to_try.append(secret)
    else:
        jwt_secret = os.environ.get("CONTEXTSYNAPSE_JWT_SECRET") or os.environ.get("AICONTEXTDB_JWT_SECRET")
        admin_key = os.environ.get("CONTEXTSYNAPSE_ADMIN_KEY") or os.environ.get("AICONTEXTDB_ADMIN_KEY")
        if jwt_secret:
            secrets_to_try.append(jwt_secret)
        if admin_key and admin_key != jwt_secret:
            secrets_to_try.append(admin_key)
        if not secrets_to_try:
            secrets_to_try.append(_get_jwt_secret())

    for sec in secrets_to_try:
        try:
            parts = token.split(".")
            if len(parts) != 3:
                return None
            signing_input = f"{parts[0]}.{parts[1]}"
            expected_sig = hmac.new(sec.encode(), signing_input.encode(), hashlib.sha256).digest()
            actual_sig = _b64url_decode(parts[2])
            if hmac.compare_digest(expected_sig, actual_sig):
                payload = json.loads(_b64url_decode(parts[1]))
                if payload.get("exp", 0) < time.time():
                    return None
                return payload
        except Exception:
            continue
    return None


# Backward-compat wrappers
def create_admin_jwt(admin_key: str, expires_in: int = 86400) -> str:
    """Create a signed JWT for admin dashboard sessions."""
    return create_jwt({"role": "admin"}, secret=admin_key, expires_in=expires_in)


def verify_admin_jwt(token: str, admin_key: str) -> bool:
    """Verify an admin JWT. Returns True if valid and not expired."""
    payload = verify_jwt(token, secret=admin_key)
    return payload is not None and payload.get("role") == "admin"

# Optional bearer token — allows endpoints to be called without auth header
# (registration, health) while still extracting credentials when present.
_bearer_scheme = HTTPBearer(auto_error=False)


# ======================================================================
# Tenant Auth (main API routes)
# ======================================================================

class TenantAuth:
    """
    FastAPI dependency that resolves a tenant from the Authorization header.

    Usage::

        tenant_auth = TenantAuth(tenant_registry)

        @app.get("/graphs")
        async def list_graphs(tenant: TenantIdentity = Depends(tenant_auth)):
            ...
    """

    def __init__(self, tenant_registry):
        self._registry = tenant_registry

    async def __call__(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ):
        if credentials is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Please provide a valid API key.",
                headers={"WWW-Authenticate": "Bearer realm=\"AIContextDB\""},
            )

        tenant = self._registry.authenticate(credentials.credentials)
        if tenant is None:
            raise HTTPException(status_code=403, detail="Invalid or expired API key.")

        if tenant.status != "active":
            raise HTTPException(status_code=403, detail="This account is currently inactive.")

        # Stash on request.state so downstream middleware/routes can access it
        request.state.tenant = tenant
        return tenant


# ======================================================================
# Admin Auth (admin routes — uses env var AICONTEXTDB_ADMIN_KEY)
# ======================================================================

class AdminAuth:
    """
    FastAPI dependency that validates admin credentials via either:

    1. ``X-Admin-Key`` header (direct key match), or
    2. ``Authorization: Bearer <jwt>`` (JWT signed with admin key).

    The ``AICONTEXTDB_ADMIN_KEY`` env var is the shared secret for both.
    """

    async def __call__(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ):
        admin_key = os.environ.get("CONTEXTSYNAPSE_ADMIN_KEY") or os.environ.get("AICONTEXTDB_ADMIN_KEY")
        if not admin_key:
            raise HTTPException(
                status_code=503,
                detail="Admin API is not configured. Contact your administrator.",
            )

        # Method 1: X-Admin-Key header (CLI / scripts)
        provided = request.headers.get("X-Admin-Key")
        if provided and hmac.compare_digest(provided, admin_key):
            return True

        # Method 2: Bearer JWT (admin dashboard)
        if credentials and verify_admin_jwt(credentials.credentials, admin_key):
            return True

        raise HTTPException(status_code=403, detail="Insufficient permissions. Admin access required.")


# ======================================================================
# Agent Auth (context routes)
# ======================================================================


class AgentAuth:
    """
    Dependency that resolves the current agent from the Authorization header.

    Inject into router endpoints via ``Depends(agent_auth)``.
    """

    def __init__(self, agent_registry):
        self._registry = agent_registry

    async def __call__(
        self,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ):
        """
        Extract agent_id + api_key from the ``Authorization: Bearer <key>`` header,
        validate against the AgentRegistry, and return the AgentIdentity.
        """
        if credentials is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Please provide a valid agent API key.",
                headers={"WWW-Authenticate": "Bearer realm=\"AIContextDB\""},
            )

        api_key = credentials.credentials

        # api_key format: "<agent_id>:<secret>"
        if ":" not in api_key:
            raise HTTPException(
                status_code=401,
                detail="Invalid API key format.",
                headers={"WWW-Authenticate": "Bearer realm=\"AIContextDB\""},
            )

        agent_id, secret = api_key.split(":", 1)

        if not self._registry.authenticate(agent_id, secret):
            raise HTTPException(
                status_code=403,
                detail="Invalid or expired API key.",
            )

        agent = self._registry.get(agent_id)
        if agent is None:
            raise HTTPException(status_code=403, detail="Agent not found.")

        # Update last_seen timestamp
        try:
            self._registry.touch(agent_id)
        except Exception:
            pass  # non-critical — don't fail auth over a timestamp

        return agent


# ======================================================================
# User Auth (SaaS dashboard routes)
# ======================================================================

class UserAuth:
    """
    FastAPI dependency that validates a user JWT from the Authorization header.

    Usage::

        user_auth = UserAuth(user_registry)

        @app.get("/dashboard/overview")
        async def overview(user: UserIdentity = Depends(user_auth)):
            ...
    """

    def __init__(self, user_registry):
        self._registry = user_registry

    async def __call__(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ):
        # Accept X-Admin-Key for admin access (same as dashboard routes)
        admin_key = os.environ.get("CONTEXTSYNAPSE_ADMIN_KEY") or os.environ.get("AICONTEXTDB_ADMIN_KEY")
        provided = request.headers.get("X-Admin-Key")
        if admin_key and provided and hmac.compare_digest(provided, admin_key):
            from dataclasses import dataclass, field as _field

            @dataclass
            class _AdminUser:
                user_id: str = "admin"
                email: str = "admin@local"
                display_name: str = "Admin"
                status: str = "active"
                role: str = "owner"
                is_super_admin: bool = True
                metadata: dict = _field(default_factory=dict)

            request.state.user = _AdminUser()
            return _AdminUser()

        if credentials is None:
            raise HTTPException(
                status_code=401,
                detail="Authentication required. Please sign in to continue.",
                headers={"WWW-Authenticate": "Bearer realm=\"AIContextDB\""},
            )

        payload = verify_jwt(credentials.credentials)
        if payload is None:
            raise HTTPException(status_code=401, detail="Your session has expired. Please sign in again.")

        if payload.get("type") != "user":
            raise HTTPException(status_code=401, detail="Invalid authentication credentials.")

        user = self._registry.get(payload.get("sub"))
        if user is None:
            raise HTTPException(status_code=401, detail="Account not found. Please sign up or contact support.")

        if user.status != "active":
            raise HTTPException(status_code=403, detail="Your account is currently inactive. Contact support for assistance.")

        request.state.user = user
        return user


class AdminAwareAuth(UserAuth):
    """UserAuth that also accepts admin JWT and X-Admin-Key header.

    Use this for endpoints that both admin and regular users need to access.
    """

    async def __call__(
        self,
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ):
        import hmac as _hmac
        from dataclasses import dataclass, field as _f

        @dataclass
        class _AdminUser:
            user_id: str = "admin"
            email: str = "admin@local"
            display_name: str = "Admin"
            status: str = "active"
            role: str = "owner"
            is_super_admin: bool = True
            metadata: dict = _f(default_factory=dict)

        admin_key = os.environ.get("CONTEXTSYNAPSE_ADMIN_KEY") or os.environ.get("AICONTEXTDB_ADMIN_KEY")

        # Path 1: X-Admin-Key header
        provided = request.headers.get("X-Admin-Key")
        if admin_key and provided and _hmac.compare_digest(provided, admin_key):
            return _AdminUser()

        # Path 2: Admin JWT (from /admin/login)
        if credentials and admin_key:
            payload = verify_jwt(credentials.credentials, secret=admin_key)
            if payload and payload.get("role") == "admin":
                return _AdminUser()

        # Path 3: Normal user JWT
        return await super().__call__(request, credentials)


# ======================================================================
# RBAC: Role-based dependency factories
# ======================================================================

_ROLE_HIERARCHY = {
    "reader": 0,
    "contributor": 1,
    "admin": 2,
    "owner": 3,
    # Legacy aliases — kept so old tokens/rows still resolve
    "viewer": 0,
    "member": 1,
}


def require_user_role(user_auth, user_registry, min_role: str):
    """Return a FastAPI dependency that enforces a minimum tenant role.

    Hierarchy: reader < contributor < admin < owner.
    Super admins bypass all role checks.
    """
    min_level = _ROLE_HIERARCHY.get(min_role, 99)

    async def _check(request: Request, user=Depends(user_auth)):
        # Super admins bypass tenant role checks
        if getattr(user, "is_super_admin", False):
            return user
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id:
            raise HTTPException(403, "No workspace access")
        memberships = user_registry.get_user_tenants(user.user_id)
        user_role = next(
            (m.role for m in memberships if m.tenant_id == tenant_id), None
        )
        if user_role is None or _ROLE_HIERARCHY.get(user_role, -1) < min_level:
            raise HTTPException(
                403,
                f"Requires {min_role} role or higher (you have: {user_role or 'none'})",
            )
        return user

    return _check


def require_agent_capability(agent_auth, *required_caps: str):
    """Return a FastAPI dependency that enforces agent capabilities.

    Example::

        require_write = require_agent_capability(agent_auth, "read", "write")

        @router.post("/data")
        async def write_data(agent=Depends(require_write)):
            ...
    """
    async def _check(agent=Depends(agent_auth)):
        missing = [c for c in required_caps if c not in agent.capabilities]
        if missing:
            raise HTTPException(
                403,
                f"Agent lacks required capabilities: {', '.join(missing)}",
            )
        return agent

    return _check
