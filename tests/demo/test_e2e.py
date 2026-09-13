"""End-to-end smoke test for the demo pipeline — Anthropic API fully mocked."""
import json
import uuid
from unittest.mock import MagicMock, patch


def _text_block(text: str):
    b = MagicMock()
    b.type = "text"
    b.text = text
    return b


def _tool_block(name: str, tool_id: str, input_dict: dict):
    b = MagicMock()
    b.type = "tool_use"
    b.id = tool_id
    b.name = name
    b.input = input_dict
    return b


def _end_turn(text: str):
    r = MagicMock()
    r.stop_reason = "end_turn"
    r.content = [_text_block(text)]
    return r


def _tool_use(blocks):
    r = MagicMock()
    r.stop_reason = "tool_use"
    r.content = blocks
    return r


class TestEndToEnd:
    def test_full_pipeline_coverage_increases(self, tmp_path):
        """Full pipeline: seed -> blind -> context (writes 1 CodeModule) -> coverage up."""
        from contextcore.demo.scenario import seed_project, AUTH_SCENARIO
        from contextcore.demo.runner import run_blind_agent, run_context_agent
        from contextcore.demo.report import format_report

        uid = uuid.uuid4().hex[:8]
        project_name = f"e2e-demo-{uid}"
        pc = seed_project(project_name, base_path=str(tmp_path))
        before_coverage = pc.coverage_score()
        assert abs(before_coverage["overall"] - 0.37) < 0.01

        # Blind agent — returns text, no tool calls
        blind_mock = MagicMock()
        blind_mock.messages.create.return_value = _end_turn(
            "I would implement a REST endpoint POST /auth/login using JWT tokens."
        )

        # Context agent — calls agent_brief, then adds a CodeModule
        node_id = f"code:login-ep-{uid}"
        ctx_calls = [
            _tool_use([
                _tool_block("agent_brief", "t1", {"project_name": project_name, "phase": "intent"}),
            ]),
            _tool_use([
                _tool_block("add_sdlc_node", "t2", {
                    "project_name": project_name,
                    "node_type": "CodeModule",
                    "node_id": node_id,
                    "properties": {"summary": "Login endpoint — validates credentials, issues JWT", "version": 1},
                }),
            ]),
            _end_turn(
                f"I reviewed the context. The existing JWT decision (arch:jwt) and bcrypt decision "
                f"(arch:bcrypt) guide this implementation. I've written the new module as {node_id}."
            ),
        ]
        ctx_mock = MagicMock()
        ctx_mock.messages.create.side_effect = ctx_calls

        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=blind_mock):
            blind_result = run_blind_agent(task=AUTH_SCENARIO["task"], model="claude-haiku-4-5-20251001")

        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=ctx_mock):
            context_result = run_context_agent(
                project_name=project_name,
                task=AUTH_SCENARIO["task"],
                model="claude-haiku-4-5-20251001",
                base_path=str(tmp_path),
            )

        after_coverage = pc.coverage_score()

        # Coverage must have gone up
        assert after_coverage["overall"] > before_coverage["overall"]
        assert after_coverage["build"] > before_coverage["build"]

        # Context agent recorded tool calls
        assert len(context_result.tool_calls) == 2
        tool_names = [tc["tool"] for tc in context_result.tool_calls]
        assert "agent_brief" in tool_names
        assert "add_sdlc_node" in tool_names

        # New node actually exists in the graph
        node = pc.db.get_node(node_id)
        assert node is not None
        assert node.label == "CodeModule"

        # Blind agent has zero tool calls
        assert context_result.tool_calls != []
        assert blind_result.tool_calls == []

        # Report renders without error and contains key strings
        report = format_report(
            before_coverage=before_coverage,
            blind=blind_result,
            context=context_result,
            after_coverage=after_coverage,
        )
        assert "37%" in report
        assert "BLIND" in report.upper()
        assert "CONTEXT" in report.upper()
        assert "VERDICT" in report.upper()

    def test_report_shows_positive_delta(self, tmp_path):
        """Report string contains a positive coverage delta."""
        from contextcore.demo.scenario import seed_project, AUTH_SCENARIO
        from contextcore.demo.runner import run_blind_agent, run_context_agent
        from contextcore.demo.report import format_report

        uid = uuid.uuid4().hex[:8]
        project_name = f"e2e-delta-{uid}"
        pc = seed_project(project_name, base_path=str(tmp_path))
        before_coverage = pc.coverage_score()

        node_id = f"code:ep-{uid}"
        arch_id = f"arch:rate-{uid}"

        ctx_calls = [
            _tool_use([_tool_block("coverage_score", "t1", {"project_name": project_name})]),
            _tool_use([
                _tool_block("add_sdlc_node", "t2", {
                    "project_name": project_name,
                    "node_type": "CodeModule",
                    "node_id": node_id,
                    "properties": {"summary": "Login handler", "version": 1},
                }),
            ]),
            _tool_use([
                _tool_block("add_sdlc_node", "t3", {
                    "project_name": project_name,
                    "node_type": "ArchDecision",
                    "node_id": arch_id,
                    "properties": {
                        "decision": "Rate-limit at API gateway layer",
                        "rationale": "Centralises enforcement, no app-level state needed",
                        "version": 1,
                    },
                }),
            ]),
            _end_turn("Implemented login endpoint, added rate-limit architecture decision."),
        ]
        ctx_mock = MagicMock()
        ctx_mock.messages.create.side_effect = ctx_calls

        blind_mock = MagicMock()
        blind_mock.messages.create.return_value = _end_turn("Generic JWT implementation.")

        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=blind_mock):
            blind_result = run_blind_agent(task=AUTH_SCENARIO["task"], model="claude-haiku-4-5-20251001")

        with patch("contextcore.demo.runner.anthropic.Anthropic", return_value=ctx_mock):
            context_result = run_context_agent(
                project_name=project_name,
                task=AUTH_SCENARIO["task"],
                model="claude-haiku-4-5-20251001",
                base_path=str(tmp_path),
            )

        after_coverage = pc.coverage_score()
        # Seeded: overall=37%, build=20%. After 1 CodeModule + 1 ArchDecision: overall=47%, build=40%
        assert abs(after_coverage["overall"] - 0.47) < 0.01

        report = format_report(
            before_coverage=before_coverage,
            blind=blind_result,
            context=context_result,
            after_coverage=after_coverage,
        )
        # Report should show +10% delta
        assert "10%" in report
