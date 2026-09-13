"""Tests for session demo report formatter."""
from unittest.mock import MagicMock
from contextcore.demo.runner import AgentResult


def _make_result():
    from contextcore.demo.session_demo import SessionDemoResult
    arch = AgentResult(
        response_text="Added rate-limit arch decision.",
        tool_calls=[
            {"tool": "agent_brief", "input": {}, "result": "{}"},
            {"tool": "coverage_score", "input": {}, "result": "{}"},
            {"tool": "add_sdlc_node", "input": {}, "result": '{"ok": true}'},
        ],
        model="claude-haiku-4-5-20251001",
    )
    dev = AgentResult(
        response_text="Added login endpoint module.",
        tool_calls=[
            {"tool": "agent_brief", "input": {}, "result": "{}"},
            {"tool": "add_sdlc_node", "input": {}, "result": '{"ok": true}'},
            {"tool": "add_sdlc_edge", "input": {}, "result": '{"ok": true}'},
        ],
        model="claude-haiku-4-5-20251001",
    )
    return SessionDemoResult(
        session_id="abc123",
        before_coverage={"overall": 0.37, "intent": 0.60, "design": 0.40, "build": 0.20, "verify": 0.40, "evolution": 0.0},
        mid_coverage={"overall": 0.43, "intent": 0.60, "design": 0.60, "build": 0.20, "verify": 0.40, "evolution": 0.0},
        after_coverage={"overall": 0.55, "intent": 0.60, "design": 0.60, "build": 0.60, "verify": 0.40, "evolution": 0.0},
        architect_result=arch,
        dev_result=dev,
        elapsed_ms=4500,
    )


class TestFormatSessionReport:
    def test_report_contains_phase_headers(self):
        from contextcore.demo.session_report import format_session_report
        report = format_session_report(_make_result())
        assert "PHASE 1" in report
        assert "PHASE 2" in report
        assert "PHASE 3" in report
        assert "PHASE 4" in report

    def test_report_contains_session_id(self):
        from contextcore.demo.session_report import format_session_report
        report = format_session_report(_make_result())
        assert "abc123" in report

    def test_report_contains_coverage_values(self):
        from contextcore.demo.session_report import format_session_report
        report = format_session_report(_make_result())
        assert "37%" in report
        assert "55%" in report

    def test_report_contains_agent_names(self):
        from contextcore.demo.session_report import format_session_report
        report = format_session_report(_make_result())
        assert "ARCHITECT" in report
        assert "DEVELOPER" in report

    def test_report_contains_timing(self):
        from contextcore.demo.session_report import format_session_report
        report = format_session_report(_make_result())
        assert "4500" in report or "4.5" in report

    def test_report_contains_tool_call_counts(self):
        from contextcore.demo.session_report import format_session_report
        report = format_session_report(_make_result())
        # Architect: 3 tool calls (1 read, 1 coverage, 1 write)
        # Developer: 3 tool calls (1 read, 1 write node, 1 write edge)
        assert "3" in report
