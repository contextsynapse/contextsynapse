"""
Integration tests for tenant isolation.
Verifies that one tenant's data is invisible to another.
"""

import pytest
from contextcore.api.tenants import TenantRegistry, TenantIdentity


@pytest.fixture
def registry(tmp_db_path):
    return TenantRegistry(db_path=tmp_db_path)


@pytest.fixture
def two_tenants(registry):
    """Create two tenants and return their identities + keys."""
    t1, k1 = registry.create("tenant-alpha", config={"max_graphs": 10})
    t2, k2 = registry.create("tenant-beta", config={"max_graphs": 5})
    return {
        "alpha": {"tenant": t1, "key": k1},
        "beta": {"tenant": t2, "key": k2},
    }


class TestCrossTenantIsolation:

    def test_namespace_scoping_prevents_cross_access(self, two_tenants):
        alpha = two_tenants["alpha"]["tenant"]
        beta = two_tenants["beta"]["tenant"]

        # Alpha creates a namespace
        alpha_ns = alpha.scoped_namespace("shared-name")
        beta_ns = beta.scoped_namespace("shared-name")

        # Same user-facing name, different internal names
        assert alpha_ns != beta_ns
        assert "shared-name" in alpha_ns
        assert "shared-name" in beta_ns
        assert alpha.tenant_id in alpha_ns
        assert beta.tenant_id in beta_ns

    def test_unscoped_rejects_other_tenants_namespace(self, two_tenants):
        alpha = two_tenants["alpha"]["tenant"]
        beta = two_tenants["beta"]["tenant"]

        alpha_ns = alpha.scoped_namespace("my-graph")
        # Beta tries to unscope alpha's namespace → None
        assert beta.unscoped_namespace(alpha_ns) is None
        # Alpha can unscope its own
        assert alpha.unscoped_namespace(alpha_ns) == "my-graph"

    def test_api_key_authenticates_correct_tenant(self, registry, two_tenants):
        alpha_key = two_tenants["alpha"]["key"]
        beta_key = two_tenants["beta"]["key"]

        alpha_result = registry.authenticate(alpha_key)
        beta_result = registry.authenticate(beta_key)

        assert alpha_result.tenant_id == two_tenants["alpha"]["tenant"].tenant_id
        assert beta_result.tenant_id == two_tenants["beta"]["tenant"].tenant_id
        # Keys don't cross-authenticate
        assert alpha_result.tenant_id != beta_result.tenant_id

    def test_suspended_tenant_cannot_authenticate(self, registry, two_tenants):
        alpha = two_tenants["alpha"]["tenant"]
        alpha_key = two_tenants["alpha"]["key"]

        registry.update_status(alpha.tenant_id, "suspended")
        assert registry.authenticate(alpha_key) is None

        # Beta still works
        beta_key = two_tenants["beta"]["key"]
        assert registry.authenticate(beta_key) is not None

    def test_deleted_tenant_invisible(self, registry, two_tenants):
        alpha = two_tenants["alpha"]["tenant"]
        registry.delete(alpha.tenant_id)

        # Alpha not in active list
        active = registry.list_tenants()
        assert not any(t.tenant_id == alpha.tenant_id for t in active)

        # Beta still visible
        assert any(t.name == "tenant-beta" for t in active)

    def test_tenant_config_isolation(self, two_tenants):
        alpha = two_tenants["alpha"]["tenant"]
        beta = two_tenants["beta"]["tenant"]

        assert alpha.config.get("max_graphs") == 10
        assert beta.config.get("max_graphs") == 5
