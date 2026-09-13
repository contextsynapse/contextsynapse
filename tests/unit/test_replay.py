"""Tests for Agent Execution Replay engine."""

import pytest
from contextcore.core.replay import AgentReplayEngine, WRITE_TOOLS


class TestReplayEngine:

    def _make_engine(self):
        return AgentReplayEngine()

    def test_record_read_step(self):
        engine = self._make_engine()
        cp = engine.record_step("run1", 1, "a1", "A1", "graph_summary", {}, "Graph: 10 nodes", "ns1")
        assert cp is None  # reads don't checkpoint
        steps = engine.get_replay("run1")
        assert len(steps) == 1
        assert steps[0].is_write is False

    def test_record_write_step(self):
        engine = self._make_engine()
        cp = engine.record_step("run1", 2, "a1", "A1", "add_knowledge",
                                {"content": "test"}, "Created Finding", "ns1")
        # No checkpoint manager -> cp is None, but step is recorded as write
        steps = engine.get_replay("run1")
        assert len(steps) == 1
        assert steps[0].is_write is True
        assert steps[0].action == "add_knowledge"

    def test_get_replay_ordered(self):
        engine = self._make_engine()
        engine.record_step("run1", 1, "a1", "A1", "graph_summary", {}, "summary", "ns1")
        engine.record_step("run1", 2, "a1", "A1", "search_nodes", {"query": "AQI"}, "found 3", "ns1")
        engine.record_step("run1", 3, "a1", "A1", "add_knowledge", {"content": "finding"}, "created", "ns1")
        steps = engine.get_replay("run1")
        assert len(steps) == 3
        assert [s.step_num for s in steps] == [1, 2, 3]

    def test_get_replay_range(self):
        engine = self._make_engine()
        for i in range(1, 6):
            engine.record_step("run1", i, "a1", "A1", "search_nodes", {}, "ok", "ns1")
        steps = engine.get_replay("run1", from_step=2, to_step=4)
        assert len(steps) == 3
        assert steps[0].step_num == 2

    def test_get_write_steps_only(self):
        engine = self._make_engine()
        engine.record_step("run1", 1, "a1", "A1", "graph_summary", {}, "summary", "ns1")
        engine.record_step("run1", 2, "a1", "A1", "add_knowledge", {}, "created", "ns1")
        engine.record_step("run1", 3, "a1", "A1", "search_nodes", {}, "found", "ns1")
        engine.record_step("run1", 4, "a1", "A1", "add_task", {}, "task created", "ns1")
        writes = engine.get_write_steps("run1")
        assert len(writes) == 2
        assert writes[0].action == "add_knowledge"
        assert writes[1].action == "add_task"

    def test_replay_dicts_serializable(self):
        engine = self._make_engine()
        engine.record_step("run1", 1, "a1", "A1", "add_knowledge", {"content": "test"}, "ok", "ns1")
        dicts = engine.get_replay_dicts("run1")
        assert len(dicts) == 1
        assert dicts[0]["action"] == "add_knowledge"
        assert "step" in dicts[0]
        assert "is_write" in dicts[0]

    def test_multi_agent_replay(self):
        engine = self._make_engine()
        engine.record_step("run1", 1, "a1", "A1", "graph_summary", {}, "ok", "ns1")
        engine.record_step("run1", 2, "a2", "A2", "search_nodes", {}, "ok", "ns1")
        engine.record_step("run1", 3, "a1", "A1", "add_knowledge", {}, "ok", "ns1")
        steps = engine.get_replay("run1")
        assert len(steps) == 3
        agents = [s.agent_name for s in steps]
        assert "A1" in agents and "A2" in agents

    def test_cleanup(self):
        engine = self._make_engine()
        engine.record_step("run1", 1, "a1", "A1", "search_nodes", {}, "ok", "ns1")
        assert len(engine.get_replay("run1")) == 1
        engine.cleanup("run1")
        assert len(engine.get_replay("run1")) == 0

    def test_write_tools_constant(self):
        assert "add_knowledge" in WRITE_TOOLS
        assert "add_task" in WRITE_TOOLS
        assert "complete_task" in WRITE_TOOLS
        assert "search_nodes" not in WRITE_TOOLS
        assert "graph_summary" not in WRITE_TOOLS

    def test_fork_no_checkpoint(self):
        engine = self._make_engine()
        engine.record_step("run1", 1, "a1", "A1", "search_nodes", {}, "ok", "ns1")
        result = engine.fork_from_step("run1", 1, "fork_ns")
        assert "error" in result

    def test_empty_run(self):
        engine = self._make_engine()
        assert engine.get_replay("nonexistent") == []
        assert engine.get_write_steps("nonexistent") == []
