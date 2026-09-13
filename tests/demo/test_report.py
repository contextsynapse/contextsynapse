"""Tests for demo report formatter."""
from contextcore.demo.runner import AgentResult


def _make_result(text: str, tool_calls: list) -> AgentResult:
    return AgentResult(
        response_text=text,
        tool_calls=tool_calls,
        model="claude-haiku-4-5-20251001",
    )


def _coverage(overall: float, build: float) -> dict:
    return {
        "intent": 0.6, "design": 0.4,
        "build": build, "verify": 0.4,
        "evolution": None, "overall": overall,
        "project_type": "greenfield",
    }


class TestFormatReport:
    def test_returns_string(self):
        from contextcore.demo.report import format_report
        result = format_report(
            before_coverage=_coverage(0.37, 0.2),
            blind=_make_result("generic response", []),
            context=_make_result("referenced req:login", [{"tool": "agent_brief", "input": {}, "result": "{}"}]),
            after_coverage=_coverage(0.47, 0.4),
        )
        assert isinstance(result, str)

    def test_contains_coverage_before(self):
        from contextcore.demo.report import format_report
        result = format_report(
            before_coverage=_coverage(0.37, 0.2),
            blind=_make_result("x", []),
            context=_make_result("y", []),
            after_coverage=_coverage(0.47, 0.4),
        )
        assert "37%" in result

    def test_contains_coverage_after(self):
        from contextcore.demo.report import format_report
        result = format_report(
            before_coverage=_coverage(0.37, 0.2),
            blind=_make_result("x", []),
            context=_make_result("y", []),
            after_coverage=_coverage(0.47, 0.4),
        )
        assert "47%" in result

    def test_contains_tool_call_count(self):
        from contextcore.demo.report import format_report
        tool_calls = [
            {"tool": "agent_brief", "input": {}, "result": "{}"},
            {"tool": "add_sdlc_node", "input": {}, "result": "{}"},
        ]
        result = format_report(
            before_coverage=_coverage(0.37, 0.2),
            blind=_make_result("x", []),
            context=_make_result("y", tool_calls),
            after_coverage=_coverage(0.47, 0.4),
        )
        assert "2" in result  # tool call count appears somewhere

    def test_contains_blind_agent_section(self):
        from contextcore.demo.report import format_report
        result = format_report(
            before_coverage=_coverage(0.37, 0.2),
            blind=_make_result("generic JWT response", []),
            context=_make_result("y", []),
            after_coverage=_coverage(0.47, 0.4),
        )
        assert "BLIND" in result.upper()
        assert "generic JWT response" in result

    def test_contains_context_agent_section(self):
        from contextcore.demo.report import format_report
        result = format_report(
            before_coverage=_coverage(0.37, 0.2),
            blind=_make_result("x", []),
            context=_make_result("referenced req:login in my design", []),
            after_coverage=_coverage(0.47, 0.4),
        )
        assert "CONTEXT" in result.upper()
        assert "referenced req:login" in result

    def test_coverage_delta_shown(self):
        from contextcore.demo.report import format_report
        result = format_report(
            before_coverage=_coverage(0.37, 0.2),
            blind=_make_result("x", []),
            context=_make_result("y", []),
            after_coverage=_coverage(0.47, 0.4),
        )
        # Delta is +10% overall
        assert "+10%" in result or "+0.10" in result or "10%" in result
