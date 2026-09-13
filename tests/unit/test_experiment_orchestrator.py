"""
Tests for the RunOrchestrator — real-time parallel experiment execution.
"""

import time
import threading
from unittest.mock import MagicMock, patch

import pytest

from contextcore.api.experiment_orchestrator import (
    RunOrchestrator,
    SharedFindingsBuffer,
    IncrementalScorer,
    SSEEvent,
)


# ─── SharedFindingsBuffer ──────────────────────────────────────────────────


class TestSharedFindingsBuffer:
    def test_add_and_get(self):
        buf = SharedFindingsBuffer()
        buf.add("Agent-A", "Iran tensions rising", "Finding")
        buf.add("Agent-B", "US military moves", "Finding")

        assert buf.count() == 2
        others = buf.get_others("Agent-A")
        assert len(others) == 1
        assert others[0]["agent"] == "Agent-B"
        assert "US military" in others[0]["content"]

    def test_thread_safety(self):
        buf = SharedFindingsBuffer()
        errors = []

        def writer(name, n):
            try:
                for i in range(n):
                    buf.add(name, f"Finding {i}", "Finding")
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=writer, args=(f"Agent-{i}", 50)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert not errors
        assert buf.count() == 250

    def test_get_others_excludes_self(self):
        buf = SharedFindingsBuffer()
        buf.add("A", "fact1", "Finding")
        buf.add("A", "fact2", "Finding")
        buf.add("B", "fact3", "Finding")

        others_a = buf.get_others("A")
        assert len(others_a) == 1
        assert others_a[0]["content"] == "fact3"

        others_b = buf.get_others("B")
        assert len(others_b) == 2


# ─── IncrementalScorer ─────────────────────────────────────────────────────


class TestIncrementalScorer:
    def test_partial_score_after_one_agent(self):
        scorer = IncrementalScorer(total_agents=3, goal="Analyze data")
        result = {
            "name": "Agent-1", "status": "completed",
            "steps": [
                {"tool": "search_nodes", "params": {"label": "Fact"}, "success": True, "result": "[Fact] data"},
                {"tool": "add_knowledge", "params": {"content": "Analysis..."}, "success": True},
            ],
        }
        scores = scorer.add_result(result)
        assert "overall" in scores
        assert scores["completion"] == 33  # 1/3
        assert scores["enrichment"] > 0
        assert scores["discovery"] > 0

    def test_score_increases_with_more_agents(self):
        scorer = IncrementalScorer(total_agents=2, goal="Test")
        r1 = {"name": "A", "status": "completed", "steps": [
            {"tool": "search_nodes", "params": {"label": "Person"}, "success": True, "result": "[Person] Alice"},
            {"tool": "add_knowledge", "params": {"content": "Found Alice"}, "success": True},
        ]}
        r2 = {"name": "B", "status": "completed", "steps": [
            {"tool": "search", "params": {"query": "events"}, "success": True, "result": "[Event] Meeting"},
            {"tool": "add_knowledge", "params": {"content": "Found meeting"}, "success": True},
        ]}

        s1 = scorer.add_result(r1)
        s2 = scorer.add_result(r2)
        assert s2["overall"] >= s1["overall"]
        assert s2["completion"] == 100  # 2/2
        assert s2["collaboration"] > s1["collaboration"]  # multi-agent writing

    def test_efficiency_tracks_success_rate(self):
        scorer = IncrementalScorer(total_agents=1, goal="Test")
        result = {"name": "A", "status": "completed", "steps": [
            {"tool": "search_nodes", "params": {}, "success": True},
            {"tool": "search_nodes", "params": {}, "success": False, "error": "fail"},
            {"tool": "add_knowledge", "params": {"content": "x"}, "success": True},
        ]}
        scores = scorer.add_result(result)
        assert scores["efficiency"] == 66  # 2/3


# ─── SSEEvent ─────────────────────────────────────────────────────────────


class TestSSEEvent:
    def test_make_adds_timestamp(self):
        event = SSEEvent.make(SSEEvent.RUN_START, {"agents": 3})
        assert "ts" in event["data"]
        assert event["event"] == "run_start"
        assert event["data"]["agents"] == 3

    def test_all_event_types_defined(self):
        expected = {"run_start", "agent_start", "agent_plan", "step_done",
                    "finding", "score_update", "agent_done", "run_complete", "error"}
        actual = {getattr(SSEEvent, attr) for attr in dir(SSEEvent) if attr.isupper()}
        assert actual == expected


# ─── RunOrchestrator Integration ──────────────────────────────────────────


class TestRunOrchestrator:
    def _make_orchestrator(self, agents, execution_mode="parallel", goal="Test goal"):
        """Create an orchestrator with mocked dependencies."""
        run = {"run_id": "test123", "agents": [], "status": "running", "_progress_log": []}
        exp = {"name": "Test Exp", "execution_mode": execution_mode}

        # Mock dependencies
        mock_conn = MagicMock()
        mock_conn.executor = MagicMock()

        mock_ctx = MagicMock()
        mock_ctx._has_oriented = False
        mock_ctx._hints_enabled = True
        mock_ctx.metadata = {}
        mock_ctx.project = None

        PlaygroundConn = MagicMock(return_value=mock_conn)
        create_tool_context = MagicMock(return_value=mock_ctx)
        ToolRegistry = MagicMock()
        ToolRegistry.dispatch = MagicMock(return_value="search result: [Fact] test data")

        mock_llm = MagicMock()
        mock_llm.generate = MagicMock(return_value='[{"tool": "search_nodes", "params": {"label": "Fact", "query": "test"}}, {"tool": "add_knowledge", "params": {"content": "Test finding", "node_type": "Finding"}}]')

        sse_events = []

        orchestrator = RunOrchestrator(
            run=run, exp=exp, agents=agents,
            graph="test_graph", registry=MagicMock(),
            run_id="test123", experiment_id="exp001",
            track_id="exp_001_test123", session_id="sess001",
            llm=mock_llm, llm_provider_name="mock",
            goal=goal, mode="sandbox", inputs={},
            PlaygroundConn=PlaygroundConn,
            create_tool_context=create_tool_context,
            ToolRegistry=ToolRegistry,
            graph_node_fn=MagicMock(),
            save_run_fn=MagicMock(),
            check_signal_fn=lambda rid: "running",
            emit_sse_fn=lambda evt, data: sse_events.append((evt, data)),
            get_llm_client_fn=MagicMock(return_value=mock_llm),
            experiment_tools={"search_nodes", "search", "add_knowledge", "add_task"},
        )

        return orchestrator, sse_events, mock_llm

    def test_parallel_mode_runs_all_agents(self):
        agents = [
            {"name": "Analyst-1", "role": "analyst", "agent_id": "a1"},
            {"name": "Analyst-2", "role": "analyst", "agent_id": "a2"},
            {"name": "Analyst-3", "role": "analyst", "agent_id": "a3"},
        ]
        orchestrator, sse_events, _ = self._make_orchestrator(agents)
        result = orchestrator.execute()

        assert result["status"] == "completed"
        assert len(result["agents"]) == 3
        assert all(a["status"] == "completed" for a in result["agents"])
        assert result.get("scores", {}).get("overall", 0) > 0

    def test_parallel_mode_emits_structured_sse(self):
        agents = [{"name": "Agent-1", "role": "worker", "agent_id": "a1"}]
        orchestrator, sse_events, _ = self._make_orchestrator(agents)
        orchestrator.execute()

        event_types = [e[0] for e in sse_events]
        assert "run_start" in event_types
        assert "agent_start" in event_types
        assert "agent_plan" in event_types
        assert "step_done" in event_types
        assert "agent_done" in event_types
        assert "run_complete" in event_types

    def test_parallel_mode_emits_findings(self):
        agents = [{"name": "Agent-1", "role": "worker", "agent_id": "a1"}]
        orchestrator, sse_events, _ = self._make_orchestrator(agents)
        orchestrator.execute()

        finding_events = [e for e in sse_events if e[0] == "finding"]
        assert len(finding_events) >= 1
        assert "content_preview" in finding_events[0][1]

    def test_parallel_mode_emits_score_updates(self):
        agents = [
            {"name": "A1", "role": "analyst", "agent_id": "a1"},
            {"name": "A2", "role": "analyst", "agent_id": "a2"},
        ]
        orchestrator, sse_events, _ = self._make_orchestrator(agents)
        orchestrator.execute()

        score_events = [e for e in sse_events if e[0] == "score_update"]
        assert len(score_events) >= 1
        assert "partial_score" in score_events[0][1]
        assert "scored_agents" in score_events[0][1]

    def test_leader_follower_mode(self):
        agents = [
            {"name": "Leader", "role": "leader", "agent_id": "leader1"},
            {"name": "Worker-1", "role": "worker", "agent_id": "w1"},
            {"name": "Worker-2", "role": "worker", "agent_id": "w2"},
        ]
        orchestrator, sse_events, mock_llm = self._make_orchestrator(
            agents, execution_mode="leader-follower"
        )

        # Leader should return tasks, workers return findings
        call_count = [0]

        def smart_generate(prompt="", **kwargs):
            call_count[0] += 1
            if "add_task" in prompt:
                # Leader planning phase
                return '[{"tool": "add_task", "params": {"title": "Research topic", "description": "Search facts", "assigned_to": "Worker-1"}}, {"tool": "add_task", "params": {"title": "Research topic 2", "description": "Search events", "assigned_to": "Worker-2"}}]'
            elif "Insight" in prompt:
                # Synthesis phase
                return '[{"tool": "search_nodes", "params": {"label": "Finding"}}, {"tool": "add_knowledge", "params": {"content": "Strategic briefing: combined analysis", "node_type": "Insight"}}]'
            else:
                # Worker execution
                return '[{"tool": "search_nodes", "params": {"label": "Fact", "query": "topic"}}, {"tool": "add_knowledge", "params": {"content": "Worker finding", "node_type": "Finding"}}]'

        mock_llm.generate = smart_generate
        result = orchestrator.execute()

        assert result["status"] == "completed"
        # Leader (plan) + 2 workers + Leader (synthesize) = 4 agent results
        assert len(result["agents"]) == 4

    def test_sequential_mode(self):
        agents = [
            {"name": "First", "role": "analyst", "agent_id": "a1"},
            {"name": "Second", "role": "analyst", "agent_id": "a2"},
        ]
        orchestrator, sse_events, _ = self._make_orchestrator(agents, execution_mode="sequential")
        result = orchestrator.execute()

        assert result["status"] == "completed"
        assert len(result["agents"]) == 2

    def test_stop_signal_aborts_execution(self):
        agents = [
            {"name": "Agent-1", "role": "analyst", "agent_id": "a1"},
            {"name": "Agent-2", "role": "analyst", "agent_id": "a2"},
        ]
        orchestrator, sse_events, _ = self._make_orchestrator(agents, execution_mode="sequential")
        # Return "stop" after first agent
        call_count = [0]

        def check_signal(rid):
            call_count[0] += 1
            return "stop" if call_count[0] > 5 else "running"

        orchestrator._check_signal = check_signal
        result = orchestrator.execute()

        assert result["status"] == "aborted"

    def test_cross_agent_findings_visible(self):
        """Agents should see each other's findings via SharedFindingsBuffer."""
        agents = [
            {"name": "Fast-Agent", "role": "analyst", "agent_id": "a1"},
            {"name": "Slow-Agent", "role": "analyst", "agent_id": "a2"},
        ]
        orchestrator, sse_events, _ = self._make_orchestrator(agents, execution_mode="sequential")

        # After first agent writes, second should see it in context
        result = orchestrator.execute()

        # Both completed
        assert len(result["agents"]) == 2
        # Findings buffer should have entries from both
        assert orchestrator.findings.count() >= 2

    def test_queue_mode_maps_to_parallel(self):
        """Old 'queue' mode should be treated as 'parallel'."""
        agents = [{"name": "Agent", "role": "analyst", "agent_id": "a1"}]
        run = {"run_id": "t", "agents": [], "status": "running", "_progress_log": []}
        exp = {"name": "Test", "execution_mode": "queue"}

        orchestrator, _, _ = self._make_orchestrator(agents)
        orchestrator.exp["execution_mode"] = "queue"
        result = orchestrator.execute()

        # Should still complete (parallel mode used internally)
        assert result["status"] == "completed"
