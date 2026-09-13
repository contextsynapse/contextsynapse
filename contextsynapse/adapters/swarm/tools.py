"""
Swarm / Agno adapter — thin wrapper over Universal Tool Layer.

Swarm uses plain Python functions as tools.

Usage:
    from contextsynapse.adapters.swarm.tools import create_swarm_tools
    functions = create_swarm_tools(namespace="my-project")
"""

from __future__ import annotations

import json
from typing import List, Optional

from contextsynapse.tools import ToolRegistry, ToolContext
from contextsynapse.adapters._base import AIContextDBConnection


def create_swarm_tools(
    namespace: str = "default",
    connection: Optional[AIContextDBConnection] = None,
    categories: Optional[List[str]] = None,
) -> list:
    """Create plain functions for Swarm/Agno from the Universal Tool Registry."""
    conn = connection or AIContextDBConnection(namespace=namespace)
    ctx = ToolContext(conn=conn, agent_id="swarm", agent_name="swarm")

    cats = categories or ["graph", "context", "task"]
    functions = []
    for td in ToolRegistry.by_category(*cats):
        def _fn(input_str="", _td=td, _ctx=ctx, **kwargs):
            if kwargs:
                return _td.handler(_ctx, **kwargs)
            try:
                args = json.loads(input_str) if input_str else {}
            except (json.JSONDecodeError, AttributeError):
                args = {_td.params[0].name: input_str} if _td.params else {}
            return _td.handler(_ctx, **args)

        _fn.__name__ = td.name
        _fn.__doc__ = td.description
        functions.append(_fn)
    return functions
