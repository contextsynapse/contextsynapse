# tests/unit/test_tenant_isolation.py
"""Tests for TenantIsolation — namespace prefixing for multi-tenancy."""
import pytest

from contextcore.security.tenant import TenantIsolation


class TestNamespacing:

    def test_namespace_creation(self):
        ti = TenantIsolation()
        ns = ti.namespace("alpha", "portfolio_growth")
        assert ns == "alpha_portfolio_growth"

    def test_namespace_with_special_chars(self):
        ti = TenantIsolation()
        ns = ti.namespace("alpha-capital", "portfolio_growth")
        assert "alpha_capital" in ns  # hyphens normalized

    def test_extract_tenant(self):
        ti = TenantIsolation()
        ns = ti.namespace("alpha", "portfolio_growth")
        tenant = ti.extract_tenant(ns)
        assert tenant == "alpha"


class TestSharedNamespaces:

    def test_market_data_is_shared(self):
        ti = TenantIsolation()
        assert ti.is_shared("market_nifty50") is True
        assert ti.is_shared("market_fii_flows") is True

    def test_stock_data_is_shared(self):
        ti = TenantIsolation()
        assert ti.is_shared("stock_tcs") is True

    def test_macro_data_is_shared(self):
        ti = TenantIsolation()
        assert ti.is_shared("macro_india") is True

    def test_portfolio_not_shared(self):
        ti = TenantIsolation()
        assert ti.is_shared("alpha_portfolio_growth") is False

    def test_sebi_rules_shared(self):
        ti = TenantIsolation()
        assert ti.is_shared("regulatory_sebi") is True


class TestAccessValidation:

    def test_tenant_can_access_own_namespace(self):
        ti = TenantIsolation()
        ns = ti.namespace("alpha", "portfolio_growth")
        assert ti.validate_access("alpha", ns) is True

    def test_tenant_cannot_access_other_namespace(self):
        ti = TenantIsolation()
        ns = ti.namespace("beta", "portfolio_value")
        assert ti.validate_access("alpha", ns) is False

    def test_any_tenant_can_access_shared(self):
        ti = TenantIsolation()
        assert ti.validate_access("alpha", "market_nifty50") is True
        assert ti.validate_access("beta", "market_nifty50") is True

    def test_extract_tenant_from_shared_returns_none(self):
        ti = TenantIsolation()
        assert ti.extract_tenant("market_nifty50") is None

    def test_extract_tenant_unknown_format(self):
        ti = TenantIsolation()
        assert ti.extract_tenant("random_namespace") is None
