"""
Dashboard Router
================
User-scoped dashboard endpoints.  All routes require ``UserAuth`` JWT.

GET   /dashboard/overview       — Graph count, node count, agent count, usage
GET   /dashboard/graphs         — List graphs in user's tenant
POST  /dashboard/graphs         — Create graph
DELETE /dashboard/graphs/{name} — Delete graph
GET   /dashboard/graphs/{name}/stats — Node/edge counts, types
GET   /dashboard/api-keys       — List tenant API keys (currently one per tenant)
POST  /dashboard/api-keys/rotate — Rotate tenant API key
GET   /dashboard/usage          — Usage metrics for current month
GET   /dashboard/activity       — Recent events
GET   /dashboard/agents         — List registered agents
PATCH /dashboard/settings       — Update user profile
"""

import json as _json
import logging
import os
import os as _os
import re as _re
import tempfile as _tempfile
import time
import uuid as _uuid
import urllib.request as _urllib_request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, File, HTTPException, BackgroundTasks, Query, Request, UploadFile, Form
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)
from .models import paginate
from .events import event_bus
from pydantic import BaseModel, Field

# Lazy cache import — initialized on first use
_cache = None

async def _get_cache():
    global _cache
    if _cache is None:
        from ..cache import create_cache
        _cache = await create_cache()
    return _cache

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreateGraphRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64, description="Graph name")


class UpdateSettingsRequest(BaseModel):
    display_name: Optional[str] = None
    password: Optional[str] = None


class InviteMemberRequest(BaseModel):
    email: str = Field(..., description="Email to invite")
    role: str = Field("contributor", description="Role: admin, contributor, reader")


class UpdateMemberRoleRequest(BaseModel):
    role: str = Field(..., description="New role: admin, contributor, reader")


class CreateSessionRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    agent_id: Optional[str] = Field(None, description="Owner agent ID (optional)")
    context_id: Optional[str] = Field(None, description="Context to attach (auto-creates thread)")
    goal: Optional[str] = Field(None, description="Session goal / objective")
    workspace: Optional[Dict[str, Any]] = Field(None, description="Workspace config: {type, repo_url, branch, token}")
    integrations: Optional[Dict[str, Any]] = Field(None, description="Integrations: {jira: {url, project, token}}")
    workspace_integration_id: Optional[str] = Field(None, description="Reference a live integration for workspace (from Integrations page)")
    jira_integration_id: Optional[str] = Field(None, description="Reference a live Jira integration")


class GrantAccessRequest(BaseModel):
    agent_id: str
    level: str = Field("read", description="read, write, or admin")
    allowed_tags: list = Field(default_factory=list)


class CreateContextRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    context_type: str = Field("knowledge_base")
    description: str = Field("")
    source: str = Field("manual")
    sensitivity: str = Field("public")
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    tags: Optional[list] = None
    graph_namespace: Optional[str] = Field(None, description="Link to an existing graph namespace instead of creating a new one")
    config: Optional[dict] = Field(None, description="Context-specific config (e.g. stack, spec, rules for software_dev)")
    schema_name: Optional[str] = Field(None, description="Bind namespace to a domain schema (e.g. 'knowledge', 'sdlc')")


class UpdateContextRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    sensitivity: Optional[str] = None
    context_type: Optional[str] = None
    config: Optional[dict] = None


class ThreadMessageRequest(BaseModel):
    content: str
    role: str = "human"
    metadata: dict = Field(default_factory=dict)


class ThreadApprovalRequest(BaseModel):
    target_id: str
    feedback: str = ""
    source: Optional[str] = None
    embedding_model: Optional[str] = None
    embedding_dimension: Optional[int] = None
    tags: Optional[list] = None


class AddContextTextRequest(BaseModel):
    content: str = Field(..., min_length=1)
    role: str = Field("user")
    label: Optional[str] = None
    sensitivity: str = Field("public")
    tags: list = Field(default_factory=list)


class AttachContextRequest(BaseModel):
    context_id: str
    role: str = Field("input", description="input or output")


class ContextSearchRequest(BaseModel):
    query: str
    limit: int = Field(20, ge=1, le=100)
    category: Optional[str] = None


class ContextRAGRequest(BaseModel):
    question: str
    limit: int = Field(5, ge=1, le=20)
    model: Optional[str] = None


class RunAgentRequest(BaseModel):
    prompt: str = Field(..., min_length=1, max_length=2000, description="Goal/prompt for the pipeline")
    lead_agent_id: Optional[str] = Field(None, description="Agent ID of the lead. If null, first registered agent is used.")
    max_turns: int = Field(10, ge=1, le=30, description="Max tool-calling rounds per agent")

class JoinRequestBody(BaseModel):
    level: str = "read"
    reason: Optional[str] = None

class ReviewRequestBody(BaseModel):
    level: Optional[str] = None
    allowed_tags: Optional[list] = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_dashboard_router(
    user_registry,
    tenant_registry,
    graph_registry,
    usage_meter,
    agent_registry=None,
    session_manager=None,
    context_manager=None,
    audit_log=None,
) -> APIRouter:
    """Create the /dashboard router with injected dependencies."""

    from .auth import UserAuth, AdminAuth, require_user_role
    import hmac as _hmac
    router = APIRouter(prefix="/dashboard", tags=["dashboard"])
    _user_auth_inner = UserAuth(user_registry)

    # Allow X-Admin-Key header OR admin JWT to bypass user JWT auth.
    async def user_auth(
        request: Request,
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
    ):
        from dataclasses import dataclass, field as _field
        admin_key = _os.environ.get("CONTEXTSYNAPSE_ADMIN_KEY") or os.environ.get("AICONTEXTDB_ADMIN_KEY")

        @dataclass
        class _AdminUser:
            user_id: str = "admin"
            email: str = "admin@local"
            display_name: str = "Admin"
            status: str = "active"
            role: str = "owner"
            is_super_admin: bool = True
            metadata: dict = _field(default_factory=dict)

        # Path 1: X-Admin-Key header
        provided = request.headers.get("X-Admin-Key")
        if admin_key and provided and _hmac.compare_digest(provided, admin_key):
            return _AdminUser()

        # Path 2: Admin JWT (from /admin/login)
        if credentials and admin_key:
            from .auth import verify_jwt
            payload = verify_jwt(credentials.credentials, secret=admin_key)
            if payload and payload.get("role") == "admin":
                return _AdminUser()

        # Path 3: Normal user JWT
        return await _user_auth_inner(request, credentials)

    # RBAC: role-gated dependencies (reader < contributor < admin < owner; super_admin bypasses all)
    require_reader      = require_user_role(user_auth, user_registry, "reader")
    require_contributor = require_user_role(user_auth, user_registry, "contributor")
    require_member      = require_contributor  # backward compat alias
    require_admin       = require_user_role(user_auth, user_registry, "admin")
    require_owner       = require_user_role(user_auth, user_registry, "owner")

    async def require_super_admin(request: Request, user=Depends(user_auth)):
        if not getattr(user, "is_super_admin", False):
            raise HTTPException(403, "Requires super_admin privileges")
        return user

    def _get_tenant(user):
        """Resolve user's primary tenant. Auto-creates default workspace for admin."""
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id and getattr(user, "is_super_admin", False):
            # Admin has no tenant — auto-create default workspace
            existing = tenant_registry.list_tenants()
            if existing:
                tenant_id = existing[0].tenant_id
            else:
                tenant, _ = tenant_registry.create(name="Default Workspace")
                tenant_id = tenant.tenant_id
            # Link admin to this tenant
            try:
                user_registry.link_tenant(user.user_id, tenant_id, "owner")
            except Exception:
                pass  # admin user may not exist in user_registry
        if not tenant_id:
            raise HTTPException(status_code=404, detail="No workspace found. Sign up first.")
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return tenant

    def _record_lineage(session_id: str, entry: dict):
        """Append a lineage entry to the session's config.lineage list."""
        if not session_manager:
            return
        session = session_manager.get_session(session_id)
        if not session:
            return
        cfg = session.config or {}
        lineage = cfg.get("lineage", [])
        entry["timestamp"] = datetime.now(timezone.utc).isoformat()
        lineage.append(entry)
        cfg["lineage"] = lineage
        try:
            session_manager._conn.execute(
                "UPDATE context_sessions SET config = ? WHERE session_id = ?",
                (_json.dumps(cfg), session_id),
            )
            session_manager._conn.commit()
        except Exception:
            pass  # optional

    # ------------------------------------------------------------------
    # Ingestion job tracking (in-memory)
    # ------------------------------------------------------------------
    from ..core.graph_structures import GraphNode, GraphEdge

    _ingest_jobs: dict = {}

    # Ingestion queue — bounded worker pool replaces unlimited BackgroundTasks
    from ..ingestion.queue import get_ingestion_queue
    _ingest_queue = get_ingestion_queue()

    def _submit_ingest(job_id: str, fn, *args):
        """Submit an ingestion job to the bounded queue instead of BackgroundTasks."""
        result = _ingest_queue.submit(job_id, fn, *args)
        if not result.get("accepted"):
            _finish_job(job_id, 0, error=result.get("reason", "Queue full"))
        return result

    # Pipeline system (ingestion scheduler)
    import os as _os_pipe
    from ..pipelines.models import PipelineStore as _PipelineStore
    _sched_pipeline_store = _PipelineStore(redis_url=_os_pipe.environ.get("AICONTEXTDB_REDIS_URL"))

    # Pipeline run store — persists to disk for audit trail
    _pipeline_runs: dict = {}
    _RUNS_DIR = Path("contextcore_data/pipeline_runs")
    _RUNS_DIR.mkdir(parents=True, exist_ok=True)

    # Load existing runs from disk on startup
    for _rf in _RUNS_DIR.glob("*.json"):
        try:
            _run_data = _json.loads(_rf.read_text(encoding="utf-8"))
            _pipeline_runs[_run_data["run_id"]] = _run_data
        except Exception:
            pass  # optional

    def _create_pipeline_run(session_id: str, prompt: str, lead: str, workers: list, version: int) -> str:
        """Create a tracked pipeline run."""
        import secrets as _s
        run_id = _s.token_hex(8)
        _pipeline_runs[run_id] = {
            "run_id": run_id,
            "session_id": session_id,
            "version": version,
            "prompt": prompt,
            "lead": lead,
            "workers": workers,
            "status": "running",
            "events": [],
            "started_at": datetime.now(timezone.utc).isoformat(),
            "completed_at": None,
            "error": None,
        }
        return run_id

    def _record_pipeline_event(run_id: str, event: dict):
        """Append an event to a pipeline run's audit trail."""
        if run_id in _pipeline_runs:
            event["_ts"] = datetime.now(timezone.utc).isoformat()
            _pipeline_runs[run_id]["events"].append(event)

    def _finish_pipeline_run(run_id: str, error: str = None):
        """Mark a pipeline run as completed or failed."""
        if run_id in _pipeline_runs:
            _pipeline_runs[run_id]["status"] = "failed" if error else "completed"
            _pipeline_runs[run_id]["completed_at"] = datetime.now(timezone.utc).isoformat()
            if error:
                _pipeline_runs[run_id]["error"] = error
            # Persist to disk
            try:
                run_file = _RUNS_DIR / f"{run_id}.json"
                run_file.write_text(_json.dumps(_pipeline_runs[run_id], default=str), encoding="utf-8")
            except Exception:
                pass  # optional

    def _create_job(source: str, graph: str, llm_model: str = None,
                    embedding_model: str = None, context_id: str = None,
                    tenant_id: str = None, stage_names: list = None) -> str:
        job_id = str(_uuid.uuid4())[:8]
        # Auto-insert DEDUP after CHUNK if missing
        if stage_names and "CHUNK" in stage_names and "DEDUP" not in stage_names:
            idx = stage_names.index("CHUNK") + 1
            stage_names = list(stage_names)  # copy to avoid mutating caller
            stage_names.insert(idx, "DEDUP")
        # Build stages dict from actual pipeline stage list
        if stage_names:
            stages = {s: {"status": "pending", "count": 0} for s in stage_names}
        else:
            stages = {}
        now = datetime.now(timezone.utc).isoformat()
        _ingest_jobs[job_id] = {
            "job_id": job_id,
            "status": "processing",
            "source": source,
            "graph": graph,
            "context_id": context_id,
            "tenant_id": tenant_id,
            "created_at": now,
            "nodes_created": 0,
            "edges_created": 0,
            "entities_extracted": 0,
            "error": None,
            "current_stage": stage_names[0] if stage_names else None,
            "llm_model": llm_model,
            "embedding_model": embedding_model,
            "stages": stages,
            "debug": False,
            "log": [],
        }
        # Log + emit event
        stage_str = " → ".join(stage_names) if stage_names else "basic"
        logger.info("[PIPELINE] Job %s submitted: source=%s graph=%s llm=%s stages=[%s]",
                    job_id, source, graph, llm_model or "none", stage_str)
        event_bus.emit("pipeline_submitted", {
            "job_id": job_id, "source": source, "graph": graph,
            "llm_model": llm_model, "stages": stage_names or [],
            "context_id": context_id,
        })
        return job_id

    def _is_job_cancelled(job_id: str) -> bool:
        """Check if a job has been cancelled (e.g. context was deleted)."""
        if job_id in _ingest_jobs:
            return _ingest_jobs[job_id].get("status") == "failed" and "cancelled" in (_ingest_jobs[job_id].get("error") or "").lower()
        return False

    def _update_stage(job_id: str, stage_name: str, status: str, count: int = 0):
        if job_id in _ingest_jobs:
            job = _ingest_jobs[job_id]
            # Auto-register stages not known at creation time (e.g. two-phase file pipeline)
            if stage_name not in job["stages"]:
                job["stages"][stage_name] = {"status": "pending", "count": 0}
            job["stages"][stage_name]["status"] = status
            job["stages"][stage_name]["count"] = count
            job["current_stage"] = stage_name
            # Append to log
            job.setdefault("log", []).append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stage": stage_name,
                "message": f"{stage_name}: {status}" + (f" ({count})" if count else ""),
            })
            event_bus.emit("ingest_job_update", {
                "job_id": job_id, "stage": stage_name,
                "status": status, "count": count,
                "context_id": job.get("context_id"),
                "stages": job["stages"],
            })

    def _finish_job(job_id: str, nodes_created: int, error: str = None,
                    edges_created: int = 0, entities_extracted: int = 0):
        if job_id in _ingest_jobs:
            job = _ingest_jobs[job_id]
            had_llm = bool(job.get("llm_model"))

            if error:
                job["status"] = "failed"
                job["error"] = error
                logger.error("[PIPELINE] Job %s FAILED: %s", job_id, error)
            elif nodes_created == 0 and entities_extracted == 0:
                # Pipeline ran but extracted nothing — warn the user
                job["status"] = "completed_empty"
                job["warning"] = (
                    "Pipeline completed but no entities were extracted. "
                    "Check your LLM model configuration or try a different model."
                    if had_llm else
                    "No entities extracted — LLM model was not configured for this pipeline."
                )
                logger.warning("[PIPELINE] Job %s completed with 0 nodes/entities (source=%s, llm=%s)",
                               job_id, job.get("source"), job.get("llm_model"))
            else:
                job["status"] = "completed"
                logger.info("[PIPELINE] Job %s completed: %d nodes, %d edges, %d entities",
                            job_id, nodes_created, edges_created, entities_extracted)

            job["nodes_created"] = nodes_created
            job["edges_created"] = edges_created
            job.setdefault("log", []).append({
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "stage": "done" if not error else "error",
                "message": error if error else f"Completed: {nodes_created} nodes, {edges_created} edges",
            })
            job["entities_extracted"] = entities_extracted

            # Record metering for successful ingestion
            if not error and usage_meter:
                tid = _ingest_jobs[job_id].get("tenant_id")
                if tid:
                    try:
                        if nodes_created > 0:
                            usage_meter.record(tid, "node_create", nodes_created)
                        if edges_created > 0:
                            usage_meter.record(tid, "edge_create", edges_created)
                        usage_meter.record(tid, "ingest")
                    except Exception:
                        pass  # optional
            # Auto-refresh context stats after successful ingestion
            ctx_id = _ingest_jobs[job_id].get("context_id")
            if ctx_id and not error and context_manager:
                try:
                    context_manager.refresh_stats(ctx_id)
                except Exception as _rs_err:
                    logging.getLogger(__name__).warning("refresh_stats failed for %s: %s", ctx_id, _rs_err)

                # Invalidate graph stats cache so next request gets fresh data
                graph_name = _ingest_jobs[job_id].get("graph", "")
                if graph_name:
                    _graph_stats_cache.pop(graph_name, None)
                    # Also try scoped variant
                    for k in list(_graph_stats_cache.keys()):
                        if graph_name in k:
                            _graph_stats_cache.pop(k, None)

                # Reactive sync: propagate new nodes to all attached boundaries
                if session_manager and nodes_created > 0:
                    try:
                        import threading
                        def _do_sync():
                            synced = session_manager.sync_context_to_boundaries(
                                ctx_id, context_manager=context_manager
                            )
                            if synced > 0:
                                try:
                                    from .events import event_bus
                                    event_bus.emit("context_updated", {
                                        "context_id": ctx_id,
                                        "nodes_synced": synced,
                                    })
                                except Exception:
                                    pass  # optional
                        threading.Thread(target=_do_sync, daemon=True).start()
                    except Exception:
                        pass  # optional

                # Store pipeline run as a node in the context's graph
                try:
                    ctx_obj = context_manager.get_context(ctx_id)
                    if ctx_obj and graph_registry:
                        from ..core.graph_structures import GraphNode, GraphEdge
                        import uuid as _p_uuid
                        graph = graph_registry.get_graph(ctx_obj.graph_namespace)
                        if graph:
                            job = _ingest_jobs[job_id]
                            run_id = str(_p_uuid.uuid4())
                            # Score graph quality after ingestion
                            quality = {}
                            try:
                                from ..context.quality import score_graph
                                quality = score_graph(graph)
                                job["quality"] = quality
                            except Exception:
                                pass

                            graph.add_node(GraphNode(
                                id=run_id,
                                label="PipelineRun",
                                properties={
                                    "job_id": job_id,
                                    "source": job.get("source", ""),
                                    "llm_model": job.get("llm_model", ""),
                                    "embedding_model": job.get("embedding_model", ""),
                                    "nodes_created": nodes_created,
                                    "edges_created": edges_created,
                                    "entities_extracted": entities_extracted,
                                    "quality_score": quality.get("overall", 0),
                                    "quality_grade": quality.get("grade", ""),
                                    "status": "completed",
                                    "stages": _json.dumps(job.get("stages", {})),
                                    "completed_at": datetime.now(timezone.utc).isoformat(),
                                },
                            ), write_through=True)
                            # Link: Context ──HAS_PIPELINE_RUN──► PipelineRun
                            graph.add_edge(GraphEdge(
                                id=str(_p_uuid.uuid4()),
                                source=ctx_id, target=run_id,
                                label="HAS_PIPELINE_RUN",
                                properties={},
                            ))
                except Exception:
                    pass  # optional
            # Auto-rebuild fulltext index for the ingested graph
            if not error and nodes_created > 0:
                try:
                    import threading
                    graph_name = _ingest_jobs[job_id].get("graph", "")
                    def _rebuild_ft():
                        try:
                            from ..search.fulltext import fulltext_index
                            db = graph_registry.get_graph(graph_name) if graph_name else None
                            if db:
                                ft_nodes = []
                                for n in db.get_all_nodes():
                                    ft_nodes.append({
                                        "node_id": getattr(n, "id", str(n)),
                                        "label": getattr(n, "label", ""),
                                        "properties": n.properties if hasattr(n, "properties") else {},
                                    })
                                fulltext_index.index_nodes(ft_nodes)
                                logger.info("[FULLTEXT] Indexed %d nodes for %s", len(ft_nodes), graph_name)
                        except Exception as e:
                            logger.debug("Fulltext index update failed: %s", e)
                    threading.Thread(target=_rebuild_ft, daemon=True).start()
                except Exception:
                    pass  # optional

            event_bus.emit("ingest_job_complete", {
                "job_id": job_id,
                "status": "failed" if error else ("completed_empty" if nodes_created == 0 else "completed"),
                "nodes_created": nodes_created,
                "edges_created": edges_created,
                "error": error,
                "context_id": ctx_id,
                "tenant_id": _ingest_jobs[job_id].get("tenant_id"),
                "stages": _ingest_jobs[job_id].get("stages"),
            })

    def _scope_graph(tenant, graph_name: str) -> str:
        """Scope a graph name to the tenant, avoiding double-scoping."""
        prefix = f"{tenant.tenant_id}:"
        if graph_name.startswith(prefix):
            return graph_name
        return f"{prefix}{graph_name.strip() or 'default'}"

    def _resolve_graph(tenant, name: str):
        """Resolve a graph name to an AIContextDB instance."""
        # Try the name directly
        graph = graph_registry.get_graph(name, load_if_missing=True)
        if graph:
            return graph, name
        # Try with old tenant prefix (backward compat)
        scoped = _scope_graph(tenant, name)
        graph = graph_registry.get_graph(scoped, load_if_missing=True)
        if graph:
            return graph, scoped
        # Check metadata (for delete of graphs that can't be loaded)
        for g in graph_registry.list_graphs():
            gname = g.get("name", "") if isinstance(g, dict) else getattr(g, "name", "")
            if gname == name or gname == scoped:
                return None, gname
        return None, name

    def _ensure_graph(scoped: str):
        """Get or create a graph, return the db instance."""
        db = graph_registry.get_graph(scoped)
        if not db:
            graph_registry.create_graph(scoped)
            db = graph_registry.get_graph(scoped)
        return db

    def _ensure_context_structure(graph, context_id: str):
        """Ensure the Context root + category nodes exist in the graph.

        Handles old contexts created before schema changes and multi-worker
        cases where one worker creates the context but another runs the pipeline.
        """
        if not graph or not context_id:
            return
        # Check if Context root exists
        root = graph.get_node(context_id)
        if root:
            # Check if category nodes exist
            knowledge_id = f"{context_id}_knowledge"
            if graph.get_node(knowledge_id):
                return  # already set up
        # Need to create structure
        from ..core.graph_structures import GraphNode, GraphEdge
        from ..context.context_schema import get_categories as _get_cats
        from datetime import datetime, timezone
        now = datetime.now(timezone.utc).isoformat()

        if not root:
            # Get context name from context_manager
            ctx_name = ""
            if context_manager:
                ctx_obj = context_manager.get_context(context_id)
                if ctx_obj:
                    ctx_name = ctx_obj.name
            graph.add_node(GraphNode(
                id=context_id, label="Context",
                properties={"name": ctx_name, "created_at": now},
            ), write_through=True)

        created_any = False
        for cat_key, (cat_label, cat_edge, _children) in _get_cats().items():
            cat_node_id = f"{context_id}_{cat_key}"
            if not graph.get_node(cat_node_id):
                graph.add_node(GraphNode(
                    id=cat_node_id, label=cat_label,
                    properties={"category": cat_key, "created_at": now},
                ), write_through=True)
                graph.add_edge(GraphEdge(
                    id=f"{context_id}_to_{cat_key}",
                    source=context_id, target=cat_node_id,
                    label=cat_edge, properties={},
                ))
                created_any = True

        # Save immediately so other workers see the structure
        if created_any:
            try:
                graph_name = graph.name if hasattr(graph, "name") else ""
                if graph_name:
                    graph_registry.save_graph(graph_name, create_checkpoint=False)
                    logging.getLogger(__name__).info(
                        "[STRUCTURE] Created context structure for %s in graph %s",
                        context_id[:12], graph_name,
                    )
            except Exception as e:
                logging.getLogger(__name__).warning("Failed to save context structure: %s", e)

    # Map context types to the category node suffix + edge label used for linking
    _CONTEXT_TYPE_CATEGORY = {
        "software_dev": ("code", "HAS_CONTENT"),
        "knowledge_base": ("knowledge", "HAS_CONTENT"),
        "rules": ("knowledge", "HAS_CONTENT"),
        "database": ("knowledge", "HAS_CONTENT"),
        "decision": ("knowledge", "HAS_CONTENT"),
        "web": ("web", "HAS_CONTENT"),
    }

    def _link_nodes_to_context(graph, context_id: str, extra_props: dict = None):
        """Link all unlinked content nodes to the context's category node.

        Every non-structural node (Documents, Entities, Facts, Features, etc.)
        gets a HAS_CONTENT edge from the appropriate category node so the
        context tree is navigable.
        """
        if not graph or not context_id:
            return
        from ..core.graph_structures import GraphEdge
        from ..context.boundaries import BOUNDARY_NODE_LABELS
        import uuid as _link_uuid
        _log = logging.getLogger(__name__)

        # Determine the right parent category node based on context type
        cat_suffix = "knowledge"  # default
        if context_manager:
            ctx_obj = context_manager.get_context(context_id)
            if ctx_obj:
                cat_suffix = _CONTEXT_TYPE_CATEGORY.get(
                    ctx_obj.context_type, ("knowledge", "HAS_CONTENT")
                )[0]

        parent_id = f"{context_id}_{cat_suffix}"
        parent = parent_id if graph.get_node(parent_id) else context_id

        # Collect IDs of all category/structure nodes to skip
        from ..context.context_schema import get_categories as _gc
        skip_ids = {context_id}
        for ck in _gc():
            skip_ids.add(f"{context_id}_{ck}")
        skip_labels = BOUNDARY_NODE_LABELS | {"Context"}
        # Also skip category labels themselves
        for _ck, (cat_label, _ce, _ch) in _gc().items():
            skip_labels.add(cat_label)

        # Collect nodes that already have an incoming HAS_CONTENT or HAS_DOCUMENT edge
        already_linked = set()
        for e in graph.get_all_edges():
            lbl = e.get("label", "") if isinstance(e, dict) else getattr(e, "label", "")
            if lbl in ("HAS_CONTENT", "HAS_DOCUMENT"):
                tgt = e.get("target", e.get("dst", "")) if isinstance(e, dict) else getattr(e, "target", "")
                already_linked.add(tgt)

        linked = 0
        for n in graph.get_all_nodes():
            nid = n.id if hasattr(n, "id") else ""
            lbl = n.label if hasattr(n, "label") else ""
            if nid in skip_ids or nid in already_linked:
                continue
            if lbl in skip_labels:
                continue
            try:
                graph.add_edge(GraphEdge(
                    id=str(_link_uuid.uuid4()),
                    source=parent, target=nid,
                    label="HAS_CONTENT",
                    properties=extra_props or {},
                ))
                linked += 1
            except Exception as edge_err:
                _log.warning("Failed to link %s %s to context: %s", lbl, nid[:12], edge_err)

        if linked:
            _log.info("[LINK] Connected %d nodes to context %s (category: %s)",
                       linked, context_id[:12], cat_suffix)
            try:
                graph_registry.save_graph(
                    graph.name if hasattr(graph, "name") else "",
                    create_checkpoint=False,
                )
            except Exception:
                pass  # optional

    def _ingest_nodes(job_id: str, scoped: str, nodes: list, edges: list = None):
        """Background worker: add nodes to graph, create edges, and persist."""
        try:
            from ..core.graph_structures import GraphEdge
            import uuid as _ing_uuid

            db = _ensure_graph(scoped)
            if not db:
                _finish_job(job_id, 0, "Could not create or find graph")
                return

            # Ensure context structure exists (handles old contexts & multi-worker)
            context_id = _ingest_jobs.get(job_id, {}).get("context_id")
            if context_id:
                _ensure_context_structure(db, context_id)

            created = 0
            doc_id = None
            for node in nodes:
                db.add_node(node, write_through=True)
                created += 1
                if node.label == "Document":
                    doc_id = node.id
                elif doc_id and not edges and node.label in ("TextChunk", "Chunk", "Passage"):
                    # Auto-create CONTAINS edges only when caller didn't supply them
                    db.add_edge(GraphEdge(
                        id=str(_ing_uuid.uuid4()),
                        source=doc_id, target=node.id,
                        label="CONTAINS", properties={},
                    ))

            # Add explicit edges if provided
            if edges:
                for edge in edges:
                    db.add_edge(edge)

            # Link documents to KnowledgeBase + save
            if context_id:
                _link_nodes_to_context(db, context_id)
            else:
                try:
                    graph_registry.save_graph(scoped, create_checkpoint=False)
                except Exception as save_err:
                    logging.getLogger(__name__).warning("Failed to persist graph %s: %s", scoped, save_err)
            _finish_job(job_id, created)
        except Exception as e:
            _finish_job(job_id, 0, str(e))

    def _resolve_pipeline_params(pipeline_id: str = None, llm_model: str = None,
                                embedding_model: str = None):
        """Resolve LLM/embedding params from a pipeline definition.

        Explicit ``llm_model`` / ``embedding_model`` override pipeline defaults,
        so users can select a different model while still using a pipeline's schema.
        """
        pipe_llm = pipe_emb = None
        if pipeline_id:
            from ..ingestion.pipeline_store import BUILTIN_PIPELINES, PipelineStore
            for bp in BUILTIN_PIPELINES:
                if bp["id"] == pipeline_id:
                    params = bp.get("default_params", {})
                    pipe_llm = params.get("llm_model")
                    pipe_emb = params.get("embedding_model")
                    break
            else:
                try:
                    store = PipelineStore()
                    stored = store.get_pipeline(pipeline_id)
                    if stored:
                        params = stored.get("default_params", {}) if isinstance(stored, dict) else getattr(stored, "default_params", {}) or {}
                        pipe_llm = params.get("llm_model")
                        pipe_emb = params.get("embedding_model")
                except Exception:
                    pass  # optional
        # Explicit overrides take precedence over pipeline defaults
        return (llm_model or pipe_llm), (embedding_model or pipe_emb)

    def _resolve_pipeline_schema(pipeline_id: str = None, schema_yaml: str = None):
        """Auto-load extraction schema for known pipeline types (e.g., SDLC)."""
        if schema_yaml:
            return schema_yaml  # Explicit schema always wins
        if not pipeline_id:
            return None
        # Map pipeline IDs to built-in schema files
        _PIPELINE_SCHEMA_MAP = {
            "builtin:sdlc-graph-rag": "sdlc",
            "builtin:knowledge-base": "knowledge_base",
        }
        schema_name = _PIPELINE_SCHEMA_MAP.get(pipeline_id)
        if schema_name:
            schema_path = _os.path.join(
                _os.path.dirname(_os.path.dirname(__file__)),
                "config", "schemas", f"{schema_name}.yaml"
            )
            if _os.path.exists(schema_path):
                try:
                    with open(schema_path, "r", encoding="utf-8") as f:
                        return f.read()
                except Exception:
                    pass  # optional
        return None

    def _update_context_embedding_meta(context_id: str, embedding_model: str,
                                        embedding_dimension: int = None):
        """Write embedding metadata back to a context after ingestion."""
        if not context_manager or not context_id or not embedding_model:
            return
        try:
            updates = {"embedding_model": embedding_model}
            if embedding_dimension:
                updates["embedding_dimension"] = embedding_dimension
            context_manager.update_context(context_id, **updates)
        except Exception as e:
            logging.getLogger(__name__).warning("Failed to update context embedding meta: %s", e)

    def _resolve_extraction_schema(cid: str):
        """Resolve extraction schema: named schema > custom yaml > industry tag > type default."""
        if not context_manager or not cid:
            return None
        try:
            ctx_obj = context_manager.get_context(cid)
            if not ctx_obj:
                return None
            # 1. Named schema in config (e.g. "healthcare", "sdlc")
            schema_name = ctx_obj.config.get("extraction_schema")
            if schema_name:
                from ..extraction.schema_registry import get_schema_registry
                schema = get_schema_registry().get_schema_object(schema_name)
                if schema:
                    logger.info("Using named schema '%s' for context %s", schema_name, cid[:12])
                    return schema
            # 2. Custom YAML in config
            custom_yaml = ctx_obj.config.get("extraction_schema_yaml")
            if custom_yaml:
                from ..extraction.schema_loader import load_schema_from_yaml_string
                return load_schema_from_yaml_string(custom_yaml, name=f"{ctx_obj.name}_custom")
            # 3. Auto-detect from industry tags
            if ctx_obj.tags:
                from ..extraction.schema_registry import get_schema_registry
                reg = get_schema_registry()
                for tag in ctx_obj.tags:
                    schema = reg.get_schema_object(tag)
                    if schema:
                        logger.info("Auto-matched schema '%s' from tag for context %s", tag, cid[:12])
                        return schema
            # 4. Default schema for context type
            from ..extraction.schema_loader import get_default_schema
            return get_default_schema(ctx_obj.context_type)
        except Exception as e:
            logger.debug("Schema resolution failed for context %s: %s", cid, e)
            return None

    def _crawl_and_ingest(job_id: str, scoped: str, start_url: str,
                         max_pages: int, max_depth: int,
                         llm_model: str = None, embedding_model: str = None,
                         schema_yaml: str = None):
        """Background worker: crawl website and ingest all pages."""
        try:
            from ..ingestion.web_crawler import crawl_website

            def on_page(page):
                _update_stage(job_id, "CRAWL", "running",
                              count=len([1]))  # will be overwritten

            pages = crawl_website(start_url, max_pages=max_pages, max_depth=max_depth,
                                  on_page=on_page)

            if not pages:
                _finish_job(job_id, 0, "No pages could be crawled from the URL")
                return

            _update_stage(job_id, "CRAWL", "completed", count=len(pages))

            _ensure_graph(scoped)
            from ..core.graph_structures import GraphEdge

            total_nodes = 0
            total_edges = 0
            page_node_ids = {}  # url → node_id

            for i, page in enumerate(pages):
                text = page.get("content", "")
                title = page.get("title", page["url"])
                page_url = page["url"]

                if not text.strip():
                    continue

                if llm_model or embedding_model:
                    # Run full pipeline for this page
                    _ingest_pipeline(
                        f"{job_id}_p{i}", scoped, text,
                        {"title": title, "source": "crawl", "url": page_url},
                        llm_model, embedding_model, schema_yaml,
                    )
                else:
                    # Basic chunking
                    doc_id = str(_uuid.uuid4())
                    page_node_ids[page_url] = doc_id
                    chunks = _chunk_text(text)
                    nodes = [GraphNode(
                        id=doc_id, label="Document",
                        properties={"name": title, "source": "crawl", "url": page_url,
                                    "char_count": len(text), "chunk_count": len(chunks)},
                    )]
                    edges = []
                    for ci, chunk in enumerate(chunks):
                        cid = str(_uuid.uuid4())
                        nodes.append(GraphNode(
                            id=cid, label="TextChunk",
                            properties={"content": chunk, "source": "crawl", "url": page_url,
                                        "chunk_index": ci, "char_count": len(chunk)},
                        ))
                        edges.append(GraphEdge(
                            id=str(_uuid.uuid4()), source=doc_id, target=cid,
                            label="CONTAINS", properties={"chunk_index": ci},
                        ))
                    _ingest_nodes(f"{job_id}_p{i}", scoped, nodes, edges)
                    total_nodes += len(nodes)
                    total_edges += len(edges)

            # Create LINKS_TO edges between pages
            db = graph_registry.get_graph(scoped)
            if db and page_node_ids:
                for page in pages:
                    src_id = page_node_ids.get(page["url"])
                    if not src_id:
                        continue
                    for link in page.get("links", []):
                        from urllib.parse import urljoin
                        abs_link = urljoin(page["url"], link)
                        tgt_id = page_node_ids.get(abs_link)
                        if tgt_id and tgt_id != src_id:
                            try:
                                db.add_edge(GraphEdge(
                                    id=str(_uuid.uuid4()), source=src_id, target=tgt_id,
                                    label="LINKS_TO", properties={},
                                ))
                                total_edges += 1
                            except Exception:
                                pass  # optional
                graph_registry.save_graph(scoped, create_checkpoint=False)

            _finish_job(job_id, total_nodes, edges_created=total_edges)
            logger.info("[CRAWL] Ingested %d pages from %s (%d nodes, %d edges)",
                        len(pages), start_url, total_nodes, total_edges)
        except Exception as e:
            logger.error("[CRAWL] Failed: %s", e, exc_info=True)
            _finish_job(job_id, 0, str(e))

    def _crawl_and_ingest_context(job_id: str, scoped: str, start_url: str,
                                  max_pages: int, max_depth: int,
                                  llm_model: str = None, embedding_model: str = None,
                                  schema_yaml: str = None, context_id: str = None):
        """Background worker: crawl website and ingest all pages into a context."""
        try:
            from ..ingestion.web_crawler import crawl_website

            pages = crawl_website(start_url, max_pages=max_pages, max_depth=max_depth)

            if not pages:
                _finish_job(job_id, 0, "No pages could be crawled from the URL")
                return

            _update_stage(job_id, "CRAWL", "completed", count=len(pages))

            _ensure_graph(scoped)
            from ..core.graph_structures import GraphEdge

            total_nodes = 0
            total_edges = 0
            page_node_ids = {}

            for i, page in enumerate(pages):
                text = page.get("content", "")
                title = page.get("title", page["url"])
                page_url = page["url"]

                if not text.strip():
                    continue

                logger.info("[CRAWL-CTX] Processing page %d/%d: %s (%d chars)",
                            i + 1, len(pages), page_url[:60], len(text))

                if llm_model or embedding_model:
                    _ingest_pipeline(
                        job_id, scoped, text,
                        {"title": title, "source": page_url, "url": page_url, "context_id": context_id},
                        llm_model, embedding_model, schema_yaml, context_id,
                    )
                else:
                    doc_id = str(_uuid.uuid4())
                    page_node_ids[page_url] = doc_id
                    chunks = _chunk_text(text)
                    nodes = [GraphNode(
                        id=doc_id, label="Document",
                        properties={"name": title, "source": page_url, "url": page_url,
                                    "char_count": len(text), "chunk_count": len(chunks)},
                    )]
                    edges = []
                    for ci, chunk in enumerate(chunks):
                        cid = str(_uuid.uuid4())
                        nodes.append(GraphNode(
                            id=cid, label="TextChunk",
                            properties={"content": chunk, "source": page_url, "url": page_url,
                                        "chunk_index": ci, "char_count": len(chunk)},
                        ))
                        edges.append(GraphEdge(
                            id=str(_uuid.uuid4()), source=doc_id, target=cid,
                            label="CONTAINS", properties={"chunk_index": ci},
                        ))
                    _ingest_nodes(f"{job_id}_p{i}", scoped, nodes, edges)
                    total_nodes += len(nodes)
                    total_edges += len(edges)

            # Create LINKS_TO edges between pages
            db = graph_registry.get_graph(scoped)
            if db and page_node_ids:
                for page in pages:
                    src_id = page_node_ids.get(page["url"])
                    if not src_id:
                        continue
                    for link in page.get("links", []):
                        from urllib.parse import urljoin
                        abs_link = urljoin(page["url"], link)
                        tgt_id = page_node_ids.get(abs_link)
                        if tgt_id and tgt_id != src_id:
                            try:
                                db.add_edge(GraphEdge(
                                    id=str(_uuid.uuid4()), source=src_id, target=tgt_id,
                                    label="LINKS_TO", properties={},
                                ))
                                total_edges += 1
                            except Exception:
                                pass  # optional
                graph_registry.save_graph(scoped, create_checkpoint=False)

            # Ensure context structure + refresh stats
            if context_id and context_manager:
                try:
                    db = graph_registry.get_graph(scoped)
                    if db:
                        _ensure_context_structure(db, context_id)
                    context_manager.refresh_stats(context_id)
                except Exception:
                    pass  # optional

            _finish_job(job_id, total_nodes, edges_created=total_edges)
            logger.info("[CRAWL-CTX] Ingested %d pages from %s (%d nodes, %d edges)",
                        len(pages), start_url, total_nodes, total_edges)
        except Exception as e:
            logger.error("[CRAWL-CTX] Failed: %s", e, exc_info=True)
            _finish_job(job_id, 0, str(e))

    def _ingest_pipeline(job_id: str, scoped: str, text: str, source_meta: dict,
                         llm_model: str = None, embedding_model: str = None,
                         schema_yaml: str = None, context_id: str = None):
        """Background worker: run text ingestion via StageExecutor."""
        try:
            # Ensure graph exists in registry (may have been LRU-evicted)
            _ensure_graph(scoped)

            # ── SDLC Repo Scan: detect repo path or GitHub URL ────────────
            _input = text.strip() if text else ""
            _is_github_url = _input.startswith("https://github.com/")
            _is_local_dir = False
            if _input:
                import os as _ros
                _is_local_dir = _ros.path.isdir(_input)

            if _is_github_url or _is_local_dir:
                from ..ingestion.stage_executor import StageExecutor
                from ..ingestion.pipeline_context import PipelineContext
                from ..ingestion.scenario_router import STAGE_SDLC_SCAN

                # Decide scan mode: quick (API only) or full (clone + scan files)
                # Default: quick for GitHub URLs, full for local dirs
                _scan_mode = source_meta.get("scan_mode", "quick" if _is_github_url else "full")

                _repo_path = _input
                if _scan_mode == "full" and _is_github_url:
                    # Full scan needs a clone
                    import tempfile, subprocess
                    _clone_dir = tempfile.mkdtemp(prefix="contextcore_repo_")
                    try:
                        _update_stage(job_id, "CLONE", "running")
                        subprocess.run(
                            ["git", "clone", "--depth", "1", _input, _clone_dir],
                            check=True, capture_output=True, timeout=360,
                        )
                        _update_stage(job_id, "CLONE", "completed")
                        _repo_path = _clone_dir
                    except Exception as _clone_err:
                        logger.warning("Git clone failed, falling back to quick scan: %s", _clone_err)
                        _update_stage(job_id, "CLONE", "failed")
                        _scan_mode = "quick"  # fallback

                logger.info("[SDLC_SCAN] %s scan for %s", _scan_mode, _input)
                _update_stage(job_id, "SDLC_SCAN", "running")

                ctx = PipelineContext(
                    graph_namespace=scoped,
                    stages=[STAGE_SDLC_SCAN],
                    source_text=_repo_path,
                    source_url=_input if _is_github_url else None,
                    source_filename=source_meta.get("title", "repo"),
                    intent="build_graph",
                    context_id=context_id,
                    pipeline_params={"scan_mode": _scan_mode, "skip_llm": True},
                    github_token=source_meta.get("github_token") or None,
                )
                executor = StageExecutor(graph_registry=graph_registry)

                # Track per-stage progress
                def _on_stage(stage_name, status, pipeline_ctx):
                    _update_stage(job_id, stage_name, status,
                                  count=len(pipeline_ctx.nodes) if pipeline_ctx.nodes else 0)
                    # Persist stage results for restart
                    if hasattr(pipeline_ctx, 'stage_results'):
                        _ingest_jobs[job_id]["stage_results"] = list(pipeline_ctx.stage_results)
                        _ingest_jobs[job_id]["current_stage_index"] = pipeline_ctx.current_stage_index
                        _ingest_jobs[job_id]["stages"] = list(pipeline_ctx.stages)

                executor.execute_all(ctx, on_stage=_on_stage)

                node_count = len(ctx.nodes) if ctx.nodes else 0
                edge_count = len(ctx.edges) if ctx.edges else 0
                final_status = "completed" if ctx.status != "failed" else "failed"
                _update_stage(job_id, "SDLC_SCAN", final_status, count=node_count)

                # Save final stage results for UI
                _ingest_jobs[job_id]["stage_results"] = list(ctx.stage_results)
                _ingest_jobs[job_id]["failed_stage"] = None
                if ctx.status == "failed" and ctx.stage_results:
                    failed = [s for s in ctx.stage_results if s.get("status") == "failed"]
                    if failed:
                        _ingest_jobs[job_id]["failed_stage"] = failed[-1].get("stage_name")
                        _ingest_jobs[job_id]["failed_stage_index"] = ctx.current_stage_index

                _finish_job(job_id, node_count, f"SDLC {_scan_mode} scan: {node_count} nodes, {edge_count} edges")
                return

            # Auto-detect conversation content → route through universal pipeline
            try:
                from ..ingestion.content_detector import detect_content_type
                if detect_content_type(text) == "conversation":
                    logger.info("[CONVERSATION] Detected conversation content, routing to universal pipeline")
                    from ..ingestion.smart_ingest import ingest_text

                    # Build schema object compatible with ingest_text
                    from types import SimpleNamespace
                    conv_schema = SimpleNamespace(
                        name="conversation", node_types={}, edge_types={},
                        fact_types=[], context_unit=None,
                    )

                    # Get graph DB
                    _conv_graph = graph_registry.get_graph(scoped)
                    if _conv_graph:
                        # Check if it's an array of conversations (ChatGPT full export)
                        import json as _cjson
                        total_nodes = 0
                        total_edges = 0
                        _is_array = False
                        try:
                            parsed = _cjson.loads(text.strip())
                            if isinstance(parsed, list) and len(parsed) > 0 and isinstance(parsed[0], dict) and "mapping" in parsed[0]:
                                _is_array = True
                                logger.info("[CONVERSATION] Processing %d conversations individually", len(parsed))
                                for i, conv in enumerate(parsed):
                                    conv_title = conv.get("title", f"Conversation {i+1}")
                                    conv_json = _cjson.dumps(conv)
                                    try:
                                        result = ingest_text(
                                            db=_conv_graph, text=conv_json, title=conv_title,
                                            schema=conv_schema,
                                        )
                                        if hasattr(result, 'entity_ids'):
                                            total_nodes += len(result.entity_ids) + len(getattr(result, 'fact_ids', {}))
                                            total_edges += getattr(result, 'edge_count', 0)
                                        elif isinstance(result, dict):
                                            total_nodes += result.get("nodes_created", 0)
                                            total_edges += result.get("edges_created", 0)
                                    except Exception as _ce:
                                        logger.warning("[CONVERSATION] Failed conv %d '%s': %s", i, conv_title[:30], _ce)
                                    if (i + 1) % 10 == 0:
                                        logger.info("[CONVERSATION] Processed %d/%d", i + 1, len(parsed))
                                _finish_job(job_id, nodes_created=total_nodes, edges_created=total_edges)
                                return
                        except (_cjson.JSONDecodeError, ValueError):
                            pass

                        if not _is_array:
                            # Single conversation or text-based conversation
                            result = ingest_text(
                                db=_conv_graph, text=text,
                                title=source_meta.get("title", "Conversation"),
                                schema=conv_schema,
                            )
                            nodes_created = 0
                            edges_created = 0
                            if hasattr(result, 'entity_ids'):
                                nodes_created = len(result.entity_ids) + len(getattr(result, 'fact_ids', {}))
                                edges_created = getattr(result, 'edge_count', 0)
                            elif isinstance(result, dict):
                                nodes_created = result.get("nodes_created", 0)
                                edges_created = result.get("edges_created", 0)
                            _finish_job(job_id, nodes_created=nodes_created, edges_created=edges_created)
                            return
                        _finish_job(job_id, nodes_created=nodes_created, edges_created=edges_created)
                        return
            except Exception as e:
                logger.warning("Conversation auto-routing failed, falling back to legacy: %s", e)

            from ..ingestion.stage_executor import StageExecutor
            from ..ingestion.scenario_router import (
                STAGE_CHUNK, STAGE_DEDUP, STAGE_EXTRACT, STAGE_EXTRACT_FACTS,
                STAGE_EMBED, STAGE_INDEX_BM25, STAGE_STORE_VECTORS,
                STAGE_ENHANCE, STAGE_PERSIST, STAGE_CANONICALIZE,
            )
            from ..ingestion.pipeline_context import PipelineContext

            # Build stage sequence for graph_rag prose text
            # PARSE_FILE creates extraction_result from source_text (required by CHUNK)
            from ..ingestion.scenario_router import STAGE_PARSE_FILE
            stages = [STAGE_PARSE_FILE, STAGE_CHUNK, STAGE_DEDUP]
            if llm_model:
                stages.extend([STAGE_EXTRACT_FACTS, STAGE_EXTRACT])
            if embedding_model:
                stages.append(STAGE_EMBED)
            if llm_model:
                stages.append(STAGE_INDEX_BM25)
            if embedding_model:
                stages.append(STAGE_STORE_VECTORS)
            if llm_model:
                stages.extend([STAGE_CANONICALIZE, STAGE_ENHANCE])
            stages.append(STAGE_PERSIST)

            params = {}
            if llm_model:
                params["llm_model"] = llm_model
            if embedding_model:
                params["embedding_model"] = embedding_model
            if schema_yaml:
                params["schema_yaml"] = schema_yaml
                # Also parse into ExtractionSchema so EXTRACT stage can use it
                try:
                    from ..extraction.schema_loader import load_schema_from_yaml_string
                    params["schema"] = load_schema_from_yaml_string(schema_yaml)
                except Exception as e:
                    logger.warning("Failed to parse schema YAML: %s", e)
            elif context_id:
                auto_schema = _resolve_extraction_schema(context_id)
                if auto_schema:
                    params["schema"] = auto_schema
                    logger.info("Auto-applied extraction schema '%s'", auto_schema.name)

            # Pass category boundary ID for IN_CATEGORY edge creation
            if context_id and context_manager:
                ctx_obj = context_manager.get_context(context_id)
                if ctx_obj:
                    from ..context.boundaries import get_category_boundary_id
                    kb_boundary = get_category_boundary_id(ctx_obj.config, "knowledge_base")
                    if kb_boundary:
                        params["_category_boundary_id"] = kb_boundary

            ctx = PipelineContext(
                graph_namespace=scoped,
                stages=stages,
                source_text=text,
                source_filename=source_meta.get("title", source_meta.get("source", "text_input")),
                source_url=source_meta.get("url") or None,
                intent="graph_rag",
                context_id=context_id,
                pipeline_params=params,
            )

            executor = StageExecutor(graph_registry=graph_registry)

            def on_stage(stage_name, status, pipeline_ctx):
                if _is_job_cancelled(job_id):
                    raise InterruptedError(f"Job cancelled during {stage_name}")
                count = 0
                if status == "completed" and pipeline_ctx:
                    if stage_name == "CHUNK":
                        count = len(getattr(pipeline_ctx, "chunks", []))
                    elif stage_name in ("EXTRACT", "EXTRACT_FACTS"):
                        count = len(getattr(pipeline_ctx, "nodes", []))
                    elif stage_name == "PERSIST":
                        count = len(getattr(pipeline_ctx, "nodes", []))
                _update_stage(job_id, stage_name, status, count)
                logger.info("[PIPELINE] Job %s stage %s → %s (count=%d)", job_id, stage_name, status, count)

            executor.execute_all(ctx, on_stage=on_stage)

            # Write embedding metadata back to context if applicable
            if context_id and embedding_model and ctx.embeddings_count > 0:
                _update_context_embedding_meta(context_id, embedding_model)

            # Extract counts from stage results (PERSIST reports bulk_ingest totals)
            total_nodes = sum(
                sr.get("summary", {}).get("nodes_created", 0)
                for sr in ctx.stage_results if sr.get("status") == "completed"
            )
            total_edges = sum(
                sr.get("summary", {}).get("edges_created", 0)
                for sr in ctx.stage_results if sr.get("status") == "completed"
            )
            # Ensure context structure + link documents to KnowledgeBase
            if context_id:
                graph = graph_registry.get_graph(scoped)
                if graph:
                    _ensure_context_structure(graph, context_id)
                    _link_nodes_to_context(graph, context_id)

            _finish_job(
                job_id,
                nodes_created=total_nodes or len(ctx.nodes),
                edges_created=total_edges or len(ctx.edges),
            )
        except Exception as e:
            logging.getLogger(__name__).error("Pipeline failed: %s", e, exc_info=True)
            _finish_job(job_id, 0, str(e))

    from ..ingestion.chunking import chunk_text as _chunk_text

    # Binary file extensions that must NOT be decoded as UTF-8
    _BINARY_EXTENSIONS = {
        ".xlsx", ".xls", ".pdf", ".docx", ".doc", ".pptx",
        ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".tiff",
        ".mp3", ".wav", ".mp4", ".avi", ".zip", ".tar", ".gz",
    }

    # Text-based graph formats that should go through the file pipeline
    _GRAPH_FILE_EXTENSIONS = {
        ".graphml", ".gml", ".ttl", ".nt", ".nq", ".rdf", ".owl", ".jsonld",
    }

    def _save_upload_to_temp(content: bytes, filename: str) -> str:
        """Save uploaded bytes to a temp file, return the path."""
        suffix = Path(filename).suffix if filename else ""
        fd, path = _tempfile.mkstemp(suffix=suffix, prefix="contextcore_upload_")
        _os.close(fd)
        with open(path, "wb") as f:
            f.write(content)
        return path

    def _is_binary_upload(filename: str) -> bool:
        """Check if the file extension indicates binary content."""
        ext = Path(filename).suffix.lower() if filename else ""
        return ext in _BINARY_EXTENSIONS

    def _needs_file_pipeline(filename: str) -> bool:
        """Check if this file should go through the file-based pipeline (binary or graph format)."""
        ext = Path(filename).suffix.lower() if filename else ""
        return ext in _BINARY_EXTENSIONS or ext in _GRAPH_FILE_EXTENSIONS

    def _ingest_chat_export_pipeline(
        job_id: str,
        scoped: str,
        file_path: str,
        filename: str,
        llm_model: str = None,
        embedding_model: str = None,
        schema_name: str = None,
        context_id: str = None,
    ):
        """Background worker: ingest a chat export (ZIP/JSON) — streaming approach.

        Each conversation is processed through the universal pipeline immediately:
        Parse → Turns → Entities → Topics → Embed → Index → done.
        Results are visible after each conversation, not after the whole batch.
        """
        import os, json as _json, time as _time
        from pathlib import Path
        from types import SimpleNamespace

        try:
            _ensure_graph(scoped)
            from ..ingestion.smart_ingest import ingest_text
            from ..ingestion.universal.parsers.turn_parser import TurnParser

            _update_stage(job_id, "PARSE_FILE", "running")

            # Extract from ZIP if needed
            ext = Path(filename).suffix.lower()
            is_zip = ext == ".zip"
            actual_file = file_path

            if is_zip:
                import zipfile, tempfile
                with zipfile.ZipFile(file_path) as zf:
                    conv_files = [n for n in zf.namelist() if "conversations" in n.lower() and n.endswith(".json")]
                    if not conv_files:
                        _update_stage(job_id, "PARSE_FILE", "failed")
                        _finish_job(job_id, 0, "No conversations.json found in ZIP")
                        return
                    fd, actual_file = tempfile.mkstemp(suffix=".json", prefix="chat_")
                    os.write(fd, zf.read(conv_files[0]))
                    os.close(fd)

            # Load conversations
            with open(actual_file, "r", encoding="utf-8") as f:
                data = _json.load(f)
            convs = data if isinstance(data, list) else data.get("conversations", data.get("chat_messages", []))

            if not convs:
                _finish_job(job_id, 0, "No conversations found in file")
                return

            # Detect source
            sample = convs[0] if convs else {}
            source = "chatgpt" if "mapping" in sample else "claude" if "chat_messages" in data else "generic"

            _update_stage(job_id, "PARSE_FILE", "completed")
            logger.info("[CHAT_STREAM] %s export: %d conversations", source, len(convs))

            # Get graph DB
            db = graph_registry.get_graph(scoped, load_if_missing=True) or graph_registry.get_graph_for_request(scoped)
            if not db:
                _finish_job(job_id, 0, f"Graph '{scoped}' not found")
                return

            # Schema for universal pipeline routing
            conv_schema = SimpleNamespace(
                name="conversation", node_types={}, edge_types={},
                fact_types=[], context_unit=None,
            )

            # Stream: process each conversation through the full pipeline immediately
            parser = TurnParser()
            total_nodes = 0
            total_edges = 0
            processed = 0
            failed = 0

            _update_stage(job_id, "EXTRACT", "running")

            for i, conv in enumerate(convs):
                # Check if job was cancelled (e.g. context deleted)
                if _is_job_cancelled(job_id):
                    logger.info("[CHAT_STREAM] Job %s cancelled after %d/%d conversations", job_id, processed, len(convs))
                    _finish_job(job_id, total_nodes, f"Cancelled after {processed}/{len(convs)} conversations",
                                edges_created=total_edges)
                    return

                conv_title = conv.get("title", f"Conversation {i+1}")

                try:
                    # Parse this single conversation
                    conv_json = _json.dumps(conv)
                    meta, turns = parser._parse_chatgpt(conv) if "mapping" in conv else parser._parse_claude(conv) if "chat_messages" in conv else parser.parse(conv_json)

                    if not turns:
                        continue

                    # Run through universal pipeline (14 operators: signals → entities → topics → embed → index)
                    result = ingest_text(
                        text=conv_json, db=db, title=conv_title,
                        schema=conv_schema,
                    )

                    # Count results
                    if hasattr(result, 'entity_ids'):
                        total_nodes += len(result.entity_ids) + len(getattr(result, 'fact_ids', {}))
                        total_edges += getattr(result, 'edge_count', 0)
                    elif isinstance(result, dict):
                        total_nodes += result.get("nodes_created", 0)
                        total_edges += result.get("edges_created", 0)

                    processed += 1

                except Exception as _conv_err:
                    failed += 1
                    logger.debug("[CHAT_STREAM] Failed '%s': %s", conv_title[:30], _conv_err)

                # Yield GIL so server stays responsive
                _time.sleep(0.01)

                # Progress update every 5 conversations
                if (i + 1) % 5 == 0:
                    _update_stage(job_id, "EXTRACT", "running", processed)
                    logger.info("[CHAT_STREAM] %d/%d conversations processed (%d failed, %d nodes so far)",
                                i + 1, len(convs), failed, total_nodes)

            _update_stage(job_id, "EXTRACT", "completed", processed)
            logger.info("[CHAT_STREAM] Complete: %d/%d conversations, %d nodes, %d edges, %d failed",
                        processed, len(convs), total_nodes, total_edges, failed)

            # Cross-ingestion entity resolution — merge duplicates across conversations
            try:
                from ..ingestion.universal.operators.resolve_entities import resolve_graph_entities
                resolve_result = resolve_graph_entities(db)
                if resolve_result.get("merged", 0) > 0:
                    logger.info("[CHAT_STREAM] Entity resolution: merged %d duplicates", resolve_result["merged"])
                    _update_stage(job_id, "RESOLVE", "completed", resolve_result["merged"])
            except Exception as _re_err:
                logger.debug("[CHAT_STREAM] Entity resolution skipped: %s", _re_err)

            # Save graph
            try:
                graph_registry.save_graph(scoped, create_checkpoint=False)
            except Exception:
                pass

            # Cleanup temp files
            if is_zip and actual_file != file_path:
                try:
                    os.unlink(actual_file)
                except Exception:
                    pass
            try:
                os.unlink(file_path)
            except Exception:
                pass

            _finish_job(job_id, total_nodes, edges_created=total_edges)
            logger.info("[CHAT_PIPELINE] Complete: %s, %d conversations", source, len(convs))

        except Exception as e:
            import traceback
            logger.error("[CHAT_PIPELINE] Failed: %s\n%s", e, traceback.format_exc()[-500:])
            _finish_job(job_id, 0, str(e)[:200])

    def _ingest_file_via_pipeline(
        job_id: str,
        scoped: str,
        file_path: str,
        filename: str,
        llm_model: str = None,
        embedding_model: str = None,
        schema_yaml: str = None,
        context_id: str = None,
        intent: str = "graph_rag",
    ):
        """Background worker: ingest a file using the scenario-driven pipeline."""
        try:
            # Ensure graph exists in registry (may have been LRU-evicted)
            _ensure_graph(scoped)

            from ..ingestion.stage_executor import StageExecutor, run_pipeline
            from ..ingestion.scenario_router import ScenarioRouter, STAGE_PARSE_FILE, STAGE_CLASSIFY
            from ..ingestion.pipeline_context import PipelineContext

            # Build pipeline params
            params = {}
            if llm_model:
                params["llm_model"] = llm_model
            if embedding_model:
                params["embedding_model"] = embedding_model

            # Load schema: explicit yaml > custom/default schema for context type
            if schema_yaml:
                params["schema_yaml"] = schema_yaml
                try:
                    from ..extraction.schema_loader import load_schema_from_yaml_string
                    params["schema"] = load_schema_from_yaml_string(schema_yaml)
                except Exception as e:
                    logger.warning("Failed to parse schema YAML: %s", e)
            elif context_id:
                auto_schema = _resolve_extraction_schema(context_id)
                if auto_schema:
                    params["schema"] = auto_schema
                    logger.info("Auto-applied extraction schema '%s'", auto_schema.name)

            # Pass category boundary ID for IN_CATEGORY edge creation
            try:
                if context_id and context_manager:
                    ctx_obj = context_manager.get_context(context_id)
                    if ctx_obj:
                        from ..context.boundaries import get_category_boundary_id
                        kb_boundary = get_category_boundary_id(ctx_obj.config, "knowledge_base")
                        if kb_boundary:
                            params["_category_boundary_id"] = kb_boundary
            except Exception:
                pass  # optional

            # Phase 1: parse + classify to determine stages
            ctx = PipelineContext(
                graph_namespace=scoped,
                stages=[STAGE_PARSE_FILE, STAGE_CLASSIFY],
                source_bytes_path=file_path,
                source_filename=filename,
                intent=intent,
                context_id=context_id,
                pipeline_params=params,
            )

            executor = StageExecutor(graph_registry=graph_registry)

            # Execute parse + classify
            executor.execute_all(ctx)

            if ctx.status == "failed":
                _finish_job(job_id, 0, f"Classification failed: {ctx.stage_results}")
                return

            # Phase 2: route to remaining stages
            # Check if a specific pipeline template was requested
            pipeline_id = params.get("pipeline_id")
            remaining_stages = None

            if pipeline_id and pipeline_id != "builtin:auto":
                from ..ingestion.pipeline_store import PipelineStore
                pstore = PipelineStore()
                pipeline = pstore.get_pipeline(pipeline_id)
                if pipeline and pipeline.get("stages"):
                    remaining_stages = [
                        s["type"] for s in pipeline["stages"]
                        if s.get("type") not in ("PARSE_FILE", "CLASSIFY")
                    ]

            if not remaining_stages:
                from ..ingestion.pipeline_context import ClassificationResult
                classification = ClassificationResult.from_dict(ctx.classification) if ctx.classification else None
                if classification:
                    router = ScenarioRouter()
                    remaining_stages = router.route(classification, intent)
                else:
                    from ..ingestion.scenario_router import (
                        STAGE_CHUNK, STAGE_DEDUP, STAGE_EXTRACT, STAGE_EXTRACT_FACTS,
                        STAGE_INDEX_BM25, STAGE_PERSIST,
                    )
                    remaining_stages = [STAGE_CHUNK, STAGE_DEDUP, STAGE_EXTRACT_FACTS, STAGE_EXTRACT, STAGE_INDEX_BM25, STAGE_PERSIST]

            # Filter stages based on available models
            if not llm_model:
                remaining_stages = [s for s in remaining_stages if s not in ("EXTRACT", "EXTRACT_FACTS", "ENHANCE_GRAPH", "INDEX_BM25")]
            if not embedding_model:
                remaining_stages = [s for s in remaining_stages if s not in ("EMBED", "STORE_VECTORS")]

            # Auto-insert DEDUP after CHUNK if not already present
            if "CHUNK" in remaining_stages and "DEDUP" not in remaining_stages:
                idx = remaining_stages.index("CHUNK") + 1
                remaining_stages.insert(idx, "DEDUP")

            # Set remaining stages and execute
            ctx.stages = remaining_stages
            ctx.current_stage_index = 0
            ctx.status = "running"

            def on_stage(stage_name, status, pipeline_ctx):
                if _is_job_cancelled(job_id):
                    raise InterruptedError(f"Job cancelled during {stage_name}")
                count = 0
                if status == "completed" and pipeline_ctx:
                    if stage_name == "CHUNK":
                        count = len(getattr(pipeline_ctx, "chunks", []))
                    elif stage_name in ("EXTRACT", "EXTRACT_FACTS"):
                        count = len(getattr(pipeline_ctx, "nodes", []))
                    elif stage_name == "PERSIST":
                        count = len(getattr(pipeline_ctx, "nodes", []))
                _update_stage(job_id, stage_name, status, count)
                logger.info("[PIPELINE] Job %s stage %s → %s (count=%d)", job_id, stage_name, status, count)

            executor.execute_all(ctx, on_stage=on_stage)

            # Write embedding metadata back to context if applicable
            if context_id and embedding_model and ctx.embeddings_count > 0:
                _update_context_embedding_meta(context_id, embedding_model)

            # Extract counts from stage results
            total_nodes = sum(
                sr.get("summary", {}).get("nodes_created", 0)
                for sr in ctx.stage_results if sr.get("status") == "completed"
            )
            total_edges = sum(
                sr.get("summary", {}).get("edges_created", 0)
                for sr in ctx.stage_results if sr.get("status") == "completed"
            )

            # Ensure context structure + link documents to KnowledgeBase
            if context_id:
                graph = graph_registry.get_graph(scoped)
                if graph:
                    _ensure_context_structure(graph, context_id)
                    _link_nodes_to_context(graph, context_id, {"filename": filename})

            _finish_job(job_id, nodes_created=total_nodes or len(ctx.nodes),
                        edges_created=total_edges or len(ctx.edges))
        except Exception as e:
            logging.getLogger(__name__).error("File pipeline failed: %s", e, exc_info=True)
            _finish_job(job_id, 0, str(e))
        finally:
            # Clean up temp file
            try:
                if file_path and _os.path.exists(file_path):
                    _os.unlink(file_path)
            except Exception:
                pass  # optional

    # ------------------------------------------------------------------
    # GET /dashboard/overview
    # ------------------------------------------------------------------
    @router.get("/overview")
    async def overview(user=Depends(user_auth)):
        """Dashboard overview: counts + this month's usage."""
        tenant = _get_tenant(user)

        # Count all graphs (no tenant filtering — namespaces are explicit)
        all_graphs = graph_registry.list_graphs()
        user_graphs = [g for g in all_graphs if g.get("name", "") != "default"]

        total_nodes = sum(g.get("num_nodes", 0) for g in user_graphs)
        total_edges = sum(g.get("num_edges", 0) for g in user_graphs)

        # Agent count
        agent_count = 0
        if agent_registry:
            try:
                agents = agent_registry.list_agents()
                agent_count = len(agents)
            except Exception:
                pass  # optional

        # Usage
        usage = usage_meter.get_summary(tenant.tenant_id)

        # Session + context counts (fast — just count, don't load each one)
        session_count = 0
        total_context_items = 0
        total_context_tokens = 0
        if context_manager:
            try:
                contexts = context_manager.list_contexts()
                total_context_items = sum(c.item_count for c in contexts)
                total_context_tokens = sum(c.estimated_tokens for c in contexts)
            except Exception:
                pass  # optional
        if session_manager:
            try:
                if getattr(user, "is_super_admin", False):
                    session_count = session_manager._conn.execute(
                        "SELECT COUNT(*) as c FROM context_sessions WHERE status = 'active'"
                    ).fetchone()["c"]
                else:
                    # Count only sessions visible to this user
                    session_count = session_manager._conn.execute(
                        "SELECT COUNT(DISTINCT cs.session_id) as c FROM context_sessions cs "
                        "LEFT JOIN session_access sa ON cs.session_id = sa.session_id "
                        "WHERE cs.status != 'deleted' "
                        "AND (cs.owner_agent_id = ? OR sa.agent_id = ?)",
                        (user.user_id, user.user_id),
                    ).fetchone()["c"]
            except Exception:
                pass  # optional

        # Context count
        context_count = 0
        if context_manager:
            try:
                context_count = len(context_manager.list_contexts())
            except Exception:
                pass  # optional

        # Pipeline run count
        pipeline_run_count = len(_pipeline_runs)

        # Active agents (seen in last hour)
        active_agents = 0
        if agent_registry:
            try:
                from datetime import timedelta
                cutoff = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
                for a in agent_registry.list_agents():
                    if getattr(a, "last_seen", None) and a.last_seen > cutoff:
                        active_agents += 1
            except Exception:
                pass  # optional

        # Integration count
        integration_count = 0
        try:
            from .integrations import IntegrationRegistry
            _ir = IntegrationRegistry()
            integration_count = len(_ir.list_for_tenant(tenant.tenant_id))
            if integration_count == 0:
                # Fallback: count all integrations in dev mode
                import sqlite3
                conn = sqlite3.connect(str(_ir._db_path))
                integration_count = conn.execute("SELECT COUNT(*) FROM integrations").fetchone()[0]
                conn.close()
        except Exception:
            pass  # optional

        # Recent activity (last 10 events from usage_meter)
        recent_activity = []
        try:
            events = usage_meter.get_recent_events(tenant.tenant_id, limit=10)
            recent_activity = events
        except Exception:
            pass  # optional

        return {
            "graph_count": len(user_graphs),
            "node_count": total_nodes,
            "edge_count": total_edges,
            "agent_count": agent_count,
            "active_agents": active_agents,
            "session_count": session_count,
            "context_count": context_count,
            "context_items": total_context_items,
            "context_tokens": total_context_tokens,
            "pipeline_runs": pipeline_run_count,
            "integration_count": integration_count,
            "usage_this_month": usage,
            "recent_activity": recent_activity,
            "tenant_id": tenant.tenant_id,
            "tenant_name": tenant.name,
        }

    # ------------------------------------------------------------------
    # GET /dashboard/graphs
    # ------------------------------------------------------------------
    @router.get("/graphs")
    async def list_graphs(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        user=Depends(user_auth),
    ):
        """List all graphs in the user's workspace (paginated)."""
        import time as _t; _t0 = _t.time()
        all_graphs = graph_registry.list_graphs()
        _t1 = _t.time()

        # Use registry metadata directly (Redis is too slow with 1.8M+ keys)
        _meta_cache = {}
        _t2 = _t.time()
        logger.info("[PERF] list_graphs: registry=%.3fs (%d graphs)", _t1 - _t0, len(all_graphs))

        result = []
        for g in all_graphs:
            name = g.get("name", "")
            if name == "default":
                continue
            entry = {**g, "display_name": name}
            # Use GraphMeta (microsecond) → fallback to registry metadata
            meta = _meta_cache.get(name)
            if meta:
                entry["node_count"] = meta.get("node_count", 0)
                entry["edge_count"] = g.get("num_edges", 0)
                entry["context_quality"] = meta.get("quality", 0)
                entry["content_type"] = meta.get("content_type", "mixed")
            else:
                entry["node_count"] = g.get("num_nodes", 0)
                entry["edge_count"] = g.get("num_edges", 0)
            result.append(entry)

        page = paginate(result, limit, offset)
        return {"graphs": page["items"], "total": page["total"], "limit": page["limit"], "offset": page["offset"]}

    # ------------------------------------------------------------------
    # GET /dashboard/graphs/unlinked — graphs not yet linked to a context
    # ------------------------------------------------------------------
    @router.get("/graphs/unlinked")
    async def list_unlinked_graphs(user=Depends(user_auth)):
        """Return graphs that are NOT already backing a Context."""
        tenant = _get_tenant(user)
        all_graphs = graph_registry.list_graphs()

        result = []
        for g in all_graphs:
            name = g.get("name", "")
            user_name = tenant.unscoped_namespace(name)
            if user_name is None:
                continue
            # Skip if already linked to a context
            if context_manager and context_manager.get_by_graph_namespace(name):
                continue
            entry = {"name": name, "display_name": user_name}
            # Use cached metadata counts instead of full graph scan
            entry["node_count"] = g.get("num_nodes", g.get("node_count", 0))
            entry["edge_count"] = g.get("num_edges", g.get("edge_count", 0))
            result.append(entry)

        return {"graphs": result, "total": len(result)}

    # ------------------------------------------------------------------
    # POST /dashboard/graphs
    # ------------------------------------------------------------------
    @router.post("/graphs", status_code=201)
    async def create_graph(req: CreateGraphRequest, bg: BackgroundTasks, user=Depends(require_member)):
        """Create a new graph in the user's workspace."""
        tenant = _get_tenant(user)
        import re as _re_graph
        clean_name = _re_graph.sub(r'[^a-zA-Z0-9_-]', '_', req.name.strip())

        existing = graph_registry.get_graph(clean_name)
        if existing:
            raise HTTPException(status_code=409, detail=f"Graph '{req.name}' already exists")

        graph_registry.create_graph(clean_name)
        graph_registry.save_graph(clean_name, create_checkpoint=False)
        bg.add_task(usage_meter.record, tenant.tenant_id, "node_create", 0)
        event_bus.emit("graph_created", {"name": clean_name})
        return {"name": req.name, "internal_name": clean_name, "status": "created"}

    # ------------------------------------------------------------------
    # DELETE /dashboard/graphs/{name}
    # ------------------------------------------------------------------
    @router.delete("/graphs/{name}")
    async def delete_graph(name: str, user=Depends(require_admin)):
        """Delete a graph from the user's workspace."""
        # Delete directly — no need to load the graph
        graph_registry.delete_graph(name)
        event_bus.emit("graph_deleted", {"name": name})
        return {"name": name, "status": "deleted"}

    # ------------------------------------------------------------------
    # GET /dashboard/graphs/{name}/stats
    # ------------------------------------------------------------------
    # Graph stats cache (TTL 30s) — avoids full graph scan on every page load
    _graph_stats_cache = {}
    _graph_stats_ttl = 30

    @router.get("/graphs/{name}/stats")
    async def graph_stats(name: str, user=Depends(user_auth)):
        """Get detailed stats for a specific graph."""
        import time as _st
        tenant = _get_tenant(user)
        graph, resolved = _resolve_graph(tenant, name)
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")

        cache_key = resolved or name
        cached = _graph_stats_cache.get(cache_key)
        if cached and (_st.time() - cached["_ts"]) < _graph_stats_ttl:
            return {k: v for k, v in cached.items() if k != "_ts"}

        # Count nodes and edges by type
        node_types = {}
        node_count = 0
        for n in graph.get_all_nodes():
            t = getattr(n, 'label', None) or getattr(n, 'node_type', 'unknown')
            node_types[t] = node_types.get(t, 0) + 1
            node_count += 1

        edge_types = {}
        edge_count = 0
        for e in graph.get_all_edges():
            t = getattr(e, 'label', None) or getattr(e, 'edge_type', 'unknown')
            edge_types[t] = edge_types.get(t, 0) + 1
            edge_count += 1

        result = {
            "name": name,
            "node_count": node_count,
            "edge_count": edge_count,
            "node_types": node_types,
            "edge_types": edge_types,
        }
        _graph_stats_cache[cache_key] = {**result, "_ts": _st.time()}
        return result

    @router.get("/graphs/{name}/quality")
    async def graph_quality(name: str, user=Depends(user_auth)):
        """Score the quality of a graph — completeness, connectivity, richness, structure."""
        tenant = _get_tenant(user)
        graph, resolved = _resolve_graph(tenant, name)
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")
        from ..context.quality import score_graph
        return score_graph(graph, graph_name=resolved or name)

    @router.post("/graphs/{name}/resolve-entities")
    async def resolve_entities(name: str, user=Depends(require_member)):
        """Run cross-ingestion entity resolution — merge duplicate entities across the graph."""
        tenant = _get_tenant(user)
        graph, resolved = _resolve_graph(tenant, name)
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")
        from ..ingestion.universal.operators.resolve_entities import resolve_graph_entities
        result = resolve_graph_entities(graph)
        if result.get("merged", 0) > 0:
            try:
                graph_registry.save_graph(resolved or name, create_checkpoint=False)
            except Exception:
                pass
        return result

    # ------------------------------------------------------------------
    # GET /dashboard/graphs/{name}/data — full nodes + edges for explorer
    # ------------------------------------------------------------------
    @router.get("/graphs/{name}/data")
    async def graph_data(name: str, limit: int = 500, user=Depends(user_auth)):
        """Return nodes and edges for graph visualization.

        Limits output for performance — large graphs are capped at `limit` nodes.
        Properties are truncated to keep payloads small.
        """
        tenant = _get_tenant(user)
        graph, resolved = _resolve_graph(tenant, name)

        if not graph:
            return {"nodes": [], "edges": [], "total_nodes": 0, "total_edges": 0, "capped": False}

        try:
            # Use paginated fetch — don't load entire graph into memory
            raw_nodes = graph.get_all_nodes(limit=limit)
            # Get total counts from stats cache or quick count
            cached_stats = _graph_stats_cache.get(resolved or name)
            if cached_stats:
                total_nodes = cached_stats.get("node_count", len(raw_nodes))
                total_edges = cached_stats.get("edge_count", 0)
            else:
                total_nodes = len(raw_nodes)
                total_edges = 0
            capped = total_nodes > limit
            # Only fetch edges connected to the returned nodes
            node_ids = {n.id for n in raw_nodes}
            raw_edges = []
            try:
                for e in graph.get_all_edges():
                    if e.source in node_ids or e.target in node_ids:
                        raw_edges.append(e)
                    if len(raw_edges) >= limit * 3:
                        break
            except Exception:
                pass
        except Exception as exc:
            logger.exception(f"[graph_data] failed to read graph {resolved!r}")
            raise HTTPException(status_code=500, detail=f"Failed to read graph data: {exc}")

        # Keys to strip (large arrays useless for visualization)
        _STRIP_KEYS = {"embedding", "embeddings", "vector", "vectors", "content", "text"}
        _MAX_PROP_LEN = 200  # truncate long string values

        nodes = []
        for n in raw_nodes:
            props = n.properties or {}
            clean_props = {}
            for k, v in props.items():
                if k in _STRIP_KEYS:
                    if k in ("content", "text") and isinstance(v, str):
                        clean_props[f"{k}_preview"] = v[:_MAX_PROP_LEN] + ("..." if len(v) > _MAX_PROP_LEN else "")
                        clean_props["char_count"] = len(v)
                    continue
                if isinstance(v, str) and len(v) > _MAX_PROP_LEN:
                    clean_props[k] = v[:_MAX_PROP_LEN] + "..."
                elif isinstance(v, (list, tuple)) and len(v) > 20:
                    clean_props[k] = f"[{len(v)} items]"
                else:
                    clean_props[k] = v
            if "embedding" in props:
                clean_props["has_embedding"] = True
                clean_props["embedding_dim"] = props.get("embedding_dim") or (len(props["embedding"]) if isinstance(props["embedding"], (list, tuple)) else 0)
            nodes.append({
                "id": n.id,
                "label": n.label,
                "name": n.name or props.get("name", ""),
                "properties": clean_props,
            })

        # Only include edges where both endpoints are in the node set
        node_ids = {n["id"] for n in nodes}
        edges = []
        for e in raw_edges:
            if e.source in node_ids and e.target in node_ids:
                edges.append({
                    "id": e.id,
                    "source": e.source,
                    "target": e.target,
                    "label": e.label,
                    "properties": e.properties or {},
                })

        return {
            "name": name,
            "nodes": nodes,
            "edges": edges,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "capped": capped,
        }

    # ------------------------------------------------------------------
    # POST /dashboard/graphs/{name}/query — execute AIQL on a specific graph
    # ------------------------------------------------------------------
    @router.post("/graphs/{name}/query")
    async def graph_query(name: str, request: Request, user=Depends(user_auth)):
        """Execute an AIQL query against a specific graph."""
        tenant = _get_tenant(user)
        graph, _ = _resolve_graph(tenant, name)
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")

        body = await request.json()
        query_str = body.get("query", "").strip()
        if not query_str:
            raise HTTPException(status_code=400, detail="Query is required")

        try:
            from contextsynapse.aiql import AIQLExecutor
            ex = AIQLExecutor(contextcore=graph, graph_registry=graph_registry)
            result = ex.execute(query_str)

            # Refresh and return updated graph data
            updated_nodes = graph.get_all_nodes()
            updated_edges = graph.get_all_edges()

            nodes_out = [{
                "id": n.id, "label": n.label,
                "name": n.name or (n.properties or {}).get("name", ""),
                "properties": n.properties or {},
            } for n in updated_nodes]

            edges_out = [{
                "id": e.id, "source": e.source, "target": e.target,
                "label": e.label, "properties": e.properties or {},
            } for e in updated_edges]

            return {
                "result": result if isinstance(result, (list, dict)) else str(result),
                "nodes": nodes_out,
                "edges": edges_out,
            }
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @router.get("/graphs/{name}/export")
    async def export_graph(name: str, format: str = "json", user=Depends(user_auth)):
        """Export a graph as JSON, CSV, or Cypher.

        Query params:
            format: "json" | "csv" | "cypher"
        """
        graph = graph_registry.get_graph(name, load_if_missing=True)
        if not graph:
            raise HTTPException(status_code=404, detail="Graph not found")

        from ..context.boundaries import BOUNDARY_NODE_LABELS
        nodes_raw = graph.get_all_nodes()
        edges_raw = graph.get_all_edges()

        nodes = []
        for n in nodes_raw:
            label = n.label if hasattr(n, "label") else ""
            if label in BOUNDARY_NODE_LABELS:
                continue
            props = n.properties if hasattr(n, "properties") else {}
            nodes.append({
                "id": n.id if hasattr(n, "id") else "",
                "label": label,
                "properties": {k: str(v)[:500] for k, v in props.items()},
            })

        edges = []
        node_ids = {n["id"] for n in nodes}
        for e in edges_raw:
            src = e.source if hasattr(e, "source") else e.get("source", "")
            tgt = e.target if hasattr(e, "target") else e.get("target", "")
            lbl = e.label if hasattr(e, "label") else e.get("label", "")
            if src in node_ids and tgt in node_ids:
                edges.append({"source": src, "target": tgt, "label": lbl})

        if format == "json":
            from starlette.responses import JSONResponse
            return JSONResponse(
                content={"nodes": nodes, "edges": edges, "graph": name,
                         "exported_at": datetime.now(timezone.utc).isoformat()},
                headers={"Content-Disposition": f"attachment; filename={name}_graph.json"},
            )

        elif format == "csv":
            import io, csv
            buf = io.StringIO()

            # Nodes CSV
            buf.write("# NODES\n")
            all_keys = set()
            for n in nodes:
                all_keys.update(n["properties"].keys())
            node_fields = ["id", "label"] + sorted(all_keys)
            w = csv.DictWriter(buf, fieldnames=node_fields, extrasaction="ignore")
            w.writeheader()
            for n in nodes:
                row = {"id": n["id"], "label": n["label"], **n["properties"]}
                w.writerow(row)

            buf.write("\n# EDGES\n")
            edge_fields = ["source", "target", "label"]
            w2 = csv.DictWriter(buf, fieldnames=edge_fields)
            w2.writeheader()
            for e in edges:
                w2.writerow(e)

            from starlette.responses import Response
            return Response(
                content=buf.getvalue(),
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={name}_graph.csv"},
            )

        elif format == "cypher":
            lines = []
            for n in nodes:
                props_str = ", ".join(f'{k}: "{v}"' for k, v in list(n["properties"].items())[:10])
                lines.append(f'CREATE (:{n["label"]} {{{props_str}, _id: "{n["id"]}"}});')
            for e in edges:
                lines.append(
                    f'MATCH (a {{_id: "{e["source"]}"}}), (b {{_id: "{e["target"]}"}}) '
                    f'CREATE (a)-[:{e["label"]}]->(b);'
                )
            from starlette.responses import PlainTextResponse
            return PlainTextResponse(
                content="\n".join(lines),
                headers={"Content-Disposition": f"attachment; filename={name}_graph.cypher"},
            )

        raise HTTPException(status_code=400, detail="Format must be: json, csv, or cypher")

    @router.post("/graphs/{name}/import")
    async def import_graph(name: str, request: Request, user=Depends(require_member)):
        """Import nodes and edges into a graph from JSON.

        Body: {"nodes": [{id, label, properties}], "edges": [{source, target, label}]}
        """
        body = await request.json()
        graph = graph_registry.get_graph(name, load_if_missing=True)
        if not graph:
            graph = graph_registry.create_graph(name)

        from ..core.graph_structures import GraphNode, GraphEdge
        import uuid as _imp_uuid

        nodes_added = 0
        edges_added = 0

        for n in body.get("nodes", []):
            try:
                graph.add_node(GraphNode(
                    id=n.get("id", str(_imp_uuid.uuid4())),
                    label=n.get("label", "Imported"),
                    properties=n.get("properties", {}),
                ), write_through=True)
                nodes_added += 1
            except Exception:
                pass  # optional

        for e in body.get("edges", []):
            try:
                graph.add_edge(GraphEdge(
                    id=str(_imp_uuid.uuid4()),
                    source=e["source"], target=e["target"],
                    label=e.get("label", "RELATED_TO"), properties={},
                ))
                edges_added += 1
            except Exception:
                pass  # optional

        graph_registry.save_graph(name, create_checkpoint=False)
        return {"status": "imported", "nodes_added": nodes_added, "edges_added": edges_added}

    @router.post("/graphs/{name}/search")
    async def graph_search_endpoint(name: str, request: Request, user=Depends(user_auth)):
        """Hybrid search across nodes in a graph (BM25 + graph traversal + rerank)."""
        body = await request.json()
        query_str = body.get("query", "").strip()
        limit = body.get("limit", 20)
        if not query_str:
            return {"results": [], "total": 0}

        graph = graph_registry.get_graph(name, load_if_missing=True)
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")

        # Try hybrid search (BM25 + hop traversal + rerank)
        try:
            from ..search.graph_search import graph_search as _hybrid
            gsr = _hybrid(graph, query_str, graph_name=name, k=limit)
            if gsr.nodes:
                results = [
                    {
                        "node_id": sn.node_id,
                        "label": sn.label,
                        "name": sn.props.get("name") or sn.props.get("title") or "",
                        "content": (sn.props.get("content") or sn.props.get("description")
                                    or sn.props.get("statement") or sn.props.get("snippet", ""))[:200],
                        "score": round(sn.score, 3),
                        "_hop": sn.distance,
                    }
                    for sn in gsr.nodes[:limit]
                ]
                return {"results": results, "total": len(results), "engine": "hybrid"}
        except Exception:
            pass

        # Fallback: naive in-memory scan
        from ..context.boundaries import BOUNDARY_NODE_LABELS
        terms = query_str.lower().split()
        results = []
        for node in graph.get_all_nodes():
            label = node.label if hasattr(node, "label") else ""
            if label in BOUNDARY_NODE_LABELS:
                continue
            props = node.properties if hasattr(node, "properties") else {}
            text = " ".join(str(v) for v in props.values()).lower()
            score = sum(1 for t in terms if t in text) / max(len(terms), 1)
            if score > 0:
                results.append({
                    "node_id": node.id if hasattr(node, "id") else "",
                    "label": label,
                    "name": props.get("name") or props.get("title") or "",
                    "content": (props.get("content") or props.get("description") or props.get("statement") or "")[:200],
                    "score": round(score, 2),
                })
        results.sort(key=lambda r: r["score"], reverse=True)
        return {"results": results[:limit], "total": len(results), "engine": "fallback"}

    # ------------------------------------------------------------------
    # GET /dashboard/api-keys
    # ------------------------------------------------------------------
    @router.get("/api-keys")
    async def list_api_keys(user=Depends(user_auth)):
        """List API keys for the user's tenant."""
        tenant = _get_tenant(user)
        # Currently one key per tenant — return masked version
        memberships = user_registry.get_user_tenants(user.user_id)
        keys = []
        for m in memberships:
            t = tenant_registry.get(m.tenant_id)
            if t:
                keys.append({
                    "tenant_id": t.tenant_id,
                    "tenant_name": t.name,
                    "role": m.role,
                    "status": t.status,
                    "key_hint": f"...{t.api_key_hash[:8]}" if t.api_key_hash else None,
                })
        return {"keys": keys}

    # ------------------------------------------------------------------
    # POST /dashboard/api-keys/rotate
    # ------------------------------------------------------------------
    @router.post("/api-keys/rotate")
    async def rotate_api_key(user=Depends(require_owner)):
        """Rotate the API key for the user's primary tenant."""
        tenant = _get_tenant(user)
        # Check ownership
        memberships = user_registry.get_user_tenants(user.user_id)
        is_owner = any(m.tenant_id == tenant.tenant_id and m.role == "owner" for m in memberships)
        if not is_owner:
            raise HTTPException(status_code=403, detail="Only workspace owners can rotate API keys")

        new_key = tenant_registry.rotate_key(tenant.tenant_id)
        if not new_key:
            raise HTTPException(status_code=500, detail="Failed to rotate key")

        return {"api_key": new_key, "message": "Key rotated. Save this — it won't be shown again."}

    # ------------------------------------------------------------------
    # GET /dashboard/usage
    # ------------------------------------------------------------------
    @router.get("/usage")
    async def get_usage(period: Optional[str] = None, user=Depends(user_auth)):
        """Get usage metrics for the current (or specified) month."""
        tenant = _get_tenant(user)
        summary = usage_meter.get_summary(tenant.tenant_id, period)
        daily = usage_meter.get_daily_breakdown(tenant.tenant_id, period)
        return {"summary": summary, "daily": daily, "period": period}

    # ------------------------------------------------------------------
    # GET /dashboard/activity
    # ------------------------------------------------------------------
    @router.get("/activity")
    async def get_activity(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        user=Depends(user_auth),
    ):
        """Get recent activity events (paginated)."""
        tenant = _get_tenant(user)
        events = usage_meter.get_recent_events(tenant.tenant_id, limit + offset)
        page = paginate(events, limit, offset)
        return {"events": page["items"], "total": page["total"], "limit": page["limit"], "offset": page["offset"]}

    # ------------------------------------------------------------------
    # Agents CRUD
    # ------------------------------------------------------------------
    @router.get("/agents")
    async def list_agents(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        platform: Optional[str] = Query(None, description="Filter by platform: desktop, mobile, app, browser, embedded"),
        user=Depends(user_auth),
    ):
        """List registered agents with details (paginated)."""
        if not agent_registry:
            return {"agents": [], "total": 0}

        try:
            agents = agent_registry.list_agents(platform=platform)
            result = []
            for a in agents:
                agent_data = {
                    "agent_id": a.agent_id,
                    "name": a.name,
                    "role": getattr(a, "role", "agent"),
                    "platform": getattr(a, "platform", "app"),
                    "capabilities": getattr(a, "capabilities", ["read", "write"]),
                    "status": getattr(a, "status", "active"),
                    "created_at": getattr(a, "created_at", None),
                    "last_seen": getattr(a, "last_seen", None),
                    "metadata": getattr(a, "metadata", {}),
                }
                # Get recent provenance count
                try:
                    prov = agent_registry.get_provenance("", agent_id=a.agent_id, limit=1)
                    agent_data["last_action"] = prov[0]["operation"] if prov else None
                    agent_data["last_seen"] = prov[0]["timestamp"] if prov else None
                except Exception:
                    agent_data["last_action"] = None
                    agent_data["last_seen"] = None
                result.append(agent_data)
            page = paginate(result, limit, offset)
            return {"agents": page["items"], "total": page["total"], "limit": page["limit"], "offset": page["offset"]}
        except Exception:
            return {"agents": [], "total": 0}

    @router.post("/agents", status_code=201)
    async def register_agent(req: dict = Body(...), user=Depends(require_admin)):
        """Register a new agent from the dashboard."""
        if not agent_registry:
            raise HTTPException(status_code=503, detail="Agent registry not available")

        name = req.get("name", "").strip()
        if not name:
            raise HTTPException(status_code=400, detail="Agent name is required")

        role = req.get("role", "agent")
        platform = req.get("platform", "app")
        capabilities = req.get("capabilities", ["read", "write"])
        metadata = req.get("metadata", {})
        metadata["registered_by"] = user.user_id
        metadata["tenant_id"] = _get_tenant(user).tenant_id

        # Agent card fields (A2A protocol)
        if req.get("description"):
            metadata["description"] = req["description"]
        if req.get("skills"):
            metadata["skills"] = req["skills"]  # list of {id, name, description, tags}
        if req.get("adapter"):
            metadata["adapter"] = req["adapter"]  # openai | anthropic | groq | ollama

        # Initialize runtime stats
        metadata.setdefault("stats", {
            "tasks_completed": 0,
            "tasks_failed": 0,
            "total_turns": 0,
            "last_error": None,
        })

        agent, api_key = agent_registry.register(
            name=name,
            role=role,
            platform=platform,
            capabilities=capabilities,
            metadata=metadata,
        )
        # Return composite key: agent_id:api_key
        return {
            "agent_id": agent.agent_id,
            "name": agent.name,
            "api_key": f"{agent.agent_id}:{api_key}",
            "message": "Agent registered. Save the API key — it won't be shown again.",
        }

    @router.delete("/agents/{agent_id}")
    async def deregister_agent(agent_id: str, user=Depends(require_admin)):
        """Remove a registered agent."""
        if not agent_registry:
            raise HTTPException(status_code=503, detail="Agent registry not available")

        deleted = agent_registry.deregister(agent_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Agent not found")
        return {"message": "Agent deregistered", "agent_id": agent_id}

    @router.get("/agents/{agent_id}/card")
    async def get_agent_card(agent_id: str, user=Depends(user_auth)):
        """Get A2A-compatible agent card with runtime stats."""
        if not agent_registry:
            raise HTTPException(status_code=503, detail="Agent registry not available")

        agent = agent_registry.get(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        from ..context.agent_card import AgentCard
        card = AgentCard.from_registry_agent(agent)

        # Enrich with runtime stats from provenance
        try:
            prov = agent_registry.get_provenance("", agent_id=agent_id, limit=100)
            stats = (getattr(agent, "metadata", {}) or {}).get("stats", {})
            card_dict = card.to_dict()
            card_dict["stats"] = {
                "tasks_completed": stats.get("tasks_completed", 0),
                "tasks_failed": stats.get("tasks_failed", 0),
                "total_turns": stats.get("total_turns", 0),
                "total_actions": len(prov),
                "last_error": stats.get("last_error"),
                "success_rate": (
                    round(stats["tasks_completed"] / max(stats["tasks_completed"] + stats["tasks_failed"], 1) * 100, 1)
                    if stats.get("tasks_completed", 0) + stats.get("tasks_failed", 0) > 0 else None
                ),
            }
            return card_dict
        except Exception:
            return card.to_dict()

    @router.patch("/agents/{agent_id}/card")
    async def update_agent_card(agent_id: str, req: dict = Body(...), user=Depends(user_auth)):
        """Update an agent's card (description, skills, adapter)."""
        if not agent_registry:
            raise HTTPException(status_code=503, detail="Agent registry not available")

        agent = agent_registry.get(agent_id)
        if not agent:
            raise HTTPException(status_code=404, detail="Agent not found")

        meta = getattr(agent, "metadata", {}) or {}
        if "description" in req:
            meta["description"] = req["description"]
        if "skills" in req:
            meta["skills"] = req["skills"]
        if "adapter" in req:
            meta["adapter"] = req["adapter"]

        agent_registry.update_metadata(agent_id, meta)
        return {"message": "Agent card updated", "agent_id": agent_id}

    @router.get("/agents/{agent_id}/activity")
    async def agent_activity(agent_id: str, user=Depends(user_auth)):
        """Get recent provenance/activity for an agent."""
        if not agent_registry:
            return {"activity": []}

        try:
            # Get provenance across all sessions for this agent
            rows = agent_registry._conn.execute(
                "SELECT * FROM provenance WHERE agent_id = ? ORDER BY timestamp DESC LIMIT 50",
                (agent_id,),
            ).fetchall()
            activity = [agent_registry._row_to_provenance(r) for r in rows]
            return {"activity": activity}
        except Exception:
            return {"activity": []}

    # ------------------------------------------------------------------
    # Sessions CRUD
    # ------------------------------------------------------------------

    @router.get("/sessions")
    async def list_sessions(user=Depends(user_auth)):
        """List context sessions visible to the authenticated user.

        Super admins see all sessions across the platform.
        """
        if not session_manager:
            return {"sessions": [], "total": 0}

        try:
            if getattr(user, "is_super_admin", False):
                sessions = session_manager.list_sessions()  # all sessions
            else:
                sessions = session_manager.list_sessions(user_id=user.user_id)
            # Pre-fetch all agents to avoid N+1 queries
            _agent_name_map = {}
            try:
                if agent_registry:
                    for _a in agent_registry.list_agents():
                        _aid = _a.agent_id if hasattr(_a, 'agent_id') else _a.get('agent_id', '')
                        _aname = _a.name if hasattr(_a, 'name') else _a.get('name', '')
                        if _aid:
                            _agent_name_map[_aid] = _aname
            except Exception:
                pass

            result = []
            for s in sessions:
                d = s.to_dict()
                # Enrich with members (names + count)
                members = []
                try:
                    members = session_manager.get_access_list(s.session_id) or []
                except Exception:
                    pass  # optional
                d["member_count"] = len(members)
                enriched = []
                for m in members:
                    mid = m.get("agent_id", "") if isinstance(m, dict) else str(m)
                    name = _agent_name_map.get(mid, mid)
                    enriched.append({**(m if isinstance(m, dict) else {"agent_id": mid}), "name": name})
                d["members"] = enriched

                # Enrich with owner name (agent → user → fallback to ID)
                d["owner_agent_name"] = None
                if s.owner_agent_id:
                    d["owner_agent_name"] = _agent_name_map.get(s.owner_agent_id)
                    if not d["owner_agent_name"] and user_registry:
                        try:
                            owner_user = user_registry.get(s.owner_agent_id)
                            if owner_user:
                                d["owner_agent_name"] = owner_user.display_name or owner_user.email
                        except Exception:
                            pass  # optional
                    if not d["owner_agent_name"]:
                        d["owner_agent_name"] = s.owner_agent_id
                result.append(d)
            return {"sessions": result, "total": len(result)}
        except Exception:
            return {"sessions": [], "total": 0}

    @router.post("/sessions", status_code=201)
    async def create_session(req: CreateSessionRequest, user=Depends(require_member)):
        """Create a new context session with optional context attachment + thread."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        try:
            config = {}
            if req.goal:
                config["goal"] = req.goal

            # Resolve workspace from integration reference or inline config
            if req.workspace_integration_id:
                try:
                    from .integrations import IntegrationRegistry
                    _int_reg = IntegrationRegistry()
                    tenant = _get_tenant(user)
                    ws_int = _int_reg.get(req.workspace_integration_id, tenant.tenant_id)
                    if ws_int and ws_int.status == "active":
                        merged = {**ws_int.config, **ws_int.credentials}
                        ws_type = ws_int.connector_type  # "git" or "github"
                        config["workspace"] = {
                            "type": ws_type,
                            "repo_url": merged.get("repo_url", ""),
                            "branch": merged.get("branch", "main"),
                            "token": merged.get("token", ""),
                        }
                        config["workspace_integration_id"] = req.workspace_integration_id
                    else:
                        logger.warning("Workspace integration %s not active", req.workspace_integration_id)
                except Exception as e:
                    logger.warning("Failed to resolve workspace integration: %s", e)
            elif hasattr(req, 'workspace') and req.workspace:
                config["workspace"] = req.workspace if isinstance(req.workspace, dict) else {}

            # Resolve Jira from integration reference or inline config
            if req.jira_integration_id:
                try:
                    from .integrations import IntegrationRegistry
                    _int_reg = IntegrationRegistry()
                    tenant = _get_tenant(user)
                    jira_int = _int_reg.get(req.jira_integration_id, tenant.tenant_id)
                    if jira_int and jira_int.status == "active":
                        merged = {**jira_int.config, **jira_int.credentials}
                        config.setdefault("integrations", {})["jira"] = {
                            "url": f"https://{merged.get('domain', '')}",
                            "project": merged.get("projects", ""),
                            "token": merged.get("api_token", ""),
                            "email": merged.get("email", ""),
                        }
                        config["jira_integration_id"] = req.jira_integration_id
                except Exception as e:
                    logger.warning("Failed to resolve Jira integration: %s", e)
            elif hasattr(req, 'integrations') and req.integrations:
                config["integrations"] = req.integrations if isinstance(req.integrations, dict) else {}

            # Use the authenticated user as owner; fall back to explicit agent_id
            owner_id = req.agent_id or user.user_id
            session = session_manager.create_session(
                name=req.name,
                owner_agent_id=owner_id,
                config=config,
            )

            # Ensure the creating user always has admin access
            if user.user_id != owner_id:
                session_manager.grant_access(session.session_id, user.user_id, "admin")

            # Graph is NOT created here — only when a context is attached or data ingested.
            # This keeps boundary creation instant and lightweight.

            # Auto-attach context and create thread
            if req.context_id:
                try:
                    session_manager.attach_context(session.session_id, req.context_id)

                    from ..context.conversation import ConversationStore
                    conv_store = ConversationStore()
                    thread = conv_store.create(
                        session_id=session.session_id,
                        title="Thread",
                        metadata={"type": "sdlc_thread", "context_id": req.context_id},
                    )
                    config["thread_id"] = thread.conversation_id
                    config["context_id"] = req.context_id
                    session_manager.update_session(session.session_id, config=config)

                    # System message
                    doc_count = chunk_count = 0
                    ctx_name = req.context_id
                    try:
                        if context_manager:
                            ctx_obj = context_manager.get_context(req.context_id)
                            if ctx_obj:
                                ctx_name = ctx_obj.name
                        db = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
                        if db:
                            for n in db.get_all_nodes():
                                if n.label in ("Document", "Source"):
                                    doc_count += 1
                                elif n.label in ("TextChunk", "Chunk", "Passage"):
                                    chunk_count += 1
                    except Exception:
                        pass  # optional

                    goal_text = f" Goal: {req.goal}" if req.goal else ""
                    conv_store.append_message(
                        conversation_id=thread.conversation_id,
                        role="system",
                        content=f"Session started.{goal_text} Context '{ctx_name}' attached "
                                f"with {doc_count} documents, {chunk_count} chunks available via RAG.",
                        agent_id=None,
                        metadata={"type": "system_event", "event": "session_started"},
                    )
                except Exception as e:
                    logger.warning("Failed to attach context/thread: %s", e)

            # Refresh session to get updated config
            session = session_manager.get_session(session.session_id) or session
            return session.to_dict()
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.post("/sessions/{session_id}/rotate")
    async def rotate_session_id(session_id: str, user=Depends(require_admin)):
        """Rotate session ID — old ID stops working, returns new ID."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        new_id = session_manager.rotate_session_id(session_id)
        if not new_id:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"old_session_id": session_id, "new_session_id": new_id, "message": "Session ID rotated. Agents must reconnect with the new ID."}

    @router.put("/sessions/{session_id}/config")
    async def update_session_config(session_id: str, req: dict = Body(...), user=Depends(require_member)):
        """Update session config (workspace, integrations)."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        # Merge new config into existing
        existing_config = session.config or {}
        if "workspace" in req:
            existing_config["workspace"] = req["workspace"]
        if "integrations" in req:
            existing_config["integrations"] = req["integrations"]

        session_manager.update_session(session_id, config=existing_config)
        return {"status": "updated", "config": existing_config}

    # ------------------------------------------------------------------
    # Workspace file access (browse + download generated code)
    # ------------------------------------------------------------------

    def _get_workspace_path(session_id: str):
        """Resolve workspace path for a session."""
        session = session_manager.get_session(session_id)
        if not session:
            return None
        cfg = session.config or {}
        ws = cfg.get("workspace", {})
        if ws.get("path"):
            return Path(ws["path"])
        safe_name = session.name.replace(' ', '-').lower()
        return Path(f"generated/{safe_name}_{session_id[:12]}")

    def _require_session_access(session_id: str, user) -> None:
        """Raise 403 if the user has no access to this session."""
        # Super admins bypass session access checks
        if getattr(user, "is_super_admin", False):
            return
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        # Owner always has access
        if session.owner_agent_id == user.user_id:
            return
        # Check session_access table
        members = session_manager.get_access_list(session_id)
        if not any(m["agent_id"] == user.user_id for m in members):
            raise HTTPException(status_code=403, detail="No access to this session")

    # ------------------------------------------------------------------
    # Session versioning (checkpoint-based)
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Pipeline run audit trail
    # ------------------------------------------------------------------

    @router.post("/sessions/{session_id}/run-agents")
    async def run_session_agents(session_id: str, req: dict = Body(...), user=Depends(require_member)):
        """Run multi-agent session with per-agent LLM provider/model config.

        Body: {
            "agents": [
                {"id": "architect-agent", "role": "architect", "provider": "anthropic", "model": "claude-haiku-4-5-20251001"},
                {"id": "dev-agent", "role": "developer", "provider": "openai", "model": "gpt-4o-mini"}
            ],
            "task": "optional custom task"
        }
        """
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        agents = req.get("agents", [])
        if len(agents) < 1:
            raise HTTPException(status_code=400, detail="At least 1 agent required")

        # Extract provider/model for architect and developer
        arch_agent = next((a for a in agents if a.get("role") == "architect"), agents[0])
        dev_agent = next((a for a in agents if a.get("role") == "developer"), agents[-1] if len(agents) > 1 else agents[0])

        # Find attached context to get ProjectContext
        attached = session_manager.get_attached_contexts(session_id)
        if not attached:
            raise HTTPException(status_code=400, detail="No context attached to session. Attach a Software Dev context first.")

        import tempfile, threading
        run_id = f"run_{__import__('uuid').uuid4().hex[:8]}"

        # Run in background thread
        def _execute():
            try:
                from ..demo.session_demo import run_session_on_project
                from ..demo.session_report import format_session_report
                from ..project.project_context import ProjectContext

                # Get the project context from attached context's graph namespace
                ctx_info = attached[0]
                ctx_ns = ctx_info.get("graph_namespace", "")
                if not ctx_ns:
                    _pipeline_runs[run_id]["status"] = "failed"
                    _pipeline_runs[run_id]["error"] = "No graph namespace on attached context"
                    return

                pc = ProjectContext.load(ctx_ns)
                task = req.get("task", "Review the project context, identify gaps, and write missing SDLC artifacts.")

                result = run_session_on_project(
                    pc=pc,
                    task=task,
                    base_path=tempfile.gettempdir(),
                    architect_provider=arch_agent.get("provider", "anthropic"),
                    architect_model=arch_agent.get("model", ""),
                    dev_provider=dev_agent.get("provider", "anthropic"),
                    dev_model=dev_agent.get("model", ""),
                )

                report = format_session_report(result)
                _pipeline_runs[run_id].update({
                    "status": "completed",
                    "report": report,
                    "before_coverage": result.before_coverage,
                    "after_coverage": result.after_coverage,
                    "architect_tools": len(result.architect_result.tool_calls),
                    "dev_tools": len(result.dev_result.tool_calls),
                    "elapsed_ms": result.elapsed_ms,
                })
            except Exception as e:
                import traceback
                _pipeline_runs[run_id].update({
                    "status": "failed",
                    "error": str(e),
                    "traceback": traceback.format_exc(),
                })

        _pipeline_runs[run_id] = {
            "run_id": run_id,
            "session_id": session_id,
            "status": "running",
            "agents": agents,
            "started_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }

        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()

        return {"run_id": run_id, "status": "running", "agents": agents}

    @router.get("/sessions/{session_id}/runs")
    async def list_pipeline_runs(session_id: str, user=Depends(user_auth)):
        """List all pipeline runs for a session (audit trail)."""
        runs = [r for r in _pipeline_runs.values() if r["session_id"] == session_id]
        runs.sort(key=lambda r: r["started_at"], reverse=True)
        # Return without full events for listing
        return {"runs": [{k: v for k, v in r.items() if k != "events"} for r in runs]}

    @router.get("/sessions/{session_id}/runs/{run_id}")
    async def get_pipeline_run(session_id: str, run_id: str,
                                filter: Optional[str] = None, user=Depends(user_auth)):
        """Get full details of a pipeline run including all events.

        Query params:
            filter: "graph" — only graph query/search tool calls
                    "git" — only workspace tool calls
                    "tasks" — only task management calls
        """
        run = _pipeline_runs.get(run_id)
        if not run or run["session_id"] != session_id:
            raise HTTPException(status_code=404, detail="Run not found")

        if not filter:
            return run

        GRAPH_TOOLS = {"query_graph", "search_nodes", "get_context", "briefing", "ask",
                       "graph_summary", "add_knowledge", "add_relationship", "use_graph"}
        GIT_TOOLS = {"ws_write_file", "ws_read_file", "ws_list_files", "ws_git_status",
                     "ws_commit", "ws_push", "ws_create_branch", "ws_run_command"}
        TASK_TOOLS = {"add_task", "claim_task", "complete_task", "list_tasks", "my_tasks",
                      "handoff_task", "get_unblocked_tasks", "task_context", "init_project",
                      "project_status", "add_decision", "get_agent_context"}

        filter_set = {"graph": GRAPH_TOOLS, "git": GIT_TOOLS, "tasks": TASK_TOOLS}.get(filter, set())
        if not filter_set:
            return run

        filtered_events = []
        for ev in run.get("events", []):
            if ev.get("type") in ("tool_call", "tool_result") and ev.get("tool") in filter_set:
                filtered_events.append(ev)
            elif ev.get("type") in ("phase", "error", "version"):
                filtered_events.append(ev)

        return {**run, "events": filtered_events, "_filter": filter}

    @router.get("/sessions/{session_id}/runs/latest")
    async def get_latest_run(session_id: str, user=Depends(user_auth)):
        """Get the most recent pipeline run for a session."""
        runs = [r for r in _pipeline_runs.values() if r["session_id"] == session_id]
        if not runs:
            return {"run": None}
        runs.sort(key=lambda r: r["started_at"], reverse=True)
        return runs[0]

    @router.post("/contexts/{context_id}/pipeline/restart")
    async def restart_pipeline_from_stage(
        context_id: str, req: dict = Body(...), user=Depends(require_member),
    ):
        """Restart a pipeline from a specific stage index.

        Body: {"stage_index": 2}  — restart from stage 2 (0-based)
        Stages 0..stage_index-1 are skipped (already completed).
        """
        stage_index = req.get("stage_index", 0)
        if stage_index < 0:
            raise HTTPException(status_code=400, detail="stage_index must be >= 0")

        # Find the context's latest pipeline run
        from ..ingestion.pipeline_store import PipelineStore
        store = PipelineStore()

        # Get context info
        ctx_obj = None
        if context_manager:
            ctx_obj = context_manager.get_context(context_id)
        if not ctx_obj:
            raise HTTPException(status_code=404, detail="Context not found")

        scoped = ctx_obj.graph_namespace
        _ensure_graph(scoped)

        import threading
        from ..ingestion.stage_executor import StageExecutor
        from ..ingestion.pipeline_context import PipelineContext

        run_id = f"restart_{__import__('uuid').uuid4().hex[:8]}"

        def _execute():
            try:
                # Get the pipeline stages from the context's pipeline config
                pipeline_id = ctx_obj.config.get("pipeline_id", "builtin:sdlc-fullscan")
                pipeline = store.get_pipeline(pipeline_id)
                if not pipeline:
                    _pipeline_runs[run_id]["status"] = "failed"
                    _pipeline_runs[run_id]["error"] = f"Pipeline {pipeline_id} not found"
                    return

                stages = [s["type"] for s in pipeline.get("stages", [])]
                if stage_index >= len(stages):
                    _pipeline_runs[run_id]["status"] = "failed"
                    _pipeline_runs[run_id]["error"] = f"stage_index {stage_index} >= {len(stages)} stages"
                    return

                ctx = PipelineContext(
                    graph_namespace=scoped,
                    stages=stages,
                    current_stage_index=stage_index,
                    source_text=ctx_obj.config.get("source_text", ""),
                    source_url=ctx_obj.config.get("source_url", ""),
                    intent="build_graph",
                    context_id=context_id,
                    pipeline_params=pipeline.get("default_params", {}),
                )

                executor = StageExecutor(graph_registry=graph_registry)
                executor.execute_all(ctx)

                _pipeline_runs[run_id].update({
                    "status": "completed" if ctx.status != "failed" else "failed",
                    "nodes": len(ctx.nodes) if ctx.nodes else 0,
                    "restarted_from": stages[stage_index],
                    "stages_run": stages[stage_index:],
                })
            except Exception as e:
                import traceback
                _pipeline_runs[run_id].update({"status": "failed", "error": str(e), "traceback": traceback.format_exc()})

        _pipeline_runs[run_id] = {
            "run_id": run_id,
            "context_id": context_id,
            "status": "running",
            "restarted_from_index": stage_index,
            "started_at": __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(),
        }

        thread = threading.Thread(target=_execute, daemon=True)
        thread.start()

        return {"run_id": run_id, "status": "running", "restarted_from_index": stage_index}

    @router.get("/sessions/{session_id}/versions")
    async def list_session_versions(session_id: str, user=Depends(user_auth)):
        """List all versions (pipeline runs) for a session."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        cfg = session.config or {}
        run_history = cfg.get("run_history", [])
        current_version = cfg.get("version", 0)

        # Also get graph checkpoints
        checkpoints = []
        try:
            from ..core.checkpoint import CheckpointManager
            cp_mgr = CheckpointManager()
            checkpoints = cp_mgr.list_checkpoints(session.graph_namespace, limit=20)
        except Exception:
            pass  # optional

        return {
            "current_version": current_version,
            "runs": list(reversed(run_history)),
            "checkpoints": checkpoints,
        }

    @router.post("/sessions/{session_id}/versions/{version}/restore")
    async def restore_session_version(session_id: str, version: int, user=Depends(require_member)):
        """Restore a session's graph to a specific version checkpoint."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        try:
            from ..core.checkpoint import CheckpointManager
            cp_mgr = CheckpointManager()
            ns = session.graph_namespace
            checkpoints = cp_mgr.list_checkpoints(ns, limit=50)

            # Find checkpoint matching this version
            target_cp = None
            for cp in checkpoints:
                msg = cp.get("message", "")
                if f"v{version}" in msg and "pre-run" in msg:
                    target_cp = cp
                    break

            if not target_cp:
                raise HTTPException(status_code=404, detail=f"No checkpoint found for version {version}")

            graph_file = f"contextcore_data/namespaces/{ns}/graph.h5"
            cp_mgr.restore_checkpoint(ns, target_cp["checkpoint_id"], graph_file)

            # Reload the graph in registry
            if graph_registry:
                try:
                    graph_registry.delete_graph(ns, delete_files=False)
                except Exception:
                    pass  # optional
                graph_registry.load_graph(ns)

            return {"status": "restored", "version": version, "checkpoint_id": target_cp["checkpoint_id"]}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/sessions/{session_id}/workspace/files")
    async def list_workspace_files(session_id: str, path: str = ".", user=Depends(user_auth)):
        """List files in the session workspace."""
        _require_session_access(session_id, user)
        ws_path = _get_workspace_path(session_id)
        if not ws_path or not ws_path.exists():
            return {"files": [], "path": str(ws_path or ""), "exists": False}

        target = ws_path / path
        if not target.exists():
            return {"files": [], "path": str(target), "exists": False}

        files = []
        for item in sorted(target.iterdir()):
            rel = str(item.relative_to(ws_path)).replace("\\", "/")
            if item.name.startswith("."):
                continue
            files.append({
                "name": item.name,
                "path": rel,
                "is_dir": item.is_dir(),
                "size": item.stat().st_size if item.is_file() else 0,
            })
        return {"files": files, "path": str(ws_path), "exists": True}

    @router.get("/sessions/{session_id}/workspace/read")
    async def read_workspace_file(session_id: str, filepath: str = Query(...), user=Depends(user_auth)):
        """Read a file from the workspace."""
        _require_session_access(session_id, user)
        ws_path = _get_workspace_path(session_id)
        if not ws_path:
            raise HTTPException(status_code=404, detail="Workspace not found")

        target = ws_path / filepath
        if not target.exists() or not target.is_file():
            raise HTTPException(status_code=404, detail="File not found")

        # Prevent path traversal
        try:
            target.resolve().relative_to(ws_path.resolve())
        except ValueError:
            raise HTTPException(status_code=403, detail="Access denied")

        try:
            content = target.read_text(encoding="utf-8", errors="replace")
            return {"filepath": filepath, "content": content, "size": len(content)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/sessions/{session_id}/workspace/download")
    async def download_workspace(session_id: str, user=Depends(user_auth)):
        """Download the entire workspace as a zip file."""
        _require_session_access(session_id, user)
        import zipfile
        import io

        ws_path = _get_workspace_path(session_id)
        if not ws_path or not ws_path.exists():
            raise HTTPException(status_code=404, detail="Workspace not found")

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for item in ws_path.rglob("*"):
                if item.is_file() and not any(p.startswith(".") for p in item.relative_to(ws_path).parts):
                    zf.write(item, item.relative_to(ws_path))

        buf.seek(0)
        session = session_manager.get_session(session_id)
        filename = f"{session.name.replace(' ', '-').lower() if session else session_id[:12]}_workspace.zip"

        from starlette.responses import StreamingResponse
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={"Content-Disposition": f"attachment; filename={filename}"},
        )

    @router.delete("/sessions/{session_id}")
    async def delete_session(session_id: str, user=Depends(require_admin)):
        """Soft-delete a context session."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        deleted = session_manager.delete_session(session_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="Session not found")
        return {"message": "Session deleted", "session_id": session_id}

    @router.get("/sessions/{session_id}/members")
    async def list_session_members(session_id: str, user=Depends(user_auth)):
        """List agents with access to a session."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        members = session_manager.get_access_list(session_id)
        # Enrich with agent names
        for m in members:
            if agent_registry:
                agent = agent_registry.get(m["agent_id"])
                m["agent_name"] = agent.name if agent else None
                m["agent_role"] = agent.role if agent else None
            else:
                m["agent_name"] = None
                m["agent_role"] = None
        return {"members": members, "session_id": session_id}

    @router.post("/sessions/{session_id}/members")
    async def grant_session_access(
        session_id: str, req: GrantAccessRequest, user=Depends(require_admin)
    ):
        """Grant an agent access to a session."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if req.level not in ("read", "write", "admin"):
            raise HTTPException(status_code=400, detail="Level must be read, write, or admin")

        session_manager.grant_access(
            session_id, req.agent_id, req.level, allowed_tags=req.allowed_tags,
        )
        return {
            "message": "Access granted",
            "agent_id": req.agent_id,
            "level": req.level,
            "allowed_tags": req.allowed_tags,
        }

    @router.delete("/sessions/{session_id}/members/{agent_id}")
    async def revoke_session_access(
        session_id: str, agent_id: str, user=Depends(require_admin)
    ):
        """Revoke an agent's access to a session."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        revoked = session_manager.revoke_access(session_id, agent_id)
        if not revoked:
            raise HTTPException(status_code=404, detail="Access grant not found")
        return {"message": "Access revoked", "agent_id": agent_id}

    # ------------------------------------------------------------------
    # Agent Assignment — assign/unassign agents to a session
    # ------------------------------------------------------------------

    @router.post("/sessions/{session_id}/agents/{agent_id}")
    async def assign_agent_to_session(session_id: str, agent_id: str, user=Depends(require_member)):
        """Assign an agent to a runtime context (session)."""
        if not session_manager:
            raise HTTPException(501, "Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        assigned = session.config.get("assigned_agents", [])
        if agent_id not in assigned:
            assigned.append(agent_id)
            session.config["assigned_agents"] = assigned
            session_manager.update_session(session_id, config=session.config)
        return {"assigned_agents": assigned}

    @router.delete("/sessions/{session_id}/agents/{agent_id}")
    async def unassign_agent_from_session(session_id: str, agent_id: str, user=Depends(require_member)):
        """Remove an agent from a runtime context."""
        if not session_manager:
            raise HTTPException(501, "Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        assigned = session.config.get("assigned_agents", [])
        if agent_id in assigned:
            assigned.remove(agent_id)
            session.config["assigned_agents"] = assigned
            session_manager.update_session(session_id, config=session.config)
        return {"assigned_agents": assigned}

    @router.get("/sessions/{session_id}/agents")
    async def list_session_agents(session_id: str, user=Depends(user_auth)):
        """List agents assigned to a session."""
        if not session_manager:
            raise HTTPException(501, "Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        assigned = session.config.get("assigned_agents", [])
        # Enrich with agent details
        agents = []
        if agent_registry:
            for aid in assigned:
                agent = agent_registry.get(aid)
                if agent:
                    agents.append({
                        "agent_id": aid,
                        "name": getattr(agent, "name", aid),
                        "platform": getattr(agent, "platform", ""),
                        "status": getattr(agent, "status", ""),
                    })
                else:
                    agents.append({"agent_id": aid, "name": aid, "platform": "", "status": "unknown"})
        return {"session_id": session_id, "agents": agents}

    @router.get("/sessions/{session_id}/activity")
    async def session_activity(session_id: str, user=Depends(user_auth)):
        """Get provenance/activity trail for a session."""
        if not agent_registry:
            return {"activity": []}

        try:
            activity = agent_registry.get_provenance(session_id, limit=50)
            # Enrich with agent names
            for a in activity:
                agent = agent_registry.get(a["agent_id"])
                a["agent_name"] = agent.name if agent else None
            return {"activity": activity}
        except Exception:
            return {"activity": []}

    # ------------------------------------------------------------------
    # Run Agent — trigger Codex on a session, stream results via SSE
    # ------------------------------------------------------------------

    @router.post("/sessions/{session_id}/run-agent")
    async def run_agent_on_session(
        session_id: str,
        req: RunAgentRequest,
        background_tasks: BackgroundTasks,
        user=Depends(user_auth),
    ):
        """Run a multi-agent pipeline on this session's graph.

        Flow: Lead agent plans & assigns tasks → each agent executes its tasks.
        Returns SSE (text/event-stream) with real-time events from all agents.
        """
        from fastapi.responses import StreamingResponse
        import asyncio
        import queue
        import threading

        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        if not agent_registry:
            raise HTTPException(status_code=503, detail="Agent registry not available")

        from ..context.agent_card import AgentCard, build_roster_prompt

        # Get all agents in this session (members)
        members = session_manager.get_access_list(session_id) or []
        member_ids = [m["agent_id"] for m in members if isinstance(m, dict) and m.get("agent_id")]

        # Build agent cards and roster — reuse existing agents as-is
        # Server-side pipeline doesn't need API keys; we set the shared connection directly.
        agent_cards = {}   # name -> AgentCard
        agent_roster = {}  # name -> {agent_id, adapter, card}
        original_to_name = {}  # original_agent_id -> name (for lead matching)
        for mid in member_ids:
            agent = agent_registry.get(mid)
            if agent:
                card = AgentCard.from_registry_agent(agent)
                agent_cards[agent.name] = card
                agent_roster[agent.name] = {
                    "agent_id": mid,
                    "adapter": card.adapter,
                    "card": card,
                }
                original_to_name[mid] = agent.name
                # Mark agent as connected
                try:
                    agent_registry.touch(mid)
                except Exception:
                    pass  # optional

        # Sessions work as standalone context boundaries — external agents
        # attach on demand via MCP/API. No default agent auto-creation.

        # Determine lead and workers (only available agents can work)
        agent_names = list(agent_roster.keys())
        available_names = [n for n in agent_names if agent_cards[n].is_available()]
        if not available_names:
            available_names = agent_names  # fallback: treat all as available

        lead_name = None
        if req.lead_agent_id:
            # Match by original agent_id (from frontend dropdown)
            lead_name = original_to_name.get(req.lead_agent_id)
            # Fallback: match by fresh agent_id or name
            if not lead_name:
                for name, info in agent_roster.items():
                    if info["agent_id"] == req.lead_agent_id or name == req.lead_agent_id:
                        lead_name = name
                        break
        if not lead_name:
            lead_name = available_names[0]
        worker_names = [n for n in available_names if n != lead_name]

        event_queue = queue.Queue()
        ns = session.graph_namespace
        goal = (session.config or {}).get("goal", "")

        # Create a SHARED graph registry + connection so all agents see the same data
        from ..adapters._base import AIContextDBConnection
        # Use the server's graph_registry so pipeline sees all existing data
        shared_conn = AIContextDBConnection(namespace=ns, graph_registry=graph_registry)

        # ── Refresh: load latest data from attached contexts ──────
        attached_context_summary = ""
        try:
            attachments = session_manager.get_session_context_ids(session_id) or []
            if attachments and context_manager:
                ctx_summaries = []
                # Budget: 4000 tokens split across contexts
                total_context_budget = 4000
                budget_per_ctx = total_context_budget // max(len(attachments), 1)

                for att in attachments:
                    ctx_id = att.get("context_id") or att if isinstance(att, str) else None
                    if not ctx_id:
                        continue
                    ctx_obj = context_manager.get_context(ctx_id)
                    if not ctx_obj:
                        continue
                    # Load items with token budget + relevance to goal
                    items = context_manager.get_items(
                        ctx_id, show_all=True,
                        max_tokens=budget_per_ctx,
                        query=req.prompt if hasattr(req, 'prompt') else None,
                    )
                    item_summary = []
                    for item in items:
                        name = item.get("name") or item.get("title") or ""
                        content = item.get("content") or item.get("statement") or item.get("description") or ""
                        label = item.get("label_type") or item.get("label") or ""
                        if name or content:
                            text = f"[{label}] {name}" if label else name
                            if content:
                                text += f": {content[:200]}"
                            item_summary.append(text)
                    if item_summary:
                        ctx_summaries.append(
                            f"### Context: {ctx_obj.name} ({ctx_obj.context_type}, {ctx_obj.item_count} items)\n"
                            + "\n".join(f"- {s}" for s in item_summary)
                        )
                if ctx_summaries:
                    attached_context_summary = (
                        "## Attached Contexts (knowledge loaded from ingested data)\n\n"
                        + "\n\n".join(ctx_summaries)
                    )
                    logger.info("Loaded %d attached contexts for session %s", len(ctx_summaries), session_id[:12])
        except Exception as ctx_err:
            logger.debug("Failed to load attached contexts: %s", ctx_err)

        # Create workspace from session config — use session_id for unique path
        from ..workspace.base import Workspace
        session_config = session.config or {}
        safe_name = session.name.replace(' ', '-').lower()
        sid_short = session.session_id[:12]
        default_ws_path = f"generated/{safe_name}_{sid_short}"
        workspace_config = dict(session_config.get("workspace", {"type": "local"}))

        # Re-resolve integration if workspace was linked by integration_id
        ws_integration_id = session_config.get("workspace_integration_id") or workspace_config.get("integration_id")
        if ws_integration_id:
            try:
                from .integrations import IntegrationRegistry
                _int_reg = IntegrationRegistry()
                # Find the tenant for this session
                _user_placeholder = type('U', (), {'user_id': session.owner_agent_id or ''})()
                tid = None
                for t in tenant_registry.list_tenants():
                    tid = t.tenant_id
                    break
                if tid:
                    ws_int = _int_reg.get(ws_integration_id, tid)
                    if ws_int and ws_int.status == "active":
                        merged = {**ws_int.config, **ws_int.credentials}
                        workspace_config = {
                            "type": ws_int.connector_type,
                            "repo_url": merged.get("repo_url", ""),
                            "branch": merged.get("branch", "main"),
                            "token": merged.get("token", ""),
                        }
                        logger.info("Resolved workspace from integration: %s (%s)",
                                     ws_int.name, ws_int.connector_type)
            except Exception as ws_err:
                logger.warning("Failed to resolve workspace integration: %s", ws_err)

        ws_type = workspace_config.get("type", "local")
        # Ensure every workspace type gets a unique path for this session
        if not workspace_config.get("path"):
            workspace_config["path"] = default_ws_path
        # For Git: path is where the repo is cloned to (unique per session)
        if ws_type in ("git", "github") and "generated/" not in workspace_config.get("path", ""):
            workspace_config["path"] = default_ws_path
        try:
            workspace = Workspace.from_config(workspace_config)
            # Git workspaces: pull latest + create feature branch
            if ws_type in ("git", "github"):
                # Pull latest from remote before agents start
                try:
                    pull_result = workspace.pull() if hasattr(workspace, 'pull') else ""
                    if pull_result:
                        logger.info("Git pull: %s", pull_result[:100])
                        event_queue.put({"type": "tool_result", "tool": "ws_pull",
                                         "result": f"Pulled latest: {pull_result[:80]}", "agent": "system"})
                except Exception as pull_err:
                    logger.debug("Git pull skipped: %s", pull_err)

                # Create feature branch
                try:
                    branch_name = f"feat/{safe_name}-v{session_config.get('version', 0) + 1}"
                    workspace.create_branch(branch_name)
                    logger.info("Created feature branch: %s", branch_name)
                except Exception as br_err:
                    logger.debug("Feature branch creation skipped: %s", br_err)
        except Exception as e:
            logger.warning("Workspace init failed: %s — falling back to local", e)
            workspace = Workspace.from_config({"type": "local", "path": default_ws_path})

        # Build roster prompt for the lead agent
        roster_prompt = build_roster_prompt(list(agent_cards.values()), lead_name)

        # Emit roster info with card summaries
        # Build name→id map for traceability
        agent_id_map = {name: info["agent_id"] for name, info in agent_roster.items()}

        event_queue.put({
            "type": "roster",
            "lead": lead_name,
            "workers": worker_names,
            "all_agents": agent_names,
            "agent_ids": agent_id_map,
            "cards": {n: c.to_dict() for n, c in agent_cards.items()},
        })

        def _resolve_model(agent_name):
            """Pick the right model for an agent based on its adapter + available API keys."""
            adapter = agent_roster.get(agent_name, {}).get("adapter", "openai")
            if adapter == "codex" or "codex" in agent_name.lower():
                return "gpt-5.1-codex", "codex"
            if (adapter == "anthropic" or "claude" in agent_name.lower()) and _os.environ.get("ANTHROPIC_API_KEY"):
                return "claude-sonnet-4-20250514", "anthropic"
            if _os.environ.get("OPENAI_API_KEY"):
                return "gpt-4o-mini", "openai"
            # Fallback chain: Groq → Ollama
            if _os.environ.get("GROQ_API_KEY"):
                return "llama-3.3-70b-versatile", "openai"  # Groq is OpenAI-compatible
            # Try Ollama (local)
            try:
                import requests
                resp = requests.get("http://localhost:11434/api/tags", timeout=2)
                if resp.ok:
                    models = [m["name"] for m in resp.json().get("models", [])]
                    for preferred in ("gemma3", "llama3", "mistral", "qwen"):
                        for m in models:
                            if preferred in m:
                                return m, "ollama"
                    if models:
                        return models[0], "ollama"
            except Exception:
                pass  # optional
            return "gpt-4o-mini", "openai"  # last resort

        def _run_single_agent(agent_name, system_prompt, user_prompt, max_turns):
            """Run one agent's tool-calling loop."""
            import contextsynapse.adapters.openai.tools as oai_tools
            from contextsynapse.workspace.tools import (
                set_workspace, WORKSPACE_TOOL_SCHEMAS, dispatch_workspace_tool
            )

            info = agent_roster.get(agent_name)
            if not info:
                event_queue.put({"type": "error", "message": f"Agent '{agent_name}' not in roster", "agent": agent_name})
                return "(agent not found)"

            # Mark agent as active
            try:
                agent_registry.touch(info["agent_id"])
            except Exception:
                pass  # optional

            # Use the shared connection so all agents see the same graph
            oai_tools._conn = shared_conn
            oai_tools._agent_id = info["agent_id"]
            oai_tools._agent_name = agent_name

            # Set workspace for this agent
            set_workspace(workspace)

            # Init team tools with shared connection
            try:
                from contextsynapse.project.team_tools import init_team
                from contextsynapse.project.graph import ProjectGraph
                pg = ProjectGraph(ns, connection=shared_conn)
                init_team(ns, agent_id=info["agent_id"], agent_name=agent_name, project=pg)
            except Exception:
                pass  # optional

            tools = oai_tools.create_openai_tools()
            # ws_* tools are already in the Universal Registry — no need to add WORKSPACE_TOOL_SCHEMAS

            # Combined dispatch: try graph tools, then workspace tools
            def _dispatch(name, args):
                if name.startswith("ws_"):
                    return dispatch_workspace_tool(name, args)
                return oai_tools.dispatch_tool_call(name, args)

            # AUTO-PUSH: inject project context into system prompt
            try:
                agent_context = pg.build_agent_context(
                    agent_id=info["agent_id"], agent_name=agent_name, max_tokens=4000,
                )
                system_prompt = f"{system_prompt}\n\n## Project Context\n{agent_context}"
            except Exception as ctx_err:
                logger.debug(f"Context injection failed: {ctx_err}")

            model, backend = _resolve_model(agent_name)

            messages = [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ]

            if backend == "anthropic":
                return _run_anthropic_loop(agent_name, model, messages, tools, max_turns, event_queue, _dispatch)
            elif backend == "codex":
                return _run_responses_loop(agent_name, model, system_prompt, user_prompt, tools, max_turns, event_queue, _dispatch)
            elif backend == "ollama":
                return _run_ollama_loop(agent_name, model, messages, tools, max_turns, event_queue, _dispatch)
            else:
                return _run_openai_loop(agent_name, model, messages, tools, max_turns, event_queue, _dispatch)

        def _run_responses_loop(agent_label, model, instructions, user_input, tools, max_turns, eq, dispatch):
            """Run Codex agent via the OpenAI Responses API (not Chat Completions)."""
            from openai import OpenAI
            client = OpenAI()

            # Convert tool schemas from Chat format to Responses format
            codex_tools = []
            for t in tools:
                if "function" in t:
                    fn = t["function"]
                    codex_tools.append({
                        "type": "function",
                        "name": fn["name"],
                        "description": fn.get("description", ""),
                        "parameters": fn.get("parameters", {"type": "object", "properties": {}}),
                    })
                else:
                    codex_tools.append(t)

            previous_response_id = None

            for turn in range(1, max_turns + 1):
                # Check for runtime prompt injections (Codex uses instructions)
                try:
                    from contextsynapse.context.injection import get_injection_queue
                    injections = get_injection_queue().get_pending(session_id, agent_label)
                    if injections:
                        formatted = get_injection_queue().format_for_agent(injections)
                        instructions = instructions + "\n" + formatted
                        for inj in injections:
                            eq.put({"type": "injection_applied", "agent": agent_label,
                                    "message": inj.get("message", ""), "injection_type": inj.get("type", "directive")})
                        if any(inj.get("type") == "stop" for inj in injections):
                            eq.put({"type": "response", "content": "(stopped by human directive)", "agent": agent_label})
                            return "(stopped by human directive)"
                except Exception:
                    pass  # optional

                eq.put({"type": "turn", "turn": turn, "max_turns": max_turns, "agent": agent_label})

                kwargs = {
                    "model": model,
                    "instructions": instructions,
                    "input": user_input,
                    "tools": codex_tools,
                    "tool_choice": "auto",
                }
                if previous_response_id:
                    kwargs["previous_response_id"] = previous_response_id

                resp = client.responses.create(**kwargs)
                previous_response_id = resp.id

                # Process output items
                has_tool_calls = False
                tool_results = []

                for item in resp.output:
                    if item.type == "function_call":
                        has_tool_calls = True
                        name, args = item.name, item.arguments
                        eq.put({"type": "tool_call", "tool": name, "args": args, "turn": turn, "agent": agent_label})
                        result = dispatch(name, args)
                        eq.put({"type": "tool_result", "tool": name, "result": result, "turn": turn, "agent": agent_label})
                        tool_results.append({
                            "type": "function_call_output",
                            "call_id": item.call_id,
                            "output": result,
                        })
                    elif item.type == "message":
                        text_parts = [c.text for c in item.content if hasattr(c, "text")]
                        content = "\n".join(text_parts) or "(no response)"
                        if not has_tool_calls or resp.status == "completed":
                            eq.put({"type": "response", "content": content, "agent": agent_label})

                if not has_tool_calls:
                    # Model responded with text only — check if it actually did work
                    # If we're past turn 1 and it stops, accept it
                    if turn > 1:
                        for item in resp.output:
                            if item.type == "message":
                                text_parts = [c.text for c in item.content if hasattr(c, "text")]
                                return "\n".join(text_parts) or "(completed)"
                        return "(completed)"
                    # Turn 1 with no tool calls — push it to use tools
                    user_input = (
                        "You must use tools to complete your tasks. "
                        "Start by calling claim_task with the task ID, "
                        "then use ws_write_file to create the code files."
                    )
                    continue

                # Feed tool results back for next turn
                if tool_results:
                    user_input = tool_results

            return "(max turns reached)"

        def _check_injections(agent_label, messages, eq):
            """Check for runtime prompt injections before each LLM turn."""
            try:
                from contextsynapse.context.injection import get_injection_queue
                injections = get_injection_queue().get_pending(session_id, agent_label)
                if injections:
                    formatted = get_injection_queue().format_for_agent(injections)
                    messages.append({"role": "user", "content": formatted})
                    for inj in injections:
                        eq.put({
                            "type": "injection_applied",
                            "agent": agent_label,
                            "message": inj.get("message", ""),
                            "injection_type": inj.get("type", "directive"),
                            "priority": inj.get("priority", "normal"),
                        })
                    # Check for stop directive
                    if any(inj.get("type") == "stop" for inj in injections):
                        return True  # signal to stop
            except Exception as e:
                logger.debug("Injection check failed: %s", e)
            return False

        def _run_ollama_loop(agent_label, model, messages, tools, max_turns, eq, dispatch):
            """Run agent via Ollama (local LLM — no tool calling, single-turn)."""
            import requests as _req
            ollama_url = _os.environ.get("OLLAMA_URL", "http://localhost:11434")

            eq.put({"type": "turn", "turn": 1, "max_turns": 1, "agent": agent_label})

            system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
            user = messages[-1]["content"] if messages else ""

            try:
                resp = _req.post(f"{ollama_url}/api/generate", json={
                    "model": model, "prompt": user, "system": system,
                    "stream": False, "options": {"num_predict": 2048},
                }, timeout=120)
                answer = resp.json().get("response", "(no response)")
            except Exception as e:
                answer = f"(Ollama error: {e})"

            eq.put({"type": "response", "content": answer[:500], "agent": agent_label, "turn": 1})
            return answer

        def _run_openai_loop(agent_label, model, messages, tools, max_turns, eq, dispatch):
            from openai import OpenAI
            client = OpenAI()

            for turn in range(1, max_turns + 1):
                # Check for runtime prompt injections
                if _check_injections(agent_label, messages, eq):
                    eq.put({"type": "response", "content": "(stopped by human directive)", "agent": agent_label})
                    return "(stopped by human directive)"

                eq.put({"type": "turn", "turn": turn, "max_turns": max_turns, "agent": agent_label})
                resp = client.chat.completions.create(model=model, messages=messages, tools=tools)
                choice = resp.choices[0]

                if choice.finish_reason == "stop" or not choice.message.tool_calls:
                    content = choice.message.content or "(no response)"
                    eq.put({"type": "response", "content": content, "agent": agent_label})
                    return content

                messages.append(choice.message)
                for tc in choice.message.tool_calls:
                    name, args = tc.function.name, tc.function.arguments
                    eq.put({"type": "tool_call", "tool": name, "args": args, "turn": turn, "agent": agent_label})
                    result = dispatch(name, args)
                    eq.put({"type": "tool_result", "tool": name, "result": result, "turn": turn, "agent": agent_label})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result})

            return "(max turns reached)"

        def _run_anthropic_loop(agent_label, model, messages, tools, max_turns, eq, dispatch):
            import anthropic
            client = anthropic.Anthropic()

            anthropic_tools = []
            for t in tools:
                fn = t.get("function", t)
                anthropic_tools.append({
                    "name": fn.get("name", t.get("name", "")),
                    "description": fn.get("description", t.get("description", "")),
                    "input_schema": fn.get("parameters", t.get("parameters", {"type": "object", "properties": {}})),
                })

            system_prompt = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
            anth_messages = [m for m in messages if m["role"] != "system"]

            for turn in range(1, max_turns + 1):
                # Check for runtime prompt injections (Anthropic uses anth_messages)
                try:
                    from contextsynapse.context.injection import get_injection_queue
                    injections = get_injection_queue().get_pending(session_id, agent_label)
                    if injections:
                        formatted = get_injection_queue().format_for_agent(injections)
                        anth_messages.append({"role": "user", "content": formatted})
                        for inj in injections:
                            eq.put({"type": "injection_applied", "agent": agent_label,
                                    "message": inj.get("message", ""), "injection_type": inj.get("type", "directive")})
                        if any(inj.get("type") == "stop" for inj in injections):
                            eq.put({"type": "response", "content": "(stopped by human directive)", "agent": agent_label})
                            return "(stopped by human directive)"
                except Exception:
                    pass  # optional

                eq.put({"type": "turn", "turn": turn, "max_turns": max_turns, "agent": agent_label})
                resp = client.messages.create(
                    model=model, max_tokens=4096, system=system_prompt,
                    messages=anth_messages, tools=anthropic_tools,
                )
                has_tool_use = any(b.type == "tool_use" for b in resp.content)

                if resp.stop_reason == "end_turn" or not has_tool_use:
                    text_parts = [b.text for b in resp.content if b.type == "text"]
                    content = "\n".join(text_parts) or "(no response)"
                    eq.put({"type": "response", "content": content, "agent": agent_label})
                    return content

                anth_messages.append({"role": "assistant", "content": resp.content})
                tool_results = []
                for block in resp.content:
                    if block.type == "tool_use":
                        name = block.name
                        args = _json.dumps(block.input) if isinstance(block.input, dict) else str(block.input)
                        eq.put({"type": "tool_call", "tool": name, "args": args, "turn": turn, "agent": agent_label})
                        result = dispatch(name, args)
                        eq.put({"type": "tool_result", "tool": name, "result": result, "turn": turn, "agent": agent_label})
                        tool_results.append({"type": "tool_result", "tool_use_id": block.id, "content": result})
                anth_messages.append({"role": "user", "content": tool_results})

            return "(max turns reached)"

        def _run_pipeline():
            """Multi-agent pipeline: lead plans → all agents execute tasks."""
            try:
                # ── Version tracking: increment run version ──────────
                session_cfg = session.config or {}
                current_version = session_cfg.get("version", 0) + 1

                # Create auditable pipeline run
                _run_id = _create_pipeline_run(
                    session_id, req.prompt, lead_name, worker_names, current_version
                )
                # Wrap event_queue to also record to audit trail
                _orig_put = event_queue.put
                def _audited_put(event):
                    # Enrich events with agent_id for traceability
                    if isinstance(event, dict) and event.get("agent") and event["agent"] in agent_id_map:
                        event["agent_id"] = agent_id_map[event["agent"]]
                    _record_pipeline_event(_run_id, event)
                    _orig_put(event)
                event_queue.put = _audited_put
                session_cfg["version"] = current_version
                session_cfg.setdefault("run_history", []).append({
                    "version": current_version,
                    "started_at": datetime.now(timezone.utc).isoformat(),
                    "prompt": req.prompt[:200],
                    "lead": lead_name,
                    "workers": worker_names,
                })
                # Keep only last 20 runs in history
                session_cfg["run_history"] = session_cfg["run_history"][-20:]
                try:
                    session_manager.update_session(session_id, config=session_cfg)
                except Exception:
                    pass  # optional

                # ── Checkpoint: snapshot graph before this run ────────
                try:
                    from ..core.checkpoint import CheckpointManager
                    cp_mgr = CheckpointManager()
                    graph_file = f"contextcore_data/namespaces/{ns}/graph.h5"
                    cp_mgr.create_checkpoint(
                        ns, graph_file,
                        message=f"v{current_version} — pre-run snapshot",
                    )
                    logger.info("Created pre-run checkpoint v%d for %s", current_version, ns)
                except Exception as cp_err:
                    logger.debug("Checkpoint creation failed: %s", cp_err)

                event_queue.put({"type": "version", "version": current_version})

                # ── Create PipelineRun node + ensure boundary structure ──
                from ..core.graph_structures import GraphNode, GraphEdge
                import uuid as _graph_uuid

                run_node_id = f"run_v{current_version}_{_run_id}"
                try:
                    graph = graph_registry.get_graph(ns)
                    if graph:
                        # Ensure boundary root node exists
                        if not graph.get_node(session_id):
                            graph.add_node(GraphNode(
                                id=session_id, label="Boundary",
                                properties={"name": session.name, "created_at": session.created_at},
                            ), write_through=True)

                        # Create PipelineRun node
                        graph.add_node(GraphNode(
                            id=run_node_id, label="PipelineRun",
                            properties={
                                "version": current_version,
                                "prompt": req.prompt[:200],
                                "lead": lead_name,
                                "workers": ",".join(worker_names),
                                "started_at": datetime.now(timezone.utc).isoformat(),
                                "status": "running",
                                "run_id": _run_id,
                            },
                        ), write_through=True)

                        # Link: Boundary ──HAS_RUN──► PipelineRun
                        graph.add_edge(GraphEdge(
                            id=str(_graph_uuid.uuid4()),
                            source=session_id, target=run_node_id,
                            label="HAS_RUN", properties={"version": current_version},
                        ))

                        # Link attached contexts to boundary (if not already linked)
                        try:
                            attachments = session_manager.get_session_context_ids(session_id) or []
                            for att in attachments:
                                ctx_id = att.get("context_id", "") if isinstance(att, dict) else str(att)
                                if not ctx_id:
                                    continue
                                ctx_obj = context_manager.get_context(ctx_id) if context_manager else None
                                ctx_name = ctx_obj.name if ctx_obj else ctx_id[:12]
                                # Add ContextRef node if not exists
                                if not graph.get_node(ctx_id):
                                    graph.add_node(GraphNode(
                                        id=ctx_id, label="ContextRef",
                                        properties={
                                            "name": ctx_name,
                                            "context_id": ctx_id,
                                            "graph_namespace": ctx_obj.graph_namespace if ctx_obj else "",
                                            "context_type": ctx_obj.context_type if ctx_obj else "",
                                            "description": ctx_obj.description if ctx_obj else "",
                                            "item_count": ctx_obj.item_count if ctx_obj else 0,
                                            "estimated_tokens": ctx_obj.estimated_tokens if ctx_obj else 0,
                                            "tags": ctx_obj.tags if ctx_obj else [],
                                            "sensitivity": ctx_obj.sensitivity if ctx_obj else "",
                                        },
                                    ), write_through=True)
                                    graph.add_edge(GraphEdge(
                                        id=str(_graph_uuid.uuid4()),
                                        source=session_id, target=ctx_id,
                                        label="HAS_CONTEXT", properties={},
                                    ))
                        except Exception:
                            pass  # optional

                        # Link agents to the run
                        for a_name, a_info in agent_roster.items():
                            agent_node_id = f"agent_{a_info['agent_id'][:12]}"
                            if not graph.get_node(agent_node_id):
                                graph.add_node(GraphNode(
                                    id=agent_node_id, label="AgentRef",
                                    properties={
                                        "name": a_name,
                                        "agent_id": a_info["agent_id"],
                                        "role": "lead" if a_name == lead_name else "worker",
                                    },
                                ), write_through=True)
                            graph.add_edge(GraphEdge(
                                id=str(_graph_uuid.uuid4()),
                                source=run_node_id, target=agent_node_id,
                                label="EXECUTED_BY", properties={"role": "lead" if a_name == lead_name else "worker"},
                            ))

                        logger.info("Created PipelineRun node %s in graph %s", run_node_id, ns)
                except Exception as gn_err:
                    logger.debug("PipelineRun node creation failed: %s", gn_err)

                # ── Phase 1: Lead agent plans ────────────────────────
                event_queue.put({"type": "phase", "phase": "planning", "agent": lead_name})

                ws_info = workspace.get_status()
                ws_type = ws_info.get("type", "local")
                ws_desc = f"Workspace: {ws_type}"
                if ws_info.get("branch"):
                    ws_desc += f" (branch: {ws_info['branch']})"
                if ws_info.get("repo_url"):
                    ws_desc += f" remote: {ws_info['repo_url']}"
                is_git = ws_type in ("git", "GitWorkspace")

                # Build git workflow instructions
                git_instructions = ""
                if is_git:
                    git_instructions = (
                        f"\n## Git Workflow\n"
                        f"This project uses Git. Follow this process:\n"
                        f"- Agents write code using ws_write_file\n"
                        f"- After completing tasks, commit with ws_commit\n"
                        f"- Push changes with ws_push\n"
                        f"- All work goes to the feature branch — the lead reviews and merges\n"
                    )

                # Check if this is a continuation (version > 1)
                is_continuation = current_version > 1
                continuation_instructions = ""
                if is_continuation:
                    continuation_instructions = (
                        f"\n## This is Run v{current_version} (continuation)\n"
                        f"Previous work has been done on this project. You MUST:\n"
                        f"1. Call list_tasks() to see what's already been completed\n"
                        f"2. Call ws_list_files() to see existing code\n"
                        f"3. Do NOT recreate tasks or files that already exist\n"
                        f"4. Only create NEW tasks for what's still missing\n"
                    )

                all_agents = [lead_name] + worker_names
                plan_prompt = (
                    f"## Goal\n{req.prompt}\n\n"
                    f"## Your Team\n"
                    f"- **{lead_name}** (YOU — Lead Architect. Plan, delegate, make decisions. You do NOT write code.)\n"
                    + "".join(f"- **{w}** (Worker — executes tasks, writes code, reads files)\n" for w in worker_names)
                    + f"\n## YOUR ROLE: Plan & Delegate ONLY\n"
                    f"You are the architect. You understand the requirements, break them into tasks, "
                    f"and assign each task to the right worker. You do NOT read files or write code yourself.\n\n"
                    f"## Steps (do these in order):\n"
                    f"1. Call briefing() FIRST — it gives you the full project state:\n"
                    f"   - Existing tasks, decisions, code files, entities, knowledge\n"
                    f"   - Workspace files already created\n"
                    f"   - What other agents have done\n"
                    f"   - Attached context data (ingested documents, extracted entities)\n"
                    f"   This is your single source of truth. Do NOT duplicate this with ws_list_files.\n"
                    f"2. If you need specific details, use ask('question about the project')\n"
                    f"3. Make key architecture decisions with add_decision()\n"
                    f"3. Break the goal into 4-8 concrete tasks with add_task()\n"
                    f"   - Leave assigned_to EMPTY or set to 'queue' — workers will self-assign\n"
                    f"   - Workers pick tasks from the queue — first to claim gets it\n"
                    f"   - Set priority (critical > high > medium > low) — highest priority picked first\n"
                    f"   - Set depends_on for ordering — blocked tasks won't be picked\n"
                    f"   - Be specific: include file paths, component names, what to implement\n"
                    f"4. Do NOT assign tasks to yourself or specific workers\n"
                    f"5. After creating all tasks, stop. Workers will self-serve from the queue.\n\n"
                    f"## Workers: {', '.join(worker_names)}\n"
                    f"They will each call get_unblocked_tasks() → claim_task() → execute → complete_task() → repeat.\n\n"
                    f"## Bad Examples (DON'T do this):\n"
                    f"- ❌ ws_read_file() — that's a worker's job\n"
                    f"- ❌ ws_write_file() — you don't write code\n"
                    f"- ❌ Assigning tasks to specific workers (let them self-serve)\n"
                    f"- ❌ Assigning tasks to yourself\n\n"
                    f"## Good Examples:\n"
                    f"- ✅ add_task(title='Create Home page', priority='critical', description='Build src/pages/Home.jsx with hero section, summary blocks')\n"
                    f"- ✅ add_task(title='Create About page', priority='high', description='Build src/pages/About.jsx with team info')\n"
                    f"- ✅ add_task(title='Create Contact page', priority='high', depends_on='<home_task_id>')\n"
                    f"{continuation_instructions}"
                    f"{git_instructions}"
                )

                lead_system = (
                    f"You are {lead_name}, the Lead Architect on project '{session.name}' (v{current_version}). "
                    f"Namespace: {ns}.\n"
                    f"{ws_desc}\n\n"
                    f"{roster_prompt}\n\n"
                    f"Your ONLY job: Understand the goal → Plan → Create tasks → Delegate to workers.\n"
                    f"Workers handle all execution (reading files, writing code, committing).\n"
                    f"You handle decisions, planning, and task creation."
                )
                if attached_context_summary:
                    lead_system += f"\n\n{attached_context_summary}"

                _run_single_agent(lead_name, lead_system, plan_prompt, req.max_turns)

                # ── Phase 2: ALL agents execute tasks in parallel ────
                import threading

                def _build_agent_exec(agent_name, is_lead=False):
                    """Build system/user prompts and run an agent."""
                    event_queue.put({"type": "phase", "phase": "executing", "agent": agent_name})

                    git_note = ""
                    if is_git:
                        git_note = (
                            f"\n\n## Git Workflow (IMPORTANT)\n"
                            f"All agents share one repo: {ws_desc}\n"
                            f"The repo has been pulled to latest before you started.\n\n"
                            f"For EACH task:\n"
                            f"1. ws_list_files — see what files exist (check before writing)\n"
                            f"2. ws_read_file — read existing files you need to modify or extend\n"
                            f"3. ws_write_file — write/update code (REAL code, not placeholders)\n"
                            f'4. ws_commit — commit with message: "[{agent_name}] <what you did>"\n'
                            f"5. complete_task — mark the task done with a summary\n\n"
                            f"IMPORTANT:\n"
                            f"- Commit after EACH task, not at the end\n"
                            f"- Always read a file before modifying it — don't overwrite others' work\n"
                            f"- The system auto-pushes after all agents finish\n"
                            f"- Other agents are working in parallel — check for conflicts"
                        )
                    else:
                        git_note = (
                            f"\n\n## Workspace\n"
                            f"Shared local workspace: {ws_desc}\n"
                            f"1. ws_list_files — check existing files\n"
                            f"2. ws_read_file — read before modifying\n"
                            f"3. ws_write_file — write code\n"
                            f"4. complete_task — mark done"
                        )

                    if is_lead:
                        agent_sys = (
                            f"You are {agent_name}, the lead agent on '{session.name}' v{current_version}. "
                            f"Namespace: {ns}. {ws_desc}\n\n"
                            f"You have three jobs:\n"
                            f"1. Execute your own assigned tasks — write real code using ws_write_file\n"
                            f"2. Review what the team produced — ws_list_files, ws_read_file\n"
                            f"3. Create a run summary — call add_knowledge with:\n"
                            f'   name="Run v{current_version} Summary"\n'
                            f'   content="Files created: ..., Tasks completed: ..., Decisions: ..."\n\n'
                            f"RULES:\n"
                            f"- Write REAL code, not descriptions\n"
                            f"- Call complete_task for each task you finish\n"
                            f"- At the END, create the run summary with add_knowledge"
                            f"{git_note}"
                        )
                    else:
                        agent_sys = (
                            f"You are {agent_name}, a developer agent on '{session.name}'. "
                            f"Namespace: {ns}. {ws_desc}\n\n"
                            f"Your job: pick tasks from the queue, execute them, repeat.\n\n"
                            f"WORKFLOW (repeat until no tasks left):\n"
                            f"1. get_unblocked_tasks() — see available tasks\n"
                            f"2. claim_task(task_id) — grab the highest priority one\n"
                            f"3. ws_list_files + ws_read_file — understand existing code\n"
                            f"4. ws_write_file — write REAL, complete code\n"
                            f"5. complete_task(task_id, summary) — mark done\n"
                            f"6. Go back to step 1 for the next task\n\n"
                            f"RULES:\n"
                            f"- Pick unassigned tasks first, then tasks assigned to you\n"
                            f"- If claim fails (another agent grabbed it), pick a different task\n"
                            f"- Read existing files before writing — don't overwrite others' work\n"
                            f"- Write REAL code, not stubs or placeholders\n"
                            f"- Complete at least one task before stopping"
                            f"{git_note}"
                        )

                    # Get available tasks (assigned to this agent OR unassigned)
                    a_info = agent_roster.get(agent_name, {})
                    a_agent_id = a_info.get("agent_id", "")
                    try:
                        # First: tasks specifically assigned to this agent
                        a_tasks = pg.get_open_tasks(agent_name=agent_name)
                        if not a_tasks:
                            a_tasks = pg.get_open_tasks(agent_id=a_agent_id)
                        # Also include unassigned (queue) tasks
                        all_open = pg.get_open_tasks()
                        unassigned = [t for t in all_open if t.get("assigned_to") in ("unassigned", "queue", "", None)]
                        # Merge: agent's tasks first, then unassigned
                        seen = {t["id"] for t in a_tasks}
                        for t in unassigned:
                            if t["id"] not in seen:
                                a_tasks.append(t)
                    except Exception:
                        a_tasks = []

                    # Get dependency edges
                    dep_map = {}
                    try:
                        dep_edges = pg.conn.get_edges(label="DEPENDS_ON")
                        for e in dep_edges:
                            src = e.source if hasattr(e, "source") else e.get("source", "")
                            tgt = e.target if hasattr(e, "target") else e.get("target", "")
                            if src:
                                dep_map.setdefault(src, []).append(tgt)
                    except Exception:
                        pass  # optional

                    # Sort: no-dep tasks first
                    no_deps = [t for t in a_tasks if t.get("id", "") not in dep_map]
                    has_deps = [t for t in a_tasks if t.get("id", "") in dep_map]
                    ordered = no_deps + has_deps

                    task_lines = []
                    for i, t in enumerate(ordered[:6], 1):
                        tid = t.get("id", "")
                        title = t.get("title", "?")
                        desc = t.get("description", "")
                        deps = dep_map.get(tid, [])
                        dep_note = f"\n  Depends on: {', '.join(deps[:3])}" if deps else ""
                        task_lines.append(f"{i}. [{t.get('priority', 'medium').upper()}] {title}\n  ID: {tid}\n  Description: {desc}{dep_note}")

                    task_block = "\n\n".join(task_lines) if task_lines else "No tasks found — call get_unblocked_tasks() to check."

                    agent_prompt = (
                        f"## Your Execution Plan\n\n{task_block}\n\n"
                        f"## Instructions\n"
                        f"Start with task #1 NOW:\n"
                        f"1. claim_task(task_id)\n"
                        f"2. ws_write_file for each code file needed\n"
                        f"3. complete_task(task_id, summary)\n"
                        f"4. Move to the next task\n\n"
                        f"Write REAL, complete code — not stubs or placeholders."
                    )

                    try:
                        _run_single_agent(agent_name, agent_sys, agent_prompt, req.max_turns)
                        agent_registry.increment_stat(a_info.get("agent_id", ""), "tasks_completed")
                    except Exception as e:
                        agent_registry.record_error(a_info.get("agent_id", ""), str(e))
                        event_queue.put({"type": "error", "message": f"{agent_name} failed: {e}", "agent": agent_name})

                # Launch all agents in parallel threads
                threads = []
                for worker in worker_names:
                    t = threading.Thread(target=_build_agent_exec, args=(worker, False), name=f"agent-{worker}")
                    threads.append(t)
                    t.start()

                # Lead also executes its own tasks in parallel
                lead_thread = threading.Thread(target=_build_agent_exec, args=(lead_name, True), name=f"agent-{lead_name}")
                threads.append(lead_thread)
                lead_thread.start()

                # Wait for all agents to finish
                for t in threads:
                    t.join(timeout=300)  # 5 min max per agent

                # Save graph so data persists across connections
                try:
                    shared_conn.save()
                except Exception:
                    pass  # optional

                # Final workspace commit + push
                try:
                    commit_result = workspace.commit(f"Pipeline v{current_version} — {req.prompt[:60]}")
                    event_queue.put({"type": "tool_result", "tool": "ws_commit", "result": commit_result, "agent": "system"})
                    if ws_type in ("git", "github", "GitWorkspace"):
                        push_result = workspace.push()
                        event_queue.put({"type": "tool_result", "tool": "ws_push", "result": push_result, "agent": "system"})
                        if "Error" in push_result:
                            logger.error("Git push failed: %s", push_result)
                except Exception as push_err:
                    logger.error("Final commit/push failed: %s", push_err)
                    event_queue.put({"type": "error", "message": f"Git sync failed: {push_err}", "agent": "system"})

                # Emit workspace summary
                try:
                    ws_status = workspace.get_status()
                    event_queue.put({"type": "workspace", "status": ws_status})
                except Exception:
                    pass  # optional

                # ── Update PipelineRun node as completed ─────────
                try:
                    graph = graph_registry.get_graph(ns)
                    if graph:
                        run_node = graph.get_node(run_node_id)
                        if run_node:
                            props = run_node.properties if hasattr(run_node, "properties") else {}
                            props["status"] = "completed"
                            props["completed_at"] = datetime.now(timezone.utc).isoformat()
                            try:
                                ws_files = workspace.list_files()
                                props["files_created"] = len(ws_files)
                            except Exception:
                                pass  # optional
                            graph.add_node(GraphNode(
                                id=run_node_id, label="PipelineRun", properties=props,
                            ), write_through=True)
                except Exception:
                    pass  # optional

                # ── Post-run checkpoint + version finalize ────────
                try:
                    from ..core.checkpoint import CheckpointManager
                    cp_mgr = CheckpointManager()
                    graph_file = f"contextcore_data/namespaces/{ns}/graph.h5"
                    cp_mgr.create_checkpoint(
                        ns, graph_file,
                        message=f"v{current_version} — post-run (goal: {req.prompt[:80]})",
                    )
                    # Update run history with completion
                    session_cfg = session_manager.get_session(session_id).config or {}
                    if session_cfg.get("run_history"):
                        session_cfg["run_history"][-1]["completed_at"] = datetime.now(timezone.utc).isoformat()
                        session_cfg["run_history"][-1]["status"] = "completed"
                        try:
                            ws_files = workspace.list_files()
                            session_cfg["run_history"][-1]["files_count"] = len(ws_files)
                        except Exception:
                            pass  # optional
                        session_manager.update_session(session_id, config=session_cfg)
                    logger.info("Created post-run checkpoint v%d for %s", current_version, ns)
                except Exception as cp_err:
                    logger.debug("Post-run checkpoint failed: %s", cp_err)

                event_queue.put({"type": "phase", "phase": "complete", "agent": "system", "version": current_version})
                _finish_pipeline_run(_run_id)
                event_queue.put({"type": "done", "run_id": _run_id})

            except Exception as e:
                event_queue.put({"type": "error", "message": str(e), "agent": "system"})
                _finish_pipeline_run(_run_id, error=str(e))
                event_queue.put({"type": "done", "run_id": _run_id})

        async def _event_generator():
            thread = threading.Thread(target=_run_pipeline, daemon=True)
            thread.start()

            while True:
                try:
                    try:
                        event = event_queue.get_nowait()
                    except queue.Empty:
                        await asyncio.sleep(0.1)
                        continue

                    yield f"data: {_json.dumps(event)}\n\n"

                    if event.get("type") == "done":
                        break
                except asyncio.CancelledError:
                    break

        return StreamingResponse(
            _event_generator(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    # ------------------------------------------------------------------
    # Session Context — compose & push context into sessions
    # ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/context")
    async def get_session_context(session_id: str, user=Depends(user_auth)):
        """Get context summary for a session — fast (<100ms), no full node scan."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        ns = session.graph_namespace

        # Try manifest first (<1ms from Redis cache)
        try:
            from ..context.quality import get_manifest, ManifestBuilder, EntityIndex
            import redis as _redis_mod, os as _os
            _r = _redis_mod.from_url(_os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379"))
            manifest = get_manifest(ns, _r)
            if manifest:
                return {
                    "manifest": manifest,
                    "count": manifest.get("stats", {}).get("total_nodes", 0),
                    "estimated_tokens": manifest.get("stats", {}).get("total_nodes", 0) * 50,
                    "graph_namespace": ns,
                    "context_quality": manifest.get("context_quality", 0),
                    "content_type": manifest.get("content_type", "mixed"),
                    "top_entities": manifest.get("top_entities", {}),
                    "themes": manifest.get("themes", []),
                    "tool_hints": manifest.get("tool_hints", []),
                }
        except Exception:
            pass

        # Fallback: read GraphMeta (microsecond) instead of scanning Redis keys
        try:
            from ..context.quality import GraphMeta
            import redis as _redis_mod2, os as _os2
            _r2 = _redis_mod2.from_url(_os2.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379"))
            meta = GraphMeta(_r2).get(ns)
            if meta:
                return {
                    "count": meta.get("node_count", 0),
                    "estimated_tokens": meta.get("node_count", 0) * 50,
                    "graph_namespace": ns,
                    "context_quality": meta.get("quality", 0),
                    "content_type": meta.get("content_type", "mixed"),
                }
        except Exception:
            pass

        return {"count": 0, "estimated_tokens": 0, "graph_namespace": ns}

    @router.post("/sessions/{session_id}/context/{item_index}/metadata")
    async def update_context_metadata(
        session_id: str, item_index: int, req: dict = Body(...), user=Depends(user_auth)
    ):
        """Update tags and sensitivity of a context item (stored as node properties)."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        tags = req.get("tags", [])
        sensitivity = req.get("sensitivity", "public")

        # Update the corresponding graph node's properties
        if graph_registry:
            graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
            if graph:
                try:
                    all_nodes = graph.get_all_nodes()
                    if 0 <= item_index < len(all_nodes):
                        node = all_nodes[item_index]
                        node.properties["tags"] = tags
                        node.properties["sensitivity"] = sensitivity
                        return {
                            "status": "updated",
                            "item_index": item_index,
                            "tags": tags,
                            "sensitivity": sensitivity,
                        }
                except Exception as exc:
                    raise HTTPException(status_code=500, detail=str(exc))

        raise HTTPException(status_code=400, detail="Could not update item")

    @router.post("/sessions/{session_id}/context/text")
    async def add_text_context(
        session_id: str, req: dict = Body(...), user=Depends(require_member)
    ):
        """Add text context to a session's graph namespace."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        text = req.get("text", "").strip()
        label = req.get("label", "Manual context")
        role = req.get("role", "background")

        if not text:
            raise HTTPException(status_code=400, detail="Text is required")

        nodes_created = 0
        # Try to extract entities and add to the session's graph
        if graph_registry:
            graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
            if not graph:
                graph_registry.create_graph(session.graph_namespace)
                graph = graph_registry.get_graph(session.graph_namespace)

            if graph:
                try:
                    from ..ingestion.llm_extractor import LLMExtractor
                    extractor = LLMExtractor()
                    entities = extractor.extract_entities(text)
                    for ent in (entities or []):
                        try:
                            graph.add_node(
                                node_type=ent.get("type", "Entity"),
                                properties=ent.get("properties", {"name": ent.get("name", "Unknown")}),
                            )
                            nodes_created += 1
                        except Exception:
                            pass  # optional
                except ImportError:
                    # No LLM extractor — store as a raw TextChunk node
                    try:
                        graph.add_node(
                            node_type="TextChunk",
                            properties={"content": text[:2000], "label": label, "role": role},
                        )
                        nodes_created = 1
                    except Exception:
                        pass  # optional

        _record_lineage(session_id, {
            "type": "overlay",
            "label": label,
            "role": role,
            "nodes_created": nodes_created,
        })

        return {
            "status": "success",
            "nodes_created": nodes_created,
            "label": label,
            "session_id": session_id,
        }

    @router.post("/sessions/{session_id}/context/from-graph")
    async def seed_from_graph(
        session_id: str, req: dict = Body(...), user=Depends(user_auth)
    ):
        """Copy nodes from a tenant graph into a session's graph namespace."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        source_graph_name = req.get("graph", "")
        node_type_filter = req.get("node_type", None)
        node_ids_filter = req.get("node_ids", None)  # list of specific node IDs
        limit = req.get("limit", 200)

        if not source_graph_name:
            raise HTTPException(status_code=400, detail="Source graph name is required")

        tenant = _get_tenant(user)
        scoped = _scope_graph(tenant, source_graph_name)

        source = graph_registry.get_graph(scoped) or graph_registry.get_graph(source_graph_name)
        if not source:
            raise HTTPException(status_code=404, detail="Source graph not found")

        # Get or create destination graph
        dest = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
        if not dest:
            graph_registry.create_graph(session.graph_namespace)
            dest = graph_registry.get_graph(session.graph_namespace)

        if not dest:
            raise HTTPException(status_code=500, detail="Could not create session graph")

        # Copy nodes
        nodes = source.get_all_nodes()
        node_ids_set = set(node_ids_filter) if node_ids_filter else None
        copied = 0
        skipped = 0
        type_counts = {}
        for n in nodes[:limit]:
            if isinstance(n, dict):
                nid = n.get("id", n.get("uuid", ""))
                ntype = n.get("label", n.get("node_type", "Entity"))
                props = {k: v for k, v in n.items() if k not in ("uuid", "domain", "label", "node_type")}
            else:
                nid = getattr(n, "id", getattr(n, "uuid", ""))
                ntype = getattr(n, "label", "Entity")
                props = getattr(n, "properties", {}) or {}

            if node_type_filter and ntype != node_type_filter:
                continue
            if node_ids_set and str(nid) not in node_ids_set:
                continue

            try:
                new_node = GraphNode(id=str(_uuid.uuid4()), label=ntype, properties=props)
                dest.add_node(new_node)
                copied += 1
                type_counts[ntype] = type_counts.get(ntype, 0) + 1
            except Exception:
                skipped += 1

        _record_lineage(session_id, {
            "type": "graph",
            "source": source_graph_name,
            "node_type_filter": node_type_filter,
            "nodes_copied": copied,
            "node_types": type_counts,
        })

        return {
            "status": "success",
            "copied": copied,
            "skipped_duplicates": skipped,
            "node_types": type_counts,
            "source_graph": source_graph_name,
            "session_id": session_id,
        }

    @router.post("/sessions/{session_id}/context/from-session")
    async def seed_from_session(
        session_id: str, req: dict = Body(...), user=Depends(user_auth)
    ):
        """Pull context nodes from another session into this one (cross-session context)."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        target = session_manager.get_session(session_id)
        if not target:
            raise HTTPException(status_code=404, detail="Target session not found")

        source_session_id = req.get("source_session_id", "")
        limit = req.get("limit", 200)

        source = session_manager.get_session(source_session_id)
        if not source:
            raise HTTPException(status_code=404, detail="Source session not found")

        if not graph_registry:
            raise HTTPException(status_code=503, detail="Graph registry not available")

        src_graph = graph_registry.get_graph(source.graph_namespace, load_if_missing=True)
        if not src_graph:
            return {"status": "success", "copied": 0, "message": "Source session has no graph data"}

        dest_graph = graph_registry.get_graph(target.graph_namespace, load_if_missing=True)
        if not dest_graph:
            graph_registry.create_graph(target.graph_namespace)
            dest_graph = graph_registry.get_graph(target.graph_namespace)

        nodes = src_graph.get_all_nodes()
        copied = 0
        skipped = 0
        type_counts = {}
        for n in nodes[:limit]:
            if isinstance(n, dict):
                ntype = n.get("label", n.get("node_type", "Entity"))
                props = {k: v for k, v in n.items() if k not in ("uuid", "domain", "label", "node_type")}
            else:
                ntype = getattr(n, "label", "Entity")
                props = getattr(n, "properties", {}) or {}
            try:
                new_node = GraphNode(id=str(_uuid.uuid4()), label=ntype, properties=props)
                dest_graph.add_node(new_node)
                copied += 1
                type_counts[ntype] = type_counts.get(ntype, 0) + 1
            except Exception:
                skipped += 1

        _record_lineage(session_id, {
            "type": "session",
            "source_session_id": source_session_id,
            "source_session_name": source.name,
            "nodes_copied": copied,
            "node_types": type_counts,
        })

        return {
            "status": "success",
            "copied": copied,
            "skipped_duplicates": skipped,
            "node_types": type_counts,
            "source_session": source.name,
            "session_id": session_id,
        }

    # ------------------------------------------------------------------
    # Session Context Preview — see what agents receive
    # ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/context/preview")
    async def preview_session_context(
        session_id: str, format: str = "messages", user=Depends(user_auth)
    ):
        """Preview session context in agent-consumable format."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        try:
            from ..context.hub import ContextHub
            hub = ContextHub()

            # Pull graph nodes as context
            if graph_registry:
                graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
                if graph:
                    try:
                        nodes = graph.get_all_nodes()
                        if nodes:
                            hub.add_nodes(nodes, label="Session Graph Nodes")
                    except Exception:
                        pass  # optional

            if format not in ("messages", "prompt", "markdown"):
                format = "messages"

            exported = hub.export(format)
            return {
                "format": format,
                "preview": exported,
                "count": len(hub),
                "estimated_tokens": hub.estimate_tokens(),
            }
        except Exception as e:
            return {"format": format, "preview": None, "count": 0, "error": str(e)}

    # ------------------------------------------------------------------
    # Context Lineage — derivation chain
    # ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/context/lineage")
    async def get_context_lineage(session_id: str, user=Depends(user_auth)):
        """Return the full lineage chain for a session's context."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        cfg = session.config or {}
        lineage = cfg.get("lineage", [])

        # Build summary breadcrumb
        breadcrumb_parts = []
        total_nodes = 0
        for entry in lineage:
            etype = entry.get("type", "unknown")
            if etype == "graph":
                count = entry.get("nodes_copied", 0)
                total_nodes += count
                breadcrumb_parts.append(f"context:{entry.get('source', '?')} ({count} nodes)")
            elif etype == "session":
                count = entry.get("nodes_copied", 0)
                total_nodes += count
                name = entry.get("source_session_name", entry.get("source_session_id", "?"))
                breadcrumb_parts.append(f"session:{name} ({count} nodes)")
            elif etype == "compose":
                count = entry.get("nodes_merged", 0)
                total_nodes += count
                sources = entry.get("source_names", entry.get("sources", []))
                breadcrumb_parts.append(f"composed from {len(sources)} sessions ({count} nodes)")
            elif etype == "overlay":
                breadcrumb_parts.append(f"overlay: {entry.get('label', 'text')}")
            elif etype == "file":
                breadcrumb_parts.append(f"file: {entry.get('filename', '?')}")

        return {
            "session_id": session_id,
            "lineage": lineage,
            "breadcrumb": " → ".join(breadcrumb_parts) if breadcrumb_parts else "No context sources yet",
            "total_sources": len(lineage),
        }

    # ------------------------------------------------------------------
    # Context Composition — merge from multiple sessions
    # ------------------------------------------------------------------

    @router.post("/sessions/{session_id}/context/compose")
    async def compose_context(
        session_id: str, req: dict = Body(...), user=Depends(user_auth)
    ):
        """Merge context from multiple sessions. Deduplicates by node name, highest sensitivity wins."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        if not graph_registry:
            raise HTTPException(status_code=503, detail="Graph registry not available")

        target_session = session_manager.get_session(session_id)
        if not target_session:
            raise HTTPException(status_code=404, detail="Target session not found")

        source_session_ids = req.get("source_session_ids", [])
        if not source_session_ids:
            raise HTTPException(status_code=400, detail="source_session_ids is required")

        # Ensure target graph exists
        dest = graph_registry.get_graph(target_session.graph_namespace, load_if_missing=True)
        if not dest:
            graph_registry.create_graph(target_session.graph_namespace)
            dest = graph_registry.get_graph(target_session.graph_namespace)
        if not dest:
            raise HTTPException(status_code=500, detail="Could not create target graph")

        # Sensitivity ordering for escalation
        sensitivity_order = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

        # Collect all nodes from sources, tracking by name for dedup
        seen_names = {}  # name -> (node_type, props, sensitivity_level)
        source_names = []
        total_from_sources = 0

        for src_id in source_session_ids:
            src_session = session_manager.get_session(src_id)
            if not src_session:
                continue
            source_names.append(src_session.name)
            src_graph = graph_registry.get_graph(src_session.graph_namespace, load_if_missing=True)
            if not src_graph:
                continue

            for n in src_graph.get_all_nodes():
                total_from_sources += 1
                if isinstance(n, dict):
                    ntype = n.get("label", n.get("node_type", "Entity"))
                    props = {k: v for k, v in n.items() if k not in ("uuid", "domain", "label", "node_type")}
                else:
                    ntype = getattr(n, "label", "Entity")
                    props = getattr(n, "properties", {}) or {}

                node_name = props.get("name", props.get("content", f"{ntype}_{id(n)}"))
                node_sens = props.get("sensitivity", "public")

                if node_name in seen_names:
                    # Dedup: keep highest sensitivity
                    existing_sens = seen_names[node_name][2]
                    if sensitivity_order.get(node_sens, 0) > sensitivity_order.get(existing_sens, 0):
                        props["sensitivity"] = node_sens
                        seen_names[node_name] = (ntype, props, node_sens)
                else:
                    seen_names[node_name] = (ntype, props, node_sens)

        # Copy deduplicated nodes to target
        merged = 0
        skipped = 0
        sensitivity_escalated = 0
        type_counts = {}
        for node_name, (ntype, props, sens) in seen_names.items():
            try:
                dest.add_node(node_type=ntype, properties=props)
                merged += 1
                type_counts[ntype] = type_counts.get(ntype, 0) + 1
            except Exception:
                skipped += 1

        duplicates_removed = total_from_sources - len(seen_names)

        _record_lineage(session_id, {
            "type": "compose",
            "sources": source_session_ids,
            "source_names": source_names,
            "nodes_merged": merged,
            "duplicates_removed": duplicates_removed,
            "sensitivity_escalated": sensitivity_escalated,
            "node_types": type_counts,
        })

        return {
            "status": "success",
            "nodes_merged": merged,
            "duplicates_removed": duplicates_removed,
            "sensitivity_escalated": sensitivity_escalated,
            "node_types": type_counts,
            "source_sessions": source_names,
            "session_id": session_id,
        }

    # ------------------------------------------------------------------
    # Context Security — bulk sensitivity + department tags
    # ------------------------------------------------------------------

    @router.post("/sessions/{session_id}/context/security")
    async def update_context_security(
        session_id: str, req: dict = Body(...), user=Depends(require_admin)
    ):
        """Apply default sensitivity and department tags (requires admin+)."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        if not graph_registry:
            raise HTTPException(status_code=503, detail="Graph registry not available")

        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        default_sensitivity = req.get("default_sensitivity", None)
        department_tags = req.get("department_tags", [])

        graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
        if not graph:
            return {"status": "success", "updated": 0, "message": "No graph data"}

        nodes = graph.get_all_nodes()
        updated = 0
        for n in nodes:
            if isinstance(n, dict):
                nid = n.get("id", n.get("uuid"))
                props = {k: v for k, v in n.items() if k not in ("uuid", "domain", "label", "node_type")}
            else:
                nid = getattr(n, "id", getattr(n, "uuid", None))
                props = getattr(n, "properties", {}) or {}

            changed = False
            # Apply sensitivity if not explicitly set
            if default_sensitivity and not props.get("_sensitivity_explicit"):
                props["sensitivity"] = default_sensitivity
                changed = True

            # Apply department tags
            if department_tags:
                existing_tags = props.get("tags", [])
                if isinstance(existing_tags, str):
                    existing_tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
                existing_dept = {t for t in existing_tags if t.startswith("dept:")}
                new_dept = set(department_tags)
                if new_dept != existing_dept:
                    non_dept = [t for t in existing_tags if not t.startswith("dept:")]
                    props["tags"] = non_dept + list(new_dept)
                    changed = True

            if changed and nid is not None:
                try:
                    graph.node_properties[nid] = props
                    updated += 1
                except Exception:
                    pass  # optional

        # Also store in session config for quick access
        cfg = session.config or {}
        cfg["default_sensitivity"] = default_sensitivity
        cfg["department_tags"] = department_tags
        try:
            session_manager._conn.execute(
                "UPDATE context_sessions SET config = ? WHERE session_id = ?",
                (_json.dumps(cfg), session_id),
            )
            session_manager._conn.commit()
        except Exception:
            pass  # optional

        return {
            "status": "success",
            "updated": updated,
            "default_sensitivity": default_sensitivity,
            "department_tags": department_tags,
        }

    # ------------------------------------------------------------------
    # Graph Node Types — for filter dropdowns
    # ------------------------------------------------------------------

    @router.get("/graphs/{name}/node-types")
    async def graph_node_types(name: str, user=Depends(user_auth)):
        """Return distinct node type labels in a graph (for filter dropdowns)."""
        tenant = _get_tenant(user)
        graph, _ = _resolve_graph(tenant, name)
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")

        nodes = graph.get_all_nodes()
        type_counts = {}
        for n in nodes:
            if isinstance(n, dict):
                ntype = n.get("label", n.get("node_type", "Entity"))
            else:
                ntype = getattr(n, "label", "Entity")
            type_counts[ntype] = type_counts.get(ntype, 0) + 1

        return {
            "name": name,
            "node_types": [{"type": t, "count": c} for t, c in sorted(type_counts.items())],
        }

    @router.get("/graphs/{name}/nodes")
    async def graph_nodes_by_label(name: str, label: str = Query(""), limit: int = Query(50), user=Depends(user_auth)):
        """Return nodes filtered by label."""
        tenant = _get_tenant(user)
        graph, _ = _resolve_graph(tenant, name)
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")
        if label:
            nodes = graph.get_all_nodes(label=label)
        else:
            nodes = graph.get_all_nodes()
        result = []
        for n in nodes[:limit]:
            props = n.properties if hasattr(n, 'properties') else {}
            result.append({"id": n.id if hasattr(n, 'id') else "", "label": n.label if hasattr(n, 'label') else "", "properties": props})
        return {"nodes": result}

    @router.post("/graphs/{name}/rebuild-cus")
    async def rebuild_context_units(name: str, bg: BackgroundTasks, user=Depends(require_member)):
        """Delete existing CUs and rebuild from all facts using graph-aware clustering."""
        tenant = _get_tenant(user)
        graph, _ = _resolve_graph(tenant, name)
        if not graph:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")

        def _rebuild():
            try:
                from ..context.context_units import cluster_facts_for_cus, build_context_unit
                from ..core.hybrid_graph_storage import GraphNode, GraphEdge
                import uuid as _uuid

                # Delete old CUs via AIQL
                try:
                    from ..aiql import AIQLExecutor
                    ex = AIQLExecutor(contextcore=graph)
                    ex.execute("DELETE NODE ContextUnit")
                except Exception:
                    pass

                # Load all facts
                facts = [{"id": n.id, "label": "Fact", "properties": n.properties or {}}
                         for n in graph.get_all_nodes(label="Fact") if (n.properties or {}).get("statement")]
                if len(facts) < 3:
                    return

                clusters = cluster_facts_for_cus(facts, min_cluster_size=3, db=graph)
                for c in clusters:
                    cu = build_context_unit(topic=c["topic"], facts=c["facts"], entities=[])
                    graph.add_node(GraphNode(id=cu["id"], label="ContextUnit", properties=cu["properties"]))
                    for fid in cu["evidence_ids"]:
                        graph.add_edge(GraphEdge(id=str(_uuid.uuid4()), source=cu["id"], target=fid, label="HAS_EVIDENCE", properties={}))

                logger.info("[CU] Rebuilt %d CUs from %d facts for graph %s", len(clusters), len(facts), name)
            except Exception as e:
                logger.error("[CU] Rebuild failed for %s: %s", name, e)

        bg.add_task(_rebuild)
        return {"status": "rebuilding", "graph": name}

    # ------------------------------------------------------------------
    # PATCH /dashboard/settings
    # ------------------------------------------------------------------
    @router.patch("/settings")
    async def update_settings(req: UpdateSettingsRequest, user=Depends(user_auth)):
        """Update user profile settings."""
        try:
            updated = user_registry.update(
                user.user_id,
                display_name=req.display_name,
                password=req.password,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not updated:
            raise HTTPException(status_code=404, detail="User not found")

        return updated.to_dict()

    # ------------------------------------------------------------------
    # GET /dashboard/team — list members + invitations
    # ------------------------------------------------------------------
    @router.get("/team")
    async def get_team(user=Depends(user_auth)):
        """List team members and pending invitations."""
        tenant = _get_tenant(user)
        memberships = user_registry.get_tenant_members(tenant.tenant_id)

        members = []
        for m in memberships:
            u = user_registry.get(m.user_id)
            members.append({
                "user_id": m.user_id,
                "email": u.email if u else "unknown",
                "display_name": u.display_name if u else "unknown",
                "role": m.role,
                "joined_at": m.joined_at,
            })

        invitations = user_registry.list_invitations(tenant.tenant_id)

        return {"members": members, "invitations": invitations}

    # ------------------------------------------------------------------
    # POST /dashboard/team/invite — invite a member
    # ------------------------------------------------------------------
    @router.post("/team/invite", status_code=201)
    async def invite_member(req: InviteMemberRequest, user=Depends(require_admin)):
        """Invite a user to the workspace (requires admin+)."""
        tenant = _get_tenant(user)

        if req.role not in ("admin", "contributor", "reader"):
            raise HTTPException(status_code=400, detail="Role must be admin, contributor, or reader")

        try:
            invite = user_registry.create_invitation(
                tenant_id=tenant.tenant_id,
                email=req.email,
                role=req.role,
                invited_by=user.user_id,
            )
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

        return invite

    # ------------------------------------------------------------------
    # DELETE /dashboard/team/invite/{invite_id} — revoke invitation
    # ------------------------------------------------------------------
    @router.delete("/team/invite/{invite_id}")
    async def revoke_invite(invite_id: str, user=Depends(require_admin)):
        """Revoke a pending invitation."""
        if not user_registry.revoke_invitation(invite_id):
            raise HTTPException(status_code=404, detail="Invitation not found or already used")
        return {"status": "revoked"}

    # ------------------------------------------------------------------
    # PATCH /dashboard/team/{user_id}/role — update member role
    # ------------------------------------------------------------------
    @router.patch("/team/{member_id}/role")
    async def update_role(member_id: str, req: UpdateMemberRoleRequest, user=Depends(require_owner)):
        """Update a team member's role (requires owner)."""
        tenant = _get_tenant(user)

        if req.role not in ("admin", "contributor", "reader"):
            raise HTTPException(status_code=400, detail="Role must be admin, contributor, or reader")

        if not user_registry.update_member_role(tenant.tenant_id, member_id, req.role):
            raise HTTPException(status_code=400, detail="Cannot change owner role or member not found")

        return {"user_id": member_id, "role": req.role}

    # ------------------------------------------------------------------
    # DELETE /dashboard/team/{user_id} — remove member
    # ------------------------------------------------------------------
    @router.delete("/team/{member_id}")
    async def remove_member(member_id: str, user=Depends(require_owner)):
        """Remove a member from the workspace (requires owner)."""
        tenant = _get_tenant(user)

        if not user_registry.remove_member(tenant.tenant_id, member_id):
            raise HTTPException(status_code=400, detail="Cannot remove owner or member not found")

        return {"status": "removed"}

    # ------------------------------------------------------------------
    # Super Admin — platform-wide user management
    # ------------------------------------------------------------------
    @router.patch("/admin/users/{target_user_id}/super-admin")
    async def set_super_admin(target_user_id: str, req: dict = Body(...), user=Depends(require_super_admin)):
        """Promote/demote a user to super_admin (requires super_admin)."""
        enable = req.get("is_super_admin", True)
        if not user_registry.set_super_admin(target_user_id, enable):
            raise HTTPException(status_code=404, detail="User not found")
        return {"user_id": target_user_id, "is_super_admin": enable}

    @router.get("/admin/users")
    async def list_all_users(user=Depends(require_super_admin)):
        """List all users across the platform (super_admin only)."""
        rows = user_registry._conn.execute(
            "SELECT * FROM users WHERE status != 'deleted' ORDER BY created_at DESC"
        ).fetchall()
        users = []
        for r in rows:
            u = user_registry._row_to_user(r)
            tenants = user_registry.get_user_tenants(u.user_id)
            users.append({
                **u.to_dict(),
                "tenants": [{"tenant_id": t.tenant_id, "role": t.role} for t in tenants],
            })
        return {"users": users, "total": len(users)}

    @router.patch("/admin/users/{target_user_id}/role")
    async def admin_set_role(target_user_id: str, req: dict = Body(...), user=Depends(require_super_admin)):
        """Set a user's role in any org (super_admin only). Adds user to org if not already a member."""
        tenant_id = req.get("tenant_id")
        role = req.get("role")
        if not tenant_id or role not in ("reader", "contributor", "admin", "owner"):
            raise HTTPException(400, "Provide tenant_id and role (reader/contributor/admin/owner)")
        target = user_registry.get(target_user_id)
        if not target:
            raise HTTPException(404, "User not found")
        # link_tenant does INSERT OR REPLACE — works for both add and update
        user_registry.link_tenant(target_user_id, tenant_id, role)
        return {"user_id": target_user_id, "tenant_id": tenant_id, "role": role}

    # ------------------------------------------------------------------
    # Ingestion endpoints
    # ------------------------------------------------------------------

    @router.get("/ingest/pipelines")
    async def list_ingest_pipelines(user=Depends(user_auth)):
        """Return available smart ingestion pipelines."""
        try:
            from ..ingestion.smart_ingest import list_pipelines
            return {"pipelines": list_pipelines()}
        except Exception:
            return {"pipelines": {}}

    @router.get("/ingest/pipeline-templates")
    async def list_all_pipelines(user=Depends(user_auth)):
        """Return all ingestion pipeline templates (builtin + custom). Used by ContextsPage."""
        try:
            from ..ingestion.pipeline_store import PipelineStore
            store = PipelineStore()
            tenant = _get_tenant(user)
            pipelines = store.list_pipelines(tenant.tenant_id)
            return {"pipelines": pipelines}
        except Exception as e:
            # Fallback: return smart pipelines as builtin list
            try:
                from ..ingestion.pipeline_store import BUILTIN_PIPELINES
                return {"pipelines": list(BUILTIN_PIPELINES)}
            except Exception:
                return {"pipelines": []}

    @router.post("/ingest/pipeline-templates", status_code=201)
    async def create_pipeline_template(req: dict = Body(...), user=Depends(require_member)):
        """Create a custom pipeline template."""
        from ..ingestion.pipeline_store import PipelineStore
        store = PipelineStore()
        tenant = _get_tenant(user)
        pipeline_id = store.create_pipeline(
            tenant_id=tenant.tenant_id,
            name=req.get("name", "Untitled"),
            description=req.get("description", ""),
            stages=req.get("stages", []),
            aiql_template=req.get("aiql_template", ""),
            default_params=req.get("default_params", {}),
            tags=req.get("tags", []),
            category=req.get("category", "custom"),
        )
        return {"id": pipeline_id, "status": "created"}

    @router.put("/ingest/pipeline-templates/{pipeline_id}")
    async def update_pipeline_template(pipeline_id: str, req: dict = Body(...), user=Depends(require_member)):
        """Update a custom pipeline template."""
        from ..ingestion.pipeline_store import PipelineStore
        store = PipelineStore()
        pipeline = store.get_pipeline(pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        if pipeline.get("builtin"):
            raise HTTPException(status_code=403, detail="Cannot modify built-in pipelines")
        store.update_pipeline(pipeline_id, **{
            k: v for k, v in req.items()
            if k in ("name", "description", "stages", "aiql_template", "default_params", "tags", "category")
        })
        return {"id": pipeline_id, "status": "updated"}

    @router.delete("/ingest/pipeline-templates/{pipeline_id}")
    async def delete_pipeline_template(pipeline_id: str, user=Depends(require_member)):
        """Delete a custom pipeline template."""
        from ..ingestion.pipeline_store import PipelineStore
        store = PipelineStore()
        pipeline = store.get_pipeline(pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        if pipeline.get("builtin"):
            raise HTTPException(status_code=403, detail="Cannot delete built-in pipelines")
        store.delete_pipeline(pipeline_id)
        return {"status": "deleted"}

    # ------------------------------------------------------------------
    # Job-based ingestion endpoints
    # ------------------------------------------------------------------

    @router.post("/ingest/submit")
    async def submit_ingest_job(req: dict = Body(...), user=Depends(require_member)):
        """Submit an ingestion job (runs in background). Returns job_id immediately."""
        tenant = _get_tenant(user)
        url = req.get("url", "")
        text = req.get("text", "")
        graph_name = req.get("graph", "")
        pipeline_name = req.get("pipeline", "smart_article")
        mode = req.get("mode", "auto")

        if not url and not text:
            raise HTTPException(status_code=400, detail="URL or text required")

        scoped = _scope_graph(tenant, graph_name)
        db = graph_registry.get_graph(scoped, load_if_missing=True) or graph_registry.create_graph(scoped)

        from ..ingestion.job_manager import get_job_manager
        from ..ingestion.smart_ingest import ingest_url, ingest_text, ingest_crawl
        from ..ingestion.filters import FilterConfig, SemanticRule

        # Build filter config
        fc = None
        filters = req.get("filters")
        if filters:
            sem_rules = [SemanticRule(query=r.get("query", ""), direction=r.get("direction", "include"),
                                      threshold=r.get("threshold", 0.6)) for r in filters.get("semantic_rules", [])]
            fc = FilterConfig(
                keywords_include=filters.get("keywords_include", []),
                keywords_exclude=filters.get("keywords_exclude", []),
                keywords_include_mode=filters.get("keywords_include_mode", "or"),
                categories_include=filters.get("categories_include", []),
                semantic_query=filters.get("semantic_query", ""),
                semantic_rules=sem_rules,
            )

        jm = get_job_manager()
        crawl = req.get("crawl", False)
        max_pages = min(req.get("max_pages", 20), 50)

        if url and crawl:
            # Crawl mode — crawl website, filter each page, ingest matching
            logger.info("[CRAWL SUBMIT] url=%s filters=%s fc=%s", url[:50], filters is not None, fc is not None)
            job = jm.submit(
                "ingest_crawl", ingest_crawl,
                url, db,
                pipeline=pipeline_name, mode="auto", context_id=scoped,
                input_summary=f"Crawl {url} (max {max_pages} pages)",
                max_pages=max_pages, filter_config=fc,
                strategy=req.get("strategy", "news_article"),
                owner=tenant.tenant_id,
            )
        elif url:
            job = jm.submit(
                "ingest_url", ingest_url,
                url, db,
                pipeline=pipeline_name, mode=mode, context_id=scoped,
                input_summary=url,
                filter_config=fc,
                owner=tenant.tenant_id,
            )
        else:
            job = jm.submit(
                "ingest_text", ingest_text,
                text, db,
                pipeline=pipeline_name, mode=mode, context_id=scoped,
                input_summary=text[:100],
                title=req.get("title", ""), filter_config=fc,
                owner=tenant.tenant_id,
            )

        return {"status": "submitted", "job_id": job.job_id, "job": job.to_dict()}

    @router.get("/ingest/jobs")
    async def list_ingest_jobs(context_id: str = "", user=Depends(user_auth)):
        """List ingestion jobs from both job systems, optionally filtered by context."""
        tenant = _get_tenant(user)

        # Source 1: job_manager (used by /ingest/submit)
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        jm_jobs = jm.list_jobs(context_id=context_id)

        # Source 2: _ingest_jobs dict (used by /ingest/url smart pipeline)
        legacy_jobs = sorted(
            _ingest_jobs.values(),
            key=lambda j: j.get("created_at", ""),
            reverse=True,
        )
        legacy_jobs = [j for j in legacy_jobs if j.get("tenant_id") == tenant.tenant_id]
        if context_id:
            legacy_jobs = [j for j in legacy_jobs if j.get("context_id") == context_id]

        # Merge by job_id (avoid duplicates)
        seen = {j.get("job_id") for j in jm_jobs}
        merged = list(jm_jobs)
        for j in legacy_jobs:
            if j.get("job_id") not in seen:
                merged.append(j)

        return {"jobs": merged[:50]}

    @router.get("/ingest/jobs/{job_id}")
    async def get_ingest_job(job_id: str, user=Depends(user_auth)):
        """Get status of a specific job."""
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        job = jm.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        return {"job": job.to_dict()}

    @router.get("/ingest/jobs/{job_id}/logs")
    async def get_job_logs(
        job_id: str,
        stage: str = Query("", description="Filter by stage name"),
        level: str = Query("", description="Filter by level: info, debug, error"),
        limit: int = Query(100, description="Max entries to return"),
        offset: int = Query(0, description="Skip entries"),
        user=Depends(user_auth),
    ):
        """Get filtered log entries for a specific job.

        Supports filtering by stage and level, with pagination.
        Used by the frontend StepWisePipelinePanel to show per-stage details.
        """
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        if not jm:
            return {"logs": [], "total": 0}

        job = jm.get_job(job_id)
        if not job:
            raise HTTPException(404, f"Job '{job_id}' not found")

        logs = list(job.log)

        # Filter by stage
        if stage:
            logs = [e for e in logs if e.stage == stage]

        # Filter by level
        if level:
            logs = [e for e in logs if e.level == level]

        total = len(logs)
        # Paginate
        logs = logs[offset:offset + limit]

        return {
            "logs": [e.to_dict() for e in logs],
            "total": total,
            "job_id": job_id,
            "filters": {"stage": stage, "level": level},
        }

    @router.post("/ingest/jobs/{job_id}/accept-all")
    async def accept_all_sub_jobs(job_id: str, user=Depends(require_member)):
        """Accept all pending sub-jobs in a review job."""
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        count = jm.accept_all(job_id)
        return {"accepted": count}

    @router.post("/ingest/jobs/{job_id}/sub/{sub_job_id}/accept")
    async def accept_sub_job(job_id: str, sub_job_id: str, user=Depends(require_member)):
        """Accept a single sub-job."""
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        ok = jm.accept_sub_job(job_id, sub_job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Sub-job not found")
        return {"status": "accepted"}

    @router.post("/ingest/jobs/{job_id}/sub/{sub_job_id}/reject")
    async def reject_sub_job(job_id: str, sub_job_id: str, user=Depends(require_member)):
        """Reject a single sub-job."""
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        ok = jm.reject_sub_job(job_id, sub_job_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Sub-job not found")
        return {"status": "rejected"}

    @router.post("/ingest/jobs/{job_id}/commit")
    async def commit_reviewed_job(job_id: str, req: dict = Body({}), user=Depends(require_member)):
        """Commit accepted sub-jobs to graph."""
        tenant = _get_tenant(user)
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        job = jm.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")

        db = graph_registry.get_graph(job.context_id, load_if_missing=True)
        if not db:
            raise HTTPException(status_code=404, detail="Context graph not found")

        result = jm.commit_reviewed(job_id, db)
        if "error" in result:
            raise HTTPException(status_code=500, detail=result["error"])
        return {"status": "completed", "result": result}

    # ------------------------------------------------------------------
    # Job management: clear + retry
    # ------------------------------------------------------------------

    @router.delete("/ingest/jobs/{job_id}")
    async def delete_ingest_job(job_id: str, force: bool = False, user=Depends(require_member)):
        """Delete/clear a job. Use ?force=true to cancel stuck running jobs."""
        from ..ingestion.job_manager import get_job_manager, JobStatus
        jm = get_job_manager()
        job = jm.get_job(job_id)
        if job:
            if job.status.value in ("running", "queued") and not force:
                raise HTTPException(status_code=409, detail="Cannot delete a running job. Use ?force=true to force cancel.")
            if job.status.value in ("running", "queued") and force:
                # Force cancel: mark as failed
                job.status = JobStatus.FAILED
                job.error = "Force cancelled by user"
                from datetime import datetime, timezone
                job.completed_at = datetime.now(timezone.utc).isoformat()
                job.add_log("system", "Force cancelled by user from dashboard")
                jm._active_count = max(0, jm._active_count - 1)
                jm._save_to_redis(job)
            jm.remove_job(job_id)

        # Remove from _ingest_jobs dict
        if job_id in _ingest_jobs:
            if _ingest_jobs[job_id].get("status") in ("processing",) and not force:
                raise HTTPException(status_code=409, detail="Cannot delete a running job. Use ?force=true to force cancel.")
            del _ingest_jobs[job_id]

        return {"status": "deleted" if not force else "force_cancelled", "job_id": job_id}

    @router.post("/ingest/jobs/clear-stuck")
    async def clear_stuck_jobs(user=Depends(require_member)):
        """Force-cancel all stuck running/queued jobs."""
        from ..ingestion.job_manager import get_job_manager, JobStatus
        from datetime import datetime, timezone
        jm = get_job_manager()
        cleared = 0
        now = datetime.now(timezone.utc).isoformat()
        for job in list(jm._jobs.values()):
            if job.status.value in ("running", "queued"):
                job.status = JobStatus.FAILED
                job.error = "Force cancelled — cleared stuck jobs"
                job.completed_at = now
                jm._save_to_redis(job)
                cleared += 1
        jm._active_count = 0

        # Also clear in Redis
        if jm._redis:
            try:
                import json
                data = jm._redis.hgetall("contextcore:jobs")
                for jid, jjson in data.items():
                    d = json.loads(jjson)
                    if d.get("status") in ("running", "queued"):
                        d["status"] = "failed"
                        d["error"] = "Force cancelled — cleared stuck jobs"
                        d["completed_at"] = now
                        jm._redis.hset("contextcore:jobs", jid, json.dumps(d))
                        cleared += 1
            except Exception:
                pass

        return {"status": "cleared", "cancelled": cleared}

    @router.delete("/ingest/jobs")
    async def clear_finished_jobs(user=Depends(require_member)):
        """Clear all completed and failed jobs."""
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        cleared = jm.clear_finished()

        # Clear from _ingest_jobs dict
        to_remove = [jid for jid, j in _ingest_jobs.items()
                     if j.get("status") in ("completed", "completed_empty", "failed")]
        for jid in to_remove:
            del _ingest_jobs[jid]

        return {"status": "cleared", "removed": cleared + len(to_remove)}

    @router.post("/ingest/jobs/{job_id}/retry")
    async def retry_ingest_job(job_id: str, user=Depends(require_member)):
        """Retry a failed job by re-submitting with the same parameters."""
        tenant = _get_tenant(user)

        # Check _ingest_jobs first (smart pipeline jobs)
        if job_id in _ingest_jobs:
            old = _ingest_jobs[job_id]
            if old.get("status") not in ("failed", "completed_empty", "completed"):
                raise HTTPException(status_code=409, detail=f"Job status is '{old.get('status')}', can only retry failed/completed jobs")

            source = old.get("source", "")
            graph = old.get("graph", "")
            scoped = old.get("context_id", "")
            debug = old.get("debug", False)

            if not source or not scoped:
                raise HTTPException(status_code=400, detail="Job missing source/context — cannot retry")

            # Re-create as a new job
            new_job_id = _create_job(source, graph, old.get("llm_model", ""), old.get("embedding_model", ""),
                                     tenant_id=tenant.tenant_id,
                                     stage_names=["clean", "chunk", "extract", "link", "embed", "index", "validate"])
            if debug and new_job_id in _ingest_jobs:
                _ingest_jobs[new_job_id]["debug"] = True

            def _run_retry(job_id, scoped, url, pipeline_name, debug_flag=debug):
                try:
                    if job_id in _ingest_jobs:
                        _ingest_jobs[job_id]["status"] = "running"

                    def _on_stage(stage_name, details=None):
                        base = stage_name.split("_")[0] if "_" in stage_name else stage_name
                        status = "completed" if stage_name.endswith("_done") or stage_name.endswith("_failed") else "running"
                        count = (details or {}).get("count", 0)
                        _update_stage(job_id, base, status, count)

                    from ..ingestion.smart_ingest import ingest_url as smart_ingest_url
                    reg = graph_registry
                    db = reg.get_graph(scoped, load_if_missing=True) or reg.create_graph(scoped)
                    result = smart_ingest_url(url, db, pipeline=pipeline_name,
                                             on_stage=_on_stage, debug=debug_flag,
                                             owner=tenant.tenant_id if tenant else "")
                    reg.save_graph(scoped, create_checkpoint=False)
                    _finish_job(
                        job_id,
                        nodes_created=len(result.passage_ids) + len(result.entity_ids) + len(result.fact_ids) + 1,
                        edges_created=result.edge_count,
                        entities_extracted=len(result.entity_ids),
                    )
                except Exception as e:
                    _finish_job(job_id, nodes_created=0, error=str(e))

            _submit_ingest(new_job_id, _run_retry, new_job_id, scoped, source, "smart_article")
            # Remove old job
            del _ingest_jobs[job_id]
            return {"status": "retrying", "old_job_id": job_id, "new_job_id": new_job_id}

        # Check job_manager
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        job = jm.get_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        if job.status.value not in ("failed",):
            raise HTTPException(status_code=409, detail=f"Job status is '{job.status.value}', can only retry failed jobs")

        raise HTTPException(status_code=501, detail="Retry not supported for this job type yet")

    # ------------------------------------------------------------------

    @router.post("/ingest/preview")
    async def preview_ingest(req: dict = Body(...), user=Depends(require_member)):
        """Preview ingestion results before committing (review mode)."""
        tenant = _get_tenant(user)
        url = req.get("url", "")
        text = req.get("text", "")
        graph_name = req.get("graph", "")
        pipeline_name = req.get("pipeline", "smart_article")

        scoped = _scope_graph(tenant, graph_name)
        reg = graph_registry
        db = reg.get_graph(scoped, load_if_missing=True) or reg.create_graph(scoped)

        try:
            from ..ingestion.smart_ingest import ingest_url, ingest_text, StagingResult
            from ..ingestion.filters import FilterConfig, SemanticRule

            # Build filter config from request
            fc = None
            filters = req.get("filters")
            if filters:
                # Parse semantic rules
                sem_rules = []
                for r in filters.get("semantic_rules", []):
                    sem_rules.append(SemanticRule(
                        query=r.get("query", ""),
                        direction=r.get("direction", "include"),
                        threshold=r.get("threshold", 0.6),
                    ))
                fc = FilterConfig(
                    keywords_include=filters.get("keywords_include", []),
                    keywords_exclude=filters.get("keywords_exclude", []),
                    keywords_include_mode=filters.get("keywords_include_mode", "or"),
                    categories_include=filters.get("categories_include", []),
                    semantic_query=filters.get("semantic_query", ""),
                    semantic_auto_threshold=filters.get("semantic_auto_threshold", 0.7),
                    semantic_review_threshold=filters.get("semantic_review_threshold", 0.4),
                    semantic_rules=sem_rules,
                )

            if url:
                result = ingest_url(url, db, mode="review", pipeline=pipeline_name, filter_config=fc)
            elif text:
                result = ingest_text(text, db, title=req.get("title", ""), mode="review",
                                     pipeline=pipeline_name, filter_config=fc)
            else:
                raise HTTPException(status_code=400, detail="URL or text required")

            if isinstance(result, StagingResult):
                return {"status": "staged", "staging_id": result.staging_id, "preview": result.to_dict()}
            return {"status": "completed", "result": {"errors": getattr(result, 'errors', [])}}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Preview failed: {e}")

    @router.post("/ingest/commit")
    async def commit_ingest(req: dict = Body(...), user=Depends(require_member)):
        """Commit a previously staged preview to the graph."""
        tenant = _get_tenant(user)
        staging_id = req.get("staging_id", "")
        exclude_ids = req.get("exclude_ids", [])
        graph_name = req.get("graph", "")

        if not staging_id:
            raise HTTPException(status_code=400, detail="staging_id required")

        from ..ingestion.smart_ingest import get_staging
        staging = get_staging(staging_id)
        if not staging:
            raise HTTPException(status_code=404, detail="Staging not found (may have expired)")

        scoped = _scope_graph(tenant, graph_name)
        reg = graph_registry
        db = reg.get_graph(scoped, load_if_missing=True) or reg.create_graph(scoped)

        try:
            result = staging.commit(db, exclude_ids=exclude_ids)
            return {
                "status": "completed",
                "result": {
                    "document_id": result.document_id,
                    "passages": len(result.passage_ids),
                    "entities": len(result.entity_ids),
                    "facts": len(result.fact_ids),
                    "edges": result.edge_count,
                },
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Commit failed: {e}")

    @router.post("/ingest/discard")
    async def discard_ingest(req: dict = Body(...), user=Depends(require_member)):
        """Discard a staged preview."""
        staging_id = req.get("staging_id", "")
        from ..ingestion.smart_ingest import get_staging
        staging = get_staging(staging_id)
        if staging:
            staging.discard()
        return {"status": "discarded"}

    @router.post("/contexts/{context_id}/reprocess")
    async def reprocess_context_endpoint(context_id: str, user=Depends(require_member)):
        """Re-process an existing context through the smart pipeline."""
        tenant = _get_tenant(user)
        try:
            from ..ingestion.reprocess import reprocess_context
            reg = graph_registry
            # Try context namespace directly or scoped
            db = reg.get_graph(context_id, load_if_missing=True)
            if not db:
                scoped = _scope_graph(tenant, context_id)
                db = reg.get_graph(scoped, load_if_missing=True)
            if not db:
                raise HTTPException(status_code=404, detail="Context graph not found")

            preview = reprocess_context(context_id, db, graph_registry=reg)
            return {"status": "staged", "preview": preview.to_dict()}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Reprocess failed: {e}")

    @router.post("/contexts/{context_id}/reprocess/commit")
    async def commit_reprocess(context_id: str, req: dict = Body(...), user=Depends(require_member)):
        """Commit a re-process preview."""
        staging_id = req.get("staging_id", "")
        from ..ingestion.smart_ingest import get_staging
        staging = get_staging(staging_id)
        if not staging:
            raise HTTPException(status_code=404, detail="Staging not found")

        tenant = _get_tenant(user)
        reg = graph_registry
        db = reg.get_graph(context_id, load_if_missing=True)
        if not db:
            scoped = _scope_graph(tenant, context_id)
            db = reg.get_graph(scoped, load_if_missing=True)

        from ..ingestion.reprocess import ReprocessPreview
        # Build minimal reprocess preview to commit
        rp = ReprocessPreview(context_id=context_id, staging_id=staging_id, after=staging)
        result = rp.commit(db)
        return {"status": "completed", "result": {
            "document_id": getattr(result, 'document_id', ''),
            "passages": len(getattr(result, 'passage_ids', [])),
            "entities": len(getattr(result, 'entity_ids', {})),
            "facts": len(getattr(result, 'fact_ids', [])),
            "edges": getattr(result, 'edge_count', 0),
        } if result else {}}

    @router.post("/contexts/{context_id}/cleanse")
    async def cleanse_context_endpoint(context_id: str, user=Depends(require_member)):
        """Cleanse an existing context — dedup, prune, remove orphans, revalidate, reindex."""
        tenant = _get_tenant(user)
        try:
            from ..ingestion.cleanse import cleanse_context
            from ..ingestion.schema_extractor import load_schema
            db = graph_registry.get_graph(context_id, load_if_missing=True)
            if not db:
                scoped = _scope_graph(tenant, context_id)
                db = graph_registry.get_graph(scoped, load_if_missing=True)
            if not db:
                raise HTTPException(status_code=404, detail="Context graph not found")
            schema = load_schema(db)
            result = cleanse_context(db, schema=schema)
            return {"status": "completed", "result": result.to_dict(), "summary": result.summary}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Cleanse failed: {e}")

    @router.get("/ingest/models")
    async def list_ingest_models(user=Depends(user_auth)):
        """Return available LLM and embedding models for ingestion."""
        _get_tenant(user)
        llm_models = []
        embedding_models = []

        # LLM models — detect from configured API keys
        if _os.environ.get("GROQ_API_KEY"):
            llm_models.append("groq:gpt-oss-120b")
            llm_models.append("groq:gpt-oss-20b")
        if _os.environ.get("OPENAI_API_KEY"):
            llm_models.append("openai:gpt-4o-mini")
            llm_models.append("openai:gpt-4o")
        if _os.environ.get("ANTHROPIC_API_KEY"):
            llm_models.append("anthropic:claude-sonnet-4-20250514")
            llm_models.append("anthropic:claude-haiku-4-5-20251001")
        if _os.environ.get("MISTRAL_API_KEY"):
            llm_models.append("mistral:mistral-large-latest")
        if _os.environ.get("DEEPSEEK_API_KEY"):
            llm_models.append("deepseek:deepseek-chat")
        # Ollama (local) — dynamically list available models
        ollama_url = _os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        _ollama_llm = []
        _ollama_emb = []
        try:
            import urllib.request, json as _j
            resp = urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=2)
            tags = _j.loads(resp.read())
            for m in tags.get("models", []):
                name = m["name"].split(":")[0]
                # Embedding models
                if "embed" in name or "nomic" in name:
                    _ollama_emb.append(f"ollama:{name}")
                else:
                    _ollama_llm.append(f"ollama:{name}")
        except Exception:
            pass  # optional
        llm_models.extend(_ollama_llm)

        # Embedding models
        if _os.environ.get("OPENAI_API_KEY"):
            embedding_models.append("openai:text-embedding-3-small")
            embedding_models.append("openai:text-embedding-3-large")
        embedding_models.extend(_ollama_emb)

        return {
            "llm_models": llm_models,
            "embedding_models": embedding_models,
            "llm_available": len(llm_models) > 0,
            "embedding_available": len(embedding_models) > 0,
        }

    @router.post("/ingest/text")
    async def ingest_text(
        bg: BackgroundTasks,
        req: dict = Body(...),
        user=Depends(require_member),
    ):
        """Ingest text into the graph (requires member+)."""
        tenant = _get_tenant(user)
        text = req.get("text", "")
        title = req.get("title", "Untitled")
        graph_name = req.get("graph", "")
        pipeline_id = req.get("pipeline_id") or None
        schema_yaml = req.get("schema") or None

        # Resolve pipeline → LLM/embedding params + auto-load schema
        llm_model, embedding_model = _resolve_pipeline_params(
            pipeline_id, req.get("llm_model"), req.get("embedding_model"))
        schema_yaml = _resolve_pipeline_schema(pipeline_id, schema_yaml)

        if not text.strip():
            raise HTTPException(status_code=400, detail="Text is required")

        scoped = _scope_graph(tenant, graph_name)
        display = scoped.split(":", 1)[-1]

        # Build stage list for progress tracking
        if llm_model or embedding_model:
            _stages = ["PARSE_FILE", "CHUNK"]
            if llm_model:
                _stages.extend(["EXTRACT_FACTS", "EXTRACT"])
            if embedding_model:
                _stages.append("EMBED")
            if llm_model:
                _stages.append("INDEX_BM25")
            if embedding_model:
                _stages.append("STORE_VECTORS")
            if llm_model:
                _stages.extend(["CANONICALIZE", "ENHANCE_GRAPH"])
            _stages.append("PERSIST")
        else:
            _stages = ["CHUNK", "PERSIST"]

        job_id = _create_job(title, display, llm_model, embedding_model,
                             tenant_id=tenant.tenant_id, stage_names=_stages)

        if llm_model or embedding_model:
            _submit_ingest(job_id, _ingest_pipeline, job_id, scoped, text.strip(),
                        {"title": title, "source": "text_input"},
                        llm_model, embedding_model, schema_yaml)
        else:
            chunks = _chunk_text(text.strip())
            doc_id = str(_uuid.uuid4())
            nodes = [
                GraphNode(
                    id=doc_id, label="Document",
                    properties={
                        "name": title or "Untitled",
                        "source": "text_input",
                        "char_count": len(text.strip()),
                        "chunk_count": len(chunks),
                    },
                ),
            ]
            edges = []
            for i, chunk in enumerate(chunks):
                chunk_id = str(_uuid.uuid4())
                nodes.append(GraphNode(
                    id=chunk_id, label="TextChunk",
                    properties={
                        "name": f"{title} (chunk {i + 1}/{len(chunks)})" if len(chunks) > 1 else title,
                        "content": chunk, "source": "text_input",
                        "char_count": len(chunk), "chunk_index": i,
                    },
                ))
                edges.append(GraphEdge(
                    id=str(_uuid.uuid4()), source=doc_id, target=chunk_id,
                    label="CONTAINS", properties={"chunk_index": i},
                ))
            _submit_ingest(job_id, _ingest_nodes, job_id, scoped, nodes, edges)

        return {"status": "accepted", "job_id": job_id, "graph": display}

    @router.post("/ingest/file")
    async def ingest_file(
        bg: BackgroundTasks,
        file: UploadFile = File(...),
        graph: str = Form(""),
        pipeline_id: str = Form(""),
        llm_model: str = Form(""),
        embedding_model: str = Form(""),
        graph_schema: str = Form(""),
        user=Depends(require_member),
    ):
        """Upload a file and ingest (requires member+)."""
        schema = graph_schema  # renamed to avoid Pydantic shadow warning
        tenant = _get_tenant(user)

        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        content = await file.read()

        scoped = _scope_graph(tenant, graph)
        display = scoped.split(":", 1)[-1]
        # Resolve pipeline → LLM/embedding params
        _pid = pipeline_id.strip() or None
        _llm, _emb = _resolve_pipeline_params(
            _pid, llm_model.strip() or None, embedding_model.strip() or None)
        _schema = _resolve_pipeline_schema(_pid, schema.strip() or None)

        # Auto-detect chat exports (ZIP/JSON) and apply chat_history schema
        _ext = Path(file.filename).suffix.lower() if file.filename else ""
        _is_chat_export = False
        if _ext in (".zip", ".json") and not _schema:
            try:
                if _ext == ".zip":
                    import zipfile as _zf, io as _io
                    with _zf.ZipFile(_io.BytesIO(content)) as zf:
                        _is_chat_export = any("conversations" in n.lower() and n.endswith(".json") for n in zf.namelist())
                elif _ext == ".json":
                    import json as _json_detect
                    _sample = _json_detect.loads(content.decode("utf-8", errors="replace")[:5000])
                    _items = _sample if isinstance(_sample, list) else _sample.get("conversations", [])
                    if _items and isinstance(_items[0], dict) and ("mapping" in _items[0] or "chat_messages" in _items[0]):
                        _is_chat_export = True
            except Exception:
                pass
            if _is_chat_export:
                _schema = "chat_history"
                logger.info("[INGEST] Auto-detected chat export: %s → applying chat_history schema", file.filename)

        # File pipeline: stages determined dynamically (two-phase), start with PARSE_FILE + CLASSIFY
        _stages = ["PARSE_FILE", "CLASSIFY"]
        job_id = _create_job(file.filename, display, _llm, _emb,
                             tenant_id=tenant.tenant_id, stage_names=_stages)

        # Chat export files — run connector + schema-guided extraction
        if _is_chat_export:
            tmp_path = _save_upload_to_temp(content, file.filename)
            bg.add_task(_ingest_chat_export_pipeline, job_id, scoped, tmp_path,
                        file.filename, _llm, _emb, _schema)
        # Binary or graph-format files go through file extraction pipeline
        elif _needs_file_pipeline(file.filename):
            if _is_binary_upload(file.filename):
                tmp_path = _save_upload_to_temp(content, file.filename)
            else:
                # Text-based graph formats: save to temp file for parser
                tmp_path = _save_upload_to_temp(content, file.filename)
            bg.add_task(_ingest_file_via_pipeline, job_id, scoped, tmp_path,
                        file.filename, _llm, _emb, _schema)
        elif _llm or _emb:
            text = content.decode("utf-8", errors="replace")
            _submit_ingest(job_id, _ingest_pipeline, job_id, scoped, text.strip(),
                        {"title": file.filename, "source": "file_upload",
                         "filename": file.filename,
                         "content_type": file.content_type or "text/plain"},
                        _llm, _emb, _schema)
        else:
            text = content.decode("utf-8", errors="replace")
            # Basic path
            nodes = [
                GraphNode(
                    id=str(_uuid.uuid4()), label="Document",
                    properties={
                        "name": file.filename, "source": "file_upload",
                        "char_count": len(text),
                        "content_type": file.content_type or "text/plain",
                    },
                ),
            ]
            chunks = _chunk_text(text.strip())
            for i, chunk in enumerate(chunks):
                nodes.append(GraphNode(
                    id=str(_uuid.uuid4()), label="TextChunk",
                    properties={
                        "name": f"{file.filename} (chunk {i + 1}/{len(chunks)})",
                        "content": chunk, "source": "file_upload",
                        "filename": file.filename,
                        "char_count": len(chunk), "chunk_index": i,
                    },
                ))
            _submit_ingest(job_id, _ingest_nodes, job_id, scoped, nodes)

        return {"status": "accepted", "job_id": job_id, "filename": file.filename, "graph": display}

    @router.post("/ingest/url")
    async def ingest_url(
        bg: BackgroundTasks,
        req: dict = Body(...),
        user=Depends(require_member),
    ):
        """Fetch URL content and ingest (requires member+)."""
        tenant = _get_tenant(user)
        url = req.get("url", "")
        graph_name = req.get("graph", "")
        pipeline_id = req.get("pipeline_id") or None
        schema_yaml = req.get("schema") or None
        smart_pipeline = req.get("pipeline", "")  # smart_article | smart_text | fast_ingest

        # Smart pipeline: use new 7-stage ingestion if requested
        if smart_pipeline in ("smart_article", "smart_text", "fast_ingest"):
            if not url.strip():
                raise HTTPException(status_code=400, detail="URL is required")
            scoped = _scope_graph(tenant, graph_name)
            display = scoped.split(":", 1)[-1]

            # Build filter config if provided
            fc = None
            filters = req.get("filters")
            if filters:
                from ..ingestion.filters import FilterConfig, SemanticRule
                sem_rules = [SemanticRule(query=r.get("query", ""), direction=r.get("direction", "include"),
                                          threshold=r.get("threshold", 0.6)) for r in filters.get("semantic_rules", [])]
                fc = FilterConfig(
                    keywords_include=filters.get("keywords_include", []),
                    keywords_exclude=filters.get("keywords_exclude", []),
                    keywords_include_mode=filters.get("keywords_include_mode", "or"),
                    categories_include=filters.get("categories_include", []),
                    semantic_query=filters.get("semantic_query", ""),
                    semantic_rules=sem_rules,
                )

            debug_flag = req.get("debug", False)
            job_id = _create_job(url, display, "", "",
                                 tenant_id=tenant.tenant_id,
                                 stage_names=["clean", "chunk", "extract", "link", "embed", "index", "validate"])
            if debug_flag and job_id in _ingest_jobs:
                _ingest_jobs[job_id]["debug"] = True

            def _run_smart(job_id, scoped, url, pipeline_name, filter_config=fc, debug=debug_flag):
                try:
                    if job_id in _ingest_jobs:
                        _ingest_jobs[job_id]["status"] = "running"

                    debug_logs = [] if debug else None

                    def _on_stage(stage_name, details=None):
                        """Report stage progress to the job tracker."""
                        # Map compound names like "clean_done" to base stage
                        base = stage_name.split("_")[0] if "_" in stage_name else stage_name
                        status = "completed" if stage_name.endswith("_done") or stage_name.endswith("_failed") else "running"
                        count = (details or {}).get("count", 0)
                        _update_stage(job_id, base, status, count)
                        # Capture debug details in job log
                        if debug and details and job_id in _ingest_jobs:
                            job = _ingest_jobs[job_id]
                            if "debug_log" not in job:
                                job["debug_log"] = []
                            job["debug_log"].append({"stage": stage_name, "details": details})

                    from ..ingestion.smart_ingest import ingest_url as smart_ingest_url
                    reg = graph_registry
                    db = reg.get_graph(scoped, load_if_missing=True) or reg.create_graph(scoped)
                    result = smart_ingest_url(url, db, pipeline=pipeline_name,
                                             on_stage=_on_stage, filter_config=filter_config,
                                             debug=debug, owner=tenant.tenant_id if tenant else "")
                    # Persist to disk so graph appears in listings
                    reg.save_graph(scoped, create_checkpoint=False)
                    _finish_job(
                        job_id,
                        nodes_created=len(result.passage_ids) + len(result.entity_ids) + len(result.fact_ids) + 1,
                        edges_created=result.edge_count,
                        entities_extracted=len(result.entity_ids),
                    )
                except Exception as e:
                    _finish_job(job_id, nodes_created=0, error=str(e))

            _submit_ingest(job_id, _run_smart, job_id, scoped, url, smart_pipeline)
            return {
                "status": "accepted", "job_id": job_id, "url": url,
                "graph": display, "pipeline": smart_pipeline,
            }

        # Resolve pipeline → LLM/embedding params
        llm_model, embedding_model = _resolve_pipeline_params(
            pipeline_id, req.get("llm_model"), req.get("embedding_model"))

        if not url.strip():
            raise HTTPException(status_code=400, detail="URL is required")

        # Crawl mode: fetch multiple pages from the website
        crawl = req.get("crawl", False)
        max_pages = min(req.get("max_pages", 20), 50)
        max_depth = min(req.get("max_depth", 2), 3)

        scoped = _scope_graph(tenant, graph_name)
        display = scoped.split(":", 1)[-1]

        # Build stage list
        if llm_model or embedding_model:
            _stages = ["PARSE_FILE", "CHUNK"]
            if llm_model:
                _stages.extend(["EXTRACT_FACTS", "EXTRACT"])
            if embedding_model:
                _stages.append("EMBED")
            if llm_model:
                _stages.append("INDEX_BM25")
            if embedding_model:
                _stages.append("STORE_VECTORS")
            if llm_model:
                _stages.extend(["CANONICALIZE", "ENHANCE_GRAPH"])
            _stages.append("PERSIST")
        else:
            _stages = ["CHUNK", "PERSIST"]

        job_id = _create_job(url, display, llm_model, embedding_model,
                             tenant_id=tenant.tenant_id, stage_names=_stages)

        if crawl:
            # Multi-page crawl — run in background
            _submit_ingest(job_id, _crawl_and_ingest, job_id, scoped, url,
                           max_pages, max_depth, llm_model, embedding_model, schema_yaml)
            return {"status": "accepted", "job_id": job_id, "url": url, "graph": display,
                    "crawl": True, "max_pages": max_pages}

        # Single page fetch
        try:
            from ..ingestion.web_crawler import fetch_single_page
            page = fetch_single_page(url)
            if page:
                text = page["content"]
                title = page["title"]
            else:
                raise ValueError("No content extracted")
        except Exception as e:
            # Fallback to simple fetch
            try:
                req_obj = _urllib_request.Request(url, headers={"User-Agent": "AIContextDB/1.0"})
                with _urllib_request.urlopen(req_obj, timeout=15) as resp:
                    raw = resp.read().decode("utf-8", errors="replace")
                title = url.split("/")[-1] or url
                if "<html" in raw.lower() or "<body" in raw.lower():
                    # Use trafilatura for proper article extraction
                    try:
                        import trafilatura
                        text = trafilatura.extract(raw, include_links=False, include_tables=True)
                        try:
                            meta_json = trafilatura.extract(raw, output_format="json")
                            if meta_json:
                                import json as _json
                                meta = _json.loads(meta_json)
                                title = meta.get("title") or title
                        except Exception:
                            pass  # optional
                    except ImportError:
                        pass  # optional
                    # Fallback: regex HTML stripping
                    if not text:
                        text = _re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=_re.DOTALL | _re.IGNORECASE)
                        text = _re.sub(r"<style[^>]*>.*?</style>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
                        text = _re.sub(r"<[^>]+>", " ", text)
                        text = _re.sub(r"\s+", " ", text).strip()
                else:
                    text = raw.strip()
            except Exception as e2:
                _finish_job(job_id, 0, f"Failed to fetch URL: {e2}")
                return {"status": "error", "job_id": job_id, "nodes_created": 0, "message": str(e2)}

        if not text:
            _finish_job(job_id, 0, "No text content extracted from URL")
            return {"status": "error", "job_id": job_id, "nodes_created": 0, "message": "No text content"}

        if llm_model or embedding_model:
            _submit_ingest(job_id, _ingest_pipeline, job_id, scoped, text,
                        {"title": title, "source": url, "url": url},
                        llm_model, embedding_model, schema_yaml)
        else:
            chunks = _chunk_text(text)
            nodes = []
            for i, chunk in enumerate(chunks):
                nodes.append(GraphNode(
                    id=str(_uuid.uuid4()), label="TextChunk",
                    properties={
                        "name": f"{title} (chunk {i + 1}/{len(chunks)})" if len(chunks) > 1 else title,
                        "content": chunk, "source": url, "url": url,
                        "char_count": len(chunk), "chunk_index": i,
                    },
                ))
            _submit_ingest(job_id, _ingest_nodes, job_id, scoped, nodes)

        return {"status": "accepted", "job_id": job_id, "url": url, "graph": display}

    @router.get("/ingest/queue")
    async def get_ingest_queue_status(user=Depends(user_auth)):
        """Get ingestion queue status (workers, depth, capacity)."""
        return _ingest_queue.get_status()

    @router.get("/jobs")
    async def list_all_jobs(
        status: str = None,
        user=Depends(user_auth),
    ):
        """List all jobs for this tenant (global jobs page)."""
        tenant = _get_tenant(user)

        # Merge both job stores
        from ..ingestion.job_manager import get_job_manager
        jm = get_job_manager()
        jm_jobs = jm.list_jobs()

        all_legacy = sorted(_ingest_jobs.values(), key=lambda j: j.get("created_at", ""), reverse=True)
        legacy_jobs = [j for j in all_legacy if j.get("tenant_id") == tenant.tenant_id]

        seen = {j.get("job_id") for j in jm_jobs}
        jobs = list(jm_jobs)
        for j in legacy_jobs:
            if j.get("job_id") not in seen:
                jobs.append(j)

        if status:
            jobs = [j for j in jobs if j.get("status") == status]
        # Compute summary counts
        processing = sum(1 for j in jobs if j.get("status") == "processing")
        completed = sum(1 for j in jobs if j.get("status") == "completed")
        failed = sum(1 for j in jobs if j.get("status") == "failed")
        return {
            "jobs": jobs[:100],
            "summary": {"processing": processing, "completed": completed, "failed": failed, "total": len(jobs)},
        }

    # ------------------------------------------------------------------
    # Session File Upload
    # ------------------------------------------------------------------

    @router.post("/sessions/{session_id}/upload")
    async def upload_session_file(
        session_id: str,
        file: UploadFile = File(...),
        user=Depends(require_member),
    ):
        """Upload a file into a session's context (requires member+)."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        content = await file.read()
        try:
            from ..context.blob import BlobStore
            blob = BlobStore()
            meta = blob.store(
                session_id=session_id,
                data=content,
                filename=file.filename or "upload",
                mime_type=file.content_type or "application/octet-stream",
            )

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="file_uploaded",
                    resource_type="session",
                    resource_id=session_id,
                    details={"filename": file.filename, "size": len(content)},
                )

            return {
                "status": "uploaded",
                "blob_id": meta.get("blob_id"),
                "filename": file.filename,
                "size": len(content),
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/sessions/{session_id}/files")
    async def list_session_files(session_id: str, user=Depends(user_auth)):
        """List files uploaded to a session."""
        try:
            from ..context.blob import BlobStore
            blob = BlobStore()
            files = blob.list_blobs(session_id)
            return {"files": files}
        except Exception:
            return {"files": []}

    # ------------------------------------------------------------------
    # Session Conversations
    # ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/conversations")
    async def list_session_conversations(session_id: str, user=Depends(user_auth)):
        """List conversation threads in a session."""
        try:
            from ..context.conversation import ConversationStore
            store = ConversationStore()
            convos = store.list_conversations(session_id)
            return {"conversations": [c.to_dict() if hasattr(c, 'to_dict') else vars(c) for c in convos]}
        except Exception as e:
            return {"conversations": [], "error": str(e)}

    @router.get("/sessions/{session_id}/conversations/{conversation_id}")
    async def get_conversation(session_id: str, conversation_id: str, user=Depends(user_auth)):
        """Get a conversation with its messages."""
        try:
            from ..context.conversation import ConversationStore
            store = ConversationStore()
            conv = store.get(conversation_id, include_messages=True)
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return conv.to_dict() if hasattr(conv, 'to_dict') else vars(conv)
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # Session Snapshots (Checkpoints)
    # ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/snapshots")
    async def list_session_snapshots(session_id: str, user=Depends(user_auth)):
        """List checkpoint snapshots for a session's graph namespace."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        try:
            from ..core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            snapshots = cm.list_checkpoints(session.graph_namespace)
            return {"snapshots": snapshots}
        except Exception:
            return {"snapshots": []}

    @router.post("/sessions/{session_id}/snapshot")
    async def create_session_snapshot(
        session_id: str,
        req: dict = Body(default={}),
        user=Depends(require_admin),
    ):
        """Create a checkpoint snapshot of a session's graph (requires admin+)."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        try:
            from ..core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            ns = session.graph_namespace
            db = graph_registry.get_graph(ns) if graph_registry else None

            message = req.get("message", "Dashboard snapshot")
            # The checkpoint manager needs a graph file path
            cp_id = cm.create_checkpoint(ns, ns, message=message)

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="snapshot_created",
                    resource_type="session",
                    resource_id=session_id,
                    details={"checkpoint_id": cp_id},
                )

            return {"status": "created", "checkpoint_id": cp_id, "session_id": session_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/sessions/{session_id}/snapshots/{checkpoint_id}/restore")
    async def restore_session_snapshot(
        session_id: str,
        checkpoint_id: str,
        user=Depends(user_auth),
    ):
        """Restore a session's graph from a checkpoint snapshot."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        try:
            from ..core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            ns = session.graph_namespace
            result = cm.restore_checkpoint(ns, checkpoint_id, ns)

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="snapshot_restored",
                    resource_type="session",
                    resource_id=session_id,
                    details={"checkpoint_id": checkpoint_id},
                )

            return {"status": "restored", "checkpoint_id": checkpoint_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # Session Quality Scoring
    # ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/quality")
    async def get_session_quality(session_id: str, user=Depends(user_auth)):
        """Get context quality report for a session."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        try:
            from ..context.quality import ContextQualityScorer
            scorer = ContextQualityScorer()
            graph = None
            if graph_registry:
                graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
            report = scorer.score_session(session, graph)
            return report.to_dict()
        except Exception as e:
            return {"session_id": session_id, "overall_score": 0, "grade": "?", "error": str(e)}

    # ------------------------------------------------------------------
    # Session Analytics
    # ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/analytics")
    async def get_session_analytics(session_id: str, user=Depends(user_auth)):
        """Get usage analytics for a session."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        node_count = 0
        if graph_registry:
            graph = graph_registry.get_graph(session.graph_namespace, load_if_missing=True)
            if graph:
                try:
                    nodes = graph.get_all_nodes()
                    node_count = len(nodes) if nodes else 0
                except Exception:
                    pass  # optional

        file_count = 0
        try:
            from ..context.blob import BlobStore
            files = BlobStore().list_blobs(session_id)
            file_count = len(files)
        except Exception:
            pass  # optional

        conv_count = 0
        try:
            from ..context.conversation import ConversationStore
            convos = ConversationStore().list_conversations(session_id)
            conv_count = len(convos)
        except Exception:
            pass  # optional

        member_count = 0
        try:
            members_data = session_manager.list_members(session_id)
            member_count = len(members_data) if members_data else 0
        except Exception:
            pass  # optional

        activity_count = 0
        try:
            activity_data = session_manager.get_activity(session_id)
            activity_count = len(activity_data) if activity_data else 0
        except Exception:
            pass  # optional

        return {
            "session_id": session_id,
            "node_count": node_count,
            "file_count": file_count,
            "conversation_count": conv_count,
            "member_count": member_count,
            "activity_count": activity_count,
            "created_at": session.created_at if hasattr(session, 'created_at') else None,
        }

    # ------------------------------------------------------------------
    # Session Invitations (cross-workspace agent access)
    # ------------------------------------------------------------------

    @router.post("/sessions/{session_id}/invite", status_code=201)
    async def create_session_invite(
        session_id: str,
        req: dict = Body(...),
        user=Depends(user_auth),
    ):
        """Generate an invite token for external agents to join a session."""
        import uuid as _uuid
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        access_level = req.get("level", "read")
        expires_hours = req.get("expires_hours", 72)

        token = _uuid.uuid4().hex
        invite = {
            "invitation_id": str(_uuid.uuid4()),
            "session_id": session_id,
            "token": token,
            "access_level": access_level,
            "expires_at": time.time() + (expires_hours * 3600),
            "created_by": user.user_id,
            "created_at": time.time(),
            "status": "pending",
        }

        # Store in session config
        import json as _json
        cfg = session.config or {}
        invites_list = cfg.get("invitations", [])
        invites_list.append(invite)
        cfg["invitations"] = invites_list
        session_manager._conn.execute(
            "UPDATE context_sessions SET config = ? WHERE session_id = ?",
            (_json.dumps(cfg), session_id),
        )
        session_manager._conn.commit()

        base_url = ""
        invite_url = f"{base_url}/context/sessions/join/{token}"

        if audit_log:
            audit_log.log(
                user_id=user.user_id,
                action="invite_created",
                resource_type="session",
                resource_id=session_id,
                details={"level": access_level, "expires_hours": expires_hours},
            )

        return {
            **invite,
            "invite_url": invite_url,
        }

    @router.get("/sessions/{session_id}/invitations")
    async def list_session_invitations(session_id: str, user=Depends(user_auth)):
        """List pending invitations for a session."""
        if not session_manager:
            return {"invitations": []}
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        invites = (session.config or {}).get("invitations", [])
        # Filter out expired
        now = time.time()
        active = [i for i in invites if i.get("expires_at", 0) > now and i.get("status") == "pending"]
        return {"invitations": active}

    @router.delete("/sessions/{session_id}/invitations/{invitation_id}")
    async def revoke_session_invite(session_id: str, invitation_id: str, user=Depends(user_auth)):
        """Revoke a session invitation."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        import json as _json
        cfg = session.config or {}
        invites = cfg.get("invitations", [])
        for inv in invites:
            if inv.get("invitation_id") == invitation_id:
                inv["status"] = "revoked"
                break

        cfg["invitations"] = invites
        session_manager._conn.execute(
            "UPDATE context_sessions SET config = ? WHERE session_id = ?",
            (_json.dumps(cfg), session_id),
        )
        session_manager._conn.commit()
        return {"status": "revoked"}

    # ------------------------------------------------------------------
    # ------------------------------------------------------------------
    # Comments / Collaboration
    # ------------------------------------------------------------------

    from ..collaboration import CommentStore
    _comment_store = CommentStore()

    @router.get("/comments/{node_id}")
    async def list_comments(node_id: str, user=Depends(user_auth)):
        """Get comments on a node (task, decision, etc.)."""
        comments = _comment_store.list(node_id)
        return {"comments": [c.to_dict() for c in comments], "total": len(comments)}

    @router.post("/comments/{node_id}")
    async def add_comment(node_id: str, req: dict = Body(...), user=Depends(user_auth)):
        """Add a comment to a node."""
        content = req.get("content", "").strip()
        if not content:
            raise HTTPException(status_code=400, detail="Content is required")
        comment = _comment_store.add(
            node_id=node_id,
            author=user.user_id,
            author_name=user.display_name,
            content=content,
            parent_id=req.get("parent_id"),
        )
        return comment.to_dict()

    @router.patch("/comments/{node_id}/{comment_id}")
    async def update_comment(node_id: str, comment_id: str, req: dict = Body(...), user=Depends(user_auth)):
        """Update or resolve a comment."""
        comment = _comment_store.update(
            comment_id=comment_id,
            content=req.get("content"),
            resolved=req.get("resolved"),
            reaction=req.get("reaction"),
        )
        if not comment:
            raise HTTPException(status_code=404, detail="Comment not found")
        return comment.to_dict()

    @router.delete("/comments/{node_id}/{comment_id}")
    async def delete_comment(node_id: str, comment_id: str, user=Depends(user_auth)):
        """Delete a comment."""
        _comment_store.delete(comment_id)
        return {"status": "deleted"}

    @router.get("/comments")
    async def recent_comments(limit: int = 20, user=Depends(user_auth)):
        """Get recent comments across all nodes."""
        comments = _comment_store.list_recent(limit)
        return {"comments": [c.to_dict() for c in comments]}

    # ------------------------------------------------------------------
    # Pipeline Schedules
    # ------------------------------------------------------------------

    @router.get("/sessions/{session_id}/schedule")
    async def get_session_schedule(session_id: str, user=Depends(user_auth)):
        """Get schedule for a session."""
        try:
            from ..scheduler import PipelineScheduler
            from .api import pipeline_scheduler
            if not pipeline_scheduler:
                return {"schedules": []}
            return {"schedules": pipeline_scheduler.list_schedules(session_id)}
        except Exception:
            return {"schedules": []}

    @router.post("/sessions/{session_id}/schedule")
    async def create_session_schedule(session_id: str, req: dict = Body(...), user=Depends(require_member)):
        """Schedule recurring pipeline runs for a session."""
        try:
            from .api import pipeline_scheduler
            if not pipeline_scheduler:
                raise HTTPException(status_code=503, detail="Scheduler not available")

            schedule = pipeline_scheduler.schedule(
                session_id=session_id,
                prompt=req.get("prompt", "Continue the project work"),
                interval=req.get("interval", "daily"),
                lead_agent_id=req.get("lead_agent_id"),
                max_turns=req.get("max_turns", 10),
            )
            return schedule
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))

    @router.delete("/sessions/{session_id}/schedule/{schedule_id}")
    async def delete_session_schedule(session_id: str, schedule_id: str, user=Depends(require_member)):
        """Remove a schedule."""
        try:
            from .api import pipeline_scheduler
            if pipeline_scheduler:
                pipeline_scheduler.unschedule(schedule_id)
            return {"status": "disabled"}
        except Exception:
            return {"status": "error"}

    @router.get("/schedules")
    async def list_all_schedules(user=Depends(user_auth)):
        """List all scheduled pipeline runs."""
        try:
            from .api import pipeline_scheduler
            if not pipeline_scheduler:
                return {"schedules": []}
            return {"schedules": pipeline_scheduler.list_schedules()}
        except Exception:
            return {"schedules": []}

    # Session Templates
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Multi-Model Comparison
    # ------------------------------------------------------------------

    @router.post("/compare-models")
    async def compare_models_endpoint(
        req: dict = Body(...),
        background_tasks: BackgroundTasks = None,
        user=Depends(user_auth),
    ):
        """Run extraction with multiple LLMs and compare results.

        Body:
            text: str — text to extract from
            models: list[str] — e.g. ["groq:gpt-oss-120b", "openai:gpt-4o-mini"]
            context_id: str (optional) — use context's schema
            schema_name: str (optional) — use a named schema
        """
        from ..comparison import compare_models as _compare

        text = req.get("text", "")
        models = req.get("models", [])
        if not text.strip():
            raise HTTPException(status_code=400, detail="Text is required")
        if not models or len(models) < 2:
            raise HTTPException(status_code=400, detail="At least 2 models required")
        if len(models) > 5:
            raise HTTPException(status_code=400, detail="Maximum 5 models")

        # Resolve schema
        schema = None
        schema_prompt = None
        ctx_id = req.get("context_id")
        schema_name = req.get("schema_name")

        if ctx_id and context_manager:
            try:
                ctx_obj = context_manager.get_context(ctx_id)
                if ctx_obj:
                    from ..extraction.schema_loader import get_default_schema
                    schema = get_default_schema(ctx_obj.context_type)
            except Exception:
                pass  # optional
        elif schema_name:
            try:
                from ..extraction.schema_loader import get_default_schema
                schema = get_default_schema(schema_name)
            except Exception:
                pass  # optional

        result = _compare(text=text, models=models, schema=schema)
        return result

    @router.get("/compare-models/available")
    async def list_available_models(user=Depends(user_auth)):
        """List LLM models available for comparison."""
        import os
        available = []

        if os.environ.get("GROQ_API_KEY"):
            available.append({"spec": "groq:gpt-oss-120b", "name": "Groq GPT-OSS 120B", "provider": "groq", "free": True})
            available.append({"spec": "groq:gpt-oss-20b", "name": "Groq GPT-OSS 20B", "provider": "groq", "free": True})
        if os.environ.get("OPENAI_API_KEY"):
            available.append({"spec": "openai:gpt-4o-mini", "name": "GPT-4o Mini", "provider": "openai", "free": False})
            available.append({"spec": "openai:gpt-4o", "name": "GPT-4o", "provider": "openai", "free": False})
            available.append({"spec": "openai:gpt-4.1-nano", "name": "GPT-4.1 Nano", "provider": "openai", "free": False})
        if os.environ.get("ANTHROPIC_API_KEY"):
            available.append({"spec": "anthropic:claude-sonnet-4-20250514", "name": "Claude Sonnet 4", "provider": "anthropic", "free": False})
            available.append({"spec": "anthropic:claude-haiku-4-5-20251001", "name": "Claude Haiku 4.5", "provider": "anthropic", "free": False})
        if os.environ.get("MISTRAL_API_KEY"):
            available.append({"spec": "mistral:mistral-large-latest", "name": "Mistral Large", "provider": "mistral", "free": False})
        if os.environ.get("DEEPSEEK_API_KEY"):
            available.append({"spec": "deepseek:deepseek-chat", "name": "DeepSeek Chat", "provider": "deepseek", "free": False})

        # Always offer Ollama if it might be running locally
        available.append({"spec": "ollama:llama3.1", "name": "Ollama Llama 3.1 (local)", "provider": "ollama", "free": True})

        return {"models": available}

    # Built-in project templates that ship with the product
    BUILTIN_TEMPLATES = [
        {
            "template_id": "builtin:sdlc",
            "name": "SDLC Software Project",
            "description": "Full software development lifecycle — ingest requirements, extract features/components/APIs, agents plan and build code with Git integration.",
            "icon": "code",
            "category": "Software",
            "config": {
                "goal": "Build the application based on the attached requirements",
                "context_type": "software_dev",
                "extraction_schema": "sdlc",
                "pipeline": "builtin:sdlc-graph-rag",
                "prompt_template": "sdlc",
            },
            "builtin": True,
        },
        {
            "template_id": "builtin:rag",
            "name": "RAG Knowledge Base",
            "description": "Ingest documents (PDF, DOCX, TXT), extract entities and facts, enable semantic search and RAG question-answering.",
            "icon": "search",
            "category": "Knowledge",
            "config": {
                "goal": "Build a searchable knowledge base from the uploaded documents",
                "context_type": "knowledge_base",
                "extraction_schema": "knowledge_base",
                "pipeline": "builtin:multi-layer-extraction",
            },
            "builtin": True,
        },
        {
            "template_id": "builtin:meeting-notes",
            "name": "Meeting Notes Analyzer",
            "description": "Extract decisions, action items, and key discussions from meeting transcripts. Track who decided what and follow up on actions.",
            "icon": "users",
            "category": "Knowledge",
            "config": {
                "goal": "Extract decisions, action items, and key takeaways from the meeting notes",
                "context_type": "decision",
                "extraction_schema": "decision",
                "pipeline": "builtin:knowledge-graph",
            },
            "builtin": True,
        },
        {
            "template_id": "builtin:api-docs",
            "name": "API Documentation",
            "description": "Ingest OpenAPI specs, API docs, or technical documentation. Extract endpoints, schemas, and relationships for agent-ready context.",
            "icon": "globe",
            "category": "Software",
            "config": {
                "goal": "Build a structured knowledge graph from the API documentation",
                "context_type": "software_dev",
                "extraction_schema": "sdlc",
                "pipeline": "builtin:schema-guided",
            },
            "builtin": True,
        },
        {
            "template_id": "builtin:research",
            "name": "Research & Analysis",
            "description": "Ingest research papers, reports, and web content. Extract claims, metrics, and relationships between concepts.",
            "icon": "book",
            "category": "Knowledge",
            "config": {
                "goal": "Extract key findings, claims, and relationships from the research material",
                "context_type": "knowledge_base",
                "extraction_schema": "knowledge_base",
                "pipeline": "builtin:full",
            },
            "builtin": True,
        },
        {
            "template_id": "builtin:database-docs",
            "name": "Database Documentation",
            "description": "Ingest database schemas, ERD docs, or SQL files. Extract tables, columns, relationships, and constraints.",
            "icon": "database",
            "category": "Data",
            "config": {
                "goal": "Build a graph of the database schema with tables, columns, and relationships",
                "context_type": "database",
                "extraction_schema": "database",
                "pipeline": "builtin:schema-guided",
            },
            "builtin": True,
        },
        {
            "template_id": "builtin:compliance",
            "name": "Rules & Compliance",
            "description": "Ingest policy documents, coding standards, or compliance requirements. Extract rules, constraints, and their relationships.",
            "icon": "shield",
            "category": "Governance",
            "config": {
                "goal": "Extract rules, standards, and compliance requirements into a queryable graph",
                "context_type": "rules",
                "extraction_schema": "rules",
                "pipeline": "builtin:schema-guided",
            },
            "builtin": True,
        },
    ]

    @router.get("/templates")
    async def list_templates(user=Depends(user_auth)):
        """List session templates (built-in + custom)."""
        tenant = _get_tenant(user)

        # Start with built-in templates
        templates = [dict(t) for t in BUILTIN_TEMPLATES]

        # Add custom templates from DB
        try:
            import sqlite3 as _sql
            from pathlib import Path as _Path
            import json as _json
            db_path = str(_Path("contextcore_data") / "context.db")
            with _sql.connect(db_path) as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS session_templates (
                    template_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    config TEXT DEFAULT '{}',
                    created_at REAL NOT NULL
                )""")
                conn.row_factory = _sql.Row
                rows = conn.execute(
                    "SELECT * FROM session_templates WHERE tenant_id = ? ORDER BY created_at DESC",
                    (tenant.tenant_id,),
                ).fetchall()
            for r in rows:
                templates.append({
                    "template_id": r["template_id"],
                    "name": r["name"],
                    "description": r["description"],
                    "config": _json.loads(r["config"]) if r["config"] else {},
                    "created_at": r["created_at"],
                    "builtin": False,
                })
        except Exception:
            pass  # optional

        return {"templates": templates}

    @router.post("/templates", status_code=201)
    async def create_template(req: dict = Body(...), user=Depends(require_admin)):
        """Save a session configuration as a reusable template."""
        import uuid as _uuid
        import json as _json
        tenant = _get_tenant(user)
        name = req.get("name", "")
        if not name.strip():
            raise HTTPException(status_code=400, detail="Name is required")

        template_id = str(_uuid.uuid4())
        config = req.get("config", {})
        description = req.get("description", "")

        try:
            import sqlite3 as _sql
            from pathlib import Path as _Path
            db_path = str(_Path("contextcore_data") / "context.db")
            with _sql.connect(db_path) as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS session_templates (
                    template_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    description TEXT DEFAULT '',
                    config TEXT DEFAULT '{}',
                    created_at REAL NOT NULL
                )""")
                conn.execute(
                    "INSERT INTO session_templates VALUES (?, ?, ?, ?, ?, ?)",
                    (template_id, tenant.tenant_id, name, description,
                     _json.dumps(config), time.time()),
                )

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="template_created",
                    resource_type="template",
                    resource_id=template_id,
                    details={"name": name},
                )

            return {"template_id": template_id, "name": name, "status": "created"}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/templates/{template_id}/instantiate")
    async def instantiate_template(template_id: str, req: dict = Body(default={}), user=Depends(user_auth)):
        """Create a new session from a template (built-in or custom)."""
        if not session_manager:
            raise HTTPException(status_code=503, detail="Session manager not available")

        # Check built-in templates first
        template_config = None
        template_name = ""
        builtin = next((t for t in BUILTIN_TEMPLATES if t["template_id"] == template_id), None)
        if builtin:
            template_config = dict(builtin["config"])
            template_name = builtin["name"]
        else:
            # Check custom templates
            try:
                import sqlite3 as _sql
                from pathlib import Path as _Path
                db_path = str(_Path("contextcore_data") / "context.db")
                with _sql.connect(db_path) as conn:
                    conn.row_factory = _sql.Row
                    row = conn.execute(
                        "SELECT * FROM session_templates WHERE template_id = ?", (template_id,)
                    ).fetchone()
                if row:
                    template_config = _json.loads(row["config"]) if row["config"] else {}
                    template_name = row["name"]
            except Exception:
                pass  # optional

        if template_config is None:
            raise HTTPException(status_code=404, detail="Template not found")

        try:
            session_name = req.get("name", f"{template_name}")

            # Build session config from template
            session_config = {
                "goal": template_config.get("goal", ""),
                "template_id": template_id,
                "template_name": template_name,
            }

            session = session_manager.create_session(
                name=session_name,
                owner_agent_id=user.user_id,
                config=session_config,
            )

            # Template context creation removed — boundaries have their own graph.
            # Users can create and attach contexts separately if needed.

            return {
                "session_id": session.session_id,
                "name": session_name,
                "template": template_name,
                "config": session_config,
                "status": "created",
            }
        except HTTPException:
            raise
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/templates/{template_id}")
    async def delete_template(template_id: str, user=Depends(require_admin)):
        """Delete a session template."""
        import sqlite3 as _sql
        from pathlib import Path as _Path
        try:
            db_path = str(_Path("contextcore_data") / "context.db")
            with _sql.connect(db_path) as conn:
                c = conn.execute("DELETE FROM session_templates WHERE template_id = ?", (template_id,))
                if c.rowcount == 0:
                    raise HTTPException(status_code=404, detail="Template not found")
            return {"status": "deleted"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # Webhooks
    # ------------------------------------------------------------------

    @router.get("/webhooks")
    async def list_webhooks(user=Depends(user_auth)):
        """List registered webhooks."""
        tenant = _get_tenant(user)
        try:
            from ..context.webhooks import WebhookRegistry
            registry = WebhookRegistry()
            hooks = registry.list_webhooks(tenant.tenant_id)
            return {"webhooks": [h.to_dict() for h in hooks]}
        except Exception:
            return {"webhooks": []}

    @router.post("/webhooks", status_code=201)
    async def create_webhook(req: dict = Body(...), user=Depends(require_admin)):
        """Register an outbound webhook URL."""
        tenant = _get_tenant(user)
        url = req.get("url", "")
        event_types = req.get("event_types", [])

        if not url.strip():
            raise HTTPException(status_code=400, detail="URL is required")

        try:
            from ..context.webhooks import WebhookRegistry
            registry = WebhookRegistry()
            hook = registry.register(tenant.tenant_id, url, event_types or None)

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="webhook_created",
                    resource_type="webhook",
                    resource_id=hook.webhook_id,
                    details={"url": url},
                )

            return {**hook.to_dict(), "secret": hook.secret}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.delete("/webhooks/{webhook_id}")
    async def delete_webhook(webhook_id: str, user=Depends(require_admin)):
        """Remove a webhook."""
        try:
            from ..context.webhooks import WebhookRegistry
            registry = WebhookRegistry()
            if not registry.delete(webhook_id):
                raise HTTPException(status_code=404, detail="Webhook not found")

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="webhook_deleted",
                    resource_type="webhook",
                    resource_id=webhook_id,
                )

            return {"status": "deleted"}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/webhooks/{webhook_id}/test")
    async def test_webhook(webhook_id: str, user=Depends(user_auth)):
        """Send a test event to a webhook."""
        try:
            from ..context.webhooks import WebhookRegistry
            registry = WebhookRegistry()
            hook = registry.get(webhook_id)
            if not hook:
                raise HTTPException(status_code=404, detail="Webhook not found")

            test_event = {
                "event_type": "test",
                "message": "This is a test event from QGraph",
                "timestamp": time.time(),
            }
            registry.dispatch(hook.tenant_id, test_event)
            return {"status": "sent", "event": test_event}
        except HTTPException:
            raise
        except Exception as e:
            return {"status": "error", "message": str(e)}

    # ------------------------------------------------------------------
    # Audit Trail
    # ------------------------------------------------------------------

    @router.get("/audit")
    async def get_audit_log(
        action: Optional[str] = None,
        user_id: Optional[str] = None,
        resource_type: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        user=Depends(user_auth),
    ):
        """Paginated audit log with optional filters."""
        if not audit_log:
            return {"entries": [], "total": 0}

        entries = audit_log.query(
            action=action,
            user_id=user_id,
            resource_type=resource_type,
            limit=limit,
            offset=offset,
        )
        total = audit_log.count(
            action=action,
            user_id=user_id,
            resource_type=resource_type,
        )
        return {
            "entries": [e.to_dict() for e in entries],
            "total": total,
        }

    @router.get("/audit/export")
    async def export_audit_log(user=Depends(require_owner)):
        """Export audit log as CSV."""
        from fastapi.responses import PlainTextResponse
        if not audit_log:
            return PlainTextResponse("No audit log available", media_type="text/csv")
        csv = audit_log.export_csv()
        return PlainTextResponse(csv, media_type="text/csv", headers={
            "Content-Disposition": "attachment; filename=audit_log.csv"
        })

    # ------------------------------------------------------------------
    # Backups (checkpoints)
    # ------------------------------------------------------------------

    @router.get("/backups")
    async def list_backups(user=Depends(require_owner)):
        """List checkpoint backups across all graphs."""
        tenant = _get_tenant(user)
        try:
            from ..core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            # List checkpoints for tenant's graphs
            all_graphs = graph_registry.list_graphs()
            tenant_graphs = [
                g.get("name", "") for g in all_graphs
                if g.get("name", "").startswith(tenant.ns_prefix())
            ]
            backups = []
            for gname in tenant_graphs:
                try:
                    cps = cm.list_checkpoints(gname)
                    for cp in cps:
                        backups.append({**cp, "graph": gname.replace(tenant.ns_prefix(), "")})
                except Exception:
                    pass  # optional
            return {"backups": backups}
        except Exception as e:
            return {"backups": [], "error": str(e)}

    @router.post("/backups")
    async def create_backup(
        req: dict = Body(default={}),
        user=Depends(require_owner),
    ):
        """Create a checkpoint backup of a graph (requires owner)."""
        tenant = _get_tenant(user)
        graph_name = req.get("graph", "default")
        scoped = _scope_graph(tenant, graph_name)

        try:
            from ..core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            db = graph_registry.get_graph(scoped)
            if not db:
                raise HTTPException(status_code=404, detail="Graph not found")

            cp_id = cm.create_checkpoint(scoped, db)

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="backup_created",
                    resource_type="graph",
                    resource_id=graph_name,
                    details={"checkpoint_id": cp_id},
                )

            return {"status": "created", "checkpoint_id": cp_id, "graph": graph_name}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/backups/{checkpoint_id}/restore")
    async def restore_backup(
        checkpoint_id: str,
        req: dict = Body(default={}),
        user=Depends(require_owner),
    ):
        """Restore a graph from a checkpoint (requires owner)."""
        tenant = _get_tenant(user)
        graph_name = req.get("graph", "default")
        scoped = _scope_graph(tenant, graph_name)

        try:
            from ..core.checkpoint import CheckpointManager
            cm = CheckpointManager()
            cm.restore_checkpoint(scoped, checkpoint_id, graph_registry)

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="backup_restored",
                    resource_type="graph",
                    resource_id=graph_name,
                    details={"checkpoint_id": checkpoint_id},
                )

            return {"status": "restored", "checkpoint_id": checkpoint_id, "graph": graph_name}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # Chunking config endpoints
    # ------------------------------------------------------------------

    _CHUNKING_CONFIG_PATH = Path(__file__).resolve().parent.parent.parent / "config" / "strategies" / "chunking.yaml"

    @router.get("/config/chunking")
    async def get_chunking_config(user=Depends(user_auth)):
        """Return the current chunking strategies configuration."""
        try:
            import yaml
            if _CHUNKING_CONFIG_PATH.exists():
                with open(_CHUNKING_CONFIG_PATH, "r", encoding="utf-8") as f:
                    data = yaml.safe_load(f) or {}
                return {"strategies": data.get("strategies", {})}
            else:
                return {"strategies": {}}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to load chunking config: {e}")

    @router.put("/config/chunking")
    async def update_chunking_config(
        req: dict = Body(...),
        user=Depends(user_auth),
    ):
        """Update the chunking strategies configuration (writes to YAML)."""
        strategies = req.get("strategies")
        if strategies is None:
            raise HTTPException(status_code=400, detail="Missing 'strategies' field")

        try:
            import yaml

            # Ensure config directory exists
            _CHUNKING_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)

            data = {"strategies": strategies}
            with open(_CHUNKING_CONFIG_PATH, "w", encoding="utf-8") as f:
                yaml.dump(data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)

            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="chunking_config_updated",
                    resource_type="config",
                    resource_id="chunking",
                    details={"strategy_count": len(strategies)},
                )

            return {"status": "saved", "strategy_count": len(strategies)}
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Failed to save chunking config: {e}")

    # ==================================================================
    # Server Configuration
    # ==================================================================

    _SERVER_CONFIG_PATH = _os.path.join(
        _os.path.dirname(_os.path.dirname(__file__)), "config", "server_config.yaml"
    )

    @router.get("/config/server")
    async def get_server_config(user=Depends(user_auth)):
        """Return server configuration — LLM, embedding, storage, and pipeline defaults."""
        import yaml

        config = {}
        if _os.path.exists(_SERVER_CONFIG_PATH):
            try:
                with open(_SERVER_CONFIG_PATH, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            except Exception:
                pass  # optional

        # Merge with live env-var state so the UI shows what's actually active
        ollama_url = _os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
        config.setdefault("llm", {})
        config.setdefault("embeddings", {})
        config.setdefault("storage", {})
        config.setdefault("pipeline_defaults", {})

        # Detect configured providers
        providers = {}
        for key, name in [("GROQ_API_KEY", "groq"), ("OPENAI_API_KEY", "openai"),
                          ("ANTHROPIC_API_KEY", "anthropic"), ("MISTRAL_API_KEY", "mistral"),
                          ("DEEPSEEK_API_KEY", "deepseek")]:
            providers[name] = bool(_os.environ.get(key))
        # Check Ollama
        try:
            import urllib.request
            urllib.request.urlopen(f"{ollama_url}/api/tags", timeout=1)
            providers["ollama"] = True
        except Exception:
            providers["ollama"] = False

        config["providers"] = providers
        config["ollama_url"] = ollama_url
        config["env"] = _os.environ.get("CONTEXTSYNAPSE_ENV") or os.environ.get("AICONTEXTDB_ENV", "development")
        config["redis_url"] = _os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "")
        config["rate_limit_rpm"] = int(_os.environ.get("CONTEXTSYNAPSE_RATE_LIMIT_RPM") or os.environ.get("AICONTEXTDB_RATE_LIMIT_RPM", "60"))

        return config

    @router.put("/config/server")
    async def update_server_config(req: dict = Body(...), user=Depends(require_admin)):
        """Update server configuration (admin only). Persists to YAML."""
        import yaml

        # Load existing
        config = {}
        if _os.path.exists(_SERVER_CONFIG_PATH):
            try:
                with open(_SERVER_CONFIG_PATH, "r", encoding="utf-8") as f:
                    config = yaml.safe_load(f) or {}
            except Exception:
                pass  # optional

        # Merge updates
        for section in ["llm", "embeddings", "storage", "pipeline_defaults"]:
            if section in req:
                config[section] = req[section]

        # Save
        _os.makedirs(_os.path.dirname(_SERVER_CONFIG_PATH), exist_ok=True)
        with open(_SERVER_CONFIG_PATH, "w", encoding="utf-8") as f:
            yaml.dump(config, f, default_flow_style=False, sort_keys=False)

        if audit_log:
            audit_log.log(
                user_id=user.user_id,
                action="server_config_updated",
                resource_type="config",
                resource_id="server",
                details={"sections": list(req.keys())},
            )

        return {"status": "saved", "config": config}

    # ==================================================================
    # Context endpoints (first-class contexts)
    # ==================================================================

    @router.get("/contexts")
    async def list_contexts(
        context_type: Optional[str] = Query(None),
        status: str = Query("active"),
        search: Optional[str] = Query(None),
        tag: Optional[str] = Query(None),
        user=Depends(user_auth),
    ):
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        contexts = context_manager.list_contexts(
            context_type=context_type, status=status, search=search, tag=tag)

        # Pre-compute session counts per context to avoid N+1
        _ctx_session_counts = {}
        try:
            if session_manager:
                for _s in session_manager.list_sessions():
                    _cid = getattr(_s, 'context_id', '') or ''
                    if _cid:
                        _ctx_session_counts[_cid] = _ctx_session_counts.get(_cid, 0) + 1
        except Exception:
            pass

        result = []
        for ctx in contexts:
            d = ctx.to_dict()
            # Add session usage info
            d["session_count"] = _ctx_session_counts.get(ctx.context_id, 0)
            result.append(d)
        return {"contexts": result, "total": len(result)}

    @router.post("/contexts", status_code=201)
    async def create_context(req: CreateContextRequest, user=Depends(require_member)):
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        try:
            # Let context_manager generate the namespace (clean, no tenant prefix)
            ctx = context_manager.create_context(
                name=req.name,
                context_type=req.context_type,
                description=req.description,
                source=req.source,
                sensitivity=req.sensitivity,
                owner_id=user.user_id,
                embedding_model=req.embedding_model,
                embedding_dimension=req.embedding_dimension,
                tags=req.tags,
                config=req.config,
                _graph_namespace=req.graph_namespace,  # only if linking to existing graph
            )
            # Bind schema if provided
            if req.schema_name:
                try:
                    from ..project.schema_manager import get_schema_manager
                    get_schema_manager().bind(ctx.graph_namespace, req.schema_name)
                except Exception as e:
                    logger.warning("Could not bind schema '%s': %s", req.schema_name, e)
            # If linked to existing graph, refresh stats from its data
            if req.graph_namespace:
                context_manager.refresh_stats(ctx.context_id)
                ctx = context_manager.get_context(ctx.context_id) or ctx
            event_bus.emit("context_created", {"context_id": ctx.context_id, "name": ctx.name, "type": ctx.context_type})
            if audit_log:
                audit_log.log(
                    user_id=user.user_id,
                    action="context_created",
                    resource_type="context",
                    resource_id=ctx.context_id,
                    details={"name": ctx.name, "type": ctx.context_type},
                )
            return ctx.to_dict()
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    # ------------------------------------------------------------------
    # Extraction Schema CRUD (registered BEFORE /contexts/{context_id})
    # ------------------------------------------------------------------

    @router.get("/contexts/schemas/defaults")
    async def list_default_extraction_schemas(user=Depends(user_auth)):
        """List all default extraction schemas by context type."""
        from ..extraction.schema_loader import list_default_schemas as _list_defaults
        return _list_defaults()

    @router.get("/contexts/schemas/defaults/{context_type}")
    async def get_default_extraction_schema(context_type: str, user=Depends(user_auth)):
        """Get the default extraction schema YAML for a context type."""
        from ..extraction.schema_loader import get_default_schema_yaml as _get_yaml
        yaml_str = _get_yaml(context_type)
        if not yaml_str:
            raise HTTPException(status_code=404, detail=f"No default schema for type: {context_type}")
        return {"context_type": context_type, "yaml": yaml_str, "source": "default"}

    @router.post("/contexts/schemas/validate")
    async def validate_extraction_schema(req: dict = Body(...), user=Depends(user_auth)):
        """Validate extraction schema YAML."""
        from ..extraction.schema_loader import validate_schema_yaml as _validate
        yaml_str = req.get("yaml", "")
        if not yaml_str.strip():
            raise HTTPException(status_code=400, detail="YAML content is required")
        return _validate(yaml_str)

    @router.get("/contexts/presets")
    async def list_context_presets(user=Depends(user_auth)):
        """List available context presets (examples)."""
        import json
        from pathlib import Path
        presets_dir = Path(__file__).parent.parent / "config" / "context_presets"
        presets = []
        if presets_dir.exists():
            for f in sorted(presets_dir.glob("*.json")):
                try:
                    data = json.loads(f.read_text())
                    presets.append({"id": f.stem, **data})
                except Exception:
                    pass
        return {"presets": presets}

    @router.get("/contexts/{context_id}/extraction-schema")
    async def get_context_extraction_schema(context_id: str, user=Depends(user_auth)):
        """Get the current extraction schema for a context (custom or default)."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        custom_yaml = ctx.config.get("extraction_schema_yaml")
        if custom_yaml:
            return {"yaml": custom_yaml, "source": "custom", "context_type": ctx.context_type}

        from ..extraction.schema_loader import get_default_schema_yaml as _get_yaml
        default_yaml = _get_yaml(ctx.context_type)
        if default_yaml:
            return {"yaml": default_yaml, "source": "default", "context_type": ctx.context_type}

        return {"yaml": None, "source": "none", "context_type": ctx.context_type}

    @router.put("/contexts/{context_id}/extraction-schema")
    async def set_context_extraction_schema(context_id: str, req: dict = Body(...),
                                            user=Depends(require_member)):
        """Save a custom extraction schema for a context."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        yaml_str = req.get("yaml", "")
        if not yaml_str.strip():
            raise HTTPException(status_code=400, detail="YAML schema is required")

        from ..extraction.schema_loader import validate_schema_yaml as _validate
        result = _validate(yaml_str)
        if not result["valid"]:
            raise HTTPException(status_code=422, detail={
                "errors": result["errors"], "warnings": result["warnings"]
            })

        context_manager.set_extraction_schema(context_id, yaml_str)
        return {"status": "saved", "warnings": result.get("warnings", [])}

    @router.delete("/contexts/{context_id}/extraction-schema")
    async def reset_context_extraction_schema(context_id: str, user=Depends(require_member)):
        """Revert a context to its default extraction schema."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        context_manager.clear_extraction_schema(context_id)
        return {"status": "reset_to_default"}

    # ------------------------------------------------------------------

    @router.get("/contexts/{context_id}")
    async def get_context(context_id: str, user=Depends(user_auth)):
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        d = ctx.to_dict()
        if session_manager:
            d["sessions"] = session_manager.get_context_session_ids(ctx.context_id)
        return d

    @router.get("/contexts/{context_id}/graph")
    async def get_context_graph(context_id: str, user=Depends(user_auth)):
        """Get the graph data (nodes + edges) for a context's namespace."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        ns = ctx.graph_namespace
        graph = graph_registry.get_graph(ns, load_if_missing=True) if graph_registry else None
        if not graph:
            return {"nodes": [], "edges": [], "node_count": 0, "edge_count": 0}

        nodes = graph.get_all_nodes()
        edges = graph.get_all_edges()
        node_list = []
        for n in nodes:
            node_list.append({
                "id": getattr(n, "id", ""),
                "label": getattr(n, "label", ""),
                "properties": getattr(n, "properties", {}),
            })
        edge_list = []
        for e in edges:
            edge_list.append({
                "id": getattr(e, "id", ""),
                "source": getattr(e, "source", ""),
                "target": getattr(e, "target", ""),
                "label": getattr(e, "label", ""),
                "properties": getattr(e, "properties", {}),
            })
        return {
            "nodes": node_list,
            "edges": edge_list,
            "node_count": len(node_list),
            "edge_count": len(edge_list),
            "graph_namespace": ns,
        }

    @router.get("/contexts/{context_id}/briefing")
    async def get_context_briefing(context_id: str, user=Depends(user_auth)):
        """Get a summary briefing for a context — node counts by type, key entities, recent activity."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        ns = ctx.graph_namespace
        graph = graph_registry.get_graph(ns, load_if_missing=True) if graph_registry else None

        node_types = {}
        edge_types = {}
        key_entities = []
        facts = []
        if graph:
            for n in graph.get_all_nodes():
                label = getattr(n, "label", "unknown")
                node_types[label] = node_types.get(label, 0) + 1
                props = getattr(n, "properties", {})
                name = props.get("name", "")
                # Collect meaningful entities (skip structural nodes)
                if label not in ("Context", "KnowledgeBase", "CodeBase", "SystemStore",
                                 "UserStore", "WebStore", "GeneratedStore", "MemoryStore",
                                 "ArtifactStore", "ToolStore", "Document", "Passage",
                                 "PipelineRun", "Session") and name:
                    key_entities.append({"name": name, "type": label})
                if label == "Fact":
                    stmt = props.get("statement", "")
                    if stmt:
                        facts.append(stmt[:120])

            for e in graph.get_all_edges():
                label = getattr(e, "label", "unknown")
                edge_types[label] = edge_types.get(label, 0) + 1

        sessions = []
        if session_manager:
            sessions = session_manager.get_context_session_ids(context_id)

        return {
            "context_id": ctx.context_id,
            "name": ctx.name,
            "description": ctx.description,
            "graph_namespace": ns,
            "node_count": sum(node_types.values()),
            "edge_count": sum(edge_types.values()),
            "node_types": node_types,
            "edge_types": edge_types,
            "key_entities": key_entities[:20],
            "facts": facts[:10],
            "sessions": sessions,
            "status": ctx.status,
        }

    @router.patch("/contexts/{context_id}")
    async def update_context(context_id: str, req: UpdateContextRequest, user=Depends(require_member)):
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        updates = req.dict(exclude_none=True)
        ctx = context_manager.update_context(context_id, **updates)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        if audit_log:
            audit_log.log(
                user_id=user.user_id,
                action="context_updated",
                resource_type="context",
                resource_id=context_id,
                details=updates,
            )
        return ctx.to_dict()

    @router.delete("/contexts/{context_id}")
    async def delete_context(context_id: str, bg: BackgroundTasks, user=Depends(require_admin)):
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")

        # Get context before deleting (need graph_namespace for cleanup)
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        graph_ns = ctx.graph_namespace

        # Auto-cancel any active jobs for this context before deleting
        from ..ingestion.job_manager import get_job_manager, JobStatus
        from datetime import datetime, timezone
        jm = get_job_manager()
        cancelled_jobs = []
        now_ts = datetime.now(timezone.utc).isoformat()

        # Cancel in JobManager
        for job in list(jm._jobs.values()):
            if job.status.value in ("running", "queued") and (
                job.context_id == context_id
                or job.context_id == graph_ns
                or context_id in job.context_id
            ):
                job.status = JobStatus.FAILED
                job.error = "Cancelled — context deleted"
                job.completed_at = now_ts
                job.add_log("system", f"Auto-cancelled: context {context_id} deleted")
                jm._active_count = max(0, jm._active_count - 1)
                jm._save_to_redis(job)
                cancelled_jobs.append(job.job_id)

        # Cancel in legacy _ingest_jobs dict
        for jid, j in list(_ingest_jobs.items()):
            if j.get("status") == "processing" and (
                j.get("context_id") == context_id
                or j.get("graph") == ctx.name
                or j.get("graph") == graph_ns
            ):
                j["status"] = "failed"
                j["error"] = "Cancelled — context deleted"
                if jid not in cancelled_jobs:
                    cancelled_jobs.append(jid)

        if cancelled_jobs:
            logger.info("[DELETE] Auto-cancelled %d job(s) for context %s: %s",
                        len(cancelled_jobs), context_id, cancelled_jobs)

        # Delete the context record
        ok = context_manager.delete_context(context_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Context not found")

        # Cascade delete in background (don't block the response)
        def _cascade_delete():
            deleted = {"context": True}

            # 0. Remove cancelled jobs from tracking dicts
            for jid in cancelled_jobs:
                _ingest_jobs.pop(jid, None)
                jm.remove_job(jid)
            if cancelled_jobs:
                deleted["cancelled_jobs"] = len(cancelled_jobs)

            # 1. Delete graph from registry + disk (try scoped and bare name)
            if graph_ns and graph_registry:
                try:
                    graph_registry.delete_graph(graph_ns)
                    deleted["graph"] = True
                    # Also try bare name if scoped
                    if ":" in graph_ns:
                        bare = graph_ns.split(":", 1)[1]
                        graph_registry.delete_graph(bare)
                    logger.info("[CASCADE] Deleted graph '%s'", graph_ns)
                except Exception as e:
                    logger.warning("[CASCADE] Graph delete failed: %s", e)

            # 2. Delete from Redis graph storage (try scoped and bare name)
            try:
                from ..storage.redis_graph_storage import RedisGraphStorage
                for ns in set([graph_ns] + ([graph_ns.split(":", 1)[1]] if ":" in graph_ns else [])):
                    rgs = RedisGraphStorage(ns)
                    keys = rgs.delete_graph()
                    if keys:
                        deleted["redis_graph"] = deleted.get("redis_graph", 0) + keys
            except Exception:
                pass  # optional

            # 2b. Delete disk files (namespaces dir + metadata.json entry)
            try:
                import shutil
                from pathlib import Path
                for ns in set([graph_ns] + ([graph_ns.split(":", 1)[1]] if ":" in graph_ns else [])):
                    for base in ["contextcore_data/namespaces", "contextcore_data"]:
                        ns_dir = Path(base) / ns.replace(":", "_")
                        if ns_dir.exists():
                            shutil.rmtree(ns_dir)
                            deleted["disk"] = True
                    # Remove from metadata.json so migration doesn't re-create it
                    mf = Path("contextcore_data/metadata.json")
                    if mf.exists():
                        try:
                            import json as _mj
                            meta = _mj.loads(mf.read_text())
                            if ns in meta:
                                del meta[ns]
                                mf.write_text(_mj.dumps(meta, indent=2))
                        except Exception:
                            pass  # optional
            except Exception:
                pass  # optional

            # 3. Delete vector store data
            try:
                from ..context.vector_integration import SessionVectorStore
                svs = SessionVectorStore()
                svs.delete_session(graph_ns)
                deleted["vectors"] = True
            except Exception:
                pass  # optional

            # 4. Delete BM25 index
            try:
                import shutil
                safe_ns = graph_ns.replace(":", "_")
                bm25_dir = os.path.join("contextcore_data", "bm25_index", safe_ns)
                if os.path.exists(bm25_dir):
                    shutil.rmtree(bm25_dir)
                    deleted["bm25"] = True
            except Exception:
                pass  # optional

            # 5. Delete Redis Search index
            try:
                from ..search.redis_search import get_search_index
                ft = get_search_index()
                if ft:
                    ft.drop_index(graph_ns)
                    deleted["redis_search"] = True
            except Exception:
                pass  # optional

            # 6. Invalidate query cache
            try:
                from ..aiql.engine.executor import _query_cache
                _query_cache.invalidate(graph_ns)
                deleted["query_cache"] = True
            except Exception:
                pass  # optional

            # 7. Notify other workers to evict
            try:
                from ..core.graph_sync import get_graph_sync
                sync = get_graph_sync()
                if sync:
                    sync.notify_write(graph_ns)
            except Exception:
                pass  # optional

            logger.info("[CASCADE] Context %s cleanup: %s", context_id, deleted)

        bg.add_task(_cascade_delete)

        event_bus.emit("context_deleted", {"context_id": context_id, "graph": graph_ns})
        event_bus.emit("graph_deleted", {"context_id": context_id, "graph": graph_ns})
        if audit_log:
            audit_log.log(
                user_id=user.user_id,
                action="context_deleted",
                resource_type="context",
                resource_id=context_id,
                details={"graph_namespace": graph_ns},
            )
        return {"status": "deleted", "graph_cleanup": "scheduled", "cancelled_jobs": cancelled_jobs}

    @router.post("/contexts/{context_id}/text")
    async def add_context_text(context_id: str, req: AddContextTextRequest, user=Depends(require_member)):
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        result = context_manager.add_text(
            context_id=context_id,
            content=req.content,
            role=req.role,
            label=req.label,
            sensitivity=req.sensitivity,
            tags=req.tags,
        )
        if result is None:
            raise HTTPException(status_code=404, detail="Context not found")
        return result

    @router.get("/contexts/{context_id}/items")
    async def get_context_items(context_id: str, category: Optional[str] = None,
                                show_all: bool = False, user=Depends(user_auth)):
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        items = context_manager.get_items(context_id, category=category, show_all=show_all)
        return {"items": items, "total": len(items)}

    @router.get("/contexts/{context_id}/items/{node_id}/provenance")
    async def get_item_provenance(context_id: str, node_id: str, user=Depends(user_auth)):
        """Get the source passage/chunk that a Fact or Entity was extracted from."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        ns = ctx.graph_namespace
        graph = graph_registry.get_graph(ns, load_if_missing=True) if graph_registry else None
        if not graph and graph_registry:
            graph = graph_registry.create_graph(ns)
        if not graph:
            return {"passages": [], "facts": [], "entities": []}

        # Try multiple access methods (Redis/LMDB/CSR/direct)
        adapter = graph.csr_adapter if hasattr(graph, 'csr_adapter') else None
        node = None
        # Try adapter first, then graph directly
        if adapter and hasattr(adapter, 'get_node'):
            node = adapter.get_node(node_id)
        if not node and hasattr(graph, 'get_node'):
            node = graph.get_node(node_id)
        if not node:
            # URL-decoded node_id might have been double-encoded
            import urllib.parse
            decoded_id = urllib.parse.unquote(node_id)
            if decoded_id != node_id:
                if adapter and hasattr(adapter, 'get_node'):
                    node = adapter.get_node(decoded_id)
                if not node and hasattr(graph, 'get_node'):
                    node = graph.get_node(decoded_id)
        if not node:
            # Return empty provenance instead of 404 — node might be a CU or system node
            return {"node_id": node_id, "passages": [], "facts": [], "entities": []}

        node_label = getattr(node, "label", getattr(node, "node_type", ""))
        result = {"node_id": node_id, "node_label": node_label, "passages": [], "facts": [], "entities": []}

        def _node_to_dict(n):
            props = getattr(n, "properties", {}) or {}
            if isinstance(props, str):
                try:
                    props = json.loads(props)
                except Exception:
                    props = {}
            return {
                "node_id": str(getattr(n, "id", "")),
                "label": getattr(n, "label", getattr(n, "node_type", "")),
                "name": props.get("name", props.get("title", props.get("statement", ""))),
                "content": props.get("content", props.get("statement", props.get("name", ""))),
                **{k: v for k, v in props.items() if k not in ("name", "content")},
            }

        # Helper: get neighbors via adapter (handles Redis/LMDB/CSR)
        def _get_outgoing(nid, edge_type=None):
            if adapter and hasattr(adapter, 'get_neighbors'):
                return adapter.get_neighbors(nid, edge_type)
            return []

        def _get_incoming(nid, edge_type=None):
            if adapter and hasattr(adapter, 'get_incoming_neighbors'):
                return adapter.get_incoming_neighbors(nid, edge_type)
            return []

        def _get_node(nid):
            return adapter.get_node(nid) if adapter else None

        if node_label == "Fact":
            # Fact -> find source Passage via INCOMING STATES edge
            for src_id, _ in _get_incoming(node_id, "STATES"):
                src_node = _get_node(src_id)
                if src_node and getattr(src_node, "label", getattr(src_node, "node_type", "")) in ("Passage", "TextChunk"):
                    result["passages"].append(_node_to_dict(src_node))
            # Fact -> find mentioned Entities via OUTGOING MENTIONS edge
            for tgt_id, _ in _get_outgoing(node_id, "MENTIONS"):
                tgt_node = _get_node(tgt_id)
                if tgt_node:
                    result["entities"].append(_node_to_dict(tgt_node))
        elif node_label in ("Document", "WebPage"):
            # Document -> find child Passages via OUTGOING CONTAINS edge
            for tgt_id, _ in _get_outgoing(node_id, "CONTAINS"):
                tgt_node = _get_node(tgt_id)
                if tgt_node:
                    result["passages"].append(_node_to_dict(tgt_node))
            # Document -> find Links via OUTGOING HAS_LINK/LINKS_TO edge
            for edge_type in ("HAS_LINK", "LINKS_TO"):
                for tgt_id, _ in _get_outgoing(node_id, edge_type):
                    tgt_node = _get_node(tgt_id)
                    if tgt_node:
                        result["entities"].append(_node_to_dict(tgt_node))
        elif node_label in ("Passage", "TextChunk"):
            # Passage -> find parent Document via INCOMING CONTAINS edge
            for doc_id, _ in _get_incoming(node_id, "CONTAINS"):
                doc_node = _get_node(doc_id)
                if doc_node and getattr(doc_node, "label", getattr(doc_node, "node_type", "")) in ("Document", "WebPage"):
                    result["document"] = _node_to_dict(doc_node)
                    break
            # Passage -> find Facts via OUTGOING STATES edge
            for tgt_id, _ in _get_outgoing(node_id, "STATES"):
                tgt_node = _get_node(tgt_id)
                if tgt_node:
                    result["facts"].append(_node_to_dict(tgt_node))
            # Also find entities mentioned in this passage
            for tgt_id, _ in _get_outgoing(node_id, "MENTIONS"):
                tgt_node = _get_node(tgt_id)
                if tgt_node:
                    result["entities"].append(_node_to_dict(tgt_node))
        elif node_label == "Topic":
            # Topic -> find Turns via INCOMING RAISES edges
            result["turns"] = []
            for src_id, _ in _get_incoming(node_id, "RAISES"):
                src_node = _get_node(src_id)
                if src_node:
                    result["turns"].append(_node_to_dict(src_node))
            # Topic -> find Entities via OUTGOING INVOLVES edges
            for tgt_id, _ in _get_outgoing(node_id, "INVOLVES"):
                tgt_node = _get_node(tgt_id)
                if tgt_node:
                    result["entities"].append(_node_to_dict(tgt_node))
        elif node_label in ("Session",):
            # Session -> find Turns via OUTGOING CONTAINS_TURN edges
            result["turns"] = []
            for tgt_id, _ in _get_outgoing(node_id, "CONTAINS_TURN"):
                tgt_node = _get_node(tgt_id)
                if tgt_node:
                    result["turns"].append(_node_to_dict(tgt_node))
        elif node_label == "Link":
            # Link -> find parent Document/Passage via INCOMING HAS_LINK edge
            for src_id, _ in _get_incoming(node_id, "HAS_LINK"):
                src_node = _get_node(src_id)
                if src_node:
                    src_label = getattr(src_node, "label", getattr(src_node, "node_type", ""))
                    if src_label in ("Document", "WebPage"):
                        result["document"] = _node_to_dict(src_node)
                    else:
                        result["passages"].append(_node_to_dict(src_node))
        else:
            # Entity or other -> find via INCOMING MENTIONS/RAISES/INVOLVES edges
            for edge_type in ("MENTIONS", "RAISES", "INVOLVES", "ASSIGNS", "PROPOSES", "HAS_LINK", "CONTAINS"):
                for src_id, _ in _get_incoming(node_id, edge_type):
                    src_node = _get_node(src_id)
                    if not src_node:
                        continue
                    src_label = getattr(src_node, "label", getattr(src_node, "node_type", ""))
                    if src_label == "Fact":
                        result["facts"].append(_node_to_dict(src_node))
                    elif src_label in ("Passage", "TextChunk", "Turn"):
                        result["passages"].append(_node_to_dict(src_node))
                    elif src_label == "Topic":
                        result.setdefault("topics", []).append(_node_to_dict(src_node))
                    else:
                        result["entities"].append(_node_to_dict(src_node))

        # Deduplicate by node_id
        seen = set()
        for key in ("passages", "facts", "entities"):
            deduped = []
            for item in result[key]:
                nid = item.get("node_id")
                if nid not in seen:
                    seen.add(nid)
                    deduped.append(item)
            result[key] = deduped

        return result

    @router.post("/contexts/{context_id}/refresh")
    async def refresh_context_stats(context_id: str, user=Depends(require_member)):
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.refresh_stats(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        return ctx.to_dict()

    # ------------------------------------------------------------------
    # Context pipeline run history
    # ------------------------------------------------------------------

    @router.get("/contexts/{context_id}/pipeline-runs")
    async def get_context_pipeline_runs(
        context_id: str,
        limit: int = Query(20, ge=1, le=100),
        user=Depends(user_auth),
    ):
        """Return the run history for a context's scheduled pipeline.

        Run records are stored in Redis under the key
        ``context_pipeline:{context_id}:runs`` (newest first, capped at 100).
        """
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        from ..pipelines.executor import _get_context_runs
        runs = _get_context_runs(context_id, limit=limit)
        return {
            "context_id": context_id,
            "runs": runs,
            "total": len(runs),
        }

    # ------------------------------------------------------------------
    # Context pipeline actions (run / pause / resume)
    # Registered BEFORE plain /contexts/{context_id} catch-alls so FastAPI
    # does not mistake "pipeline" for a context_id value.
    # ------------------------------------------------------------------

    @router.post("/contexts/{context_id}/pipeline-runs")
    async def run_context_pipeline_alias(context_id: str, bg: BackgroundTasks, user=Depends(user_auth)):
        """Alias: trigger a manual pipeline run (used by PipelinesPage Monitor tab)."""
        return await run_context_pipeline(context_id, bg, user)

    @router.post("/contexts/{context_id}/pipeline/run")
    async def run_context_pipeline(context_id: str, bg: BackgroundTasks, user=Depends(user_auth)):
        """Trigger a manual pipeline run for the context."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        pipeline_config = ctx.config.get("pipeline", {})
        if not pipeline_config.get("source_url") and not pipeline_config.get("template_id"):
            raise HTTPException(status_code=400, detail="No pipeline configured for this context. Select a pipeline template first.")
        from ..pipelines.executor import execute_context_pipeline
        bg.add_task(execute_context_pipeline, context_id, context_manager, graph_registry)
        return {"status": "submitted", "context_id": context_id}

    @router.post("/contexts/{context_id}/pipeline/pause")
    async def pause_context_pipeline(context_id: str, user=Depends(user_auth)):
        """Pause the scheduled pipeline for a context."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        schedule = ctx.config.get("schedule", {})
        schedule["status"] = "paused"
        ctx.config["schedule"] = schedule
        context_manager.update_context(context_id, config=ctx.config)
        return {"status": "paused"}

    @router.post("/contexts/{context_id}/pipeline/resume")
    async def resume_context_pipeline(context_id: str, user=Depends(user_auth)):
        """Resume a paused scheduled pipeline for a context."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        schedule = ctx.config.get("schedule", {})
        schedule["status"] = "active"
        from datetime import datetime, timezone, timedelta
        pipeline = ctx.config.get("pipeline", {})
        interval = pipeline.get("interval_minutes", 60)
        schedule["next_run_at"] = (datetime.now(timezone.utc) + timedelta(minutes=interval)).isoformat()
        ctx.config["schedule"] = schedule
        context_manager.update_context(context_id, config=ctx.config)
        return {"status": "active"}

    @router.post("/contexts/{context_id}/pipeline/cleanup-orphans")
    async def cleanup_orphan_nodes(context_id: str, user=Depends(require_member)):
        """Remove orphan nodes (nodes with zero edges) from a context's graph.
        Skips system nodes (Context, ContextMeta, ContextIntelligence, etc.)."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        namespace = ctx.graph_namespace or context_id
        db = graph_registry.get_graph(namespace, load_if_missing=True)
        if not db:
            raise HTTPException(status_code=404, detail="Graph not found")

        # System node types to keep even if orphaned
        keep_types = {"Context", "ContextMeta", "ContextIntelligence", "KnowledgeBase",
                      "CodeBase", "SystemStore", "UserStore", "WebStore", "GeneratedStore",
                      "MemoryStore", "ArtifactStore", "ToolStore", "VectorIndex", "BM25Index",
                      "Project", "Session"}
        deleted = 0
        try:
            all_nodes = list(db.csr_adapter.get_all_nodes())
            for node in all_nodes:
                label = getattr(node, "node_type", getattr(node, "label", ""))
                if label in keep_types:
                    continue
                nid = getattr(node, "id", None)
                if not nid:
                    continue
                # Check if node has any edges
                neighbors = db.csr_adapter.get_neighbors(nid) if hasattr(db.csr_adapter, 'get_neighbors') else []
                edges = db.get_edges_for_node(nid) if hasattr(db, 'get_edges_for_node') else []
                if not neighbors and not edges:
                    db.remove_node(nid)
                    deleted += 1
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Cleanup failed: {e}")

        # Refresh stats
        try:
            context_manager.refresh_stats(context_id)
        except Exception:
            pass

        return {"deleted": deleted, "context_id": context_id}

    @router.post("/contexts/{context_id}/pipeline-runs/{run_id}/rollback")
    async def rollback_pipeline_run(context_id: str, run_id: str, user=Depends(require_member)):
        """Rollback a pipeline run — delete all nodes created by that run."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        from ..pipelines.executor import rollback_run
        result = rollback_run(context_id, run_id, context_manager, graph_registry)
        if result.get("status") == "error":
            raise HTTPException(status_code=400, detail=result.get("error", "Rollback failed"))
        return result

    # ------------------------------------------------------------------
    # Context search & RAG
    # ------------------------------------------------------------------

    @router.post("/contexts/{context_id}/search")
    async def search_context(context_id: str, req: ContextSearchRequest, user=Depends(user_auth)):
        """Search within a context's backing graph by keyword matching."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        db = graph_registry.get_graph(ctx.graph_namespace)
        if not db:
            return {"results": [], "total": 0}

        from ..context.boundaries import BOUNDARY_NODE_LABELS
        from ..context.context_manager import ContextManager
        query_terms = req.query.lower().split()
        if not query_terms:
            return {"results": [], "total": 0}

        # Get relevant labels for this context type
        relevant_labels = ContextManager._TYPE_RELEVANT_LABELS.get(ctx.context_type)

        results = []
        # Field importance weights
        field_weights = {"name": 2.0, "content": 1.0, "statement": 1.5, "label": 0.5}

        for node in db.get_all_nodes():
            if isinstance(node, dict):
                node_label = node.get("label", "")
                props = node.get("properties", {})
                node_id = str(node.get("id", ""))
            else:
                node_label = getattr(node, "label", "")
                props = getattr(node, "properties", {}) or {}
                node_id = str(getattr(node, "id", ""))

            if node_label in BOUNDARY_NODE_LABELS:
                continue
            if relevant_labels and node_label not in relevant_labels:
                continue
            if req.category and props.get("category") != req.category:
                continue

            score = 0.0
            snippet = ""
            searchable = {
                "name": str(props.get("name", "")),
                "content": str(props.get("content", "")),
                "statement": str(props.get("statement", "")),
                "label": str(node_label),
            }
            matched_terms = 0
            for field_name, val in searchable.items():
                val_lower = val.lower()
                weight = field_weights[field_name]
                for term in query_terms:
                    if term in val_lower:
                        # Count occurrences for term frequency
                        count = val_lower.count(term)
                        score += weight * min(count, 5)  # cap at 5 hits per field
                        matched_terms += 1
                        if not snippet and len(val) > 0:
                            idx = val_lower.find(term)
                            start = max(0, idx - 80)
                            end = min(len(val), idx + len(term) + 120)
                            snippet = val[start:end]

            if score > 0:
                # Normalize: bonus for matching more query terms
                term_coverage = matched_terms / max(len(query_terms) * len(searchable), 1)
                score = score * (1.0 + term_coverage)
                results.append({
                    "node_id": node_id,
                    "node_type": node_label,
                    "name": props.get("name", ""),
                    "content": str(props.get("content", "") or props.get("statement", "")),
                    "snippet": snippet,
                    "score": round(score, 3),
                    "category": props.get("category"),
                    "properties": props,
                })

        # Normalize scores to 0–1 range
        if results:
            max_score = max(r["score"] for r in results)
            if max_score > 0:
                for r in results:
                    r["score"] = round(r["score"] / max_score, 3)

        results.sort(key=lambda r: r["score"], reverse=True)
        results = results[:req.limit]
        return {"results": results, "total": len(results)}

    @router.post("/contexts/{context_id}/rag")
    async def rag_context(context_id: str, req: ContextRAGRequest, user=Depends(user_auth)):
        """RAG: retrieve relevant nodes from context, generate answer via LLM."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx_obj = context_manager.get_context(context_id)
        if not ctx_obj:
            raise HTTPException(status_code=404, detail="Context not found")
        db = graph_registry.get_graph(ctx_obj.graph_namespace)
        if not db:
            return {"answer": "No data in this context yet.", "sources": []}

        # 1a. Vector semantic search (if vectors exist)
        vector_results = {}  # node_id → score
        try:
            from ..vector.vector_db_manager import get_vector_db_manager
            from ..models.embedding_service import AIContextDBEmbeddingService
            ns = ctx_obj.graph_namespace
            emb_svc = AIContextDBEmbeddingService()
            q_result = emb_svc.embed_text(req.question, model_name="nomic-embed-text")
            if q_result.get("success") and q_result.get("embedding"):
                mgr = get_vector_db_manager()
                vec_store = mgr.create_store(
                    dimension=len(q_result["embedding"]),
                    metric="cosine",
                    collection_name=f"{ns}_passages",
                )
                hits = vec_store.search(q_result["embedding"], k=req.limit * 2)
                for hit in hits:
                    vector_results[hit["node_id"]] = hit["score"]
        except Exception as vec_err:
            logger.debug("Vector search skipped: %s", vec_err)

        # 1b. Keyword search
        from ..context.boundaries import BOUNDARY_NODE_LABELS
        query_terms = req.question.lower().split()
        scored = []
        rag_field_weights = {"name": 2.0, "content": 1.0, "statement": 1.5}
        # Skip structure nodes
        structure_labels = {"Context", "KnowledgeBase", "CodeBase", "SystemStore",
                           "UserStore", "WebStore", "GeneratedStore", "PipelineRun"}
        for node in db.get_all_nodes():
            if isinstance(node, dict):
                node_label = node.get("label", "")
                props = node.get("properties", {})
                node_id = str(node.get("id", ""))
            else:
                node_label = getattr(node, "label", "")
                props = getattr(node, "properties", {}) or {}
                node_id = str(getattr(node, "id", ""))
            if node_label in BOUNDARY_NODE_LABELS or node_label in structure_labels:
                continue
            # Keyword score
            kw_score = 0.0
            for field, weight in rag_field_weights.items():
                val = str(props.get(field, "")).lower()
                for term in query_terms:
                    if term in val:
                        kw_score += weight * min(val.count(term), 5)
            # Vector score (0-1, higher = more similar)
            vec_score = vector_results.get(node_id, 0.0)
            # Combined: keyword (normalized later) + vector boost
            combined = kw_score + (vec_score * 10.0)  # boost vector matches
            if combined > 0:
                scored.append({"node_id": node_id, "label": node_label, "props": props,
                               "score": combined, "kw_score": kw_score, "vec_score": vec_score})
        # Normalize to 0–1
        if scored:
            max_s = max(s["score"] for s in scored)
            if max_s > 0:
                for s in scored:
                    s["score"] = round(s["score"] / max_s, 3)
        scored.sort(key=lambda x: x["score"], reverse=True)
        top = scored[:req.limit]

        if not top:
            return {"answer": "No relevant information found in this context.", "sources": []}

        # 2. Build context + call LLM
        try:
            from ..llm.client import get_llm_client
            llm = get_llm_client()

            # Build prompt
            context_parts = []
            for r in top:
                content = r["props"].get("content") or r["props"].get("statement") or r["props"].get("name", "")
                source_label = r["props"].get("name", r["label"])
                context_parts.append(f"[Source: {source_label}]\n{content}")
            context_block = "\n\n---\n\n".join(context_parts)

            prompt = (
                f"Answer the following question based ONLY on the provided context. "
                f"Cite the source names in your answer. If the context doesn't contain enough "
                f"information, say so.\n\n"
                f"## Context\n\n{context_block}\n\n"
                f"## Question\n\n{req.question}\n\n"
                f"## Answer\n\n"
            )

            answer = llm.generate(prompt=prompt, max_tokens=4000)
            return {
                "answer": answer,
                "model": getattr(llm, 'model', None),
                "sources": [{"node_id": r["node_id"], "label": r["props"].get("name", r["label"]), "score": r["score"]} for r in top],
            }
        except Exception as e:
            logger.warning("RAG generation failed: %s", e)
            context_text = "\n\n".join(
                f"[{r['label']}] {r['props'].get('content') or r['props'].get('statement') or r['props'].get('name', '')}"
                for r in top
            )
            return {
                "answer": f"Retrieved {len(top)} relevant items (LLM unavailable: {e}):\n\n{context_text}",
                "sources": [{"node_id": r["node_id"], "label": r["props"].get("name", r["label"]), "score": r["score"]} for r in top],
            }

    # ------------------------------------------------------------------
    # Context ingestion endpoints
    # ------------------------------------------------------------------

    @router.get("/contexts/ingest/models")
    async def list_context_ingest_models(user=Depends(user_auth)):
        """Return available LLM and embedding models for context ingestion."""
        # Delegate to the main ingest models endpoint logic
        return await list_ingest_models(user=user)

    # ------------------------------------------------------------------
    # Context Source Policy
    # ------------------------------------------------------------------

    @router.get("/contexts/{context_id}/policy")
    async def get_context_source_policy(context_id: str, user=Depends(user_auth)):
        """Get the source policy for a context."""
        if not context_manager:
            raise HTTPException(501, "Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(404, "Context not found")
        from ..context.source_policy import get_policy_summary
        return get_policy_summary(ctx)

    @router.put("/contexts/{context_id}/policy")
    async def set_context_source_policy(context_id: str, req: dict = Body(...), user=Depends(require_admin)):
        """Set the source policy for a context (admin only).

        Body: {"allowed_domains": [...], "blocked_domains": [...], "allowed_types": [...], ...}
        """
        if not context_manager:
            raise HTTPException(501, "Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(404, "Context not found")
        # Update config.source_policy
        config = dict(ctx.config or {})
        config["source_policy"] = req
        context_manager.update_context(context_id, config=config)
        from ..context.source_policy import get_policy_summary
        ctx = context_manager.get_context(context_id)
        return get_policy_summary(ctx)

    # ------------------------------------------------------------------
    # Extraction Schema Registry
    # ------------------------------------------------------------------

    @router.get("/schemas")
    async def list_extraction_schemas(tag: str | None = None, user=Depends(user_auth)):
        """List all available extraction schemas (builtin + custom)."""
        from ..extraction.schema_registry import get_schema_registry
        schemas = get_schema_registry().list_schemas()
        if tag is not None:
            tag_lower = tag.lower()
            schemas = [
                s for s in schemas
                if tag_lower in [t.lower() for t in s.get("tags", [])]
            ]
        return {"schemas": schemas}

    @router.get("/schemas/{name}")
    async def get_extraction_schema_detail(name: str, user=Depends(user_auth)):
        """Get a schema's YAML content by name."""
        from ..extraction.schema_registry import get_schema_registry
        yaml_content = get_schema_registry().get_schema(name)
        if not yaml_content:
            raise HTTPException(404, f"Schema '{name}' not found")
        import yaml as _yaml
        parsed = _yaml.safe_load(yaml_content)
        return {"name": name, "yaml": yaml_content, "parsed": parsed}

    @router.post("/schemas")
    async def save_custom_schema(req: dict = Body(...), user=Depends(require_admin)):
        """Save a custom extraction schema."""
        from ..extraction.schema_registry import get_schema_registry
        name = req.get("name", "")
        yaml_content = req.get("yaml", "")
        if not name or not yaml_content:
            raise HTTPException(400, "name and yaml are required")
        try:
            result = get_schema_registry().save_custom(
                name, yaml_content,
                description=req.get("description", ""),
                industry=req.get("industry", ""),
                tags=req.get("tags", []),
            )
            return result
        except ValueError as e:
            raise HTTPException(400, str(e))

    @router.delete("/schemas/{name}")
    async def delete_custom_schema(name: str, user=Depends(require_admin)):
        """Delete a custom schema (cannot delete builtins)."""
        from ..extraction.schema_registry import get_schema_registry
        deleted = get_schema_registry().delete_custom(name)
        if not deleted:
            raise HTTPException(404, "Custom schema not found (builtin schemas cannot be deleted)")
        return {"deleted": True}

    # ------------------------------------------------------------------
    # Context Graph Schema
    # ------------------------------------------------------------------

    @router.get("/contexts/schema")
    async def get_context_schema(user=Depends(user_auth)):
        """Return the current context graph schema (categories, children, edges)."""
        from ..context.context_schema import load_schema
        return load_schema()

    @router.put("/contexts/schema")
    async def update_context_schema(req: dict = Body(...), user=Depends(require_admin)):
        """Update the default context graph schema (admin only)."""
        if "categories" not in req:
            raise HTTPException(status_code=400, detail="Schema must have 'categories' key")
        try:
            import yaml
            from ..context.context_schema import _DEFAULT_SCHEMA_PATH
            with open(_DEFAULT_SCHEMA_PATH, "w", encoding="utf-8") as f:
                yaml.dump(req, f, default_flow_style=False, sort_keys=False)
            return {"status": "updated", "categories": len(req["categories"])}
        except ImportError:
            raise HTTPException(status_code=501, detail="pyyaml not installed")

    def _check_source_policy(ctx, source_url=None, source_type=None, filename=None, file_size_mb=None):
        """Check context source policy. Raises HTTPException if blocked."""
        from ..context.source_policy import check_source_allowed
        allowed, reason = check_source_allowed(ctx, source_url=source_url,
                                                source_type=source_type, filename=filename,
                                                file_size_mb=file_size_mb)
        if not allowed:
            raise HTTPException(status_code=403, detail=f"Source blocked by policy: {reason}")

    @router.post("/contexts/{context_id}/ingest/text")
    async def context_ingest_text(
        context_id: str,
        bg: BackgroundTasks,
        req: dict = Body(...),
        user=Depends(require_member),
    ):
        """Ingest text into a context (runs pipeline on its backing graph)."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        _check_source_policy(ctx, source_type="text")

        text = req.get("text", "")
        title = req.get("title", "Untitled")
        pipeline_id = req.get("pipeline_id") or None
        schema_yaml = req.get("schema") or None
        github_token = req.get("github_token") or None  # optional per-request token

        # Resolve pipeline → LLM/embedding params
        llm_model, embedding_model = _resolve_pipeline_params(
            pipeline_id, req.get("llm_model"), req.get("embedding_model"))

        if not text.strip():
            raise HTTPException(status_code=400, detail="Text is required")

        scoped = ctx.graph_namespace
        try:
            _tid = _get_tenant(user).tenant_id
        except Exception:
            _tid = None

        # Build stage list for progress tracking
        if llm_model or embedding_model:
            _stages = ["PARSE_FILE", "CHUNK"]
            if llm_model:
                _stages.extend(["EXTRACT_FACTS", "EXTRACT"])
            if embedding_model:
                _stages.append("EMBED")
            if llm_model:
                _stages.append("INDEX_BM25")
            if embedding_model:
                _stages.append("STORE_VECTORS")
            if llm_model:
                _stages.extend(["CANONICALIZE", "ENHANCE_GRAPH"])
            _stages.append("PERSIST")
        else:
            _stages = ["CHUNK", "PERSIST"]

        job_id = _create_job(title, ctx.name, llm_model, embedding_model,
                             context_id=context_id, tenant_id=_tid, stage_names=_stages)

        _is_repo_input = text.strip().startswith("https://github.com/") or (
            __import__("os").path.isdir(text.strip()) if text.strip() else False
        )
        if llm_model or embedding_model or _is_repo_input:
            _submit_ingest(job_id, _ingest_pipeline, job_id, scoped, text.strip(),
                        {"title": title, "source": "text_input", "context_id": context_id,
                         "github_token": github_token},
                        llm_model, embedding_model, schema_yaml, context_id)
        else:
            chunks = _chunk_text(text.strip())
            nodes = []
            for i, chunk in enumerate(chunks):
                nodes.append(GraphNode(
                    id=str(_uuid.uuid4()), label="TextChunk",
                    properties={
                        "name": f"{title} (chunk {i + 1}/{len(chunks)})" if len(chunks) > 1 else title,
                        "content": chunk, "source": "text_input",
                        "char_count": len(chunk), "chunk_index": i,
                    },
                ))
            _submit_ingest(job_id, _ingest_nodes, job_id, scoped, nodes)

        # Update context source metadata
        context_manager.update_context(context_id, source=f"text:{title}")

        return {"status": "accepted", "job_id": job_id, "context": ctx.name}

    @router.post("/contexts/{context_id}/ingest/file")
    async def context_ingest_file(
        context_id: str,
        bg: BackgroundTasks,
        file: UploadFile = File(...),
        pipeline_id: str = Form(""),
        llm_model: str = Form(""),
        embedding_model: str = Form(""),
        graph_schema: str = Form(""),
        user=Depends(require_member),
    ):
        """Upload a file and ingest into a context."""
        schema = graph_schema
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        _check_source_policy(ctx, source_type="file", filename=file.filename)

        if not file.filename:
            raise HTTPException(status_code=400, detail="No file provided")

        content = await file.read()

        scoped = ctx.graph_namespace
        try:
            _tid = _get_tenant(user).tenant_id
        except Exception:
            _tid = None
        # Resolve pipeline → LLM/embedding params
        _llm, _emb = _resolve_pipeline_params(
            pipeline_id.strip() or None, llm_model.strip() or None, embedding_model.strip() or None)
        _schema = schema.strip() or None

        # Auto-detect chat exports and apply chat_history schema
        _ext = Path(file.filename).suffix.lower() if file.filename else ""
        if _ext in (".zip", ".json") and not _schema:
            try:
                if _ext == ".zip":
                    import zipfile as _zf, io as _io
                    with _zf.ZipFile(_io.BytesIO(content)) as zf:
                        if any("conversations" in n.lower() and n.endswith(".json") for n in zf.namelist()):
                            _schema = "chat_history"
                elif _ext == ".json":
                    import json as _jd
                    _s = _jd.loads(content.decode("utf-8", errors="replace")[:5000])
                    _it = _s if isinstance(_s, list) else _s.get("conversations", [])
                    if _it and isinstance(_it[0], dict) and ("mapping" in _it[0] or "chat_messages" in _it[0]):
                        _schema = "chat_history"
            except Exception:
                pass
            if _schema == "chat_history":
                logger.info("[INGEST] Auto-detected chat export in context %s: %s", context_id, file.filename)

        _stages = ["PARSE_FILE", "CLASSIFY"]
        job_id = _create_job(file.filename, ctx.name, _llm, _emb,
                             context_id=context_id, tenant_id=_tid, stage_names=_stages)

        # Chat export files — run connector + schema-guided extraction
        if _schema == "chat_history" or (_ext in (".zip", ".json") and _schema and "chat" in _schema):
            tmp_path = _save_upload_to_temp(content, file.filename)
            bg.add_task(_ingest_chat_export_pipeline, job_id, scoped, tmp_path,
                        file.filename, _llm, _emb, _schema, context_id)
        # Binary or graph-format files go through file extraction pipeline
        elif _needs_file_pipeline(file.filename):
            if _is_binary_upload(file.filename):
                tmp_path = _save_upload_to_temp(content, file.filename)
            else:
                tmp_path = _save_upload_to_temp(content, file.filename)
            bg.add_task(_ingest_file_via_pipeline, job_id, scoped, tmp_path,
                        file.filename, _llm, _emb, _schema, context_id)
        elif _llm or _emb:
            text = content.decode("utf-8", errors="replace")
            _submit_ingest(job_id, _ingest_pipeline, job_id, scoped, text.strip(),
                        {"title": file.filename, "source": "file_upload",
                         "filename": file.filename, "context_id": context_id,
                         "content_type": file.content_type or "text/plain"},
                        _llm, _emb, _schema, context_id)
        else:
            text = content.decode("utf-8", errors="replace")
            nodes = [
                GraphNode(
                    id=str(_uuid.uuid4()), label="Document",
                    properties={
                        "name": file.filename, "source": "file_upload",
                        "char_count": len(text),
                        "content_type": file.content_type or "text/plain",
                    },
                ),
            ]
            chunks = _chunk_text(text.strip())
            for i, chunk in enumerate(chunks):
                nodes.append(GraphNode(
                    id=str(_uuid.uuid4()), label="TextChunk",
                    properties={
                        "name": f"{file.filename} (chunk {i + 1}/{len(chunks)})",
                        "content": chunk, "source": "file_upload",
                        "filename": file.filename,
                        "char_count": len(chunk), "chunk_index": i,
                    },
                ))
            _submit_ingest(job_id, _ingest_nodes, job_id, scoped, nodes)

        # Update context source
        context_manager.update_context(context_id, source=f"document:{file.filename}")

        return {"status": "accepted", "job_id": job_id, "filename": file.filename, "context": ctx.name}

    @router.post("/contexts/{context_id}/ingest/url")
    async def context_ingest_url(
        context_id: str,
        bg: BackgroundTasks,
        req: dict = Body(...),
        user=Depends(require_member),
    ):
        """Fetch URL content and ingest into a context."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        url = req.get("url", "")
        _check_source_policy(ctx, source_type="url", source_url=url)

        crawl = req.get("crawl", False)
        max_pages = min(req.get("max_pages", 20), 50)
        max_depth = min(req.get("max_depth", 2), 5)
        pipeline_id = req.get("pipeline_id") or None
        schema_yaml = req.get("schema") or None

        # Resolve pipeline → LLM/embedding params
        llm_model, embedding_model = _resolve_pipeline_params(
            pipeline_id, req.get("llm_model"), req.get("embedding_model"))

        if not url.strip():
            raise HTTPException(status_code=400, detail="URL is required")

        scoped = ctx.graph_namespace
        try:
            _tid = _get_tenant(user).tenant_id
        except Exception:
            _tid = None

        if crawl:
            # Multi-page crawl mode
            _stages = ["CRAWL"]
            if llm_model or embedding_model:
                _stages.extend(["PARSE_FILE", "CHUNK", "DEDUP"])
                if llm_model:
                    _stages.extend(["EXTRACT_FACTS", "EXTRACT"])
                if embedding_model:
                    _stages.append("EMBED")
                if llm_model:
                    _stages.append("INDEX_BM25")
                if embedding_model:
                    _stages.append("STORE_VECTORS")
                if llm_model:
                    _stages.extend(["CANONICALIZE", "ENHANCE_GRAPH"])
                _stages.append("PERSIST")
            else:
                _stages.extend(["CHUNK", "DEDUP", "PERSIST"])

            job_id = _create_job(url, ctx.name, llm_model, embedding_model,
                                 context_id=context_id, tenant_id=_tid, stage_names=_stages)

            _submit_ingest(job_id, _crawl_and_ingest_context, job_id, scoped, url,
                           max_pages, max_depth, llm_model, embedding_model,
                           schema_yaml, context_id)

            # Update context source + type
            updates = {"source": f"web:{url}"}
            if ctx.context_type == "knowledge_base":
                updates["context_type"] = "web"
            context_manager.update_context(context_id, **updates)

            return {"status": "accepted", "job_id": job_id, "url": url,
                    "crawl": True, "max_pages": max_pages, "context": ctx.name}

        # Single-page mode
        if llm_model or embedding_model:
            _stages = ["PARSE_FILE", "CHUNK"]
            if llm_model:
                _stages.extend(["EXTRACT_FACTS", "EXTRACT"])
            if embedding_model:
                _stages.append("EMBED")
            if llm_model:
                _stages.append("INDEX_BM25")
            if embedding_model:
                _stages.append("STORE_VECTORS")
            if llm_model:
                _stages.extend(["CANONICALIZE", "ENHANCE_GRAPH"])
            _stages.append("PERSIST")
        else:
            _stages = ["CHUNK", "PERSIST"]

        job_id = _create_job(url, ctx.name, llm_model, embedding_model,
                             context_id=context_id, tenant_id=_tid, stage_names=_stages)

        # Fetch URL content
        try:
            req_obj = _urllib_request.Request(url, headers={"User-Agent": "AIContextDB/1.0"})
            with _urllib_request.urlopen(req_obj, timeout=15) as resp:
                raw = resp.read().decode("utf-8", errors="replace")
        except Exception as e:
            _finish_job(job_id, 0, f"Failed to fetch URL: {e}")
            return {"status": "error", "job_id": job_id, "nodes_created": 0, "message": str(e)}

        # Extract article content using trafilatura (proper article extractor)
        text = None
        title = url.split("/")[-1] or url
        if "<html" in raw.lower() or "<body" in raw.lower():
            try:
                import trafilatura
                text = trafilatura.extract(raw, include_links=False, include_tables=True)
                # Try to get title from metadata
                try:
                    meta_json = trafilatura.extract(raw, output_format="json")
                    if meta_json:
                        import json as _json
                        meta = _json.loads(meta_json)
                        title = meta.get("title") or title
                except Exception:
                    pass  # optional
            except ImportError:
                pass  # optional
            # Fallback: regex HTML stripping
            if not text:
                text = _re.sub(r"<script[^>]*>.*?</script>", "", raw, flags=_re.DOTALL | _re.IGNORECASE)
                text = _re.sub(r"<style[^>]*>.*?</style>", "", text, flags=_re.DOTALL | _re.IGNORECASE)
                text = _re.sub(r"<[^>]+>", " ", text)
                text = _re.sub(r"\s+", " ", text).strip()
        else:
            text = raw.strip()

        if not text:
            _finish_job(job_id, 0, "No text content extracted from URL")
            return {"status": "error", "job_id": job_id, "nodes_created": 0, "message": "No text content"}

        if llm_model or embedding_model:
            _submit_ingest(job_id, _ingest_pipeline, job_id, scoped, text,
                        {"title": title, "source": url, "url": url, "context_id": context_id},
                        llm_model, embedding_model, schema_yaml, context_id)
        else:
            chunks = _chunk_text(text)
            nodes = []
            for i, chunk in enumerate(chunks):
                nodes.append(GraphNode(
                    id=str(_uuid.uuid4()), label="TextChunk",
                    properties={
                        "name": f"{title} (chunk {i + 1}/{len(chunks)})" if len(chunks) > 1 else title,
                        "content": chunk, "source": url, "url": url,
                        "char_count": len(chunk), "chunk_index": i,
                    },
                ))
            _submit_ingest(job_id, _ingest_nodes, job_id, scoped, nodes)

        # Update context source + type
        updates = {"source": f"web:{url}"}
        if ctx.context_type == "knowledge_base":
            updates["context_type"] = "web"
        context_manager.update_context(context_id, **updates)

        return {"status": "accepted", "job_id": job_id, "url": url, "context": ctx.name}

    # ------------------------------------------------------------------
    # POST /contexts/{context_id}/ingest/connector/test — test connection
    # ------------------------------------------------------------------
    @router.post("/contexts/{context_id}/ingest/connector/test")
    async def test_connector(
        context_id: str,
        req: dict = Body(...),
        user=Depends(require_member),
    ):
        """Test a connector's connection before ingesting."""
        connector_type = req.get("connector_type", "")
        config = req.get("config", {})
        credentials = req.get("credentials", {})

        if not connector_type:
            raise HTTPException(status_code=400, detail="connector_type is required")

        try:
            from ..ingestion.connectors import get_ingestor
            ingestor = get_ingestor(connector_type, config, credentials)
        except Exception as e:
            return {"success": False, "message": str(e)}

        if not ingestor:
            return {"success": False, "message": f"Unsupported connector type: {connector_type}"}

        try:
            result = ingestor.test_connection()
            return result
        except Exception as e:
            return {"success": False, "message": str(e)}

    # POST /contexts/{context_id}/ingest/connector — connector ingestion
    # ------------------------------------------------------------------
    @router.post("/contexts/{context_id}/ingest/connector")
    async def context_ingest_connector(
        context_id: str,
        bg: BackgroundTasks,
        req: dict = Body(...),
        user=Depends(require_member),
    ):
        """Ingest data from an external connector into a context's graph.

        Request body:
            connector_type: database | github | jira | rest_api | salesforce | sap | servicenow
            config: {...}            — connector-specific config
            credentials: {...}       — inline credentials (or use integration_id)
            integration_id: "..."    — optional: use stored integration credentials
            phase: both | metadata | data   — default: both
            pipeline_id: "..."       — for Phase 2 extraction
        """
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")

        connector_type = req.get("connector_type", "")
        config = req.get("config", {})
        credentials = req.get("credentials", {})
        integration_id = req.get("integration_id")
        phase = req.get("phase", "both")
        pipeline_id = req.get("pipeline_id") or None

        if not connector_type:
            raise HTTPException(status_code=400, detail="connector_type is required")

        # Resolve credentials from integration registry if integration_id provided
        if integration_id:
            try:
                from .integrations import IntegrationRegistry
                _int_reg = IntegrationRegistry()
                tenant = _get_tenant(user)
                integration = _int_reg.get(integration_id, tenant.tenant_id)
                if integration:
                    config = {**integration.config, **config}  # req config overrides
                    credentials = integration.credentials or {}
                    if not connector_type:
                        connector_type = integration.connector_type
                else:
                    raise HTTPException(status_code=404, detail="Integration not found")
            except ImportError:
                pass  # optional

        # Verify connector type is supported
        try:
            from ..ingestion.connectors import get_ingestor
            ingestor = get_ingestor(connector_type, config, credentials)
        except Exception as e:
            raise HTTPException(status_code=400, detail=str(e))

        if not ingestor:
            raise HTTPException(status_code=400, detail=f"Unsupported connector type: {connector_type}")

        # Resolve LLM/embedding params for Phase 2
        llm_model, embedding_model = _resolve_pipeline_params(
            pipeline_id, req.get("llm_model"), req.get("embedding_model"))

        scoped = ctx.graph_namespace
        try:
            _tid = _get_tenant(user).tenant_id
        except Exception:
            _tid = None

        _stages = ["CONNECTOR_METADATA"]
        if phase in ("both", "data"):
            _stages.append("PARSE_FILE")
            _stages.append("CHUNK")
            if llm_model:
                _stages.extend(["EXTRACT_FACTS", "EXTRACT"])
            if embedding_model:
                _stages.append("EMBED")
            if llm_model:
                _stages.append("INDEX_BM25")
            if embedding_model:
                _stages.append("STORE_VECTORS")
            if llm_model:
                _stages.extend(["CANONICALIZE", "ENHANCE_GRAPH"])
            _stages.append("PERSIST")

        job_id = _create_job(
            f"{connector_type}://{config.get('connection_string', config.get('repo_url', config.get('domain', connector_type)))}",
            ctx.name, llm_model, embedding_model,
            context_id=context_id, tenant_id=_tid, stage_names=_stages)

        _submit_ingest(job_id, _ingest_connector, job_id, context_id, scoped,
                        connector_type, config, credentials, phase,
                        llm_model, embedding_model, None, integration_id)

        # Update context source
        source_label = config.get("connection_string", config.get("repo_url", config.get("domain", connector_type)))
        context_manager.update_context(context_id, source=f"connector:{connector_type}:{source_label}")

        return {
            "status": "accepted",
            "job_id": job_id,
            "connector_type": connector_type,
            "phase": phase,
            "context": ctx.name,
        }

    def _ingest_connector(job_id, context_id, scoped, connector_type, config,
                           credentials, phase, llm_model, embedding_model,
                           schema_yaml, integration_id):
        """Background worker for connector ingestion."""
        import time as _time
        start = _time.time()

        try:
            from ..ingestion.connectors import get_ingestor
            from ..core.graph_structures import GraphNode as _GN, GraphEdge as _GE

            ingestor = get_ingestor(connector_type, config, credentials)
            if not ingestor:
                _finish_job(job_id, 0, error=f"No ingestor for: {connector_type}")
                return

            _ensure_graph(scoped)
            db = graph_registry.get_graph(scoped)
            if not db:
                _finish_job(job_id, 0, error=f"Could not access graph: {scoped}")
                return

            total_nodes = 0
            total_edges = 0
            connector_node_id = None

            # Phase 1: Metadata discovery
            if phase in ("both", "metadata"):
                _update_stage(job_id, "CONNECTOR_METADATA", "running", 0)
                try:
                    result = ingestor.discover_metadata(context_id)

                    for n in result.nodes:
                        db.add_node(_GN(id=n["id"], label=n["label"], properties=n.get("properties", {})))
                        total_nodes += 1
                        if n["label"] == "Connector":
                            connector_node_id = n["id"]

                    for e in result.edges:
                        db.add_edge(_GE(id=e["id"], source=e["source"], target=e["target"],
                                        label=e["label"], properties=e.get("properties", {})))
                        total_edges += 1

                    # Ensure context structure
                    if context_id and context_manager:
                        try:
                            _ensure_context_structure(db, context_id)
                        except Exception:
                            pass  # optional

                    graph_registry.save_graph(scoped, create_checkpoint=False)
                    _update_stage(job_id, "CONNECTOR_METADATA", "completed", total_nodes)
                    logger.info("[CONNECTOR] %s metadata: %d nodes, %d edges",
                                connector_type, total_nodes, total_edges)
                except Exception as e:
                    logger.error("[CONNECTOR] Metadata discovery failed: %s", e, exc_info=True)
                    _update_stage(job_id, "CONNECTOR_METADATA", "failed", 0)
                    _finish_job(job_id, 0, error=str(e))
                    return

            # Phase 2: Data extraction via pipeline
            if phase in ("both", "data"):
                try:
                    result = ingestor.pull_data(context_id)
                    data_items = result.data_items
                    source_node_links = []  # Track (source_node_id, data_item_title) for post-linking

                    if data_items:
                        structured_count = sum(1 for d in data_items if d.get("structured"))
                        text_count = len(data_items) - structured_count
                        logger.info("[CONNECTOR] %s data: %d structured, %d text items",
                                    connector_type, structured_count, text_count)

                        for i, item in enumerate(data_items):
                            if item.get("structured"):
                                # ── Structured path: direct record → typed node ──
                                records = item.get("records", [])
                                label = item.get("node_label", "Record")
                                key_field = item.get("key_field")
                                source_nid = item.get("_source_node_id")
                                fk_map = item.get("foreign_keys", {})  # {col: {table, column, node_id_prefix}}

                                from ..ingestion.connectors.base import ConnectorIngestor as _CI
                                sub = _CI.__new__(_CI)
                                sub.connector_type = connector_type
                                rec_result = sub._records_to_nodes(
                                    records, label, key_field=key_field,
                                    source_node_id=source_nid)

                                for n in rec_result.nodes:
                                    db.add_node(_GN(id=n["id"], label=n["label"],
                                                    properties=n.get("properties", {})))
                                    total_nodes += 1
                                for e in rec_result.edges:
                                    db.add_edge(_GE(id=e["id"], source=e["source"],
                                                    target=e["target"], label=e["label"],
                                                    properties=e.get("properties", {})))
                                    total_edges += 1

                                # FK edges: link records to referenced records
                                if fk_map:
                                    import hashlib as _hl
                                    for record_node in rec_result.nodes:
                                        props = record_node.get("properties", {})
                                        for fk_col, fk_info in fk_map.items():
                                            fk_val = props.get(fk_col)
                                            if fk_val is not None:
                                                ref_label = fk_info.get("ref_label", "Record")
                                                ref_id = f"{ref_label.lower()}_{_hl.sha256(str(fk_val).encode()).hexdigest()[:12]}"
                                                if db.get_node(ref_id):
                                                    try:
                                                        db.add_edge(_GE(
                                                            id=str(_uuid.uuid4()),
                                                            source=record_node["id"],
                                                            target=ref_id,
                                                            label="REFERENCES",
                                                            properties={"foreign_key": fk_col},
                                                        ))
                                                        total_edges += 1
                                                    except Exception:
                                                        pass  # optional

                                logger.info("[CONNECTOR] Created %d %s nodes from structured data",
                                            len(rec_result.nodes), label)
                            else:
                                # ── Unstructured path: text → LLM extraction pipeline ──
                                text = item.get("text", "")
                                if not text.strip():
                                    continue
                                title = item.get("title", f"{connector_type} item {i+1}")
                                source_nid = item.get("_source_node_id")
                                if source_nid:
                                    source_node_links.append(source_nid)

                                _ingest_pipeline(
                                    job_id, scoped, text,
                                    {"title": title, "source": connector_type,
                                     "connector_type": connector_type,
                                     "context_id": context_id},
                                    llm_model, embedding_model, schema_yaml, context_id,
                                )

                        # Save after all structured nodes are created
                        graph_registry.save_graph(scoped, create_checkpoint=False)
                except Exception as e:
                    logger.error("[CONNECTOR] Data pull failed: %s", e, exc_info=True)

            # Record SyncRun node in graph
            duration_ms = int((_time.time() - start) * 1000)
            if connector_node_id and db:
                try:
                    sync_node, sync_edge = ingestor._make_sync_run_node(
                        connector_node_id, "success", total_nodes, total_edges, duration_ms)
                    db.add_node(_GN(id=sync_node["id"], label=sync_node["label"],
                                    properties=sync_node["properties"]))
                    db.add_edge(_GE(id=sync_edge["id"], source=sync_edge["source"],
                                    target=sync_edge["target"], label=sync_edge["label"],
                                    properties=sync_edge["properties"]))
                    graph_registry.save_graph(scoped, create_checkpoint=False)
                except Exception:
                    pass  # optional

            # Refresh context stats
            if context_id and context_manager:
                try:
                    context_manager.refresh_stats(context_id)
                except Exception:
                    pass  # optional

            _finish_job(job_id, total_nodes, edges_created=total_edges)
            logger.info("[CONNECTOR] %s completed: %d nodes, %d edges in %dms",
                        connector_type, total_nodes, total_edges, duration_ms)
        except Exception as e:
            logger.error("[CONNECTOR] Failed: %s", e, exc_info=True)
            _finish_job(job_id, 0, error=str(e))

    @router.get("/contexts/{context_id}/ingest/jobs")
    async def list_context_ingest_jobs(context_id: str, user=Depends(user_auth)):
        """List ingestion jobs for a specific context."""
        if not context_manager:
            raise HTTPException(status_code=501, detail="Context manager not available")
        ctx = context_manager.get_context(context_id)
        if not ctx:
            raise HTTPException(status_code=404, detail="Context not found")
        # Filter jobs by context's graph namespace
        ctx_jobs = [
            j for j in _ingest_jobs.values()
            if j.get("graph") == ctx.name or j.get("graph") == ctx.graph_namespace
        ]
        ctx_jobs.sort(key=lambda j: j.get("created_at", ""), reverse=True)
        return {"jobs": ctx_jobs[:20]}

    # ------------------------------------------------------------------
    # Playground — Agent DevTools endpoints
    # ------------------------------------------------------------------

    @router.get("/playground/tools")
    async def list_playground_tools(user=Depends(user_auth)):
        """List all available MCP tools with schemas for the playground."""
        try:
            from ..tools.registry import ToolRegistry
            # Ensure tools are registered
            try:
                from ..tools.graph import register_graph_tools
                from ..tools.context import register_context_tools
                from ..tools.tasks import register_task_tools
                register_graph_tools()
                register_context_tools()
                register_task_tools()
            except Exception:
                pass  # optional

            tools = []
            for t in ToolRegistry.all():
                tools.append(t.to_json_schema())
            # Group by category
            categories = {}
            for t in tools:
                cat = t.get("category", "other")
                categories.setdefault(cat, []).append(t)
            return {"tools": tools, "categories": categories, "count": len(tools)}
        except Exception as e:
            return {"tools": [], "categories": {}, "count": 0, "error": str(e)}

    @router.post("/playground/tool")
    async def run_playground_tool(req: dict = Body(...), user=Depends(user_auth)):
        """Execute a single MCP tool from the playground.

        Body: {tool_name, params: {}, graph: "optional_graph_name"}
        """
        import time as _t
        tool_name = req.get("tool_name", "")
        params = req.get("params", {})
        graph_name = req.get("graph", "")

        if not tool_name:
            raise HTTPException(status_code=400, detail="tool_name is required")

        start = _t.time()
        try:
            from ..tools.registry import ToolRegistry, ToolContext
            # Ensure tools are registered
            try:
                from ..tools.graph import register_graph_tools
                from ..tools.context import register_context_tools
                from ..tools.tasks import register_task_tools
                register_graph_tools()
                register_context_tools()
                register_task_tools()
            except Exception:
                pass  # optional

            tool_def = ToolRegistry.get(tool_name)
            if not tool_def:
                available = ToolRegistry.names()
                return {"success": False, "error": f"Unknown tool: {tool_name}", "available": available[:20]}

            # Build a ToolContext
            tenant = _get_tenant(user)
            ns = graph_name
            if ns:
                ns = _scope_graph(tenant, ns) if ":" not in ns else ns

            from .playground_conn import PlaygroundConn, create_tool_context
            conn = PlaygroundConn(ns, graph_registry)
            ctx = create_tool_context(conn, f"playground_{user.user_id}", "Playground")

            # Dispatch the tool
            result_str = ToolRegistry.dispatch(tool_name, ctx, params)
            duration_ms = int((_t.time() - start) * 1000)

            # Try to parse as JSON
            try:
                import json as _json
                result_data = _json.loads(result_str)
            except Exception:
                result_data = result_str

            return {
                "success": True,
                "tool": tool_name,
                "result": result_data,
                "duration_ms": duration_ms,
            }
        except Exception as e:
            duration_ms = int((_t.time() - start) * 1000)
            logger.error("[PLAYGROUND] Tool %s failed: %s", tool_name, e, exc_info=True)
            return {"success": False, "tool": tool_name, "error": str(e), "duration_ms": duration_ms}

    @router.post("/playground/simulate")
    async def run_playground_simulation(req: dict = Body(...), user=Depends(user_auth)):
        """Run a scripted agent simulation — sequence of tool calls.

        Body: {
            agent: {name, role},
            steps: [{tool: "search_nodes", params: {query: "..."}}],
            graph: "graph_name",
            framework: "langgraph",  // optional: adapter framework
            session_id: "...",       // optional: existing session to join
            api_key: "..."           // optional: agent API key
        }
        """
        import time as _t
        agent_config = req.get("agent", {})
        steps = req.get("steps", [])
        graph_name = req.get("graph", "")
        fw = req.get("framework", "")
        session_id = req.get("session_id", "")
        agent_api_key = req.get("api_key", "")

        if not steps:
            raise HTTPException(status_code=400, detail="steps is required")

        try:
            from ..tools.registry import ToolRegistry, ToolContext
            try:
                from ..tools.graph import register_graph_tools
                from ..tools.context import register_context_tools
                from ..tools.tasks import register_task_tools
                register_graph_tools()
                register_context_tools()
                register_task_tools()
            except Exception:
                pass  # optional

            tenant = _get_tenant(user)
            ns = _scope_graph(tenant, graph_name) if graph_name and ":" not in graph_name else graph_name

            # If session_id provided, resolve context + runtime graphs
            runtime_ns = ""
            if session_id and session_manager:
                try:
                    sess = session_manager.get_session(session_id)
                    if sess:
                        ns = sess.get("graph_namespace", ns)
                        runtime_ns = sess.get("runtime_namespace", "")
                        logger.info("[PLAYGROUND] Session %s: context=%s runtime=%s", session_id, ns, runtime_ns)
                except Exception:
                    pass

            from .playground_conn import PlaygroundConn, create_tool_context
            conn = PlaygroundConn(ns, graph_registry)
            runtime_conn = PlaygroundConn(runtime_ns, graph_registry) if runtime_ns else None
            ctx = create_tool_context(conn, f"playground_{user.user_id}", agent_config.get("name", "Playground Agent"))
            if runtime_conn:
                ctx.runtime_conn = runtime_conn

            # Attach framework and session metadata to context
            if fw:
                ctx.metadata = getattr(ctx, "metadata", {})
                ctx.metadata["framework"] = fw
            if session_id:
                ctx.metadata = getattr(ctx, "metadata", {})
                ctx.metadata["session_id"] = session_id
            if agent_api_key:
                ctx.metadata = getattr(ctx, "metadata", {})
                ctx.metadata["api_key"] = agent_api_key

            results = []
            total_start = _t.time()
            for i, step in enumerate(steps):
                tool_name = step.get("tool", "")
                params = step.get("params", {})
                step_start = _t.time()

                try:
                    result_str = ToolRegistry.dispatch(tool_name, ctx, params)
                    try:
                        import json as _json
                        result_data = _json.loads(result_str)
                    except Exception:
                        result_data = result_str

                    results.append({
                        "step": i + 1,
                        "tool": tool_name,
                        "params": params,
                        "success": True,
                        "result": result_data,
                        "duration_ms": int((_t.time() - step_start) * 1000),
                    })
                except Exception as e:
                    results.append({
                        "step": i + 1,
                        "tool": tool_name,
                        "params": params,
                        "success": False,
                        "error": str(e),
                        "duration_ms": int((_t.time() - step_start) * 1000),
                    })

            return {
                "agent": agent_config,
                "steps_completed": len(results),
                "results": results,
                "total_duration_ms": int((_t.time() - total_start) * 1000),
                "framework": fw or None,
                "session_id": session_id or None,
            }
        except Exception as e:
            return {"success": False, "error": str(e)}

    @router.post("/playground/simulate/llm")
    async def run_playground_llm_agent(req: dict = Body(...), user=Depends(user_auth)):
        """Run an LLM-powered agent step.

        Body: {
            agent: {name, system_prompt, task},
            graph: "graph_name",
            llm_model: "groq:gpt-oss-120b",
            max_steps: 5,
            history: [],             // previous steps for multi-turn
            framework: "langgraph",  // optional: adapter framework
            session_id: "...",       // optional: existing session
            api_key: "..."           // optional: agent API key
        }
        """
        import time as _t
        agent_config = req.get("agent", {})
        graph_name = req.get("graph", "")
        llm_model = req.get("llm_model", "")
        max_steps = min(req.get("max_steps", 5), 20)
        history = req.get("history", [])
        fw = req.get("framework", "")
        session_id = req.get("session_id", "")
        agent_api_key = req.get("api_key", "")

        if not agent_config.get("task"):
            raise HTTPException(status_code=400, detail="agent.task is required")

        try:
            from ..tools.registry import ToolRegistry, ToolContext
            try:
                from ..tools.graph import register_graph_tools
                from ..tools.context import register_context_tools
                from ..tools.tasks import register_task_tools
                register_graph_tools()
                register_context_tools()
                register_task_tools()
            except Exception:
                pass  # optional

            # Get LLM client
            from ..llm import get_llm_client
            provider, _, model = (llm_model or "groq:gpt-oss-120b").partition(":")
            llm = get_llm_client(provider=provider, model=model) if model else get_llm_client()

            tenant = _get_tenant(user)
            ns = _scope_graph(tenant, graph_name) if graph_name and ":" not in graph_name else graph_name

            # Resolve session graphs if provided
            runtime_ns = ""
            if session_id and session_manager:
                try:
                    sess = session_manager.get_session(session_id)
                    if sess:
                        ns = sess.get("graph_namespace", ns)
                        runtime_ns = sess.get("runtime_namespace", "")
                        logger.info("[PLAYGROUND-LLM] Session %s: context=%s runtime=%s", session_id, ns, runtime_ns)
                except Exception:
                    pass

            from .playground_conn import PlaygroundConn, create_tool_context
            conn = PlaygroundConn(ns, graph_registry)
            runtime_conn = PlaygroundConn(runtime_ns, graph_registry) if runtime_ns else None
            ctx = create_tool_context(conn, f"playground_{user.user_id}", agent_config.get("name", "LLM Agent"))
            if runtime_conn:
                ctx.runtime_conn = runtime_conn

            # Attach framework and session metadata
            if fw or session_id or agent_api_key:
                ctx.metadata = getattr(ctx, "metadata", {})
                if fw:
                    ctx.metadata["framework"] = fw
                if session_id:
                    ctx.metadata["session_id"] = session_id
                if agent_api_key:
                    ctx.metadata["api_key"] = agent_api_key

            # Build available tools list for the prompt
            tool_schemas = [t.to_json_schema() for t in ToolRegistry.all() if t.handler]
            tool_names = [t["name"] for t in tool_schemas]
            import json as _json_tools
            tools_desc = _json_tools.dumps([
                {"name": t["name"], "description": t.get("description", ""), "parameters": t.get("parameters", {})}
                for t in tool_schemas[:30]
            ], indent=2)

            system_prompt = agent_config.get("system_prompt", "You are a helpful AI agent.")
            task = agent_config["task"]

            steps = []
            total_start = _t.time()

            for step_num in range(max_steps):
                # Build conversation for LLM
                history_text = ""
                for h in history + steps:
                    if h.get("type") == "tool_call":
                        history_text += f"\nTool call: {h['tool']}({h.get('params', {})})\nResult: {str(h.get('result', ''))[:500]}\n"
                    elif h.get("type") == "reasoning":
                        history_text += f"\nReasoning: {h['text']}\n"

                prompt = f"""{system_prompt}

You have access to these tools:
{tools_desc}

Task: {task}

{f'Previous steps:{history_text}' if history_text else ''}

Decide your next action. Respond in this exact JSON format:
{{"reasoning": "your thought process", "action": "tool_name", "params": {{"key": "value"}}, "done": false}}

If the task is complete, set "done": true and include a "summary" field.
Respond with ONLY valid JSON."""

                step_start = _t.time()
                try:
                    import json as _json
                    raw = llm.generate(prompt=prompt, max_tokens=1000)
                    if not raw or not raw.strip():
                        steps.append({"type": "error", "text": "LLM returned empty response", "step": step_num + 1})
                        break

                    # Parse LLM response
                    text = raw.strip()
                    # Extract JSON from markdown code blocks if present
                    import re as _re
                    json_match = _re.search(r'```(?:json)?\s*([\s\S]*?)```', text)
                    if json_match:
                        text = json_match.group(1).strip()
                    if not text.startswith("{"):
                        brace_match = _re.search(r'\{[\s\S]*\}', text)
                        if brace_match:
                            text = brace_match.group(0)

                    decision = _json.loads(text)
                    reasoning = decision.get("reasoning", "")
                    action = decision.get("action", "")
                    params = decision.get("params", {})
                    done = decision.get("done", False)

                    steps.append({
                        "type": "reasoning",
                        "text": reasoning,
                        "step": step_num + 1,
                        "duration_ms": int((_t.time() - step_start) * 1000),
                    })

                    if done:
                        steps.append({
                            "type": "done",
                            "summary": decision.get("summary", "Task completed."),
                            "step": step_num + 1,
                        })
                        break

                    if action and action in tool_names:
                        tool_start = _t.time()
                        try:
                            result_str = ToolRegistry.dispatch(action, ctx, params)
                            try:
                                result_data = _json.loads(result_str)
                            except Exception:
                                result_data = result_str
                            steps.append({
                                "type": "tool_call",
                                "tool": action,
                                "params": params,
                                "result": result_data,
                                "success": True,
                                "step": step_num + 1,
                                "duration_ms": int((_t.time() - tool_start) * 1000),
                            })
                        except Exception as e:
                            steps.append({
                                "type": "tool_call",
                                "tool": action,
                                "params": params,
                                "error": str(e),
                                "success": False,
                                "step": step_num + 1,
                            })
                    elif action:
                        steps.append({"type": "error", "text": f"Unknown tool: {action}", "step": step_num + 1})

                except _json.JSONDecodeError as e:
                    steps.append({"type": "error", "text": f"LLM returned invalid JSON: {str(e)[:100]}", "raw": raw[:300] if raw else "", "step": step_num + 1})
                    break
                except Exception as e:
                    steps.append({"type": "error", "text": str(e), "step": step_num + 1})
                    break

            return {
                "agent": agent_config,
                "steps": steps,
                "total_duration_ms": int((_t.time() - total_start) * 1000),
            }
        except Exception as e:
            logger.error("[PLAYGROUND] LLM agent failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    @router.post("/playground/simulate/plan")
    async def run_playground_planner(req: dict = Body(...), user=Depends(user_auth)):
        """Plan-then-execute: LLM decomposes a goal into multi-agent tasks.

        The user describes the goal AND the agents in natural language.
        The LLM decides which agents to create, what tasks each performs,
        and the execution order (dependencies).

        Body: {
            goal: "I need a researcher to explore the graph and a reviewer to check for conflicts...",
            graph: "graph_name",
            llm_model: "groq:gpt-oss-120b",
            max_tasks: 10,
            auto_execute: true
        }
        """
        import time as _t
        goal = req.get("goal", "")
        graph_name = req.get("graph", "")
        llm_model = req.get("llm_model", "")
        max_tasks = min(req.get("max_tasks", 10), 20)
        auto_execute = req.get("auto_execute", True)

        if not goal.strip():
            raise HTTPException(status_code=400, detail="goal is required")

        try:
            from ..tools.registry import ToolRegistry, ToolContext
            try:
                from ..tools.graph import register_graph_tools
                from ..tools.context import register_context_tools
                from ..tools.tasks import register_task_tools
                register_graph_tools()
                register_context_tools()
                register_task_tools()
            except Exception:
                pass  # optional

            from ..llm import get_llm_client
            provider, _, model = (llm_model or "groq:gpt-oss-120b").partition(":")
            llm = get_llm_client(provider=provider, model=model) if model else get_llm_client()

            tenant = _get_tenant(user)
            ns = _scope_graph(tenant, graph_name) if graph_name and ":" not in graph_name else graph_name

            # Discover tools from MCP registry — same schemas agents see
            import json as _json
            tool_schemas = [t.to_json_schema() for t in ToolRegistry.all() if t.handler]
            # Compact JSON schema for each tool (what MCP clients receive)
            tools_json = _json.dumps([
                {
                    "name": t["name"],
                    "description": t.get("description", ""),
                    "parameters": t.get("parameters", {}),
                }
                for t in tool_schemas
            ], indent=2)

            # ── Phase 1: Plan ──
            plan_prompt = f"""You are an orchestration planner for a multi-agent system.

The user will describe a goal, possibly mentioning specific agents or roles they want.
Your job is to:
1. Decide which agents are needed (infer from the prompt)
2. Create a step-by-step plan where each step is assigned to a specific agent
3. Define dependencies between steps

## Available MCP Tools (JSON Schema)

These are the exact tool definitions from the MCP server. Use ONLY these tool names
and ONLY the parameter names defined in each tool's "parameters.properties" object.

{tools_json}

## User's Request

{goal}

## Output Format

Respond with ONLY this JSON:
{{
  "agents": [
    {{"name": "Agent Name", "role": "role_description"}}
  ],
  "plan": [
    {{"step": 1, "agent": "Agent Name", "description": "what this step does", "tool": "tool_name", "params": {{"param_name": "value"}}, "depends_on": []}}
  ]
}}

## Rules
- Infer agents from the user's prompt
- Each step's "tool" MUST be an exact tool name from the schema above
- Each step's "params" MUST use exact parameter names from that tool's parameters.properties
- depends_on lists step numbers that must finish first (empty = immediate)
- Maximum {max_tasks} steps
- Order logically: gather → analyze → summarize

Respond with ONLY valid JSON."""

            plan_start = _t.time()
            raw_plan = llm.generate(prompt=plan_prompt, max_tokens=2000)
            plan_duration = int((_t.time() - plan_start) * 1000)

            # Parse the plan
            import json as _json
            text = raw_plan.strip()
            if "```" in text:
                text = text.split("```")[1]
                if text.startswith("json"):
                    text = text[4:]
                text = text.strip()
            parsed = _json.loads(text)

            # Handle both formats: {agents, plan} or just [tasks]
            if isinstance(parsed, dict):
                agents_list = parsed.get("agents", [])
                tasks = parsed.get("plan", [])
            elif isinstance(parsed, list):
                tasks = parsed
                # Infer agents from tasks
                agent_names = list(dict.fromkeys(t.get("agent", "Agent") for t in tasks))
                agents_list = [{"name": n, "role": "assistant"} for n in agent_names]
            else:
                tasks = [parsed]
                agents_list = [{"name": "Agent", "role": "assistant"}]

            tasks = tasks[:max_tasks]

            result = {
                "goal": goal,
                "agents": agents_list,
                "plan": tasks,
                "plan_duration_ms": plan_duration,
                "task_count": len(tasks),
                "agent_count": len(agents_list),
            }

            if not auto_execute:
                return result

            # ── Phase 2: Execute ──
            from .playground_conn import PlaygroundConn, create_tool_context
            conn = PlaygroundConn(ns, graph_registry)

            # Create a ToolContext per agent so each has its own identity
            agent_contexts = {}
            for ag in agents_list:
                ag_name = ag.get("name", "Agent")
                agent_contexts[ag_name] = create_tool_context(
                    conn, f"sim_{ag_name.lower().replace(' ', '_')}_{user.user_id}", ag_name,
                )

            # Fallback context for tasks with unknown agent
            default_ctx = create_tool_context(conn, f"planner_{user.user_id}", "Planner")

            completed = set()
            execution_results = []
            exec_start = _t.time()

            for task in tasks:
                step_num = task.get("step", 0)
                deps = task.get("depends_on", [])
                tool_name = task.get("tool", "")
                params = task.get("params", {})
                desc = task.get("description", "")
                task_agent = task.get("agent", "")

                # Check dependencies
                unmet = [d for d in deps if d not in completed]
                if unmet:
                    execution_results.append({
                        "step": step_num,
                        "agent": task_agent,
                        "description": desc,
                        "tool": tool_name,
                        "skipped": True,
                        "reason": f"Dependencies not met: {unmet}",
                    })
                    continue

                # Pick the right agent context
                ctx = agent_contexts.get(task_agent, default_ctx)

                step_start = _t.time()
                try:
                    result_str = ToolRegistry.dispatch(tool_name, ctx, params)
                    try:
                        result_data = _json.loads(result_str)
                    except Exception:
                        result_data = result_str

                    execution_results.append({
                        "step": step_num,
                        "agent": task_agent,
                        "description": desc,
                        "tool": tool_name,
                        "params": params,
                        "success": True,
                        "result": result_data,
                        "duration_ms": int((_t.time() - step_start) * 1000),
                    })
                    completed.add(step_num)
                except Exception as e:
                    execution_results.append({
                        "step": step_num,
                        "agent": task_agent,
                        "description": desc,
                        "tool": tool_name,
                        "params": params,
                        "success": False,
                        "error": str(e),
                        "duration_ms": int((_t.time() - step_start) * 1000),
                    })
                    completed.add(step_num)  # still mark as done so dependents can try

            result["execution"] = execution_results
            result["execution_duration_ms"] = int((_t.time() - exec_start) * 1000)
            result["total_duration_ms"] = int((_t.time() - plan_start) * 1000)
            result["steps_completed"] = len(completed)
            return result

        except Exception as e:
            logger.error("[PLAYGROUND] Planner failed: %s", e, exc_info=True)
            return {"success": False, "error": str(e)}

    # ------------------------------------------------------------------
    # Session ↔ Context attachment endpoints
    # ------------------------------------------------------------------

    @router.post("/sessions/{session_id}/contexts")
    async def attach_context_to_session(
        session_id: str, req: AttachContextRequest, user=Depends(require_member),
    ):
        if not session_manager:
            raise HTTPException(status_code=501, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        ok = session_manager.attach_context(session_id, req.context_id, req.role)
        if not ok:
            raise HTTPException(status_code=500, detail="Failed to attach context")

        synced_count = 0

        # Create ContextRef in boundary graph (reference only, no data copying)
        # Agents search attached contexts via ContextRef.graph_namespace
        ctx_name = ""
        ctx_ns = ""
        item_count = 0
        try:
            ctx_obj = context_manager.get_context(req.context_id) if context_manager else None
            if ctx_obj:
                ctx_name = ctx_obj.name
                ctx_ns = ctx_obj.graph_namespace
                item_count = ctx_obj.item_count
        except Exception:
            pass

        # Emit event
        try:
            from .events import event_bus
            event_bus.emit("context_attached", {
                "session_id": session_id,
                "context_id": req.context_id,
                "synced_nodes": synced_count,
            })
        except Exception:
            pass  # optional

        return {
            "status": "attached",
            "session_id": session_id,
            "context_id": req.context_id,
            "role": req.role,
            "synced_nodes": synced_count,
        }

    @router.post("/sessions/{session_id}/repair-graph")
    async def repair_session_graph(session_id: str, user=Depends(require_member)):
        """Repair session graph — ensure boundary root, context refs, and edges exist."""
        if not session_manager:
            raise HTTPException(status_code=501, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")

        from ..core.graph_structures import GraphNode, GraphEdge
        import uuid as _repair_uuid

        graph = graph_registry.get_graph(session.graph_namespace)
        if not graph:
            graph = graph_registry.create_graph(session.graph_namespace)

        repaired = []

        # Ensure boundary root
        if not graph.get_node(session_id):
            graph.add_node(GraphNode(
                id=session_id, label="Boundary",
                properties={"name": session.name, "created_at": session.created_at},
            ), write_through=True)
            repaired.append("Created Boundary root node")

        # Ensure all attached contexts have graph nodes
        attachments = session_manager.get_session_context_ids(session_id) or []
        for att in attachments:
            ctx_id = att.get("context_id", "") if isinstance(att, dict) else str(att)
            if not ctx_id:
                continue
            if not graph.get_node(ctx_id):
                ctx_name = ctx_id[:12]
                ctx_type = ""
                ctx_ns = ""
                ctx_desc = ""
                ctx_items = 0
                ctx_tokens = 0
                ctx_tags: list = []
                ctx_sensitivity = ""
                if context_manager:
                    ctx_obj = context_manager.get_context(ctx_id)
                    if ctx_obj:
                        ctx_name = ctx_obj.name
                        ctx_type = ctx_obj.context_type or ""
                        ctx_ns = ctx_obj.graph_namespace or ""
                        ctx_desc = ctx_obj.description or ""
                        ctx_items = ctx_obj.item_count or 0
                        ctx_tokens = ctx_obj.estimated_tokens or 0
                        ctx_tags = ctx_obj.tags or []
                        ctx_sensitivity = ctx_obj.sensitivity or ""
                graph.add_node(GraphNode(
                    id=ctx_id, label="ContextRef",
                    properties={
                        "name": ctx_name,
                        "context_id": ctx_id,
                        "graph_namespace": ctx_ns,
                        "context_type": ctx_type,
                        "description": ctx_desc,
                        "item_count": ctx_items,
                        "estimated_tokens": ctx_tokens,
                        "tags": ctx_tags,
                        "sensitivity": ctx_sensitivity,
                    },
                ), write_through=True)
                graph.add_edge(GraphEdge(
                    id=str(_repair_uuid.uuid4()),
                    source=session_id, target=ctx_id,
                    label="HAS_CONTEXT", properties={},
                ))
                repaired.append(f"Linked context: {ctx_name}")

        # Save
        try:
            graph_registry.save_graph(session.graph_namespace, create_checkpoint=False)
        except Exception:
            pass  # optional

        return {"status": "repaired", "actions": repaired, "total": len(repaired)}

    @router.delete("/sessions/{session_id}/contexts/{ctx_id:path}")
    async def detach_context_from_session(
        session_id: str, ctx_id: str, user=Depends(require_member),
    ):
        if not session_manager:
            raise HTTPException(status_code=501, detail="Session manager not available")
        ok = session_manager.detach_context(session_id, ctx_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Attachment not found")
        return {"status": "detached"}

    @router.get("/sessions/{session_id}/contexts")
    async def list_session_contexts(session_id: str, user=Depends(user_auth)):
        if not session_manager:
            raise HTTPException(status_code=501, detail="Session manager not available")
        attachments = session_manager.get_session_context_ids(session_id)
        # Enrich with context details
        enriched = []
        for att in attachments:
            entry = dict(att)
            if context_manager:
                ctx = context_manager.get_context(att["context_id"])
                if ctx:
                    entry["context"] = ctx.to_dict()
            enriched.append(entry)
        return {"contexts": enriched, "total": len(enriched)}

    # ==================================================================
    # Session Join Requests (approval gate)
    # ==================================================================

    @router.post("/sessions/{session_id}/join-requests")
    async def request_join_session(session_id: str, body: JoinRequestBody, user=Depends(user_auth)):
        """Agent requests to join a session (goes to pending queue)."""
        if not session_manager:
            raise HTTPException(status_code=501, detail="Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(status_code=404, detail="Session not found")
        # Use user_id as agent_id for dashboard-initiated requests
        agent_id = getattr(user, "user_id", None) or "unknown"
        result = session_manager.request_join(session_id, agent_id, body.level, body.reason)
        return result

    @router.get("/sessions/{session_id}/join-requests")
    async def list_join_requests(session_id: str, user=Depends(user_auth)):
        """List pending join requests for a session (owner/admin view)."""
        if not session_manager:
            raise HTTPException(status_code=501, detail="Session manager not available")
        requests = session_manager.get_pending_requests(session_id)
        # Enrich with agent info
        enriched = []
        for req in requests:
            entry = dict(req)
            if agent_registry:
                agent = agent_registry.get(req["agent_id"])
                if agent:
                    entry["agent"] = agent.to_dict()
            enriched.append(entry)
        return {"requests": enriched, "total": len(enriched)}

    @router.post("/sessions/{session_id}/join-requests/{request_id}/approve")
    async def approve_join_request(
        session_id: str, request_id: str, body: ReviewRequestBody, user=Depends(user_auth)
    ):
        """Approve a pending join request — grants access."""
        if not session_manager:
            raise HTTPException(status_code=501, detail="Session manager not available")
        reviewer_id = getattr(user, "user_id", None) or "dashboard"
        ok = session_manager.approve_join(request_id, reviewer_id, body.level, body.allowed_tags)
        if not ok:
            raise HTTPException(status_code=404, detail="Request not found or already reviewed")
        return {"status": "approved", "request_id": request_id}

    @router.post("/sessions/{session_id}/join-requests/{request_id}/deny")
    async def deny_join_request(session_id: str, request_id: str, user=Depends(user_auth)):
        """Deny a pending join request."""
        if not session_manager:
            raise HTTPException(status_code=501, detail="Session manager not available")
        reviewer_id = getattr(user, "user_id", None) or "dashboard"
        ok = session_manager.deny_join(request_id, reviewer_id)
        if not ok:
            raise HTTPException(status_code=404, detail="Request not found or already reviewed")
        return {"status": "denied", "request_id": request_id}

    # ==================================================================
    # Session Thread (SDLC conversation — human + agent messages)
    # ==================================================================

    def _get_thread_id(session_id: str) -> str:
        """Get the thread conversation_id for a session."""
        from ..context.conversation import ConversationStore
        if not session_manager:
            raise HTTPException(501, "Session manager not available")
        session = session_manager.get_session(session_id)
        if not session:
            raise HTTPException(404, "Session not found")
        thread_id = session.config.get("thread_id")
        if not thread_id:
            # Auto-create thread if missing
            store = ConversationStore()
            thread = store.create(session_id=session_id, title="Thread",
                                  metadata={"type": "sdlc_thread"})
            config = dict(session.config)
            config["thread_id"] = thread.conversation_id
            session_manager.update_session(session_id, config=config)
            thread_id = thread.conversation_id
        return thread_id

    @router.get("/sessions/{session_id}/thread")
    async def get_thread(session_id: str, limit: int = Query(50), user=Depends(user_auth)):
        """Get session thread messages."""
        from ..context.conversation import ConversationStore
        thread_id = _get_thread_id(session_id)
        store = ConversationStore()
        conv = store.get(thread_id, include_messages=True)
        if not conv:
            return {"messages": [], "thread_id": thread_id}
        messages = [m.to_dict() for m in (conv.messages or [])[-limit:]]
        session = session_manager.get_session(session_id)
        return {
            "thread_id": thread_id,
            "goal": session.config.get("goal", "") if session else "",
            "messages": messages,
            "count": len(messages),
        }

    @router.post("/sessions/{session_id}/thread")
    async def post_to_thread(session_id: str, req: ThreadMessageRequest, user=Depends(user_auth)):
        """Post a human message to the session thread."""
        from ..context.conversation import ConversationStore
        thread_id = _get_thread_id(session_id)
        store = ConversationStore()
        metadata = dict(req.metadata)
        if "type" not in metadata:
            metadata["type"] = "instruction"
        msg = store.append_message(
            conversation_id=thread_id,
            role=req.role,
            content=req.content,
            agent_id=getattr(user, "user_id", "human"),
            metadata=metadata,
        )
        event_bus.emit("thread_message", {
            "session_id": session_id, "thread_id": thread_id,
            "role": req.role, "content": req.content[:100],
        })
        return {"message_id": msg.message_id, "thread_id": thread_id}

    @router.post("/sessions/{session_id}/thread/approve")
    async def approve_in_thread(session_id: str, req: ThreadApprovalRequest, user=Depends(user_auth)):
        """Post an approval to the session thread."""
        from ..context.conversation import ConversationStore
        thread_id = _get_thread_id(session_id)
        store = ConversationStore()
        content = f"Approved: {req.target_id[:12]}"
        if req.feedback:
            content += f" — {req.feedback}"
        msg = store.append_message(
            conversation_id=thread_id,
            role="human",
            content=content,
            agent_id=getattr(user, "user_id", "human"),
            metadata={"type": "approval", "target_id": req.target_id, "action": "approved"},
        )
        event_bus.emit("thread_approval", {
            "session_id": session_id, "target_id": req.target_id, "action": "approved",
        })
        return {"message_id": msg.message_id, "action": "approved", "target_id": req.target_id}

    @router.post("/sessions/{session_id}/thread/reject")
    async def reject_in_thread(session_id: str, req: ThreadApprovalRequest, user=Depends(user_auth)):
        """Post a rejection to the session thread."""
        from ..context.conversation import ConversationStore
        thread_id = _get_thread_id(session_id)
        store = ConversationStore()
        content = f"Rejected: {req.target_id[:12]}"
        if req.feedback:
            content += f" — {req.feedback}"
        msg = store.append_message(
            conversation_id=thread_id,
            role="human",
            content=content,
            agent_id=getattr(user, "user_id", "human"),
            metadata={"type": "approval", "target_id": req.target_id, "action": "rejected",
                       "feedback": req.feedback},
        )
        event_bus.emit("thread_rejection", {
            "session_id": session_id, "target_id": req.target_id, "action": "rejected",
            "feedback": req.feedback,
        })
        return {"message_id": msg.message_id, "action": "rejected", "target_id": req.target_id}

    # ── Gateway / Cost Tracking Endpoints ────────────────────────────

    @router.get("/gateway/costs/{session_id}")
    async def gateway_boundary_costs(session_id: str, user=Depends(require_member)):
        """Get cost report for a boundary — all agents, total spend, savings tips."""
        try:
            from contextsynapse.gateway import get_gateway
            ns = _session_namespace(session_id)
            return get_gateway().get_boundary_report(ns)
        except Exception as e:
            return {"error": str(e)}

    @router.get("/gateway/costs/{session_id}/{agent_id}")
    async def gateway_agent_costs(session_id: str, agent_id: str, user=Depends(require_member)):
        """Get cost report for a specific agent in a boundary."""
        try:
            from contextsynapse.gateway import get_gateway
            ns = _session_namespace(session_id)
            return get_gateway().get_agent_report(ns, agent_id)
        except Exception as e:
            return {"error": str(e)}

    @router.get("/gateway/external-log/{session_id}")
    async def gateway_external_log(session_id: str, agent_id: str = "", limit: int = 50, user=Depends(require_member)):
        """Get log of external tool calls agents made outside the boundary."""
        try:
            from contextsynapse.gateway import get_gateway
            ns = _session_namespace(session_id)
            return {"items": get_gateway().get_external_log(ns, agent_id)}
        except Exception as e:
            return {"error": str(e)}

    @router.get("/gateway/policy/{session_id}")
    async def gateway_get_policy(session_id: str, user=Depends(require_member)):
        """Get tool access policy for a boundary."""
        try:
            from contextsynapse.gateway import get_gateway
            ns = _session_namespace(session_id)
            return get_gateway().get_policy(ns)
        except Exception as e:
            return {"error": str(e)}

    class PolicyUpdate(BaseModel):
        allow_tools: Optional[List[str]] = None
        deny_tools: Optional[List[str]] = None
        allow_categories: Optional[List[str]] = None
        deny_categories: Optional[List[str]] = None
        allow_external: bool = True
        max_tool_calls_per_minute: int = 60
        max_cost_per_session: float = 0.0
        default_allow: bool = True

    @router.put("/gateway/policy/{session_id}")
    async def gateway_set_policy(session_id: str, body: PolicyUpdate, user=Depends(require_member)):
        """Set tool access policy for a boundary."""
        try:
            from contextsynapse.gateway import get_gateway
            ns = _session_namespace(session_id)
            get_gateway().set_policy(
                ns,
                allow=body.allow_tools,
                deny=body.deny_tools,
                allow_categories=body.allow_categories,
                deny_categories=body.deny_categories,
                allow_external=body.allow_external,
                max_tool_calls_per_minute=body.max_tool_calls_per_minute,
                max_cost_per_session=body.max_cost_per_session,
                default_allow=body.default_allow,
            )
            event_bus.emit("policy_updated", {"session_id": session_id})
            return {"status": "ok", "namespace": ns}
        except Exception as e:
            return {"error": str(e)}

    class AgentPolicyUpdate(BaseModel):
        agent_id: str
        allow_tools: Optional[List[str]] = None
        deny_tools: Optional[List[str]] = None

    @router.put("/gateway/policy/{session_id}/agent")
    async def gateway_set_agent_policy(session_id: str, body: AgentPolicyUpdate, user=Depends(require_member)):
        """Set tool policy for a specific agent within a boundary."""
        try:
            from contextsynapse.gateway import get_policy
            ns = _session_namespace(session_id)
            get_policy().set_agent_policy(
                ns, body.agent_id,
                allow=body.allow_tools,
                deny=body.deny_tools,
            )
            event_bus.emit("agent_policy_updated", {"session_id": session_id, "agent_id": body.agent_id})
            return {"status": "ok", "agent_id": body.agent_id}
        except Exception as e:
            return {"error": str(e)}

    def _session_namespace(session_id: str) -> str:
        """Resolve session ID to graph namespace."""
        try:
            from ..context.session import SessionManager
            sm = SessionManager()
            session = sm.get_session(session_id)
            if session:
                return session.get("graph_namespace", session_id)
        except Exception:
            pass  # optional
        return session_id

    # ── Runtime Prompt Injection ─────────────────────────────────

    class InjectRequest(BaseModel):
        message: str
        target_agent: str = "all"
        type: str = "directive"  # directive, constraint, answer, priority, stop
        priority: str = "normal"  # high, normal, low

    @router.post("/sessions/{session_id}/inject")
    async def inject_prompt(session_id: str, body: InjectRequest, user=Depends(require_member)):
        """Inject a runtime prompt into a running agent pipeline."""
        try:
            from contextsynapse.context.injection import get_injection_queue
            entry = get_injection_queue().inject(
                session_id=session_id,
                target_agent=body.target_agent,
                message=body.message,
                injection_type=body.type,
                priority=body.priority,
                injected_by=getattr(user, "user_id", "human"),
            )
            event_bus.emit("prompt_injected", {
                "session_id": session_id,
                "target_agent": body.target_agent,
                "message": body.message[:100],
                "type": body.type,
                "priority": body.priority,
            })
            return {"injected": True, "target_agent": body.target_agent, "type": body.type}
        except Exception as e:
            return {"error": str(e)}

    @router.get("/sessions/{session_id}/injections")
    async def get_injections(session_id: str, user=Depends(require_member)):
        """Check if there are pending injections for a session."""
        try:
            from contextsynapse.context.injection import get_injection_queue
            q = get_injection_queue()
            has = q.has_pending(session_id, "all")
            return {"has_pending": has}
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Graph Archival
    # ------------------------------------------------------------------

    @router.post("/archive/{graph_name}")
    async def archive_graph_nodes(graph_name: str, user=Depends(require_admin)):
        """Archive old nodes from a graph based on TTL."""
        from ..core.archiver import GraphArchiver
        archiver = GraphArchiver(graph_registry)
        body = {}
        try:
            from fastapi import Request as _Req
            # Accept optional ttl_days and dry_run from query params
        except Exception:
            pass  # optional
        result = archiver.archive_old_nodes(graph_name, ttl_days=30)
        if "error" not in result:
            from .events import event_bus
            event_bus.emit("nodes_archived", {
                "graph": graph_name,
                "archived": result.get("archived", 0),
                "cutoff_date": result.get("cutoff_date", ""),
            })
        return result

    @router.get("/archives")
    async def list_archives(user=Depends(user_auth)):
        """List all archive files."""
        from ..core.archiver import GraphArchiver
        archiver = GraphArchiver(graph_registry)
        return {"archives": archiver.list_archives()}

    @router.post("/archives/{filename}/restore")
    async def restore_archive(filename: str, user=Depends(require_admin)):
        """Restore archived nodes back into their graph."""
        from ..core.archiver import GraphArchiver
        archiver = GraphArchiver(graph_registry)
        result = archiver.restore_archive(filename)
        if "error" not in result:
            from .events import event_bus
            event_bus.emit("archive_restored", {
                "filename": filename,
                "restored_nodes": result.get("restored_nodes", 0),
            })
        return result

    # ------------------------------------------------------------------
    # Webhooks Management
    # ------------------------------------------------------------------

    @router.get("/webhooks")
    async def list_webhooks(user=Depends(user_auth)):
        """List registered webhooks for the user's tenant."""
        try:
            from ..context.webhooks import WebhookRegistry
            wh = WebhookRegistry()
            tenant = _get_tenant(user)
            hooks = wh.list_webhooks(tenant.tenant_id)
            return {"webhooks": [h.to_dict() for h in hooks]}
        except Exception as e:
            return {"webhooks": [], "error": str(e)}

    @router.post("/webhooks")
    async def create_webhook(req: dict = Body(...), user=Depends(require_admin)):
        """Register a new webhook."""
        from ..context.webhooks import WebhookRegistry
        wh = WebhookRegistry()
        tenant = _get_tenant(user)
        url = req.get("url", "")
        event_types = req.get("event_types", [])
        if not url:
            raise HTTPException(400, "url is required")
        hook = wh.register(tenant.tenant_id, url, event_types or None)
        return {"webhook": hook.to_dict(), "secret": hook.secret}

    @router.delete("/webhooks/{webhook_id}")
    async def delete_webhook(webhook_id: str, user=Depends(require_admin)):
        """Delete a webhook."""
        from ..context.webhooks import WebhookRegistry
        wh = WebhookRegistry()
        deleted = wh.delete(webhook_id)
        if not deleted:
            raise HTTPException(404, "Webhook not found")
        return {"deleted": True}

    @router.post("/webhooks/test")
    async def test_webhook(req: dict = Body(...), user=Depends(require_admin)):
        """Send a test event to a webhook URL."""
        from ..context.webhooks import WebhookRegistry
        wh = WebhookRegistry()
        url = req.get("url", "")
        if not url:
            raise HTTPException(400, "url is required")
        hook = wh.get(req.get("webhook_id", ""))
        if not hook:
            # Create a temporary hook for testing
            test_event = {
                "event_type": "test",
                "message": "This is a test webhook from AIContextDB",
                "timestamp": __import__("time").time(),
            }
            try:
                import urllib.request, json as _json
                body = _json.dumps(test_event).encode()
                r = urllib.request.Request(url, data=body, headers={"Content-Type": "application/json"}, method="POST")
                with urllib.request.urlopen(r, timeout=10) as resp:
                    return {"success": True, "status": resp.status}
            except Exception as e:
                return {"success": False, "error": str(e)}
        # Dispatch test to existing hook
        wh.dispatch(hook.tenant_id, {"event_type": "test", "message": "Test webhook"})
        return {"success": True}

    # ------------------------------------------------------------------
    # Execution Graph Schema
    # ------------------------------------------------------------------

    @router.get("/schema")
    async def get_execution_schema(domain: str = "general", user=Depends(user_auth)):
        """Get the execution graph schema. Domain: general, software, research, support, legal."""
        from ..context.execution_schema import get_schema_summary
        return get_schema_summary(domain)

    # ------------------------------------------------------------------
    # Replication (Multi-Region)
    # ------------------------------------------------------------------

    @router.post("/replication/export/{graph_name}")
    async def export_snapshot(graph_name: str, user=Depends(require_admin)):
        """Export a graph snapshot for replication to another region."""
        from ..core.replication import GraphReplicator
        replicator = GraphReplicator(graph_registry)
        return replicator.export_snapshot(graph_name)

    @router.post("/replication/import")
    async def import_snapshot(req: dict = Body(...), user=Depends(require_admin)):
        """Import a graph snapshot from another region."""
        from ..core.replication import GraphReplicator
        replicator = GraphReplicator(graph_registry)
        path = req.get("snapshot_path", "")
        target = req.get("target_graph", "")
        read_only = req.get("read_only", True)
        if not path:
            raise HTTPException(400, "snapshot_path is required")
        return replicator.import_snapshot(path, target_graph=target or None, read_only=read_only)

    @router.get("/replication/snapshots")
    async def list_snapshots(user=Depends(user_auth)):
        """List available snapshots for replication."""
        from ..core.replication import GraphReplicator
        replicator = GraphReplicator(graph_registry)
        return {"snapshots": replicator.list_snapshots()}

    # ------------------------------------------------------------------
    # Savings / Benchmark Dashboard
    # ------------------------------------------------------------------

    @router.get("/savings")
    async def get_savings_report(
        namespace: str = None,
        period: str = None,
        user=Depends(user_auth),
    ):
        """Get token/cost savings report — proves AIContextDB value."""
        try:
            from ..benchmark.savings import calculate_savings
            from ..gateway import get_gateway
            tracker = get_gateway().cost_tracker
            return calculate_savings(tracker, graph_registry, namespace, period)
        except Exception as e:
            logger.debug("[SAVINGS] %s", e)
            return {
                "actual_tokens": 0, "baseline_tokens": 0, "tokens_saved": 0,
                "savings_pct": 0, "actual_cost_usd": 0, "baseline_cost_usd": 0,
                "cost_saved_usd": 0, "context_compression_pct": 0,
                "total_queries": 0, "period": "", "data_source": "unavailable",
                "message": "No usage data yet. Run queries or pipelines to start tracking token consumption.",
            }

    @router.post("/savings/benchmark")
    async def run_live_benchmark(req: dict = Body(...), user=Depends(require_admin)):
        """Run live A/B benchmark: same questions WITH vs WITHOUT AIContextDB.

        Body: {"graph": "graph_name", "questions": ["q1", "q2"], "score_quality": true}
        score_quality (default true) enables LLM-as-judge precision scoring (extra LLM calls).
        """
        from ..benchmark.live_ab import run_live_ab
        graph_name = req.get("graph", "")
        questions = req.get("questions") or None
        score_quality = req.get("score_quality", True)
        if not graph_name:
            raise HTTPException(400, "graph name is required")
        result = run_live_ab(graph_registry, graph_name, questions, score_quality=score_quality)
        return result

    @router.get("/savings/compare")
    async def get_savings_comparison(user=Depends(user_auth)):
        """Side-by-side comparison: with vs without AIContextDB."""
        from ..benchmark.savings import calculate_savings, BASELINE_TOKENS_PER_QUERY
        from ..gateway import get_gateway
        tracker = get_gateway().cost_tracker

        report = calculate_savings(tracker, graph_registry)
        queries = report.get("total_queries", 0)
        actual = report.get("actual_tokens", 0)
        baseline = report.get("baseline_tokens", 0)

        return {
            "data_source": report.get("data_source", "estimated"),
            "with_contextcore": {
                "tokens_per_query": round(actual / queries) if queries > 0 else 0,
                "total_tokens": actual,
                "total_cost_usd": report["actual_cost_usd"],
                "avg_latency_ms": report["avg_latency_ms"],
            },
            "without_contextcore": {
                "tokens_per_query": round(baseline / queries) if queries > 0 else BASELINE_TOKENS_PER_QUERY,
                "total_tokens": baseline,
                "total_cost_usd": report["baseline_cost_usd"],
                "avg_latency_ms": "N/A (full dump each time)",
            },
            "savings": {
                "tokens_saved": report["tokens_saved"],
                "savings_pct": report["savings_pct"],
                "cost_saved_usd": report["cost_saved_usd"],
                "context_compression_pct": report["context_compression_pct"],
            },
            "period": report["period"],
            "note": "Measured from real query data" if report.get("data_source") == "measured" else "Estimated (run queries to get real measurements)",
        }

    # ------------------------------------------------------------------
    # Agent Trust Scores
    # ------------------------------------------------------------------

    @router.get("/trust")
    async def list_trust_scores(user=Depends(user_auth)):
        """List all agents with trust scores."""
        if not agent_registry:
            return {"scores": []}
        try:
            scores = agent_registry.list_trust_scores()
            return {
                "scores": scores,
                "message": None if scores else "No agents registered yet. Agents appear here once they connect and interact with the system.",
            }
        except Exception as e:
            logger.debug("[TRUST] list_trust_scores failed: %s", e)
            return {
                "scores": [],
                "message": "No agents registered yet. Agents appear here once they connect and interact with the system.",
            }

    @router.get("/trust/{agent_id}")
    async def get_agent_trust(agent_id: str, user=Depends(user_auth)):
        """Get trust score for a specific agent."""
        if not agent_registry:
            raise HTTPException(404, "Agent registry not available")
        return agent_registry.get_trust(agent_id)

    @router.post("/trust/{agent_id}/verify")
    async def record_verified_claim(agent_id: str, req: dict = Body(...), user=Depends(require_admin)):
        """Record a verified or false claim for an agent."""
        verified = req.get("verified", True)
        note = req.get("note", "")
        if not agent_registry:
            raise HTTPException(404, "Agent registry not available")
        result = agent_registry.record_claim(agent_id, verified=verified, note=note)
        return result

    @router.put("/trust/{agent_id}/level")
    async def set_agent_trust_level(agent_id: str, req: dict = Body(...), user=Depends(require_admin)):
        """Manually set an agent's trust level (admin override)."""
        level = req.get("level", "")
        note = req.get("note", "manual override")
        if not agent_registry:
            raise HTTPException(404, "Agent registry not available")
        try:
            agent_registry.set_trust_level(agent_id, level, note)
        except ValueError as e:
            raise HTTPException(400, str(e))
        return agent_registry.get_trust(agent_id)

    # ------------------------------------------------------------------
    # Time Travel + Diffing
    # ------------------------------------------------------------------

    @router.get("/time-travel/{graph_name}/at")
    async def graph_at_time(graph_name: str, timestamp: str = "", user=Depends(user_auth)):
        """Get graph state at a specific timestamp."""
        from ..core.time_travel import GraphTimeTraveler
        db = graph_registry.get_graph(graph_name)
        if not db:
            raise HTTPException(404, f"Graph '{graph_name}' not found")
        if not timestamp:
            raise HTTPException(400, "timestamp parameter required (ISO format)")
        tt = GraphTimeTraveler(db)
        result = tt.at(timestamp)
        # Don't return full node objects — just summaries
        nodes = []
        for n in result["nodes"][:50]:
            p = n.properties if hasattr(n, "properties") else {}
            nodes.append({
                "id": n.id if hasattr(n, "id") else "",
                "label": n.label if hasattr(n, "label") else "",
                "name": p.get("name") or p.get("title") or "",
                "created_at": p.get("_created_at", ""),
            })
        return {"timestamp": timestamp, "node_count": result["node_count"], "labels": result["labels"], "nodes": nodes}

    @router.get("/time-travel/{graph_name}/diff")
    async def graph_diff(graph_name: str, t1: str = "", t2: str = "", user=Depends(user_auth)):
        """Compare graph state between two timestamps."""
        from ..core.time_travel import GraphTimeTraveler
        db = graph_registry.get_graph(graph_name)
        if not db:
            raise HTTPException(404, f"Graph '{graph_name}' not found")
        if not t1 or not t2:
            raise HTTPException(400, "t1 and t2 parameters required")
        return GraphTimeTraveler(db).diff(t1, t2)

    @router.get("/time-travel/{graph_name}/timeline")
    async def graph_timeline(graph_name: str, hours: int = 24, user=Depends(user_auth)):
        """Get activity timeline for a graph."""
        from ..core.time_travel import GraphTimeTraveler
        db = graph_registry.get_graph(graph_name)
        if not db:
            raise HTTPException(404, f"Graph '{graph_name}' not found")
        return {"timeline": GraphTimeTraveler(db).timeline(hours=hours)}

    @router.get("/time-travel/{graph_name}/agent/{agent_id}")
    async def agent_graph_activity(graph_name: str, agent_id: str, user=Depends(user_auth)):
        """Get all graph activity by a specific agent."""
        from ..core.time_travel import GraphTimeTraveler
        db = graph_registry.get_graph(graph_name)
        if not db:
            raise HTTPException(404, f"Graph '{graph_name}' not found")
        return {"activity": GraphTimeTraveler(db).agent_activity(agent_id)}

    # ------------------------------------------------------------------
    # Context Marketplace
    # ------------------------------------------------------------------

    @router.get("/marketplace")
    async def marketplace_search(query: str = "", category: str = "", free_only: bool = False, user=Depends(user_auth)):
        """Search the context marketplace."""
        from ..marketplace.registry import get_marketplace
        return {"listings": get_marketplace().search(query, category, free_only)}

    @router.post("/marketplace/publish")
    async def marketplace_publish(req: dict = Body(...), user=Depends(require_admin)):
        """Publish a knowledge graph to the marketplace."""
        from ..marketplace.registry import get_marketplace
        name = req.get("name", "")
        graph = req.get("graph_namespace", "")
        if not name or not graph:
            raise HTTPException(400, "name and graph_namespace required")
        db = graph_registry.get_graph(graph)
        nc = len(db.get_all_nodes()) if db else 0
        ec = len(db.get_all_edges()) if db else 0
        return get_marketplace().publish(
            name=name, graph_namespace=graph, publisher_id=user.user_id,
            publisher_name=getattr(user, "display_name", ""),
            description=req.get("description", ""), category=req.get("category", "general"),
            tags=req.get("tags"), schema_name=req.get("schema", ""),
            price_monthly=req.get("price", 0), node_count=nc, edge_count=ec,
        )

    @router.post("/marketplace/subscribe")
    async def marketplace_subscribe(req: dict = Body(...), user=Depends(user_auth)):
        """Subscribe to a marketplace listing."""
        from ..marketplace.registry import get_marketplace
        return get_marketplace().subscribe(req.get("listing_id", ""), user.user_id, req.get("target_namespace", ""))

    @router.get("/marketplace/my")
    async def my_marketplace(user=Depends(user_auth)):
        from ..marketplace.registry import get_marketplace
        mp = get_marketplace()
        return {"published": mp.my_listings(user.user_id), "subscribed": mp.my_subscriptions(user.user_id)}

    # ------------------------------------------------------------------
    # Agent Skill Learning
    # ------------------------------------------------------------------

    @router.get("/skills/{agent_id}")
    async def get_agent_skills(agent_id: str, user=Depends(user_auth)):
        from ..context.skill_learning import get_skill_learner
        l = get_skill_learner()
        return {"tips": l.get_tips(agent_id), "stats": l.get_agent_stats(agent_id)}

    # ------------------------------------------------------------------
    # Real-Time Collaboration
    # ------------------------------------------------------------------

    @router.get("/collab/{session_id}")
    async def get_collab_info(session_id: str, user=Depends(user_auth)):
        from ..realtime.collaboration import get_collaboration_hub
        return get_collaboration_hub().get_session_info(session_id)

    # ------------------------------------------------------------------
    # Context Intelligence Engine
    # ------------------------------------------------------------------

    @router.get("/intelligence/stats")
    async def intelligence_stats(request: Request):
        """Get intelligence engine stats for dashboard."""
        try:
            from ..intelligence import get_intelligence_stats
            stats = get_intelligence_stats()
            return {"stats": stats}
        except Exception as e:
            return {"stats": {}, "error": str(e)}

    @router.get("/intelligence/conflicts")
    async def intelligence_conflicts(request: Request, status: str = None):
        """List detected conflicts."""
        try:
            from ..intelligence import _modules
            detector = _modules.get("conflict_detector")
            if not detector:
                return {"conflicts": [], "error": "Intelligence engine not started"}
            conflicts = detector.list_conflicts(status=status)
            return {"conflicts": [
                {
                    "conflict_id": c.conflict_id,
                    "node_a_id": c.node_a_id,
                    "node_b_id": c.node_b_id,
                    "conflict_type": c.conflict_type,
                    "summary": c.summary,
                    "severity": c.severity,
                    "status": c.status,
                    "detected_at": c.detected_at,
                    "resolution": c.resolution,
                }
                for c in conflicts
            ]}
        except Exception as e:
            return {"conflicts": [], "error": str(e)}

    @router.post("/intelligence/conflicts/{conflict_id}/resolve")
    async def resolve_conflict(request: Request, conflict_id: str):
        """Resolve a conflict."""
        try:
            body = await request.json()
            resolution = body.get("resolution", "")
            from ..intelligence import _modules
            detector = _modules.get("conflict_detector")
            if not detector:
                return {"error": "Intelligence engine not started"}
            result = detector.resolve_conflict(conflict_id, resolution)
            if result:
                return {"resolved": True, "conflict_id": conflict_id}
            return {"resolved": False, "error": "Conflict not found"}
        except Exception as e:
            return {"resolved": False, "error": str(e)}

    @router.post("/intelligence/conflicts/{conflict_id}/dismiss")
    async def dismiss_conflict(request: Request, conflict_id: str):
        """Dismiss a conflict."""
        try:
            from ..intelligence import _modules
            detector = _modules.get("conflict_detector")
            if not detector:
                return {"error": "Intelligence engine not started"}
            result = detector.dismiss_conflict(conflict_id)
            if result:
                return {"dismissed": True, "conflict_id": conflict_id}
            return {"dismissed": False, "error": "Conflict not found"}
        except Exception as e:
            return {"dismissed": False, "error": str(e)}

    @router.post("/intelligence/feedback")
    async def submit_feedback(request: Request):
        """Submit feedback on a context node (human or agent)."""
        try:
            body = await request.json()
            node_id = body.get("node_id", "")
            signal = body.get("signal", "")
            comment = body.get("comment", "")
            source = body.get("source", "human:dashboard")

            valid = {"helpful", "critical", "irrelevant", "misleading", "outdated"}
            if signal not in valid:
                return {"error": f"Invalid signal. Must be one of: {', '.join(sorted(valid))}"}

            from ..intelligence import _modules
            feedback = _modules.get("feedback_loop")
            if feedback:
                from ..intelligence.feedback_loop import FeedbackRecord
                record = FeedbackRecord(
                    node_id=node_id, signal=signal,
                    source=source, comment=comment,
                )
                await feedback.record_feedback(record)

            return {"recorded": True, "node_id": node_id, "signal": signal}
        except Exception as e:
            return {"recorded": False, "error": str(e)}

    @router.get("/intelligence/suggestions/{agent_id}")
    async def get_suggestions(request: Request, agent_id: str):
        """Get pending suggestions for an agent."""
        try:
            from ..intelligence import _modules
            radar = _modules.get("context_radar")
            if not radar:
                return {"suggestions": []}
            suggestions = radar.get_suggestions(agent_id)
            return {"suggestions": [
                {
                    "suggestion_id": s.suggestion_id,
                    "trigger": s.trigger,
                    "message": s.message,
                    "node_ids": s.node_ids,
                    "status": s.status,
                    "created_at": s.created_at,
                }
                for s in suggestions
            ]}
        except Exception as e:
            return {"suggestions": [], "error": str(e)}

    @router.get("/intelligence/config")
    async def get_intelligence_config(request: Request):
        """Get current intelligence configuration."""
        try:
            from ..intelligence.config import IntelligenceConfig
            cfg = IntelligenceConfig.from_env()
            return {
                "source_watcher": cfg.source_watcher,
                "feedback_loop": cfg.feedback_loop,
                "conflict_detector": cfg.conflict_detector,
                "context_radar": cfg.context_radar,
                "watch_interval_seconds": cfg.watch_interval_seconds,
                "max_versions": cfg.max_versions,
                "conflict_similarity_low": cfg.conflict_similarity_low,
                "conflict_similarity_high": cfg.conflict_similarity_high,
            }
        except Exception as e:
            return {"error": str(e)}

    # ------------------------------------------------------------------
    # Intelligence: Source Watching + Conflict Scan
    # ------------------------------------------------------------------

    @router.get("/intelligence/sources")
    async def list_watched_sources(user=Depends(user_auth)):
        """List all watched sources across graphs."""
        try:
            sources = []
            # Scan contexts for source URLs
            if context_manager:
                for ctx in context_manager.list_contexts():
                    src = ctx.source or ""
                    if src.startswith("http") or src.startswith("text:http"):
                        url = src.replace("text:", "").strip()
                        sources.append({
                            "source_id": ctx.context_id,
                            "type": "url",
                            "uri": url,
                            "context_name": ctx.name,
                            "context_id": ctx.context_id,
                            "status": "watching",
                        })
            return {"sources": sources}
        except Exception as e:
            return {"sources": [], "error": str(e)}

    @router.post("/intelligence/scan")
    async def trigger_intelligence_scan(user=Depends(user_auth)):
        """Run conflict detection and freshness check across all graphs."""
        try:
            from ..intelligence import _modules
            results = {"conflicts_found": 0, "sources_checked": 0, "stale_sources": 0, "graphs_scanned": 0}

            # Run conflict detection on graphs that have contexts (not test graphs)
            detector = _modules.get("conflict_detector")
            if detector and hasattr(detector, 'scan_graph') and context_manager:
                for ctx in context_manager.list_contexts():
                    ns = ctx.graph_namespace
                    if not ns:
                        continue
                    try:
                        db = graph_registry.get_graph(ns) if graph_registry else None
                        if db:
                            found = detector.scan_graph(db, ns)
                            results["conflicts_found"] += found
                            results["graphs_scanned"] += 1
                    except Exception:
                        pass

            # Check source freshness
            watcher = _modules.get("source_watcher")
            if watcher and context_manager:
                from ..intelligence.source_watcher import check_url_freshness
                for ctx in context_manager.list_contexts():
                    src = ctx.source or ""
                    if src.startswith("http") or src.startswith("text:http"):
                        url = src.replace("text:", "").strip()
                        results["sources_checked"] += 1
                        try:
                            check = await check_url_freshness(url, "")
                            if check.get("status") == "modified":
                                results["stale_sources"] += 1
                        except Exception:
                            pass

            return {"status": "completed", "results": results}
        except Exception as e:
            return {"status": "error", "error": str(e)}

    # ------------------------------------------------------------------
    # Scheduled Ingestion Pipeline CRUD + Run/Pause/Resume/Webhook
    # NOTE: /pipelines/schemas must be registered BEFORE /pipelines/{pipeline_id}
    # so FastAPI does not treat "schemas" as a pipeline_id.
    # ------------------------------------------------------------------

    @router.get("/pipelines/schemas")
    async def list_pipeline_schemas(user=Depends(user_auth)):
        """List available extraction schemas for pipeline configuration."""
        try:
            from ..extraction.schema_registry import get_schema_registry
            reg = get_schema_registry()
            schemas = reg.list_schemas() if hasattr(reg, "list_schemas") else []
            return {"schemas": schemas}
        except Exception as e:
            return {"schemas": [], "error": str(e)}

    # ------------------------------------------------------------------
    # Pipeline template endpoints (design-time)
    # ------------------------------------------------------------------

    @router.post("/pipelines", status_code=201)
    async def create_sched_pipeline(
        bg: BackgroundTasks,
        req: dict = Body(...),
        user=Depends(user_auth),
    ):
        """Create a new pipeline (all fields — source, trigger, filters, extraction, steps, models)."""
        from ..pipelines.models import Pipeline as _Pipeline
        target_context_id = req.get("target_context_id", "")
        create_context_name = req.get("create_context_name", "")

        # Auto-create context if requested
        if create_context_name and context_manager:
            try:
                ctx = context_manager.create_context(
                    name=create_context_name,
                    context_type="pipeline",
                    owner_id=user.user_id,
                )
                target_context_id = ctx.context_id
            except Exception as e:
                raise HTTPException(500, f"Failed to create context: {e}")

        p = _Pipeline(
            name=req.get("name", ""),
            description=req.get("description", ""),
            source_type=req.get("source_type", "web_crawl"),
            source_url=req.get("source_url", ""),
            max_pages=req.get("max_pages", 5),
            trigger_type=req.get("trigger_type", "one_time"),
            interval_minutes=req.get("interval_minutes", 60),
            include_keywords=req.get("include_keywords", []),
            exclude_keywords=req.get("exclude_keywords", []),
            semantic_filter=req.get("semantic_filter", ""),
            semantic_threshold=req.get("semantic_threshold", 0.6),
            schema_id=req.get("schema_id", ""),
            schema_mode=req.get("schema_mode", "schema_plus"),
            steps=req.get("steps", {}),
            llm_model=req.get("llm_model", ""),
            embedding_model=req.get("embedding_model", ""),
            target_context_id=target_context_id,
            status="active" if target_context_id else "draft",
        )
        if not p.name:
            raise HTTPException(400, "name is required")
        if p.trigger_type == "scheduled":
            from datetime import timedelta
            p.next_run_at = (datetime.now(timezone.utc) + timedelta(minutes=p.interval_minutes)).isoformat()
        _sched_pipeline_store.save(p)
        event_bus.emit("pipeline_created", {"id": p.id, "name": p.name})
        return p.to_dict()

    @router.get("/pipelines")
    async def list_sched_pipelines(user=Depends(user_auth)):
        """List all pipeline templates."""
        try:
            pipelines = _sched_pipeline_store.list_all()
            return {"pipelines": [p.to_dict() for p in pipelines]}
        except Exception as e:
            return {"pipelines": [], "error": str(e)}

    @router.get("/pipelines/runs")
    async def list_pipeline_runs_global(
        limit: int = 50,
        graph: str = "",
        pipeline_id: str = "",
        status: str = "",
        user=Depends(user_auth),
    ):
        """List all pipeline runs (proxy to pipeline_router's store)."""
        try:
            from ..ingestion.pipeline_store import PipelineStore
            store = PipelineStore()
            tenant = _get_tenant(user)
            runs = store.list_runs(
                tenant.tenant_id,
                pipeline_id=pipeline_id or None,
                graph=graph or None,
                status=status or None,
                limit=limit,
            )
            return {"runs": runs}
        except Exception as e:
            logger.warning("list_pipeline_runs_global: %s", e)
            return {"runs": []}

    @router.get("/pipelines/runs/{run_id}")
    async def get_pipeline_run_global(run_id: str, user=Depends(user_auth)):
        """Get a specific pipeline run (proxy to pipeline_router's store)."""
        try:
            from ..ingestion.pipeline_store import PipelineStore
            store = PipelineStore()
            run = store.get_run(run_id)
            if not run:
                raise HTTPException(404, "Run not found")
            return run
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(500, str(e))

    @router.get("/pipelines/{pipeline_id}")
    async def get_sched_pipeline(pipeline_id: str, user=Depends(user_auth)):
        """Get a pipeline by id, including run history."""
        p = _sched_pipeline_store.get(pipeline_id)
        if not p:
            raise HTTPException(404, f"Pipeline {pipeline_id} not found")
        result = p.to_dict()
        result["runs"] = _sched_pipeline_store.get_runs(pipeline_id, limit=20)
        return result

    @router.put("/pipelines/{pipeline_id}")
    async def update_sched_pipeline(
        pipeline_id: str,
        req: dict = Body(...),
        user=Depends(require_member),
    ):
        """Update any pipeline field."""
        p = _sched_pipeline_store.get(pipeline_id)
        if not p:
            raise HTTPException(404, f"Pipeline {pipeline_id} not found")
        old_context_id = p.target_context_id
        updatable = [
            "name", "description", "source_type", "source_url", "max_pages",
            "trigger_type", "interval_minutes",
            "include_keywords", "exclude_keywords", "semantic_filter", "semantic_threshold",
            "schema_id", "schema_mode", "steps",
            "llm_model", "embedding_model", "target_context_id",
        ]
        for key in updatable:
            if key in req:
                setattr(p, key, req[key])
        # Promote draft -> active when target_context_id is first set
        if not old_context_id and p.target_context_id and p.status == "draft":
            p.status = "active"
        if p.trigger_type == "scheduled" and not p.next_run_at:
            from datetime import timedelta
            p.next_run_at = (datetime.now(timezone.utc) + timedelta(minutes=p.interval_minutes)).isoformat()
        _sched_pipeline_store.save(p)
        event_bus.emit("pipeline_updated", {"id": p.id, "name": p.name})
        return p.to_dict()

    @router.delete("/pipelines/{pipeline_id}", status_code=204)
    async def delete_sched_pipeline(pipeline_id: str, user=Depends(require_member)):
        """Delete a pipeline template."""
        p = _sched_pipeline_store.get(pipeline_id)
        if not p:
            raise HTTPException(404, f"Pipeline {pipeline_id} not found")
        _sched_pipeline_store.delete(pipeline_id)
        event_bus.emit("pipeline_deleted", {"id": pipeline_id})
        return None

    # ------------------------------------------------------------------
    # Pipeline run / pause / resume / runs / webhook
    # ------------------------------------------------------------------

    @router.post("/pipelines/{pipeline_id}/run")
    async def run_sched_pipeline(
        pipeline_id: str,
        bg: BackgroundTasks,
        user=Depends(require_member),
    ):
        """Trigger a manual run of the pipeline in the background."""
        p = _sched_pipeline_store.get(pipeline_id)
        if not p:
            raise HTTPException(404, f"Pipeline {pipeline_id} not found")
        if p.status == "paused":
            raise HTTPException(400, "Pipeline is paused. Resume it first.")
        from ..pipelines.executor import execute_pipeline
        bg.add_task(execute_pipeline, pipeline_id, _sched_pipeline_store)
        event_bus.emit("pipeline_run_triggered", {"id": pipeline_id, "name": p.name})
        return {"status": "queued", "pipeline_id": pipeline_id, "message": "Pipeline run started in background"}

    @router.post("/pipelines/{pipeline_id}/pause")
    async def pause_sched_pipeline(pipeline_id: str, user=Depends(require_member)):
        """Pause a pipeline."""
        p = _sched_pipeline_store.get(pipeline_id)
        if not p:
            raise HTTPException(404, f"Pipeline {pipeline_id} not found")
        p.status = "paused"
        _sched_pipeline_store.save(p)
        event_bus.emit("pipeline_paused", {"id": pipeline_id})
        return {"status": "paused", "pipeline_id": pipeline_id}

    @router.post("/pipelines/{pipeline_id}/resume")
    async def resume_sched_pipeline(pipeline_id: str, user=Depends(require_member)):
        """Resume a paused pipeline."""
        p = _sched_pipeline_store.get(pipeline_id)
        if not p:
            raise HTTPException(404, f"Pipeline {pipeline_id} not found")
        p.status = "active"
        if p.trigger_type == "scheduled" and not p.next_run_at:
            from datetime import timedelta
            p.next_run_at = (datetime.now(timezone.utc) + timedelta(minutes=p.interval_minutes)).isoformat()
        _sched_pipeline_store.save(p)
        event_bus.emit("pipeline_resumed", {"id": pipeline_id})
        return {"status": "active", "pipeline_id": pipeline_id}

    @router.get("/pipelines/{pipeline_id}/runs")
    async def list_sched_pipeline_runs(
        pipeline_id: str,
        limit: int = Query(20, ge=1, le=100),
        user=Depends(user_auth),
    ):
        """List run history for a pipeline."""
        p = _sched_pipeline_store.get(pipeline_id)
        if not p:
            raise HTTPException(404, f"Pipeline {pipeline_id} not found")
        runs = _sched_pipeline_store.get_runs(pipeline_id, limit=limit)
        return {"pipeline_id": pipeline_id, "runs": runs, "total": len(runs)}

    @router.post("/pipelines/{pipeline_id}/webhook")
    async def webhook_sched_pipeline(
        pipeline_id: str,
        bg: BackgroundTasks,
        req: dict = Body(default={}),
    ):
        """Webhook trigger for a pipeline. No auth required — pipeline_id acts as the secret."""
        p = _sched_pipeline_store.get(pipeline_id)
        if not p:
            raise HTTPException(404, f"Pipeline {pipeline_id} not found")
        if p.status == "paused":
            raise HTTPException(400, "Pipeline is paused")
        from ..pipelines.executor import execute_pipeline
        bg.add_task(execute_pipeline, pipeline_id, _sched_pipeline_store)
        event_bus.emit("pipeline_webhook_triggered", {"id": pipeline_id, "payload": req})
        return {"status": "queued", "pipeline_id": pipeline_id}

    return router
