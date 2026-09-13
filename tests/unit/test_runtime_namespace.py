"""
Tests for runtime namespace separation.

Tasks, findings, and agent actions belong in the runtime graph (session_rt),
not the atomic context graph (session). This test verifies that:
1. Sessions get a runtime_namespace automatically
2. Old sessions without runtime_namespace get a derived fallback
3. SessionScope carries runtime_namespace to tool context
4. dispatch_goal writes to the runtime namespace
5. ToolContext has runtime_conn for agent-produced writes
6. add_knowledge routes agent types (Finding, Insight) to runtime graph
"""

import pytest
from unittest.mock import MagicMock
from contextcore.context.session import ContextSession, ContextSessionManager
from contextcore.mcp.session_context import SessionScope


class TestRuntimeNamespaceOnSession:

    def test_create_session_has_runtime_namespace(self, tmp_db_path):
        mgr = ContextSessionManager(db_path=tmp_db_path)
        session = mgr.create_session("test-rt")
        assert session.runtime_namespace
        assert session.runtime_namespace.endswith("_rt")
        assert session.runtime_namespace == f"{session.graph_namespace}_rt"

    def test_retrieved_session_has_runtime_namespace(self, tmp_db_path):
        mgr = ContextSessionManager(db_path=tmp_db_path)
        created = mgr.create_session("test-rt-retrieve")
        loaded = mgr.get_session(created.session_id)
        assert loaded.runtime_namespace == created.runtime_namespace

    def test_old_session_backfills_runtime_namespace(self, tmp_db_path):
        """Sessions created before v4 migration get a derived runtime_namespace."""
        mgr = ContextSessionManager(db_path=tmp_db_path)
        session = mgr.create_session("test-backfill")
        # Simulate old session without runtime_namespace
        mgr._conn.execute(
            "UPDATE context_sessions SET runtime_namespace = '' WHERE session_id = ?",
            (session.session_id,),
        )
        mgr._conn.commit()
        loaded = mgr.get_session(session.session_id)
        assert loaded.runtime_namespace == f"{loaded.graph_namespace}_rt"

    def test_runtime_namespace_distinct_from_graph_namespace(self, tmp_db_path):
        mgr = ContextSessionManager(db_path=tmp_db_path)
        session = mgr.create_session("test-distinct")
        assert session.runtime_namespace != session.graph_namespace


class TestSessionScopeRuntime:

    def test_scope_carries_runtime_namespace(self):
        scope = SessionScope(
            session_id="s1",
            graph_namespace="ctx_test_abc",
            agent_id="a1",
            agent_name="claude",
            runtime_namespace="ctx_test_abc_rt",
        )
        assert scope.runtime_namespace == "ctx_test_abc_rt"

    def test_scope_defaults_empty_runtime(self):
        scope = SessionScope(
            session_id="s1",
            graph_namespace="ctx_test_abc",
            agent_id="a1",
            agent_name="claude",
        )
        assert scope.runtime_namespace == ""

    def test_scope_runtime_conn_initially_none(self):
        scope = SessionScope(
            session_id="s1",
            graph_namespace="ctx_test_abc",
            agent_id="a1",
            agent_name="claude",
            runtime_namespace="ctx_test_abc_rt",
        )
        assert scope.runtime_conn is None


class TestDispatcherUsesRuntime:

    def test_dispatch_goal_targets_runtime_namespace(self, tmp_db_path):
        """dispatch_goal should write tasks to runtime_namespace, not graph_namespace."""
        from unittest.mock import MagicMock, patch

        mgr = ContextSessionManager(db_path=tmp_db_path)
        session = mgr.create_session("test-dispatch-rt")

        # Mock graph_registry to capture which namespace gets used
        mock_registry = MagicMock()
        mock_db = MagicMock()
        mock_registry.get_graph.return_value = mock_db

        # Mock agent_registry
        mock_agent = MagicMock()
        mock_agent.name = "test-agent"
        mock_agent.agent_id = "agent-1"
        mock_agent.status = "active"
        mock_agent.metadata = {}
        mock_agent.capabilities = []

        mock_agent_reg = MagicMock()
        mock_agent_reg.list_agents.return_value = [mock_agent]

        from contextcore.agents.dispatcher import dispatch_goal
        result = dispatch_goal(
            goal="Test task separation",
            session_id=session.session_id,
            graph_registry=mock_registry,
            agent_registry=mock_agent_reg,
            session_manager=mgr,
        )

        # Verify it used the runtime namespace, not the main graph namespace
        if result.get("tasks"):
            called_ns = mock_registry.get_graph.call_args[0][0]
            assert called_ns == session.runtime_namespace, (
                f"dispatch_goal wrote to '{called_ns}' but should use runtime "
                f"namespace '{session.runtime_namespace}'"
            )
            assert called_ns != session.graph_namespace


class TestToolContextRuntime:

    def test_tool_context_has_runtime_conn(self):
        from contextcore.tools.registry import ToolContext
        mock_conn = MagicMock()
        mock_rt = MagicMock()
        ctx = ToolContext(conn=mock_conn, runtime_conn=mock_rt, agent_id="a1")
        assert ctx.runtime_conn is mock_rt
        assert ctx.conn is mock_conn

    def test_tool_context_runtime_conn_defaults_none(self):
        from contextcore.tools.registry import ToolContext
        mock_conn = MagicMock()
        ctx = ToolContext(conn=mock_conn, agent_id="a1")
        assert ctx.runtime_conn is None


class TestKnowledgeRouting:
    """Verify add_knowledge routes agent-produced types to runtime graph."""

    def test_finding_goes_to_runtime(self):
        """Finding is agent-produced → should write to runtime_conn."""
        from contextcore.tools.graph import _add_knowledge
        from contextcore.tools.registry import ToolContext

        mock_conn = MagicMock()
        mock_conn.query.return_value = {"success": True, "data": {"uuid": "test-uuid"}}
        mock_conn.namespace = "test_ns"
        mock_conn.contextcore = MagicMock()

        mock_rt = MagicMock()
        mock_rt.query.return_value = {"success": True, "data": {"uuid": "rt-uuid"}}
        mock_rt.namespace = "test_ns_rt"
        mock_rt.contextcore = MagicMock()
        mock_rt.contextcore.csr_adapter.get_all_nodes.return_value = []

        ctx = ToolContext(conn=mock_conn, runtime_conn=mock_rt, agent_id="a1", agent_name="claude")

        result = _add_knowledge(ctx, content="Security vulnerability found", node_type="Finding")
        # Should have written to runtime, not atomic
        assert mock_rt.query.called, "Finding should write to runtime_conn"
        assert not mock_conn.query.called, "Finding should NOT write to atomic conn"

    def test_fact_goes_to_atomic(self):
        """Fact is source knowledge → should write to conn (atomic)."""
        from contextcore.tools.graph import _add_knowledge
        from contextcore.tools.registry import ToolContext

        mock_conn = MagicMock()
        mock_conn.query.return_value = {"success": True, "data": {"uuid": "test-uuid"}}
        mock_conn.namespace = "test_ns"
        mock_conn.contextcore = MagicMock()
        mock_conn.contextcore.csr_adapter.get_all_nodes.return_value = []

        mock_rt = MagicMock()

        ctx = ToolContext(conn=mock_conn, runtime_conn=mock_rt, agent_id="a1", agent_name="claude")

        result = _add_knowledge(ctx, content="Python 3.12 released", node_type="Fact")
        assert mock_conn.query.called, "Fact should write to atomic conn"
        assert not mock_rt.query.called, "Fact should NOT write to runtime_conn"

    def test_knowledge_goes_to_atomic(self):
        """Knowledge (default) is source data → should write to conn."""
        from contextcore.tools.graph import _add_knowledge
        from contextcore.tools.registry import ToolContext

        mock_conn = MagicMock()
        mock_conn.query.return_value = {"success": True, "data": {"uuid": "test-uuid"}}
        mock_conn.namespace = "test_ns"
        mock_conn.contextcore = MagicMock()
        mock_conn.contextcore.csr_adapter.get_all_nodes.return_value = []

        mock_rt = MagicMock()

        ctx = ToolContext(conn=mock_conn, runtime_conn=mock_rt, agent_id="a1", agent_name="claude")

        result = _add_knowledge(ctx, content="API rate limit is 100/min")
        assert mock_conn.query.called
        assert not mock_rt.query.called

    def test_insight_goes_to_runtime(self):
        """Insight is agent-produced → runtime."""
        from contextcore.tools.graph import _add_knowledge
        from contextcore.tools.registry import ToolContext

        mock_conn = MagicMock()
        mock_rt = MagicMock()
        mock_rt.query.return_value = {"success": True, "data": {"uuid": "rt-uuid"}}
        mock_rt.namespace = "test_ns_rt"
        mock_rt.contextcore = MagicMock()
        mock_rt.contextcore.csr_adapter.get_all_nodes.return_value = []

        ctx = ToolContext(conn=mock_conn, runtime_conn=mock_rt, agent_id="a1", agent_name="claude")

        _add_knowledge(ctx, content="The auth module needs refactoring", node_type="Insight")
        assert mock_rt.query.called
        assert not mock_conn.query.called


class TestLogActionRouting:
    """Verify log_action writes to runtime graph."""

    def test_log_action_goes_to_runtime(self):
        from contextcore.tools.graph import _log_action
        from contextcore.tools.registry import ToolContext

        mock_conn = MagicMock()
        mock_rt = MagicMock()
        mock_rt.query.return_value = {"success": True, "data": {"uuid": "action-uuid"}}

        ctx = ToolContext(conn=mock_conn, runtime_conn=mock_rt, agent_id="a1", agent_name="claude")

        _log_action(ctx, action="refactor", description="Extracted auth logic")
        assert mock_rt.query.called, "AgentAction should write to runtime"
        assert not mock_conn.query.called, "AgentAction should NOT write to atomic"

    def test_log_action_falls_back_to_conn(self):
        """Without runtime_conn, falls back to conn."""
        from contextcore.tools.graph import _log_action
        from contextcore.tools.registry import ToolContext

        mock_conn = MagicMock()
        mock_conn.query.return_value = {"success": True, "data": {"uuid": "action-uuid"}}

        ctx = ToolContext(conn=mock_conn, agent_id="a1", agent_name="claude")

        _log_action(ctx, action="refactor", description="Extracted auth logic")
        assert mock_conn.query.called
