"""Tests for graph-enhanced search."""
from __future__ import annotations

import pytest
from unittest.mock import MagicMock, patch

from contextcore.search.graph_search import (
    expand_query, hop_traverse, rerank, graph_search,
    SearchNode, SearchEdge, GraphSearchResult,
)


class TestExpandQuery:
    def test_extracts_entities_from_query(self):
        """Quoted terms and capitalized words become entity seeds."""
        result = expand_query('"Kubernetes" monitoring setup', db=None)
        assert "kubernetes" in result.entity_terms
        assert "monitoring" in result.original_terms

    def test_expands_from_topic_nodes(self):
        """When a query term matches a Topic node, pull its keywords."""
        db = MagicMock()
        mock_lmdb = MagicMock()
        mock_lmdb.search.return_value = [
            {"node_id": "topic_1", "label": "Topic", "name": "monitoring",
             "snippet": "prometheus grafana alerting", "score": 0.9},
        ]
        mock_lmdb.stats.return_value = {"nodes": 100}

        with patch("contextcore.search.graph_search.get_lmdb_index", return_value=mock_lmdb):
            result = expand_query("monitoring best practices", db=db, graph_name="test")

        assert len(result.expanded_terms) > 0

    def test_returns_expanded_query_object(self):
        result = expand_query("simple query", db=None)
        assert hasattr(result, "original_terms")
        assert hasattr(result, "entity_terms")
        assert hasattr(result, "expanded_terms")
        assert hasattr(result, "all_terms")
        assert "simple" in result.original_terms or "query" in result.original_terms


class TestHopTraversal:
    def _make_db(self):
        """Build a mock DB with a small graph:
        Fact_1 --MENTIONS--> Entity_K8s --INVOLVES--> Topic_Infra
        Fact_1 --MENTIONS--> Entity_Prom
        """
        db = MagicMock()
        nodes = {
            "fact_1": MagicMock(label="Fact", properties={"name": "K8s monitoring", "statement": "Use Prometheus for K8s monitoring"}),
            "ent_k8s": MagicMock(label="Technology", properties={"name": "Kubernetes"}),
            "ent_prom": MagicMock(label="Technology", properties={"name": "Prometheus"}),
            "topic_infra": MagicMock(label="Topic", properties={"name": "Infrastructure"}),
        }
        db.get_node = lambda nid: nodes.get(nid)
        edges = {
            "fact_1": [
                ("ent_k8s", MagicMock(edge_type="MENTIONS", source_id="fact_1", target_id="ent_k8s")),
                ("ent_prom", MagicMock(edge_type="MENTIONS", source_id="fact_1", target_id="ent_prom")),
            ],
            "ent_k8s": [
                ("topic_infra", MagicMock(edge_type="INVOLVES", source_id="ent_k8s", target_id="topic_infra")),
            ],
            "ent_prom": [],
            "topic_infra": [],
        }
        db.get_neighbors = lambda nid, **kw: edges.get(nid, [])
        return db

    def test_collects_1hop_neighbors(self):
        db = self._make_db()
        seeds = [SearchNode(node_id="fact_1", label="Fact", props={"name": "K8s monitoring"}, score=1.0, distance=0)]
        nodes, edges = hop_traverse(db, seeds, max_hops=1, token_budget=4000)
        neighbor_ids = {n.node_id for n in nodes if n.distance == 1}
        assert "ent_k8s" in neighbor_ids
        assert "ent_prom" in neighbor_ids

    def test_collects_2hop_neighbors(self):
        db = self._make_db()
        seeds = [SearchNode(node_id="fact_1", label="Fact", props={"name": "K8s monitoring"}, score=1.0, distance=0)]
        nodes, edges = hop_traverse(db, seeds, max_hops=2, token_budget=4000)
        neighbor_ids = {n.node_id for n in nodes if n.distance == 2}
        assert "topic_infra" in neighbor_ids

    def test_respects_token_budget(self):
        db = self._make_db()
        seeds = [SearchNode(node_id="fact_1", label="Fact", props={"name": "K8s monitoring"}, score=1.0, distance=0)]
        nodes, edges = hop_traverse(db, seeds, max_hops=2, token_budget=50)
        assert len(nodes) < 5

    def test_returns_edges(self):
        db = self._make_db()
        seeds = [SearchNode(node_id="fact_1", label="Fact", props={"name": "K8s monitoring"}, score=1.0, distance=0)]
        nodes, edges = hop_traverse(db, seeds, max_hops=1, token_budget=4000)
        assert len(edges) >= 2
        edge_types = {e.edge_type for e in edges}
        assert "MENTIONS" in edge_types


class TestRerank:
    def test_seeds_score_higher_than_hops(self):
        nodes = [
            SearchNode(node_id="seed_1", label="Fact", props={"name": "main result", "_quality": 80}, score=0.9, distance=0),
            SearchNode(node_id="hop_1", label="Entity", props={"name": "related", "_quality": 80}, score=0.0, distance=1, via_edge="MENTIONS"),
        ]
        edges = [SearchEdge(source="seed_1", target="hop_1", edge_type="MENTIONS", weight=0.8)]
        ranked = rerank(nodes, edges, original_scores={"seed_1": 0.9})
        assert ranked[0].node_id == "seed_1"
        assert ranked[0].score > ranked[1].score

    def test_high_value_edges_boost_score(self):
        nodes = [
            SearchNode(node_id="seed", label="Problem", props={"name": "bug"}, score=1.0, distance=0),
            SearchNode(node_id="solution", label="Solution", props={"name": "fix"}, score=0.0, distance=1, via_edge="SOLVED_BY"),
            SearchNode(node_id="next_turn", label="Turn", props={"name": "turn 5"}, score=0.0, distance=1, via_edge="NEXT"),
        ]
        edges = [
            SearchEdge(source="seed", target="solution", edge_type="SOLVED_BY", weight=1.0),
            SearchEdge(source="seed", target="next_turn", edge_type="NEXT", weight=0.3),
        ]
        ranked = rerank(nodes, edges, original_scores={"seed": 1.0})
        sol = next(n for n in ranked if n.node_id == "solution")
        turn = next(n for n in ranked if n.node_id == "next_turn")
        assert sol.score > turn.score

    def test_quality_affects_score(self):
        nodes = [
            SearchNode(node_id="high_q", label="Fact", props={"name": "good", "_quality": 95}, score=0.0, distance=1, via_edge="MENTIONS"),
            SearchNode(node_id="low_q", label="Fact", props={"name": "bad", "_quality": 20}, score=0.0, distance=1, via_edge="MENTIONS"),
        ]
        edges = [
            SearchEdge(source="seed", target="high_q", edge_type="MENTIONS", weight=0.8),
            SearchEdge(source="seed", target="low_q", edge_type="MENTIONS", weight=0.8),
        ]
        ranked = rerank(nodes, edges, original_scores={})
        high = next(n for n in ranked if n.node_id == "high_q")
        low = next(n for n in ranked if n.node_id == "low_q")
        assert high.score > low.score


class TestGraphSearch:
    def _make_db_with_graph(self):
        db = MagicMock()
        db.name = "test"
        nodes = {
            "fact_1": MagicMock(label="Fact", properties={"name": "K8s setup", "statement": "Use EKS for Kubernetes"}),
            "ent_k8s": MagicMock(label="Technology", properties={"name": "Kubernetes"}),
        }
        db.get_node = lambda nid: nodes.get(nid)
        db.get_neighbors = lambda nid, **kw: [
            ("ent_k8s", MagicMock(edge_type="MENTIONS")),
        ] if nid == "fact_1" else []
        return db

    def test_returns_graph_search_result(self):
        db = self._make_db_with_graph()
        mock_lmdb = MagicMock()
        mock_lmdb.search_bm25.return_value = [
            {"node_id": "fact_1", "score": 0.9, "label": "Fact", "name": "K8s setup"},
        ]
        mock_lmdb.search.return_value = [
            {"node_id": "fact_1", "score": 0.9, "label": "Fact", "name": "K8s setup", "snippet": ""},
        ]
        mock_lmdb.stats.return_value = {"nodes": 100}

        with patch("contextcore.search.graph_search.get_lmdb_index", return_value=mock_lmdb):
            result = graph_search(db, "Kubernetes setup", graph_name="test")

        assert isinstance(result, GraphSearchResult)
        assert len(result.nodes) >= 1
        assert any(n.node_id == "fact_1" for n in result.nodes)
        assert "ms" in result.stats


class TestEndToEnd:
    def test_graph_search_with_real_expansion_and_hops(self):
        """Full pipeline: expand -> seed -> hop -> rerank -> subgraph."""
        from contextcore.search.graph_search import graph_search, GraphSearchResult

        db = MagicMock()
        db.name = "e2e"

        nodes = {
            "fact_1": MagicMock(label="Fact", properties={
                "name": "Monitoring setup",
                "statement": "Use Prometheus and Grafana for Kubernetes monitoring",
                "_quality": 80,
            }),
            "ent_prom": MagicMock(label="Technology", properties={"name": "Prometheus", "_quality": 70}),
            "ent_graf": MagicMock(label="Technology", properties={"name": "Grafana", "_quality": 70}),
            "topic_obs": MagicMock(label="Topic", properties={"name": "Observability", "_quality": 60}),
        }
        db.get_node = lambda nid: nodes.get(nid)

        edges_map = {
            "fact_1": [
                ("ent_prom", MagicMock(edge_type="MENTIONS")),
                ("ent_graf", MagicMock(edge_type="MENTIONS")),
            ],
            "ent_prom": [
                ("topic_obs", MagicMock(edge_type="INVOLVES")),
            ],
            "ent_graf": [],
            "topic_obs": [],
        }
        db.get_neighbors = lambda nid, **kw: edges_map.get(nid, [])

        mock_lmdb = MagicMock()
        mock_lmdb.stats.return_value = {"nodes": 50}
        mock_lmdb.search_bm25.return_value = [
            {"node_id": "fact_1", "score": 0.95, "label": "Fact", "name": "Monitoring setup"},
        ]
        mock_lmdb.search.return_value = [
            {"node_id": "fact_1", "score": 0.9, "label": "Fact", "name": "Monitoring setup", "snippet": "prometheus grafana"},
        ]

        with patch("contextcore.search.graph_search.get_lmdb_index", return_value=mock_lmdb):
            result = graph_search(db, "How to monitor Kubernetes?", graph_name="e2e", k=10, max_hops=2)

        assert isinstance(result, GraphSearchResult)
        assert len(result.nodes) >= 2  # seed + expanded
        assert len(result.edges) >= 1
        assert "fact_1" in result.seeds
        assert result.stats["seeds"] >= 1
        assert float(result.stats["ms"]) >= 0
        # Seeds (distance=0) should rank first
        assert result.nodes[0].distance == 0
