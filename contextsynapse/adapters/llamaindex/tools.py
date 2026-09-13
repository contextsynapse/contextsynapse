"""
LlamaIndex adapter — thin wrapper over Universal Tool Layer.

Usage:
    from contextsynapse.adapters.llamaindex.tools import create_llamaindex_tools
    tools = create_llamaindex_tools(namespace="my-project")
"""

from __future__ import annotations

import json
from typing import List, Optional

from contextsynapse.tools import ToolRegistry, ToolContext
from contextsynapse.adapters._base import AIContextDBConnection


def create_llamaindex_tools(
    namespace: str = "default",
    connection: Optional[AIContextDBConnection] = None,
    categories: Optional[List[str]] = None,
) -> list:
    """Create LlamaIndex-compatible tools from the Universal Tool Registry."""
    try:
        from llama_index.core.tools import FunctionTool
    except ImportError:
        # Fallback: return callables
        return _create_callable_tools(namespace, connection, categories)

    conn = connection or AIContextDBConnection(namespace=namespace)
    ctx = ToolContext(conn=conn, agent_id="llamaindex", agent_name="llamaindex")

    cats = categories or ["graph", "context", "task"]
    tools = []
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
        tools.append(FunctionTool.from_defaults(fn=_fn, name=td.name, description=td.description))
    return tools


def _create_callable_tools(namespace, connection, categories):
    """Fallback when llama_index is not installed."""
    conn = connection or AIContextDBConnection(namespace=namespace)
    ctx = ToolContext(conn=conn, agent_id="llamaindex", agent_name="llamaindex")
    cats = categories or ["graph", "context", "task"]

    tools = []
    for td in ToolRegistry.by_category(*cats):
        def _fn(_td=td, _ctx=ctx, **kwargs):
            return _td.handler(_ctx, **kwargs)
        _fn.__name__ = td.name
        _fn.__doc__ = td.description
        tools.append(_fn)
    return tools
