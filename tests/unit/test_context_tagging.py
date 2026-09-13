"""
Unit tests for ContextHub tagging, sensitivity filtering, and agent-based export.
"""

import pytest
from contextcore.context.hub import (
    ContextHub,
    ContextItem,
    ContextRole,
    Sensitivity,
)


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def hub():
    """Pre-populated hub with items at various sensitivity levels."""
    h = ContextHub(system_prompt="Test system prompt")

    # public item
    h.add_text("Public quarterly report", role="background", label="Q4 Report",
               tags=["financial", "q4"], sensitivity="public")

    # internal item
    h.add_text("Internal roadmap draft", role="background", label="Roadmap",
               tags=["strategy"], sensitivity="internal")

    # confidential item (PII)
    h.add_text("Customer SSN: 123-45-6789", role="retrieved", label="Customer PII",
               tags=["pii", "pii:ssns", "customer"], sensitivity="confidential")

    # restricted item
    h.add_text("Board compensation details", role="background", label="Board Comp",
               tags=["executive", "compensation"], sensitivity="restricted")

    # agent contribution (internal, tagged)
    h.contribute(
        content="Summary of Q4 financials",
        agent_id="agent-summarizer",
        tags=["financial", "summary"],
        sensitivity="internal",
    )

    return h


# ── ContextItem tagging ──────────────────────────────────────────────

class TestContextItemTags:

    def test_item_has_tags_and_sensitivity(self):
        item = ContextItem(content="test", tags=["a", "b"], sensitivity="confidential")
        assert item.tags == ["a", "b"]
        assert item.sensitivity == "confidential"
        assert item.pii_detected is False

    def test_item_defaults(self):
        item = ContextItem(content="test")
        assert item.tags == []
        assert item.sensitivity == "public"
        assert item.pii_detected is False

    def test_to_dict_includes_tags(self):
        item = ContextItem(content="x", tags=["pii"], sensitivity="confidential", pii_detected=True)
        d = item.to_dict()
        assert d["tags"] == ["pii"]
        assert d["sensitivity"] == "confidential"
        assert d["pii_detected"] is True

    def test_sensitivity_enum_values(self):
        assert Sensitivity.PUBLIC == "public"
        assert Sensitivity.INTERNAL == "internal"
        assert Sensitivity.CONFIDENTIAL == "confidential"
        assert Sensitivity.RESTRICTED == "restricted"


# ── Hub filtering by tags ────────────────────────────────────────────

class TestHubTagFiltering:

    def test_filter_by_single_tag(self, hub):
        items = hub.filter(tags=["financial"])
        labels = [i.label for i in items]
        assert "Q4 Report" in labels
        assert "Summary of Q4 financials" in [i.content for i in items] or \
               any("financial" in (i.tags or []) for i in items)

    def test_filter_by_multiple_tags_union(self, hub):
        """Tags filter uses OR logic — match any of the supplied tags."""
        items = hub.filter(tags=["strategy", "executive"])
        labels = [i.label for i in items]
        assert "Roadmap" in labels
        assert "Board Comp" in labels

    def test_filter_by_nonexistent_tag(self, hub):
        items = hub.filter(tags=["nonexistent"])
        assert len(items) == 0

    def test_filter_by_exact_sensitivity(self, hub):
        items = hub.filter(sensitivity="confidential")
        assert len(items) == 1
        assert items[0].label == "Customer PII"

    def test_filter_by_max_sensitivity(self, hub):
        # Up to internal → should include public + internal, not confidential/restricted
        items = hub.filter(max_sensitivity="internal")
        sensitivities = {i.sensitivity for i in items}
        assert "confidential" not in sensitivities
        assert "restricted" not in sensitivities
        assert "public" in sensitivities
        assert "internal" in sensitivities

    def test_filter_max_sensitivity_public(self, hub):
        items = hub.filter(max_sensitivity="public")
        assert all(i.sensitivity == "public" for i in items)

    def test_filter_max_sensitivity_restricted(self, hub):
        """Restricted ceiling should return everything."""
        items = hub.filter(max_sensitivity="restricted")
        # Should include all non-system items (system prompt has no sensitivity override)
        assert len(items) >= 4


# ── Agent-based export filtering ─────────────────────────────────────

class TestFilterForAgent:

    def test_read_agent_sees_only_public(self, hub):
        items = hub.filter_for_agent(access_level="read")
        sensitivities = {i.sensitivity for i in items}
        assert sensitivities <= {"public"}

    def test_write_agent_sees_public_and_internal(self, hub):
        items = hub.filter_for_agent(access_level="write")
        sensitivities = {i.sensitivity for i in items}
        assert "public" in sensitivities
        assert "internal" in sensitivities
        assert "confidential" not in sensitivities
        assert "restricted" not in sensitivities

    def test_admin_sees_everything(self, hub):
        items = hub.filter_for_agent(access_level="admin")
        sensitivities = {i.sensitivity for i in items}
        assert "public" in sensitivities
        assert "restricted" in sensitivities

    def test_read_with_allowed_tags_sees_tagged_confidential(self, hub):
        """A read-level agent with allowed_tags=["pii"] can see the PII item."""
        items = hub.filter_for_agent(access_level="read", allowed_tags=["pii"])
        labels = [i.label for i in items]
        assert "Customer PII" in labels
        # Should still NOT see untagged restricted items
        assert "Board Comp" not in labels

    def test_write_with_allowed_tags_adds_to_baseline(self, hub):
        items = hub.filter_for_agent(access_level="write", allowed_tags=["executive"])
        labels = [i.label for i in items]
        assert "Board Comp" in labels  # from allowed_tags
        assert "Roadmap" in labels     # from write baseline


# ── Serialization round-trip ─────────────────────────────────────────

class TestSerializationWithTags:

    def test_save_load_preserves_tags(self, hub, tmp_path):
        path = str(tmp_path / "hub.json")
        hub.save(path)
        loaded = ContextHub.load(path)

        for orig, loaded_item in zip(hub.items(), loaded.items()):
            assert orig.tags == loaded_item.tags
            assert orig.sensitivity == loaded_item.sensitivity
            assert orig.pii_detected == loaded_item.pii_detected

    def test_to_dict_includes_tag_fields(self, hub):
        d = hub.to_dict()
        for item_dict in d["items"]:
            assert "tags" in item_dict
            assert "sensitivity" in item_dict
            assert "pii_detected" in item_dict
