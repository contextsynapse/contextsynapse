"""
Agent Worker Router
====================
REST API for remote agents to poll tasks, claim work, report results,
and interact with the shared graph — all authenticated via agent_id:secret.

Mount on the main FastAPI app::

    from contextsynapse.api.agent_worker_router import create_agent_worker_router
    app.include_router(create_agent_worker_router(graph_registry))

Endpoints:
    GET    /agent/sessions                           — List agent's sessions
    GET    /agent/sessions/{sid}/tasks/mine           — Poll assigned tasks
    GET    /agent/sessions/{sid}/tasks/available      — Poll unassigned tasks
    POST   /agent/sessions/{sid}/tasks/{tid}/claim    — Claim a task
    POST   /agent/sessions/{sid}/tasks/{tid}/complete — Report completion
    POST   /agent/sessions/{sid}/tasks/{tid}/handoff  — Hand off to another agent
    POST   /agent/sessions/{sid}/heartbeat            — Status heartbeat
    GET    /agent/sessions/{sid}/workspace/config     — Get workspace config
    POST   /agent/sessions/{sid}/workspace/artifacts  — Upload files (no-git agents)
    POST   /agent/sessions/{sid}/workspace/push-event — Notify of git push
    POST   /agent/sessions/{sid}/graph/nodes          — Add knowledge node
    POST   /agent/sessions/{sid}/graph/query          — Execute AIQL query
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Platform → Tier Mapping ──────────────────────────────────────────

_PLATFORM_TIER: dict[str, str] = {
    "embedded": "instant",
    "mobile": "fast",
    "browser": "fast",
    "desktop": "standard",
    "app": "standard",
}


def _tier_for_platform(platform: str | None, explicit_tier: str | None) -> str:
    """Resolve projection tier: explicit param wins; otherwise use platform default."""
    if explicit_tier is not None:
        return explicit_tier
    return _PLATFORM_TIER.get(platform or "", "standard")


_TIER_BUDGETS: dict[str, dict] = {
    "instant": {"token_budget": 500,  "time_budget_ms": 50},
    "fast":    {"token_budget": 1500, "time_budget_ms": 200},
    "standard":{"token_budget": 3000, "time_budget_ms": 1000},
    "deep":    {"token_budget": 8000, "time_budget_ms": 5000},
}


def _build_platform_hints(platform: str | None, resolved_tier: str, tier_was_explicit: bool) -> dict:
    """Build the platform_hints block for orient_agent responses."""
    budgets = _TIER_BUDGETS.get(resolved_tier, _TIER_BUDGETS["standard"])
    if tier_was_explicit:
        note = "Tier explicitly set by caller."
    else:
        note = f"Tier auto-selected for {platform or 'unknown'} platform. Pass ?tier=deep to override."
    return {
        "platform": platform or "unknown",
        "tier": resolved_tier,
        **budgets,
        "note": note,
    }


# ── Request/Response Models ─────────────────────────────────────────

class TaskCompleteRequest(BaseModel):
    summary: str
    files_changed: List[str] = Field(default_factory=list)

class TaskHandoffRequest(BaseModel):
    to_agent: str
    notes: str = ""

class HeartbeatRequest(BaseModel):
    status: str = "idle"  # idle | working | error
    current_task_id: Optional[str] = None
    progress: float = 0.0
    message: str = ""

class ArtifactUploadRequest(BaseModel):
    task_id: Optional[str] = None
    files: List[Dict[str, str]]  # [{"path": "src/app.py", "content": "..."}]
    commit_message: str = "Agent artifact upload"

class TaskCreateRequest(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    assigned_to: str = ""
    tags: str = ""
    depends_on: str = ""
    parent_task: str = ""

class GraphEdgeRequest(BaseModel):
    source: str
    target: str
    label: str
    properties: Dict[str, Any] = Field(default_factory=dict)

class GraphSearchRequest(BaseModel):
    label: str = ""
    query: str = ""
    where: Dict[str, Any] = Field(default_factory=dict)
    limit: int = 10

class PushEventRequest(BaseModel):
    branch: str
    commit_sha: str = ""
    files_changed: List[str] = Field(default_factory=list)
    task_id: Optional[str] = None

class GraphNodeRequest(BaseModel):
    label: str
    properties: Dict[str, Any] = Field(default_factory=dict)

class GraphQueryRequest(BaseModel):
    aiql: str


# ── Module-level stubs for internal helpers (patchable in tests) ─────
# These are overridden inside create_agent_worker_router closures but
# exposed here so unit tests can patch them via their module path.
def _get_session_or_404(session_id: str):  # pragma: no cover
    raise NotImplementedError("call create_agent_worker_router first")

def _get_connection(session):  # pragma: no cover
    raise NotImplementedError("call create_agent_worker_router first")

def _get_runtime_connection(session):  # pragma: no cover
    raise NotImplementedError("call create_agent_worker_router first")

def _get_project_graph(session):  # pragma: no cover
    raise NotImplementedError("call create_agent_worker_router first")


# ── Router Factory ──────────────────────────────────────────────────

def create_agent_worker_router(graph_registry=None) -> APIRouter:
    """Build the /agent router for remote agent workers."""

    router = APIRouter(prefix="/agent", tags=["Agent Worker"])

    from ..context.store_factory import create_agent_registry
    from ..context.session import ContextSessionManager
    from .auth import AgentAuth
    from .events import event_bus

    agent_registry = create_agent_registry()
    session_manager = ContextSessionManager(graph_registry=graph_registry)
    require_agent = AgentAuth(agent_registry)

    def _get_session_or_404(session_id: str):
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, f"Session '{session_id}' not found")
        return session

    def _get_project_graph(session):
        """ProjectGraph targets the runtime namespace (tasks, findings, actions)."""
        from ..project.graph import ProjectGraph
        from ..adapters._base import AIContextDBConnection
        rt_ns = getattr(session, 'runtime_namespace', '') or f"{session.graph_namespace}_rt"
        conn = AIContextDBConnection(namespace=rt_ns, graph_registry=graph_registry)
        return ProjectGraph(rt_ns, connection=conn, agent_registry=agent_registry)

    def _get_connection(session):
        """Connection targets the atomic namespace (source knowledge)."""
        from ..adapters._base import AIContextDBConnection
        return AIContextDBConnection(namespace=session.graph_namespace, graph_registry=graph_registry)

    def _get_runtime_connection(session):
        """Connection to the runtime graph (agent work)."""
        from ..adapters._base import AIContextDBConnection
        rt_ns = getattr(session, 'runtime_namespace', '') or f"{session.graph_namespace}_rt"
        return AIContextDBConnection(namespace=rt_ns, graph_registry=graph_registry)

    # ── Session Discovery ───────────────────────────────────────────

    @router.get("/sessions")
    async def list_my_sessions(agent=Depends(require_agent)):
        """List sessions this agent has access to, with pending task counts."""
        all_sessions = session_manager.list_sessions()
        results = []
        for s in all_sessions:
            if s.status != "active":
                continue
            access_list = session_manager.get_access_list(s.session_id)
            agent_ids = [a["agent_id"] for a in access_list if isinstance(a, dict)]
            if agent.agent_id in agent_ids:
                # Count pending tasks
                try:
                    pg = _get_project_graph(s)
                    open_tasks = pg.get_open_tasks(agent_id=agent.agent_id, agent_name=agent.name)
                    all_open = pg.get_open_tasks()
                    unassigned = [t for t in all_open if t["assigned_to"] in ("unassigned", "")]
                except Exception:
                    open_tasks, unassigned = [], []

                results.append({
                    "session_id": s.session_id,
                    "name": s.name,
                    "status": s.status,
                    "graph_namespace": s.graph_namespace,
                    "my_tasks": len(open_tasks),
                    "unassigned_tasks": len(unassigned),
                    "goal": (s.config or {}).get("goal", ""),
                })
        return {"sessions": results}

    # ── Task Polling ────────────────────────────────────────────────

    @router.get("/sessions/{session_id}/tasks/mine")
    async def my_tasks(
        session_id: str,
        status: str = Query("all", description="open|in_progress|all"),
        agent=Depends(require_agent),
    ):
        """Get tasks assigned to this agent."""
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        tasks = pg.get_open_tasks(agent_id=agent.agent_id, agent_name=agent.name)
        if status != "all":
            tasks = [t for t in tasks if t["status"] == status]
        return {"tasks": tasks, "count": len(tasks)}

    @router.get("/sessions/{session_id}/tasks/available")
    async def available_tasks(
        session_id: str,
        priority: str = Query("", description="Filter by priority"),
        agent=Depends(require_agent),
    ):
        """Get open unassigned tasks available to claim."""
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        all_open = pg.get_open_tasks()
        available = [t for t in all_open if t["assigned_to"] in ("unassigned", "", None)]
        if priority:
            available = [t for t in available if t["priority"] == priority]
        return {"tasks": available, "count": len(available)}

    @router.get("/sessions/{session_id}/tasks/stream")
    async def stream_tasks(session_id: str, agent=Depends(require_agent)):
        """SSE endpoint — push task notifications to agents in real-time.

        Instead of polling /tasks/available every N seconds, agents connect here
        and receive instant notifications when tasks are created or unblocked.
        Falls back: if no events within 30s, sends a heartbeat with pending count.
        """
        import asyncio
        from starlette.responses import StreamingResponse

        session = _get_session_or_404(session_id)

        async def event_generator():
            queue = event_bus.subscribe()
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        if event.get("type") in ("task_available", "task_unblocked", "task_created"):
                            yield f"data: {json.dumps(event)}\n\n"
                    except asyncio.TimeoutError:
                        # Heartbeat with pending task count
                        pg = _get_project_graph(session)
                        open_tasks = pg.get_open_tasks()
                        available = [t for t in open_tasks if t["assigned_to"] in ("unassigned", "", None)]
                        yield f"data: {json.dumps({'type': 'heartbeat', 'available': len(available)})}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                event_bus.unsubscribe(queue)

        return StreamingResponse(event_generator(), media_type="text/event-stream")

    # ── Task Lifecycle ──────────────────────────────────────────────

    @router.post("/sessions/{session_id}/tasks/{task_id}/claim")
    async def claim_task(session_id: str, task_id: str, agent=Depends(require_agent)):
        """Claim an open task."""
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        result = pg.claim_task(task_id, agent.agent_id)
        if result.startswith("Error"):
            raise HTTPException(409, result)
        agent_registry.touch(agent.agent_id)
        return {"status": "claimed", "task_id": task_id, "message": result}

    @router.post("/sessions/{session_id}/tasks/{task_id}/complete")
    async def complete_task(
        session_id: str, task_id: str, req: TaskCompleteRequest, agent=Depends(require_agent),
    ):
        """Report task completion."""
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        result = pg.complete_task(
            task_id, agent.agent_id, summary=req.summary,
            files_changed=req.files_changed or None,
        )
        if result.startswith("Error"):
            raise HTTPException(400, result)
        agent_registry.touch(agent.agent_id)
        return {"status": "completed", "task_id": task_id, "message": result}

    @router.post("/sessions/{session_id}/tasks/{task_id}/handoff")
    async def handoff_task(
        session_id: str, task_id: str, req: TaskHandoffRequest, agent=Depends(require_agent),
    ):
        """Hand off a task to another agent."""
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        result = pg.handoff_task(task_id, from_agent=agent.agent_id, to_agent=req.to_agent, notes=req.notes)
        if result.startswith("Error"):
            raise HTTPException(400, result)
        return {"status": "handed_off", "task_id": task_id, "message": result}

    @router.post("/sessions/{session_id}/tasks")
    async def create_task(session_id: str, req: TaskCreateRequest, agent=Depends(require_agent)):
        """Create a new task (or break a task into subtasks)."""
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        dep_list = [d.strip() for d in req.depends_on.split(",") if d.strip()] if req.depends_on else None
        tag_list = [t.strip() for t in req.tags.split(",") if t.strip()] if req.tags else None
        task_id = pg.add_task(
            title=req.title, description=req.description,
            priority=req.priority, assigned_to=req.assigned_to or None,
            tags=tag_list, depends_on=dep_list,
            parent_task=req.parent_task or None,
            created_by=agent.name,
        )
        return {"task_id": task_id, "title": req.title, "assigned_to": req.assigned_to, "parent_task": req.parent_task}

    @router.get("/sessions/{session_id}/tasks")
    async def list_all_tasks(
        session_id: str,
        status: str = Query("", description="open|in_progress|completed|all"),
        agent=Depends(require_agent),
    ):
        """List all tasks in the session."""
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        tasks = pg.get_open_tasks()
        if status and status != "all":
            tasks = [t for t in tasks if t["status"] == status]
        return {"tasks": tasks, "count": len(tasks)}

    # ── Heartbeat ───────────────────────────────────────────────────

    @router.post("/sessions/{session_id}/heartbeat")
    async def heartbeat(session_id: str, req: HeartbeatRequest, agent=Depends(require_agent)):
        """Agent status heartbeat — updates last_seen, returns pending task count."""
        _get_session_or_404(session_id)
        agent_registry.touch(agent.agent_id)

        # Count pending tasks
        try:
            session = session_manager.get_session(session_id)
            pg = _get_project_graph(session)
            my_tasks = pg.get_open_tasks(agent_id=agent.agent_id, agent_name=agent.name)
            pending = len(my_tasks)
        except Exception:
            pending = 0

        return {
            "ack": True,
            "server_time": datetime.now(timezone.utc).isoformat(),
            "pending_tasks": pending,
            "agent_status": req.status,
        }

    # ── Workspace ───────────────────────────────────────────────────

    @router.get("/sessions/{session_id}/workspace/config")
    async def get_workspace_config(session_id: str, agent=Depends(require_agent)):
        """Get workspace configuration for this session."""
        session = _get_session_or_404(session_id)
        config = (session.config or {}).get("workspace", {"type": "local"})

        # Add computed agent-specific branch name
        branch_pattern = config.get("branch_pattern", "agent/{agent_name}/{task_id}")
        config["agent_branch_prefix"] = f"agent/{agent.name}"

        # Remove sensitive fields for non-admin agents
        safe_config = dict(config)
        if agent.role != "admin" and "admin" not in (agent.capabilities or []):
            safe_config.pop("github_token", None)

        return {"workspace": safe_config, "session_name": session.name}

    @router.post("/sessions/{session_id}/workspace/artifacts")
    async def upload_artifacts(
        session_id: str, req: ArtifactUploadRequest, agent=Depends(require_agent),
    ):
        """Upload files for agents that can't use git directly."""
        session = _get_session_or_404(session_id)
        ws_config = (session.config or {}).get("workspace", {"type": "local", "path": f"generated/{session.name.replace(' ', '-').lower()}"})

        from ..workspace.base import Workspace
        ws = Workspace.from_config(ws_config)

        written = []
        for f in req.files:
            result = ws.write_file(f["path"], f["content"])
            written.append(f["path"])

        # Commit if git workspace
        commit_result = ws.commit(req.commit_message)

        # Record in graph
        conn = _get_connection(session)
        conn.add_node(label="ArtifactUpload", properties={
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "files": ",".join(written),
            "task_id": req.task_id or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        return {"status": "received", "files_written": len(written), "commit": commit_result}

    @router.post("/sessions/{session_id}/workspace/push-event")
    async def push_event(
        session_id: str, req: PushEventRequest, agent=Depends(require_agent),
    ):
        """Notify server that agent pushed to the git remote."""
        session = _get_session_or_404(session_id)
        conn = _get_runtime_connection(session)  # agent action → runtime graph

        # Record push event in graph
        conn.add_node(label="GitPush", properties={
            "agent_id": agent.agent_id,
            "agent_name": agent.name,
            "branch": req.branch,
            "commit_sha": req.commit_sha,
            "files_changed": ",".join(req.files_changed),
            "task_id": req.task_id or "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        agent_registry.touch(agent.agent_id)
        return {"status": "recorded", "branch": req.branch}

    # ── Graph Access ────────────────────────────────────────────────

    @router.post("/sessions/{session_id}/graph/nodes")
    async def add_graph_node(
        session_id: str, req: GraphNodeRequest, agent=Depends(require_agent),
    ):
        """Add a knowledge node to the session's graph."""
        session = _get_session_or_404(session_id)
        # All agent writes go to runtime graph — agents never write directly to atomic.
        # Atomic context is populated only by the ingestion pipeline.
        # Nodes keep their label (Fact, Knowledge, etc.) but live in runtime
        # until explicitly promoted to atomic.
        conn = _get_runtime_connection(session)

        props = dict(req.properties)
        props["_agent_id"] = agent.agent_id
        props["_agent_name"] = agent.name

        # Use AIQL to ensure node goes to the correct namespace
        prop_parts = []
        for k, v in props.items():
            if isinstance(v, str):
                escaped = v.replace('"', '\\"')
                prop_parts.append(f'{k}: "{escaped}"')
            elif isinstance(v, bool):
                prop_parts.append(f'{k}: {"TRUE" if v else "FALSE"}')
            elif isinstance(v, (int, float)):
                prop_parts.append(f'{k}: {v}')
            else:
                escaped = str(v).replace('"', '\\"')
                prop_parts.append(f'{k}: "{escaped}"')
        prop_str = ", ".join(prop_parts)
        result = conn.query(f'CREATE NODE {req.label} {{{prop_str}}}')
        node_id = result.get("data", {}).get("uuid", "")
        return {"node_id": node_id, "label": req.label}

    @router.post("/sessions/{session_id}/graph/query")
    async def query_graph(
        session_id: str, req: GraphQueryRequest, agent=Depends(require_agent),
    ):
        """Execute an AIQL query against the session's graph."""
        session = _get_session_or_404(session_id)
        conn = _get_connection(session)
        result = conn.query(req.aiql)
        return result

    @router.post("/sessions/{session_id}/graph/edges")
    async def add_graph_edge(
        session_id: str, req: GraphEdgeRequest, agent=Depends(require_agent),
    ):
        """Create an edge between two nodes."""
        session = _get_session_or_404(session_id)
        conn = _get_connection(session)
        props = dict(req.properties)
        props["_agent_id"] = agent.agent_id
        edge = conn.add_edge(source=req.source, target=req.target, label=req.label, properties=props)
        return {"edge_id": edge.id, "label": req.label, "source": req.source, "target": req.target}

    @router.post("/sessions/{session_id}/graph/search")
    async def search_graph_nodes(
        session_id: str, req: GraphSearchRequest, agent=Depends(require_agent),
    ):
        """Search nodes by label, query text, and/or property filters."""
        session = _get_session_or_404(session_id)
        conn = _get_connection(session)

        # If query text provided, use search_nodes tool (keyword + semantic)
        if req.query and req.query.strip():
            try:
                from ..tools import ToolRegistry
                from .playground_conn import PlaygroundConn, create_tool_context
                _conn = PlaygroundConn(session.graph_namespace, graph_registry)
                _ctx = create_tool_context(_conn, agent.agent_id, agent.name)
                params = {"query": req.query, "limit": str(req.limit)}
                if req.label:
                    params["label"] = req.label
                result_text = ToolRegistry.dispatch("search_nodes", _ctx, params)
                return {"results": result_text, "query": req.query, "label": req.label}
            except Exception:
                pass  # fallback to AIQL

        # Fallback: AIQL SELECT
        from ..security.sanitize import sanitize_aiql_identifier, build_safe_where_clause
        where_clause = build_safe_where_clause(req.where)
        safe_label = sanitize_aiql_identifier(req.label) if req.label else ""
        query = f"SELECT * FROM {safe_label}{where_clause}" if safe_label else f"SELECT *{where_clause}"
        result = conn.query(query)
        nodes = result.get("nodes", [])
        # Limit results and truncate content to prevent ResponseTooLargeError
        limit = req.limit if hasattr(req, 'limit') and req.limit else 10
        truncated = []
        for n in nodes[:limit]:
            if isinstance(n, dict):
                props = n.get("properties", n)
                truncated.append({
                    "label": n.get("label", n.get("node_type", "?")),
                    "name": (props.get("name", "") or "")[:100],
                    "content": (props.get("content", props.get("statement", "")) or "")[:200],
                })
            else:
                props = n.properties if hasattr(n, 'properties') else {}
                truncated.append({
                    "label": n.label if hasattr(n, 'label') else "?",
                    "name": (props.get("name", "") or "")[:100],
                    "content": (props.get("content", props.get("statement", "")) or "")[:200],
                })
        return {"nodes": truncated, "count": len(nodes), "showing": len(truncated)}

    # ── Agent Activity ─────────────────────────────────────────

    @router.get("/sessions/{session_id}/activity")
    async def agent_session_activity(
        session_id: str, limit: int = Query(50), agent=Depends(require_agent),
    ):
        """Get agent activity for this session — who wrote what, when."""
        session = _get_session_or_404(session_id)
        conn = _get_connection(session)

        # Find all nodes with _agent_id or _agent_name or created_by
        activity = []
        try:
            for label in ["Finding", "Insight", "Decision", "Task", "ToolCall"]:
                nodes = conn.db.get_all_nodes(label=label) if hasattr(conn, 'db') and conn.db else []
                for n in nodes:
                    props = n.properties if hasattr(n, 'properties') else {}
                    agent_name = props.get("_agent_name", props.get("created_by", props.get("source", "")))
                    if agent_name:
                        activity.append({
                            "id": n.id if hasattr(n, 'id') else "",
                            "label": n.label if hasattr(n, 'label') else label,
                            "agent": agent_name,
                            "name": (props.get("name", "") or "")[:100],
                            "content": (props.get("content", props.get("statement", "")) or "")[:200],
                            "created_at": props.get("created_at", ""),
                        })
        except Exception:
            pass

        # Sort by created_at descending
        activity.sort(key=lambda x: x.get("created_at", ""), reverse=True)
        return {"activity": activity[:limit], "total": len(activity)}

    # ── Context Gaps ──────────────────────────────────────────

    @router.get("/sessions/{session_id}/gaps")
    async def get_context_gaps(session_id: str, limit: int = Query(20), agent=Depends(require_agent)):
        """Get top context gaps — what agents searched for but couldn't find."""
        session = _get_session_or_404(session_id)
        from ..intelligence.context_gaps import get_top_gaps, get_gap_stats
        gaps = get_top_gaps(namespace=session.graph_namespace, limit=limit)
        stats = get_gap_stats(namespace=session.graph_namespace)
        return {"gaps": gaps, "stats": stats}

    # ── GPT Action Schema (auto-generated) ──────────────────────

    @router.get("/openapi.json")
    async def agent_openapi_schema(request: Request):
        """Auto-generated OpenAPI schema for GPT Custom Actions.

        ChatGPT can import this directly:
        1. In GPT editor → Actions → Import URL
        2. Paste: https://your-server/agent/openapi.json
        """
        # Build absolute URL from request (works behind ngrok/proxy)
        forwarded_host = request.headers.get("x-forwarded-host", "")
        forwarded_proto = request.headers.get("x-forwarded-proto", "https")
        if forwarded_host:
            host = f"{forwarded_proto}://{forwarded_host}"
        else:
            host = f"{request.url.scheme}://{request.url.netloc}"

        return {
            "openapi": "3.1.0",
            "info": {
                "title": "AIContextDB Agent API",
                "description": "Shared knowledge graph for AI agents. Search, write, dispatch goals.",
                "version": "1.1.0",
            },
            "servers": [{"url": f"{host}/agent" if host else "/agent"}],
            "paths": {
                "/sessions/{session_id}/graph/search": {
                    "post": {
                        "operationId": "searchGraph",
                        "summary": "Search the knowledge graph",
                        "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "description": "Node type filter"},
                                "query": {"type": "string", "description": "Search keywords"},
                                "limit": {"type": "integer", "default": 10},
                            },
                        }}}},
                        "responses": {"200": {"description": "Search results"}},
                    }
                },
                "/sessions/{session_id}/graph/nodes": {
                    "post": {
                        "operationId": "addNode",
                        "summary": "Add a node to the graph",
                        "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {
                                "label": {"type": "string", "default": "Finding"},
                                "properties": {"type": "object", "properties": {
                                    "name": {"type": "string"},
                                    "content": {"type": "string"},
                                }, "required": ["name"]},
                            },
                            "required": ["label", "properties"],
                        }}}},
                        "responses": {"200": {"description": "Created node"}},
                    }
                },
                "/sessions/{session_id}/dispatch": {
                    "post": {
                        "operationId": "dispatchGoal",
                        "summary": "Decompose a goal into tasks and assign to agents",
                        "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"goal": {"type": "string"}},
                            "required": ["goal"],
                        }}}},
                        "responses": {"200": {"description": "Tasks created"}},
                    }
                },
                "/sessions/{session_id}/graph/query": {
                    "post": {
                        "operationId": "queryGraph",
                        "summary": "Execute an AIQL query",
                        "parameters": [{"name": "session_id", "in": "path", "required": True, "schema": {"type": "string"}}],
                        "requestBody": {"required": True, "content": {"application/json": {"schema": {
                            "type": "object",
                            "properties": {"aiql": {"type": "string"}},
                            "required": ["aiql"],
                        }}}},
                        "responses": {"200": {"description": "Query results"}},
                    }
                },
            },
        }

    # ── Goal Dispatch ─────────────────────────────────────────────

    class DispatchGoalRequest(BaseModel):
        goal: str
        session_id: str = ""

    @router.post("/sessions/{session_id}/dispatch")
    async def dispatch_agent_goal(
        session_id: str, req: DispatchGoalRequest, agent=Depends(require_agent),
    ):
        """Decompose a goal into tasks and auto-assign to agents."""
        from ..agents.dispatcher import dispatch_goal as _dispatch
        from ..context.store_factory import create_agent_registry
        from ..context.session import ContextSessionManager

        llm_fn = None
        try:
            from ..llm import get_llm_client
            llm = get_llm_client()
            llm_fn = lambda prompt: llm.generate(prompt=prompt, max_tokens=500)
        except Exception:
            pass

        result = _dispatch(
            goal=req.goal,
            session_id=session_id,
            graph_registry=graph_registry,
            agent_registry=create_agent_registry(),
            session_manager=ContextSessionManager(),
            llm_fn=llm_fn,
        )
        return result

    # ── Agent Context ───────────────────────────────────────────────

    @router.get("/sessions/{session_id}/orient")
    async def orient_agent(
        session_id: str,
        max_tokens: int = Query(2000, description="Max projection budget"),
        max_time_ms: int = Query(1000, description="Max time budget in ms"),
        tier: Optional[str] = Query(None, description="Projection tier: instant|fast|standard|deep. Defaults to platform-appropriate tier."),
        agent=Depends(require_agent),
    ):
        """Contextual Graph Projection — queryless context delivery.

        Returns task-specific projected context from the shared knowledge graph.
        Tier controls pipeline depth: instant (cache only), fast (BM25),
        standard (BM25+Qdrant+hops), deep (all stages+signals+extended).
        """
        session = _get_session_or_404(session_id)
        conn = _get_connection(session)
        rt_conn = _get_runtime_connection(session)
        pg = _get_project_graph(session)

        from ..tools.registry import ToolContext
        ctx = ToolContext(
            conn=conn,
            runtime_conn=rt_conn,
            agent_id=agent.agent_id,
            agent_name=agent.name,
        )
        ctx.project = pg

        tier_was_explicit = tier is not None
        tier = _tier_for_platform(agent.platform, tier)
        hints = _build_platform_hints(agent.platform, tier, tier_was_explicit)

        # Try CGP projection with budget
        try:
            from ..context.projection import project_context
            projection = project_context(ctx, max_tokens=max_tokens,
                                          max_time_ms=max_time_ms, tier=tier)
            if projection:
                return {"orient": projection, "mode": "projection", "agent": agent.name,
                        "tier": tier, "platform_hints": hints}
        except Exception:
            pass

        # Fallback: classic graph summary
        try:
            from ..tools.graph import _orient
            result = _orient(ctx)
            return {"orient": result, "mode": "manifest", "agent": agent.name,
                    "tier": "fallback", "platform_hints": hints}
        except Exception as e:
            return {"orient": f"Graph: {session.graph_namespace}", "mode": "basic",
                    "agent": agent.name, "tier": "fallback", "platform_hints": hints}

    @router.get("/sessions/{session_id}/context")
    async def get_agent_context(
        session_id: str,
        system_prompt: str = Query("", description="Custom system prompt"),
        max_tokens: int = Query(6000, description="Max token budget"),
        agent=Depends(require_agent),
    ):
        """Get full project context for this agent — requirements, tasks, decisions, code produced.

        This is the main way an agent understands what it should do and what's happened so far.
        Returns LLM-ready context as a single prompt string.
        """
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        context = pg.build_agent_context(
            agent_id=agent.agent_id,
            system_prompt=system_prompt,
            max_tokens=max_tokens,
        )
        return {"context": context, "session": session.name, "agent": agent.name}

    # ── Natural Language Query ──────────────────────────────────────

    class AskRequest(BaseModel):
        question: str

    @router.post("/sessions/{session_id}/graph/ask")
    async def ask_graph(session_id: str, req: AskRequest, agent=Depends(require_agent)):
        """Ask about the project in plain English. No query syntax needed.

        Examples: "What are the requirements?", "What has codex done?",
        "Show me the decisions", "What tasks are blocked?"
        """
        session = _get_session_or_404(session_id)
        conn = _get_connection(session)
        pg = _get_project_graph(session)

        from ..search.semantic_query import semantic_search
        result = semantic_search(req.question, conn, pg)
        return result

    # ── Task Context ────────────────────────────────────────────────

    @router.get("/sessions/{session_id}/tasks/{task_id}/context")
    async def get_task_context(session_id: str, task_id: str, agent=Depends(require_agent)):
        """Get everything about a specific task: requirements, decisions, code files, dependencies."""
        session = _get_session_or_404(session_id)
        pg = _get_project_graph(session)
        conn = _get_connection(session)

        node = pg._resolve_task_id(task_id)
        if not node:
            raise HTTPException(404, f"Task {task_id} not found")

        props = node.properties if hasattr(node, "properties") else {}
        tid = node.id if hasattr(node, "id") else task_id

        # Find related nodes via edges
        edges = conn.get_edges()
        related = {"requirements": [], "decisions": [], "code_files": [], "subtasks": [], "depends_on": []}

        for e in edges:
            src = e.source if hasattr(e, "source") else e.get("source", "")
            tgt = e.target if hasattr(e, "target") else e.get("target", "")
            lbl = e.label if hasattr(e, "label") else e.get("label", "")

            if src == tid or tgt == tid:
                other_id = tgt if src == tid else src
                other = conn.get_node(other_id)
                if other:
                    other_label = other.label if hasattr(other, "label") else ""
                    other_props = other.properties if hasattr(other, "properties") else {}
                    info = {"id": other_id, "label": other_label, **other_props}

                    if other_label == "Requirement":
                        related["requirements"].append(info)
                    elif other_label == "Decision":
                        related["decisions"].append(info)
                    elif other_label == "CodeFile":
                        related["code_files"].append(info)
                    elif other_label == "Task" and lbl == "HAS_SUBTASK":
                        related["subtasks"].append(info)
                    elif other_label == "Task" and lbl == "DEPENDS_ON":
                        related["depends_on"].append(info)

        return {
            "task": {"id": tid, "label": "Task", **props},
            **related,
        }

    # ── Promotion ──────────────────────────────────────────────────

    class PromotionConfigRequest(BaseModel):
        auto_promote_threshold: Optional[float] = None
        review_threshold: Optional[float] = None
        require_human_review: Optional[bool] = None
        weights: Optional[Dict[str, float]] = None

    class PromotionRejectRequest(BaseModel):
        reason: str = ""

    @router.get("/sessions/{session_id}/promotion/queue")
    async def promotion_queue(session_id: str, limit: int = Query(50),
                               agent=Depends(require_agent)):
        """Get pending promotion review items."""
        session = _get_session_or_404(session_id)
        from ..context.promotion import PromotionQueue
        queue = PromotionQueue(session.graph_namespace)
        items = queue.pending(limit=limit)
        return {"items": items, "count": queue.count()}

    @router.post("/sessions/{session_id}/promotion/{node_id}/approve")
    async def approve_promotion(session_id: str, node_id: str,
                                 agent=Depends(require_agent)):
        """Approve a node for promotion to atomic context."""
        session = _get_session_or_404(session_id)
        from ..context.promotion import PromotionQueue, promote_node

        queue = PromotionQueue(session.graph_namespace)
        queue.approve(node_id, agent.agent_id)

        rt_conn = _get_runtime_connection(session)
        at_conn = _get_connection(session)
        promoted_id = promote_node(node_id, rt_conn, at_conn,
                                    promoted_by=agent.name)

        if promoted_id:
            return {"promoted": True, "atomic_node_id": promoted_id,
                    "node_id": node_id}
        raise HTTPException(400, f"Failed to promote node {node_id}")

    @router.post("/sessions/{session_id}/promotion/{node_id}/reject")
    async def reject_promotion(session_id: str, node_id: str,
                                req: PromotionRejectRequest,
                                agent=Depends(require_agent)):
        """Reject a node from promotion queue."""
        session = _get_session_or_404(session_id)
        from ..context.promotion import PromotionQueue

        queue = PromotionQueue(session.graph_namespace)
        queue.reject(node_id, agent.agent_id, reason=req.reason)
        return {"rejected": True, "node_id": node_id}

    @router.get("/sessions/{session_id}/promotion/config")
    async def get_promotion_config(session_id: str,
                                    agent=Depends(require_agent)):
        """Get promotion configuration for this session."""
        session = _get_session_or_404(session_id)
        from ..context.promotion import PROMOTION_DEFAULTS
        config = dict(PROMOTION_DEFAULTS)
        session_promo = (session.config or {}).get("promotion", {})
        config.update(session_promo)
        return {"config": config, "session_id": session_id}

    @router.put("/sessions/{session_id}/promotion/config")
    async def update_promotion_config(session_id: str,
                                       req: PromotionConfigRequest,
                                       agent=Depends(require_agent)):
        """Update promotion configuration for this session."""
        session = _get_session_or_404(session_id)
        promo_config = (session.config or {}).get("promotion", {})

        if req.auto_promote_threshold is not None:
            promo_config["auto_promote_threshold"] = req.auto_promote_threshold
        if req.review_threshold is not None:
            promo_config["review_threshold"] = req.review_threshold
        if req.require_human_review is not None:
            promo_config["require_human_review"] = req.require_human_review
        if req.weights is not None:
            promo_config["weights"] = req.weights

        config = dict(session.config or {})
        config["promotion"] = promo_config
        try:
            import sqlite3, json as _json
            conn = sqlite3.connect(str(session_manager.db_path))
            conn.execute(
                "UPDATE context_sessions SET config = ? WHERE session_id = ?",
                (_json.dumps(config), session.session_id),
            )
            conn.commit()
            conn.close()
        except Exception as e:
            raise HTTPException(500, f"Failed to save config: {e}")

        return {"updated": True, "config": promo_config}

    return router
