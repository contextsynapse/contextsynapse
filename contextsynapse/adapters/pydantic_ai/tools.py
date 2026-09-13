"""
Pydantic AI adapter — thin wrapper over Universal Tool Layer.

Usage:
    from contextsynapse.adapters.pydantic_ai.tools import create_pydantic_ai_tools
    tools = create_pydantic_ai_tools(namespace="my-project")
"""

from __future__ import annotations

import json
from typing import List, Optional

from contextsynapse.tools import ToolRegistry, ToolContext
from contextsynapse.adapters._base import AIContextDBConnection


def create_pydantic_ai_tools(
    namespace: str = "default",
    connection: Optional[AIContextDBConnection] = None,
    categories: Optional[List[str]] = None,
) -> list:
    """Create Pydantic AI-compatible tools from the Universal Tool Registry."""
    conn = connection or AIContextDBConnection(namespace=namespace)
    ctx = ToolContext(conn=conn, agent_id="pydantic_ai", agent_name="pydantic_ai")

    cats = categories or ["graph", "context", "task"]
    tools = []
    for td in ToolRegistry.by_category(*cats):
        def _handler(_ctx=ctx, _td=td, **kwargs):
            return _td.handler(_ctx, **kwargs)

        _handler.__name__ = td.name
        _handler.__doc__ = td.description
        tools.append(_handler)
    return tools
