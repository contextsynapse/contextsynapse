"""
Unit tests for TenantRegistry — CRUD, authentication, namespace scoping.
"""

import pytest
from contextcore.api.tenants import TenantRegistry, TenantIdentity


@pytest.fixture
def registry(tmp_db_path):
    return TenantRegistry(db_path=tmp_db_path)


class TestTenantCRUD:

    def test_create_tenant(self, registry):
        tenant, api_key = registry.create("acme-corp")
        assert tenant.name == "acme-corp"
        assert tenant.status == "active"
        assert tenant.tenant_id
        assert api_key
        assert len(api_key) > 20

    def test_get_tenant(self, registry):
        tenant, _ = registry.create("test-co")
        fetched = registry.get(tenant.tenant_id)
        assert fetched is not None
        assert fetched.name == "test-co"

    def test_get_by_name(self, registry):
        registry.create("findme")
        tenant = registry.get_by_name("findme")
        assert tenant is not None
        assert tenant.name == "findme"

    def test_duplicate_name_raises(self, registry):
        registry.create("unique-name")
        with pytest.raises(Exception):
            registry.create("unique-name")

    def test_list_tenants(self, registry):
        registry.create("t1")
        registry.create("t2")
        registry.create("t3")
        tenants = registry.list_tenants()
        assert len(tenants) == 3

    def test_delete_tenant(self, registry):
        tenant, _ = registry.create("to-delete")
        assert registry.delete(tenant.tenant_id)
        assert registry.get(tenant.tenant_id) is None
        # Should still be in inactive list
        all_tenants = registry.list_tenants(include_inactive=True)
        assert any(t.tenant_id == tenant.tenant_id for t in all_tenants)

    def test_suspend_tenant(self, registry):
        tenant, _ = registry.create("to-suspend")
        registry.update_status(tenant.tenant_id, "suspended")
        updated = registry.get(tenant.tenant_id)
        assert updated.status == "suspended"

    def test_to_dict_hides_api_key(self, registry):
        tenant, _ = registry.create("secret")
        d = tenant.to_dict()
        assert "api_key_hash" not in d


class TestTenantAuth:

    def test_authenticate_valid_key(self, registry):
        tenant, api_key = registry.create("auth-test")
        result = registry.authenticate(api_key)
        assert result is not None
        assert result.tenant_id == tenant.tenant_id

    def test_authenticate_invalid_key(self, registry):
        registry.create("auth-test2")
        result = registry.authenticate("totally-wrong-key")
        assert result is None

    def test_authenticate_suspended_tenant(self, registry):
        tenant, api_key = registry.create("suspended-co")
        registry.update_status(tenant.tenant_id, "suspended")
        # authenticate checks status = 'active'
        result = registry.authenticate(api_key)
        assert result is None

    def test_rotate_key(self, registry):
        tenant, old_key = registry.create("rotate-test")
        new_key = registry.rotate_key(tenant.tenant_id)
        assert new_key is not None
        assert new_key != old_key
        # Old key should no longer work
        assert registry.authenticate(old_key) is None
        # New key should work
        assert registry.authenticate(new_key) is not None


class TestNamespaceScoping:

    def test_scoped_namespace(self):
        tenant = TenantIdentity(tenant_id="abc123", name="test")
        assert tenant.scoped_namespace("my-graph") == "abc123:my-graph"

    def test_unscoped_namespace_own(self):
        tenant = TenantIdentity(tenant_id="abc123", name="test")
        assert tenant.unscoped_namespace("abc123:my-graph") == "my-graph"

    def test_unscoped_namespace_other_tenant(self):
        tenant = TenantIdentity(tenant_id="abc123", name="test")
        assert tenant.unscoped_namespace("other999:their-graph") is None

    def test_ns_prefix(self):
        tenant = TenantIdentity(tenant_id="abc123", name="test")
        assert tenant.ns_prefix() == "abc123:"
