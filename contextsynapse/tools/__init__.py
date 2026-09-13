"""
Universal Tool Layer — Define once, use everywhere.

Import this module to register all tools into the ToolRegistry.
Adapters loop over ToolRegistry to generate their format-specific schemas.

Usage:
    from contextsynapse.tools import ToolRegistry, ToolContext

    # All tools are registered at import time
    print(f"{ToolRegistry.count()} tools available")

    # Export for OpenAI
    schemas = ToolRegistry.to_openai_schemas()

    # Dispatch a call
    result = ToolRegistry.dispatch("ask", ctx, {"question": "What are the requirements?"})
"""

from .registry import ToolRegistry, ToolContext, ToolDef, ToolParam, tool

# Import all tool modules to trigger registration
from . import graph           # 8 tools
from . import tasks           # 11 tools
from . import context         # 10 tools (briefing, ask, task_context, rag_query, rag_graph, search, web_fetch, reason, graph_timeline, graph_diff)
from . import workspace_tools # 8 tools
from . import gateway_tools   # 5 tools (report_external, report_tokens, my_usage, boundary_costs, get_tool_policy)
from . import a2a_tools       # 4 tools (a2a_discover, a2a_send_task, a2a_get_task, a2a_cancel_task)
from . import intelligence      # 5 tools (check_freshness, rate_context, detect_conflicts, resolve_conflict, get_suggestions)
from . import memory            # 2 tools (remember, recall)
from . import cognition_tools   # 2 tools (trace_lineage, invalidate_node)
from . import shield_tools     # 1 tool (context_report_card)

__all__ = [
    "ToolRegistry", "ToolContext", "ToolDef", "ToolParam", "tool",
]
