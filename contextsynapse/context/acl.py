"""Context-level Access Control — scope-based ACL + ownership resolution.

Every context path belongs to a scope (market, portfolio, client, frozen).
Each scope has role-based permissions. Portfolio and client scopes additionally
require ownership verification — an FM can only see their own portfolios.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional

from ..security.rbac import Role
from ..security.sanitize import parse_context_path, VALID_SCOPES


class ContextPermission(str, Enum):
    READ = "read"
    WRITE = "write"


@dataclass
class JWTIdentity:
    """Identity extracted from a JWT token."""
    sub: str
    role: Role
    tenant: str = ""
    portfolio_ids: List[str] = field(default_factory=list)
    client_ids: List[str] = field(default_factory=list)
    client_id: Optional[str] = None  # for CLIENT_VIEWER role


CONTEXT_ACL: dict = {
    "market": {
        Role.FUND_MANAGER:        {ContextPermission.READ},
        Role.COMPLIANCE_OFFICER:  {ContextPermission.READ},
        Role.RESEARCH_ANALYST:    {ContextPermission.READ},
        Role.CLIENT_VIEWER:       {ContextPermission.READ},
        Role.OPERATIONS:          {ContextPermission.READ},
        Role.ADMIN:               {ContextPermission.READ, ContextPermission.WRITE},
    },
    "portfolio": {
        Role.FUND_MANAGER:        {ContextPermission.READ},
        Role.COMPLIANCE_OFFICER:  {ContextPermission.READ},
        Role.RESEARCH_ANALYST:    set(),
        Role.CLIENT_VIEWER:       set(),
        Role.OPERATIONS:          {ContextPermission.READ},
        Role.ADMIN:               {ContextPermission.READ, ContextPermission.WRITE},
    },
    "client": {
        Role.FUND_MANAGER:        {ContextPermission.READ},
        Role.COMPLIANCE_OFFICER:  {ContextPermission.READ},
        Role.CLIENT_VIEWER:       {ContextPermission.READ},
        Role.OPERATIONS:          {ContextPermission.READ, ContextPermission.WRITE},
        Role.ADMIN:               {ContextPermission.READ, ContextPermission.WRITE},
    },
    "frozen": {
        Role.FUND_MANAGER:        {ContextPermission.READ},
        Role.COMPLIANCE_OFFICER:  {ContextPermission.READ},
        Role.RESEARCH_ANALYST:    set(),
        Role.CLIENT_VIEWER:       set(),
        Role.OPERATIONS:          {ContextPermission.READ},
        Role.ADMIN:               {ContextPermission.READ},
    },
}


class OwnershipResolver:
    """Checks whether a user can access a specific context path.

    ACL grants scope-level permission. This resolver checks whether the user
    owns the specific portfolio/client within that scope.
    """

    def can_access(self, user: JWTIdentity, context_path: str) -> bool:
        scope, parts = parse_context_path(context_path)

        if scope not in VALID_SCOPES:
            return False

        if scope == "market":
            return True

        if scope == "frozen":
            return user.role in (Role.COMPLIANCE_OFFICER, Role.ADMIN, Role.FUND_MANAGER)

        if scope == "portfolio":
            if not parts:
                return False
            portfolio_id = parts[0]
            if user.role in (Role.COMPLIANCE_OFFICER, Role.ADMIN):
                return True
            return portfolio_id in user.portfolio_ids

        if scope == "client":
            if not parts:
                return False
            client_id = parts[0]
            if user.role in (Role.COMPLIANCE_OFFICER, Role.ADMIN):
                return True
            if user.role == Role.CLIENT_VIEWER:
                return client_id == user.client_id
            return client_id in user.client_ids

        return False


def check_context_access(
    user: JWTIdentity,
    context_path: str,
    permission: ContextPermission,
) -> bool:
    """Check if a user has a specific permission on a context path.

    Combines scope-level ACL with ownership verification.
    """
    scope, _ = parse_context_path(context_path)
    scope_perms = CONTEXT_ACL.get(scope, {})
    role_perms = scope_perms.get(user.role, set())

    if permission not in role_perms:
        return False

    resolver = OwnershipResolver()
    return resolver.can_access(user, context_path)
