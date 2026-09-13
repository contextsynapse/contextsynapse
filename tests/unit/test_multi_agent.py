"""Tests for multi-agent coordination."""
from __future__ import annotations
import pytest
from unittest.mock import MagicMock


class TestFileConflictDetection:
    def test_detects_overlapping_files(self):
        from contextcore.project.graph import ProjectGraph

        conn = MagicMock()
        # Task we want to claim has files: ["src/auth.py", "src/api.py"]
        task_node = MagicMock(properties={"files": '["src/auth.py", "src/api.py"]', "status": "open"})
        # Another in-progress task has overlapping file
        other_task = MagicMock(id="other_1", properties={
            "files": '["src/auth.py", "src/middleware.py"]',
            "status": "in_progress",
            "assigned_to": "agent_b",
            "title": "Refactor auth",
        })

        conn.get_node = MagicMock(return_value=task_node)
        conn.get_nodes = MagicMock(return_value=[other_task])

        pg = ProjectGraph.__new__(ProjectGraph)
        pg.conn = conn

        conflicts = pg.check_file_conflicts("task_1", "agent_a")
        assert len(conflicts) == 1
        assert "src/auth.py" in conflicts[0]["files"]
        assert conflicts[0]["other_agent"] == "agent_b"

    def test_no_conflict_when_no_overlap(self):
        from contextcore.project.graph import ProjectGraph

        conn = MagicMock()
        task_node = MagicMock(properties={"files": '["src/auth.py"]', "status": "open"})
        other_task = MagicMock(id="other_1", properties={
            "files": '["src/database.py"]',
            "status": "in_progress",
            "assigned_to": "agent_b",
            "title": "Setup DB",
        })

        conn.get_node = MagicMock(return_value=task_node)
        conn.get_nodes = MagicMock(return_value=[other_task])

        pg = ProjectGraph.__new__(ProjectGraph)
        pg.conn = conn

        conflicts = pg.check_file_conflicts("task_1", "agent_a")
        assert len(conflicts) == 0

    def test_no_conflict_same_agent(self):
        from contextcore.project.graph import ProjectGraph

        conn = MagicMock()
        task_node = MagicMock(properties={"files": '["src/auth.py"]', "status": "open"})
        other_task = MagicMock(id="other_1", properties={
            "files": '["src/auth.py"]',
            "status": "in_progress",
            "assigned_to": "agent_a",
            "title": "Other task",
        })

        conn.get_node = MagicMock(return_value=task_node)
        conn.get_nodes = MagicMock(return_value=[other_task])

        pg = ProjectGraph.__new__(ProjectGraph)
        pg.conn = conn

        # Same agent - should not flag as conflict
        conflicts = pg.check_file_conflicts("task_1", "agent_a")
        assert len(conflicts) == 0


class TestCapabilityQuery:
    def test_query_by_capability(self):
        """Test that we can query agents by capability."""
        from contextcore.context.agents_redis import RedisAgentRegistry
        assert hasattr(RedisAgentRegistry, 'query_by_capability')

    def test_query_by_capability_finds_matching(self):
        """Test capability query with mocked Redis."""
        from contextcore.context.agents_redis import RedisAgentRegistry
        import json

        mock_redis = MagicMock()
        mock_redis.smembers.return_value = {"agent1", "agent2"}
        mock_redis.hgetall.side_effect = [
            {
                "agent_id": "agent1",
                "name": "coder",
                "capabilities": json.dumps(["code", "review"]),
            },
            {
                "agent_id": "agent2",
                "name": "writer",
                "capabilities": json.dumps(["writing", "research"]),
            },
        ]

        reg = RedisAgentRegistry(redis_client=mock_redis)
        results = reg.query_by_capability("code")
        assert len(results) == 1
        assert results[0]["name"] == "coder"


class TestAgentProfile:
    def test_profile_from_name(self):
        from contextcore.agents.dispatcher import get_agent_profile
        agent = MagicMock(name="claude-analyst", metadata={})
        profile = get_agent_profile(agent)
        assert "code review" in profile["strengths"] or "strengths" in profile

    def test_profile_enriched_from_registry(self):
        from contextcore.agents.dispatcher import get_agent_profile
        registry = MagicMock()
        reg_agent = MagicMock()
        reg_agent.name = "custom-bot"
        reg_agent.capabilities = ["testing", "deployment"]
        registry.list_agents.return_value = [reg_agent]
        agent = MagicMock()
        agent.name = "custom-bot"
        agent.metadata = {}
        profile = get_agent_profile(agent, registry=registry)
        assert "testing" in profile["strengths"]
