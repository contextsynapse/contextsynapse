"""
Unit tests for PII masking modes in ContextHub.filter_for_agent().
"""

import pytest
from contextcore.context.hub import ContextHub, ContextItem


@pytest.fixture
def hub_with_pii():
    """Hub containing a mix of public, PII, and restricted items."""
    h = ContextHub()
    h.add_text("Public report", sensitivity="public", tags=["financial"])
    h.add_text(
        "Customer email: alice@example.com, SSN: 123-45-6789",
        sensitivity="confidential",
        tags=["pii", "customer"],
    )
    # Mark the PII item
    h._items[-1].pii_detected = True

    h.add_text("Internal roadmap", sensitivity="internal", tags=["strategy"])
    return h


class TestPIIModeBlock:

    def test_block_removes_pii_items(self, hub_with_pii):
        items = hub_with_pii.filter_for_agent(
            access_level="admin", pii_mode="block"
        )
        contents = [i.content for i in items]
        assert "Public report" in contents
        assert "Internal roadmap" in contents
        assert not any("alice@example.com" in c for c in contents)

    def test_block_is_default(self, hub_with_pii):
        items = hub_with_pii.filter_for_agent(access_level="admin")
        contents = [i.content for i in items]
        assert not any("alice@example.com" in c for c in contents)


class TestPIIModeMask:

    def test_mask_replaces_pii_patterns(self, hub_with_pii):
        items = hub_with_pii.filter_for_agent(
            access_level="admin", pii_mode="mask"
        )
        pii_items = [i for i in items if i.pii_detected]
        assert len(pii_items) == 1
        # Original PII should be replaced
        assert "alice@example.com" not in pii_items[0].content
        assert "123-45-6789" not in pii_items[0].content
        # Masking placeholder should be present
        assert "***" in pii_items[0].content or "****" in pii_items[0].content

    def test_mask_preserves_non_pii(self, hub_with_pii):
        items = hub_with_pii.filter_for_agent(
            access_level="admin", pii_mode="mask"
        )
        contents = [i.content for i in items]
        assert "Public report" in contents
        assert "Internal roadmap" in contents


class TestPIIModeRedact:

    def test_redact_replaces_entire_content(self, hub_with_pii):
        items = hub_with_pii.filter_for_agent(
            access_level="admin", pii_mode="redact"
        )
        pii_items = [i for i in items if i.pii_detected]
        assert len(pii_items) == 1
        assert pii_items[0].content == "[REDACTED — contains PII]"

    def test_redact_preserves_non_pii(self, hub_with_pii):
        items = hub_with_pii.filter_for_agent(
            access_level="admin", pii_mode="redact"
        )
        contents = [i.content for i in items]
        assert "Public report" in contents


class TestPIIModeAllow:

    def test_allow_keeps_pii_unmodified(self, hub_with_pii):
        items = hub_with_pii.filter_for_agent(
            access_level="admin", pii_mode="allow"
        )
        contents = [i.content for i in items]
        assert any("alice@example.com" in c for c in contents)
        assert any("123-45-6789" in c for c in contents)


class TestPIIModeWithAllowedTags:

    def test_agent_with_pii_tag_sees_raw_pii(self, hub_with_pii):
        """Agent with allowed_tags=["pii"] should see raw PII regardless of mode."""
        items = hub_with_pii.filter_for_agent(
            access_level="read", allowed_tags=["pii"], pii_mode="block"
        )
        contents = [i.content for i in items]
        assert any("alice@example.com" in c for c in contents)

    def test_agent_without_pii_tag_gets_blocked(self, hub_with_pii):
        """Agent with allowed_tags but NOT "pii" should still have PII blocked."""
        items = hub_with_pii.filter_for_agent(
            access_level="read", allowed_tags=["financial"], pii_mode="block"
        )
        contents = [i.content for i in items]
        assert not any("alice@example.com" in c for c in contents)


class TestPIIModeDoesNotMutateOriginal:

    def test_mask_does_not_alter_hub_items(self, hub_with_pii):
        original_content = hub_with_pii._items[1].content
        hub_with_pii.filter_for_agent(access_level="admin", pii_mode="mask")
        assert hub_with_pii._items[1].content == original_content

    def test_redact_does_not_alter_hub_items(self, hub_with_pii):
        original_content = hub_with_pii._items[1].content
        hub_with_pii.filter_for_agent(access_level="admin", pii_mode="redact")
        assert hub_with_pii._items[1].content == original_content
