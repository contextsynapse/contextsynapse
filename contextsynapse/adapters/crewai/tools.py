"""
CrewAI adapter — thin wrapper over Universal Tool Layer.

Usage:
    from contextsynapse.adapters.crewai.tools import create_crewai_tools
    tools = create_crewai_tools(namespace="my-project")
"""

from __future__ import annotations

import json
from typing import List, Optional

from contextsynapse.tools import ToolRegistry, ToolContext
from contextsynapse.adapters._base import AIContextDBConnection


def create_crewai_tools(
    namespace: str = "default",
    connection: Optional[AIContextDBConnection] = None,
    categories: Optional[List[str]] = None,
) -> list:
    """Create CrewAI-compatible tools from the Universal Tool Registry."""
    try:
        from crewai_tools import BaseTool
    except ImportError:
        # Fallback: return dicts if crewai not installed
        return _create_dict_tools(namespace, connection, categories)

    conn = connection or AIContextDBConnection(namespace=namespace)
    ctx = ToolContext(conn=conn, agent_id="crewai", agent_name="crewai")

    cats = categories or ["graph", "context", "task"]
    tools = []
    for td in ToolRegistry.by_category(*cats):
        # Dynamically create BaseTool subclass
        tool_cls = type(td.name, (BaseTool,), {
            "name": td.name,
            "description": td.description,
            "_td": td,
            "_ctx": ctx,
            "_run": lambda self, input_str="", **kw: self._td.handler(self._ctx, **kw) if kw else self._td.handler(self._ctx, **_parse(input_str, self._td)),
        })
        tools.append(tool_cls())
    return tools


def _create_dict_tools(namespace, connection, categories):
    """Fallback when crewai is not installed."""
    conn = connection or AIContextDBConnection(namespace=namespace)
    ctx = ToolContext(conn=conn, agent_id="crewai", agent_name="crewai")
    cats = categories or ["graph", "context", "task"]
    return [
        {"name": td.name, "description": td.description, "handler": lambda _td=td, **kw: _td.handler(ctx, **kw)}
        for td in ToolRegistry.by_category(*cats)
    ]


def _parse(input_str, td):
    try:
        return json.loads(input_str) if input_str else {}
    except (json.JSONDecodeError, AttributeError):
        return {td.params[0].name: input_str} if td.params else {}
