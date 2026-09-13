"""
Experiment Router — multi-agent simulation experiments.

Provides CRUD for experiments and execution of multi-agent workflows.
Each experiment has:
  - A name and description
  - A target graph/context
  - Multiple agents (each with name, role, framework, prompt, API key)
  - Execution order (sequential or dependency-based)
  - Persisted results for replay and comparison

Endpoints:
  POST   /experiments              — Create experiment
  GET    /experiments              — List experiments
  GET    /experiments/{id}         — Get experiment details
  PUT    /experiments/{id}         — Update experiment
  DELETE /experiments/{id}         — Delete experiment
  POST   /experiments/{id}/agents  — Add agent to experiment
  DELETE /experiments/{id}/agents/{agent_id} — Remove agent
  POST   /experiments/{id}/run     — Execute experiment
  GET    /experiments/{id}/runs    — List past runs
  GET    /experiments/{id}/runs/{run_id} — Get run details
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Body, Depends, HTTPException, Query

logger = logging.getLogger(__name__)


def create_experiment_router(
    graph_registry,
    agent_registry,
    user_auth,
    require_member,
    session_manager=None,
):
    router = APIRouter(prefix="/experiments", tags=["experiments"])

    # In-memory store + Redis persistence
    _experiments: Dict[str, Dict] = {}
    _runs: Dict[str, Dict] = {}

    # Run control signals: run_id → {"signal": "running"|"pause"|"stop"}
    _run_controls: Dict[str, Dict] = {}
    import threading
    _run_threads: Dict[str, threading.Thread] = {}

    # SSE step callbacks: run_id → callable(event_type, data)
    _run_step_callbacks: Dict[str, Callable] = {}

    # Cache Redis client at router creation (not per-call)
    _redis_client = None
    try:
        import os as _os
        _redis_url = _os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if _redis_url:
            import redis as _redis_mod
            _redis_client = _redis_mod.from_url(_redis_url, decode_responses=True)
            _redis_client.ping()
            logger.info("[EXPERIMENTS] Redis connected for persistence")
    except Exception as _e:
        logger.warning("[EXPERIMENTS] Redis not available: %s", _e)
        _redis_client = None

    def _get_redis():
        return _redis_client

    def _save_experiment(exp: Dict):
        """Persist experiment to Redis AND in-memory."""
        _experiments[exp["id"]] = exp
        r = _get_redis()
        if r:
            try:
                data = json.dumps(exp, default=str)
                r.hset("experiments", exp["id"], data)
            except Exception as e:
                logger.error("[EXPERIMENTS] Redis save failed for %s: %s", exp["id"], e)

    def _load_experiments():
        """Sync experiments from Redis into in-memory dict."""
        r = _get_redis()
        if r:
            try:
                raw = r.hgetall("experiments")
                for eid, data in raw.items():
                    _experiments[eid] = json.loads(data)
                if raw:
                    logger.debug("[EXPERIMENTS] Loaded %d experiments from Redis", len(raw))
            except Exception as e:
                logger.warning("[EXPERIMENTS] Failed to load from Redis: %s", e)

    def _save_run(run: Dict):
        _runs[run["run_id"]] = run
        r = _get_redis()
        if r:
            try:
                r.hset(f"experiment_runs:{run['experiment_id']}", run["run_id"], json.dumps(run, default=str))
            except Exception:
                pass

    # Load on import
    _load_experiments()

    # ------------------------------------------------------------------
    # CRUD
    # ------------------------------------------------------------------

    @router.post("", status_code=201)
    async def create_experiment(req: dict = Body(...), user=Depends(user_auth)):
        exp_id = str(uuid.uuid4())[:12]
        session_id = req.get("session_id", "")
        context_id = req.get("context_id", "")

        exp = {
            "id": exp_id,
            "name": req.get("name", "Untitled Experiment"),
            "description": req.get("description", ""),
            "goal": req.get("goal", ""),  # what agents work toward
            "session_id": session_id,  # shared runtime — all agents operate here
            "context_id": context_id,  # target context/graph
            "agents": [],
            "execution_mode": req.get("execution_mode", "queue"),
            "created_at": datetime.now(timezone.utc).isoformat(),
            "created_by": getattr(user, "user_id", ""),
            "run_count": 0,
        }
        _save_experiment(exp)
        return exp

    @router.get("")
    async def list_experiments(user=Depends(user_auth)):
        _load_experiments()  # sync from Redis
        exps = sorted(_experiments.values(), key=lambda e: e.get("created_at", ""), reverse=True)
        return {"experiments": exps, "total": len(exps)}

    @router.get("/{experiment_id}")
    async def get_experiment(experiment_id: str, user=Depends(user_auth)):
        # Try in-memory first, then Redis
        exp = _experiments.get(experiment_id)
        if not exp:
            r = _get_redis()
            if r:
                try:
                    data = r.hget("experiments", experiment_id)
                    if data:
                        exp = json.loads(data)
                        _experiments[experiment_id] = exp
                except Exception:
                    pass
        if not exp:
            raise HTTPException(404, "Experiment not found.")
        return exp

    @router.put("/{experiment_id}")
    async def update_experiment(experiment_id: str, req: dict = Body(...), user=Depends(require_member)):
        exp = _experiments.get(experiment_id)
        if not exp:
            raise HTTPException(404, "Experiment not found.")
        for key in ("name", "description", "goal", "execution_mode", "session_id", "context_id"):
            if key in req:
                exp[key] = req[key]
        _save_experiment(exp)
        return exp

    @router.delete("/{experiment_id}")
    async def delete_experiment(experiment_id: str, user=Depends(require_member)):
        if experiment_id not in _experiments:
            raise HTTPException(404, "Experiment not found.")

        # Clean up sandbox graphs and runs for this experiment
        cleaned_sandboxes = 0
        cleaned_runs = 0
        run_ids_to_delete = []
        for rid, run in list(_runs.items()):
            if run.get("experiment_id") == experiment_id:
                run_ids_to_delete.append(rid)
                # Delete sandbox graph if it exists
                sandbox_ns = run.get("sandbox_namespace")
                if sandbox_ns and graph_registry:
                    try:
                        graph_registry.delete_graph(sandbox_ns)
                        cleaned_sandboxes += 1
                    except Exception:
                        pass

        for rid in run_ids_to_delete:
            _runs.pop(rid, None)
            _run_controls.pop(rid, None)
            cleaned_runs += 1

        # Delete experiment from memory + Redis
        del _experiments[experiment_id]
        r = _get_redis()
        if r:
            try:
                r.hdel("experiments", experiment_id)
                # Also clean up run records from Redis
                for rid in run_ids_to_delete:
                    r.hdel(f"experiment_runs:{experiment_id}", rid)
            except Exception:
                pass

        logger.info("[EXPERIMENT] Deleted experiment %s (cleaned %d runs, %d sandboxes)",
                    experiment_id, cleaned_runs, cleaned_sandboxes)
        return {"deleted": True, "cleaned_runs": cleaned_runs, "cleaned_sandboxes": cleaned_sandboxes}

    # ------------------------------------------------------------------
    # Agent management
    # ------------------------------------------------------------------

    @router.post("/{experiment_id}/agents")
    async def add_agent_to_experiment(experiment_id: str, req: dict = Body(...), user=Depends(require_member)):
        """Add an agent to the experiment.

        Two paths:
        1. Existing agent (from dropdown): provide agent_id → looks up in registry,
           refreshes API key automatically
        2. New agent: provide name + role (no agent_id) → registers fresh agent

        Body: {
            agent_id: "" (select from dropdown — auto-fills everything),
            name: "Researcher" (used for new agents or display override),
            role: "researcher",
            framework: "langgraph",
            prompt: "Search the graph for...",
            api_key: "" (optional — paste if you have it, otherwise auto-generated),
            depends_on: [] (optional — agent names this one waits for)
        }
        """
        exp = _experiments.get(experiment_id)
        if not exp:
            _load_experiments()
            exp = _experiments.get(experiment_id)
        if not exp:
            raise HTTPException(404, "Experiment not found.")

        agent_id_input = req.get("agent_id", "")
        name = req.get("name", "Agent")
        role = req.get("role", "agent")
        framework = req.get("framework", "")
        prompt = req.get("prompt", "")
        provided_key = req.get("api_key", "")
        session_id = exp.get("session_id", "")

        # Prevent duplicate agents — if same agent_id exists, update it in place
        existing_agents = exp.get("agents", [])
        duplicate_idx = None
        if agent_id_input:
            for i, a in enumerate(existing_agents):
                if a.get("agent_id") == agent_id_input:
                    duplicate_idx = i
                    break
        elif name:
            for i, a in enumerate(existing_agents):
                if a.get("name") == name:
                    duplicate_idx = i
                    break

        if agent_id_input:
            # Path 1: Existing agent selected from dropdown
            existing = agent_registry.get(agent_id_input)
            if not existing:
                raise HTTPException(404, f"Agent '{agent_id_input}' not found in registry.")

            agent_id = existing.agent_id
            name = name or existing.name
            role = role or getattr(existing, "role", "agent")

            if provided_key:
                api_key = provided_key
            else:
                # Refresh key so we can return it
                api_key = agent_registry.refresh_key(agent_id)
                if not api_key:
                    raise HTTPException(500, "Failed to generate API key for agent.")
            full_key = f"{agent_id}:{api_key}"
        else:
            # Path 2: Register new agent
            try:
                agent_identity, api_key = agent_registry.register(
                    name=f"sim_{name}_{experiment_id[:6]}",
                    role=role,
                    platform="app",
                    metadata={"experiment_id": experiment_id, "framework": framework},
                )
                agent_id = agent_identity.agent_id
                full_key = f"{agent_id}:{api_key}"
            except Exception as e:
                raise HTTPException(500, f"Failed to register agent: {e}")

        agent_entry = {
            "agent_id": agent_id,
            "name": name,
            "role": role,
            "framework": framework,
            "prompt": prompt,
            "llm_model": req.get("llm_model", ""),  # per-agent LLM override
            "api_key": api_key,
            "full_key": full_key,
            "session_id": session_id,
            "depends_on": req.get("depends_on", []),
            "added_at": datetime.now(timezone.utc).isoformat(),
        }
        if duplicate_idx is not None:
            # Update existing agent in place (keeps position, updates config)
            exp["agents"][duplicate_idx] = agent_entry
            action = "updated"
        else:
            exp["agents"].append(agent_entry)
            action = "added"
        _save_experiment(exp)

        return {
            "agent_id": agent_id,
            "name": name,
            "api_key": api_key,
            "full_key": full_key,
            "message": f"Agent '{name}' {action} in experiment.",
        }

    @router.delete("/{experiment_id}/agents/{agent_id:path}")
    async def remove_agent_from_experiment(experiment_id: str, agent_id: str, user=Depends(require_member)):
        exp = _experiments.get(experiment_id)
        if not exp:
            raise HTTPException(404, "Experiment not found.")
        before = len(exp.get("agents", []))
        # Match by agent_id OR by name (frontend may send either)
        exp["agents"] = [
            a for a in exp.get("agents", [])
            if a.get("agent_id") != agent_id and a.get("name") != agent_id
        ]
        after = len(exp["agents"])
        if before == after:
            raise HTTPException(404, f"Agent '{agent_id}' not found in experiment.")
        _save_experiment(exp)
        return {"removed": True, "agents_remaining": after}

    # ------------------------------------------------------------------
    # Run experiment
    # ------------------------------------------------------------------

    # ------------------------------------------------------------------
    # Core execution engine (runs in background thread for async mode)
    # ------------------------------------------------------------------

    def _check_signal(run_id: str) -> str:
        """Check control signal. Returns 'running', 'pause', or 'stop'."""
        ctrl = _run_controls.get(run_id, {})
        return ctrl.get("signal", "running")

    def _wait_if_paused(run_id: str, timeout: int = 300):
        """Block if paused. Returns False if stopped."""
        import time as _t
        waited = 0
        while _check_signal(run_id) == "pause":
            _t.sleep(1)
            waited += 1
            if waited >= timeout:
                return False  # auto-resume after timeout
        return _check_signal(run_id) != "stop"

    @router.post("/{experiment_id}/run")
    async def run_experiment(experiment_id: str, req: dict = Body(default={}), user=Depends(require_member)):
        """Execute the experiment.

        Body params:
          mode: "sandbox" (default) | "live"
          async_mode: true (default) | false — async returns immediately with run_id
          inputs: {} — runtime key-value inputs injected into agent prompts
          llm_model: "" — override LLM (e.g. "openai:gpt-4o")
          goal: "" — runtime goal override (replaces default agent prompts)
        """
        exp = _experiments.get(experiment_id)
        if not exp:
            # Try loading from Redis
            _load_experiments()
            exp = _experiments.get(experiment_id)
        if not exp:
            raise HTTPException(404, "Experiment not found.")

        agents = exp.get("agents", [])
        if not agents:
            # Agents might not have been loaded from Redis — retry
            _load_experiments()
            exp = _experiments.get(experiment_id, exp)
            agents = exp.get("agents", [])

        # Auto-pull agents from the runtime context (session) if none configured
        if not agents:
            exp_session_id = exp.get("session_id", "")
            if exp_session_id and session_manager:
                try:
                    _sess = session_manager.get_session(exp_session_id)
                    if _sess:
                        assigned = _sess.config.get("assigned_agents", [])
                        if assigned and agent_registry:
                            for aid in assigned:
                                agent_obj = agent_registry.get(aid)
                                if agent_obj:
                                    agents.append({
                                        "agent_id": aid,
                                        "name": getattr(agent_obj, "name", aid),
                                        "role": getattr(agent_obj, "platform", "analyst") or "analyst",
                                        "framework": "",
                                        "prompt": "",
                                        "llm_model": "",
                                    })
                            if agents:
                                exp["agents"] = agents
                                _save_experiment(exp)
                                logger.info("[EXPERIMENT] Auto-pulled %d agents from session %s",
                                            len(agents), exp_session_id[:16])
                except Exception as _e:
                    logger.debug("[EXPERIMENT] Auto-pull agents failed: %s", _e)

        if not agents:
            raise HTTPException(400, "No agents configured and none found in the runtime context.")

        exp_session = exp.get("session_id", "")
        if not exp_session:
            raise HTTPException(400, "No runtime context (session) attached.")

        session_obj = None
        exp_graph = None
        _sm = session_manager
        # Fallback: create session manager if not passed
        if not _sm:
            try:
                from ..context.session import ContextSessionManager
                _sm = ContextSessionManager(graph_registry=graph_registry)
            except Exception:
                pass
        if _sm:
            try:
                session_obj = _sm.get_session(exp_session)
                if session_obj:
                    exp_graph = session_obj.graph_namespace
            except Exception as e:
                logger.error("[EXPERIMENT] Session lookup failed: %s", e)
        if not session_obj:
            raise HTTPException(400, f"Session '{exp_session[:16]}' not found. session_manager={'available' if _sm else 'MISSING'}")
        if not exp_graph:
            raise HTTPException(400, f"Session '{exp_session[:16]}' has no graph namespace.")

        # Runtime graph = session graph (has merged data from all attached contexts)
        data_graph = exp_graph
        logger.info("[EXPERIMENT] Using runtime graph: %s (session: %s)", data_graph, exp_session)

        llm_model = req.get("llm_model", "")
        mode = req.get("mode", "sandbox")
        if mode not in ("sandbox", "live"):
            mode = "sandbox"
        async_mode = req.get("async_mode", True)
        inputs = req.get("inputs", {})  # runtime key-value inputs
        goal_override = req.get("goal", "")  # runtime goal override

        run_id = str(uuid.uuid4())[:12]
        track_id = f"exp_{experiment_id[:8]}_{run_id}"

        from .playground_conn import PlaygroundConn, create_tool_context
        from ..tools import ToolRegistry
        # Use the existing graph_registry (which already has session graphs loaded)
        # instead of creating a fresh one that can't see them
        reg = graph_registry

        # Sandbox clone is deferred to the async thread to avoid blocking the API response.
        # Just compute the namespace name here so we can return it immediately.
        sandbox_ns = None
        if mode == "sandbox":
            sandbox_ns = f"__playground__/exp_{experiment_id[:8]}_{run_id[:8]}"

        active_graph = sandbox_ns if mode == "sandbox" else data_graph

        run = {
            "run_id": run_id,
            "track_id": track_id,
            "type": "experiment",
            "mode": mode,
            "experiment_id": experiment_id,
            "experiment_name": exp.get("name", ""),
            "session_id": exp_session,
            "source_graph": data_graph,
            "sandbox_namespace": sandbox_ns,
            "inputs": inputs,
            "goal": goal_override,
            "started_at": datetime.now(timezone.utc).isoformat(),
            "agents": [],
            "status": "running",
        }
        _runs[run_id] = run
        _run_controls[run_id] = {"signal": "running"}
        _save_run(run)

        _first_graph = active_graph

        # Helper: safely write a node to the graph via AIQL
        def _graph_node(executor, label, props_dict):
            """Create a node in the graph. Props are auto-escaped."""
            try:
                props_dict["_playground_generated"] = "true"  # mark all experiment-generated nodes
                parts = []
                for k, v in props_dict.items():
                    sv = str(v).replace('"', "'").replace('\n', ' ')[:250]
                    parts.append(f'{k}: "{sv}"')
                aiql = f'CREATE NODE {label} {{{", ".join(parts)}}}'
                executor.execute(aiql)
            except Exception:
                pass

        # ── Execution function (can run sync or in thread) ─────────
        def _execute():
            try:
                _do_execute_run(
                    run, exp, agents, _first_graph, exp_session, experiment_id,
                    track_id, run_id, mode, reg, llm_model, inputs, goal_override,
                    PlaygroundConn, create_tool_context, ToolRegistry, _graph_node,
                )
            except Exception as _fatal:
                import traceback
                run["status"] = "failed"
                run["error"] = str(_fatal)
                run["error_detail"] = traceback.format_exc()[-500:]
                run["completed_at"] = datetime.now(timezone.utc).isoformat()
                _save_run(run)
                _run_controls.pop(run_id, None)
                _run_threads.pop(run_id, None)
                logger.error("[EXPERIMENT] Fatal error in run %s: %s", run_id, _fatal)

        if async_mode:
            t = threading.Thread(target=_execute, daemon=True, name=f"exp-{run_id}")
            _run_threads[run_id] = t
            t.start()
            # Return the full run object (with agents=[] initially) so frontend can render theater immediately
            return {**run, "async": True}
        else:
            _execute()
            return run

    @router.get("/{experiment_id}/runs/{run_id}/stream")
    async def stream_experiment_run(experiment_id: str, run_id: str):
        """Stream experiment steps via SSE. Attach after POST /{id}/run to receive real-time events."""
        import queue as _queue
        import asyncio as _asyncio
        from fastapi.responses import StreamingResponse

        step_queue = _queue.Queue()

        def on_step(event_type: str, data: dict):
            step_queue.put({"event": event_type, "data": data})

        _run_step_callbacks[run_id] = on_step

        # If run is already completed before stream attached, emit complete immediately
        existing_run = _runs.get(run_id)

        async def generate():
            try:
                # If the run finished before we connected, drain any queued events then close
                deadline_empty_ticks = 0
                while True:
                    await _asyncio.sleep(0.1)
                    flushed = False
                    while not step_queue.empty():
                        try:
                            item = step_queue.get_nowait()
                        except _queue.Empty:
                            break
                        event_type = item.get("event", "step")
                        data_str = json.dumps(item.get("data", {}), default=str)
                        yield f"event: {event_type}\ndata: {data_str}\n\n"
                        flushed = True
                        if event_type in ("complete", "run_complete"):
                            return

                    # Check if run is finished and queue is empty
                    current_run = _runs.get(run_id)
                    if current_run and current_run.get("status") in ("completed", "failed", "aborted"):
                        if not flushed:
                            deadline_empty_ticks += 1
                        else:
                            deadline_empty_ticks = 0
                        # Give 5 extra ticks (0.5s) to drain any in-flight events
                        if deadline_empty_ticks >= 5:
                            status = current_run.get("status", "completed")
                            agents_count = len(current_run.get("agents", []))
                            duration = current_run.get("total_duration_ms", 0)
                            data_str = json.dumps({"status": status, "agents": agents_count, "duration_ms": duration}, default=str)
                            yield f"event: complete\ndata: {data_str}\n\n"
                            return
            except _asyncio.CancelledError:
                pass
            except Exception:
                pass
            finally:
                _run_step_callbacks.pop(run_id, None)

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    def _do_execute_run(
        run, exp, agents, _first_graph, exp_session, experiment_id,
        track_id, run_id, mode, reg, llm_model, inputs, goal_override,
        PlaygroundConn, create_tool_context, ToolRegistry, _graph_node,
    ):
        """Core execution engine — runs agents sequentially."""
        total_start = time.time()
        experiment_start = total_start  # alias for SSE complete event
        completed_agents = set()

        # ── Progress helper (defined early — used by all phases) ──
        def _progress(msg):
            run["_progress"] = msg
            run["_progress_at"] = datetime.now(timezone.utc).isoformat()
            # Append to progress log so UI can show all steps
            if "_progress_log" not in run:
                run["_progress_log"] = []
            run["_progress_log"].append({
                "msg": msg,
                "time": datetime.now(timezone.utc).isoformat(),
                "elapsed_ms": int((time.time() - total_start) * 1000),
            })
            logger.info("[EXPERIMENT] %s — %s", track_id, msg)

        # ── Sandbox setup: verify source data, reuse or clone ──────────
        sandbox_ns = run.get("sandbox_namespace")
        if mode == "live":
            logger.warning("[EXPERIMENT] Running in LIVE mode — writes go directly to '%s'", _first_graph)
            _progress("⚠️ Live mode — writes affect production graph")
        if mode == "sandbox" and sandbox_ns:
            source = run.get("source_graph", _first_graph)
            run["_progress"] = "Checking source graph data..."

            # 1. Verify source graph actually has data
            _t_check = time.time()
            try:
                source_db = reg.get_graph(source, load_if_missing=True)
                if not source_db:
                    source_db = reg.get_graph_for_request(source)
                source_nodes = source_db.get_all_nodes() if source_db else []
                source_count = len(source_nodes)
            except Exception:
                source_count = 0

            if source_count == 0:
                run["status"] = "failed"
                run["error"] = f"Source graph '{source}' has no data. Ingest content first."
                run["completed_at"] = datetime.now(timezone.utc).isoformat()
                _save_run(run)
                return

            _progress(f"Source graph: {source_count} nodes ({(time.time()-_t_check)*1000:.0f}ms)")
            logger.info("[EXPERIMENT] Source graph '%s' has %d nodes (%.0fms)", source, source_count, (time.time()-_t_check)*1000)

            # 2. Check if sandbox already has data (reuse from previous clone)
            try:
                sandbox_db = reg.get_graph_for_request(sandbox_ns)
                existing_nodes = len(sandbox_db.get_all_nodes()) if sandbox_db else 0
            except Exception:
                existing_nodes = 0

            if existing_nodes > 0:
                # Sandbox already populated — skip clone
                logger.info("[EXPERIMENT] Sandbox '%s' already has %d nodes — skipping clone",
                            sandbox_ns, existing_nodes)
                run["_progress"] = f"Sandbox ready ({existing_nodes} nodes, clone skipped)"
            else:
                # 3. Try direct reference first: if source is Redis-backed,
                #    just point the sandbox at the source data (zero-copy)
                source_is_redis = getattr(source_db, '_using_redis_backend', False)
                if source_is_redis:
                    # Use source graph directly as sandbox — agents write to sandbox namespace
                    # but read from source. This avoids the expensive clone entirely.
                    run["_progress"] = f"Linking sandbox to source ({source_count} nodes)..."
                    _first_graph = source
                    run["sandbox_namespace"] = None  # no separate sandbox needed
                    sandbox_ns = None
                    logger.info("[EXPERIMENT] Using source graph directly (Redis, %d nodes) — no clone needed",
                                source_count)
                else:
                    # 4. Fallback: full clone (non-Redis source)
                    run["_progress"] = f"Cloning {source_count} nodes..."
                    # Auto-cleanup previous sandbox graphs
                    for prev_run in list(_runs.values()):
                        if (prev_run.get("experiment_id") == experiment_id
                            and prev_run.get("mode") == "sandbox"
                            and prev_run.get("sandbox_namespace")
                            and prev_run.get("sandbox_namespace") != sandbox_ns
                            and not prev_run.get("promoted")
                            and not prev_run.get("discarded")):
                            try:
                                reg.delete_graph(prev_run["sandbox_namespace"])
                                prev_run["discarded"] = True
                                _save_run(prev_run)
                            except Exception:
                                pass

                    from ..core.replication import GraphReplicator
                    replicator = GraphReplicator(graph_registry=reg)
                    clone_result = replicator.clone_graph(source, sandbox_ns)
                    if "error" in clone_result:
                        run["status"] = "failed"
                        run["error"] = f"Failed to create sandbox: {clone_result['error']}"
                        run["completed_at"] = datetime.now(timezone.utc).isoformat()
                        _save_run(run)
                        return
                    logger.info("[EXPERIMENT] Cloned: %s -> %s (%d nodes, %d edges)",
                                source, sandbox_ns, clone_result["cloned_nodes"], clone_result["cloned_edges"])

            # Update active graph
            _first_graph = sandbox_ns or source

            # Copy manifest from source to sandbox so agents see quality context
            if sandbox_ns and source:
                try:
                    from ..context.quality import get_manifest
                    import redis as _redis_mod
                    _r = _redis_mod.from_url(os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379"))
                    src_manifest = get_manifest(source, _r)
                    if src_manifest:
                        import json as _json
                        _r.set(f"manifest:{sandbox_ns}", _json.dumps(src_manifest), ex=3600)
                        logger.info("[EXPERIMENT] Copied manifest from %s to %s", source, sandbox_ns)
                        # Also copy result cache keys
                        for cache_key in _r.keys(f"results:{source}:*"):
                            ck = cache_key.decode() if isinstance(cache_key, bytes) else cache_key
                            new_key = ck.replace(f"results:{source}:", f"results:{sandbox_ns}:")
                            val = _r.get(cache_key)
                            if val:
                                _r.set(new_key, val, ex=3600)
                except Exception as e:
                    logger.debug("[EXPERIMENT] Manifest copy failed: %s", e)

        # Pre-warm search indexes (keyword + BM25) in background thread
        def _warm_indexes():
            try:
                _warm_start = time.time()
                from ..search.rag import _build_keyword_index, plain_search
                _warm_db = reg.get_graph(_first_graph, load_if_missing=True)
                if _warm_db:
                    _build_keyword_index(_warm_db, _first_graph)
                    # Also trigger BM25 build by doing a dummy search
                    plain_search(_warm_db, "warmup", k=1)
                    logger.info("[EXPERIMENT] Search indexes warmed in %dms", int((time.time() - _warm_start) * 1000))
            except Exception as e:
                logger.debug("[EXPERIMENT] Index warmup failed: %s", e)

        import threading
        _warm_thread = threading.Thread(target=_warm_indexes, daemon=True)
        _warm_thread.start()

        # Pre-init project so add_task works instantly (not 18s on first call)
        try:
            _init_conn = PlaygroundConn(_first_graph, reg)
            from .experiment_router import ProjectGraph as _PG2
        except ImportError:
            pass
        try:
            from ..project.graph import ProjectGraph as _PG2
            _pre_project = _PG2(_first_graph, connection=_init_conn)
            logger.info("[EXPERIMENT] Project pre-initialized for %s", _first_graph)
        except Exception as _pe:
            logger.debug("[EXPERIMENT] Project pre-init failed: %s", _pe)

        # Log experiment start in the graph
        try:
            if _first_graph:
                _conn = PlaygroundConn(_first_graph, reg)
                if _conn.executor:
                    _graph_node(_conn.executor, "ExperimentRun", {
                        "name": f"Experiment: {exp.get('name', '')}",
                        "track_id": track_id, "run_id": run_id,
                        "experiment_id": experiment_id, "type": "experiment",
                        "mode": mode, "agent_count": len(agents),
                        "status": "running",
                        "started_at": datetime.now(timezone.utc).isoformat(),
                        "inputs": json.dumps(inputs)[:200] if inputs else "",
                        "goal": goal_override[:200] if goal_override else "",
                        "playground": "true",
                        "_playground_generated": "true",
                    })
        except Exception as e:
            logger.debug("[EXPERIMENT] Failed to log ExperimentRun node: %s", e)

        # ── Initialize shared intelligence systems ──────────────────
        from ..context.propagation import ContextPropagator
        from ..context.gravity import ContextGravity
        from ..core.replay import get_replay_engine
        propagator = ContextPropagator(ttl_seconds=300)
        gravity = ContextGravity(decay_seconds=300)
        try:
            from ..core.checkpoint import checkpoint_manager as _cp_mgr
        except Exception:
            _cp_mgr = None
        replay_engine = get_replay_engine(_cp_mgr, reg)

        # Resolve LLM once for all agents (no test call — just check init)
        _progress("Resolving LLM provider...")
        from ..llm import get_llm_client
        import os as _os
        llm = None
        llm_provider_name = ""
        if llm_model:
            provider, _, model = llm_model.partition(":")
            llm = get_llm_client(provider=provider, model=model) if model else get_llm_client(provider=provider)
            llm_provider_name = provider
        else:
            # Check which providers have API keys configured (no network call)
            provider_checks = [
                ("groq", "GROQ_API_KEY"),       # fastest (free, ~0.5s/call)
                ("openai", "OPENAI_API_KEY"),     # reliable
                ("anthropic", "ANTHROPIC_API_KEY"),
                ("ollama", None),                 # local fallback
            ]
            for _prov, _env_key in provider_checks:
                if _env_key and not _os.environ.get(_env_key):
                    continue  # skip providers without keys
                try:
                    llm = get_llm_client(provider=_prov)
                    llm_provider_name = _prov
                    _progress(f"LLM provider: {_prov}")
                    break
                except Exception:
                    llm = None
        if not llm:
            run["status"] = "failed"
            run["error"] = "No LLM provider available. Set OPENAI_API_KEY, ANTHROPIC_API_KEY, or start Ollama."
            _save_run(run)
            return

        # Warm up the LLM — skip if all agents have per-agent overrides
        all_have_own_llm = all(a.get("llm_model", "") not in ("", "auto", "default") for a in agents)
        if not all_have_own_llm:
            _t_warmup = time.time()
            _progress(f"Warming up {llm_provider_name}:{llm.model}...")
            try:
                llm.generate("hi", max_tokens=1)
            except Exception:
                pass
            _progress(f"LLM ready: {llm_provider_name}:{llm.model} ({(time.time()-_t_warmup)*1000:.0f}ms)")
        else:
            _progress("Agents have per-agent LLM — skipping default warmup")

        # ── Goal decomposition helper ─────────────────────────────
        def _decompose_goal(goal_text, topic_context, project_graph):
            """Use LLM to break a goal into tasks with dependencies. Returns list of created tasks."""
            _progress("Decomposing goal into tasks...")
            prompt = f"""Break this goal into 3-5 concrete tasks. Return a JSON array.

GOAL: {goal_text}

AVAILABLE DATA IN THE GRAPH:
{topic_context[:600] if topic_context else '(no topic scan available)'}

Return ONLY a valid JSON array like this:
[
  {{"title": "first task title", "description": "what to search and analyze", "depends_on": []}},
  {{"title": "second task", "description": "what to do", "depends_on": [0]}},
  {{"title": "write final summary", "description": "combine all findings into a summary", "depends_on": [0, 1]}}
]

Rules:
- depends_on = array of task indexes (0-based) that must complete first
- First task(s) should have depends_on: [] (no dependencies)
- Last task should be a summary/synthesis that depends on all others
- Each task description should mention specific keywords to search for
- 3-5 tasks maximum"""

            try:
                parsed = llm.generate_json(prompt, system="You decompose goals into tasks. Reply with valid JSON array only.")
            except Exception:
                # Fallback: try raw generate and parse
                try:
                    raw = llm.generate(prompt, system="Reply with valid JSON array only.", max_tokens=500)
                    import re as _re
                    match = _re.search(r'\[[\s\S]*\]', raw)
                    parsed = json.loads(match.group()) if match else []
                except Exception:
                    parsed = []

            if not parsed or not isinstance(parsed, list):
                # Fallback: single task from the goal
                parsed = [{"title": goal_text[:60], "description": goal_text, "depends_on": []}]

            # Create tasks in the project graph
            created_tasks = []
            task_ids = []
            for i, t in enumerate(parsed):
                title = t.get("title", f"Task {i+1}")
                desc = t.get("description", "")
                deps = t.get("depends_on", [])
                dep_ids = [task_ids[d] for d in deps if d < len(task_ids)]

                task_id = project_graph.add_task(
                    title=title,
                    description=desc,
                    priority="medium",
                    depends_on=dep_ids or None,
                )
                task_ids.append(task_id)
                created_tasks.append({"title": title, "description": desc, "task_id": task_id})
                logger.info("[EXPERIMENT] Created task: %s (id: %s, deps: %s)", title[:40], task_id[:12], dep_ids)

            _progress(f"Created {len(created_tasks)} tasks from goal")
            return created_tasks

        # Scoped tool set — graph/search/context/task tools only
        # Tier 1: CORE — tools shown in LLM prompt
        CORE_TOOLS = {
            # Search & Discovery
            "search_nodes",     # find by label + query (LMDB, fast)
            "search",           # text search across all nodes
            "rag_query",        # semantic search with sources (Qdrant)
            "rag_graph",        # graph-aware semantic search
            # Graph Operations
            "add_knowledge",    # write Finding/Insight to graph
            "add_relationship", # connect two nodes
            "query_graph",      # AIQL queries for advanced users
            "graph_summary",    # overview of graph structure
            # Task Coordination
            "add_task",         # create and assign tasks
            "complete_task",    # mark task done
            "my_tasks",         # check assigned tasks
        }
        # Tier 2: EXTENDED — callable but not in prompt
        EXTENDED_TOOLS = {
            "add_decision", "use_graph", "topic_scan",
            "briefing", "orient", "get_context", "get_agent_context",
            "claim_task", "list_tasks", "get_unblocked_tasks",
            "handoff_task", "task_context",
            "extract_entities", "extract_facts", "log_action", "reason",
            "check_freshness", "detect_conflicts", "graph_diff", "graph_timeline",
            "report_tokens", "my_usage", "remember", "recall",
        }
        EXPERIMENT_TOOLS = CORE_TOOLS | EXTENDED_TOOLS
        _progress(f"Loading tools ({len(CORE_TOOLS)} core + {len(EXTENDED_TOOLS)} extended)...")
        tool_schemas = [
            t.to_json_schema() for t in ToolRegistry.all()
            if t.handler and t.name in EXPERIMENT_TOOLS
        ]
        tools_json = json.dumps([
            {"name": t["name"], "description": t.get("description", ""),
             "parameters": t.get("parameters", {})}
            for t in tool_schemas
        ], indent=1)

        agent_graph = _first_graph or "default"
        execution_mode = exp.get("execution_mode", "queue")

        # ── Helper: run a single agent with a given prompt + role ──────
        def _run_single_agent(agent_def, prompt, role, phase="execute"):
            """Execute one agent's LLM loop. Returns the agent_result dict."""
            agent_name = agent_def.get("name", "Agent")
            agent_id = agent_def.get("agent_id", f"sim_{agent_name}")

            # Per-agent LLM override — allows mixing expensive (GPT-4o for leader)
            # with cheap (GPT-4o-mini for workers) in the same experiment
            agent_llm = llm  # default to run-level LLM
            agent_llm_name = llm_provider_name
            agent_llm_model = agent_def.get("llm_model", "")
            if agent_llm_model and agent_llm_model not in ("auto", "default", ""):
                try:
                    _prov, _, _model = agent_llm_model.partition(":")
                    candidate = get_llm_client(provider=_prov, model=_model) if _model else get_llm_client(provider=_prov)
                    # Quick validation: ensure it's not Ollama when Ollama is down
                    if _prov == "ollama":
                        try:
                            import requests as _req
                            _req.get("http://localhost:11434/api/tags", timeout=2)
                        except Exception:
                            raise RuntimeError("Ollama is not running — falling back to run-level LLM")
                    agent_llm = candidate
                    agent_llm_name = agent_llm_model
                    _progress(f"Agent '{agent_name}' using LLM: {agent_llm_model}")
                except Exception as e:
                    logger.warning("[EXPERIMENT] Per-agent LLM '%s' failed for %s: %s — using run-level LLM (%s)",
                                  agent_llm_model, agent_name, e, llm_provider_name)
                    _progress(f"Agent '{agent_name}' LLM '{agent_llm_model}' unavailable, using {llm_provider_name}")

            conn = PlaygroundConn(agent_graph, reg)
            ctx = create_tool_context(conn, agent_id, agent_name)
            ctx.metadata = getattr(ctx, "metadata", {})
            ctx.metadata.update({
                "experiment_id": experiment_id,
                "track_id": track_id, "run_id": run_id, "type": "experiment",
            })

            # Auto-init project so task tools (add_task, claim_task, etc.) work
            if not hasattr(ctx, "project") or not ctx.project:
                try:
                    from ..project.graph import ProjectGraph
                    ctx.project = ProjectGraph(agent_graph, connection=conn)
                except Exception as e:
                    logger.warning("[EXPERIMENT] ProjectGraph init failed for %s: %s", agent_name, e)
                    # Retry with a simpler approach — just create a minimal project object
                    try:
                        from ..project.graph import ProjectGraph
                        # Ensure the conn has get_nodes before trying
                        ctx.project = ProjectGraph(agent_graph, connection=conn)
                    except Exception:
                        logger.error("[EXPERIMENT] ProjectGraph completely failed for %s", agent_name)

            agent_start = time.time()
            agent_result = {
                "name": agent_name, "agent_id": agent_id, "role": role,
                "framework": agent_def.get("framework", ""),
                "llm_model": agent_llm_name or llm_provider_name,
                "prompt": prompt, "track_id": track_id,
                "type": "experiment", "steps": [], "status": "running",
            }

            try:
                # ── Get live graph context for this agent ────────
                # Try manifest first (fast, <100ms, high quality)
                # Look up manifest from SOURCE graph, not sandbox (sandbox has no manifest)
                manifest_text = ""
                try:
                    from ..context.quality import get_manifest, ManifestBuilder
                    _db = conn.db if hasattr(conn, 'db') else None
                    _redis = getattr(_db, '_redis', None) if _db else None
                    # Try source graph first, then current namespace
                    _source = run.get("source_graph", "")
                    _ns = getattr(conn, 'namespace', '') or agent_graph
                    manifest = get_manifest(_source, _redis) if _source else None
                    if not manifest:
                        manifest = get_manifest(_ns, _redis)
                    if manifest:
                        manifest_text = ManifestBuilder.to_text(manifest)
                except Exception as e:
                    logger.debug("[EXPERIMENT] Manifest lookup failed: %s", e)

                graph_summary_text = ""
                if not manifest_text:
                    # Fallback to graph_summary if no manifest
                    try:
                        sr = ToolRegistry.dispatch("graph_summary", ctx, {})
                        if sr and isinstance(sr, str):
                            graph_summary_text = sr.strip()
                    except Exception as e:
                        logger.warning("[EXPERIMENT] Graph summary failed for %s: %s", agent_name, e)

                # Topic scan — shows what data topics exist (no LLM needed)
                topic_text = ""
                if not manifest_text:
                    # Manifest already includes themes — skip topic_scan
                    try:
                        from ..search.rag import topic_scan_text
                        _db = conn.db if hasattr(conn, 'db') else None
                        _ns = getattr(conn, 'namespace', '') or agent_graph
                        if _db:
                            topic_text = topic_scan_text(_db, _ns)
                    except Exception as e:
                        logger.debug("[EXPERIMENT] topic_scan failed: %s", e)

                # ── Build team roster ────────────────────────────
                team_info = []
                for i, a in enumerate(agents):
                    status = "completed" if a.get("name") in completed_agents else "waiting"
                    if a.get("name") == agent_name:
                        status = "YOU (running now)"
                    team_info.append(f"  - {a.get('name')} ({a.get('role', 'agent')}): {status}")
                team_text = "\n".join(team_info)

                # ── Query graph for experiment knowledge (from prior agents) ──
                def _get_experiment_context():
                    """Read Findings, Insights, AgentMessages from this experiment run."""
                    sections = []
                    try:
                        db = conn.db
                        if not db:
                            return ""
                        all_nodes = db.get_all_nodes()
                        findings = []
                        insights = []
                        messages = []
                        for n in all_nodes:
                            props = n.properties if hasattr(n, "properties") else {}
                            if props.get("track_id") != track_id:
                                continue
                            if props.get("agent") == agent_name:
                                continue  # skip own nodes
                            label = n.label if hasattr(n, "label") else ""
                            content = props.get("content", "")[:200]
                            source = props.get("agent", "unknown")
                            if label == "Finding" and content:
                                findings.append(f"  [{source}] {content}")
                            elif label == "Insight" and content:
                                insights.append(f"  [{source}] {content}")
                            # AgentMessage nodes no longer created — cross-agent updates come from propagation
                        if insights:
                            sections.append("INSIGHTS FROM OTHER AGENTS:\n" + "\n".join(insights[:8]))
                        if findings:
                            sections.append("FINDINGS FROM OTHER AGENTS:\n" + "\n".join(findings[:10]))
                        if messages:
                            sections.append("AGENT MESSAGES:\n" + "\n".join(messages[:8]))
                    except Exception as e:
                        logger.debug("[EXPERIMENT] Context collection failed: %s", e)
                    return "\n\n".join(sections)

                # ── Agent loop: reason → act → observe → repeat ──
                history = []
                _recent_actions = []  # track last few (action, params) to detect loops
                search_count = 0
                write_count = 0
                _progress(f"Agent '{agent_name}' ({role}) starting — graph={agent_graph}")

                max_steps = 8 if role in ("worker", "agent") else 10  # workers need fewer steps
                if phase == "synthesize":
                    max_steps = 6  # synthesize just reads findings and writes summary
                for step_num in range(max_steps):
                    # Check control signals between steps
                    sig = _check_signal(run_id)
                    if sig == "stop":
                        agent_result["steps"].append({"step": step_num + 1, "summary": "Stopped by user"})
                        agent_result["status"] = "aborted"
                        break
                    if sig == "pause":
                        if not _wait_if_paused(run_id):
                            agent_result["steps"].append({"step": step_num + 1, "summary": "Stopped while paused"})
                            agent_result["status"] = "aborted"
                            break

                    # Collect cross-agent updates (what did other agents do?)
                    pending_updates = propagator.get_pending(agent_id, agent_graph, max_updates=5)
                    updates_text = ""
                    if pending_updates:
                        updates_text = "\n\nUpdates from other agents:\n" + "\n".join(f"  - {u}" for u in pending_updates)

                    # Gravity-adjusted intent
                    intent = gravity.get_intent(agent_id) if hasattr(gravity, "get_intent") else ""
                    intent_text = f"\nYour focus areas: {intent}" if intent else ""

                    # Read experiment knowledge from graph (findings/insights by other agents)
                    exp_context = _get_experiment_context() if step_num == 0 or step_num % 3 == 0 else ""
                    # Only refresh every 3 steps to avoid overhead

                    # Build step history
                    history_text = ""
                    for h in history[-6:]:  # only keep last 6 steps to avoid prompt bloat
                        result_preview = str(h.get("result", ""))[:250]
                        history_text += f"\nStep {h['step']}: {h['tool']}({json.dumps(h['params'])[:80]})"
                        if h.get("success") is False:
                            history_text += f" ERROR: {h.get('error', '')[:80]}"
                        else:
                            history_text += f" OK: {result_preview}"
                        history_text += "\n"

                    # ── Build the step prompt ──
                    # PLATFORM POLICY: agents don't need to know tool names or mechanics.
                    # The agent thinks about the TASK. The platform translates intent to tools.
                    # Prompts are: context + task + what you've learned so far.
                    steps_left = max_steps - step_num

                    # Summarize what agent has learned so far (not raw tool calls)
                    learned_text = ""
                    for h in history[-4:]:
                        result_str = str(h.get("result", ""))[:200]
                        learned_text += f"\n- {result_str}\n"

                    # Save context for first step (so UI can show what agent sees)
                    if step_num == 0:
                        ctx_parts = [manifest_text or graph_summary_text, topic_text]
                        # Add team info
                        team = [a.get("name", "?") for a in agents]
                        if len(team) > 1:
                            ctx_parts.append(f"TEAM: {', '.join(team)} ({len(team)} agents)")
                        else:
                            ctx_parts.append("TEAM: You are the only agent.")
                        agent_result["context"] = "\n".join(p for p in ctx_parts if p).strip()

                    # Use manifest if available, otherwise fall back to graph_summary + topic_text
                    context_block = manifest_text if manifest_text else f"{graph_summary_text}\n{topic_text}"

                    step_prompt = f"""You are "{agent_name}" ({role}).

CONTEXT:
{context_block}
{updates_text}
{"" if not exp_context else exp_context}

TASK: {prompt}
{"" if not inputs else "INPUTS: " + "; ".join(f"{k}={v}" for k, v in inputs.items())}

{f'DATA COLLECTED SO FAR:{learned_text}' if learned_text else ''}

TOOLS:
- search(query="keywords") — find facts and data across all nodes
- search_nodes(label="Person") — find entities by type (also: Organization, Location, Event, Fact, Finding)
- search_nodes(label="Fact", query="topic") — find facts about a specific topic
- rag_query(question="...") — semantic search with source citations
- add_knowledge(content="...", node_type="Finding") — write analysis to graph
- add_task(title="...", description="...", assigned_to="AgentName") — assign work to others
- complete_task(task_id="...") — mark a task as done when you finish it
- my_tasks() — check tasks assigned to you

You have {steps_left} steps left.
{">>> You have searched enough. Finish now — say done with your findings in the summary. <<<" if search_count >= 3 else ""}
Only report facts found in search results — do NOT invent data.

Respond ONLY with JSON:
{{"reasoning": "your thinking", "action": "tool_name", "params": {{}}, "done": false}}
When done: {{"reasoning": "conclusion", "action": "", "params": {{}}, "done": true, "summary": "your key findings with specific data"}}"""

                    # Record context delivery for quality tracking
                    try:
                        from ..context.quality_tracker import get_quality_tracker
                        _qt = get_quality_tracker()
                        _qt.record_delivery(
                            agent_id, node_ids=[],
                            token_count=max(1, len(step_prompt) // 4),
                            node_labels=list(set(
                                t for t in (graph_summary_text or "").split("\n")
                                if ":" in t and not t.strip().startswith("Search")
                            ))[:10],
                        )
                    except Exception:
                        pass

                    _progress(f"Agent '{agent_name}' step {step_num + 1} — calling LLM...")

                    # Emit step-start event for SSE streaming
                    _step_start_ts = time.time()
                    if run_id in _run_step_callbacks:
                        try:
                            _run_step_callbacks[run_id]("step", {
                                "agent": agent_id, "agent_name": agent_name,
                                "step": step_num + 1, "action": "thinking",
                                "status": "running",
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception:
                            pass

                    try:
                        import concurrent.futures as _cf
                        with _cf.ThreadPoolExecutor(max_workers=1) as _tp:
                            future = _tp.submit(agent_llm.generate, prompt=step_prompt, max_tokens=500)
                            raw = future.result(timeout=60)  # 60s timeout per step
                    except _cf.TimeoutError:
                        logger.error("[EXPERIMENT] Agent '%s' step %d — LLM timeout (60s)", agent_name, step_num + 1)
                        agent_result["steps"].append({"step": step_num + 1, "error": "LLM timeout (60s)"})
                        agent_result["status"] = "failed"
                        agent_result["error"] = "LLM call timed out after 60 seconds"
                        break
                    except Exception as llm_err:
                        err_msg = str(llm_err)[:200]
                        logger.error("[EXPERIMENT] Agent '%s' LLM call failed: %s", agent_name, err_msg)
                        agent_result["steps"].append({"step": step_num + 1, "error": f"LLM error: {err_msg}"})
                        agent_result["status"] = "failed"
                        agent_result["error"] = err_msg
                        break
                    if not raw or not raw.strip():
                        logger.warning("[EXPERIMENT] Agent '%s' step %d — empty response", agent_name, step_num + 1)
                        agent_result["steps"].append({"step": step_num + 1, "error": "Empty LLM response"})
                        break

                    text = raw.strip()
                    # Extract JSON from LLM response — handle markdown blocks,
                    # trailing text, and other common LLM quirks
                    if "```" in text:
                        for p in text.split("```")[1:]:
                            cleaned = p.strip()
                            if cleaned.startswith("json"):
                                cleaned = cleaned[4:].strip()
                            if cleaned.startswith("{"):
                                text = cleaned
                                break

                    # Try to find a JSON object in the text (LLMs sometimes add preamble/postamble)
                    decision = None
                    try:
                        decision = json.loads(text)
                    except json.JSONDecodeError:
                        # Try extracting first { ... } block
                        brace_start = text.find("{")
                        if brace_start >= 0:
                            depth = 0
                            for ci, ch in enumerate(text[brace_start:], brace_start):
                                if ch == "{": depth += 1
                                elif ch == "}": depth -= 1
                                if depth == 0:
                                    try:
                                        decision = json.loads(text[brace_start:ci+1])
                                    except json.JSONDecodeError:
                                        pass
                                    break

                    if decision is None:
                        logger.warning("[EXPERIMENT] Agent '%s' step %d — bad JSON: %s", agent_name, step_num + 1, text[:120])
                        agent_result["steps"].append({"step": step_num + 1, "error": "Invalid JSON from LLM", "raw": text[:300]})
                        # Don't break — give the agent another chance
                        continue

                    reasoning = decision.get("reasoning", "")
                    action = decision.get("action", "")
                    params = decision.get("params", {})
                    done = decision.get("done", False)

                    # ── Track search vs write balance ──
                    _recent_actions.append(action)
                    search_count = sum(1 for a in _recent_actions if a in ("search", "search_nodes", "rag_query"))
                    write_count = sum(1 for a in _recent_actions if a in ("add_knowledge", "add_decision"))
                    # Also detect 4+ of same action overall (even with gaps)
                    if not done and action:
                        action_count = sum(1 for a in _recent_actions if a == action)
                        # Hard limits per action type
                        # Leaders need to create tasks for each worker — allow more
                        task_limit = 4 if role == "leader" else 2
                        max_per_action = {"add_task": task_limit, "add_knowledge": 3, "add_decision": 2}
                        limit = max_per_action.get(action, 5)
                        if action_count >= limit:
                            logger.warning("[EXPERIMENT] Agent '%s' hit limit for %s (%d/%d) — forcing done",
                                          agent_name, action, action_count, limit)
                            done = True
                            reasoning += f" [Auto-stopped: used {action} {action_count} times]"

                    _step_start = time.time()
                    step_entry = {
                        "step": step_num + 1, "reasoning": reasoning,
                        "tool": action, "params": params,
                        "llm_ms": int((_step_start - agent_start) * 1000) if step_num == 0 else None,
                    }

                    logger.info("[EXPERIMENT] Agent '%s' step %d — %s → %s",
                                agent_name, step_num + 1, reasoning[:80], action or "DONE")

                    # NOTE: AgentThought nodes are NOT persisted to the graph.
                    # They pollute search results and confuse agents. The reasoning
                    # is already captured in the step_entry for the timeline UI.

                    if done:
                        # Guard: if agent says done but never wrote anything, force one more step
                        WRITE_ACTIONS = {"add_knowledge", "add_task", "add_decision", "log_action", "remember"}
                        has_written = any(h.get("tool") in WRITE_ACTIONS for h in history)
                        if not has_written and step_num < 11 and role != "leader":
                            # Agent tried to finish without producing output — inject correction
                            logger.info("[EXPERIMENT] Agent '%s' said done without writing — forcing write step", agent_name)
                            # Build a write prompt from search history
                            search_data = "\n".join(
                                str(h.get("result", ""))[:200] for h in history if h.get("tool") in ("search", "search_nodes")
                            )
                            if search_data:
                                # Extract actual facts, not raw tool output
                                clean_facts = []
                                for line in search_data.split("\n"):
                                    line = line.strip()
                                    if any(line.startswith(f"[{l}]") for l in ("Fact", "Passage", "Entity", "Person", "Event", "Location", "Organization")):
                                        fact_text = line.split("]", 1)[-1].strip()
                                        if fact_text and len(fact_text) > 15:
                                            clean_facts.append(fact_text[:150])
                                finding_content = "; ".join(clean_facts[:5]) if clean_facts else search_data[:300]
                                # Override the decision — force a write
                                done = False
                                action = "add_knowledge"
                                params = {
                                    "content": f"Analysis by {agent_name}: {finding_content}",
                                    "node_type": "Finding",
                                }
                                reasoning = "[System: agent tried to finish without writing. Auto-generating Finding from search results.]"
                            # If no search data either, let it finish

                        summary = decision.get("summary", reasoning)
                        step_entry["summary"] = summary
                        agent_result["steps"].append(step_entry)
                        # Persist summary as Insight node
                        _graph_node(conn.executor, "Insight", {
                            "name": f"Insight by {agent_name}",
                            "content": summary,
                            "source": f"experiment:{experiment_id}",
                            "agent": agent_name, "agent_id": agent_id,
                            "role": role, "track_id": track_id, "type": "experiment",
                        })
                        # Propagate to other agents
                        propagator.propagate(
                            source_agent=agent_name, event_type="agent_completed",
                            content=f"{agent_name} finished: {summary[:200]}",
                            namespace=agent_graph, priority="normal",
                        )
                        # Cross-agent notification via propagation only — no graph pollution
                        break

                    if action:
                        gravity.observe(agent_id, action, params)

                        if action not in EXPERIMENT_TOOLS:
                            step_entry["error"] = f"Tool '{action}' not available. Use graph/search/task tools."
                            step_entry["success"] = False
                            history.append({"step": step_num + 1, "tool": action, "params": params, "success": False, "error": step_entry["error"]})
                        else:
                            try:
                                result_str = ToolRegistry.dispatch(action, ctx, params)
                                try:
                                    result_data = json.loads(result_str) if isinstance(result_str, str) else result_str
                                except Exception:
                                    result_data = result_str
                                step_entry["result"] = result_data
                                step_entry["success"] = True
                                history.append({"step": step_num + 1, "tool": action, "params": params, "result": result_data, "success": True})
                                logger.info("[EXPERIMENT] Agent '%s' step %d — %s OK", agent_name, step_num + 1, action)

                                # ── Record for Execution Replay ──
                                try:
                                    cp_id = replay_engine.record_step(
                                        run_id, step_num + 1, agent_id, agent_name,
                                        action, params, str(result_data)[:500],
                                        agent_graph, reasoning=reasoning,
                                    )
                                    if cp_id:
                                        step_entry["checkpoint_id"] = cp_id
                                except Exception:
                                    pass

                                # ── Platform auto-write: only in multi-agent scenarios where
                                # other agents need the results. Single agent just reports in summary. ──
                                search_count = sum(1 for h in history if h.get("tool") in ("search", "search_nodes", "rag_query"))
                                write_count = sum(1 for h in history if h.get("tool") in ("add_knowledge", "add_task", "add_decision"))
                                is_multi_agent = len(agents) > 1
                                if search_count >= 2 and write_count == 0 and step_num >= 2 and is_multi_agent and role != "leader":
                                    # Extract actual facts from search results (not raw tool output)
                                    facts = []
                                    for h in history:
                                        if h.get("tool") in ("search", "search_nodes") and h.get("result"):
                                            for line in str(h["result"]).split("\n"):
                                                line = line.strip()
                                                if any(line.startswith(f"[{l}]") for l in ("Fact","Passage","Entity","Statistic")):
                                                    content = line.split("—")[-1].strip() if "—" in line else line.split("]")[-1].strip()
                                                    if content and len(content) > 20:
                                                        facts.append(content[:150])
                                    if facts:
                                        auto_content = f"Key findings by {agent_name}: " + "; ".join(facts[:5])
                                        logger.info("[PLATFORM] Auto-writing Finding for %s (searched %dx, wrote 0)", agent_name, search_count)
                                        try:
                                            auto_result = ToolRegistry.dispatch("add_knowledge", ctx, {
                                                "content": auto_content[:800],
                                                "node_type": "Finding",
                                            })
                                            history.append({"step": step_num + 1, "tool": "add_knowledge", "params": {"auto": True}, "result": auto_result, "success": True})
                                            agent_result["steps"].append({"step": step_num + 1, "reasoning": "[Platform: auto-write Finding from collected data]", "tool": "add_knowledge", "result": auto_result[:200], "success": True})
                                        except Exception:
                                            pass

                                # NOTE: AgentAction nodes are NOT persisted to the graph.
                                # They pollute search and confuse agents. Actions are
                                # already captured in step_entry for the timeline UI.

                                # ── Persist findings from read/search tools ──
                                READ_TOOLS = {"search_nodes", "search", "rag_query", "rag_graph", "briefing", "graph_summary", "query_graph"}
                                if action in READ_TOOLS and result_data:
                                    # Extract a useful snippet from the result
                                    snippet = ""
                                    if isinstance(result_data, dict):
                                        # rag_query returns {answer: ...}
                                        snippet = result_data.get("answer", "") or result_data.get("summary", "")
                                        if not snippet:
                                            # search_nodes returns {nodes: [...]}
                                            nodes = result_data.get("nodes", [])
                                            if nodes:
                                                snippet = f"Found {len(nodes)} nodes"
                                                if len(nodes) <= 5:
                                                    names = [n.get("name", n.get("id", ""))[:30] for n in nodes[:5] if isinstance(n, dict)]
                                                    if names:
                                                        snippet += ": " + ", ".join(names)
                                    elif isinstance(result_data, str) and len(result_data) > 30:
                                        snippet = result_data[:250]

                                    if snippet and len(snippet) > 10:
                                        _graph_node(conn.executor, "Finding", {
                                            "name": f"Finding by {agent_name} via {action}",
                                            "content": snippet,
                                            "source_tool": action,
                                            "query": json.dumps(params)[:150],
                                            "agent": agent_name, "agent_id": agent_id,
                                            "step": step_num + 1,
                                            "track_id": track_id, "type": "experiment",
                                        })

                                # ── Propagate write actions to other agents ──
                                WRITE_TOOLS = {"add_knowledge", "add_task", "add_decision", "complete_task", "handoff_task", "log_action"}
                                if action in WRITE_TOOLS:
                                    propagator.propagate(
                                        source_agent=agent_name, event_type=action,
                                        content=f"{agent_name} called {action}: {json.dumps(params)[:150]}",
                                        namespace=agent_graph, priority="normal",
                                    )
                                    # Propagation handles cross-agent notification — no AgentMessage nodes

                            except Exception as e:
                                step_entry["error"] = str(e)
                                step_entry["success"] = False
                                history.append({"step": step_num + 1, "tool": action, "params": params, "success": False, "error": str(e)})
                                logger.error("[EXPERIMENT] Agent '%s' %s error: %s", agent_name, action, e)

                    step_entry["duration_ms"] = int((time.time() - _step_start) * 1000)
                    agent_result["steps"].append(step_entry)

                    # Emit step-result event for SSE streaming
                    if run_id in _run_step_callbacks:
                        try:
                            tool_name = step_entry.get("tool") or action or "unknown"
                            result_val = step_entry.get("result", "")
                            result_preview = str(result_val)[:200] if result_val else ""
                            step_params = step_entry.get("params") or {}
                            _run_step_callbacks[run_id]("step", {
                                "agent": agent_id, "agent_name": agent_name,
                                "step": step_num + 1, "action": tool_name,
                                "params": {k: str(v)[:50] for k, v in (step_params or {}).items()},
                                "status": "done",
                                "result": result_preview,
                                "success": step_entry.get("success"),
                                "duration_ms": int((time.time() - _step_start_ts) * 1000),
                                "ts": datetime.now(timezone.utc).isoformat(),
                            })
                        except Exception:
                            pass

                    # Live update: expose current agent's steps to polling
                    run["_live_agent"] = {
                        "name": agent_name,
                        "role": role,
                        "phase": agent_result.get("phase", ""),
                        "status": "running",
                        "steps": list(agent_result["steps"]),
                        "step_count": len(agent_result["steps"]),
                        "duration_ms": int((time.time() - agent_start) * 1000),
                    }

                if agent_result["status"] == "running":
                    agent_result["status"] = "completed"
                agent_result["duration_ms"] = int((time.time() - agent_start) * 1000)
                agent_result["steps_count"] = len(agent_result["steps"])
                run.pop("_live_agent", None)  # clear live data — agent is done
                logger.info("[EXPERIMENT] Agent '%s' done — %d steps, %dms",
                            agent_name, len(agent_result["steps"]), agent_result["duration_ms"])

                # Auto-complete tasks assigned to this agent
                try:
                    task_results = ToolRegistry.dispatch("my_tasks", ctx, {})
                    if task_results and "id:" in str(task_results):
                        import re as _re
                        task_ids = _re.findall(r'id:\s*([a-f0-9-]+)', str(task_results))
                        for tid in task_ids[:3]:
                            try:
                                ToolRegistry.dispatch("complete_task", ctx, {"task_id": tid})
                                logger.info("[EXPERIMENT] Auto-completed task %s for agent %s", tid, agent_name)
                            except Exception:
                                pass
                except Exception:
                    pass

                # Emit agent_done event for SSE streaming
                if run_id in _run_step_callbacks:
                    try:
                        _run_step_callbacks[run_id]("agent_done", {
                            "agent": agent_id, "agent_name": agent_name,
                            "steps": len(agent_result["steps"]),
                            "status": agent_result.get("status", "completed"),
                            "duration_ms": agent_result["duration_ms"],
                            "ts": datetime.now(timezone.utc).isoformat(),
                        })
                    except Exception:
                        pass

            except Exception as e:
                import traceback
                agent_result["status"] = "failed"
                agent_result["error"] = str(e)
                agent_result["error_detail"] = traceback.format_exc()[-500:]
                agent_result["duration_ms"] = int((time.time() - agent_start) * 1000)
                logger.error("[EXPERIMENT] Agent '%s' FAILED: %s\n%s", agent_name, e, traceback.format_exc()[-500:])

            return agent_result

        # ── ORCHESTRATION (via RunOrchestrator) ────────────────────
        goal = goal_override or exp.get("goal", "") or exp.get("description", "") or "Analyze the graph and provide insights"

        # Map old "queue" mode to "parallel" (unified pre-planned execution)
        if execution_mode == "queue":
            exp["execution_mode"] = "parallel"

        from .experiment_orchestrator import RunOrchestrator

        def _orchestrator_emit_sse(event_type, data):
            """Bridge orchestrator SSE events to the existing callback system."""
            if run_id in _run_step_callbacks:
                try:
                    _run_step_callbacks[run_id](event_type, data)
                except Exception:
                    pass

        orchestrator = RunOrchestrator(
            run=run,
            exp=exp,
            agents=agents,
            graph=agent_graph,
            registry=reg,
            run_id=run_id,
            experiment_id=experiment_id,
            track_id=track_id,
            session_id=exp_session,
            llm=llm,
            llm_provider_name=llm_provider_name,
            goal=goal,
            mode=mode,
            inputs=inputs,
            PlaygroundConn=PlaygroundConn,
            create_tool_context=create_tool_context,
            ToolRegistry=ToolRegistry,
            graph_node_fn=_graph_node,
            save_run_fn=_save_run,
            check_signal_fn=_check_signal,
            emit_sse_fn=_orchestrator_emit_sse,
            get_llm_client_fn=get_llm_client,
            experiment_tools=EXPERIMENT_TOOLS,
        )

        orchestrator.execute()

        # Auto-cleanup sandbox graph after completion (1 hour TTL for review)
        if sandbox_ns:
            def _cleanup_sandbox():
                try:
                    import time as _time
                    _time.sleep(3600)  # 1 hour TTL
                    reg.delete_graph(sandbox_ns)
                    logger.info("[EXPERIMENT] Auto-cleaned sandbox: %s", sandbox_ns)
                except Exception:
                    pass
            threading.Thread(target=_cleanup_sandbox, daemon=True, name=f"cleanup-{sandbox_ns}").start()

        # Finalize
        _save_experiment(exp)
        _run_controls.pop(run_id, None)
        _run_threads.pop(run_id, None)

        completed = sum(1 for a in run["agents"] if a.get("status") == "completed")
        skipped = sum(1 for a in run["agents"] if a.get("status") == "skipped")
        failed = sum(1 for a in run["agents"] if a.get("status") == "failed")
        logger.info("[EXPERIMENT] Run '%s' finished — overall=%d/100, graph=%s, agents=%d (ok=%d, skip=%d, fail=%d), %dms",
                    track_id, run.get("scores", {}).get("overall", 0),
                    agent_graph, len(agents), completed, skipped, failed, run.get("total_duration_ms", 0))

        return run

    # ------------------------------------------------------------------
    # Run history
    # ------------------------------------------------------------------

    @router.get("/runs/all")
    async def list_all_runs(
        session_id: str = Query(None),
        mode: str = Query(None),
        limit: int = Query(50, ge=1, le=200),
        user=Depends(user_auth),
    ):
        """List all runs across all experiments. Supports filtering by session and mode."""
        all_runs = []

        # Collect from in-memory
        for run in _runs.values():
            all_runs.append(run)

        # Collect from Redis
        r = _get_redis()
        if r:
            seen_ids = {run["run_id"] for run in all_runs}
            try:
                for exp_id in list(_experiments.keys()):
                    raw = r.hgetall(f"experiment_runs:{exp_id}")
                    for rid, data in raw.items():
                        if rid not in seen_ids:
                            try:
                                all_runs.append(json.loads(data))
                                seen_ids.add(rid)
                            except Exception:
                                pass
            except Exception:
                pass

        # Filter
        if session_id:
            all_runs = [r for r in all_runs if r.get("session_id") == session_id]
        if mode:
            all_runs = [r for r in all_runs if r.get("mode") == mode]

        all_runs.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        return {"runs": all_runs[:limit], "total": len(all_runs)}

    @router.get("/{experiment_id}/runs")
    async def list_runs(experiment_id: str, user=Depends(user_auth)):
        """List past runs for an experiment."""
        # Load from Redis
        r = _get_redis()
        runs = []
        if r:
            try:
                raw = r.hgetall(f"experiment_runs:{experiment_id}")
                for rid, data in raw.items():
                    runs.append(json.loads(data))
            except Exception:
                pass
        # Also check in-memory
        for run in _runs.values():
            if run.get("experiment_id") == experiment_id and run["run_id"] not in {r["run_id"] for r in runs}:
                runs.append(run)
        runs.sort(key=lambda x: x.get("started_at", ""), reverse=True)
        return {"runs": runs, "total": len(runs)}

    @router.get("/{experiment_id}/runs/{run_id}")
    async def get_run(experiment_id: str, run_id: str, user=Depends(user_auth)):
        run = _runs.get(run_id)
        if not run:
            r = _get_redis()
            if r:
                try:
                    data = r.hget(f"experiment_runs:{experiment_id}", run_id)
                    if data:
                        run = json.loads(data)
                except Exception:
                    pass
        if not run:
            raise HTTPException(404, "Run not found.")
        return run

    # ------------------------------------------------------------------
    # Run control (stop, pause, resume)
    # ------------------------------------------------------------------

    @router.post("/{experiment_id}/runs/{run_id}/stop")
    async def stop_run(experiment_id: str, run_id: str, user=Depends(require_member)):
        """Stop a running experiment."""
        if run_id not in _run_controls:
            # Check if run exists but already completed
            run_data = _runs.get(run_id)
            if run_data and run_data.get("status") != "running":
                return {"run_id": run_id, "signal": "already_finished", "status": run_data.get("status")}
            raise HTTPException(404, "Run not active.")
        _run_controls[run_id]["signal"] = "stop"
        logger.info("[EXPERIMENT] Run %s — STOP signal sent", run_id)
        return {"run_id": run_id, "signal": "stop"}

    @router.post("/{experiment_id}/runs/{run_id}/pause")
    async def pause_run(experiment_id: str, run_id: str, user=Depends(require_member)):
        """Pause a running experiment (agents will wait between steps)."""
        if run_id not in _run_controls:
            raise HTTPException(404, "Run not active.")
        _run_controls[run_id]["signal"] = "pause"
        logger.info("[EXPERIMENT] Run %s — PAUSE signal sent", run_id)
        return {"run_id": run_id, "signal": "pause"}

    @router.post("/{experiment_id}/runs/{run_id}/resume")
    async def resume_run(experiment_id: str, run_id: str, user=Depends(require_member)):
        """Resume a paused experiment."""
        if run_id not in _run_controls:
            raise HTTPException(404, "Run not active.")
        _run_controls[run_id]["signal"] = "running"
        logger.info("[EXPERIMENT] Run %s — RESUME signal sent", run_id)
        return {"run_id": run_id, "signal": "running"}

    @router.get("/{experiment_id}/runs/{run_id}/status")
    async def get_run_status(experiment_id: str, run_id: str, user=Depends(user_auth)):
        """Get live status of a running experiment (for polling)."""
        run_data = _runs.get(run_id)
        if not run_data:
            r = _get_redis()
            if r:
                try:
                    raw = r.hget(f"experiment_runs:{experiment_id}", run_id)
                    if raw:
                        run_data = json.loads(raw)
                except Exception:
                    pass
        if not run_data:
            raise HTTPException(404, "Run not found.")
        ctrl = _run_controls.get(run_id, {})
        return {
            "run_id": run_id,
            "status": run_data.get("status"),
            "signal": ctrl.get("signal", "finished"),
            "agents_completed": sum(1 for a in run_data.get("agents", []) if a.get("status") in ("completed", "failed", "skipped", "aborted")),
            "agents_total": len(run_data.get("agents", [])),
            "total_duration_ms": run_data.get("total_duration_ms"),
            "mode": run_data.get("mode"),
        }

    # ------------------------------------------------------------------
    # Execution Replay
    # ------------------------------------------------------------------

    @router.get("/{experiment_id}/runs/{run_id}/replay")
    async def get_run_replay(experiment_id: str, run_id: str, user=Depends(user_auth)):
        """Get step-by-step replay with graph diffs for each write operation."""
        from ..core.replay import get_replay_engine
        engine = get_replay_engine()
        steps = engine.get_replay_dicts(run_id)
        write_steps = [s for s in steps if s.get("is_write")]
        return {
            "run_id": run_id,
            "total_steps": len(steps),
            "write_steps": len(write_steps),
            "steps": steps,
        }

    @router.get("/{experiment_id}/runs/{run_id}/quality")
    async def get_run_quality(experiment_id: str, run_id: str, user=Depends(user_auth)):
        """Get context quality scores for each agent in a run."""
        from ..context.quality_tracker import get_quality_tracker
        tracker = get_quality_tracker()
        run_data = _runs.get(run_id, {})
        agents = run_data.get("agents", [])
        results = []
        for agent in agents:
            aid = agent.get("agent_id", "")
            report = tracker.compute_quality(aid)
            if report.deliveries_count > 0:
                results.append({
                    "agent_id": aid,
                    "agent_name": agent.get("name", ""),
                    "role": agent.get("role", ""),
                    **report.to_dict(),
                })
        return {"run_id": run_id, "quality": results}

    @router.post("/{experiment_id}/runs/{run_id}/fork")
    async def fork_run(experiment_id: str, run_id: str, req: dict = Body(default={}), user=Depends(require_member)):
        """Fork an experiment from a specific step to try alternative decisions."""
        from ..core.replay import get_replay_engine
        step_num = req.get("from_step", 1)
        new_ns = req.get("namespace", f"fork_{run_id[:8]}_{step_num}")
        source_ns = req.get("source_namespace", "")

        # Try to find source namespace from run data
        if not source_ns:
            run_data = _runs.get(run_id)
            if run_data:
                source_ns = run_data.get("sandbox_namespace") or run_data.get("source_graph", "")

        engine = get_replay_engine()
        result = engine.fork_from_step(run_id, step_num, new_ns, source_ns)
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result

    @router.get("/{experiment_id}/runs/{run_id}/steps/{step}/graph")
    async def get_step_graph(experiment_id: str, run_id: str, step: int, user=Depends(user_auth)):
        """Get graph snapshot at a specific step."""
        from ..core.replay import get_replay_engine
        run_data = _runs.get(run_id, {})
        ns = run_data.get("sandbox_namespace") or run_data.get("source_graph", "")
        engine = get_replay_engine()
        result = engine.get_graph_at_step(run_id, step, ns)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result

    # ------------------------------------------------------------------
    # Bootstrap code generation
    # ------------------------------------------------------------------

    @router.get("/{experiment_id}/bootstrap")
    async def get_bootstrap(experiment_id: str, user=Depends(user_auth)):
        """Generate runnable bootstrap code for all agents in the experiment.

        Returns per-agent code snippets for their configured framework.
        """
        exp = _experiments.get(experiment_id)
        if not exp:
            _load_experiments()
            exp = _experiments.get(experiment_id)
        if not exp:
            raise HTTPException(404, "Experiment not found.")

        from .experiment_bootstrap import generate_bootstrap
        import os

        api_url = os.environ.get("CONTEXTSYNAPSE_API_URL") or os.environ.get("AICONTEXTDB_API_URL", "http://localhost:8000")
        agents = exp.get("agents", [])
        bootstraps = []
        for agent in agents:
            b = generate_bootstrap(agent, exp, api_url=api_url)
            b["agent_name"] = agent.get("name", "")
            b["agent_id"] = agent.get("agent_id", "")
            bootstraps.append(b)

        return {
            "experiment_id": experiment_id,
            "experiment_name": exp.get("name", ""),
            "agents": bootstraps,
        }

    @router.get("/{experiment_id}/bootstrap/{agent_id}")
    async def get_agent_bootstrap(experiment_id: str, agent_id: str, user=Depends(user_auth)):
        """Generate bootstrap code for a single agent."""
        exp = _experiments.get(experiment_id)
        if not exp:
            _load_experiments()
            exp = _experiments.get(experiment_id)
        if not exp:
            raise HTTPException(404, "Experiment not found.")

        agent = next((a for a in exp.get("agents", []) if a.get("agent_id") == agent_id), None)
        if not agent:
            raise HTTPException(404, "Agent not found in experiment.")

        from .experiment_bootstrap import generate_bootstrap
        import os

        api_url = os.environ.get("CONTEXTSYNAPSE_API_URL") or os.environ.get("AICONTEXTDB_API_URL", "http://localhost:8000")
        return generate_bootstrap(agent, exp, api_url=api_url)

    # ------------------------------------------------------------------
    # Promote sandbox → live (replay experiment actions on real graph)
    # ------------------------------------------------------------------

    @router.post("/{experiment_id}/runs/{run_id}/promote")
    async def promote_sandbox(experiment_id: str, run_id: str, user=Depends(require_member)):
        """Promote a sandbox run — copy experiment-created nodes/edges to the real graph."""
        run_data = _runs.get(run_id)
        if not run_data:
            r = _get_redis()
            if r:
                try:
                    raw = r.hget(f"experiment_runs:{experiment_id}", run_id)
                    if raw:
                        run_data = json.loads(raw)
                except Exception:
                    pass
        if not run_data:
            raise HTTPException(404, "Run not found.")
        if run_data.get("mode") != "sandbox":
            raise HTTPException(400, "Only sandbox runs can be promoted.")
        if run_data.get("promoted"):
            raise HTTPException(400, "Already promoted.")
        if run_data.get("discarded"):
            raise HTTPException(400, "Run was discarded.")

        sandbox_ns = run_data.get("sandbox_namespace", "")
        source_graph = run_data.get("source_graph", "")
        exp_track_id = run_data.get("track_id", "")

        if not sandbox_ns or not source_graph:
            raise HTTPException(400, "Missing sandbox or source graph info.")

        from ..core.registry_factory import create_graph_registry
        from ..core.graph_structures import GraphNode, GraphEdge
        reg = create_graph_registry()

        sandbox_db = reg.get_graph(sandbox_ns)
        target_db = reg.get_graph(source_graph)
        if not sandbox_db or not target_db:
            raise HTTPException(400, "Could not access sandbox or target graph.")

        # Find nodes created during the experiment (tagged with track_id)
        promoted_nodes = 0
        promoted_edges = 0
        for node in sandbox_db.get_all_nodes():
            props = node.properties if hasattr(node, "properties") else {}
            if props.get("track_id") == exp_track_id or props.get("type") == "experiment":
                try:
                    new_node = GraphNode(id=node.id, label=node.label, properties=props)
                    target_db.add_node(new_node)
                    promoted_nodes += 1
                except Exception:
                    pass

        for edge in sandbox_db.get_all_edges():
            props = edge.properties if hasattr(edge, "properties") else {}
            if props.get("track_id") == exp_track_id:
                try:
                    new_edge = GraphEdge(
                        id=edge.id, source=edge.source, target=edge.target,
                        label=edge.label, properties=props,
                    )
                    target_db.add_edge(new_edge)
                    promoted_edges += 1
                except Exception:
                    pass

        # Drop sandbox
        try:
            reg.delete_graph(sandbox_ns)
        except Exception:
            pass

        run_data["promoted"] = True
        run_data["promoted_nodes"] = promoted_nodes
        run_data["promoted_edges"] = promoted_edges
        _save_run(run_data)

        logger.info("[EXPERIMENT] Promoted sandbox %s → %s: %d nodes, %d edges",
                    sandbox_ns, source_graph, promoted_nodes, promoted_edges)

        return {
            "promoted": True,
            "target_graph": source_graph,
            "promoted_nodes": promoted_nodes,
            "promoted_edges": promoted_edges,
        }

    # ------------------------------------------------------------------
    # Discard sandbox (drop temp graph)
    # ------------------------------------------------------------------

    @router.post("/{experiment_id}/runs/{run_id}/discard")
    async def discard_sandbox(experiment_id: str, run_id: str, user=Depends(require_member)):
        """Discard a sandbox run — drop the temporary graph."""
        run_data = _runs.get(run_id)
        if not run_data:
            r = _get_redis()
            if r:
                try:
                    raw = r.hget(f"experiment_runs:{experiment_id}", run_id)
                    if raw:
                        run_data = json.loads(raw)
                except Exception:
                    pass
        if not run_data:
            raise HTTPException(404, "Run not found.")
        if run_data.get("mode") != "sandbox":
            raise HTTPException(400, "Only sandbox runs can be discarded.")
        if run_data.get("discarded"):
            raise HTTPException(400, "Already discarded.")

        sandbox_ns = run_data.get("sandbox_namespace", "")
        if sandbox_ns:
            from ..core.registry_factory import create_graph_registry
            reg = create_graph_registry()
            try:
                reg.delete_graph(sandbox_ns)
            except Exception:
                pass

        run_data["discarded"] = True
        _save_run(run_data)

        logger.info("[EXPERIMENT] Discarded sandbox: %s", sandbox_ns)
        return {"discarded": True, "sandbox_namespace": sandbox_ns}

    # ------------------------------------------------------------------
    # Delete a run (permanently remove from history)
    # ------------------------------------------------------------------

    @router.delete("/{experiment_id}/runs/{run_id}")
    async def delete_run(experiment_id: str, run_id: str, user=Depends(require_member)):
        """Permanently delete a run and its sandbox graph if any."""
        run_data = _runs.pop(run_id, None)

        # Also try Redis
        r = _get_redis()
        if r:
            try:
                raw = r.hget(f"experiment_runs:{experiment_id}", run_id)
                if raw and not run_data:
                    run_data = json.loads(raw)
                r.hdel(f"experiment_runs:{experiment_id}", run_id)
            except Exception:
                pass

        if not run_data:
            raise HTTPException(404, "Run not found.")

        # Cleanup sandbox graph if exists
        sandbox_ns = run_data.get("sandbox_namespace", "")
        if sandbox_ns:
            try:
                from ..core.registry_factory import create_graph_registry
                reg = create_graph_registry()
                reg.delete_graph(sandbox_ns)
            except Exception:
                pass

        # Stop if still running
        _run_controls.pop(run_id, None)
        t = _run_threads.pop(run_id, None)

        logger.info("[EXPERIMENT] Deleted run %s (experiment %s)", run_id, experiment_id)
        return {"deleted": True, "run_id": run_id}

    return router
