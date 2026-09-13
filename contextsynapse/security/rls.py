"""RLS — Row-Level Security enforcement.

Filters query results based on user's scoped_ids.
Users only see resources they're authorized for.

Global access roles (admin, compliance_officer) bypass RLS.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Set

from fastapi import Depends

logger = logging.getLogger(__name__)


# Roles that bypass all row-level filtering
GLOBAL_ACCESS_ROLES: Set[str] = {"admin", "compliance_officer"}

# Roles that can see all clients (in addition to global)
_CLIENT_GLOBAL_ROLES: Set[str] = {"admin", "compliance_officer", "operations"}


class RLSFilter:
    """Row-level security filter scoped to a single authenticated user.

    Instantiate per-request from the JWT claims and use the filter_*
    methods to strip unauthorised rows before returning API responses.
    """

    def __init__(
        self,
        user_id: str,
        role: str,
        scoped_ids: Optional[List[str]],
        tenant_id: str,
    ):
        self.user_id = user_id
        self.role = role
        self.scoped_ids: List[str] = scoped_ids or []
        self.tenant_id = tenant_id
        self._scoped_set: Set[str] = set(self.scoped_ids)
        self._is_global = role in GLOBAL_ACCESS_ROLES

    # ------------------------------------------------------------------
    # Generic helper
    # ------------------------------------------------------------------

    def apply_filter(
        self,
        items: List[dict],
        id_field: str,
        allowed_ids: Optional[Set[str]] = None,
    ) -> List[dict]:
        """Filter a list of dicts, keeping only those whose *id_field*
        value is in *allowed_ids*.

        If *allowed_ids* is None the user's scoped_ids are used.
        """
        ids = allowed_ids if allowed_ids is not None else self._scoped_set
        return [item for item in items if item.get(id_field) in ids]

    # ------------------------------------------------------------------
    # Tenant guard (call first on every response)
    # ------------------------------------------------------------------

    def check_tenant(self, resource_tenant_id: str) -> bool:
        """Ensure *resource_tenant_id* matches the authenticated user's tenant."""
        return resource_tenant_id == self.tenant_id

    # ------------------------------------------------------------------
    # Resource-specific filters
    # ------------------------------------------------------------------

    def filter_portfolios(self, portfolios: List[dict]) -> List[dict]:
        """Return only portfolios the user is authorised to see."""
        if self._is_global:
            return portfolios
        return self.apply_filter(portfolios, "portfolio_id")

    def filter_schemes(self, schemes: List[dict]) -> List[dict]:
        """Return only MF schemes the user is authorised to see."""
        if self._is_global:
            return schemes
        return self.apply_filter(schemes, "scheme_id")

    def filter_trades(self, trades: List[dict]) -> List[dict]:
        """Return only trades belonging to the user's authorised portfolios."""
        if self._is_global:
            return trades
        return self.apply_filter(trades, "portfolio_id")

    def filter_clients(self, clients: List[dict]) -> List[dict]:
        """Return only clients the user is authorised to see.

        Operations and admin/compliance roles can see all clients.
        Other roles are filtered by client_id in scoped_ids.
        """
        if self.role in _CLIENT_GLOBAL_ROLES:
            return clients
        return self.apply_filter(clients, "client_id")

    def filter_folios(self, folios: List[dict]) -> List[dict]:
        """Return only MF folios for schemes the user can access."""
        if self._is_global:
            return folios
        return self.apply_filter(folios, "scheme_id")

    def filter_invoices(self, invoices: List[dict]) -> List[dict]:
        """Return invoices matching the user's portfolio_id or client_id scope."""
        if self._is_global:
            return invoices
        return [
            inv for inv in invoices
            if inv.get("portfolio_id") in self._scoped_set
            or inv.get("client_id") in self._scoped_set
        ]

    def filter_settlements(self, settlements: List[dict]) -> List[dict]:
        """Return settlements whose trade's portfolio is in scope."""
        if self._is_global:
            return settlements
        return self.apply_filter(settlements, "portfolio_id")

    def filter_notifications(self, notifications: List[dict]) -> List[dict]:
        """Return only notifications addressed to the current user.

        Admins can see all notifications.
        """
        if self.role == "admin":
            return notifications
        return [n for n in notifications if n.get("recipient_id") == self.user_id]

    # ------------------------------------------------------------------
    # Single-resource access check
    # ------------------------------------------------------------------

    def can_access(self, resource_type: str, resource_id: str) -> bool:
        """Check whether the user can access a specific resource.

        Global-access roles always return True.
        """
        if self._is_global:
            return True
        return resource_id in self._scoped_set


# ======================================================================
# FastAPI dependency
# ======================================================================

def get_rls_filter(request=None, user=None) -> RLSFilter:
    """Build an RLSFilter from a JWTIdentity.

    Typical usage as a FastAPI dependency::

        from contextsynapse.context.acl import JWTIdentity
        from contextsynapse.security.rls import get_rls_filter, RLSFilter

        @router.get("/portfolios")
        async def list_portfolios(
            rls: RLSFilter = Depends(get_rls_filter),
        ):
            all_portfolios = fetch_all(...)
            return rls.filter_portfolios(all_portfolios)

    To wire this up, register a concrete dependency in your app that
    resolves the JWTIdentity first::

        from contextsynapse.security.jwt_identity import extract_identity_from_token

        async def _resolve_identity(request: Request) -> JWTIdentity:
            token = request.headers.get("Authorization", "").removeprefix("Bearer ")
            identity = extract_identity_from_token(token)
            if identity is None:
                raise HTTPException(401, "Invalid token")
            return identity

        def get_rls(identity: JWTIdentity = Depends(_resolve_identity)) -> RLSFilter:
            scoped = identity.portfolio_ids or identity.client_ids or []
            return RLSFilter(
                user_id=identity.sub,
                role=identity.role.value,
                scoped_ids=scoped,
                tenant_id=identity.tenant,
            )
    """
    # This function serves as a template.  Callers should override it
    # with a concrete Depends() chain that extracts JWTIdentity from
    # the request.  The default implementation is a no-op for import
    # compatibility.
    if user is not None:
        # user is expected to be a JWTIdentity-like object
        scoped = getattr(user, "portfolio_ids", None) or getattr(user, "scheme_ids", None) or []
        return RLSFilter(
            user_id=getattr(user, "sub", ""),
            role=getattr(user, "role", "").value if hasattr(getattr(user, "role", ""), "value") else str(getattr(user, "role", "")),
            scoped_ids=list(scoped),
            tenant_id=getattr(user, "tenant", ""),
        )
    raise ValueError(
        "get_rls_filter requires a JWTIdentity user. "
        "Wire it as a FastAPI Depends() with your auth dependency."
    )
