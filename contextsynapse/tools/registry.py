"""
Universal Tool Registry
========================
Define each tool once. Every adapter (MCP, OpenAI, LangChain, CrewAI, etc.)
reads from this registry and converts to its own format.

Usage:
    from contextsynapse.tools.registry import ToolRegistry, ToolDef, ToolParam, ToolContext

    # Register a tool
    ToolRegistry.register(ToolDef(
        name="query_graph",
        description="Execute an AIQL query",
        category="graph",
        params=[ToolParam("query", "string", "AIQL query string")],
        handler=_query_graph,
    ))

    # Dispatch a tool call
    result = ToolRegistry.dispatch("query_graph", ctx, {"query": "SELECT * FROM Task"})

    # Export for OpenAI
    schemas = ToolRegistry.to_openai_schemas()
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_MISSING = object()


@dataclass
class ToolParam:
    """A single parameter for a tool."""
    name: str
    type: str = "string"       # string | integer | number | boolean | object | array
    description: str = ""
    required: bool = True
    default: Any = _MISSING
    enum: Optional[List[str]] = None


@dataclass
class ToolDef:
    """A tool definition — name, description, parameters, and handler."""
    name: str
    description: str
    category: str              # graph | task | workspace | context | extraction | code | pipeline
    params: List[ToolParam] = field(default_factory=list)
    handler: Optional[Callable] = None  # (ctx: ToolContext, **kwargs) -> str
    requires_project: bool = False
    requires_workspace: bool = False

    def to_openai_schema(self) -> dict:
        """Convert to OpenAI function-calling format."""
        properties = {}
        required = []
        for p in self.params:
            prop: Dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            if p.default is not _MISSING:
                prop["default"] = p.default
            properties[p.name] = prop
            if p.required and p.default is _MISSING:
                required.append(p.name)

        schema: Dict[str, Any] = {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                },
            },
        }
        if required:
            schema["function"]["parameters"]["required"] = required
        return schema

    def to_json_schema(self) -> dict:
        """Convert to framework-agnostic JSON Schema."""
        properties = {}
        required = []
        for p in self.params:
            prop: Dict[str, Any] = {"type": p.type, "description": p.description}
            if p.enum:
                prop["enum"] = p.enum
            if p.default is not _MISSING:
                prop["default"] = p.default
            properties[p.name] = prop
            if p.required and p.default is _MISSING:
                required.append(p.name)

        return {
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            },
        }


@dataclass
class ToolContext:
    """Injected into every tool handler. Replaces module-level globals.

    Built once per agent session, passed to every tool call.
    """
    conn: Any                          # AIContextDBConnection (atomic context — knowledge)
    runtime_conn: Any = None           # AIContextDBConnection (runtime — tasks, findings, actions)
    agent_id: str = ""
    agent_name: str = ""
    project: Any = None                # Optional[ProjectGraph]
    workspace: Any = None              # Optional[Workspace]
    thread_id: str = ""
    resolved_session: Any = None

    # Hint state — tracks agent behavior for contextual hints
    _steps_taken: int = 0
    _writes_made: int = 0
    _has_oriented: bool = False
    _hints_enabled: bool = True

    def record_provenance(self, operation: str, target_type: str, target_id: str, metadata: Optional[dict] = None):
        """Record provenance for audit trail."""
        if not self.agent_id:
            return
        try:
            from contextsynapse.context.agents import ProvenanceRecord
            from contextsynapse.context.store_factory import create_agent_registry
            registry = create_agent_registry()
            registry.record_provenance(ProvenanceRecord(
                session_id=self.conn.namespace if self.conn else "",
                agent_id=self.agent_id,
                operation=operation,
                target_type=target_type,
                target_id=target_id,
                metadata=metadata or {},
            ))
        except Exception as e:
            logger.debug("Provenance recording failed: %s", e)

    def thread_log(self, content: str, action: str = ""):
        """Log to session thread (if configured)."""
        if not self.thread_id:
            return
        try:
            from contextsynapse.context.conversation import ConversationStore
            store = ConversationStore()
            store.append_message(
                conversation_id=self.thread_id,
                role="assistant",
                content=content,
                agent_id=self.agent_id,
                metadata={"type": "agent_action", "action": action, "agent_name": self.agent_name},
            )
        except Exception as e:
            logger.debug("Thread log failed: %s", e)


class ToolRegistry:
    """Central registry of all tools. Class-level singleton."""

    _tools: Dict[str, ToolDef] = {}

    @classmethod
    def register(cls, tool_def: ToolDef):
        """Register a tool definition."""
        cls._tools[tool_def.name] = tool_def

    @classmethod
    def get(cls, name: str) -> Optional[ToolDef]:
        """Get a tool by name."""
        return cls._tools.get(name)

    @classmethod
    def all(cls) -> List[ToolDef]:
        """Get all registered tools."""
        return list(cls._tools.values())

    @classmethod
    def by_category(cls, *categories: str) -> List[ToolDef]:
        """Get tools filtered by category."""
        return [t for t in cls._tools.values() if t.category in categories]

    @classmethod
    def names(cls) -> List[str]:
        """Get all tool names."""
        return list(cls._tools.keys())

    @classmethod
    def dispatch(cls, name: str, ctx: ToolContext, kwargs: Optional[dict] = None) -> str:
        """Call a tool by name. Routes through the gateway for policy + cost tracking."""
        # Gateway tools bypass the gateway to avoid recursion
        if name in ("report_external", "report_tokens", "my_usage", "boundary_costs", "get_tool_policy"):
            return cls._dispatch_direct(name, ctx, kwargs)

        # Route through gateway for policy, cost tracking, and action emission
        try:
            from contextsynapse.gateway import get_gateway
            return get_gateway().execute(name, ctx, kwargs)
        except ImportError:
            return cls._dispatch_direct(name, ctx, kwargs)

    # Write tools — never get hints (agent is already productive)
    _WRITE_TOOLS = {
        "add_knowledge", "add_relationship", "add_decision", "add_task",
        "complete_task", "handoff_task", "claim_task", "log_action",
        "remember", "ws_write_file", "ws_commit",
    }

    @classmethod
    def _dispatch_direct(cls, name: str, ctx: ToolContext, kwargs: Optional[dict] = None) -> str:
        """Direct dispatch without gateway (used internally by gateway)."""
        tool_def = cls._tools.get(name)
        if not tool_def:
            available = ", ".join(sorted(cls._tools.keys()))
            return f"Error: Unknown tool '{name}'. Available: {available}"
        if not tool_def.handler:
            return f"Error: Tool '{name}' has no handler."
        # Normalize kwargs — LLMs sometimes send a string instead of dict
        if isinstance(kwargs, str):
            kwargs = {"query": kwargs}
        elif not isinstance(kwargs, dict):
            kwargs = {}
        try:
            result = tool_def.handler(ctx, **kwargs)
            # Record action for context quality tracking
            try:
                from contextsynapse.context.quality_tracker import get_quality_tracker
                agent_id = getattr(ctx, "agent_id", "") or ""
                if agent_id:
                    get_quality_tracker().record_agent_action(agent_id, name, kwargs or {})
            except Exception:
                pass

            # Track hint state
            ctx._steps_taken = getattr(ctx, "_steps_taken", 0) + 1
            if name in cls._WRITE_TOOLS:
                ctx._writes_made = getattr(ctx, "_writes_made", 0) + 1

            # Append contextual hint
            hint = cls._get_hint(ctx, name)
            if hint:
                result = f"{result}\n\n> {hint}"

            # Auto-briefing: inject orient on first tool call
            if not getattr(ctx, '_has_oriented', False) and name not in ('orient', 'briefing'):
                try:
                    from .graph import _orient
                    briefing = _orient(ctx)
                    if briefing and len(briefing) > 50:
                        result = f"═══ BRIEFING ═══\n{briefing}\n═════════════════\n\n{result}"
                except Exception:
                    pass  # never break the tool call

            # Pending updates: inject propagation events from other agents
            try:
                from ..context.propagation import get_propagator
                ns = getattr(ctx.conn, '_namespace', '') if ctx.conn else ''
                agent_id = getattr(ctx, 'agent_id', '') or ''
                if ns and agent_id:
                    updates = get_propagator().get_pending(agent_id, ns)
                    if updates:
                        update_lines = "\n".join(f"  • {u}" for u in updates)
                        result = f"{result}\n\n> Updates from other agents:\n{update_lines}"
            except Exception:
                pass  # never break the tool call

            return result
        except Exception as e:
            logger.error("Tool %s failed: %s", name, e)
            return f"Error executing {name}: {e}"

    @classmethod
    def _get_hint(cls, ctx: ToolContext, tool_name: str) -> str:
        """Return a 1-line contextual hint based on agent state, or empty string."""
        if not getattr(ctx, "_hints_enabled", True):
            return ""
        if getattr(ctx, "_steps_taken", 0) > 5:
            return ""
        if tool_name in cls._WRITE_TOOLS or tool_name == "orient":
            return ""

        steps = getattr(ctx, "_steps_taken", 0)
        writes = getattr(ctx, "_writes_made", 0)

        # First interaction — auto-briefing handles orientation
        if steps <= 1:
            return ""

        # Multiple searches, no writes — nudge to write
        if steps >= 3 and writes == 0:
            return 'You have data. Write your analysis: add_knowledge(content="...", node_type="Finding")'

        # Has tasks but doing unrelated work
        try:
            if ctx.project and steps >= 2:
                tasks = ctx.project.get_open_tasks(agent_id=ctx.agent_id, agent_name=ctx.agent_name)
                if tasks:
                    t = tasks[0]
                    return f"Reminder: You have an open task: \"{t['title']}\" (id: {t['id']})"
        except Exception:
            pass

        return ""

    @classmethod
    def to_openai_schemas(cls, categories: Optional[List[str]] = None) -> List[dict]:
        """Export tools as OpenAI function-calling schemas."""
        tools = cls.by_category(*categories) if categories else cls.all()
        return [t.to_openai_schema() for t in tools]

    @classmethod
    def to_json_schemas(cls, categories: Optional[List[str]] = None) -> List[dict]:
        """Export tools as framework-agnostic JSON Schemas."""
        tools = cls.by_category(*categories) if categories else cls.all()
        return [t.to_json_schema() for t in tools]

    @classmethod
    def count(cls) -> int:
        return len(cls._tools)

    @classmethod
    def clear(cls):
        """Clear all registered tools (for testing)."""
        cls._tools.clear()


# ── Decorator for convenient registration ──────────────────────────

def tool(
    name: str,
    category: str,
    description: str,
    params: Optional[List[ToolParam]] = None,
    requires_project: bool = False,
    requires_workspace: bool = False,
):
    """Decorator to register a tool handler.

    Usage::

        @tool("query_graph", "graph", "Execute an AIQL query",
              params=[ToolParam("query", "string", "AIQL query")])
        def _query_graph(ctx: ToolContext, query: str) -> str:
            return ctx.conn.query(query)
    """
    def decorator(fn: Callable) -> Callable:
        td = ToolDef(
            name=name,
            description=description,
            category=category,
            params=params or [],
            handler=fn,
            requires_project=requires_project,
            requires_workspace=requires_workspace,
        )
        ToolRegistry.register(td)
        return fn
    return decorator
