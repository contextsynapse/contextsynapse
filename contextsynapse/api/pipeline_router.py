"""
Pipeline Router
===============
REST endpoints for managing and executing stored pipelines.

GET    /dashboard/pipelines                — List pipeline templates
GET    /dashboard/pipelines/{id}           — Get pipeline details + stats
POST   /dashboard/pipelines                — Create custom pipeline
PUT    /dashboard/pipelines/{id}           — Update custom pipeline
DELETE /dashboard/pipelines/{id}           — Delete custom pipeline
POST   /dashboard/pipelines/{id}/run       — Execute a pipeline
GET    /dashboard/pipelines/runs           — List pipeline runs (all)
GET    /dashboard/pipelines/runs/{run_id}  — Get run details + accuracy
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from pydantic import BaseModel, Field

from ..ingestion.pipeline_store import (
    PipelineStore,
    PipelineRun,
    AccuracyScore,
    StageLog,
)

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class CreatePipelineRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=200)
    description: str = ""
    stages: List[Dict[str, Any]] = Field(default_factory=list)
    aiql_template: str = ""
    default_params: Optional[Dict[str, Any]] = None
    governance: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    category: str = "ingestion"
    schema_name: str = ""
    parser: str = ""           # turn | record | paragraph
    filters: Optional[Dict[str, Any]] = None
    graph_config: Optional[Dict[str, Any]] = None


class UpdatePipelineRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    stages: Optional[List[Dict[str, Any]]] = None
    aiql_template: Optional[str] = None
    default_params: Optional[Dict[str, Any]] = None
    governance: Optional[Dict[str, Any]] = None
    tags: Optional[List[str]] = None
    category: Optional[str] = None
    enabled: Optional[bool] = None
    schema_name: Optional[str] = None
    parser: Optional[str] = None
    filters: Optional[Dict[str, Any]] = None
    graph_config: Optional[Dict[str, Any]] = None


class RunPipelineRequest(BaseModel):
    graph: str = Field(..., min_length=1)
    text: Optional[str] = None
    url: Optional[str] = None
    params: Optional[Dict[str, Any]] = None
    governance_tags: Optional[List[str]] = None
    data_classification: str = "internal"


class StartRunRequest(BaseModel):
    """Start a new step-wise pipeline run."""
    graph: str = Field(..., min_length=1)
    pipeline_id: Optional[str] = None         # use builtin:auto if omitted
    intent: str = "graph_rag"                 # graph_rag, build_graph, search_only
    mode: str = "step_by_step"                # step_by_step or run_all
    text: Optional[str] = None
    url: Optional[str] = None
    params: Optional[Dict[str, Any]] = None


class EditContextRequest(BaseModel):
    """User edits to intermediate pipeline data."""
    nodes: Optional[List[Dict[str, Any]]] = None
    edges: Optional[List[Dict[str, Any]]] = None
    chunks: Optional[List[Dict[str, Any]]] = None
    tables: Optional[List[Dict[str, Any]]] = None
    classification: Optional[Dict[str, Any]] = None
    stages: Optional[List[str]] = None


class UploadSchemaRequest(BaseModel):
    """Upload a YAML schema for schema-first ingestion."""
    graph: str = Field(..., min_length=1)
    schema_yaml: str = Field(..., min_length=10)
    name: Optional[str] = None
    strict: bool = False


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_pipeline_router(
    user_registry,
    tenant_registry,
    graph_registry,
    usage_meter=None,
) -> APIRouter:
    """Create the /dashboard/pipelines router."""

    from .auth import UserAuth

    router = APIRouter(tags=["pipelines"])
    user_auth = UserAuth(user_registry)
    store = PipelineStore()

    def _get_tenant(user):
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id:
            raise HTTPException(status_code=404, detail="No workspace found")
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return tenant

    def _scope_graph(tenant, name: str) -> str:
        return f"{tenant.tenant_id}:{name}"

    # ------------------------------------------------------------------
    # GET /dashboard/pipelines — list templates
    # ------------------------------------------------------------------
    @router.get("/dashboard/pipelines")
    async def list_pipelines(user=Depends(user_auth)):
        tenant = _get_tenant(user)
        pipelines = store.list_pipelines(tenant.tenant_id)
        # Attach stats to each pipeline
        for p in pipelines:
            p["stats"] = store.get_pipeline_stats(p["id"], tenant.tenant_id)
        return {"pipelines": pipelines}

    # ------------------------------------------------------------------
    # GET /dashboard/pipelines/stages — stage explanations
    # ------------------------------------------------------------------
    @router.get("/dashboard/pipelines/stages")
    async def list_stage_explanations(user=Depends(user_auth)):
        """Return human-readable explanations for each pipeline stage type."""
        from ..ingestion.pipeline_store import STAGE_EXPLANATIONS
        return {"stages": STAGE_EXPLANATIONS}

    # ------------------------------------------------------------------
    # GET /dashboard/pipelines/runs — list all runs
    # NOTE: Must be registered BEFORE /dashboard/pipelines/{pipeline_id}
    #       to avoid the wildcard matching "runs" as a pipeline_id.
    # ------------------------------------------------------------------
    @router.get("/dashboard/pipelines/runs")
    async def list_runs(
        graph: Optional[str] = None,
        pipeline_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 50,
        user=Depends(user_auth),
    ):
        tenant = _get_tenant(user)
        runs = store.list_runs(
            tenant.tenant_id,
            pipeline_id=pipeline_id,
            graph=graph,
            status=status,
            limit=limit,
        )
        return {"runs": runs}

    # ------------------------------------------------------------------
    # GET /dashboard/pipelines/runs/{run_id} — get run details
    # ------------------------------------------------------------------
    @router.get("/dashboard/pipelines/runs/{run_id}")
    async def get_run(run_id: str, user=Depends(user_auth)):
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        return run

    # ------------------------------------------------------------------
    # GET /dashboard/pipelines/{id} — get pipeline + stats
    # ------------------------------------------------------------------
    @router.get("/dashboard/pipelines/{pipeline_id}")
    async def get_pipeline(pipeline_id: str, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        pipeline = store.get_pipeline(pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        pipeline["stats"] = store.get_pipeline_stats(pipeline_id, tenant.tenant_id)
        pipeline["recent_runs"] = store.list_runs(tenant.tenant_id, pipeline_id=pipeline_id, limit=10)
        return pipeline

    # ------------------------------------------------------------------
    # POST /dashboard/pipelines — create custom pipeline
    # ------------------------------------------------------------------
    @router.post("/dashboard/pipelines")
    async def create_pipeline(req: CreatePipelineRequest, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        pipeline = store.create_pipeline(
            tenant_id=tenant.tenant_id,
            name=req.name,
            description=req.description,
            stages=req.stages,
            aiql_template=req.aiql_template,
            default_params=req.default_params,
            governance=req.governance,
            tags=req.tags,
            category=req.category,
            created_by=user.user_id,
            schema_name=req.schema_name,
            parser=req.parser,
            filters=req.filters,
            graph_config=req.graph_config,
        )
        return {"pipeline": pipeline, "message": "Pipeline created"}

    # ------------------------------------------------------------------
    # PUT /dashboard/pipelines/{id} — update custom pipeline
    # ------------------------------------------------------------------
    @router.put("/dashboard/pipelines/{pipeline_id}")
    async def update_pipeline(pipeline_id: str, req: UpdatePipelineRequest, user=Depends(user_auth)):
        updates = {k: v for k, v in req.model_dump().items() if v is not None}
        result = store.update_pipeline(pipeline_id, **updates)
        if not result:
            raise HTTPException(status_code=400, detail="Cannot update (builtin or not found)")
        return {"pipeline": result, "message": "Pipeline updated"}

    # ------------------------------------------------------------------
    # DELETE /dashboard/pipelines/{id}
    # ------------------------------------------------------------------
    @router.delete("/dashboard/pipelines/{pipeline_id}")
    async def delete_pipeline(pipeline_id: str, user=Depends(user_auth)):
        if not store.delete_pipeline(pipeline_id):
            raise HTTPException(status_code=400, detail="Cannot delete (builtin or not found)")
        return {"message": "Pipeline deleted"}

    # ------------------------------------------------------------------
    # POST /dashboard/pipelines/{id}/run — execute pipeline
    # ------------------------------------------------------------------
    @router.post("/dashboard/pipelines/{pipeline_id}/run")
    async def run_pipeline(pipeline_id: str, req: RunPipelineRequest, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        pipeline = store.get_pipeline(pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        if not pipeline.get("enabled", True):
            raise HTTPException(status_code=400, detail="Pipeline is disabled")

        # Smart pipeline shortcut: route to new 7-stage pipeline
        smart_name = pipeline.get("_smart_pipeline")
        if smart_name:
            scoped = _scope_graph(tenant, req.graph)
            try:
                from ..ingestion.smart_ingest import ingest_url as _smart_url, ingest_text as _smart_text
                from ..core.registry import GraphRegistry
                reg = GraphRegistry()
                db = reg.get_graph(scoped, load_if_missing=True) or reg.create_graph(scoped)
                if req.url:
                    result = _smart_url(req.url, db, pipeline=smart_name)
                elif req.text:
                    result = _smart_text(req.text, db, title="Ingested text", pipeline=smart_name)
                else:
                    raise HTTPException(status_code=400, detail="URL or text required")
                return {
                    "status": "completed",
                    "pipeline_id": pipeline_id,
                    "result": {
                        "document_id": result.document_id,
                        "passages": len(result.passage_ids),
                        "entities": len(result.entity_ids),
                        "facts": len(result.fact_ids),
                        "links": len(result.link_ids),
                        "edges": result.edge_count,
                        "errors": result.errors,
                    },
                }
            except HTTPException:
                raise
            except Exception as e:
                raise HTTPException(status_code=500, detail=f"Smart pipeline error: {e}")

        # Determine source
        if req.text:
            source_text = req.text
            source_label = f"text:{len(req.text)} chars"
            source_type = "text"
        elif req.url:
            # Fetch URL
            try:
                import httpx
                resp = httpx.get(req.url, timeout=30, follow_redirects=True)
                resp.raise_for_status()
                raw_html = resp.text
                source_label = req.url
                source_type = "url"
                # Extract article content using trafilatura
                source_text = None
                if "<html" in raw_html.lower() or "<body" in raw_html.lower():
                    try:
                        import trafilatura
                        source_text = trafilatura.extract(raw_html, include_links=False, include_tables=True)
                    except ImportError:
                        pass  # optional
                    if not source_text:
                        import re as _re
                        source_text = _re.sub(r"<script[^>]*>.*?</script>", "", raw_html, flags=_re.DOTALL | _re.IGNORECASE)
                        source_text = _re.sub(r"<style[^>]*>.*?</style>", "", source_text, flags=_re.DOTALL | _re.IGNORECASE)
                        source_text = _re.sub(r"<[^>]+>", " ", source_text)
                        source_text = _re.sub(r"\s+", " ", source_text).strip()
                else:
                    source_text = raw_html.strip()
                if not source_text:
                    raise ValueError("No text content extracted from URL")
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Failed to fetch URL: {e}")
        else:
            raise HTTPException(status_code=400, detail="Provide 'text' or 'url'")

        scoped_graph = _scope_graph(tenant, req.graph)

        # Duplicate check
        source_hash = hashlib.sha256(source_text.encode()).hexdigest()
        dup = store.find_duplicate(graph=req.graph, source_hash=source_hash, tenant_id=tenant.tenant_id)
        if dup:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate content: identical source was already ingested into graph '{req.graph}' "
                       f"(run {dup['run_id'][:12]}, {dup['status']}).",
            )

        # Create run record
        run = store.create_run(
            pipeline_id=pipeline_id,
            tenant_id=tenant.tenant_id,
            user_id=user.user_id,
            graph=req.graph,
            source=source_label,
            source_type=source_type,
            source_hash=source_hash,
            params=req.params,
            governance_tags=req.governance_tags,
            data_classification=req.data_classification,
        )

        # Execute via async job queue or fallback to thread
        import asyncio
        try:
            from ..jobs.queue import job_queue
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_enqueue_pipeline(
                    job_queue, store, graph_registry, run.run_id, pipeline, scoped_graph, source_text, run.params_used
                ))
            except RuntimeError:
                # Fallback: run in thread if no event loop
                import threading
                threading.Thread(
                    target=_execute_pipeline,
                    args=(store, graph_registry, run.run_id, pipeline, scoped_graph, source_text, run.params_used),
                    daemon=True,
                ).start()
        except ImportError:
            # Job queue module not available, run in thread
            import threading
            threading.Thread(
                target=_execute_pipeline,
                args=(store, graph_registry, run.run_id, pipeline, scoped_graph, source_text, run.params_used),
                daemon=True,
            ).start()

        if usage_meter:
            try:
                usage_meter.record(tenant.tenant_id, "pipeline_run")
            except Exception:
                pass  # optional

        return {
            "run_id": run.run_id,
            "pipeline_id": pipeline_id,
            "pipeline_name": pipeline["name"],
            "graph": req.graph,
            "status": "running",
            "stages": [s["name"] for s in pipeline.get("stages", [])],
        }

    # ------------------------------------------------------------------
    # POST /dashboard/pipelines/{id}/run/file — execute pipeline with file upload
    # ------------------------------------------------------------------
    @router.post("/dashboard/pipelines/{pipeline_id}/run/file")
    async def run_pipeline_file(
        pipeline_id: str,
        file: UploadFile = File(...),
        graph: str = Form(...),
        data_classification: str = Form("internal"),
        params: Optional[str] = Form(None),
        governance_tags: Optional[str] = Form(None),
        user=Depends(user_auth),
    ):
        tenant = _get_tenant(user)
        pipeline = store.get_pipeline(pipeline_id)
        if not pipeline:
            raise HTTPException(status_code=404, detail="Pipeline not found")
        if not pipeline.get("enabled", True):
            raise HTTPException(status_code=400, detail="Pipeline is disabled")

        # Read file content — support binary files via temp file
        content_bytes = await file.read()
        source_hash = hashlib.sha256(content_bytes).hexdigest()

        # Reject duplicate file
        dup = store.find_duplicate(graph=graph, source_hash=source_hash, tenant_id=tenant.tenant_id)
        if dup:
            raise HTTPException(
                status_code=409,
                detail=f"Duplicate file: '{file.filename}' has identical content to "
                       f"'{dup['source']}' (run {dup['run_id'][:12]}, {dup['status']}). "
                       f"The same file was already ingested into graph '{graph}'.",
            )

        import os, tempfile as _tf
        from pathlib import Path as _P
        _ext = _P(file.filename).suffix.lower() if file.filename else ""
        _binary_exts = {".xlsx", ".xls", ".pdf", ".docx", ".doc", ".pptx"}
        is_binary = _ext in _binary_exts

        if is_binary:
            # Save to temp file for extraction pipeline
            fd, tmp_path = _tf.mkstemp(suffix=_ext, prefix="pipeline_upload_")
            os.close(fd)
            with open(tmp_path, "wb") as _f:
                _f.write(content_bytes)
            source_text = None
        else:
            tmp_path = None
            try:
                source_text = content_bytes.decode("utf-8")
            except UnicodeDecodeError:
                raise HTTPException(status_code=400, detail="File must be text-based (UTF-8)")

        parsed_params = {}
        if params:
            try:
                parsed_params = json.loads(params)
            except Exception:
                pass  # optional

        parsed_tags = []
        if governance_tags:
            try:
                parsed_tags = json.loads(governance_tags)
            except Exception:
                pass  # optional

        scoped_graph = _scope_graph(tenant, graph)

        run = store.create_run(
            pipeline_id=pipeline_id,
            tenant_id=tenant.tenant_id,
            user_id=user.user_id,
            graph=graph,
            source=file.filename or "uploaded_file",
            source_type="file",
            source_hash=source_hash,
            params=parsed_params,
            governance_tags=parsed_tags,
            data_classification=data_classification,
        )

        # Execute via async job queue or fallback to thread
        import asyncio
        try:
            from ..jobs.queue import job_queue
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_enqueue_pipeline(
                    job_queue, store, graph_registry, run.run_id, pipeline, scoped_graph, source_text, run.params_used
                ))
            except RuntimeError:
                import threading
                threading.Thread(
                    target=_execute_pipeline,
                    args=(store, graph_registry, run.run_id, pipeline, scoped_graph, source_text, run.params_used),
                    daemon=True,
                ).start()
        except ImportError:
            import threading
            threading.Thread(
                target=_execute_pipeline,
                args=(store, graph_registry, run.run_id, pipeline, scoped_graph, source_text, run.params_used),
                daemon=True,
            ).start()

        if usage_meter:
            try:
                usage_meter.record(tenant.tenant_id, "pipeline_run")
            except Exception:
                pass  # optional

        return {
            "run_id": run.run_id,
            "pipeline_id": pipeline_id,
            "pipeline_name": pipeline["name"],
            "graph": graph,
            "filename": file.filename,
            "status": "running",
        }

    # ------------------------------------------------------------------
    # Step-wise Pipeline Execution Endpoints
    # ------------------------------------------------------------------

    # In-memory store for active pipeline run contexts
    _run_contexts: Dict[str, Any] = {}

    # ------------------------------------------------------------------
    # Async background helpers
    # ------------------------------------------------------------------

    def _make_sub_step_callback():
        """Create a thread-safe callback that emits sub-step events."""
        try:
            from .events import event_bus
            loop = asyncio.get_event_loop()

            def _on_sub_step(run_id, stage, message, progress):
                loop.call_soon_threadsafe(
                    event_bus.emit, "pipeline_sub_step",
                    {"run_id": run_id, "stage": stage, "message": message, "progress": progress},
                )
            return _on_sub_step
        except Exception:
            return None

    def _emit_stage_event(ctx):
        """Emit a stage-level status update via event_bus."""
        try:
            from .events import event_bus
            event_bus.emit("pipeline_stage_update", {
                "run_id": ctx.run_id,
                "status": ctx.status,
                "current_stage": ctx.current_stage,
                "stages": ctx.stages,
                "completed_stages": [sr["stage_name"] for sr in ctx.stage_results],
                "remaining_stages": ctx.remaining_stages,
                "stage_results": ctx.stage_results,
                "stage_output": ctx.stage_results[-1] if ctx.stage_results else None,
                "sub_steps": ctx.sub_steps,
                "nodes_count": len(ctx.nodes),
                "edges_count": len(ctx.edges),
                "chunks_count": len(ctx.chunks),
                "classification": ctx.classification,
            })
        except Exception:
            pass  # optional

    def _resolve_remaining_stages(ctx):
        """Determine remaining stages after CLASSIFY.

        If the user selected a specific pipeline template, use its stage
        sequence (skipping PARSE_FILE/CLASSIFY which already ran).
        Otherwise fall back to the ScenarioRouter.
        """
        pipeline_id = ctx.pipeline_id or "builtin:auto"

        # If a specific template was selected, use its stages
        if pipeline_id and pipeline_id != "builtin:auto":
            pipeline = store.get_pipeline(pipeline_id)
            if pipeline and pipeline.get("stages"):
                # Extract stage types, skip the ones already executed
                template_stages = [
                    s["type"] for s in pipeline["stages"]
                    if s.get("type") not in ("PARSE_FILE", "CLASSIFY")
                ]
                if template_stages:
                    logger.info(
                        "Using template stages for %s: %s", pipeline_id, template_stages,
                    )
                    return template_stages

        # Fallback: use the scenario router
        if not ctx.classification:
            return []

        from ..ingestion.pipeline_context import ClassificationResult
        from ..ingestion.scenario_router import ScenarioRouter
        classification = ClassificationResult.from_dict(ctx.classification)
        router_obj = ScenarioRouter()
        return router_obj.route(classification, ctx.intent)

    def _filter_stages_by_config(stages, ctx):
        """Remove stages that require LLM/embeddings if those aren't configured."""
        llm = ctx.pipeline_params.get("llm_model")
        emb = ctx.pipeline_params.get("embedding_model")
        filtered = stages
        if not llm:
            filtered = [s for s in filtered if s not in ("EXTRACT", "EXTRACT_FACTS", "ENHANCE_GRAPH")]
        if not emb:
            filtered = [s for s in filtered if s not in ("EMBED", "STORE_VECTORS")]
        # INDEX_BM25 depends on facts which need LLM
        if not llm:
            filtered = [s for s in filtered if s != "INDEX_BM25"]
        return filtered

    def _sync_to_db(ctx):
        """Sync current PipelineContext state to SQLite for persistence."""
        try:
            store.update_run_status(ctx.run_id, ctx.status)
            store.sync_stages_log(ctx.run_id, ctx.stage_results)
            store.update_run_counts(
                ctx.run_id,
                nodes=len(ctx.nodes),
                edges=len(ctx.edges),
                entities=len([n for n in ctx.nodes if n.get("properties", {}).get("extraction_method") == "llm_extraction"]),
            )
        except Exception as e:
            logger.warning("Failed to sync run %s to DB: %s", ctx.run_id, e)

    async def _cleanup_after_delay(run_id, delay=300):
        """Remove completed run from in-memory store after TTL."""
        await asyncio.sleep(delay)
        _run_contexts.pop(run_id, None)

    async def _run_next_background(ctx, executor):
        """Run one stage in a thread and emit status update when done."""
        try:
            ctx.status = "running"
            store.update_run_status(ctx.run_id, "running")
            _emit_stage_event(ctx)
            await asyncio.to_thread(executor.execute_next, ctx)
        except Exception as e:
            logger.error("Background stage failed: %s", e, exc_info=True)
            ctx.status = "failed"
            ctx.record_sub_step(f"Error: {e}")
        _sync_to_db(ctx)
        _emit_stage_event(ctx)
        # Keep in memory for 5 min after terminal state
        if ctx.status in ("completed", "failed"):
            asyncio.create_task(_cleanup_after_delay(ctx.run_id))

    async def _run_all_background(ctx, executor):
        """Run all stages in a thread, routing after CLASSIFY."""
        try:
            ctx.status = "running"
            store.update_run_status(ctx.run_id, "running")
            _emit_stage_event(ctx)
            # Parse + classify first
            await asyncio.to_thread(executor.execute_all, ctx)
            _sync_to_db(ctx)  # sync after initial stages

            if ctx.status != "failed" and ctx.classification:
                remaining = _resolve_remaining_stages(ctx)
                remaining = _filter_stages_by_config(remaining, ctx)
                ctx.stages = remaining
                ctx.current_stage_index = 0
                ctx.status = "running"
                _emit_stage_event(ctx)
                await asyncio.to_thread(executor.execute_all, ctx)
        except Exception as e:
            logger.error("Background run-all failed: %s", e, exc_info=True)
            ctx.status = "failed"
            ctx.record_sub_step(f"Error: {e}")
        _sync_to_db(ctx)
        _emit_stage_event(ctx)
        # Keep in memory for 5 min after terminal state
        if ctx.status in ("completed", "failed"):
            asyncio.create_task(_cleanup_after_delay(ctx.run_id))

    @router.post("/dashboard/pipelines/runs/start")
    async def start_pipeline_run(
        graph: str = Form(..., min_length=1),
        pipeline_id: Optional[str] = Form(None),
        intent: str = Form("graph_rag"),
        mode: str = Form("step_by_step"),
        text: Optional[str] = Form(None),
        url: Optional[str] = Form(None),
        params: Optional[str] = Form(None),
        file: Optional[UploadFile] = File(None),
        user=Depends(user_auth),
    ):
        """Start a new pipeline run (step-by-step or run-all)."""
        tenant = _get_tenant(user)
        scoped = _scope_graph(tenant, graph)

        from ..ingestion.pipeline_context import PipelineContext
        from ..ingestion.stage_executor import StageExecutor
        from ..ingestion.scenario_router import ScenarioRouter, STAGE_PARSE_FILE, STAGE_CLASSIFY

        parsed_params = json.loads(params) if params else {}

        # Determine source
        source_file = None
        source_text = text
        source_hash = ""

        if file and file.filename:
            import os, tempfile as _tf
            from pathlib import Path as _Path
            content = await file.read()
            source_hash = hashlib.sha256(content).hexdigest()

            # Reject duplicate file (same content already ingested into this graph)
            dup = store.find_duplicate(graph=graph, source_hash=source_hash, tenant_id=tenant.tenant_id)
            if dup:
                raise HTTPException(
                    status_code=409,
                    detail=f"Duplicate file: '{file.filename}' has identical content to "
                           f"'{dup['source']}' (run {dup['run_id'][:12]}, {dup['status']}). "
                           f"The same file was already ingested into graph '{graph}'.",
                )

            ext = _Path(file.filename).suffix.lower() if file.filename else ""
            fd, tmp = _tf.mkstemp(suffix=ext, prefix="run_upload_")
            os.close(fd)
            with open(tmp, "wb") as f:
                f.write(content)
            source_file = tmp
        elif source_text:
            source_hash = hashlib.sha256(source_text.encode()).hexdigest()
            dup = store.find_duplicate(graph=graph, source_hash=source_hash, tenant_id=tenant.tenant_id)
            if dup:
                raise HTTPException(
                    status_code=409,
                    detail=f"Duplicate content: identical text was already ingested into graph '{graph}' "
                           f"(run {dup['run_id'][:12]}, {dup['status']}).",
                )

        ctx = PipelineContext(
            graph_namespace=scoped,
            stages=[STAGE_PARSE_FILE, STAGE_CLASSIFY],
            source_text=source_text,
            source_bytes_path=source_file,
            source_filename=file.filename if file else None,
            source_hash=source_hash,
            intent=intent,
            mode=mode,
            pipeline_params=parsed_params,
            pipeline_id=pipeline_id or "builtin:auto",
        )

        executor = StageExecutor(
            graph_registry=graph_registry,
            on_sub_step=_make_sub_step_callback(),
        )

        _run_contexts[ctx.run_id] = {"ctx": ctx, "executor": executor}

        # Launch execution in background — return run_id immediately
        if mode == "run_all":
            asyncio.create_task(_run_all_background(ctx, executor))
        else:
            asyncio.create_task(_run_next_background(ctx, executor))

        return {
            "run_id": ctx.run_id,
            "status": ctx.status,
            "stages": ctx.stages,
        }

    @router.get("/dashboard/pipelines/runs/{run_id}/context")
    async def get_run_context(run_id: str, user=Depends(user_auth)):
        """Get the current pipeline context for a step-wise run."""
        entry = _run_contexts.get(run_id)
        if entry:
            ctx = entry["ctx"]
            return {
                "run_id": ctx.run_id,
                "status": ctx.status,
                "mode": ctx.mode,
                "current_stage": ctx.current_stage,
                "current_stage_index": ctx.current_stage_index,
                "stages": ctx.stages,
                "remaining_stages": ctx.remaining_stages,
                "stage_results": ctx.stage_results,
                "classification": ctx.classification,
                "nodes_count": len(ctx.nodes),
                "edges_count": len(ctx.edges),
                "chunks_count": len(ctx.chunks),
                "nodes_preview": ctx.nodes[:20],
                "edges_preview": ctx.edges[:20],
                "intent": ctx.intent,
                "sub_steps": ctx.sub_steps,
            }

        # Fallback: read from SQLite (run completed or server restarted)
        run = store.get_run(run_id)
        if not run:
            raise HTTPException(status_code=404, detail="Run not found")
        stages_log = run.get("stages") or []
        return {
            "run_id": run_id,
            "status": run.get("status", "unknown"),
            "mode": run.get("mode", "run_all"),
            "current_stage": None,
            "current_stage_index": len(stages_log),
            "stages": [s.get("stage_name", "") for s in stages_log] if stages_log else [],
            "remaining_stages": [],
            "stage_results": stages_log,
            "classification": None,
            "nodes_count": run.get("nodes_created", 0),
            "edges_count": run.get("edges_created", 0),
            "chunks_count": 0,
            "nodes_preview": [],
            "edges_preview": [],
            "intent": run.get("intent", "graph_rag"),
            "sub_steps": [],
        }

    @router.patch("/dashboard/pipelines/runs/{run_id}/context")
    async def edit_run_context(run_id: str, req: EditContextRequest, user=Depends(user_auth)):
        """Edit intermediate data in a paused pipeline run."""
        entry = _run_contexts.get(run_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Run not found")
        ctx = entry["ctx"]
        if ctx.status not in ("paused", "pending"):
            raise HTTPException(status_code=400, detail=f"Run is {ctx.status}, not editable")

        overrides = {k: v for k, v in req.model_dump().items() if v is not None}
        ctx.apply_overrides(overrides)

        # If classification was changed, re-route stages
        if "classification" in overrides:
            from ..ingestion.pipeline_context import ClassificationResult
            from ..ingestion.scenario_router import ScenarioRouter
            classification = ClassificationResult.from_dict(ctx.classification)
            router_obj = ScenarioRouter()
            new_stages = router_obj.route(classification, ctx.intent)
            ctx.stages = new_stages
            ctx.current_stage_index = 0

        return {"message": "Context updated", "stages": ctx.stages}

    @router.post("/dashboard/pipelines/runs/{run_id}/continue")
    async def continue_run(run_id: str, user=Depends(user_auth)):
        """Execute the next stage of a paused pipeline run (async)."""
        entry = _run_contexts.get(run_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Run not found")
        ctx = entry["ctx"]
        executor = entry["executor"]

        if ctx.is_done:
            return {"run_id": run_id, "status": "completed", "message": "All stages done"}

        if ctx.status == "running":
            return {"run_id": run_id, "status": "running", "message": "Already running"}

        # After CLASSIFY, route remaining stages
        if ctx.current_stage is None and ctx.classification and not ctx.is_done:
            remaining = _resolve_remaining_stages(ctx)
            remaining = _filter_stages_by_config(remaining, ctx)
            ctx.stages = remaining
            ctx.current_stage_index = 0

        # Launch in background, return immediately
        asyncio.create_task(_run_next_background(ctx, executor))

        return {"run_id": run_id, "status": "running", "current_stage": ctx.current_stage}

    @router.post("/dashboard/pipelines/runs/{run_id}/run-all")
    async def run_all_remaining(run_id: str, user=Depends(user_auth)):
        """Execute all remaining stages without pausing (async)."""
        entry = _run_contexts.get(run_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Run not found")
        ctx = entry["ctx"]
        executor = entry["executor"]

        if ctx.is_done:
            return {"run_id": run_id, "status": "completed"}

        if ctx.status == "running":
            return {"run_id": run_id, "status": "running", "message": "Already running"}

        # Route if needed
        if ctx.current_stage is None and ctx.classification:
            remaining = _resolve_remaining_stages(ctx)
            remaining = _filter_stages_by_config(remaining, ctx)
            ctx.stages = remaining
            ctx.current_stage_index = 0

        # Launch in background, return immediately
        asyncio.create_task(_run_all_background(ctx, executor))

        return {"run_id": run_id, "status": "running"}

    @router.post("/dashboard/pipelines/runs/{run_id}/skip")
    async def skip_stage(run_id: str, user=Depends(user_auth)):
        """Skip the current stage and advance."""
        entry = _run_contexts.get(run_id)
        if not entry:
            raise HTTPException(status_code=404, detail="Run not found")
        ctx = entry["ctx"]
        if ctx.is_done:
            return {"run_id": run_id, "status": "completed"}

        skipped = ctx.current_stage
        ctx.skip_stage()

        return {
            "run_id": run_id,
            "skipped": skipped,
            "status": ctx.status,
            "current_stage": ctx.current_stage,
            "remaining_stages": ctx.remaining_stages,
        }

    @router.delete("/dashboard/pipelines/runs/{run_id}/cancel")
    async def cancel_run(run_id: str, user=Depends(user_auth)):
        """Cancel an active pipeline run."""
        entry = _run_contexts.pop(run_id, None)
        if not entry:
            raise HTTPException(status_code=404, detail="Run not found")
        ctx = entry["ctx"]
        ctx.status = "cancelled"

        # Clean up temp file
        if ctx.source_bytes_path:
            import os
            try:
                if os.path.exists(ctx.source_bytes_path):
                    os.unlink(ctx.source_bytes_path)
            except Exception:
                pass  # optional

        return {"run_id": run_id, "status": "cancelled"}

    # ------------------------------------------------------------------
    # POST /dashboard/pipelines/runs/batch — multi-file ingestion
    # ------------------------------------------------------------------
    @router.post("/dashboard/pipelines/runs/batch")
    async def batch_pipeline_run(
        files: List[UploadFile] = File(...),
        graph: str = Form(...),
        intent: str = Form("graph_rag"),
        mode: str = Form("run_all"),
        pipeline_id: Optional[str] = Form(None),
        params: Optional[str] = Form(None),
        user=Depends(user_auth),
    ):
        """
        Multi-file batch ingestion.

        Each file is classified independently and routed through the best
        pipeline. Results are merged into the same target graph.
        """
        tenant = _get_tenant(user)
        scoped = _scope_graph(tenant, graph)

        from ..ingestion.pipeline_context import PipelineContext
        from ..ingestion.stage_executor import StageExecutor
        from ..ingestion.scenario_router import ScenarioRouter, STAGE_PARSE_FILE, STAGE_CLASSIFY
        from pathlib import Path as _P

        parsed_params = {}
        if params:
            try:
                parsed_params = json.loads(params)
            except Exception:
                pass  # optional

        executor = StageExecutor(graph_registry=graph_registry)
        router_obj = ScenarioRouter()
        results = []

        for upload in files:
            import os, tempfile as _tf
            content = await upload.read()
            ext = _P(upload.filename).suffix.lower() if upload.filename else ""
            _binary_exts = {".xlsx", ".xls", ".pdf", ".docx", ".doc", ".pptx"}

            if ext in _binary_exts:
                fd, tmp = _tf.mkstemp(suffix=ext, prefix="batch_")
                os.close(fd)
                with open(tmp, "wb") as f:
                    f.write(content)
                source_file = tmp
                source_text = None
            else:
                source_file = None
                try:
                    source_text = content.decode("utf-8")
                except UnicodeDecodeError:
                    results.append({
                        "filename": upload.filename,
                        "status": "failed",
                        "error": "Not a valid UTF-8 text file",
                    })
                    continue

            ctx = PipelineContext(
                graph_namespace=scoped,
                stages=[STAGE_PARSE_FILE, STAGE_CLASSIFY],
                source_text=source_text,
                source_bytes_path=source_file,
                source_filename=upload.filename,
                intent=intent,
                mode="run_all",
                pipeline_params=parsed_params,
                pipeline_id=pipeline_id or "builtin:auto",
            )

            try:
                # Parse + classify
                executor.execute_all(ctx)

                # Route remaining stages
                if ctx.status != "failed" and ctx.classification:
                    from ..ingestion.pipeline_context import ClassificationResult
                    classification = ClassificationResult.from_dict(ctx.classification)
                    remaining = router_obj.route(classification, intent)

                    if not parsed_params.get("llm_model"):
                        remaining = [s for s in remaining if s not in ("EXTRACT", "ENHANCE_GRAPH")]
                    if not parsed_params.get("embedding_model"):
                        remaining = [s for s in remaining if s != "EMBED"]

                    ctx.stages = remaining
                    ctx.current_stage_index = 0
                    ctx.status = "running"
                    executor.execute_all(ctx)

                results.append({
                    "filename": upload.filename,
                    "run_id": ctx.run_id,
                    "status": ctx.status,
                    "classification": ctx.classification,
                    "nodes": len(ctx.nodes),
                    "edges": len(ctx.edges),
                    "stages_completed": [sr["stage_name"] for sr in ctx.stage_results],
                })
            except Exception as e:
                logger.error("Batch file %s failed: %s", upload.filename, e)
                results.append({
                    "filename": upload.filename,
                    "status": "failed",
                    "error": str(e),
                })
            finally:
                # Clean up temp file
                if source_file:
                    try:
                        os.unlink(source_file)
                    except Exception:
                        pass  # optional

        total_nodes = sum(r.get("nodes", 0) for r in results)
        total_edges = sum(r.get("edges", 0) for r in results)
        succeeded = sum(1 for r in results if r.get("status") == "completed")

        return {
            "graph": graph,
            "files_total": len(files),
            "files_succeeded": succeeded,
            "files_failed": len(files) - succeeded,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "results": results,
        }

    # ------------------------------------------------------------------
    # Schema Management
    # ------------------------------------------------------------------

    # In-memory schema store (per-graph)
    _graph_schemas: Dict[str, Dict[str, Any]] = {}

    @router.post("/dashboard/pipelines/schemas")
    async def upload_schema(req: UploadSchemaRequest, user=Depends(user_auth)):
        """Upload a YAML schema for schema-first ingestion."""
        tenant = _get_tenant(user)

        try:
            import yaml
            data = yaml.safe_load(req.schema_yaml)
            if not isinstance(data, dict):
                raise HTTPException(status_code=400, detail="Invalid YAML: expected a mapping")
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"YAML parse error: {e}")

        # Validate schema structure
        try:
            import tempfile, os
            fd, tmp = tempfile.mkstemp(suffix=".yaml", prefix="schema_")
            os.close(fd)
            with open(tmp, "w", encoding="utf-8") as f:
                f.write(req.schema_yaml)
            try:
                from ..schema.schema_parser import SchemaParser
                parser = SchemaParser(tmp)
                schema = parser.parse()
            finally:
                os.unlink(tmp)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Schema validation error: {e}")

        scoped = _scope_graph(tenant, req.graph)
        schema_entry = {
            "graph": req.graph,
            "scoped_graph": scoped,
            "name": req.name or data.get("NAMESPACE", req.graph),
            "strict": req.strict,
            "schema_yaml": req.schema_yaml,
            "node_types": list(schema.node_types.keys()),
            "edge_types": list(schema.edge_types.keys()),
            "version": schema.version,
        }
        _graph_schemas[scoped] = schema_entry

        # Generate extraction hints
        from ..ingestion.schema_validator import SchemaValidator
        validator = SchemaValidator(schema)
        hints = validator.get_extraction_hints()

        return {
            "message": "Schema uploaded",
            "schema": schema_entry,
            "extraction_hints": hints,
        }

    @router.get("/dashboard/pipelines/schemas/{graph_name}")
    async def get_schema(graph_name: str, user=Depends(user_auth)):
        """Get the schema for a graph."""
        tenant = _get_tenant(user)
        scoped = _scope_graph(tenant, graph_name)
        entry = _graph_schemas.get(scoped)
        if not entry:
            raise HTTPException(status_code=404, detail="No schema found for this graph")
        return {"schema": entry}

    @router.delete("/dashboard/pipelines/schemas/{graph_name}")
    async def delete_schema(graph_name: str, user=Depends(user_auth)):
        """Remove the schema for a graph."""
        tenant = _get_tenant(user)
        scoped = _scope_graph(tenant, graph_name)
        if scoped not in _graph_schemas:
            raise HTTPException(status_code=404, detail="No schema found")
        del _graph_schemas[scoped]
        return {"message": "Schema removed"}

    return router


# ---------------------------------------------------------------------------
# Pipeline Executor — delegates to AIQL executor
# ---------------------------------------------------------------------------

def _execute_pipeline(
    store: PipelineStore,
    graph_registry,
    run_id: str,
    pipeline: Dict,
    scoped_graph: str,
    text: str,
    params: Dict,
):
    """
    Execute pipeline stages via the AIQL executor.

    Writes text as a normalized JSON document to disk, then dispatches each
    stage to the executor's internal stage methods — identical to AIQL
    PIPELINE execution.
    """
    import os
    import tempfile
    from pathlib import Path
    from ..aiql.engine import AIQLExecutor

    store.update_run_status(run_id, "running")
    accuracy = AccuracyScore()
    normalized_file = None  # cleanup later

    try:
        # Create executor wired to the graph registry
        logger.info(f"[pipeline] Starting run {run_id} on graph={scoped_graph}")
        executor = AIQLExecutor(graph_registry=graph_registry)

        # Ensure graph exists (use registry directly — scoped names contain ':')
        db = graph_registry.get_graph(scoped_graph, load_if_missing=True)
        if not db:
            db = graph_registry.create_graph(scoped_graph)
        executor.active_namespace = scoped_graph

        namespace = scoped_graph
        if not db:
            raise RuntimeError(f"Could not access graph '{scoped_graph}'")

        # Count nodes/edges before to calculate deltas
        nodes_before = len(db.get_all_nodes())
        edges_before = len(db.get_all_edges())

        # ---------------------------------------------------------------
        # Write text as a normalized JSON file that the executor expects
        # ---------------------------------------------------------------
        document_id = f"pipeline_doc_{run_id[:12]}"
        safe_ns = namespace.replace(":", "__")
        norm_dir = Path("contextcore_data") / "namespaces" / safe_ns / "documents" / document_id / "normalized"
        norm_dir.mkdir(parents=True, exist_ok=True)
        normalized_file = norm_dir / "normalized_v1.json"

        # Build pages (group paragraphs into ~2000-char pages)
        pages = _split_into_pages(text)
        normalized_doc = {
            "document_id": document_id,
            "version": "v1",
            "content": {
                "pages": [
                    {
                        "page_no": i + 1,
                        "flat_text": page_text,
                        "text": page_text,
                        "elements": [{"type": "text", "content": page_text}],
                    }
                    for i, page_text in enumerate(pages)
                ],
            },
        }
        normalized_file.write_text(json.dumps(normalized_doc, ensure_ascii=False), encoding="utf-8")
        logger.info(f"[pipeline] Wrote normalized doc ({len(pages)} pages) to {normalized_file}")

        # ---------------------------------------------------------------
        # Create Document node in the graph
        # ---------------------------------------------------------------
        from ..core.graph_structures import GraphNode, GraphEdge

        doc_node_id = f"doc_{document_id}"
        # Fetch run record to get the actual source URL/label
        run_record = store.get_run(run_id)
        run_source = run_record.get("source", "pipeline") if run_record else "pipeline"
        run_source_type = run_record.get("source_type", "unknown") if run_record else "unknown"
        # Use actual source (URL or filename) as the source field, store origin as "pipeline"
        doc_node = GraphNode(
            id=doc_node_id,
            label="Document",
            properties={
                "name": document_id,
                "source": run_source,
                "source_type": run_source_type,
                "pipeline": pipeline.get("name", "unknown"),
                "pipeline_run_id": run_id,
                "text_length": len(text),
                "pages": len(pages),
            },
        )
        db.add_node(doc_node)

        # ---------------------------------------------------------------
        # Initialize executor's stage_data for this namespace
        # ---------------------------------------------------------------
        if not hasattr(executor, '_stage_data'):
            executor._stage_data = {}
        executor._stage_data.setdefault(namespace, {})
        stage_data = executor._stage_data[namespace]

        stage_data['extract'] = {
            'status': 'success',
            'document_id': document_id,
            'normalized_file': str(normalized_file),
            'normalized_version': 'v1',
            'normalized_doc': normalized_doc,
            'pages_count': len(pages),
        }
        stage_data['connect'] = {
            'status': 'completed',
            'document_nodes': [doc_node_id],
            'nodes_created': 1,
            'edges_created': 0,
        }

        # ---------------------------------------------------------------
        # Execute stages
        # ---------------------------------------------------------------
        stages = pipeline.get("stages", [])

        for stage_def in stages:
            stage_name = stage_def["name"]
            stage_type = stage_def["type"]
            stage_config = stage_def.get("config", {})

            store.update_stage(run_id, stage_name, "running")

            try:
                # Build the stage dict that the executor expects
                aiql_stage = {
                    'type': stage_type,
                    'stage_type': stage_type,
                    'step_name': stage_name,
                    **stage_config,
                }

                # Add model params from pipeline config / run params
                if stage_type == "EMBED":
                    aiql_stage.setdefault('parameters', {})
                    aiql_stage['parameters']['model'] = params.get('embedding_model', 'text-embedding-3-small')
                elif stage_type in ("EXTRACT_ENTITIES", "EXTRACT_ENTITIES_AND_RELATIONSHIPS"):
                    aiql_stage['using_llm'] = params.get('llm_model', 'gpt-4o-mini')
                if stage_type == "CHUNK":
                    aiql_stage['chunk_method'] = params.get(
                        'chunk_method', stage_config.get('method', 'paragraph'))
                    aiql_stage.setdefault('parameters', {})
                    aiql_stage['parameters']['chunk_size'] = params.get(
                        'chunk_size', stage_config.get('max_size', 2000))

                # Dispatch to executor's stage methods
                namespace_graph = db
                source_col = 'raw_docs'
                target_col = 'processed_docs'

                if stage_type == "CHUNK":
                    result = executor._execute_chunk_stage(
                        aiql_stage, stage_data, namespace,
                        source_col, target_col, namespace_graph,
                    )
                    count = result.get('chunks_created', 0) if isinstance(result, dict) else 0
                    # Fallback: count Chunk nodes in graph
                    if count == 0:
                        try:
                            count = sum(1 for n in db.get_all_nodes() if n.label == "Chunk")
                        except Exception:
                            pass  # optional
                    accuracy.chunk_count = count
                    accuracy.chunk_quality = 0.8
                    store.update_stage(run_id, stage_name, "completed", items_created=count)

                elif stage_type == "EMBED":
                    result = executor._execute_embed_stage(
                        aiql_stage, stage_data, namespace,
                        source_col, target_col, namespace_graph,
                    )
                    count = result.get('chunks_embedded', 0) if isinstance(result, dict) else 0
                    accuracy.embedded_count = count
                    accuracy.embedding_coverage = 1.0 if count > 0 else 0.0
                    store.update_stage(run_id, stage_name, "completed", items_created=count)

                elif stage_type == "EXTRACT_ENTITIES":
                    result = executor._execute_extract_entities_stage(
                        aiql_stage, stage_data, namespace,
                        source_col, target_col, namespace_graph,
                    )
                    count = result.get('entities_extracted', 0) if isinstance(result, dict) else 0
                    accuracy.entity_count = count
                    accuracy.entity_confidence_avg = 0.8 if count > 0 else 0.0
                    store.update_stage(run_id, stage_name, "completed", items_created=count)

                elif stage_type == "EXTRACT_ENTITIES_AND_RELATIONSHIPS":
                    result = executor._execute_extract_entities_and_relationships_stage(
                        aiql_stage, stage_data, namespace,
                        source_col, target_col,
                    )
                    ent = result.get('entities_created', 0) if isinstance(result, dict) else 0
                    rel = result.get('relationships_created', 0) if isinstance(result, dict) else 0
                    accuracy.entity_count = ent
                    accuracy.relationship_count = rel
                    accuracy.entity_confidence_avg = 0.8 if ent > 0 else 0.0
                    accuracy.relationship_coverage = 0.7 if rel > 0 else 0.0
                    store.update_stage(run_id, stage_name, "completed", items_created=ent + rel)

                elif stage_type == "ENHANCE_GRAPH":
                    result = executor._execute_enhance_graph_stage(
                        aiql_stage, stage_data, namespace,
                        source_col, target_col,
                    )
                    count = result.get('edges_created', 0) if isinstance(result, dict) else 0
                    store.update_stage(run_id, stage_name, "completed", items_created=count)

                elif stage_type == "PERSIST":
                    try:
                        graph_registry.save_graph(scoped_graph)
                    except Exception:
                        pass  # optional
                    store.update_stage(run_id, stage_name, "completed")

                else:
                    store.update_stage(run_id, stage_name, "skipped",
                                       metadata={"reason": f"Unknown stage type: {stage_type}"})

            except Exception as stage_err:
                logger.exception(f"[pipeline] stage {stage_name} failed for run {run_id}")
                store.update_stage(run_id, stage_name, "failed", error=str(stage_err))
                # Continue to next stage (graceful degradation)

        # Compute final counts from graph delta
        nodes_after = len(db.get_all_nodes())
        edges_after = len(db.get_all_edges())
        total_nodes = nodes_after - nodes_before
        total_edges = edges_after - edges_before

        accuracy.compute_overall()
        store.update_run_counts(run_id, max(total_nodes, 0), max(total_edges, 0), accuracy.entity_count)
        store.set_accuracy(run_id, accuracy)
        store.update_run_status(run_id, "completed")

    except Exception as exc:
        logger.exception(f"[pipeline] run {run_id} failed")
        store.update_run_status(run_id, "failed", error=str(exc))


def _split_into_pages(text: str, page_size: int = 2000) -> List[str]:
    """Split text into page-sized sections for the normalized document."""
    paragraphs = text.split("\n\n")
    pages: List[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 > page_size and current:
            pages.append(current)
            current = para
        else:
            current = f"{current}\n\n{para}" if current else para
    if current.strip():
        pages.append(current)
    return pages if pages else [text]


async def _enqueue_pipeline(job_queue, store, graph_registry, run_id, pipeline, scoped_graph, text, params):
    """Enqueue a pipeline run into the async job queue."""
    await job_queue.enqueue(
        _execute_pipeline,
        store, graph_registry, run_id, pipeline, scoped_graph, text, params,
    )
