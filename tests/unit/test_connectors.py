"""Tests for the Connector framework — base, Kafka, manager."""

import pytest
from contextcore.connectors.base import (
    BaseConnector, ConnectorManager, ConnectorConfig, ConnectorDocument,
)
from contextcore.core.registry import GraphRegistry


class MockConnector(BaseConnector):
    """Test connector that returns canned documents."""
    def __init__(self, docs=None):
        config = ConnectorConfig(
            connector_type="mock", name="TestMock",
            target_context_id="ctx_test",
        )
        super().__init__(config)
        self._docs = docs or []

    def poll(self):
        return self._docs


class TestConnectorDocument:
    def test_content_hash_deterministic(self):
        d1 = ConnectorDocument(url="http://a.com", title="Test", content="Hello")
        d2 = ConnectorDocument(url="http://a.com", title="Test", content="Hello")
        assert d1.content_hash == d2.content_hash

    def test_content_hash_differs(self):
        d1 = ConnectorDocument(url="http://a.com", title="A", content="Hello")
        d2 = ConnectorDocument(url="http://b.com", title="B", content="World")
        assert d1.content_hash != d2.content_hash


class TestBaseConnector:
    def test_dedup(self):
        doc = ConnectorDocument(title="Same", content="Same content")
        c = MockConnector(docs=[doc, doc, doc])
        result = c._dedup(c.poll())
        assert len(result) == 1  # deduped to 1

    def test_status_tracking(self):
        c = MockConnector()
        assert c.status.documents_ingested == 0
        assert c.status.active is True


class TestConnectorManager:
    def test_register_and_list(self):
        mgr = ConnectorManager()
        c = MockConnector()
        cid = mgr.register(c)
        assert cid
        configs = mgr.list_connectors()
        assert len(configs) == 1
        assert configs[0].connector_type == "mock"

    def test_unregister(self):
        mgr = ConnectorManager()
        c = MockConnector()
        cid = mgr.register(c)
        assert mgr.unregister(cid) is True
        assert len(mgr.list_connectors()) == 0

    def test_poll_one(self):
        mgr = ConnectorManager()
        docs = [ConnectorDocument(title="Article 1", content="Content about elections")]
        c = MockConnector(docs=docs)
        cid = mgr.register(c)
        result = mgr.poll_one(cid)
        assert len(result) == 1
        assert c.status.documents_ingested == 1

    def test_poll_all(self):
        mgr = ConnectorManager()
        c1 = MockConnector(docs=[ConnectorDocument(title="A", content="X")])
        c2 = MockConnector(docs=[ConnectorDocument(title="B", content="Y")])
        id1 = mgr.register(c1)
        id2 = mgr.register(c2)
        results = mgr.poll_all()
        assert results[id1] == 1
        assert results[id2] == 1

    def test_poll_deduplicates(self):
        mgr = ConnectorManager()
        doc = ConnectorDocument(title="Same", content="Same article")
        c = MockConnector(docs=[doc])
        cid = mgr.register(c)
        mgr.poll_one(cid)
        # Second poll — same doc should be deduped
        result = mgr.poll_one(cid)
        assert len(result) == 0
        assert c.status.documents_ingested == 1  # still 1

    def test_inactive_connector_skipped(self):
        mgr = ConnectorManager()
        c = MockConnector(docs=[ConnectorDocument(title="X", content="Y")])
        c.config.active = False
        cid = mgr.register(c)
        results = mgr.poll_all()
        assert results == {}

    def test_direct_ingest_fallback(self):
        """When no ingest_fn, manager uses direct graph write."""
        reg = GraphRegistry()
        reg.create_graph("test_ctx_ns")

        # Mock context manager
        class MockCtx:
            context_id = "ctx_1"
            graph_namespace = "test_ctx_ns"
        class MockCM:
            def get_context(self, cid):
                return MockCtx()

        mgr = ConnectorManager(graph_registry=reg, context_manager=MockCM())
        doc = ConnectorDocument(title="Direct Test", content="Direct content for testing the fallback path")
        c = MockConnector(docs=[doc])
        c.config.target_context_id = "ctx_1"
        cid = mgr.register(c)
        mgr.poll_one(cid)

        # Verify node was written to graph
        db = reg.get_graph("test_ctx_ns")
        docs_in_graph = [n for n in db.get_all_nodes() if getattr(n, "label", "") == "Document"]
        assert len(docs_in_graph) == 1
        assert docs_in_graph[0].properties["title"] == "Direct Test"

    def test_status_tracking(self):
        mgr = ConnectorManager()
        c = MockConnector(docs=[ConnectorDocument(title="A", content="B")])
        cid = mgr.register(c)
        mgr.poll_one(cid)
        statuses = mgr.list_status()
        assert len(statuses) == 1
        assert statuses[0].documents_ingested == 1
        assert statuses[0].last_polled is not None


class TestConnectorConfig:
    def test_to_dict_hides_api_key(self):
        config = ConnectorConfig(
            connector_type="api", name="NewsAPI",
            config={"api_key": "secret-123", "query": "India"},
        )
        d = config.to_dict()
        assert "api_key" not in d["config"]
        assert d["config"]["query"] == "India"
