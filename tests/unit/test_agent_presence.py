"""Tests for agent presence section in orient()."""
import pytest
from unittest.mock import MagicMock, patch


def _make_ctx(agent_id="agent_self", agent_name="Self", tasks=None):
    """Build a minimal ToolContext mock for orient() tests."""
    ctx = MagicMock()
    ctx.agent_id = agent_id
    ctx.agent_name = agent_name
    ctx.conn = MagicMock()
    ctx.conn.namespace = "test_ns"
    ctx.conn._namespace = "test_ns"
    ctx.conn._graph_registry = None
    ctx.runtime_conn = None
    ctx.project = MagicMock()
    ctx.project.get_open_tasks.return_value = tasks or []
    ctx._has_oriented = False
    return ctx


def _build_presence_section(ctx):
    """Extract the presence-building logic so we can unit-test it in isolation."""
    from contextcore.tools.graph import _build_presence_section as _bps
    return _bps(ctx)


def test_presence_section_shows_other_agents_claimed_tasks():
    tasks = [
        {"id": "t1", "title": "Implement auth", "status": "in_progress", "assigned_to": "agent_other"},
        {"id": "t2", "title": "Write tests", "status": "in_progress", "assigned_to": "agent_self"},
    ]
    ctx = _make_ctx(agent_id="agent_self", tasks=tasks)
    section = _build_presence_section(ctx)

    assert "agent_other" in section
    assert "Implement auth" in section
    # Self must NOT appear
    assert "Write tests" not in section


def test_presence_section_empty_when_no_other_agents():
    tasks = [
        {"id": "t1", "title": "Solo task", "status": "in_progress", "assigned_to": "agent_self"},
    ]
    ctx = _make_ctx(agent_id="agent_self", tasks=tasks)
    section = _build_presence_section(ctx)

    assert section == ""


def test_presence_section_empty_when_no_tasks():
    ctx = _make_ctx()
    ctx.project.get_open_tasks.return_value = []
    section = _build_presence_section(ctx)

    assert section == ""


def test_presence_section_handles_no_project():
    ctx = _make_ctx()
    ctx.project = None
    section = _build_presence_section(ctx)

    assert section == ""


def test_presence_section_multiple_tasks_same_agent():
    tasks = [
        {"id": "t1", "title": "Task A", "status": "in_progress", "assigned_to": "agent_bob"},
        {"id": "t2", "title": "Task B", "status": "in_progress", "assigned_to": "agent_bob"},
    ]
    ctx = _make_ctx(tasks=tasks)
    section = _build_presence_section(ctx)

    assert "agent_bob" in section
    assert "Task A" in section
