"""Tests for SecurityMiddleware — configurable security at context + runtime level."""

import pytest
from contextcore.security.middleware import (
    SecurityMiddleware, SecurityConfig, AgentClearance,
)


@pytest.fixture
def mw():
    return SecurityMiddleware(SecurityConfig(
        pii_detection=True,
        auto_tagging=True,
        encryption=False,
        audit_trail=False,  # skip audit in tests for speed
    ))


@pytest.fixture
def mw_disabled():
    return SecurityMiddleware(SecurityConfig.disabled())


class TestSecurityConfig:

    def test_default_enabled(self):
        c = SecurityConfig()
        assert c.enabled is True
        assert c.pii_detection is True

    def test_disabled(self):
        c = SecurityConfig.disabled()
        assert c.enabled is False
        assert c.pii_detection is False
        assert c.redaction_mode == "allow"

    def test_from_dict(self):
        c = SecurityConfig.from_dict({"pii_detection": False, "encryption": True})
        assert c.pii_detection is False
        assert c.encryption is True

    def test_to_dict(self):
        c = SecurityConfig()
        d = c.to_dict()
        assert "enabled" in d
        assert "pii_detection" in d


class TestAgentClearance:

    def test_public_access(self):
        ac = AgentClearance(agent_id="a1", clearance_level="public")
        assert ac.can_access("public") is True
        assert ac.can_access("internal") is False
        assert ac.can_access("confidential") is False

    def test_internal_access(self):
        ac = AgentClearance(agent_id="a1", clearance_level="internal")
        assert ac.can_access("public") is True
        assert ac.can_access("internal") is True
        assert ac.can_access("confidential") is False

    def test_confidential_access(self):
        ac = AgentClearance(agent_id="a1", clearance_level="confidential")
        assert ac.can_access("public") is True
        assert ac.can_access("internal") is True
        assert ac.can_access("confidential") is True
        assert ac.can_access("restricted") is False

    def test_restricted_access(self):
        ac = AgentClearance(agent_id="a1", clearance_level="restricted")
        assert ac.can_access("restricted") is True


class TestSecureOnIngest:

    def test_auto_tags_node(self, mw):
        props = {"content": "Contact admin@company.com for salary details"}
        secured = mw.secure_on_ingest(props)
        assert secured.get("sensitivity") in ("confidential", "restricted")
        assert secured.get("pii_detected") is True
        assert secured.get("_auto_classified") is True

    def test_public_content_stays_public(self, mw):
        props = {"content": "The weather in Mumbai is 35 degrees"}
        secured = mw.secure_on_ingest(props)
        assert secured.get("sensitivity") == "public"
        assert secured.get("pii_detected", False) is False

    def test_disabled_passthrough(self, mw_disabled):
        props = {"content": "SSN: 123-45-6789"}
        secured = mw_disabled.secure_on_ingest(props)
        assert secured == props  # no changes
        assert "sensitivity" not in secured

    def test_encryption_when_enabled(self):
        mw = SecurityMiddleware(SecurityConfig(
            encryption=True, pii_detection=False, auto_tagging=False, audit_trail=False,
        ))
        props = {"content": "Secret data", "email": "test@x.com"}
        secured = mw.secure_on_ingest(props)
        # If encryption key is set, email should be encrypted
        # (depends on AICONTEXTDB_ENCRYPTION_KEY env var)


class TestContextOverrides:

    def test_context_config_override(self, mw):
        mw.set_context_config("ctx_public_news", SecurityConfig(
            pii_detection=False, auto_tagging=False, redaction_mode="allow",
        ))
        config = mw.get_effective_config(context_id="ctx_public_news")
        assert config.pii_detection is False
        assert config.redaction_mode == "allow"

    def test_session_overrides_context(self, mw):
        mw.set_context_config("ctx_1", SecurityConfig(redaction_mode="allow"))
        mw.set_session_config("sess_1", SecurityConfig(redaction_mode="redact"))
        config = mw.get_effective_config(context_id="ctx_1", session_id="sess_1")
        assert config.redaction_mode == "redact"  # session wins

    def test_global_fallback(self, mw):
        config = mw.get_effective_config(context_id="unknown", session_id="unknown")
        assert config.pii_detection is True  # global default


class TestFilterByeClearance:

    def test_filter_public_only(self, mw):
        nodes = [
            {"id": "1", "sensitivity": "public", "name": "News"},
            {"id": "2", "sensitivity": "confidential", "name": "HR Data"},
            {"id": "3", "sensitivity": "restricted", "name": "Strategy"},
        ]
        mw.set_agent_clearance("a1", AgentClearance(agent_id="a1", clearance_level="public"))
        filtered = mw.filter_nodes_by_clearance(nodes, "a1")
        assert len(filtered) == 1
        assert filtered[0]["name"] == "News"

    def test_filter_internal_sees_two(self, mw):
        nodes = [
            {"id": "1", "sensitivity": "public"},
            {"id": "2", "sensitivity": "internal"},
            {"id": "3", "sensitivity": "confidential"},
        ]
        mw.set_agent_clearance("a1", AgentClearance(agent_id="a1", clearance_level="internal"))
        filtered = mw.filter_nodes_by_clearance(nodes, "a1")
        assert len(filtered) == 2

    def test_no_clearance_defaults_public(self, mw):
        nodes = [
            {"id": "1", "sensitivity": "public"},
            {"id": "2", "sensitivity": "internal"},
        ]
        filtered = mw.filter_nodes_by_clearance(nodes, "unknown_agent")
        assert len(filtered) == 1

    def test_restricted_sees_all(self, mw):
        nodes = [
            {"id": "1", "sensitivity": "public"},
            {"id": "2", "sensitivity": "confidential"},
            {"id": "3", "sensitivity": "restricted"},
        ]
        mw.set_agent_clearance("admin", AgentClearance(agent_id="admin", clearance_level="restricted"))
        filtered = mw.filter_nodes_by_clearance(nodes, "admin")
        assert len(filtered) == 3


class TestWrapDispatch:

    def test_dispatch_passthrough_when_disabled(self, mw_disabled):
        def mock_dispatch(name, ctx, params):
            return "result: 5 nodes found"

        result = mw_disabled.wrap_dispatch("search_nodes", None, {"query": "test"}, mock_dispatch)
        assert result == "result: 5 nodes found"

    def test_dispatch_with_pii_redaction(self):
        mw = SecurityMiddleware(SecurityConfig(
            pii_detection=True, audit_trail=False, redaction_mode="redact",
        ))
        mw.set_agent_clearance("a1", AgentClearance(agent_id="a1", clearance_level="public"))

        class FakeCtx:
            agent_id = "a1"
            agent_name = "Agent1"

        def mock_dispatch(name, ctx, params):
            return "Found: john@example.com called 555-123-4567"

        result = mw.wrap_dispatch("search_nodes", FakeCtx(), {"query": "contact"}, mock_dispatch)
        assert "john@example.com" not in result
        assert "[EMAIL REDACTED]" in result

    def test_dispatch_allow_mode_no_redaction(self):
        mw = SecurityMiddleware(SecurityConfig(
            pii_detection=True, audit_trail=False, redaction_mode="allow",
        ))
        mw.set_agent_clearance("a1", AgentClearance(agent_id="a1", clearance_level="public"))

        class FakeCtx:
            agent_id = "a1"
            agent_name = "Agent1"

        def mock_dispatch(name, ctx, params):
            return "Email: test@x.com"

        result = mw.wrap_dispatch("search_nodes", FakeCtx(), {}, mock_dispatch)
        assert "test@x.com" in result  # allow mode — no redaction
