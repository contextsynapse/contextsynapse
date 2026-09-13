"""
MCP Session Context — per-connection session scoping via ContextVar.

When the MCP server handles multiple sessions (SSE/HTTP transport),
each connection gets its own SessionScope via a ContextVar. Tool handlers
read the current scope instead of module globals.

For stdio (single-session, Claude Desktop), the ContextVar is None
and tool handlers fall back to module globals for backward compatibility.
"""

from __future__ import annotations

import logging
import threading
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass
class SessionScope:
    """Per-connection session context. Created by auth middleware."""

    session_id: str
    graph_namespace: str
    agent_id: str
    agent_name: str
    runtime_namespace: str = ""   # separate graph for agent work (tasks, findings, actions)
    access_level: str = "read"  # read | write | admin
    thread_id: str = ""

    # Lazily initialized — set by connection pool
    conn: Any = None              # AIContextDBConnection
    runtime_conn: Any = None      # AIContextDBConnection for runtime graph
    tool_context: Any = None      # ToolContext
    fan_out_graphs: list = field(default_factory=list)  # [(ns, db), ...]

    def has_write_access(self) -> bool:
        return self.access_level in ("write", "admin")

    def has_admin_access(self) -> bool:
        return self.access_level == "admin"


# ContextVar for per-request session scope
# None = stdio single-session mode (use module globals)
_current_session: ContextVar[Optional[SessionScope]] = ContextVar(
    "mcp_session", default=None
)


def get_session_scope() -> Optional[SessionScope]:
    """Get the current session scope. Returns None in stdio mode."""
    return _current_session.get()


def set_session_scope(scope: SessionScope) -> None:
    """Set the session scope for the current async context."""
    _current_session.set(scope)


def clear_session_scope() -> None:
    """Clear the session scope (e.g., on connection close)."""
    _current_session.set(None)
