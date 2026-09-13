"""Tests for graph construction operators (build_edges)."""
from __future__ import annotations

from unittest.mock import MagicMock
from dataclasses import dataclass, field
from typing import Any, Dict

import pytest


@dataclass
class Chunk:
    """Local Chunk for tests."""
    content: str
    index: int
    chunk_type: str = "paragraph"
    metadata: Dict[str, Any] = field(default_factory=dict)


def _make_graph_ctx():
    """Create a mock GraphContext with expected attributes."""
    ctx = MagicMock()
    ctx.entity_ids = {}
    ctx.compiled_schema = None
    ctx.add_edge = MagicMock()
    ctx.db = MagicMock()
    return ctx


class TestBuildEdges:
    def test_creates_schema_edges_from_cooccurrence(self):
        from contextcore.ingestion.universal.operators.build_edges import BuildEdgesOperator

        op = BuildEdgesOperator(min_cooccurrence=2)
        ctx = _make_graph_ctx()

        mock_schema = MagicMock()
        mock_schema.edge_types = {"USED_WITH": {"source": "Tool", "target": "Tool"}}
        ctx.compiled_schema = mock_schema
        ctx.entity_ids = {"Tool:kubernetes": "ent_k8s", "Tool:docker": "ent_docker"}

        # 2 chunks with both entities -> meets threshold
        chunks = [
            Chunk(content="K8s + Docker", index=0, metadata={"entity_node_ids": ["ent_k8s", "ent_docker"]}),
            Chunk(content="K8s + Docker again", index=1, metadata={"entity_node_ids": ["ent_k8s", "ent_docker"]}),
        ]

        op.process(chunks, ctx)
        assert ctx.add_edge.call_count >= 1

    def test_skips_below_threshold(self):
        from contextcore.ingestion.universal.operators.build_edges import BuildEdgesOperator

        op = BuildEdgesOperator(min_cooccurrence=2)
        ctx = _make_graph_ctx()

        mock_schema = MagicMock()
        mock_schema.edge_types = {"USED_WITH": {"source": "Tool", "target": "Tool"}}
        ctx.compiled_schema = mock_schema
        ctx.entity_ids = {"Tool:kubernetes": "ent_k8s", "Tool:docker": "ent_docker"}

        # Only 1 chunk -> below threshold
        chunks = [Chunk(content="K8s + Docker", index=0, metadata={"entity_node_ids": ["ent_k8s", "ent_docker"]})]

        op.process(chunks, ctx)
        assert ctx.add_edge.call_count == 0

    def test_creates_related_to_without_schema_match(self):
        from contextcore.ingestion.universal.operators.build_edges import BuildEdgesOperator

        op = BuildEdgesOperator(min_cooccurrence=2)
        ctx = _make_graph_ctx()

        mock_schema = MagicMock()
        mock_schema.edge_types = {}  # no matching edge types
        ctx.compiled_schema = mock_schema
        ctx.entity_ids = {"Tool:kubernetes": "ent_k8s", "Concept:containers": "ent_cont"}

        chunks = [
            Chunk(content="K8s containers", index=0, metadata={"entity_node_ids": ["ent_k8s", "ent_cont"]}),
            Chunk(content="K8s containers again", index=1, metadata={"entity_node_ids": ["ent_k8s", "ent_cont"]}),
        ]

        op.process(chunks, ctx)
        # Should create RELATED_TO as fallback
        assert ctx.add_edge.call_count >= 1
        # Verify it used RELATED_TO
        call_args = ctx.add_edge.call_args
        assert call_args[0][2] == "RELATED_TO"

    def test_no_schema_still_creates_related_to(self):
        from contextcore.ingestion.universal.operators.build_edges import BuildEdgesOperator

        op = BuildEdgesOperator(min_cooccurrence=1)
        ctx = _make_graph_ctx()

        ctx.compiled_schema = None
        ctx.entity_ids = {"Person:alice": "ent_alice", "Person:bob": "ent_bob"}

        chunks = [
            Chunk(content="Alice and Bob", index=0, metadata={"entity_node_ids": ["ent_alice", "ent_bob"]}),
        ]

        op.process(chunks, ctx)
        assert ctx.add_edge.call_count == 1
        call_args = ctx.add_edge.call_args
        assert call_args[0][2] == "RELATED_TO"


# ---------------------------------------------------------------------------
# ResolveEntities tests
# ---------------------------------------------------------------------------

def _make_resolve_ctx():
    """Create a mock GraphContext for resolve_entities tests."""
    ctx = MagicMock()
    ctx.entity_ids = {}
    ctx.compiled_schema = None
    ctx.add_edge = MagicMock()
    ctx.db = MagicMock()
    ctx.db.add_node = MagicMock()
    ctx.db.add_edge = MagicMock()
    ctx.db.get_node = MagicMock(return_value=None)
    ctx.db.remove_node = MagicMock()
    ctx.db.get_all_nodes = MagicMock(return_value=[])
    ctx.db.get_neighbors = MagicMock(return_value=[])
    return ctx


class TestResolveEntities:
    def test_merges_duplicate_entities(self):
        from contextcore.ingestion.universal.operators.resolve_entities import ResolveEntitiesOperator
        op = ResolveEntitiesOperator()
        ctx = _make_resolve_ctx()

        node_1 = MagicMock(id="n1", label="Tool", properties={"name": "Kubernetes"})
        node_2 = MagicMock(id="n2", label="Tool", properties={"name": "Kubernetes"})
        node_3 = MagicMock(id="n3", label="Tool", properties={"name": "Docker"})
        ctx.db.get_all_nodes.return_value = [node_1, node_2, node_3]

        op.process([], ctx)

        assert ctx.db.remove_node.call_count == 1  # n2 removed
        assert ctx.entity_ids.get("Tool:kubernetes") == "n1"

    def test_case_insensitive_merge(self):
        from contextcore.ingestion.universal.operators.resolve_entities import ResolveEntitiesOperator
        op = ResolveEntitiesOperator()
        ctx = _make_resolve_ctx()

        node_1 = MagicMock(id="n1", label="Tool", properties={"name": "Kubernetes"})
        node_2 = MagicMock(id="n2", label="Tool", properties={"name": "kubernetes"})
        ctx.db.get_all_nodes.return_value = [node_1, node_2]

        op.process([], ctx)
        assert ctx.db.remove_node.call_count == 1

    def test_different_labels_not_merged(self):
        from contextcore.ingestion.universal.operators.resolve_entities import ResolveEntitiesOperator
        op = ResolveEntitiesOperator()
        ctx = _make_resolve_ctx()

        node_1 = MagicMock(id="n1", label="Tool", properties={"name": "Kubernetes"})
        node_2 = MagicMock(id="n2", label="Concept", properties={"name": "Kubernetes"})
        ctx.db.get_all_nodes.return_value = [node_1, node_2]

        op.process([], ctx)
        assert ctx.db.remove_node.call_count == 0  # different labels, no merge

    def test_no_crash_when_db_missing_get_all_nodes(self):
        from contextcore.ingestion.universal.operators.resolve_entities import ResolveEntitiesOperator
        op = ResolveEntitiesOperator()
        ctx = _make_resolve_ctx()
        del ctx.db.get_all_nodes
        result = op.process(["chunk1"], ctx)
        assert result == ["chunk1"]

    def test_redirects_edges_on_merge(self):
        from contextcore.ingestion.universal.operators.resolve_entities import ResolveEntitiesOperator
        op = ResolveEntitiesOperator()
        ctx = _make_resolve_ctx()

        node_1 = MagicMock(id="n1", label="Tool", properties={"name": "Kubernetes"})
        node_2 = MagicMock(id="n2", label="Tool", properties={"name": "kubernetes"})
        edge = MagicMock(edge_type="USES")
        ctx.db.get_all_nodes.return_value = [node_1, node_2]
        ctx.db.get_neighbors.return_value = [("n5", edge)]

        op.process([], ctx)

        # Edge redirected from n2->n5 to n1->n5
        ctx.add_edge.assert_called_once()


# ---------------------------------------------------------------------------
# StoreDocuments tests
# ---------------------------------------------------------------------------


class TestStoreDocuments:
    def test_truncates_long_content(self):
        from contextcore.ingestion.universal.operators.store_documents import StoreDocumentsOperator

        op = StoreDocumentsOperator()
        ctx = _make_graph_ctx()

        long_text = "A" * 500
        ctx.node_ids = ["node_1"]
        mock_node = MagicMock(id="node_1", label="Fact", properties={"name": "Test", "content": long_text})
        ctx.get_node = MagicMock(return_value=mock_node)

        # Should work without crashing
        op.process([], ctx)

    def test_skips_short_content(self):
        from contextcore.ingestion.universal.operators.store_documents import StoreDocumentsOperator

        op = StoreDocumentsOperator()
        ctx = _make_graph_ctx()

        ctx.node_ids = ["node_1"]
        mock_node = MagicMock(id="node_1", label="Fact", properties={"name": "Test", "content": "Short"})
        ctx.get_node = MagicMock(return_value=mock_node)

        op.process([], ctx)
        # No truncation should happen for short content


# ---------------------------------------------------------------------------
# InferDomains tests
# ---------------------------------------------------------------------------


class TestInferDomains:
    def test_creates_domain_for_large_entity_group(self):
        from contextcore.ingestion.universal.operators.infer_domains import InferDomainsOperator

        op = InferDomainsOperator()
        ctx = _make_graph_ctx()

        ctx.entity_ids = {
            "Tool:kubernetes": "n1",
            "Tool:docker": "n2",
            "Tool:helm": "n3",
            "Tool:eks": "n4",
        }
        # add_node returns a fake domain id
        ctx.add_node = MagicMock(return_value="domain_1")

        op.process([], ctx)
        # Should create 1 Domain node (4 Tools >= threshold of 3)
        assert ctx.add_node.call_count >= 1
        # Should create BELONGS_TO edges
        assert ctx.add_edge.call_count >= 4

    def test_skips_small_groups(self):
        from contextcore.ingestion.universal.operators.infer_domains import InferDomainsOperator

        op = InferDomainsOperator()
        ctx = _make_graph_ctx()

        ctx.entity_ids = {
            "Tool:kubernetes": "n1",
            "Tool:docker": "n2",
        }
        ctx.add_node = MagicMock(return_value="domain_1")

        op.process([], ctx)
        assert ctx.add_node.call_count == 0  # only 2, below threshold
