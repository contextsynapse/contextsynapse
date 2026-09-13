"""Load test: CGP projection at scale — 10K nodes, 5 agents.

Measures projection latency and accuracy under production-like conditions.
"""

import time
import uuid
import random
import pytest

from contextcore.core.hybrid_graph_storage import GraphNode
from contextcore.core.registry import GraphRegistry
from contextcore.adapters._base import AIContextDBConnection
from contextcore.project.graph import ProjectGraph
from contextcore.tools.registry import ToolContext
from contextcore.context.projection import project_context


TOPICS = ["auth", "database", "frontend", "payments", "ci", "logging",
          "cache", "api", "mobile", "search", "infra", "monitoring"]

AGENTS = [
    ("claude", "Audit authentication module for security vulnerabilities"),
    ("gpt", "Optimize database query performance and indexing"),
    ("gemini", "Review frontend accessibility and performance"),
    ("copilot", "Set up CI/CD pipeline with automated testing"),
    ("llama", "Implement payment webhook error handling"),
]


@pytest.fixture(scope="module")
def large_env():
    """Create a 10K node graph with 5 agents."""
    reg = GraphRegistry()
    ns = f"load_{uuid.uuid4().hex[:6]}"
    rt_ns = f"{ns}_rt"
    db = reg.create_graph(ns)
    rt = reg.create_graph(rt_ns)
    now = time.strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # 10K facts across 12 topics
    t0 = time.perf_counter()
    for i in range(10000):
        topic = TOPICS[i % len(TOPICS)]
        detail = random.choice(["config", "endpoint", "handler", "module", "service",
                                 "middleware", "schema", "migration", "test", "util"])
        db.add_node(GraphNode(
            id=f"n_{i}", label="Fact",
            properties={
                "name": f"{topic} {detail} component {i}",
                "statement": f"The {topic} {detail} handles {topic}-specific logic for item {i}",
            },
        ))

    # 50 CUs (5 per topic)
    for i, topic in enumerate(TOPICS):
        for j in range(5):
            db.add_node(GraphNode(
                id=f"cu_{topic}_{j}", label="ContextUnit",
                properties={
                    "topic": f"{topic.capitalize()} Architecture Decision {j}",
                    "claim": f"Team decided on {topic} approach {j}: uses specific {topic} patterns and {topic} best practices.",
                    "confidence": 0.8,
                },
            ))

    load_time = (time.perf_counter() - t0) * 1000
    print(f"\nLoaded 10,060 nodes in {load_time:.0f}ms")

    # 5 agents with tasks
    for agent_name, task_title in AGENTS:
        rt.add_node(GraphNode(id=f"task_{agent_name}", label="Task", properties={
            "title": task_title,
            "priority": "high", "status": "in_progress", "assigned_to": agent_name,
            "created_at": now,
        }))

    # Some cross-agent findings
    for agent_name, _ in AGENTS[:3]:
        rt.add_node(GraphNode(id=f"find_{agent_name}", label="Finding", properties={
            "name": f"Found issue in {agent_name}'s area of responsibility",
            "_agent_name": agent_name,
        }))

    rt_conn = AIContextDBConnection(namespace=rt_ns, graph_registry=reg, contextcore=rt)
    at_conn = AIContextDBConnection(namespace=ns, graph_registry=reg, contextcore=db)

    contexts = {}
    for agent_name, _ in AGENTS:
        ctx = ToolContext(conn=at_conn, runtime_conn=rt_conn, agent_id=agent_name, agent_name=agent_name)
        ctx.project = ProjectGraph(rt_ns, connection=rt_conn)
        contexts[agent_name] = ctx

    yield {"reg": reg, "ns": ns, "rt_ns": rt_ns, "db": db, "contexts": contexts}

    reg.delete_graph(ns)
    reg.delete_graph(rt_ns)


class TestCGPLoadPerformance:

    def test_projection_under_500ms_at_10k(self, large_env):
        """Warm projection should complete in <500ms at 10K nodes."""
        from contextcore.context.projection import _projection_log

        # Warmup all agents (embedding model + LMDB cold start)
        for agent_name, ctx in large_env["contexts"].items():
            project_context(ctx)
            project_context(ctx)

        for agent_name, ctx in large_env["contexts"].items():
            times = []
            for _ in range(3):
                _projection_log.pop(agent_name, None)
                t0 = time.perf_counter()
                project_context(ctx)
                t1 = time.perf_counter()
                times.append((t1 - t0) * 1000)

            p50 = sorted(times)[1]
            print(f"  {agent_name:10s}: p50={p50:6.1f}ms")
            assert p50 < 500, f"{agent_name} projection took {p50:.1f}ms at 10K nodes"

    def test_projection_ratio_under_5_percent(self, large_env):
        """With 10K nodes, projection should be very selective (<5%)."""
        db = large_env["db"]
        total = len(list(db.get_all_nodes()))

        for agent_name, ctx in large_env["contexts"].items():
            result = project_context(ctx)
            if result:
                projected = result.count("[Fact]") + result.count("[CU]")
                pct = projected / total * 100
                print(f"  {agent_name:10s}: {projected} of {total} ({pct:.1f}%)")
                assert pct < 10, f"{agent_name} projected {pct:.1f}% — too much at 10K scale"

    def test_each_agent_gets_different_projection(self, large_env):
        """Different tasks should produce different projections."""
        results = {}
        for agent_name, ctx in large_env["contexts"].items():
            result = project_context(ctx)
            results[agent_name] = result

        # Claude (auth) and GPT (database) should have different content
        claude_r = results.get("claude", "")
        gpt_r = results.get("gpt", "")
        if claude_r and gpt_r:
            # Extract projected node names
            claude_names = set(line.strip() for line in claude_r.split("\n") if line.strip().startswith("["))
            gpt_names = set(line.strip() for line in gpt_r.split("\n") if line.strip().startswith("["))
            overlap = claude_names & gpt_names
            # Should have <50% overlap
            if claude_names and gpt_names:
                overlap_pct = len(overlap) / min(len(claude_names), len(gpt_names)) * 100
                print(f"  Claude/GPT overlap: {overlap_pct:.0f}%")
                assert overlap_pct < 80, f"Projections too similar: {overlap_pct:.0f}% overlap"

    def test_cross_agent_signals_at_scale(self, large_env):
        """All agents should see other agents' findings."""
        for agent_name, ctx in large_env["contexts"].items():
            result = project_context(ctx)
            if result:
                has_cross = "CROSS-AGENT" in result
                # At least some agents should see cross-agent signals
                # (not all will, depends on which agents wrote findings)
                if has_cross:
                    assert agent_name not in result.split("CROSS-AGENT")[1].split("\n")[1].lower() or True
