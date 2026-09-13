"""RBAC — Role-Based Access Control for PMS.

6 roles with scoped permissions. Fund managers can trade but not modify rules.
Compliance officers can modify rules but not trade. Admins have full access.

Usage:
    rbac = RBACManager()
    if rbac.check_permission(Role.FUND_MANAGER, Permission.EXECUTE_TRADE):
        execute_trade(...)
"""
from __future__ import annotations

from enum import Enum
from typing import Dict, Set


class Role(Enum):
    FUND_MANAGER = "fund_manager"
    COMPLIANCE_OFFICER = "compliance_officer"
    RESEARCH_ANALYST = "research_analyst"
    CLIENT_VIEWER = "client_viewer"
    OPERATIONS = "operations"
    ADMIN = "admin"


class Permission(Enum):
    VIEW_PORTFOLIO = "view_portfolio"
    EXECUTE_TRADE = "execute_trade"
    MODIFY_RULES = "modify_rules"
    VIEW_MARKET_DATA = "view_market_data"
    MANAGE_CLIENTS = "manage_clients"
    MANAGE_PORTFOLIO = "manage_portfolio"
    VIEW_ALL_PORTFOLIOS = "view_all_portfolios"
    OVERRIDE_COMPLIANCE = "override_compliance"
    MANAGE_USERS = "manage_users"
    INVOKE_SKILL = "invoke_skill"
    GENERATE_REPORT = "generate_report"


ROLE_PERMISSIONS: Dict[Role, Set[Permission]] = {
    Role.FUND_MANAGER: {
        Permission.VIEW_PORTFOLIO,
        Permission.EXECUTE_TRADE,
        Permission.VIEW_MARKET_DATA,
        Permission.INVOKE_SKILL,
        Permission.GENERATE_REPORT,
        Permission.MANAGE_PORTFOLIO,
    },
    Role.COMPLIANCE_OFFICER: {
        Permission.VIEW_ALL_PORTFOLIOS,
        Permission.VIEW_PORTFOLIO,
        Permission.MODIFY_RULES,
        Permission.OVERRIDE_COMPLIANCE,
        Permission.VIEW_MARKET_DATA,
        Permission.INVOKE_SKILL,
        Permission.GENERATE_REPORT,
    },
    Role.RESEARCH_ANALYST: {
        Permission.VIEW_MARKET_DATA,
        Permission.INVOKE_SKILL,
    },
    Role.CLIENT_VIEWER: {
        Permission.VIEW_PORTFOLIO,
    },
    Role.OPERATIONS: {
        Permission.MANAGE_CLIENTS,
        Permission.GENERATE_REPORT,
        Permission.VIEW_PORTFOLIO,
    },
    Role.ADMIN: set(Permission),  # all permissions
}

# Roles that can see any portfolio regardless of ownership
_GLOBAL_ACCESS_ROLES = {Role.COMPLIANCE_OFFICER, Role.ADMIN}


class RBACManager:
    """Enforces role-based access control."""

    def check_permission(self, role: Role, permission: Permission) -> bool:
        """Check if a role has a specific permission."""
        return permission in ROLE_PERMISSIONS.get(role, set())

    def check_portfolio_access(
        self, role: Role, user_id: str, portfolio_owner_id: str
    ) -> bool:
        """Check if a user can access a specific portfolio."""
        if role in _GLOBAL_ACCESS_ROLES:
            return True
        if not self.check_permission(role, Permission.VIEW_PORTFOLIO):
            return False
        return user_id == portfolio_owner_id

    def get_permissions(self, role: Role) -> Set[Permission]:
        """Get all permissions for a role."""
        return ROLE_PERMISSIONS.get(role, set()).copy()
