"""Tests for orient() tool and context hints."""

import uuid
import pytest
from contextcore.core.hybrid_graph_storage import AIContextDB
from contextcore.core.registry import GraphRegistry
from contextcore.adapters._base import AIContextDBConnection
from contextcore.tools import ToolRegistry
from contextcore.tools.registry import ToolContext


def _name():
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def ctx():
    """Fresh ToolContext with a graph containing sample data."""
    name = _name()
    registry = GraphRegistry()
    db = registry.create_graph(name)
    conn = AIContextDBConnection(namespace=name, graph_registry=registry, contextcore=db)

    # Add some sample nodes
    conn.query('CREATE NODE Fact {name: "India has low inflation", statement: "India has low inflation"}')
    conn.query('CREATE NODE Fact {name: "IPL 2026 match results", statement: "LSG beat DC in IPL 2026"}')
    conn.query('CREATE NODE Entity {name: "India", description: "Country"}')

    context = ToolContext(
        conn=conn,
        agent_id="test-agent",
        agent_name="TestBot",
    )
    return context


class TestOrientTool:
    """Test the orient() tool returns structured overview."""

    def test_orient_returns_graph_info(self, ctx):
        result = ToolRegistry.dispatch("orient", ctx, {})
        assert "content node" in result.lower() or "nodes" in result.lower() or "Graph" in result

    def test_orient_returns_topics(self, ctx):
        result = ToolRegistry.dispatch("orient", ctx, {})
        # Small graph — should show something about data
        assert "TOPIC" in result.upper() or "DATA" in result.upper() or "Fact" in result

    def test_orient_returns_suggested_actions(self, ctx):
        result = ToolRegistry.dispatch("orient", ctx, {})
        assert "search" in result.lower()

    def test_orient_sets_has_oriented_flag(self, ctx):
        assert ctx._has_oriented is False
        ToolRegistry.dispatch("orient", ctx, {})
        assert ctx._has_oriented is True


class TestContextHints:
    """Test contextual hints on tool responses."""

    def test_first_call_shows_auto_briefing(self, ctx):
        """First call auto-injects a briefing instead of an orient() hint."""
        result = ToolRegistry.dispatch("search_nodes", ctx, {"label": "Fact"})
        assert "BRIEFING" in result or "orient()" in result

    def test_no_hint_after_orient(self, ctx):
        ToolRegistry.dispatch("orient", ctx, {})
        result = ToolRegistry.dispatch("search_nodes", ctx, {"label": "Fact"})
        assert "orient()" not in result

    def test_write_nudge_after_multiple_searches(self, ctx):
        ToolRegistry.dispatch("search_nodes", ctx, {"label": "Fact"})
        ToolRegistry.dispatch("search_nodes", ctx, {"query": "india"})
        ToolRegistry.dispatch("search_nodes", ctx, {"query": "ipl"})
        result = ToolRegistry.dispatch("search_nodes", ctx, {"query": "delhi"})
        assert "add_knowledge" in result.lower() or "write" in result.lower()

    def test_no_hint_on_write_operations(self, ctx):
        result = ToolRegistry.dispatch("add_knowledge", ctx, {"content": "test finding", "node_type": "Finding"})
        assert "> Tip:" not in result
        assert "> Reminder:" not in result

    def test_hints_stop_after_5_steps(self, ctx):
        for i in range(6):
            ToolRegistry.dispatch("search_nodes", ctx, {"label": "Fact"})
        result = ToolRegistry.dispatch("search_nodes", ctx, {"label": "Fact"})
        assert "> Tip:" not in result
