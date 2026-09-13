"""
Integration tests for the full pipeline:
  ingest (with PII scan) → tag → ACL filter → export

These tests exercise the real ContextSessionManager, UnifiedIngestor,
AgentRegistry, and ContextHub together — no mocks, no HTTP layer.
"""

import pytest
from contextcore.context.session import ContextSessionManager
from contextcore.context.agents import AgentRegistry
from contextcore.context.hub import ContextHub
from contextcore.context.ingest import UnifiedIngestor
from contextcore.context.blob import BlobStore


# ── Fixtures ──────────────────────────────────────────────────────────

@pytest.fixture
def agent_registry(tmp_db_path):
    return AgentRegistry(db_path=tmp_db_path)


@pytest.fixture
def session_mgr(tmp_db_path):
    return ContextSessionManager(db_path=tmp_db_path)


@pytest.fixture
def ingestor(session_mgr, agent_registry):
    return UnifiedIngestor(
        session_manager=session_mgr,
        agent_registry=agent_registry,
        blob_store=BlobStore(),
    )


@pytest.fixture
def setup(agent_registry, session_mgr):
    """Register agents and create a session."""
    admin_agent, admin_key = agent_registry.register("admin-bot", role="admin")
    reader_agent, reader_key = agent_registry.register("reader-bot", role="reader")
    analyst_agent, analyst_key = agent_registry.register("analyst-bot", role="analyst")

    session = session_mgr.create_session("test-workspace", owner_agent_id=admin_agent.agent_id)

    # Grant access:
    # reader  → read, no extra tags
    # analyst → read, allowed to see "financial" and "pii" tagged items
    session_mgr.grant_access(session.session_id, reader_agent.agent_id, "read")
    session_mgr.grant_access(session.session_id, analyst_agent.agent_id, "read",
                             allowed_tags=["financial", "pii"])

    return {
        "session": session,
        "admin": admin_agent,
        "reader": reader_agent,
        "analyst": analyst_agent,
    }


# ── Tests ─────────────────────────────────────────────────────────────

class TestIngestPIIAutoTagging:

    def test_text_with_pii_auto_tagged_confidential(self, ingestor, setup):
        result = ingestor.ingest(
            session_id=setup["session"].session_id,
            agent_id=setup["admin"].agent_id,
            data="Customer email: alice@example.com, SSN: 123-45-6789",
            tags=["customer"],
        )
        assert result["pii_detected"] is True
        assert "pii" in result["tags"]
        assert "pii:ssns" in result["tags"]
        assert "pii:emails" in result["tags"]
        assert "customer" in result["tags"]
        assert result["sensitivity"] == "confidential"

    def test_text_without_pii_stays_public(self, ingestor, setup):
        result = ingestor.ingest(
            session_id=setup["session"].session_id,
            agent_id=setup["admin"].agent_id,
            data="The company reported strong Q4 earnings.",
            tags=["financial"],
        )
        assert result["pii_detected"] is False
        assert result["sensitivity"] == "public"
        assert "financial" in result["tags"]

    def test_explicit_sensitivity_overrides_auto(self, ingestor, setup):
        result = ingestor.ingest(
            session_id=setup["session"].session_id,
            agent_id=setup["admin"].agent_id,
            data="alice@example.com",  # has PII
            sensitivity="restricted",  # explicit override
        )
        assert result["pii_detected"] is True
        assert result["sensitivity"] == "restricted"

    def test_auto_pii_scan_disabled(self, ingestor, setup):
        result = ingestor.ingest(
            session_id=setup["session"].session_id,
            agent_id=setup["admin"].agent_id,
            data="SSN: 123-45-6789",
            auto_pii_scan=False,
        )
        assert result["pii_detected"] is False
        assert result["sensitivity"] == "public"

    def test_structured_data_pii_scan(self, ingestor, setup):
        result = ingestor.ingest(
            session_id=setup["session"].session_id,
            agent_id=setup["admin"].agent_id,
            data={"name": "Alice", "email": "alice@example.com", "type": "Contact"},
        )
        assert result["pii_detected"] is True
        assert "pii" in result["tags"]


class TestEndToEndTaggedExport:

    def test_reader_only_sees_public(self, ingestor, setup, session_mgr):
        sid = setup["session"].session_id

        # Ingest public item
        ingestor.ingest(sid, setup["admin"].agent_id,
                        "Q4 revenue was $10M", tags=["financial"], sensitivity="public")

        # Ingest confidential item
        ingestor.ingest(sid, setup["admin"].agent_id,
                        "CEO SSN: 123-45-6789", tags=["executive", "pii"])

        # Build hub with both items
        hub = ContextHub()
        hub.add_text("Q4 revenue was $10M", tags=["financial"], sensitivity="public")
        hub.add_text("CEO SSN: 123-45-6789", tags=["executive", "pii"], sensitivity="confidential")

        # Reader (read access, no tags) → only public
        access = session_mgr.get_agent_access(sid, setup["reader"].agent_id)
        visible = hub.filter_for_agent(access["access_level"], access["allowed_tags"])
        assert all(i.sensitivity == "public" for i in visible)
        assert len(visible) == 1

    def test_analyst_sees_public_plus_tagged_confidential(self, ingestor, setup, session_mgr):
        sid = setup["session"].session_id

        hub = ContextHub()
        hub.add_text("Q4 revenue was $10M", tags=["financial"], sensitivity="public")
        hub.add_text("Customer email: alice@example.com", tags=["pii", "customer"],
                     sensitivity="confidential")
        hub.add_text("Board compensation", tags=["executive"], sensitivity="restricted")

        # Analyst (read + allowed_tags=["financial", "pii"])
        access = session_mgr.get_agent_access(sid, setup["analyst"].agent_id)
        visible = hub.filter_for_agent(access["access_level"], access["allowed_tags"])
        labels_or_contents = [i.content for i in visible]

        assert "Q4 revenue was $10M" in labels_or_contents        # public
        assert "Customer email: alice@example.com" in labels_or_contents  # tagged pii
        assert "Board compensation" not in labels_or_contents      # restricted, no tag match

    def test_admin_sees_all(self, ingestor, setup, session_mgr):
        sid = setup["session"].session_id

        hub = ContextHub()
        hub.add_text("Public", sensitivity="public")
        hub.add_text("Internal", sensitivity="internal")
        hub.add_text("Confidential", sensitivity="confidential")
        hub.add_text("Restricted", sensitivity="restricted")

        access = session_mgr.get_agent_access(sid, setup["admin"].agent_id)
        visible = hub.filter_for_agent(access["access_level"], access["allowed_tags"])
        assert len(visible) == 4


class TestIngestProvenance:

    def test_ingest_result_carries_tags_and_sensitivity(self, ingestor, setup):
        """Even without a backing graph, the result dict carries tag info."""
        sid = setup["session"].session_id
        result = ingestor.ingest(
            sid, setup["admin"].agent_id,
            data="Secret plans",
            tags=["strategy"],
            sensitivity="restricted",
        )
        assert result["tags"] == ["strategy"]
        assert result["sensitivity"] == "restricted"
        assert result["pii_detected"] is False

    def test_pii_ingest_result_tags(self, ingestor, setup):
        sid = setup["session"].session_id
        result = ingestor.ingest(
            sid, setup["admin"].agent_id,
            data="Card: 4111-1111-1111-1111",
            tags=["payment"],
        )
        assert result["pii_detected"] is True
        assert "pii" in result["tags"]
        assert "payment" in result["tags"]
        assert result["sensitivity"] == "confidential"
