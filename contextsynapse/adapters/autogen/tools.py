"""
AutoGen adapter — thin wrapper over Universal Tool Layer.

Usage:
    from contextsynapse.adapters.autogen.tools import create_autogen_tools
    tools = create_autogen_tools(namespace="my-project")
"""

from __future__ import annotations

import json
from typing import List, Optional

from contextsynapse.tools import ToolRegistry, ToolContext
from contextsynapse.adapters._base import AIContextDBConnection


def create_autogen_tools(
    namespace: str = "default",
    connection: Optional[AIContextDBConnection] = None,
    categories: Optional[List[str]] = None,
) -> list:
    """Create AutoGen-compatible tool schemas + dispatch function."""
    conn = connection or AIContextDBConnection(namespace=namespace)
    ctx = ToolContext(conn=conn, agent_id="autogen", agent_name="autogen")

    cats = categories or ["graph", "context", "task"]
    schemas = ToolRegistry.to_openai_schemas(cats)  # AutoGen uses OpenAI format

    def dispatch(name: str, arguments: str) -> str:
        args = json.loads(arguments) if arguments else {}
        return ToolRegistry.dispatch(name, ctx, args)

    return schemas, dispatch
