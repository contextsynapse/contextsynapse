"""RBAC — Role-Based Access Control for PMS and MF verticals.

8 roles + Client portal. Multi-role support (small firm: one person = FM + Compliance).
Scoped access (FM sees only assigned clients). Delegation (CIO transfers scope temporarily).

Roles:
  admin              — platform admin, full access
  cio                — all portfolios, strategy, override, approve
  fund_manager       — own clients, trade, rules, signals
  research_analyst   — market data, research notes, no trades
  risk_manager       — risk limits, all portfolios read-only
  compliance_officer — SEBI rules, audit, read-only
  operations         — settlements, NAV, billing, recon
  relationship_manager — assigned clients, reports

Client portal is a separate view, not a platform role.
"""

from __future__ import annotations

from enum import Enum


class Role(Enum):
    # Platform level
    ADMIN = "admin"                          # ContextSynapse platform admin (DBA-level)

    # Vertical admin
    PMS_ADMIN = "pms_admin"                  # PMS application admin (manages PMS users, sensors, health)

    # Business roles
    CIO = "cio"
    FUND_MANAGER = "fund_manager"
    RESEARCH_ANALYST = "research_analyst"
    RISK_MANAGER = "risk_manager"
    COMPLIANCE_OFFICER = "compliance_officer"
    OPERATIONS = "operations"
    RELATIONSHIP_MANAGER = "relationship_manager"


class Permission(Enum):
    # Portfolio / Scheme
    VIEW_PORTFOLIO = "view_portfolio"
    VIEW_ALL_PORTFOLIOS = "view_all_portfolios"
    MANAGE_PORTFOLIO = "manage_portfolio"
    VIEW_SCHEME = "view_scheme"

    # Trading
    PROPOSE_TRADE = "propose_trade"
    APPROVE_TRADE = "approve_trade"
    EXECUTE_TRADE = "execute_trade"

    # Rules & Compliance
    VIEW_RULES = "view_rules"
    CREATE_RULES = "create_rules"
    MODIFY_RULES = "modify_rules"
    OVERRIDE_COMPLIANCE = "override_compliance"

    # Market Intelligence
    VIEW_MARKET_DATA = "view_market_data"
    ADD_SIGNAL = "add_signal"
    CONFIGURE_SENSORS = "configure_sensors"

    # Clients
    VIEW_CLIENTS = "view_clients"
    MANAGE_CLIENTS = "manage_clients"
    VIEW_ASSIGNED_CLIENTS = "view_assigned_clients"

    # Operations
    MANAGE_SETTLEMENTS = "manage_settlements"
    COMPUTE_NAV = "compute_nav"
    MANAGE_BILLING = "manage_billing"

    # Skills & Reports
    INVOKE_SKILL = "invoke_skill"
    GENERATE_REPORT = "generate_report"

    # Admin
    MANAGE_USERS = "manage_users"
    VIEW_AUDIT = "view_audit"
    MANAGE_PIPELINES = "manage_pipelines"

    # Workflow
    INITIATE_WORKFLOW = "initiate_workflow"
    APPROVE_WORKFLOW = "approve_workflow"
    DELEGATE_ACCESS = "delegate_access"

    # PMS Admin (vertical-level)
    MANAGE_PMS_USERS = "manage_pms_users"        # create/edit PMS users, assign roles
    VIEW_SYSTEM_HEALTH = "view_system_health"    # sensor status, pipeline freshness
    MANAGE_STOCK_CONTEXTS = "manage_stock_contexts"  # promote/archive stock contexts
    VIEW_PMS_CONTEXTS = "view_pms_contexts"      # see all PMS graph contexts (business lens)


# ═══════════════════════════════════════════════════════════════
# Role → Permission Matrix
# ═══════════════════════════════════════════════════════════════

ROLE_PERMISSIONS: dict[Role, set[Permission]] = {
    Role.ADMIN: set(Permission),  # platform admin — everything

    Role.PMS_ADMIN: {
        # All CIO permissions
        Permission.VIEW_ALL_PORTFOLIOS, Permission.VIEW_PORTFOLIO,
        Permission.MANAGE_PORTFOLIO, Permission.VIEW_SCHEME,
        Permission.PROPOSE_TRADE, Permission.APPROVE_TRADE, Permission.EXECUTE_TRADE,
        Permission.VIEW_RULES, Permission.CREATE_RULES, Permission.MODIFY_RULES,
        Permission.OVERRIDE_COMPLIANCE,
        Permission.VIEW_MARKET_DATA, Permission.ADD_SIGNAL, Permission.CONFIGURE_SENSORS,
        Permission.VIEW_CLIENTS, Permission.MANAGE_CLIENTS,
        Permission.INVOKE_SKILL, Permission.GENERATE_REPORT,
        Permission.VIEW_AUDIT, Permission.MANAGE_PIPELINES,
        Permission.INITIATE_WORKFLOW, Permission.APPROVE_WORKFLOW, Permission.DELEGATE_ACCESS,
        # PMS Admin extras
        Permission.MANAGE_PMS_USERS,
        Permission.VIEW_SYSTEM_HEALTH,
        Permission.MANAGE_STOCK_CONTEXTS,
        Permission.VIEW_PMS_CONTEXTS,
        Permission.MANAGE_USERS,  # manage PMS users (not platform users)
        Permission.MANAGE_SETTLEMENTS, Permission.COMPUTE_NAV, Permission.MANAGE_BILLING,
    },

    Role.CIO: {
        Permission.VIEW_ALL_PORTFOLIOS,
        Permission.VIEW_PORTFOLIO,
        Permission.MANAGE_PORTFOLIO,
        Permission.VIEW_SCHEME,
        Permission.PROPOSE_TRADE,
        Permission.APPROVE_TRADE,
        Permission.EXECUTE_TRADE,
        Permission.VIEW_RULES,
        Permission.CREATE_RULES,
        Permission.MODIFY_RULES,
        Permission.OVERRIDE_COMPLIANCE,
        Permission.VIEW_MARKET_DATA,
        Permission.ADD_SIGNAL,
        Permission.CONFIGURE_SENSORS,
        Permission.VIEW_CLIENTS,
        Permission.MANAGE_CLIENTS,
        Permission.INVOKE_SKILL,
        Permission.GENERATE_REPORT,
        Permission.VIEW_AUDIT,
        Permission.MANAGE_PIPELINES,
        Permission.INITIATE_WORKFLOW,
        Permission.APPROVE_WORKFLOW,
        Permission.DELEGATE_ACCESS,
    },
    Role.FUND_MANAGER: {
        Permission.VIEW_PORTFOLIO,
        Permission.MANAGE_PORTFOLIO,
        Permission.VIEW_SCHEME,
        Permission.PROPOSE_TRADE,
        Permission.EXECUTE_TRADE,
        Permission.VIEW_RULES,
        Permission.CREATE_RULES,
        Permission.VIEW_MARKET_DATA,
        Permission.ADD_SIGNAL,
        Permission.CONFIGURE_SENSORS,
        Permission.VIEW_ASSIGNED_CLIENTS,
        Permission.INVOKE_SKILL,
        Permission.GENERATE_REPORT,
        Permission.INITIATE_WORKFLOW,
    },
    Role.RESEARCH_ANALYST: {
        Permission.VIEW_MARKET_DATA,
        Permission.ADD_SIGNAL,
        Permission.VIEW_PORTFOLIO,
        Permission.VIEW_SCHEME,
        Permission.INVOKE_SKILL,
    },
    Role.RISK_MANAGER: {
        Permission.VIEW_ALL_PORTFOLIOS,
        Permission.VIEW_PORTFOLIO,
        Permission.VIEW_SCHEME,
        Permission.VIEW_RULES,
        Permission.CREATE_RULES,
        Permission.MODIFY_RULES,
        Permission.VIEW_MARKET_DATA,
        Permission.VIEW_CLIENTS,
        Permission.GENERATE_REPORT,
        Permission.VIEW_AUDIT,
        Permission.APPROVE_WORKFLOW,
    },
    Role.COMPLIANCE_OFFICER: {
        Permission.VIEW_ALL_PORTFOLIOS,
        Permission.VIEW_PORTFOLIO,
        Permission.VIEW_SCHEME,
        Permission.VIEW_RULES,
        Permission.MODIFY_RULES,
        Permission.OVERRIDE_COMPLIANCE,
        Permission.VIEW_MARKET_DATA,
        Permission.VIEW_CLIENTS,
        Permission.GENERATE_REPORT,
        Permission.VIEW_AUDIT,
        Permission.APPROVE_WORKFLOW,
    },
    Role.OPERATIONS: {
        Permission.VIEW_PORTFOLIO,
        Permission.VIEW_SCHEME,
        Permission.MANAGE_SETTLEMENTS,
        Permission.COMPUTE_NAV,
        Permission.MANAGE_BILLING,
        Permission.VIEW_CLIENTS,
        Permission.GENERATE_REPORT,
    },
    Role.RELATIONSHIP_MANAGER: {
        Permission.VIEW_ASSIGNED_CLIENTS,
        Permission.VIEW_PORTFOLIO,
        Permission.GENERATE_REPORT,
    },
}

# Roles with global portfolio visibility
GLOBAL_ACCESS_ROLES = {Role.ADMIN, Role.PMS_ADMIN, Role.CIO, Role.COMPLIANCE_OFFICER, Role.RISK_MANAGER}

# Roles that see only scoped data
SCOPED_ROLES = {Role.FUND_MANAGER, Role.RELATIONSHIP_MANAGER}


class RBACManager:
    """RBAC with multi-role support.

    A user can hold multiple roles (small firm: one person = FM + Compliance).
    Effective permissions = union of all role permissions.
    """

    def get_effective_permissions(self, roles: list[str]) -> set[Permission]:
        """Get merged permissions for a user with multiple roles."""
        merged = set()
        for role_str in roles:
            try:
                role = Role(role_str)
                merged |= ROLE_PERMISSIONS.get(role, set())
            except ValueError:
                continue
        return merged

    def check_permission(self, roles: list[str], permission: Permission) -> bool:
        """Check if any of the user's roles grants a permission."""
        return permission in self.get_effective_permissions(roles)

    check = check_permission

    def check_portfolio_access(
        self,
        roles: list[str],
        user_id: str,
        portfolio_owner_id: str,
        scoped_ids: list[str] | None = None,
    ) -> bool:
        """Check if user can access a specific portfolio."""
        role_enums = [Role(r) for r in roles if r in [x.value for x in Role]]

        if any(r in GLOBAL_ACCESS_ROLES for r in role_enums):
            return True
        perms = self.get_effective_permissions(roles)
        if Permission.VIEW_PORTFOLIO not in perms:
            return False
        if scoped_ids:
            return portfolio_owner_id in scoped_ids
        return user_id == portfolio_owner_id

    def check_client_access(
        self,
        roles: list[str],
        client_id: str,
        scoped_ids: list[str] | None = None,
    ) -> bool:
        """Check if user can access a specific client."""
        role_enums = [Role(r) for r in roles if r in [x.value for x in Role]]
        if any(r in GLOBAL_ACCESS_ROLES for r in role_enums):
            return True
        perms = self.get_effective_permissions(roles)
        if Permission.VIEW_ASSIGNED_CLIENTS in perms and scoped_ids:
            return client_id in scoped_ids
        return Permission.VIEW_CLIENTS in perms

    def is_global_access(self, roles: list[str]) -> bool:
        role_enums = [Role(r) for r in roles if r in [x.value for x in Role]]
        return any(r in GLOBAL_ACCESS_ROLES for r in role_enums)

    def get_role_info(self, role_str: str) -> dict:
        try:
            role = Role(role_str)
        except ValueError:
            return {"role": role_str, "label": role_str, "permissions": []}
        perms = ROLE_PERMISSIONS.get(role, set())
        return {
            "role": role.value,
            "label": ROLE_LABELS.get(role, role.value),
            "description": ROLE_DESCRIPTIONS.get(role, ""),
            "permissions": sorted(p.value for p in perms),
            "permission_count": len(perms),
            "global_access": role in GLOBAL_ACCESS_ROLES,
        }

    def list_roles(self) -> list:
        return [self.get_role_info(r.value) for r in Role]


ROLE_LABELS = {
    Role.ADMIN: "Platform Admin",
    Role.PMS_ADMIN: "PMS Admin",
    Role.CIO: "Chief Investment Officer",
    Role.FUND_MANAGER: "Fund Manager",
    Role.RESEARCH_ANALYST: "Research Analyst",
    Role.RISK_MANAGER: "Risk Manager",
    Role.COMPLIANCE_OFFICER: "Compliance Officer",
    Role.OPERATIONS: "Operations",
    Role.RELATIONSHIP_MANAGER: "Relationship Manager",
}

ROLE_DESCRIPTIONS = {
    Role.ADMIN: "ContextSynapse platform — graphs, pipelines, tenants, infrastructure",
    Role.PMS_ADMIN: "PMS application — manage users, sensors, stock contexts, system health, audit",
    Role.CIO: "All portfolios, strategy, approve trades, override compliance, delegate access",
    Role.FUND_MANAGER: "Assigned clients, trade, create rules, manual signals — scoped to own clients",
    Role.RESEARCH_ANALYST: "Market data, fusion, sensors, research notes — no trades",
    Role.RISK_MANAGER: "All portfolios read-only, risk limits, rules, approve risk workflows",
    Role.COMPLIANCE_OFFICER: "Audit trail, SEBI rules, override blocks, approve compliance workflows",
    Role.OPERATIONS: "Settlements, NAV, billing, recon — no trades, no rules",
    Role.RELATIONSHIP_MANAGER: "Assigned clients only, generate reports — no trades, no market data",
}

# Small firm presets — one person wears multiple hats
SMALL_FIRM_PRESETS = {
    "solo_owner": ["pms_admin"],                                    # firm owner does everything
    "owner_plus_fm": ["pms_admin", "fund_manager"],                 # owner who also trades
    "solo_fm": ["fund_manager", "compliance_officer", "operations"],
    "fm_plus_compliance": ["fund_manager", "compliance_officer"],
    "fm_plus_ops": ["fund_manager", "operations"],
    "cio_all": ["cio"],
}
