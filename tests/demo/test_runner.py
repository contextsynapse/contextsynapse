"""Tests for demo agent runners — Anthropic API is mocked throughout."""
import json
import pytest
from unittest.mock import MagicMock, patch


def _make_text_response(text: str):
    """Build a mock Anthropic end_turn response."""
    block = MagicMock()
    block.type = "text"
    block.text = text
    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]
    return response


def _make_tool_use_response(tool_name: str, tool_id: str, tool_input: dict):
    """Build a mock Anthropic tool_use response followed by end_turn."""
    tool_block = MagicMock()
    tool_block.type = "tool_use"
    tool_block.id = tool_id
    tool_block.name = tool_name
    tool_block.input = tool_input

    tool_response = MagicMock()
    tool_response.stop_reason = "tool_use"
    tool_response.content = [tool_block]
    return tool_response


class TestAgentResult:
    def test_agent_result_has_required_fields(self):
        from contextcore.demo.runner import AgentResult
        result = AgentResult(
            response_text="hello",
            tool_calls=[],
            model="claude-haiku-4-5-20251001",
        )
        assert result.response_text == "hello"
        assert result.tool_calls == []
        assert result.model == "claude-haiku-4-5-20251001"


class TestBlindAgent:
    def test_blind_agent_returns_agent_result(self):
        from contextcore.demo.runner import run_blind_agent, AgentResult
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_text_response(
            "I would implement a JWT-based login endpoint."
        )
        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=mock_client):
            result = run_blind_agent(
                task="Design a login endpoint.",
                model="claude-haiku-4-5-20251001",
            )
        assert isinstance(result, AgentResult)

    def test_blind_agent_response_contains_text(self):
        from contextcore.demo.runner import run_blind_agent
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_text_response(
            "JWT-based login endpoint."
        )
        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=mock_client):
            result = run_blind_agent(
                task="Design a login endpoint.",
                model="claude-haiku-4-5-20251001",
            )
        assert "JWT" in result.response_text

    def test_blind_agent_has_zero_tool_calls(self):
        from contextcore.demo.runner import run_blind_agent
        mock_client = MagicMock()
        mock_client.messages.create.return_value = _make_text_response("response")
        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=mock_client):
            result = run_blind_agent(task="task", model="claude-haiku-4-5-20251001")
        assert result.tool_calls == []


class TestContextAgent:
    def test_context_agent_returns_agent_result(self, tmp_path):
        from contextcore.demo.runner import run_context_agent, AgentResult
        from contextcore.demo.scenario import seed_project
        pc = seed_project("ctx-runner-test", base_path=str(tmp_path))

        # Simulate: one tool call (agent_brief) then end_turn
        tool_response = _make_tool_use_response(
            "agent_brief", "tool_001", {"project_name": pc.name}
        )
        final_response = _make_text_response("Based on the brief, I'll implement the login endpoint.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, final_response]

        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=mock_client):
            result = run_context_agent(
                project_name=pc.name,
                task="Implement login endpoint.",
                model="claude-haiku-4-5-20251001",
                base_path=str(tmp_path),
            )
        assert isinstance(result, AgentResult)

    def test_context_agent_records_tool_calls(self, tmp_path):
        from contextcore.demo.runner import run_context_agent
        from contextcore.demo.scenario import seed_project
        pc = seed_project("ctx-tool-record", base_path=str(tmp_path))

        tool_response = _make_tool_use_response(
            "coverage_score", "tool_002", {"project_name": pc.name}
        )
        final_response = _make_text_response("Coverage looks thin on build.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, final_response]

        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=mock_client):
            result = run_context_agent(
                project_name=pc.name,
                task="Check coverage.",
                model="claude-haiku-4-5-20251001",
                base_path=str(tmp_path),
            )
        assert len(result.tool_calls) == 1
        assert result.tool_calls[0]["tool"] == "coverage_score"

    def test_context_agent_dispatches_add_sdlc_node(self, tmp_path):
        from contextcore.demo.runner import run_context_agent
        from contextcore.demo.scenario import seed_project
        import uuid
        pc = seed_project("ctx-write-test", base_path=str(tmp_path))
        uid = uuid.uuid4().hex[:8]
        new_node_id = f"code:login-ep-{uid}"

        tool_response = _make_tool_use_response(
            "add_sdlc_node", "tool_003", {
                "project_name": pc.name,
                "node_type": "CodeModule",
                "node_id": new_node_id,
                "properties": {"summary": "Login endpoint handler", "version": 1},
            }
        )
        final_response = _make_text_response("Added the login endpoint module.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [tool_response, final_response]

        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=mock_client):
            run_context_agent(
                project_name=pc.name,
                task="Implement login endpoint.",
                model="claude-haiku-4-5-20251001",
                base_path=str(tmp_path),
            )

        # Verify the node was actually written to the graph
        node = pc.db.get_node(new_node_id)
        assert node is not None
        assert node.label == "CodeModule"
