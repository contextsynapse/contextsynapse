"""Tests for typed agent memory — save/recall with memory_type."""

import uuid
import pytest
from unittest.mock import MagicMock
from contextcore.context.agent_memory import AgentMemory


@pytest.fixture
def memory():
    """Create AgentMemory with a unique graph per test to prevent Redis key collisions."""
    from contextcore.core.registry import GraphRegistry
    ns = f"test_memory_{uuid.uuid4().hex[:8]}"
    reg = GraphRegistry()
    reg.create_graph(ns)
    yield AgentMemory(reg, namespace=ns)
    try:
        reg.delete_graph(ns, delete_files=True)
    except Exception:
        pass


class TestTypedMemory:
    def test_save_memory_fact(self, memory):
        mid = memory.save_memory("agent-1", "Delhi AQI exceeds 300", memory_type="fact")
        assert mid.startswith("mem_")

    def test_save_memory_decision(self, memory):
        mid = memory.save_memory("agent-1", "Focus on health impacts", memory_type="decision")
        assert mid.startswith("mem_")

    def test_save_memory_invalid_type_defaults_to_fact(self, memory):
        mid = memory.save_memory("agent-1", "test", memory_type="invalid")
        assert mid.startswith("mem_")
        # The memory should have been stored with type "fact"
        memories = memory.recall("agent-1", limit=10)
        found = [m for m in memories if m.get("content") == "test"]
        assert len(found) == 1

    def test_recall_memories_by_query(self, memory):
        memory.save_memory("agent-1", "User prefers dark mode", memory_type="preference")
        memory.save_memory("agent-1", "Always validate inputs", memory_type="pattern")
        memory.save_memory("agent-1", "Light mode causes eye strain", memory_type="fact")

        results = memory.recall_memories("agent-1", query="dark mode", k=5)
        assert len(results) > 0
        # "dark mode" should match the preference
        contents = [r.get("content", "") for r in results]
        assert any("dark mode" in c for c in contents)

    def test_recall_memories_by_type(self, memory):
        memory.save_memory("agent-1", "Decided to use PostgreSQL", memory_type="decision")
        memory.save_memory("agent-1", "Python is fast enough", memory_type="fact")

        # Filter by type
        decisions = memory.recall_memories("agent-1", query="", k=10, memory_type="decision")
        facts = memory.recall_memories("agent-1", query="", k=10, memory_type="fact")
        assert any("PostgreSQL" in d.get("content", "") for d in decisions)
        assert any("Python" in f.get("content", "") for f in facts)

    def test_remember_and_recall_basic(self, memory):
        memory.remember("agent-2", "important finding", tags=["fact"])
        results = memory.recall("agent-2", query="important", limit=5)
        assert len(results) > 0
        assert any("important" in r.get("content", "") for r in results)

    def test_forget(self, memory):
        mid = memory.save_memory("agent-1", "temporary note", memory_type="fact")
        assert memory.forget("agent-1", mid) is True

    def test_memory_types_constant(self, memory):
        assert "decision" in memory.MEMORY_TYPES
        assert "preference" in memory.MEMORY_TYPES
        assert "pattern" in memory.MEMORY_TYPES
        assert "fact" in memory.MEMORY_TYPES

    def test_separate_agent_memories(self, memory):
        memory.save_memory("agent-A", "Memory for A", memory_type="fact")
        memory.save_memory("agent-B", "Memory for B", memory_type="fact")

        a_mems = memory.recall("agent-A", limit=10)
        b_mems = memory.recall("agent-B", limit=10)
        a_contents = [m.get("content", "") for m in a_mems]
        b_contents = [m.get("content", "") for m in b_mems]
        assert "Memory for A" in a_contents
        assert "Memory for B" in b_contents
        assert "Memory for B" not in a_contents
