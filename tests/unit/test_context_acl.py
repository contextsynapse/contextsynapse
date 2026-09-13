"""Tests for context ACL and ownership resolution."""
import pytest
from contextcore.context.acl import (
    ContextPermission, JWTIdentity, CONTEXT_ACL, OwnershipResolver,
    check_context_access,
)
from contextcore.security.rbac import Role


def _make_fm(portfolios=None, clients=None):
    return JWTIdentity(
        sub="fm_1", role=Role.FUND_MANAGER, tenant="firm_a",
        portfolio_ids=portfolios or ["p001", "p002"],
        client_ids=clients or ["c001"],
    )


def _make_compliance():
    return JWTIdentity(
        sub="co_1", role=Role.COMPLIANCE_OFFICER, tenant="firm_a",
        portfolio_ids=[], client_ids=[],
    )


def _make_client(client_id="c001"):
    return JWTIdentity(
        sub="client_1", role=Role.CLIENT_VIEWER, tenant="firm_a",
        portfolio_ids=[], client_ids=[], client_id=client_id,
    )


def _make_analyst():
    return JWTIdentity(
        sub="ra_1", role=Role.RESEARCH_ANALYST, tenant="firm_a",
        portfolio_ids=[], client_ids=[],
    )


class TestContextACL:
    def test_all_roles_can_read_market(self):
        for role in Role:
            perms = CONTEXT_ACL["market"].get(role, set())
            assert ContextPermission.READ in perms, f"{role} should read market"

    def test_analyst_cannot_read_portfolio(self):
        perms = CONTEXT_ACL["portfolio"].get(Role.RESEARCH_ANALYST, set())
        assert ContextPermission.READ not in perms

    def test_client_cannot_read_portfolio(self):
        perms = CONTEXT_ACL["portfolio"].get(Role.CLIENT_VIEWER, set())
        assert ContextPermission.READ not in perms

    def test_only_admin_can_write_market(self):
        for role in Role:
            perms = CONTEXT_ACL["market"].get(role, set())
            if role == Role.ADMIN:
                assert ContextPermission.WRITE in perms
            else:
                assert ContextPermission.WRITE not in perms


class TestOwnershipResolver:
    def setup_method(self):
        self.resolver = OwnershipResolver()

    def test_fm_can_access_own_portfolio(self):
        fm = _make_fm(portfolios=["p001"])
        assert self.resolver.can_access(fm, "portfolio:p001:holdings")

    def test_fm_denied_other_portfolio(self):
        fm = _make_fm(portfolios=["p001"])
        assert not self.resolver.can_access(fm, "portfolio:p999:holdings")

    def test_compliance_can_access_any_portfolio(self):
        co = _make_compliance()
        assert self.resolver.can_access(co, "portfolio:p999:holdings")

    def test_client_can_access_own_data(self):
        cl = _make_client("c001")
        assert self.resolver.can_access(cl, "client:c001:profile")

    def test_client_denied_other_client(self):
        cl = _make_client("c001")
        assert not self.resolver.can_access(cl, "client:c002:profile")

    def test_fm_can_access_own_client(self):
        fm = _make_fm(clients=["c001"])
        assert self.resolver.can_access(fm, "client:c001:mandate")

    def test_fm_denied_other_client(self):
        fm = _make_fm(clients=["c001"])
        assert not self.resolver.can_access(fm, "client:c999:mandate")

    def test_everyone_can_access_market(self):
        analyst = _make_analyst()
        assert self.resolver.can_access(analyst, "market:tcs")

    def test_unknown_scope_denied(self):
        fm = _make_fm()
        assert not self.resolver.can_access(fm, "badscope:foo")


class TestCheckContextAccess:
    def test_fm_read_own_portfolio(self):
        fm = _make_fm(portfolios=["p001"])
        assert check_context_access(fm, "portfolio:p001:holdings", ContextPermission.READ)

    def test_fm_write_market_denied(self):
        fm = _make_fm()
        assert not check_context_access(fm, "market:tcs", ContextPermission.WRITE)

    def test_analyst_read_portfolio_denied(self):
        analyst = _make_analyst()
        assert not check_context_access(analyst, "portfolio:p001:holdings", ContextPermission.READ)
