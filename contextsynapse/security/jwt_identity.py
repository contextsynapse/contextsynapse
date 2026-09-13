"""JWT Identity — extracts PMS-scoped identity from JWT tokens.

Creates and validates JWTs carrying role, tenant, portfolio, and client scopes.
Uses the existing HMAC-SHA256 JWT implementation from contextsynapse.api.auth.
"""
from __future__ import annotations

from typing import List, Optional

from ..api.auth import create_jwt, verify_jwt
from ..context.acl import JWTIdentity
from ..security.rbac import Role


def create_pms_jwt(
    sub: str,
    role: str,
    tenant: str = "",
    portfolio_ids: Optional[List[str]] = None,
    client_ids: Optional[List[str]] = None,
    client_id: Optional[str] = None,
    expires_in: int = 3600,
) -> str:
    """Create a JWT token with PMS-scoped claims."""
    payload = {
        "sub": sub,
        "role": role,
        "tenant": tenant,
        "portfolio_ids": portfolio_ids or [],
        "client_ids": client_ids or [],
    }
    if client_id:
        payload["client_id"] = client_id
    return create_jwt(payload, expires_in=expires_in)


def extract_identity_from_token(token: str) -> Optional[JWTIdentity]:
    """Extract a JWTIdentity from a JWT token string.

    Returns None if the token is invalid, expired, or malformed.
    """
    payload = verify_jwt(token)
    if payload is None:
        return None

    role_str = payload.get("role", "")
    try:
        role = Role(role_str)
    except ValueError:
        return None

    return JWTIdentity(
        sub=payload.get("sub", ""),
        role=role,
        tenant=payload.get("tenant", ""),
        portfolio_ids=payload.get("portfolio_ids", []),
        client_ids=payload.get("client_ids", []),
        client_id=payload.get("client_id"),
    )
