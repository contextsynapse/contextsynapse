# tests/unit/test_rbac.py
"""Tests for RBAC — role-based access control for PMS."""
import pytest

from contextcore.security.rbac import RBACManager, Role, Permission


class TestRoles:
    def test_all_roles_exist(self):
        assert Role.FUND_MANAGER.value == "fund_manager"
        assert Role.COMPLIANCE_OFFICER.value == "compliance_officer"
        assert Role.RESEARCH_ANALYST.value == "research_analyst"
        assert Role.CLIENT_VIEWER.value == "client_viewer"
        assert Role.OPERATIONS.value == "operations"
        assert Role.ADMIN.value == "admin"


class TestFundManagerPermissions:
    def test_can_view_portfolio(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.FUND_MANAGER, Permission.VIEW_PORTFOLIO) is True

    def test_can_execute_trade(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.FUND_MANAGER, Permission.EXECUTE_TRADE) is True

    def test_can_invoke_skill(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.FUND_MANAGER, Permission.INVOKE_SKILL) is True

    def test_cannot_modify_rules(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.FUND_MANAGER, Permission.MODIFY_RULES) is False

    def test_cannot_manage_users(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.FUND_MANAGER, Permission.MANAGE_USERS) is False


class TestComplianceOfficerPermissions:
    def test_can_view_all_portfolios(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.COMPLIANCE_OFFICER, Permission.VIEW_ALL_PORTFOLIOS) is True

    def test_can_modify_rules(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.COMPLIANCE_OFFICER, Permission.MODIFY_RULES) is True

    def test_can_override_compliance(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.COMPLIANCE_OFFICER, Permission.OVERRIDE_COMPLIANCE) is True

    def test_cannot_execute_trade(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.COMPLIANCE_OFFICER, Permission.EXECUTE_TRADE) is False


class TestResearchAnalystPermissions:
    def test_can_view_market_data(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.RESEARCH_ANALYST, Permission.VIEW_MARKET_DATA) is True

    def test_cannot_view_portfolio(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.RESEARCH_ANALYST, Permission.VIEW_PORTFOLIO) is False

    def test_cannot_execute_trade(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.RESEARCH_ANALYST, Permission.EXECUTE_TRADE) is False


class TestClientViewerPermissions:
    def test_can_view_portfolio(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.CLIENT_VIEWER, Permission.VIEW_PORTFOLIO) is True

    def test_cannot_execute_trade(self):
        rbac = RBACManager()
        assert rbac.check_permission(Role.CLIENT_VIEWER, Permission.EXECUTE_TRADE) is False


class TestPortfolioAccess:
    def test_fund_manager_owns_portfolio(self):
        rbac = RBACManager()
        assert rbac.check_portfolio_access(Role.FUND_MANAGER, "user1", "user1") is True

    def test_fund_manager_denied_other_portfolio(self):
        rbac = RBACManager()
        assert rbac.check_portfolio_access(Role.FUND_MANAGER, "user1", "user2") is False

    def test_compliance_officer_sees_all(self):
        rbac = RBACManager()
        assert rbac.check_portfolio_access(Role.COMPLIANCE_OFFICER, "officer1", "anyone") is True

    def test_admin_sees_all(self):
        rbac = RBACManager()
        assert rbac.check_portfolio_access(Role.ADMIN, "admin1", "anyone") is True

    def test_client_viewer_only_own(self):
        rbac = RBACManager()
        assert rbac.check_portfolio_access(Role.CLIENT_VIEWER, "client1", "client1") is True
        assert rbac.check_portfolio_access(Role.CLIENT_VIEWER, "client1", "client2") is False

    def test_get_permissions(self):
        rbac = RBACManager()
        perms = rbac.get_permissions(Role.FUND_MANAGER)
        assert Permission.VIEW_PORTFOLIO in perms
        assert Permission.EXECUTE_TRADE in perms
        assert Permission.MANAGE_USERS not in perms
