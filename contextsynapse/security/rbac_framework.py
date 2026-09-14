"""Generic RBAC Framework — verticals register their own roles and permissions.

Platform provides:
  - RoleRegistry (register roles from any vertical)
  - PermissionChecker (check permissions across multi-role)
  - Access groups (team-level grouping)
  - Scoped access (entity-level filtering)

Vertical provides:
  - Role definitions with permissions
  - Access group definitions
  - Small firm presets

Usage:
    from contextsynapse.security.rbac_framework import get_role_registry

    reg = get_role_registry()

    # Vertical registers roles
    reg.register_vertical("pms", {
        "pms_admin": {"permissions": [...], "label": "PMS Admin", "global": True},
        "fund_manager": {"permissions": [...], "label": "Fund Manager", "global": False},
    })

    # Check permissions (multi-role)
    reg.can(["fund_manager", "compliance_officer"], "execute_trade")

    # Get effective permissions for a user
    perms = reg.effective_permissions(["fund_manager"])
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


class RoleRegistry:
    """Central registry for roles across all verticals."""

    def __init__(self):
        self._verticals: dict[str, dict[str, dict]] = {}
        self._access_groups: dict[str, list[str]] = {}
        self._presets: dict[str, list[str]] = {}

    # ── Registration ─────────────────────────────────────────────

    def register_vertical(self, vertical: str, roles: dict[str, dict]):
        """Register roles for a vertical.

        roles: {
            "fund_manager": {
                "permissions": ["view_portfolio", "propose_trade", ...],
                "label": "Fund Manager",
                "description": "...",
                "global_access": False,
            },
        }
        """
        self._verticals[vertical] = roles
        logger.info("[RBAC] Registered %d roles for vertical '%s'", len(roles), vertical)

    def register_access_groups(self, groups: dict[str, list[str]]):
        """Register access groups (team-level role grouping).

        groups: {"investment_team": ["cio", "fund_manager", "research_analyst"]}
        """
        self._access_groups.update(groups)

    def register_presets(self, presets: dict[str, list[str]]):
        """Register small-firm presets (multi-role combos).

        presets: {"solo_owner": ["pms_admin", "fund_manager"]}
        """
        self._presets.update(presets)

    # ── Permission checking ──────────────────────────────────────

    def effective_permissions(self, roles: list[str], vertical: str = "") -> set[str]:
        """Get merged permissions across all roles (multi-role support)."""
        perms = set()
        for role in roles:
            role_def = self._find_role(role, vertical)
            if role_def:
                perms |= set(role_def.get("permissions", []))
        return perms

    def can(self, roles: list[str], permission: str, vertical: str = "") -> bool:
        """Check if any role has the permission."""
        return permission in self.effective_permissions(roles, vertical)

    def can_any(self, roles: list[str], *permissions: str, vertical: str = "") -> bool:
        efp = self.effective_permissions(roles, vertical)
        return bool(efp & set(permissions))

    def can_all(self, roles: list[str], *permissions: str, vertical: str = "") -> bool:
        efp = self.effective_permissions(roles, vertical)
        return set(permissions) <= efp

    def is_global(self, roles: list[str], vertical: str = "") -> bool:
        """Check if any role has global (all-entity) access."""
        for role in roles:
            role_def = self._find_role(role, vertical)
            if role_def and role_def.get("global_access"):
                return True
        return False

    # ── Queries ──────────────────────────────────────────────────

    def list_roles(self, vertical: str = "") -> list[dict]:
        """List all roles, optionally filtered by vertical."""
        result = []
        for vert, roles in self._verticals.items():
            if vertical and vert != vertical:
                continue
            for role_id, role_def in roles.items():
                result.append({
                    "role": role_id,
                    "vertical": vert,
                    "label": role_def.get("label", role_id),
                    "description": role_def.get("description", ""),
                    "permission_count": len(role_def.get("permissions", [])),
                    "global_access": role_def.get("global_access", False),
                })
        return result

    def get_role(self, role_id: str, vertical: str = "") -> dict | None:
        return self._find_role(role_id, vertical)

    def list_access_groups(self) -> dict[str, list[str]]:
        return dict(self._access_groups)

    def list_presets(self) -> dict[str, list[str]]:
        return dict(self._presets)

    def get_group_roles(self, group: str) -> list[str]:
        return self._access_groups.get(group, [])

    # ── Internal ─────────────────────────────────────────────────

    def _find_role(self, role_id: str, vertical: str = "") -> dict | None:
        """Find a role definition, searching specified vertical first."""
        if vertical and vertical in self._verticals:
            if role_id in self._verticals[vertical]:
                return self._verticals[vertical][role_id]
        # Search all verticals
        for roles in self._verticals.values():
            if role_id in roles:
                return roles[role_id]
        return None


# ── Singleton ────────────────────────────────────────────────────

_instance: RoleRegistry | None = None

def get_role_registry() -> RoleRegistry:
    global _instance
    if _instance is None:
        _instance = RoleRegistry()
        # Register platform-level admin role
        _instance.register_vertical("platform", {
            "admin": {
                "permissions": ["*"],  # all permissions
                "label": "Platform Admin",
                "description": "ContextSynapse platform — full infrastructure access",
                "global_access": True,
            },
        })
    return _instance
