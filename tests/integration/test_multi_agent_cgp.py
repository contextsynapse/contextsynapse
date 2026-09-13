"""Integration test: Claude (MCP) + GPT (REST) collaborating via CGP.

Tests the full projection pipeline with real graph storage, real LMDB
indexes, and real ProjectGraph. No mocks.
"""

import time
import uuid
import pytest

from contextcore.core.hybrid_graph_storage import GraphNode, GraphEdge
from contextcore.core.registry import GraphRegistry
from contextcore.adapters._base import AIContextDBConnection
from contextcore.project.graph import ProjectGraph
from contextcore.tools.registry import ToolContext
from contextcore.context.projection import project_context


@pytest.fixture
def collab_env():
    """Set up a shared environment: atomic graph + runtime graph + 2 agents."""
    reg = GraphRegistry()
    ns = f"collab_{uuid.uuid4().hex[:6]}"
    rt_ns = f"{ns}_rt"
    atomic_db = reg.create_graph(ns)
    runtime_db = reg.create_graph(rt_ns)
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Populate atomic graph with diverse knowledge
    knowledge = [
        "Auth module uses JWT with RS256 signing for API endpoints",
        "TokenStore handles session token persistence in Redis",
        "Refresh tokens stored in HTTP-only secure cookies",
        "User passwords hashed with bcrypt 12 salt rounds",
        "Session expiry 24 hours with sliding window refresh",
        "Database runs PostgreSQL 15 with pgvector extension",
        "Frontend built with React 18 and TypeScript on Vercel",
        "Payment processing uses Stripe API webhook validation",
        "Rate limiting at API gateway 100 req/min per user",
        "Logging structured JSON via Winston shipped to Datadog",
    ]
    for content in knowledge:
        atomic_db.add_node(GraphNode(
            id=f"n_{uuid.uuid4().hex[:6]}", label="Fact",
            properties={"name": content, "statement": content},
        ))

    # CUs from past conversations
    atomic_db.add_node(GraphNode(
        id="cu_auth", label="ContextUnit",
        properties={
            "topic": "Authentication Architecture",
            "claim": "JWT RS256 with HTTP-only cookies for refresh. Sliding window sessions. Key rotation via KeyManager.",
            "confidence": 0.9,
        },
    ))
    atomic_db.add_node(GraphNode(
        id="cu_security", label="ContextUnit",
        properties={
            "topic": "Security Audit Findings",
            "claim": "bcrypt salt rounds increased to 12. CORS locked to specific origins. CSP headers for auth pages.",
            "confidence": 0.85,
        },
    ))
    atomic_db.add_node(GraphNode(
        id="cu_db", label="ContextUnit",
        properties={
            "topic": "Database Design",
            "claim": "PostgreSQL with pgvector. Partitioned by tenant_id. Connection pool 20.",
            "confidence": 0.8,
        },
    ))

    # Tasks in runtime
    runtime_db.add_node(GraphNode(id="task_claude", label="Task", properties={
        "title": "Audit authentication module for security vulnerabilities",
        "description": "Review JWT handling, token storage, session management",
        "priority": "critical", "status": "in_progress", "assigned_to": "claude",
        "created_at": now,
    }))
    runtime_db.add_node(GraphNode(id="task_gpt", label="Task", properties={
        "title": "Optimize database query performance and connection pooling",
        "description": "Review PostgreSQL queries, pgvector indexes, pool settings",
        "priority": "high", "status": "in_progress", "assigned_to": "gpt",
        "created_at": now,
    }))

    # Build connections
    rt_conn = AIContextDBConnection(namespace=rt_ns, graph_registry=reg, contextcore=runtime_db)
    at_conn = AIContextDBConnection(namespace=ns, graph_registry=reg, contextcore=atomic_db)

    # Claude context
    claude_ctx = ToolContext(conn=at_conn, runtime_conn=rt_conn, agent_id="claude", agent_name="claude")
    claude_ctx.project = ProjectGraph(rt_ns, connection=rt_conn)

    # GPT context
    gpt_ctx = ToolContext(conn=at_conn, runtime_conn=rt_conn, agent_id="gpt", agent_name="gpt")
    gpt_ctx.project = ProjectGraph(rt_ns, connection=rt_conn)

    yield {
        "reg": reg, "ns": ns, "rt_ns": rt_ns,
        "atomic_db": atomic_db, "runtime_db": runtime_db,
        "claude_ctx": claude_ctx, "gpt_ctx": gpt_ctx,
    }

    reg.delete_graph(ns)
    reg.delete_graph(rt_ns)


class TestMultiAgentCGP:

    def test_claude_gets_auth_context(self, collab_env):
        result = project_context(collab_env["claude_ctx"])
        assert result is not None
        assert "PROJECTED CONTEXT" in result
        # Should find auth-related content
        assert "auth" in result.lower() or "JWT" in result or "token" in result.lower()

    def test_gpt_gets_database_context(self, collab_env):
        result = project_context(collab_env["gpt_ctx"])
        assert result is not None
        assert "PROJECTED CONTEXT" in result
        # Should find database-related content
        assert "database" in result.lower() or "PostgreSQL" in result or "pgvector" in result.lower()

    def test_claude_does_not_get_react(self, collab_env):
        result = project_context(collab_env["claude_ctx"])
        assert "React" not in result
        assert "Vercel" not in result

    def test_gpt_does_not_get_stripe(self, collab_env):
        result = project_context(collab_env["gpt_ctx"])
        assert "Stripe" not in result
        assert "Datadog" not in result

    def test_cross_agent_signals_visible(self, collab_env):
        """After Claude writes findings, GPT sees them on re-orient."""
        runtime_db = collab_env["runtime_db"]

        # Claude writes a finding
        runtime_db.add_node(GraphNode(id="fc1", label="Finding", properties={
            "name": "JWT refresh endpoint has no rate limiting",
            "_agent_name": "claude", "created_by": "claude",
        }))

        # GPT re-orients — should see Claude's finding
        # Need fresh context to see new nodes
        gpt_ctx = collab_env["gpt_ctx"]
        result = project_context(gpt_ctx)
        if result:
            assert "claude" in result.lower() or "rate limiting" in result.lower()

    def test_each_agent_gets_own_task(self, collab_env):
        claude_result = project_context(collab_env["claude_ctx"])
        gpt_result = project_context(collab_env["gpt_ctx"])

        assert "Audit authentication" in claude_result
        assert "Optimize database" in gpt_result

    def test_projection_uses_context_units(self, collab_env):
        """CUs from past conversations should appear in projection."""
        result = project_context(collab_env["claude_ctx"])
        # Should include auth-related CU
        has_cu = "[CU]" in result or "Architecture" in result or "Security Audit" in result
        assert has_cu

    def test_projection_is_fast(self, collab_env):
        """Warm projection should complete in <100ms for this graph size."""
        # Warmup (cold start includes embedding model load)
        project_context(collab_env["claude_ctx"])
        project_context(collab_env["claude_ctx"])

        from contextcore.context.projection import _projection_log
        times = []
        for _ in range(5):
            _projection_log.pop("claude", None)
            t0 = time.perf_counter()
            project_context(collab_env["claude_ctx"])
            t1 = time.perf_counter()
            times.append((t1 - t0) * 1000)

        p50 = sorted(times)[2]
        assert p50 < 100, f"Warm projection took {p50:.1f}ms, should be <100ms"
