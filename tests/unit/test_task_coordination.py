"""Integration tests — multi-agent task coordination safety."""

import uuid
import threading
import pytest
from contextcore.core.hybrid_graph_storage import AIContextDB
from contextcore.core.registry import GraphRegistry
from contextcore.adapters._base import AIContextDBConnection
from contextcore.project.graph import ProjectGraph


def _name():
    return f"test_{uuid.uuid4().hex[:8]}"


@pytest.fixture
def project():
    """Fresh ProjectGraph with a unique namespace."""
    name = _name()
    registry = GraphRegistry()
    db = registry.create_graph(name)
    conn = AIContextDBConnection(namespace=name, graph_registry=registry, contextcore=db)
    pg = ProjectGraph(name, connection=conn)
    return pg


class TestAtomicClaim:
    """Verify only one agent can claim a task."""

    def test_second_agent_rejected(self, project):
        task_id = project.add_task("Implement auth", priority="high")
        result_a = project.claim_task(task_id, "agent-a")
        assert "claimed" in result_a.lower() or "agent-a" in result_a.lower()

        result_b = project.claim_task(task_id, "agent-b")
        assert "error" in result_b.lower() or "claimed" in result_b.lower() or "agent-a" in result_b.lower()
        # agent-b should NOT have claimed it
        assert "agent-b claimed" not in result_b.lower()

    def test_same_agent_reentrant(self, project):
        task_id = project.add_task("Write tests")
        project.claim_task(task_id, "agent-a")
        result = project.claim_task(task_id, "agent-a")
        # Should succeed or confirm already claimed
        assert "error" not in result.lower() or "already claimed" in result.lower()

    def test_concurrent_claim_one_winner(self, project):
        """Two threads race to claim — exactly one succeeds."""
        task_id = project.add_task("Race task", priority="high")
        results = {}

        def claim(agent):
            results[agent] = project.claim_task(task_id, agent)

        t1 = threading.Thread(target=claim, args=("agent-a",))
        t2 = threading.Thread(target=claim, args=("agent-b",))
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        winners = [a for a, r in results.items() if "claimed" in r.lower() and "error" not in r.lower()]
        assert len(winners) == 1, f"Expected 1 winner, got: {results}"


class TestVersionLocking:
    """Verify optimistic version prevents stale updates."""

    def test_add_task_sets_version(self, project):
        task_id = project.add_task("Versioned task")
        node = project.conn.get_node(task_id)
        props = node.properties if hasattr(node, "properties") else node
        assert props.get("_version") == 1

    def test_claim_increments_version(self, project):
        task_id = project.add_task("Version bump test")
        project.claim_task(task_id, "agent-a")
        node = project.conn.get_node(task_id)
        props = node.properties if hasattr(node, "properties") else node
        assert props.get("_version") == 2

    def test_complete_increments_version(self, project):
        task_id = project.add_task("Complete version test")
        project.claim_task(task_id, "agent-a")
        project.complete_task(task_id, "agent-a", summary="Done")
        node = project.conn.get_node(task_id)
        props = node.properties if hasattr(node, "properties") else node
        assert props.get("_version") == 3


class TestEventEmission:
    """Verify task operations emit events."""

    def test_claim_emits_event(self, project):
        events = []
        project._event_bus.subscribe(lambda e: events.append(e))
        task_id = project.add_task("Event test")
        project.claim_task(task_id, "agent-a")
        assert any(e["type"] == "task_claimed" for e in events)

    def test_complete_emits_event(self, project):
        events = []
        project._event_bus.subscribe(lambda e: events.append(e))
        task_id = project.add_task("Complete event test")
        project.claim_task(task_id, "agent-a")
        project.complete_task(task_id, "agent-a", summary="Done")
        assert any(e["type"] == "task_completed" for e in events)

    def test_handoff_emits_event(self, project):
        events = []
        project._event_bus.subscribe(lambda e: events.append(e))
        task_id = project.add_task("Handoff event test")
        project.claim_task(task_id, "agent-a")
        project.handoff_task(task_id, "agent-a", "agent-b", notes="Your turn")
        assert any(e["type"] == "task_handed_off" for e in events)

    def test_unblock_emits_event(self, project):
        events = []
        task_a = project.add_task("Blocker task")
        task_b = project.add_task("Blocked task", depends_on=[task_a])
        project._event_bus.subscribe(lambda e: events.append(e))
        project.claim_task(task_a, "agent-a")
        project.complete_task(task_a, "agent-a", summary="Done")
        assert any(e["type"] == "task_unblocked" for e in events)
