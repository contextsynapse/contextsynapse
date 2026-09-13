"""
RunOrchestrator — Real-time parallel experiment execution engine.

Replaces the monolithic _do_execute_run with a clean orchestrator that:
- Runs agents in parallel by default (ThreadPoolExecutor)
- Emits structured SSE events for live UI updates
- Shares findings across agents via SharedFindingsBuffer
- Scores incrementally as agents complete
- Supports 3 modes: parallel, leader-follower, sequential
"""

from __future__ import annotations

import json
import logging
import re
import time
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ─── Structured SSE Event Types ────────────────────────────────────────────

class SSEEvent:
    """Typed SSE event for real-time streaming."""

    RUN_START = "run_start"
    AGENT_START = "agent_start"
    AGENT_PLAN = "agent_plan"
    STEP_DONE = "step_done"
    FINDING = "finding"
    SCORE_UPDATE = "score_update"
    AGENT_DONE = "agent_done"
    RUN_COMPLETE = "run_complete"
    ERROR = "error"

    @staticmethod
    def make(event_type: str, data: dict) -> dict:
        data["ts"] = datetime.now(timezone.utc).isoformat()
        return {"event": event_type, "data": data}


# ─── SharedFindingsBuffer ──────────────────────────────────────────────────

class SharedFindingsBuffer:
    """Thread-safe buffer for cross-agent finding visibility during a run."""

    def __init__(self):
        self._lock = threading.Lock()
        self._findings: List[Dict] = []

    def add(self, agent_name: str, content: str, node_type: str = "Finding"):
        with self._lock:
            self._findings.append({
                "agent": agent_name,
                "content": content,
                "node_type": node_type,
                "ts": datetime.now(timezone.utc).isoformat(),
            })

    def get_others(self, exclude_agent: str) -> List[Dict]:
        """Get findings from other agents (for cross-agent context)."""
        with self._lock:
            return [f for f in self._findings if f["agent"] != exclude_agent]

    def all(self) -> List[Dict]:
        with self._lock:
            return list(self._findings)

    def count(self) -> int:
        with self._lock:
            return len(self._findings)


# ─── Incremental Scorer ───────────────────────────────────────────────────

class IncrementalScorer:
    """Computes partial scores as agents complete, without waiting for all."""

    def __init__(self, total_agents: int, goal: str):
        self.total_agents = total_agents
        self.goal = goal
        self._agent_results: List[Dict] = []
        self._lock = threading.Lock()

    def add_result(self, agent_result: Dict) -> Dict[str, int]:
        """Add a completed agent result and return current partial scores."""
        with self._lock:
            self._agent_results.append(agent_result)
            return self._compute()

    def _compute(self) -> Dict[str, int]:
        all_steps = []
        for a in self._agent_results:
            all_steps.extend(a.get("steps", []))

        scores = {}

        # Coverage
        content_types = {"Person", "Organization", "Location", "Event", "Fact", "Document", "Passage"}
        queried_types = set()
        for s in all_steps:
            p = s.get("params", {})
            if p.get("label"):
                queried_types.add(p["label"])
            r_str = str(s.get("result", ""))
            for ct in content_types:
                if f"[{ct}]" in r_str:
                    queried_types.add(ct)
        queried_content = queried_types & content_types
        scores["coverage"] = min(100, int((len(queried_content) / len(content_types)) * 100))

        # Discovery
        findings = sum(1 for s in all_steps if s.get("tool") in ("search_nodes", "rag_query", "search", "query_graph") and s.get("success"))
        scores["discovery"] = min(100, findings * 15)

        # Enrichment
        writes = sum(1 for s in all_steps if s.get("tool") in ("add_knowledge", "add_task", "add_decision", "log_action") and s.get("success"))
        scores["enrichment"] = min(100, writes * 20)

        # Collaboration — measures how the context layer enabled agents to build on each other's work
        n_agents = len(self._agent_results)

        # 1. Cross-agent findings consumed: agent B's search returned agent A's written data
        cross_reads = 0
        for a in self._agent_results:
            other_names = {r.get("name", "") for r in self._agent_results if r.get("name") != a.get("name")}
            for s in a.get("steps", []):
                if s.get("tool") in ("search_nodes", "search") and s.get("success"):
                    result_text = str(s.get("result", ""))
                    if "Finding" in result_text or "Insight" in result_text:
                        cross_reads += 1
                    for other in other_names:
                        if other and other in result_text:
                            cross_reads += 1

        # 2. Shared entity coverage: agents searched overlapping entities (complementary research)
        agent_search_terms = {}
        for a in self._agent_results:
            name = a.get("name", "")
            terms = set()
            for s in a.get("steps", []):
                if s.get("tool") in ("search_nodes", "search") and s.get("success"):
                    q = (s.get("params", {}).get("query", "") or "").lower()
                    lbl = (s.get("params", {}).get("label", "") or "").lower()
                    for t in q.split():
                        if len(t) > 2:
                            terms.add(t)
                    if lbl:
                        terms.add(lbl)
            agent_search_terms[name] = terms
        entity_overlap = 0
        names_list = list(agent_search_terms.keys())
        for i in range(len(names_list)):
            for j in range(i + 1, len(names_list)):
                overlap = agent_search_terms[names_list[i]] & agent_search_terms[names_list[j]]
                entity_overlap += len(overlap)

        # 3. Context amplification: agents got richer context via SharedFindingsBuffer
        #    Count how many agents had cross-agent findings injected into their prompt
        agents_with_shared_context = 0
        for a in self._agent_results:
            ctx = a.get("context", "")
            if "OTHER AGENTS" in ctx or "FINDINGS" in ctx:
                agents_with_shared_context += 1

        # 4. Graph density: agents collectively wrote nodes that reference overlapping entities
        agents_writing = set()
        agents_searching = set()
        for a in self._agent_results:
            name = a.get("name", "")
            for s in a.get("steps", []):
                if s.get("tool") == "add_knowledge" and s.get("success"):
                    agents_writing.add(name)
                if s.get("tool") in ("search_nodes", "search") and s.get("success"):
                    agents_searching.add(name)
        # Agents who both read and wrote = active graph participants
        active_participants = len(agents_writing & agents_searching)

        # 5. CU coverage: how many distinct CU topics were covered across all agents
        cu_topics_covered = set()
        for a in self._agent_results:
            ctx = a.get("context", "")
            for line in ctx.split("\n"):
                if line.startswith("[ContextUnit:"):
                    cu_topics_covered.add(line)

        # Score
        collab_score = 0
        collab_score += min(cross_reads * 12, 30)                    # Cross-agent reads (up to 30)
        collab_score += min(entity_overlap * 10, 25)                 # Shared entity research (up to 25)
        collab_score += min(agents_with_shared_context * 10, 15)     # Context amplification (up to 15)
        collab_score += min(active_participants * 10, 20)            # Active graph participants (up to 20)
        collab_score += min(len(cu_topics_covered) * 5, 10)          # CU breadth (up to 10)
        scores["collaboration"] = min(100, collab_score)

        # Efficiency
        total_steps = len(all_steps)
        successful = sum(1 for s in all_steps if s.get("success"))
        scores["efficiency"] = int((successful / max(total_steps, 1)) * 100)

        # Completion (partial — based on completed vs total)
        scores["completion"] = int((len(self._agent_results) / max(self.total_agents, 1)) * 100)

        # Overall (without insight_quality — that comes at the end)
        weights_partial = {
            "coverage": 0.12, "discovery": 0.20, "enrichment": 0.20,
            "collaboration": 0.18, "efficiency": 0.15, "completion": 0.15,
        }
        scores["overall"] = int(sum(scores.get(k, 0) * w for k, w in weights_partial.items()))

        return scores


# ─── RunOrchestrator ──────────────────────────────────────────────────────

class RunOrchestrator:
    """
    Orchestrates parallel agent execution with real-time SSE streaming.

    Usage:
        orchestrator = RunOrchestrator(
            run=run_dict, exp=exp_dict, agents=agents_list,
            graph=graph_name, registry=reg, ...
        )
        orchestrator.execute()
    """

    def __init__(
        self,
        run: Dict,
        exp: Dict,
        agents: List[Dict],
        graph: str,
        registry,
        run_id: str,
        experiment_id: str,
        track_id: str,
        session_id: str,
        llm,
        llm_provider_name: str,
        goal: str,
        mode: str,
        inputs: Optional[Dict],
        # Injection points for existing infrastructure
        PlaygroundConn,
        create_tool_context,
        ToolRegistry,
        graph_node_fn: Callable,
        save_run_fn: Callable,
        check_signal_fn: Callable,
        emit_sse_fn: Optional[Callable] = None,
        get_llm_client_fn: Optional[Callable] = None,
        experiment_tools: Optional[Set[str]] = None,
    ):
        self.run = run
        self.exp = exp
        self.agents = agents
        self.graph = graph
        self.registry = registry
        self.run_id = run_id
        self.experiment_id = experiment_id
        self.track_id = track_id
        self.session_id = session_id
        self.llm = llm
        self.llm_provider_name = llm_provider_name
        self.goal = goal
        self.mode = mode
        self.inputs = inputs

        self.PlaygroundConn = PlaygroundConn
        self.create_tool_context = create_tool_context
        self.ToolRegistry = ToolRegistry
        self._graph_node = graph_node_fn
        self._save_run = save_run_fn
        self._check_signal = check_signal_fn
        self._emit_sse = emit_sse_fn or (lambda event_type, data: None)
        self._get_llm_client = get_llm_client_fn
        self.EXPERIMENT_TOOLS = experiment_tools or set()

        self.total_start = time.time()
        self.findings = SharedFindingsBuffer()
        self.scorer = IncrementalScorer(len(agents), goal)
        self.completed_agents: Set[str] = set()

        # Pre-init shared resources (avoids 40s+ lazy init per agent)
        self._shared_conn = None
        self._shared_project = None
        self._manifest_cache = None
        self._cu_context = ""       # Full CU briefing (for leader/synthesizer)
        self._cu_list = []          # Raw CU dicts (for partitioning)
        self._agent_cus = {}        # agent_name → [assigned CU dicts]
        self._pre_init()

    # ── Pre-initialization (expensive ops done ONCE) ─────────────────

    def _pre_init(self):
        """Pre-initialize expensive shared resources before agents run."""
        try:
            self._shared_conn = self.PlaygroundConn(self.graph, self.registry)
            # Pre-init ProjectGraph once (avoids 40s+ per-agent lazy init)
            from ..project.graph import ProjectGraph as _PG
            self._shared_project = _PG(self.graph, connection=self._shared_conn)
            logger.info("[ORCHESTRATOR] ProjectGraph pre-initialized for %s", self.graph)
        except Exception as e:
            logger.warning("[ORCHESTRATOR] ProjectGraph pre-init failed: %s", e)

        # Pre-cache manifest (avoid Redis lookup per agent)
        self._manifest_cache = self._get_manifest()

        # Pre-load Context Units matched to the experiment goal
        self._cu_context = self._load_context_units()

    def _load_context_units(self) -> str:
        """Load CUs, match to goal, partition across agents, return full briefing."""
        try:
            conn = self._shared_conn
            if not conn or not conn.db:
                return ""

            db = conn.db
            cu_nodes = db.get_all_nodes(label="ContextUnit")
            if not cu_nodes:
                return ""

            from ..context.context_units import match_intent, format_cu_for_agent
            cus = []
            for node in cu_nodes:
                props = node.properties if hasattr(node, 'properties') else {}
                cus.append({
                    "id": node.id if hasattr(node, 'id') else "",
                    "label": "ContextUnit",
                    "properties": props,
                })

            if not cus:
                return ""

            # Match CUs to the experiment goal
            matched = match_intent(self.goal, cus, limit=len(self.agents) * 3 or 5)
            if not matched:
                matched = sorted(cus, key=lambda c: len(c.get("evidence_ids", [])), reverse=True)[:len(self.agents) * 3 or 5]

            self._cu_list = matched

            # Partition CUs across agents — round-robin assignment
            n_agents = len(self.agents)
            if n_agents > 1 and len(matched) > 1:
                for i, cu in enumerate(matched):
                    agent_name = self.agents[i % n_agents].get("name", f"Agent-{i % n_agents}")
                    if agent_name not in self._agent_cus:
                        self._agent_cus[agent_name] = []
                    self._agent_cus[agent_name].append(cu)

                # Log partition
                for name, assigned in self._agent_cus.items():
                    topics = [c.get("properties", {}).get("topic", "?") for c in assigned]
                    logger.info("[ORCHESTRATOR] %s assigned CUs: %s", name, topics)
            else:
                # Single agent gets everything
                if self.agents:
                    self._agent_cus[self.agents[0].get("name", "Agent")] = matched

            # Full briefing (for synthesizer or fallback)
            lines = [f"INTELLIGENCE BRIEFING ({len(matched)} Context Units):"]
            for cu in matched:
                lines.append(format_cu_for_agent(cu))
                lines.append("")

            text = "\n".join(lines)
            logger.info("[ORCHESTRATOR] Loaded %d CUs, partitioned across %d agents", len(matched), n_agents)
            return text
        except Exception as e:
            logger.debug("[ORCHESTRATOR] CU loading failed: %s", e)
            return ""

    def _get_agent_cu_context(self, agent_name: str) -> str:
        """Get the CU briefing for a specific agent (partitioned topics)."""
        from ..context.context_units import format_cu_for_agent
        assigned = self._agent_cus.get(agent_name, [])
        if not assigned:
            return self._cu_context or ""

        # Show this agent's assigned topics + summary of what others are covering
        lines = [f"YOUR ASSIGNED TOPICS ({len(assigned)} Context Units):"]
        for cu in assigned:
            lines.append(format_cu_for_agent(cu))
            lines.append("")

        # Tell agent what others are covering (so it doesn't duplicate)
        other_topics = []
        for other_name, other_cus in self._agent_cus.items():
            if other_name != agent_name:
                topics = [c.get("properties", {}).get("topic", "?") for c in other_cus]
                other_topics.append(f"{other_name} is covering: {', '.join(topics)}")
        if other_topics:
            lines.append("OTHER AGENTS' ASSIGNED TOPICS (avoid duplicating their work):")
            for ot in other_topics:
                lines.append(f"  - {ot}")

        return "\n".join(lines)

    # ── Progress ────────────────────────────────────────────────────────

    def _progress(self, msg: str):
        self.run["_progress"] = msg
        self.run["_progress_at"] = datetime.now(timezone.utc).isoformat()
        if "_progress_log" not in self.run:
            self.run["_progress_log"] = []
        self.run["_progress_log"].append({
            "msg": msg,
            "time": datetime.now(timezone.utc).isoformat(),
            "elapsed_ms": int((time.time() - self.total_start) * 1000),
        })
        logger.info("[EXPERIMENT] %s — %s", self.track_id, msg)

    def _emit(self, event_type: str, data: dict):
        """Emit structured SSE event."""
        self._emit_sse(event_type, data)

    # ── Agent Execution (pre-planned, single LLM call) ─────────────────

    def _plan_and_execute_agent(
        self, agent_def: Dict, prompt: str, role: str, phase: str, max_tools: int = 6
    ) -> Dict:
        """Single LLM call → tool plan → execute all steps. Returns agent_result."""
        agent_name = agent_def.get("name", "Agent")
        agent_id = agent_def.get("agent_id", f"sim_{agent_name}")
        agent_start = time.time()

        agent_result = {
            "name": agent_name, "agent_id": agent_id,
            "role": role, "phase": phase, "status": "running",
            "llm_model": agent_def.get("llm_model", ""),
            "framework": agent_def.get("framework", ""),
            "type": "experiment", "steps": [],
        }

        # Emit agent_start
        self._emit(SSEEvent.AGENT_START, {
            "agent_id": agent_id, "agent_name": agent_name,
            "role": role, "phase": phase,
        })

        # Build tool context (reuse shared conn for reads, each agent gets own ctx)
        conn = self._shared_conn or self.PlaygroundConn(self.graph, self.registry)
        ctx = self.create_tool_context(conn, agent_id, agent_name)
        ctx._has_oriented = True
        ctx._hints_enabled = False
        ctx.metadata = getattr(ctx, "metadata", {})
        ctx.metadata.update({
            "experiment_id": self.experiment_id,
            "track_id": self.track_id, "run_id": self.run_id, "type": "experiment",
        })

        # Use pre-initialized project (instant, no lazy init)
        ctx.project = self._shared_project

        # Use cached manifest
        manifest_text = self._manifest_cache or ""

        # Inject cross-agent findings into context
        other_findings = self.findings.get_others(agent_name)
        findings_block = ""
        if other_findings:
            findings_block = "\n\nOTHER AGENTS' FINDINGS (use as additional context):\n"
            for f in other_findings[-5:]:  # last 5 findings
                findings_block += f"- [{f['agent']}] {f['content'][:150]}\n"

        # Build context: partitioned CUs + manifest + cross-agent findings
        cu_block = self._get_agent_cu_context(agent_name)
        context_block = cu_block + "\n\n" + (manifest_text or "Graph data available.") + findings_block
        context_block = context_block.strip()

        # Build the plan prompt
        plan_prompt = f"""You are "{agent_name}" ({role}).

CONTEXT:
{context_block}

TASK: {prompt}

Return a JSON array of tool calls to execute. Each item: {{"tool": "tool_name", "params": {{...}}}}

Available tools (fastest first):
- search_nodes(label="Fact", query="topic") — FAST: find facts about a topic (<300ms)
- search_nodes(label="Person") — FAST: find people, orgs, locations, events (<300ms)
- search(query="keywords") — broader text search across all nodes
- add_knowledge(content="...", node_type="Finding") — write analysis
- add_task(title="...", description="...", assigned_to="name") — assign work

RULES:
- ALWAYS end with add_knowledge to write your analysis as a Finding or Insight
- Search first, then write. Every plan MUST include at least one add_knowledge call
- Use search_nodes with label + query for fast, relevant results

Return ONLY a JSON array. Max {max_tools} tool calls."""

        self._progress(f"Agent '{agent_name}' ({role}) planning...")

        # Get LLM
        agent_llm = self.llm
        agent_llm_model = agent_def.get("llm_model", "")
        if agent_llm_model and agent_llm_model not in ("auto", "default", "") and self._get_llm_client:
            try:
                _prov, _, _model = agent_llm_model.partition(":")
                agent_llm = self._get_llm_client(provider=_prov, model=_model) if _model else self._get_llm_client(provider=_prov)
            except Exception:
                pass

        # Single LLM call
        try:
            import concurrent.futures as _cf
            with _cf.ThreadPoolExecutor(max_workers=1) as _tp:
                future = _tp.submit(agent_llm.generate, prompt=plan_prompt, max_tokens=800)
                text = future.result(timeout=30)
            llm_ms = int((time.time() - agent_start) * 1000)
            logger.info("[EXPERIMENT] Agent '%s' LLM returned %d chars in %dms", agent_name, len(text or ""), llm_ms)
        except Exception as e:
            agent_result["status"] = "failed"
            agent_result["error"] = f"LLM call failed: {e}"
            agent_result["duration_ms"] = int((time.time() - agent_start) * 1000)
            self._emit(SSEEvent.ERROR, {"agent_id": agent_id, "error": str(e)})
            return agent_result

        # Parse tool plan
        tool_plan = []
        try:
            json_match = re.search(r'\[.*\]', text, re.DOTALL)
            if json_match:
                tool_plan = json.loads(json_match.group())
        except Exception as parse_err:
            logger.warning("[EXPERIMENT] Failed to parse tool plan: %s | LLM: %s", parse_err, (text or "")[:300])

        # Emit plan event
        self._emit(SSEEvent.AGENT_PLAN, {
            "agent_id": agent_id, "agent_name": agent_name,
            "tool_plan": [{"tool": c.get("tool", ""), "params": c.get("params", {})} for c in tool_plan[:max_tools]],
            "llm_ms": llm_ms,
        })

        agent_result["context"] = context_block[:2000]
        agent_result["llm_ms"] = llm_ms

        # Execute tool plan
        for i, call in enumerate(tool_plan[:max_tools]):
            # Check for stop signal
            if self._check_signal(self.run_id) == "stop":
                agent_result["status"] = "aborted"
                break

            tool_name = call.get("tool", "")
            params = call.get("params", {})
            step_start = time.time()

            if tool_name not in self.EXPERIMENT_TOOLS:
                agent_result["steps"].append({
                    "step": i + 1, "tool": tool_name, "params": params,
                    "success": False, "error": f"Tool '{tool_name}' not available",
                    "duration_ms": 0,
                })
                continue

            try:
                result_str = self.ToolRegistry.dispatch(tool_name, ctx, params)
                step_ms = int((time.time() - step_start) * 1000)

                agent_result["steps"].append({
                    "step": i + 1, "tool": tool_name, "params": params,
                    "result": str(result_str)[:500], "success": True,
                    "duration_ms": step_ms,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })

                self._progress(f"Agent '{agent_name}' step {i+1} — {tool_name} OK ({step_ms}ms)")

                # Emit step_done
                self._emit(SSEEvent.STEP_DONE, {
                    "agent_id": agent_id, "agent_name": agent_name,
                    "step": i + 1, "action": tool_name,
                    "params": {k: str(v)[:50] for k, v in params.items()},
                    "duration_ms": step_ms,
                    "result": str(result_str)[:200],
                    "success": True,
                })

                # Track findings for cross-agent sharing
                if tool_name == "add_knowledge" and params.get("content"):
                    content = params["content"]
                    node_type = params.get("node_type", "Finding")
                    self.findings.add(agent_name, content, node_type)
                    self._emit(SSEEvent.FINDING, {
                        "agent_id": agent_id, "agent_name": agent_name,
                        "content_preview": content[:200],
                        "node_type": node_type,
                        "total_findings": self.findings.count(),
                    })

            except Exception as e:
                step_ms = int((time.time() - step_start) * 1000)
                agent_result["steps"].append({
                    "step": i + 1, "tool": tool_name, "params": params,
                    "success": False, "error": str(e), "duration_ms": step_ms,
                    "ts": datetime.now(timezone.utc).isoformat(),
                })

        # Auto-write if agent searched but didn't write
        self._auto_write_finding(agent_result, agent_name, ctx, phase)

        # Finalize agent result
        if agent_result["status"] == "running":
            agent_result["status"] = "completed"
        agent_result["duration_ms"] = int((time.time() - agent_start) * 1000)
        agent_result["steps_count"] = len(agent_result["steps"])

        # Emit agent_done
        self._emit(SSEEvent.AGENT_DONE, {
            "agent_id": agent_id, "agent_name": agent_name,
            "steps": len(agent_result["steps"]),
            "status": agent_result["status"],
            "duration_ms": agent_result["duration_ms"],
            "findings_count": len([s for s in agent_result["steps"] if s.get("tool") == "add_knowledge" and s.get("success")]),
        })

        return agent_result

    # ── Auto-write finding helper ──────────────────────────────────────

    def _auto_write_finding(self, agent_result: Dict, agent_name: str, ctx, phase: str):
        """If agent searched but didn't write, auto-create a Finding from results."""
        has_writes = any(s.get("tool") == "add_knowledge" and s.get("success") for s in agent_result["steps"])
        has_searches = any(s.get("tool") in ("search_nodes", "search") and s.get("success") for s in agent_result["steps"])

        if has_searches and not has_writes:
            try:
                facts = []
                for s in agent_result["steps"]:
                    if s.get("tool") in ("search_nodes", "search") and s.get("success"):
                        for line in str(s.get("result", "")).split("\n"):
                            line = line.strip()
                            if any(line.startswith(f"[{t}]") for t in ("Fact", "Person", "Event", "Organization")):
                                fact_text = line.split("]", 1)[-1].strip()[:150]
                                if fact_text and len(fact_text) > 15:
                                    facts.append(fact_text)
                if facts:
                    node_type = "Insight" if phase == "synthesize" else "Finding"
                    label = "Synthesis" if phase == "synthesize" else "Analysis"
                    auto_content = f"{label} by {agent_name}: " + "; ".join(facts[:5])
                    auto_result = self.ToolRegistry.dispatch("add_knowledge", ctx, {
                        "content": auto_content[:800], "node_type": node_type
                    })
                    agent_result["steps"].append({
                        "step": len(agent_result["steps"]) + 1, "tool": "add_knowledge",
                        "params": {"content": auto_content[:800], "node_type": node_type},
                        "result": str(auto_result)[:200], "success": True,
                        "duration_ms": 0,
                    })
                    self.findings.add(agent_name, auto_content[:800], node_type)
                    logger.info("[EXPERIMENT] Auto-wrote %s for %s (%d facts)", node_type, agent_name, len(facts))
            except Exception:
                pass

    # ── Manifest helper ────────────────────────────────────────────────

    def _get_manifest(self) -> str:
        """Load manifest text for agent context."""
        try:
            from ..context.quality import get_manifest, ManifestBuilder
            import redis as _redis_mod
            import os
            _r = _redis_mod.from_url(os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379"))
            source = self.run.get("source_graph", "")
            m = None
            if source:
                m = get_manifest(source, _r)
            if not m:
                m = get_manifest(self.graph, _r)
            if m:
                return ManifestBuilder.to_text(m)
        except Exception as e:
            logger.debug("[EXPERIMENT] Manifest lookup: %s", e)
        return ""

    # ── Execution Modes ────────────────────────────────────────────────

    def execute(self) -> Dict:
        """Main entry point — dispatches to the appropriate execution mode."""
        execution_mode = self.exp.get("execution_mode", "parallel")

        # Emit run_start
        self._emit(SSEEvent.RUN_START, {
            "run_id": self.run_id,
            "mode": execution_mode,
            "agents": [{"name": a.get("name"), "role": a.get("role", "agent")} for a in self.agents],
            "goal": self.goal[:300],
            "total_agents": len(self.agents),
        })

        if execution_mode == "leader-follower" and len(self.agents) >= 2:
            self._execute_leader_follower()
        elif execution_mode == "sequential":
            self._execute_sequential()
        else:
            # Default: parallel (replaces old "queue" mode)
            self._execute_parallel()

        # Final scoring (with insight quality from LLM)
        self._final_score()

        # Build output
        self._build_output()

        # Finalize run
        any_aborted = any(a.get("status") == "aborted" for a in self.run["agents"])
        if self._check_signal(self.run_id) == "stop" or any_aborted:
            self.run["status"] = "aborted"
        else:
            self.run["status"] = "completed"
        self.run["completed_at"] = datetime.now(timezone.utc).isoformat()
        self.run["total_duration_ms"] = int((time.time() - self.total_start) * 1000)
        self.run["graph"] = self.graph
        self.run["session_id"] = self.session_id
        self.run["llm_provider"] = self.llm_provider_name

        # Persist score as graph node
        if self.run.get("scores"):
            try:
                conn = self.PlaygroundConn(self.graph, self.registry)
                self._graph_node(conn.executor, "ExperimentScore", {
                    "name": f"Score: {self.exp.get('name', '')}",
                    "overall": self.run["scores"].get("overall", 0),
                    "coverage": self.run["scores"].get("coverage", 0),
                    "discovery": self.run["scores"].get("discovery", 0),
                    "enrichment": self.run["scores"].get("enrichment", 0),
                    "collaboration": self.run["scores"].get("collaboration", 0),
                    "efficiency": self.run["scores"].get("efficiency", 0),
                    "completion": self.run["scores"].get("completion", 0),
                    "insight_quality": self.run["scores"].get("insight_quality", 0),
                    "track_id": self.track_id, "type": "experiment",
                })
            except Exception:
                pass

        self.exp["run_count"] = self.exp.get("run_count", 0) + 1
        self._save_run(self.run)

        # Emit run_complete
        self._emit(SSEEvent.RUN_COMPLETE, {
            "run_id": self.run_id,
            "status": self.run["status"],
            "total_ms": self.run["total_duration_ms"],
            "score": self.run.get("scores", {}).get("overall", 0),
            "scores": self.run.get("scores", {}),
            "agents_completed": sum(1 for a in self.run["agents"] if a.get("status") == "completed"),
            "findings_count": self.findings.count(),
            "briefing_preview": (self.run.get("output") or "")[:300],
        })

        return self.run

    # ── PARALLEL MODE (default) ────────────────────────────────────────

    def _generate_focus_angles(self) -> Dict[str, str]:
        """Generate unique focus angles for each agent based on CU topics or goal decomposition."""
        n = len(self.agents)
        if n <= 1:
            return {}

        angles = {}

        # Strategy 1: Use partitioned CU topics as focus angles
        if self._agent_cus:
            for agent_name, cus in self._agent_cus.items():
                topics = [c.get("properties", {}).get("topic", "") for c in cus if c.get("properties", {}).get("topic")]
                if topics:
                    angles[agent_name] = "; ".join(topics)
            if len(angles) == n:
                logger.info("[ORCHESTRATOR] Focus angles from CU partition: %s",
                            {k: v[:50] for k, v in angles.items()})
                return angles

        # Strategy 2: Decompose goal into complementary perspectives
        # Use predefined research lenses that work for any topic
        perspectives = [
            "Key actors, people, and organizations involved — WHO is doing what",
            "Events, timeline, and recent developments — WHAT happened and WHEN",
            "Causes, motivations, and strategic interests — WHY this is happening",
            "Impacts, consequences, and future implications — WHAT NEXT",
            "Regional and international reactions — HOW others are responding",
            "Economic and financial dimensions — money, trade, sanctions",
            "Military and security aspects — forces, threats, alliances",
            "Diplomatic efforts and negotiations — talks, deals, agreements",
        ]

        for i, agent_def in enumerate(self.agents):
            name = agent_def.get("name", f"Agent-{i}")
            angles[name] = perspectives[i % len(perspectives)]

        logger.info("[ORCHESTRATOR] Focus angles from perspectives: %s",
                    {k: v[:40] for k, v in angles.items()})
        return angles

    def _execute_parallel(self):
        """All agents run concurrently with pre-planned execution."""
        self._progress(f"Parallel mode: {len(self.agents)} agents starting concurrently...")

        # Generate unique focus angles for each agent to avoid duplicate effort
        focus_angles = self._generate_focus_angles()

        def _run_agent(agent_def, focus=""):
            a_name = agent_def.get("name", "Agent")
            a_prompt = agent_def.get("prompt", "")
            other_agents = [a.get("name") for a in self.agents if a.get("name") != a_name]
            team_info = f"Other agents working in parallel: {', '.join(other_agents)}" if other_agents else ""

            focus_directive = f"\nYOUR FOCUS: {focus}\nSearch for data specifically related to your assigned focus. Do NOT duplicate other agents' work." if focus else ""

            prompt = f"""GOAL: {self.goal}
{f'INSTRUCTIONS: {a_prompt}' if a_prompt else ''}
{team_info}{focus_directive}

Search the graph for relevant data, then write a Finding with your analysis.
You MUST end with add_knowledge to write your analysis."""

            return self._plan_and_execute_agent(agent_def, prompt, agent_def.get("role", "agent"), phase="execute")

        max_workers = min(len(self.agents), 8)  # Cap at 8 concurrent
        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            futures = {pool.submit(_run_agent, a, focus_angles.get(a.get("name", ""), "")): a for a in self.agents}
            for future in as_completed(futures):
                try:
                    result = future.result()
                    self.run["agents"].append(result)
                    self._save_run(self.run)
                    self.completed_agents.add(result.get("name", ""))

                    # Incremental score
                    if result.get("status") == "completed":
                        partial_scores = self.scorer.add_result(result)
                        self._emit(SSEEvent.SCORE_UPDATE, {
                            "partial_score": partial_scores.get("overall", 0),
                            "scores": partial_scores,
                            "scored_agents": len(self.completed_agents),
                            "total_agents": len(self.agents),
                        })
                except Exception as e:
                    agent_def = futures[future]
                    self.run["agents"].append({
                        "name": agent_def.get("name", "Agent"),
                        "status": "failed", "error": str(e),
                        "duration_ms": 0,
                    })
                    logger.error("[EXPERIMENT] Agent failed: %s", e)

    # ── LEADER-FOLLOWER MODE ──────────────────────────────────────────

    def _execute_leader_follower(self):
        """Leader plans tasks, workers execute in parallel, leader synthesizes."""
        leader = self.agents[0]
        workers = self.agents[1:]
        leader_name = leader.get("name", "Leader")
        worker_names = ', '.join(w.get("name", f"Worker-{i}") for i, w in enumerate(workers))

        # Phase 1: PLAN
        self._progress(f"Phase 1: PLAN — {leader_name} analyzing graph...")
        plan_prompt = f"""GOAL: {self.goal}

Your team: {worker_names} ({len(workers)} member(s))

You MUST create exactly {len(workers)} task(s) using add_task — one per team member.
Each task should specify what to research. Do NOT search — just create tasks.

Example plan for 1 worker named "Research Analyst":
[{{"tool": "add_task", "params": {{"title": "Research Iran-US tensions", "description": "Search for facts about Iran ceasefire, US military moves, key actors", "assigned_to": "Research Analyst"}}}}]"""

        plan_result = self._plan_and_execute_agent(leader, plan_prompt, "leader", "plan", max_tools=len(workers) + 1)
        self.run["agents"].append(plan_result)
        self._save_run(self.run)
        self.completed_agents.add(leader_name)

        # Extract tasks from leader's plan
        leader_tasks = {}
        for step in plan_result.get("steps", []):
            if step.get("tool") == "add_task" and step.get("success"):
                params = step.get("params", {})
                assigned = params.get("assigned_to", "")
                if assigned:
                    leader_tasks[assigned] = {
                        "title": params.get("title", ""),
                        "description": params.get("description", ""),
                    }
        logger.info("[EXPERIMENT] Leader created %d tasks: %s", len(leader_tasks), list(leader_tasks.keys()))

        # Phase 2: EXECUTE — Workers in parallel
        self._progress(f"Phase 2: EXECUTE — {len(workers)} workers in parallel...")

        def _run_worker(worker_def):
            w_name = worker_def.get("name", "Worker")
            task_info = leader_tasks.get(w_name, {})
            task_block = f"\nYOUR TASK: {task_info['title']}\n{task_info['description']}" if task_info else ""

            worker_prompt = f"""GOAL: {self.goal}
{task_block}

Search for relevant data, then write a Finding with specific facts, names, and numbers."""

            return self._plan_and_execute_agent(worker_def, worker_prompt, "worker", "execute", max_tools=6)

        with ThreadPoolExecutor(max_workers=len(workers)) as pool:
            futures = {pool.submit(_run_worker, w): w for w in workers}
            for future in as_completed(futures):
                try:
                    w_result = future.result()
                    self.run["agents"].append(w_result)
                    self._save_run(self.run)
                    self.completed_agents.add(w_result.get("name", ""))

                    # Incremental score
                    if w_result.get("status") == "completed":
                        partial_scores = self.scorer.add_result(w_result)
                        self._emit(SSEEvent.SCORE_UPDATE, {
                            "partial_score": partial_scores.get("overall", 0),
                            "scores": partial_scores,
                            "scored_agents": len(self.completed_agents),
                            "total_agents": len(self.agents),
                        })
                except Exception as e:
                    worker_def = futures[future]
                    self.run["agents"].append({
                        "name": worker_def.get("name", "Worker"),
                        "status": "failed", "error": str(e),
                    })

        # Phase 3: SYNTHESIZE — Leader reads findings and writes summary
        if self._check_signal(self.run_id) != "stop":
            self._progress(f"Phase 3: SYNTHESIZE — {leader_name} reviewing findings...")
            synth_prompt = f"""Your team has completed their research.

GOAL: {self.goal}

Your plan MUST be: 1) search_nodes to read Findings, 2) add_knowledge to write a comprehensive Insight.
The Insight should be a strategic briefing paragraph with specific names, numbers, and facts.
node_type MUST be "Insight" (not "Finding")."""

            synth_result = self._plan_and_execute_agent(leader, synth_prompt, "leader", "synthesize", max_tools=4)
            self.run["agents"].append(synth_result)
            self._save_run(self.run)

    # ── SEQUENTIAL MODE (backward compat) ─────────────────────────────

    def _execute_sequential(self):
        """Agents run one at a time (backward compat). Still uses pre-planned execution."""
        self._progress(f"Sequential mode: {len(self.agents)} agents...")

        for agent_def in self.agents:
            if self._check_signal(self.run_id) == "stop":
                self.run["agents"].append({"name": agent_def.get("name"), "status": "aborted"})
                break

            a_name = agent_def.get("name", "Agent")
            a_prompt = agent_def.get("prompt", "")

            prompt = f"""GOAL: {self.goal}
{f'INSTRUCTIONS: {a_prompt}' if a_prompt else ''}

Search the graph for relevant data, then write a Finding with your analysis.
You MUST end with add_knowledge to write your analysis."""

            result = self._plan_and_execute_agent(agent_def, prompt, agent_def.get("role", "agent"), phase="execute")
            self.run["agents"].append(result)
            self._save_run(self.run)
            self.completed_agents.add(a_name)

            # Incremental score
            if result.get("status") == "completed":
                partial_scores = self.scorer.add_result(result)
                self._emit(SSEEvent.SCORE_UPDATE, {
                    "partial_score": partial_scores.get("overall", 0),
                    "scores": partial_scores,
                    "scored_agents": len(self.completed_agents),
                    "total_agents": len(self.agents),
                })

    # ── Final scoring with LLM insight quality ─────────────────────────

    def _final_score(self):
        """Compute final score including LLM-evaluated insight quality."""
        try:
            # Get partial scores from incremental scorer
            scores = self.scorer._compute() if self.scorer._agent_results else {}

            # LLM-evaluated insight quality
            final_insights = []
            for a in self.run.get("agents", []):
                for s in a.get("steps", []):
                    if s.get("tool") == "add_knowledge" and s.get("success"):
                        content = s.get("params", {}).get("content", "")
                        if content and len(content) > 30:
                            final_insights.append(content)

            if final_insights and self.llm:
                try:
                    eval_prompt = f"""Rate the quality of this experiment's output on a scale of 0-100.

GOAL: {self.goal}

EXPERIMENT INSIGHTS:
{chr(10).join(f'- {ins[:200]}' for ins in final_insights[:5])}

Rate based on:
- Relevance: Do the insights address the goal?
- Depth: Are they specific or generic?
- Actionability: Can someone act on these findings?

Reply with ONLY a number between 0 and 100."""
                    raw_score = self.llm.generate(prompt=eval_prompt, max_tokens=10)
                    nums = re.findall(r'\d+', raw_score.strip())
                    scores["insight_quality"] = min(100, max(0, int(nums[0]))) if nums else 50
                except Exception:
                    scores["insight_quality"] = 50
            else:
                scores["insight_quality"] = 0 if not final_insights else 50

            # Final overall with insight_quality weighted in
            weights = {
                "coverage": 0.10, "discovery": 0.15, "enrichment": 0.15,
                "collaboration": 0.15, "efficiency": 0.10, "completion": 0.10,
                "insight_quality": 0.25,
            }
            scores["overall"] = int(sum(scores.get(k, 0) * w for k, w in weights.items()))

            self.run["scores"] = scores
            logger.info("[EXPERIMENT] Final scores: %s", scores)
        except Exception as e:
            logger.warning("[EXPERIMENT] Scoring failed: %s", e)
            self.run["scores"] = {}

    # ── Build output briefing ──────────────────────────────────────────

    def _build_output(self):
        """Build experiment summary from all Findings and Insights."""
        summary_parts = []
        for a in self.run.get("agents", []):
            for s in a.get("steps", []):
                if s.get("tool") == "add_knowledge" and s.get("success"):
                    content = s.get("params", {}).get("content", "")
                    node_type = s.get("params", {}).get("node_type", "Finding")
                    if content and len(content) > 20:
                        summary_parts.append({
                            "type": node_type,
                            "agent": a.get("name", ""),
                            "phase": a.get("phase", ""),
                            "content": content,
                        })
        self.run["summary"] = summary_parts

        if summary_parts:
            insights = [p for p in summary_parts if p["type"] == "Insight"]
            findings = [p for p in summary_parts if p["type"] == "Finding"]
            output_lines = []
            if insights:
                output_lines.append("=== INTELLIGENCE BRIEFING ===")
                for ins in insights:
                    output_lines.append(f"\n{ins['content']}")
            if findings:
                if output_lines:
                    output_lines.append("\n\n=== SUPPORTING FINDINGS ===")
                else:
                    output_lines.append("=== FINDINGS ===")
                for f in findings:
                    output_lines.append(f"\n[{f['agent']}] {f['content']}")
            self.run["output"] = "\n".join(output_lines)
        else:
            self.run["output"] = "No findings or insights were produced."
