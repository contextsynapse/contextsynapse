"""Tests for multi-agent session demo."""
import pytest
from unittest.mock import patch, MagicMock


def _make_mock_response(text="Done.", tool_use=None):
    """Build a mock Anthropic response."""
    resp = MagicMock()
    if tool_use:
        resp.stop_reason = "tool_use"
        blocks = []
        for tu in tool_use:
            block = MagicMock()
            block.type = "tool_use"
            block.id = f"tu_{tu['name']}"
            block.name = tu["name"]
            block.input = tu["input"]
            blocks.append(block)
        resp.content = blocks
    else:
        resp.stop_reason = "end_turn"
        block = MagicMock()
        block.type = "text"
        block.text = text
        resp.content = [block]
    return resp


class TestSessionDemoResult:
    def test_result_has_required_fields(self, tmp_path):
        from contextcore.demo.session_demo import SessionDemoResult
        r = SessionDemoResult(
            session_id="abc",
            before_coverage={"overall": 0.37},
            mid_coverage={"overall": 0.45},
            after_coverage={"overall": 0.55},
            architect_result=MagicMock(),
            dev_result=MagicMock(),
            elapsed_ms=1234,
        )
        assert r.session_id == "abc"
        assert r.before_coverage["overall"] == 0.37
        assert r.elapsed_ms == 1234


class TestSessionDemoPhases:
    @patch("anthropic.Anthropic")
    def test_full_pipeline_coverage_increases(self, mock_anthropic_cls, tmp_path):
        """Full session demo: coverage goes up after both agents write nodes."""
        from contextcore.demo.session_demo import run_session_demo

        # Architect writes an ArchDecision, then ends
        arch_tool_call = _make_mock_response(tool_use=[{
            "name": "add_sdlc_node",
            "input": {
                "project_name": "PLACEHOLDER",
                "node_type": "ArchDecision",
                "node_id": "arch:rate-limit-redis",
                "properties": {
                    "decision": "Use Redis for rate limiting with sliding window.",
                    "rationale": "Low latency, atomic operations.",
                    "version": 1,
                },
            },
        }])
        arch_end = _make_mock_response(text="I added a rate-limiting architecture decision.")

        # Dev writes a CodeModule, then ends
        dev_tool_call = _make_mock_response(tool_use=[{
            "name": "add_sdlc_node",
            "input": {
                "project_name": "PLACEHOLDER",
                "node_type": "CodeModule",
                "node_id": "code:login-endpoint",
                "properties": {
                    "summary": "POST /auth/login — validates credentials, enforces rate limit, returns JWT.",
                    "version": 1,
                },
            },
        }])
        dev_end = _make_mock_response(text="I added the login endpoint module.")

        # Mock client returns alternating tool_use/end_turn for each agent
        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [
            arch_tool_call, arch_end,   # architect: tool_use then end
            dev_tool_call, dev_end,     # developer: tool_use then end
        ]
        mock_anthropic_cls.return_value = mock_client

        result = run_session_demo(
            model="claude-haiku-4-5-20251001",
            base_path=str(tmp_path),
        )

        assert result.before_coverage["overall"] == pytest.approx(0.37, abs=0.01)
        assert result.after_coverage["overall"] > result.before_coverage["overall"]
        assert result.elapsed_ms >= 0

    @patch("anthropic.Anthropic")
    def test_session_created_with_agents(self, mock_anthropic_cls, tmp_path):
        """Session is created and agents are granted access."""
        from contextcore.demo.session_demo import run_session_demo

        mock_client = MagicMock()
        end = _make_mock_response(text="Nothing to do.")
        mock_client.messages.create.return_value = end
        mock_anthropic_cls.return_value = mock_client

        result = run_session_demo(
            model="claude-haiku-4-5-20251001",
            base_path=str(tmp_path),
        )

        assert result.session_id  # non-empty
        assert result.architect_result is not None
        assert result.dev_result is not None

    @patch("anthropic.Anthropic")
    def test_mid_coverage_captured_between_agents(self, mock_anthropic_cls, tmp_path):
        """Mid-coverage is captured after architect but before developer."""
        from contextcore.demo.session_demo import run_session_demo

        # Architect writes a node
        arch_tool = _make_mock_response(tool_use=[{
            "name": "add_sdlc_node",
            "input": {
                "project_name": "PLACEHOLDER",
                "node_type": "Constraint",
                "node_id": "constraint:max-payload",
                "properties": {
                    "description": "Request payload must not exceed 1MB.",
                    "version": 1,
                },
            },
        }])
        arch_end = _make_mock_response(text="Added constraint.")
        dev_end = _make_mock_response(text="Looks good.")

        mock_client = MagicMock()
        mock_client.messages.create.side_effect = [arch_tool, arch_end, dev_end]
        mock_anthropic_cls.return_value = mock_client

        result = run_session_demo(
            model="claude-haiku-4-5-20251001",
            base_path=str(tmp_path),
        )

        # mid_coverage captured between architect and developer
        assert "overall" in result.mid_coverage
