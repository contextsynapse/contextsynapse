"""Tests for budget-aware projection."""

import time
import pytest


class TestProjectionBudget:

    def test_initial_state_has_remaining(self):
        from contextcore.context.projection import ProjectionBudget
        budget = ProjectionBudget(max_tokens=2000, max_time_ms=1000, tier="standard")
        assert budget.has_remaining()
        assert budget.remaining_tokens == 2000
        assert not budget.exhausted()

    def test_token_consumption(self):
        from contextcore.context.projection import ProjectionBudget
        budget = ProjectionBudget(max_tokens=100, max_time_ms=5000, tier="standard")
        budget.consume_tokens(60)
        assert budget.remaining_tokens == 40
        assert budget.has_remaining()
        budget.consume_tokens(50)
        assert budget.remaining_tokens == -10
        assert budget.exhausted()

    def test_time_exhaustion(self):
        from contextcore.context.projection import ProjectionBudget
        budget = ProjectionBudget(max_tokens=10000, max_time_ms=1, tier="standard")
        time.sleep(0.05)  # 50ms >> 1ms budget (generous margin for Windows timer resolution)
        assert budget.exhausted()

    def test_tier_defaults(self):
        from contextcore.context.projection import ProjectionBudget
        instant = ProjectionBudget.from_tier("instant")
        assert instant.max_tokens == 500
        assert instant.max_time_ms == 50
        assert instant.tier == "instant"

        fast = ProjectionBudget.from_tier("fast")
        assert fast.max_tokens == 1500
        assert fast.max_time_ms == 200
        assert fast.tier == "fast"

        standard = ProjectionBudget.from_tier("standard")
        assert standard.max_tokens == 3000
        assert standard.max_time_ms == 1000
        assert standard.tier == "standard"

        deep = ProjectionBudget.from_tier("deep")
        assert deep.max_tokens == 8000
        assert deep.max_time_ms == 5000
        assert deep.tier == "deep"

    def test_budget_overrides_tier_defaults(self):
        from contextcore.context.projection import ProjectionBudget
        budget = ProjectionBudget(max_tokens=500, max_time_ms=5000, tier="deep")
        assert budget.max_tokens == 500  # caller override wins
        assert budget.tier == "deep"

    def test_should_run_stage(self):
        from contextcore.context.projection import ProjectionBudget
        instant = ProjectionBudget.from_tier("instant")
        assert instant.should_run_stage("cache")
        assert not instant.should_run_stage("bm25")
        assert not instant.should_run_stage("qdrant")
        assert not instant.should_run_stage("hops")
        assert not instant.should_run_stage("signals")
        assert not instant.should_run_stage("extended")

        fast = ProjectionBudget.from_tier("fast")
        assert fast.should_run_stage("cache")
        assert fast.should_run_stage("bm25")
        assert not fast.should_run_stage("qdrant")

        standard = ProjectionBudget.from_tier("standard")
        assert standard.should_run_stage("cache")
        assert standard.should_run_stage("bm25")
        assert standard.should_run_stage("qdrant")
        assert standard.should_run_stage("hops")
        assert not standard.should_run_stage("signals")

        deep = ProjectionBudget.from_tier("deep")
        assert deep.should_run_stage("cache")
        assert deep.should_run_stage("bm25")
        assert deep.should_run_stage("qdrant")
        assert deep.should_run_stage("hops")
        assert deep.should_run_stage("signals")
        assert deep.should_run_stage("extended")


from unittest.mock import MagicMock


class FakeNode:
    def __init__(self, id, label, properties):
        self.id = id
        self.label = label
        self.node_type = label
        self.properties = properties


def make_mock_ctx(tasks=None, atomic_nodes=None, runtime_nodes=None):
    """Build a minimal ToolContext mock for projection tests."""
    atomic_db = MagicMock()
    atomic_db.get_all_nodes.return_value = atomic_nodes or []
    atomic_db.get_all_edges.return_value = []
    atomic_db.csr_adapter = atomic_db
    atomic_db.get_neighbors.return_value = []

    runtime_db = MagicMock()
    runtime_db.get_all_nodes.return_value = runtime_nodes or []

    conn = MagicMock()
    conn.db = atomic_db
    conn.namespace = "test_ns"
    conn._namespace = "test_ns"

    rt_conn = MagicMock()
    rt_conn.db = runtime_db

    project = MagicMock()
    project.get_open_tasks.return_value = tasks or []

    ctx = MagicMock()
    ctx.conn = conn
    ctx.runtime_conn = rt_conn
    ctx.project = project
    ctx.agent_id = "agent_1"
    ctx.agent_name = "claude"
    return ctx


class TestBudgetAwareProjection:

    def test_project_context_accepts_new_params(self):
        """project_context signature accepts max_time_ms and tier."""
        from contextcore.context.projection import project_context
        ctx = make_mock_ctx(tasks=[])
        # No tasks -> returns None, but shouldn't raise on new params
        result = project_context(ctx, max_tokens=3000, max_time_ms=1000, tier="standard")
        assert result is None

    def test_fast_tier_completes_without_error(self):
        """Fast tier runs BM25 but not Qdrant or hop expansion."""
        from contextcore.context.projection import project_context
        nodes = [
            FakeNode("n1", "Fact", {"name": "JWT token validation", "content": "Tokens are validated using RS256"}),
            FakeNode("n2", "Fact", {"name": "Auth middleware security", "content": "All endpoints require auth tokens"}),
        ]
        tasks = [{"title": "Audit auth tokens security", "id": "t1", "priority": "high", "status": "open"}]
        ctx = make_mock_ctx(tasks=tasks, atomic_nodes=nodes)
        result = project_context(ctx, max_tokens=1500, max_time_ms=200, tier="fast")
        # Should produce a result (may be None if no matches, which is fine for test graphs)
        assert result is None or isinstance(result, str)

    def test_subgraph_projector_accepts_budget(self):
        """SubgraphProjector.project() accepts optional budget parameter."""
        from contextcore.context.projection import SubgraphProjector, IntentVector, ProjectionBudget
        intent = IntentVector(
            keywords={"auth": 1.0, "token": 0.8},
            task_title="Audit auth",
            confidence=0.5,
        )
        budget = ProjectionBudget.from_tier("fast")
        result = SubgraphProjector.project(intent=intent, atomic_db=None, budget=budget)
        # No db -> empty result, but shouldn't crash
        assert result.nodes == []

    def test_subgraph_projector_backward_compat(self):
        """SubgraphProjector.project() still works with max_tokens only (no budget)."""
        from contextcore.context.projection import SubgraphProjector, IntentVector
        intent = IntentVector(
            keywords={"auth": 1.0},
            task_title="Audit",
            confidence=0.5,
        )
        result = SubgraphProjector.project(intent=intent, atomic_db=None, max_tokens=2000)
        assert result.nodes == []
