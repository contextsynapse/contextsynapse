"""
MCP Auth Middleware — authenticates agents and sets session scope per connection.

Starlette ASGI middleware that intercepts MCP SSE/streamable-http connections:
1. Extracts session_id from URL path /mcp/sessions/{session_id}/*
2. Extracts Authorization: Bearer agent_id:secret from headers
3. Resolves session → graph namespace
4. Checks agent's access level for the session
5. Sets SessionScope in ContextVar for tool handlers

For unauthorized requests, returns 401/403 before the SSE stream opens.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any, Optional

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .session_context import SessionScope, set_session_scope, clear_session_scope

logger = logging.getLogger(__name__)

# URL pattern: /sessions/{session_id}/...
_SESSION_PATTERN = re.compile(r"/sessions/([a-zA-Z0-9_-]+)")


class MCPAuthMiddleware:
    """ASGI middleware for MCP multi-session authentication."""

    def __init__(self, app: ASGIApp, graph_registry=None):
        self.app = app
        self.graph_registry = graph_registry
        self._session_manager = None
        self._agent_registry = None

    def _get_session_manager(self):
        if self._session_manager is None:
            try:
                from ..context.session import ContextSessionManager
                from ..core.registry import GraphRegistry
                self._session_manager = ContextSessionManager(
                    graph_registry=self.graph_registry or GraphRegistry()
                )
            except Exception:
                pass
        return self._session_manager

    def _get_agent_registry(self):
        if self._agent_registry is None:
            try:
                from ..context.store_factory import create_agent_registry
                self._agent_registry = create_agent_registry()
            except Exception:
                pass
        return self._agent_registry

    async def __call__(self, scope: Scope, receive: Receive, send: Send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")

        # Extract session_id from URL
        match = _SESSION_PATTERN.search(path)
        if not match:
            # No session in URL — pass through (might be a health check or static)
            await self.app(scope, receive, send)
            return

        session_id = match.group(1)

        # Extract auth from headers
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode("utf-8", errors="ignore")

        if not auth_header.startswith("Bearer "):
            response = JSONResponse(
                {"error": "Authentication required", "detail": "Authorization: Bearer agent_id:secret"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        api_key = auth_header[7:]  # Strip "Bearer "

        if ":" not in api_key:
            response = JSONResponse(
                {"error": "Invalid API key format", "detail": "Expected: agent_id:secret"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        agent_id, secret = api_key.split(":", 1)

        # Authenticate agent
        registry = self._get_agent_registry()
        if not registry:
            response = JSONResponse(
                {"error": "Agent registry unavailable"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        if not registry.authenticate(agent_id, secret):
            response = JSONResponse(
                {"error": "Invalid or expired API key"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        agent = registry.get(agent_id)
        if not agent:
            response = JSONResponse(
                {"error": "Agent not found"},
                status_code=403,
            )
            await response(scope, receive, send)
            return

        # Resolve session
        sm = self._get_session_manager()
        if not sm:
            response = JSONResponse(
                {"error": "Session manager unavailable"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        session = sm.get_session(session_id)
        if not session:
            response = JSONResponse(
                {"error": f"Session '{session_id}' not found"},
                status_code=404,
            )
            await response(scope, receive, send)
            return

        # Check access level
        access_level = "read"
        try:
            access = sm.get_agent_access(session_id, agent_id)
            if access:
                access_level = access.get("access_level", "read")
            else:
                # Auto-grant based on agent capabilities
                if "write" in (agent.capabilities or []):
                    access_level = "write"
                if "admin" in (agent.capabilities or []):
                    access_level = "admin"
        except Exception:
            pass

        # Build session scope
        # runtime_namespace holds agent work (tasks, findings, actions)
        # Falls back to graph_namespace_rt for old sessions without runtime_namespace
        rt_ns = getattr(session, 'runtime_namespace', '') or f"{session.graph_namespace}_rt"
        session_scope = SessionScope(
            session_id=session_id,
            graph_namespace=session.graph_namespace,
            runtime_namespace=rt_ns,
            agent_id=agent_id,
            agent_name=agent.name,
            access_level=access_level,
            thread_id=session.config.get("thread_id", ""),
        )

        # Attach fan-out graphs
        try:
            attached = sm.get_attached_contexts(session_id)
            reg = self.graph_registry
            if reg and attached:
                from ..core.registry import GraphRegistry
                if not reg:
                    reg = GraphRegistry()
                for ctx_ref in attached:
                    ctx_ns = ctx_ref.get("graph_namespace", "")
                    if ctx_ns and ctx_ns != session.graph_namespace:
                        ctx_db = reg.get_graph(ctx_ns, load_if_missing=True)
                        if ctx_db:
                            session_scope.fan_out_graphs.append((ctx_ns, ctx_db))
        except Exception:
            pass

        # Set context var and proceed
        set_session_scope(session_scope)

        # Audit log
        logger.info("[MCP_AUTH] Agent '%s' (%s) connected to session '%s' (graph: %s, access: %s)",
                     agent.name, agent_id, session_id, session.graph_namespace, access_level)

        try:
            # Touch agent last_seen
            try:
                registry.touch(agent_id)
            except Exception:
                pass

            await self.app(scope, receive, send)
        finally:
            clear_session_scope()
