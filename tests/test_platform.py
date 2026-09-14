"""Platform Core Tests — verifies all platform capabilities work.

Run: pytest tests/test_platform.py -v
"""
import pytest


class TestFieldEncryption:
    def test_encrypt_decrypt(self):
        from contextsynapse.security.field_encryption import FieldEncryptor
        enc = FieldEncryptor()
        original = "ABCPS1234K"
        encrypted = enc.encrypt_value(original)
        assert encrypted.startswith(("enc:", "b64:"))
        assert enc.decrypt_value(encrypted) == original

    def test_mask_last4(self):
        from contextsynapse.security.field_encryption import mask_last4
        assert mask_last4("ABCPS1234K") == "XXXXXX234K"
        assert mask_last4("1234") == "XXXX"  # 4 chars = fully masked
        assert mask_last4("AB") == "XX"

    def test_mask_email(self):
        from contextsynapse.security.field_encryption import mask_email
        assert mask_email("alice@example.com") == "a***e@example.com"

    def test_mask_phone(self):
        from contextsynapse.security.field_encryption import mask_phone
        assert mask_phone("+91-98765-43210") == "XXXXXXXX3210"

    def test_record_processing(self):
        from contextsynapse.security.field_encryption import FieldEncryptor
        enc = FieldEncryptor()
        enc.register("test_table", {
            "ssn": {"mask": "last4", "decrypt_roles": ["admin"]},
        })
        record = {"name": "Alice", "ssn": "123-45-6789"}
        # FM can't see SSN
        fm_view = enc.process_record(record, "test_table", user_roles=["fm"])
        assert "XXXX" in fm_view["ssn"]
        assert fm_view["name"] == "Alice"
        # Admin can see SSN
        admin_view = enc.process_record(record, "test_table", user_roles=["admin"])
        assert admin_view["ssn"] == "123-45-6789"


class TestRBACFramework:
    def test_register_vertical(self):
        from contextsynapse.security.rbac_framework import RoleRegistry
        reg = RoleRegistry()
        reg.register_vertical("test", {
            "editor": {"permissions": ["read", "write"], "label": "Editor", "global_access": False},
            "viewer": {"permissions": ["read"], "label": "Viewer", "global_access": False},
        })
        assert reg.can(["editor"], "write")
        assert not reg.can(["viewer"], "write")
        assert reg.can(["viewer"], "read")

    def test_multi_role(self):
        from contextsynapse.security.rbac_framework import RoleRegistry
        reg = RoleRegistry()
        reg.register_vertical("test", {
            "role_a": {"permissions": ["perm_1", "perm_2"], "label": "A", "global_access": False},
            "role_b": {"permissions": ["perm_3"], "label": "B", "global_access": False},
        })
        # Multi-role: union of permissions
        perms = reg.effective_permissions(["role_a", "role_b"])
        assert perms == {"perm_1", "perm_2", "perm_3"}

    def test_global_access(self):
        from contextsynapse.security.rbac_framework import RoleRegistry
        reg = RoleRegistry()
        reg.register_vertical("test", {
            "admin": {"permissions": ["all"], "label": "Admin", "global_access": True},
            "user": {"permissions": ["read"], "label": "User", "global_access": False},
        })
        assert reg.is_global(["admin"])
        assert not reg.is_global(["user"])


class TestWorkflowRegistry:
    def test_register_workflow(self):
        from contextsynapse.workflow.registry import WorkflowTypeRegistry
        reg = WorkflowTypeRegistry()
        reg.register("test_flow", {
            "label": "Test",
            "approvers": ["admin"],
            "auto_approve_below": 1000,
            "vertical": "test",
        })
        assert reg.exists("test_flow")
        config = reg.get("test_flow")
        assert config["label"] == "Test"
        assert config["approvers"] == ["admin"]

    def test_list_by_vertical(self):
        from contextsynapse.workflow.registry import WorkflowTypeRegistry
        reg = WorkflowTypeRegistry()
        reg.register("a", {"label": "A", "vertical": "pms"})
        reg.register("b", {"label": "B", "vertical": "mf"})
        pms = reg.list_all("pms")
        assert "a" in pms
        assert "b" not in pms


class TestPMSRegistration:
    def test_pms_registers_with_platform(self):
        from contextsynapse.security.rbac_framework import get_role_registry
        from contextsynapse.workflow.registry import get_workflow_registry
        from contextsynapse.security.field_encryption import get_field_encryptor

        # Reset singletons for clean test
        import contextsynapse.security.rbac_framework as rf
        import contextsynapse.workflow.registry as wr
        import contextsynapse.security.field_encryption as fe
        rf._instance = None
        wr._instance = None
        fe._instance = None

        from verticals.pms.backend.register import register_pms_vertical
        register_pms_vertical()

        # Verify roles
        reg = get_role_registry()
        roles = reg.list_roles("pms")
        role_names = {r["role"] for r in roles}
        assert "fund_manager" in role_names
        assert "cio" in role_names
        assert "pms_admin" in role_names

        # Verify workflows
        wf = get_workflow_registry()
        assert wf.exists("trade")
        assert wf.exists("client_onboard")

        # Verify PII
        enc = get_field_encryptor()
        assert "pan" in enc.pii_fields("pms_clients")


class TestParallelPipeline:
    def test_parallel_execution(self):
        import time
        from contextsynapse.pipelines.parallel import run_parallel

        def slow_task(n=1):
            time.sleep(0.1)
            return f"done_{n}"

        tasks = [
            ("t1", slow_task, {"n": 1}),
            ("t2", slow_task, {"n": 2}),
            ("t3", slow_task, {"n": 3}),
        ]
        start = time.time()
        result = run_parallel(tasks, max_workers=3, timeout=5)
        elapsed = time.time() - start

        assert result["completed"] == 3
        assert result["failed"] == 0
        assert elapsed < 0.5  # parallel should be < 0.5s, not 0.3s sequential
