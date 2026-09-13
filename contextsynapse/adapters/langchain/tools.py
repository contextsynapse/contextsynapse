"""
LangChain adapter — thin wrapper over Universal Tool Layer.

Usage:
    from contextsynapse.adapters.langchain.tools import create_langchain_tools
    tools = create_langchain_tools(namespace="my-project")
"""

from __future__ import annotations

import json
from typing import List, Optional

from contextsynapse.tools import ToolRegistry, ToolContext
from contextsynapse.adapters._base import AIContextDBConnection


def create_langchain_tools(
    namespace: str = "default",
    connection: Optional[AIContextDBConnection] = None,
    categories: Optional[List[str]] = None,
) -> list:
    """Create LangChain Tool objects from the Universal Tool Registry."""
    try:
        from langchain_core.tools import Tool
    except ImportError:
        from langchain.tools import Tool

    conn = connection or AIContextDBConnection(namespace=namespace)
    ctx = ToolContext(conn=conn, agent_id="langchain", agent_name="langchain")

    cats = categories or ["graph", "context", "task"]
    tools = []
    for td in ToolRegistry.by_category(*cats):
        def _handler(input_str, _td=td, _ctx=ctx):
            try:
                args = json.loads(input_str) if input_str.startswith("{") else {}
            except (json.JSONDecodeError, AttributeError):
                args = {td.params[0].name: input_str} if td.params else {}
            return _td.handler(_ctx, **args)

        tools.append(Tool(name=td.name, func=_handler, description=td.description))
    return tools
