"""
Boundary Router
================
REST API for atomic contexts and runtime context boundaries.

Contexts:  /contexts/*           — standalone CRUD for typed knowledge graphs
Boundaries: /boundaries/*        — execution scopes that assemble contexts
Agent Work: /boundaries/{id}/*   — tasks, graph, workspace, briefing

Mount::
    from contextsynapse.api.boundary_router import create_boundary_router
    app.include_router(create_boundary_router(graph_registry))
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ── Request/Response Models ─────────────────────────────────────────

class ContextCreateRequest(BaseModel):
    name: str
    type: str  # document | code | rules | knowledge | decision | config
    metadata: Dict[str, Any] = Field(default_factory=dict)

class ContextNodeRequest(BaseModel):
    label: str
    properties: Dict[str, Any] = Field(default_factory=dict)

class ContextIngestRequest(BaseModel):
    nodes: List[Dict[str, Any]]  # [{"label": "Requirement", "properties": {...}}, ...]

class BoundaryCreateRequest(BaseModel):
    name: str
    goal: str = ""
    workspace: Dict[str, Any] = Field(default_factory=dict)
    config: Dict[str, Any] = Field(default_factory=dict)

class AttachContextRequest(BaseModel):
    context_id: str
    role: str = "input"

class GrantAccessRequest(BaseModel):
    agent_id: str
    access_level: str = "write"

class TaskCreateRequest(BaseModel):
    title: str
    description: str = ""
    priority: str = "medium"
    assigned_to: str = ""
    tags: str = ""
    depends_on: str = ""
    parent_task: str = ""

class TaskCompleteRequest(BaseModel):
    summary: str
    files_changed: List[str] = Field(default_factory=list)

class TaskHandoffRequest(BaseModel):
    to_agent: str
    notes: str = ""

class HeartbeatRequest(BaseModel):
    status: str = "idle"
    current_task_id: str = ""
    message: str = ""

class GraphNodeRequest(BaseModel):
    label: str
    properties: Dict[str, Any] = Field(default_factory=dict)

class GraphQueryRequest(BaseModel):
    aiql: str

class AskRequest(BaseModel):
    question: str

class ArtifactUploadRequest(BaseModel):
    task_id: str = ""
    files: List[Dict[str, str]]
    commit_message: str = "Agent upload"


# ── Router Factory ──────────────────────────────────────────────────

def create_boundary_router(graph_registry=None) -> APIRouter:
    """Build the /contexts and /boundaries routers."""

    from ..context.boundary import BoundaryManager
    from ..context.store_factory import create_agent_registry
    from .auth import AgentAuth, require_agent_capability

    agent_registry = create_agent_registry()
    boundary_mgr = BoundaryManager(graph_registry=graph_registry)
    require_agent = AgentAuth(agent_registry)
    require_read = require_agent_capability(require_agent, "read")
    require_write = require_agent_capability(require_agent, "read", "write")
    require_admin = require_agent_capability(require_agent, "read", "write", "admin")

    router = APIRouter(tags=["Contexts & Boundaries"])

    def _get_boundary_or_404(boundary_id: str):
        b = boundary_mgr.get_boundary(boundary_id)
        if not b:
            raise HTTPException(404, f"Boundary '{boundary_id}' not found")
        return b

    def _get_exec_connection(boundary):
        from ..adapters._base import AIContextDBConnection
        return AIContextDBConnection(namespace=boundary.execution_namespace, graph_registry=graph_registry)

    def _get_project_graph(boundary):
        from ..project.graph import ProjectGraph
        conn = _get_exec_connection(boundary)
        return ProjectGraph(boundary.execution_namespace, connection=conn, agent_registry=agent_registry)

    # ================================================================
    # CONTEXTS — standalone CRUD
    # ================================================================

    @router.post("/contexts")
    async def create_context(req: ContextCreateRequest, agent=Depends(require_admin)):
        ctx = boundary_mgr.create_context(name=req.name, type=req.type, metadata=req.metadata)
        return ctx.to_dict()

    @router.get("/contexts")
    async def list_contexts(type: str = Query("", description="Filter by type"), agent=Depends(require_read)):
        contexts = boundary_mgr.list_contexts(type=type or None)
        return {"contexts": [c.to_dict() for c in contexts], "count": len(contexts)}

    @router.get("/contexts/{context_id}")
    async def get_context(context_id: str, agent=Depends(require_read)):
        ctx = boundary_mgr.get_context(context_id)
        if not ctx:
            raise HTTPException(404, f"Context '{context_id}' not found")
        return ctx.to_dict()

    @router.delete("/contexts/{context_id}")
    async def delete_context(context_id: str, agent=Depends(require_admin)):
        ctx = boundary_mgr.get_context(context_id)
        if not ctx:
            raise HTTPException(404, "Context not found")
        # Soft delete
        boundary_mgr._conn.execute("UPDATE atomic_contexts SET status = 'deleted' WHERE context_id = ?", (context_id,))
        boundary_mgr._conn.commit()
        return {"status": "deleted", "context_id": context_id}

    @router.post("/contexts/{context_id}/nodes")
    async def add_context_node(context_id: str, req: ContextNodeRequest, agent=Depends(require_write)):
        """Add a node to an atomic context."""
        ctx = boundary_mgr.get_context(context_id)
        if not ctx:
            raise HTTPException(404, "Context not found")
        from ..adapters._base import AIContextDBConnection
        conn = AIContextDBConnection(namespace=ctx.graph_namespace, graph_registry=graph_registry)
        from ..tools.formatting import serialize_props_to_aiql
        props = dict(req.properties)
        props["_agent_id"] = agent.agent_id
        prop_str = serialize_props_to_aiql(props)
        result = conn.query(f'CREATE NODE {req.label} {{{prop_str}}}')
        node_id = result.get("data", {}).get("uuid", "")
        return {"node_id": node_id, "label": req.label, "context_id": context_id}

    @router.get("/contexts/{context_id}/nodes")
    async def list_context_nodes(context_id: str, label: str = Query("", description="Filter by label"), agent=Depends(require_read)):
        ctx = boundary_mgr.get_context(context_id)
        if not ctx:
            raise HTTPException(404, "Context not found")
        from ..adapters._base import AIContextDBConnection
        conn = AIContextDBConnection(namespace=ctx.graph_namespace, graph_registry=graph_registry)
        if label:
            result = conn.query(f"SELECT * FROM {label}")
            nodes = result.get("nodes", [])
        else:
            # SELECT * without label is broken in AIQL — query by context type
            _TYPE_TO_LABELS = {
                "document": ["Requirement", "Document", "TextChunk"],
                "code": ["CodeFile", "Module", "ProjectSpec"],
                "rules": ["Rule", "Constraint"],
                "knowledge": ["Finding", "Entity", "Fact", "Knowledge"],
                "decision": ["Decision"],
            }
            labels_to_try = _TYPE_TO_LABELS.get(ctx.type, [ctx.type.capitalize()])
            nodes = []
            for lbl in labels_to_try:
                result = conn.query(f"SELECT * FROM {lbl}")
                nodes.extend(result.get("nodes", []))
            # Also try generic labels
            if not nodes:
                for lbl in ["Requirement", "Document", "Rule", "Finding", "Decision", "Task", "CodeFile"]:
                    result = conn.query(f"SELECT * FROM {lbl}")
                    nodes.extend(result.get("nodes", []))
        return {"nodes": nodes, "count": len(nodes)}

    @router.post("/contexts/{context_id}/ingest")
    async def ingest_to_context(context_id: str, req: ContextIngestRequest, agent=Depends(require_write)):
        """Bulk ingest nodes into an atomic context."""
        ctx = boundary_mgr.get_context(context_id)
        if not ctx:
            raise HTTPException(404, "Context not found")
        from ..adapters._base import AIContextDBConnection
        from ..tools.formatting import serialize_props_to_aiql
        conn = AIContextDBConnection(namespace=ctx.graph_namespace, graph_registry=graph_registry)
        created = 0
        for node_data in req.nodes:
            label = node_data.get("label", ctx.type.capitalize())
            props = node_data.get("properties", {})
            props["_agent_id"] = agent.agent_id
            prop_str = serialize_props_to_aiql(props)
            conn.query(f'CREATE NODE {label} {{{prop_str}}}')
            created += 1
        return {"created": created, "context_id": context_id}

    # ================================================================
    # BOUNDARIES — execution scope CRUD
    # ================================================================

    @router.post("/boundaries")
    async def create_boundary(req: BoundaryCreateRequest, agent=Depends(require_admin)):
        boundary = boundary_mgr.create_boundary(
            name=req.name, goal=req.goal,
            workspace_config=req.workspace, config=req.config,
        )
        # Auto-grant creator access
        boundary_mgr.grant_access(boundary.boundary_id, agent.agent_id, "admin")
        return boundary.to_dict()

    @router.get("/boundaries")
    async def list_boundaries(status: str = Query("active"), agent=Depends(require_read)):
        boundaries = boundary_mgr.list_boundaries(status=status)
        # Filter to ones this agent has access to
        results = []
        for b in boundaries:
            access = boundary_mgr.get_access_list(b.boundary_id)
            agent_ids = [a["agent_id"] for a in access]
            if agent.agent_id in agent_ids or agent.role == "admin":
                b.attached_contexts = boundary_mgr.list_attached_contexts(b.boundary_id)
                results.append(b.to_dict())
        return {"boundaries": results, "count": len(results)}

    @router.get("/boundaries/{boundary_id}")
    async def get_boundary(boundary_id: str, agent=Depends(require_read)):
        b = _get_boundary_or_404(boundary_id)
        return b.to_dict()

    @router.delete("/boundaries/{boundary_id}")
    async def delete_boundary(boundary_id: str, agent=Depends(require_admin)):
        _get_boundary_or_404(boundary_id)
        boundary_mgr._conn.execute("UPDATE boundaries SET status = 'archived' WHERE boundary_id = ?", (boundary_id,))
        boundary_mgr._conn.commit()
        return {"status": "archived", "boundary_id": boundary_id}

    # ── Context attachment ──────────────────────────────────────────

    @router.post("/boundaries/{boundary_id}/contexts")
    async def attach_context(boundary_id: str, req: AttachContextRequest, agent=Depends(require_admin)):
        _get_boundary_or_404(boundary_id)
        ctx = boundary_mgr.get_context(req.context_id)
        if not ctx:
            raise HTTPException(404, f"Context '{req.context_id}' not found")
        boundary_mgr.attach_context(boundary_id, req.context_id, role=req.role)
        return {"status": "attached", "context_id": req.context_id, "role": req.role}

    @router.delete("/boundaries/{boundary_id}/contexts/{context_id}")
    async def detach_context(boundary_id: str, context_id: str, agent=Depends(require_admin)):
        boundary_mgr.detach_context(boundary_id, context_id)
        return {"status": "detached"}

    @router.get("/boundaries/{boundary_id}/contexts")
    async def list_attached_contexts(boundary_id: str, agent=Depends(require_read)):
        _get_boundary_or_404(boundary_id)
        contexts = boundary_mgr.list_attached_contexts(boundary_id)
        return {"contexts": contexts, "count": len(contexts)}

    # ── Agent access ────────────────────────────────────────────────

    @router.post("/boundaries/{boundary_id}/agents")
    async def grant_agent_access(boundary_id: str, req: GrantAccessRequest, agent=Depends(require_admin)):
        _get_boundary_or_404(boundary_id)
        boundary_mgr.grant_access(boundary_id, req.agent_id, req.access_level)
        return {"status": "granted", "agent_id": req.agent_id, "access_level": req.access_level}

    @router.delete("/boundaries/{boundary_id}/agents/{agent_id}")
    async def revoke_agent_access(boundary_id: str, agent_id: str, agent=Depends(require_admin)):
        boundary_mgr.revoke_access(boundary_id, agent_id)
        return {"status": "revoked"}

    @router.get("/boundaries/{boundary_id}/agents")
    async def list_boundary_agents(boundary_id: str, agent=Depends(require_read)):
        _get_boundary_or_404(boundary_id)
        agents = boundary_mgr.get_access_list(boundary_id)
        return {"agents": agents, "count": len(agents)}

    # ================================================================
    # AGENT WORK — briefing, tasks, graph, workspace
    # ================================================================

    @router.get("/boundaries/{boundary_id}/briefing")
    async def get_briefing(boundary_id: str, max_tokens: int = Query(6000), agent=Depends(require_read)):
        """Get full project briefing — all contexts + execution state."""
        _get_boundary_or_404(boundary_id)
        briefing = boundary_mgr.build_briefing(boundary_id, agent_id=agent.agent_id, agent_name=agent.name, max_tokens=max_tokens)
        return {"briefing": briefing, "boundary_id": boundary_id, "agent": agent.name}

    # ── Tasks ───────────────────────────────────────────────────────

    @router.get("/boundaries/{boundary_id}/tasks/mine")
    async def my_tasks(boundary_id: str, agent=Depends(require_read)):
        boundary = _get_boundary_or_404(boundary_id)
        pg = _get_project_graph(boundary)
        tasks = pg.get_open_tasks(agent_id=agent.agent_id, agent_name=agent.name)
        return {"tasks": tasks, "count": len(tasks)}

    @router.get("/boundaries/{boundary_id}/tasks/available")
    async def available_tasks(boundary_id: str, agent=Depends(require_read)):
        boundary = _get_boundary_or_404(boundary_id)
        pg = _get_project_graph(boundary)
        all_open = pg.get_open_tasks()
        available = [t for t in all_open if t["assigned_to"] in ("unassigned", "", None)]
        return {"tasks": available, "count": len(available)}

    @router.post("/boundaries/{boundary_id}/tasks")
    async def create_task(boundary_id: str, req: TaskCreateRequest, agent=Depends(require_write)):
        boundary = _get_boundary_or_404(boundary_id)
        pg = _get_project_graph(boundary)
        dep_list = [d.strip() for d in req.depends_on.split(",") if d.strip()] if req.depends_on else None
        tag_list = [t.strip() for t in req.tags.split(",") if t.strip()] if req.tags else None
        task_id = pg.add_task(
            title=req.title, description=req.description, priority=req.priority,
            assigned_to=req.assigned_to or None, tags=tag_list, depends_on=dep_list,
            parent_task=req.parent_task or None, created_by=agent.name,
        )
        return {"task_id": task_id, "title": req.title}

    @router.get("/boundaries/{boundary_id}/tasks")
    async def list_tasks(boundary_id: str, status: str = Query(""), agent=Depends(require_read)):
        boundary = _get_boundary_or_404(boundary_id)
        pg = _get_project_graph(boundary)
        tasks = pg.get_open_tasks()
        if status and status != "all":
            tasks = [t for t in tasks if t["status"] == status]
        return {"tasks": tasks, "count": len(tasks)}

    @router.post("/boundaries/{boundary_id}/tasks/{task_id}/claim")
    async def claim_task(boundary_id: str, task_id: str, agent=Depends(require_write)):
        boundary = _get_boundary_or_404(boundary_id)
        pg = _get_project_graph(boundary)
        result = pg.claim_task(task_id, agent.agent_id)
        if result.startswith("Error"):
            raise HTTPException(409, result)
        return {"status": "claimed", "message": result}

    @router.post("/boundaries/{boundary_id}/tasks/{task_id}/complete")
    async def complete_task(boundary_id: str, task_id: str, req: TaskCompleteRequest, agent=Depends(require_write)):
        boundary = _get_boundary_or_404(boundary_id)
        pg = _get_project_graph(boundary)
        result = pg.complete_task(task_id, agent.agent_id, summary=req.summary, files_changed=req.files_changed or None)
        if result.startswith("Error"):
            raise HTTPException(400, result)
        return {"status": "completed", "message": result}

    @router.post("/boundaries/{boundary_id}/tasks/{task_id}/handoff")
    async def handoff_task(boundary_id: str, task_id: str, req: TaskHandoffRequest, agent=Depends(require_write)):
        boundary = _get_boundary_or_404(boundary_id)
        pg = _get_project_graph(boundary)
        result = pg.handoff_task(task_id, from_agent=agent.agent_id, to_agent=req.to_agent, notes=req.notes)
        if result.startswith("Error"):
            raise HTTPException(400, result)
        return {"status": "handed_off", "message": result}

    # ── Heartbeat ───────────────────────────────────────────────────

    @router.post("/boundaries/{boundary_id}/heartbeat")
    async def heartbeat(boundary_id: str, req: HeartbeatRequest, agent=Depends(require_read)):
        _get_boundary_or_404(boundary_id)
        agent_registry.touch(agent.agent_id)
        from datetime import datetime, timezone
        return {"ack": True, "server_time": datetime.now(timezone.utc).isoformat()}

    # ── Graph (execution graph) ─────────────────────────────────────

    @router.post("/boundaries/{boundary_id}/graph/nodes")
    async def add_graph_node(boundary_id: str, req: GraphNodeRequest, agent=Depends(require_write)):
        boundary = _get_boundary_or_404(boundary_id)
        conn = _get_exec_connection(boundary)
        from ..tools.formatting import serialize_props_to_aiql
        props = dict(req.properties)
        props["_agent_id"] = agent.agent_id
        props["_agent_name"] = agent.name
        prop_str = serialize_props_to_aiql(props)
        result = conn.query(f'CREATE NODE {req.label} {{{prop_str}}}')
        node_id = result.get("data", {}).get("uuid", "")
        return {"node_id": node_id, "label": req.label}

    @router.post("/boundaries/{boundary_id}/graph/query")
    async def query_graph(boundary_id: str, req: GraphQueryRequest, agent=Depends(require_read)):
        boundary = _get_boundary_or_404(boundary_id)
        conn = _get_exec_connection(boundary)
        return conn.query(req.aiql)

    @router.post("/boundaries/{boundary_id}/graph/ask")
    async def ask_graph(boundary_id: str, req: AskRequest, agent=Depends(require_read)):
        """Natural language query across all attached contexts + execution graph."""
        boundary = _get_boundary_or_404(boundary_id)
        conn = _get_exec_connection(boundary)
        pg = _get_project_graph(boundary)
        from ..search.semantic_query import semantic_search
        return semantic_search(req.question, conn, pg)

    @router.post("/boundaries/{boundary_id}/graph/search")
    async def search_graph(boundary_id: str, label: str = "", where: str = "{}", agent=Depends(require_read)):
        boundary = _get_boundary_or_404(boundary_id)
        conn = _get_exec_connection(boundary)
        import json as _json
        from ..security.sanitize import sanitize_aiql_identifier, build_safe_where_clause
        where_dict = _json.loads(where) if where and where != "{}" else None
        where_clause = build_safe_where_clause(where_dict)
        safe_label = sanitize_aiql_identifier(label) if label else ""
        query = f"SELECT * FROM {safe_label}{where_clause}" if safe_label else f"SELECT *{where_clause}"
        result = conn.query(query)
        return {"nodes": result.get("nodes", []), "count": len(result.get("nodes", []))}

    # ── Workspace ───────────────────────────────────────────────────

    @router.get("/boundaries/{boundary_id}/workspace/config")
    async def get_workspace_config(boundary_id: str, agent=Depends(require_read)):
        boundary = _get_boundary_or_404(boundary_id)
        return {"workspace": boundary.workspace_config, "boundary_name": boundary.name}

    @router.post("/boundaries/{boundary_id}/workspace/artifacts")
    async def upload_artifacts(boundary_id: str, req: ArtifactUploadRequest, agent=Depends(require_write)):
        boundary = _get_boundary_or_404(boundary_id)
        ws_config = boundary.workspace_config or {"type": "local", "path": f"generated/{boundary.name.lower().replace(' ', '-')}"}
        from ..workspace.base import Workspace
        ws = Workspace.from_config(ws_config)
        written = []
        for f in req.files:
            ws.write_file(f["path"], f["content"])
            written.append(f["path"])
        ws.commit(req.commit_message)
        return {"files_written": len(written), "commit_message": req.commit_message}

    return router
