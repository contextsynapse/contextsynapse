"""
Unit tests for context quality improvements:
- Fix 1: Edges included in build_context()
- Fix 2: Better node/edge rendering (full IDs, priority props, rich edges)
- Fix 3: Relevance scoring with keyword overlap
- Fix 4: Edge-neighbor expansion in search results
"""

import pytest
from unittest.mock import MagicMock, patch
from contextcore.context.hub import (
    ContextHub,
    ContextItem,
    ContextRole,
    _node_to_text,
    _edge_to_text,
)
from contextcore.context.scoping import ContextScoper, ScopingConfig


# ── Helpers ──────────────────────────────────────────────────────────

def _make_node(nid, label, **props):
    """Create a mock GraphNode."""
    node = MagicMock()
    node.id = nid
    node.label = label
    node.properties = dict(props)
    return node


def _make_edge(src, tgt, label="RELATED"):
    """Create a mock GraphEdge."""
    edge = MagicMock()
    edge.source = src
    edge.target = tgt
    edge.label = label
    edge.source_id = src
    edge.target_id = tgt
    edge.from_id = src
    edge.to_id = tgt
    return edge


# ═══════════════════════════════════════════════════════════════════════
# Fix 2: Node/Edge rendering
# ═══════════════════════════════════════════════════════════════════════

class TestNodeRendering:
    """_node_to_text should show full IDs and priority-ordered properties."""

    def test_full_uuid_preserved(self):
        """Node IDs should NOT be truncated to 8 chars."""
        full_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        node = _make_node(full_uuid, "Person", name="Alice")
        text = _node_to_text(node)
        assert full_uuid in text, "Full UUID should be in rendered text"

    def test_full_uuid_preserved_dict(self):
        """Dict nodes should also show full UUIDs."""
        full_uuid = "a1b2c3d4-e5f6-7890-abcd-ef1234567890"
        node = {"uuid": full_uuid, "label": "Person", "name": "Alice"}
        text = _node_to_text(node)
        assert full_uuid in text

    def test_priority_properties_first(self):
        """name, title, content, description should appear before other props."""
        node = _make_node("id1", "Task",
                          status="open", name="Fix bug", category="backend",
                          description="Fix the login bug", assigned_to="alice")
        text = _node_to_text(node)
        lines = text.strip().split("\n")
        # Find positions of key fields
        prop_lines = [l.strip() for l in lines[1:]]  # skip header
        keys = [l.split(":")[0] for l in prop_lines]
        assert keys.index("name") < keys.index("category"), "name should come before category"
        assert keys.index("description") < keys.index("assigned_to"), "description should come before assigned_to"

    def test_priority_properties_dict(self):
        """Dict nodes should also have priority-ordered properties."""
        node = {"uuid": "x", "label": "Requirement",
                "name": "Auth", "description": "Implement auth",
                "category": "security", "priority": "high"}
        text = _node_to_text(node)
        lines = text.strip().split("\n")
        prop_lines = [l.strip() for l in lines[1:]]
        keys = [l.split(":")[0] for l in prop_lines]
        assert keys.index("name") < keys.index("category")
        assert keys.index("priority") < keys.index("category")


class TestEdgeRendering:
    """_edge_to_text should use human-readable names when provided."""

    def test_edge_with_node_names(self):
        """Edges should render with display names instead of IDs."""
        edge = _make_edge("id-alice", "id-project", "WORKS_ON")
        names = {"id-alice": "Alice", "id-project": "Project X"}
        text = _edge_to_text(edge, node_names=names)
        assert "Alice" in text
        assert "Project X" in text
        assert "WORKS_ON" in text

    def test_edge_without_names_falls_back_to_id(self):
        """Without node_names, edge should show truncated IDs."""
        edge = _make_edge("abcdefghijklmnop", "1234567890abcdef", "DEPENDS_ON")
        text = _edge_to_text(edge)
        assert "DEPENDS_ON" in text
        assert "abcdefghijkl" in text  # truncated to 12

    def test_edge_dict_with_names(self):
        """Dict edges should also support node_names."""
        edge = {"source": "id1", "target": "id2", "label": "REQUIRES"}
        names = {"id1": "Task A", "id2": "Requirement B"}
        text = _edge_to_text(edge, node_names=names)
        assert "Task A" in text
        assert "Requirement B" in text
        assert "REQUIRES" in text

    def test_edge_dict_with_properties(self):
        """Dict edges with extra properties should show them."""
        edge = {"source": "id1", "target": "id2", "label": "RELATES",
                "weight": 0.9, "since": "2026-01"}
        text = _edge_to_text(edge)
        assert "weight=0.9" in text
        assert "since=2026-01" in text


# ═══════════════════════════════════════════════════════════════════════
# Fix 2 (continued): add_edges with node_names
# ═══════════════════════════════════════════════════════════════════════

class TestHubAddEdges:
    """ContextHub.add_edges should accept and use node_names."""

    def test_add_edges_with_names(self):
        hub = ContextHub(max_tokens=8000)
        edges = [_make_edge("id1", "id2", "DEPENDS_ON")]
        names = {"id1": "Task A", "id2": "Requirement 1"}
        hub.add_edges(edges, node_names=names)
        prompt = hub.to_prompt()
        assert "Task A" in prompt
        assert "Requirement 1" in prompt
        assert "DEPENDS_ON" in prompt
        assert "Relationships" in prompt  # label changed from "Edges"

    def test_add_edges_without_names(self):
        """Backward compat — add_edges works without node_names."""
        hub = ContextHub(max_tokens=8000)
        edges = [_make_edge("src123", "tgt456", "LINK")]
        hub.add_edges(edges)
        prompt = hub.to_prompt()
        assert "LINK" in prompt

    def test_add_edges_empty(self):
        """Empty edge list should be a no-op."""
        hub = ContextHub(max_tokens=8000)
        hub.add_edges([])
        assert len(hub._items) == 0


# ═══════════════════════════════════════════════════════════════════════
# Fix 3: Relevance scoring
# ═══════════════════════════════════════════════════════════════════════

class TestRelevanceScoring:
    """ContextScoper should compute keyword-overlap relevance from a query."""

    def _make_items(self):
        """Create test context items with different content."""
        return [
            ContextItem(content="The authentication system uses JWT tokens for session management",
                        role=ContextRole.RETRIEVED, label="Auth"),
            ContextItem(content="Database migration scripts for PostgreSQL schema updates",
                        role=ContextRole.RETRIEVED, label="DB"),
            ContextItem(content="Frontend React components for the login page with JWT validation",
                        role=ContextRole.RETRIEVED, label="UI"),
            ContextItem(content="CI/CD pipeline configuration for automated deployments",
                        role=ContextRole.RETRIEVED, label="DevOps"),
        ]

    def test_query_relevance_computation(self):
        """Items matching query keywords should score higher than non-matching."""
        scoper = ContextScoper(ScopingConfig(strategy="relevance"))
        items = self._make_items()
        scored = scoper.score_items(items, query="JWT authentication login")

        # Auth item (has "authentication" and "JWT") and UI item (has "login" and "JWT")
        # should score higher than DB and DevOps items
        scores_by_label = {s.item.label: s.score for s in scored}
        assert scores_by_label["Auth"] > scores_by_label["DB"]
        assert scores_by_label["Auth"] > scores_by_label["DevOps"]
        assert scores_by_label["UI"] > scores_by_label["DevOps"]

    def test_no_query_uses_default_relevance(self):
        """Without a query, relevance should default to 0.3 (low)."""
        scoper = ContextScoper(ScopingConfig(strategy="relevance"))
        items = self._make_items()
        scored = scoper.score_items(items, query=None)
        # All should get the same default relevance
        for s in scored:
            assert abs(s.score - 0.3) < 0.01, f"Default relevance should be ~0.3, got {s.score}"

    def test_external_scores_override_auto(self):
        """Externally provided relevance_scores should take precedence."""
        scoper = ContextScoper(ScopingConfig(strategy="relevance"))
        items = self._make_items()
        external = {0: 1.0, 1: 0.0, 2: 0.5, 3: 0.2}
        scored = scoper.score_items(items, query="anything", relevance_scores=external)
        scores = {s.item.label: s.score for s in scored}
        assert scores["Auth"] > scores["UI"] > scores["DevOps"] > scores["DB"]

    def test_combined_strategy_uses_relevance(self):
        """Combined strategy should weight relevance alongside role and recency."""
        scoper = ContextScoper(ScopingConfig(
            strategy="combined",
            role_weight_factor=0.1,
            recency_factor=0.1,
            relevance_factor=0.8,  # heavily weight relevance
        ))
        items = self._make_items()
        scored = scoper.score_items(items, query="database migration PostgreSQL")
        scores_by_label = {s.item.label: s.score for s in scored}
        # DB item matches all 3 query terms
        assert scores_by_label["DB"] > scores_by_label["Auth"]
        assert scores_by_label["DB"] > scores_by_label["DevOps"]

    def test_select_within_budget_with_query(self):
        """Budget selection should prefer query-relevant items."""
        scoper = ContextScoper(ScopingConfig(
            strategy="combined",
            max_tokens=200,  # tight budget
            relevance_factor=0.8,
            role_weight_factor=0.1,
            recency_factor=0.1,
        ))
        items = self._make_items()
        selected = scoper.select_within_budget(items, query="JWT authentication")
        labels = [item.label for item in selected]
        # Auth and UI items should be preferred (JWT match)
        assert "Auth" in labels or "UI" in labels

    def test_compute_query_relevance_static(self):
        """Directly test the static relevance computation method."""
        items = [
            ContextItem(content="Python flask web application", role=ContextRole.RETRIEVED),
            ContextItem(content="Java spring boot microservice", role=ContextRole.RETRIEVED),
        ]
        scores = ContextScoper._compute_query_relevance("flask python web", items)
        assert scores[0] > scores[1], "Flask item should match more query terms"

    def test_empty_query_returns_no_scores(self):
        """Stop-word-only query should return empty scores dict."""
        items = [ContextItem(content="anything", role=ContextRole.RETRIEVED)]
        scores = ContextScoper._compute_query_relevance("the is a", items)
        assert scores == {}


# ═══════════════════════════════════════════════════════════════════════
# Fix 1: build_context includes edges
# ═══════════════════════════════════════════════════════════════════════

class TestBuildContextEdges:
    """AIContextDBConnection.build_context should include edges."""

    def test_build_context_includes_edges(self):
        """build_context should add edges to the hub."""
        from contextcore.adapters._base import AIContextDBConnection

        # Mock the graph
        mock_db = MagicMock()
        mock_db.get_all_nodes.return_value = [
            _make_node("node-1", "Person", name="Alice"),
            _make_node("node-2", "Task", name="Fix bug"),
        ]
        mock_db.get_all_edges.return_value = [
            _make_edge("node-1", "node-2", "ASSIGNED_TO"),
        ]

        mock_registry = MagicMock()
        mock_registry.get_graph.return_value = mock_db

        conn = AIContextDBConnection(namespace="test", graph_registry=mock_registry)
        conn.contextcore = mock_db

        hub = conn.build_context(max_tokens=8000)
        prompt = hub.to_prompt()

        assert "ASSIGNED_TO" in prompt, "Edge label should be in context"
        assert "Alice" in prompt, "Node name should be in edge rendering"
        assert "Relationships" in prompt, "Relationships section should exist"

    def test_build_context_no_edges_still_works(self):
        """build_context should not break if get_all_edges raises."""
        from contextcore.adapters._base import AIContextDBConnection

        mock_db = MagicMock()
        mock_db.get_all_nodes.return_value = [
            _make_node("n1", "Fact", name="Test fact"),
        ]
        mock_db.get_all_edges.side_effect = AttributeError("no edges")

        mock_registry = MagicMock()
        mock_registry.get_graph.return_value = mock_db

        conn = AIContextDBConnection(namespace="test", graph_registry=mock_registry)
        conn.contextcore = mock_db

        hub = conn.build_context(max_tokens=8000)
        prompt = hub.to_prompt()
        assert "Test fact" in prompt, "Nodes should still be in context"
