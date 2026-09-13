"""
Context Runtime (CR)
=====================
The execution scope where contexts merge and agents work.

A boundary:
  - Assembles multiple input contexts (read-only)
  - Creates an execution graph (read-write) where agents produce work
  - Manages agent access and workspace config
  - Provides a unified view across all contexts

Usage:
    mgr = BoundaryManager()

    # Create atomic contexts
    req_ctx = mgr.create_context("Requirements", type="document")
    mgr.ingest(req_ctx.context_id, [{"label": "Requirement", "properties": {"title": "..."}}])

    # Create boundary and attach contexts
    boundary = mgr.create_boundary("ABC Website Sprint 1", goal="Build the website")
    mgr.attach_context(boundary.boundary_id, req_ctx.context_id, role="input")

    # Agent gets unified view
    view = mgr.get_unified_view(boundary.boundary_id)
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class AtomicContext:
    """A typed graph — one kind of data. The unit of knowledge."""
    context_id: str
    name: str
    type: str                      # document | code | rules | knowledge | decision | config
    graph_namespace: str           # where nodes live
    status: str = "active"
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    metadata: Dict[str, Any] = field(default_factory=dict)
    node_count: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "context_id": self.context_id,
            "name": self.name,
            "type": self.type,
            "graph_namespace": self.graph_namespace,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "metadata": self.metadata,
            "node_count": self.node_count,
        }


@dataclass
class RuntimeContextBoundary:
    """The execution scope — where contexts merge and agents work."""
    boundary_id: str
    name: str
    goal: str = ""
    status: str = "active"         # active | paused | completed | archived
    execution_namespace: str = ""  # graph namespace for agent output
    workspace_config: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(default_factory=_now)
    updated_at: str = field(default_factory=_now)
    config: Dict[str, Any] = field(default_factory=dict)

    # Populated by BoundaryManager, not stored directly
    attached_contexts: List[Dict[str, Any]] = field(default_factory=list)
    agent_access: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "boundary_id": self.boundary_id,
            "name": self.name,
            "goal": self.goal,
            "status": self.status,
            "execution_namespace": self.execution_namespace,
            "workspace_config": self.workspace_config,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "config": self.config,
            "attached_contexts": self.attached_contexts,
            "agent_access": self.agent_access,
        }

    # Backward compat with Session
    @property
    def session_id(self):
        return self.boundary_id

    @property
    def graph_namespace(self):
        return self.execution_namespace


class BoundaryManager:
    """Manages atomic contexts and runtime context boundaries.

    Stores metadata in SQLite. Graphs are in the GraphRegistry.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS atomic_contexts (
        context_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        type TEXT NOT NULL,
        graph_namespace TEXT NOT NULL,
        status TEXT DEFAULT 'active',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        metadata TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS boundaries (
        boundary_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        goal TEXT DEFAULT '',
        status TEXT DEFAULT 'active',
        execution_namespace TEXT NOT NULL,
        workspace_config TEXT DEFAULT '{}',
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        config TEXT DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS boundary_contexts (
        boundary_id TEXT NOT NULL,
        context_id TEXT NOT NULL,
        role TEXT DEFAULT 'input',
        attached_at TEXT NOT NULL,
        PRIMARY KEY (boundary_id, context_id)
    );

    CREATE TABLE IF NOT EXISTS boundary_agents (
        boundary_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        access_level TEXT DEFAULT 'read',
        granted_at TEXT NOT NULL,
        PRIMARY KEY (boundary_id, agent_id)
    );
    """

    def __init__(self, db_path: str = "contextcore_data/context.db", graph_registry=None):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)
        self._graph_registry = graph_registry

    # ── Context CRUD ────────────────────────────────────────────────

    def create_context(self, name: str, type: str, metadata: Optional[Dict] = None) -> AtomicContext:
        """Create an atomic context with its own graph namespace."""
        context_id = secrets.token_hex(12)
        namespace = f"ctx_{name.replace(' ', '_')[:30]}_{context_id[:6]}"
        now = _now()

        ctx = AtomicContext(
            context_id=context_id,
            name=name,
            type=type,
            graph_namespace=namespace,
            created_at=now,
            updated_at=now,
            metadata=metadata or {},
        )

        self._conn.execute(
            "INSERT INTO atomic_contexts (context_id, name, type, graph_namespace, status, created_at, updated_at, metadata) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (context_id, name, type, namespace, "active", now, now, json.dumps(metadata or {})),
        )
        self._conn.commit()

        # Create graph namespace
        if self._graph_registry:
            self._graph_registry.create_graph(namespace)

        logger.info("Created atomic context: %s (%s, type=%s)", name, context_id[:8], type)
        return ctx

    def get_context(self, context_id: str) -> Optional[AtomicContext]:
        row = self._conn.execute(
            "SELECT * FROM atomic_contexts WHERE context_id = ?", (context_id,)
        ).fetchone()
        if not row:
            return None
        return AtomicContext(
            context_id=row["context_id"],
            name=row["name"],
            type=row["type"],
            graph_namespace=row["graph_namespace"],
            status=row["status"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    def list_contexts(self, type: Optional[str] = None) -> List[AtomicContext]:
        if type:
            rows = self._conn.execute(
                "SELECT * FROM atomic_contexts WHERE status = 'active' AND type = ? ORDER BY created_at DESC", (type,)
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM atomic_contexts WHERE status = 'active' ORDER BY created_at DESC"
            ).fetchall()
        return [AtomicContext(
            context_id=r["context_id"], name=r["name"], type=r["type"],
            graph_namespace=r["graph_namespace"], status=r["status"],
            created_at=r["created_at"], updated_at=r["updated_at"],
            metadata=json.loads(r["metadata"] or "{}"),
        ) for r in rows]

    # ── Boundary CRUD ───────────────────────────────────────────────

    def create_boundary(
        self,
        name: str,
        goal: str = "",
        workspace_config: Optional[Dict] = None,
        config: Optional[Dict] = None,
    ) -> RuntimeContextBoundary:
        """Create a runtime context boundary with its own execution namespace."""
        boundary_id = secrets.token_hex(12)
        exec_ns = f"rcb_{name.replace(' ', '_')[:30]}_{boundary_id[:6]}"
        now = _now()
        ws_config = workspace_config or {}
        cfg = config or {}

        boundary = RuntimeContextBoundary(
            boundary_id=boundary_id,
            name=name,
            goal=goal,
            status="active",
            execution_namespace=exec_ns,
            workspace_config=ws_config,
            created_at=now,
            updated_at=now,
            config=cfg,
        )

        self._conn.execute(
            "INSERT INTO boundaries (boundary_id, name, goal, status, execution_namespace, workspace_config, created_at, updated_at, config) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (boundary_id, name, goal, "active", exec_ns, json.dumps(ws_config), now, now, json.dumps(cfg)),
        )
        self._conn.commit()

        # Create execution graph namespace
        if self._graph_registry:
            self._graph_registry.create_graph(exec_ns)

        logger.info("Created boundary: %s (%s, exec=%s)", name, boundary_id[:8], exec_ns)
        return boundary

    def get_boundary(self, boundary_id: str) -> Optional[RuntimeContextBoundary]:
        row = self._conn.execute(
            "SELECT * FROM boundaries WHERE boundary_id = ?", (boundary_id,)
        ).fetchone()
        if not row:
            return None

        boundary = RuntimeContextBoundary(
            boundary_id=row["boundary_id"],
            name=row["name"],
            goal=row["goal"],
            status=row["status"],
            execution_namespace=row["execution_namespace"],
            workspace_config=json.loads(row["workspace_config"] or "{}"),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            config=json.loads(row["config"] or "{}"),
        )

        # Populate attached contexts
        boundary.attached_contexts = self.list_attached_contexts(boundary_id)
        boundary.agent_access = self.get_access_list(boundary_id)
        return boundary

    def list_boundaries(self, status: str = "active") -> List[RuntimeContextBoundary]:
        rows = self._conn.execute(
            "SELECT * FROM boundaries WHERE status = ? ORDER BY created_at DESC", (status,)
        ).fetchall()
        return [RuntimeContextBoundary(
            boundary_id=r["boundary_id"], name=r["name"], goal=r["goal"],
            status=r["status"], execution_namespace=r["execution_namespace"],
            workspace_config=json.loads(r["workspace_config"] or "{}"),
            created_at=r["created_at"], updated_at=r["updated_at"],
            config=json.loads(r["config"] or "{}"),
        ) for r in rows]

    # ── Context attachment ──────────────────────────────────────────

    def attach_context(self, boundary_id: str, context_id: str, role: str = "input"):
        """Attach an atomic context to a boundary."""
        now = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO boundary_contexts (boundary_id, context_id, role, attached_at) VALUES (?, ?, ?, ?)",
            (boundary_id, context_id, role, now),
        )
        self._conn.commit()
        logger.info("Attached context %s to boundary %s (role=%s)", context_id[:8], boundary_id[:8], role)

    def detach_context(self, boundary_id: str, context_id: str):
        self._conn.execute(
            "DELETE FROM boundary_contexts WHERE boundary_id = ? AND context_id = ?",
            (boundary_id, context_id),
        )
        self._conn.commit()

    def list_attached_contexts(self, boundary_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT bc.*, ac.name, ac.type, ac.graph_namespace FROM boundary_contexts bc "
            "JOIN atomic_contexts ac ON bc.context_id = ac.context_id "
            "WHERE bc.boundary_id = ?",
            (boundary_id,),
        ).fetchall()
        return [
            {
                "context_id": r["context_id"],
                "name": r["name"],
                "type": r["type"],
                "graph_namespace": r["graph_namespace"],
                "role": r["role"],
                "attached_at": r["attached_at"],
            }
            for r in rows
        ]

    # ── Agent access ────────────────────────────────────────────────

    def grant_access(self, boundary_id: str, agent_id: str, level: str = "write"):
        now = _now()
        self._conn.execute(
            "INSERT OR REPLACE INTO boundary_agents (boundary_id, agent_id, access_level, granted_at) VALUES (?, ?, ?, ?)",
            (boundary_id, agent_id, level, now),
        )
        self._conn.commit()

    def revoke_access(self, boundary_id: str, agent_id: str):
        self._conn.execute(
            "DELETE FROM boundary_agents WHERE boundary_id = ? AND agent_id = ?",
            (boundary_id, agent_id),
        )
        self._conn.commit()

    def get_access_list(self, boundary_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT * FROM boundary_agents WHERE boundary_id = ?", (boundary_id,)
        ).fetchall()
        return [{"agent_id": r["agent_id"], "access_level": r["access_level"], "granted_at": r["granted_at"]} for r in rows]

    # ── Unified view ────────────────────────────────────────────────

    def get_unified_view(self, boundary_id: str) -> Dict[str, Any]:
        """Get all data from all attached contexts + execution graph.

        Returns a dict with separate sections for each context and the execution graph.
        This is what `briefing()` uses to build the agent's context.
        """
        boundary = self.get_boundary(boundary_id)
        if not boundary:
            return {"error": f"Boundary {boundary_id} not found"}

        # Runtime namespace holds agent work (tasks, findings, actions)
        rt_ns = f"{boundary.execution_namespace}_rt"
        view = {
            "boundary": boundary.to_dict(),
            "input_contexts": [],
            "execution": {"namespace": boundary.execution_namespace, "nodes": []},
            "runtime": {"namespace": rt_ns, "nodes": []},
        }

        if not self._graph_registry:
            return view

        from contextsynapse.adapters._base import AIContextDBConnection

        # Labels to try per context type (SELECT * without label is broken)
        _TYPE_LABELS = {
            "document": ["Requirement", "Document", "TextChunk", "Passage"],
            "code": ["CodeFile", "Module", "ProjectSpec"],
            "rules": ["Rule", "Constraint"],
            "knowledge": ["Finding", "Entity", "Fact", "Knowledge"],
            "decision": ["Decision"],
            "config": ["Config", "Setting"],
        }
        _EXEC_LABELS = ["Task", "Decision", "Action", "CodeFile", "Finding", "Knowledge", "GitPush", "AgentAction"]

        # Collect nodes from each attached context
        for ctx_info in boundary.attached_contexts:
            ns = ctx_info["graph_namespace"]
            try:
                conn = AIContextDBConnection(namespace=ns, graph_registry=self._graph_registry)
                labels = _TYPE_LABELS.get(ctx_info["type"], [ctx_info["type"].capitalize()])
                nodes = []
                for label in labels:
                    result = conn.query(f"SELECT * FROM {label}")
                    nodes.extend(result.get("nodes", []))
                view["input_contexts"].append({
                    "context_id": ctx_info["context_id"],
                    "name": ctx_info["name"],
                    "type": ctx_info["type"],
                    "role": ctx_info["role"],
                    "node_count": len(nodes),
                    "nodes": nodes,
                })
            except Exception as e:
                logger.debug("Failed to read context %s: %s", ns, e)

        # Collect nodes from execution graph
        try:
            exec_conn = AIContextDBConnection(namespace=boundary.execution_namespace, graph_registry=self._graph_registry)
            exec_nodes = []
            for label in _EXEC_LABELS:
                result = exec_conn.query(f"SELECT * FROM {label}")
                exec_nodes.extend(result.get("nodes", []))
            view["execution"]["nodes"] = exec_nodes
        except Exception as e:
            logger.debug("Failed to read execution graph: %s", e)

        # Collect nodes from runtime graph (agent-produced work)
        _RUNTIME_LABELS = ["Task", "Finding", "Insight", "Decision", "AgentAction",
                           "Observation", "Note", "Pattern", "Action"]
        try:
            rt_conn = AIContextDBConnection(namespace=rt_ns, graph_registry=self._graph_registry)
            rt_nodes = []
            for label in _RUNTIME_LABELS:
                result = rt_conn.query(f"SELECT * FROM {label}")
                rt_nodes.extend(result.get("nodes", []))
            view["runtime"]["nodes"] = rt_nodes
        except Exception as e:
            logger.debug("Failed to read runtime graph %s: %s", rt_ns, e)

        return view

    def build_briefing(self, boundary_id: str, agent_id: str = "", agent_name: str = "", max_tokens: int = 6000) -> str:
        """Build an LLM-ready briefing from the unified view."""
        view = self.get_unified_view(boundary_id)
        boundary = view.get("boundary", {})

        lines = [
            f"## Project: {boundary.get('name', '?')}",
            f"Goal: {boundary.get('goal', 'Not specified')}",
            "",
        ]

        # Input contexts
        for ctx in view.get("input_contexts", []):
            lines.append(f"### {ctx['name']} ({ctx['type']}, {ctx['node_count']} items)")
            for node in ctx.get("nodes", [])[:20]:
                title = node.get("title", node.get("name", node.get("path", str(node)[:60])))
                lines.append(f"  - {title}")
            lines.append("")

        # Execution graph
        exec_nodes = view.get("execution", {}).get("nodes", [])
        if exec_nodes:
            from collections import Counter
            type_counts = Counter(n.get("label", "?") for n in exec_nodes)
            lines.append(f"### Execution ({len(exec_nodes)} items)")
            for label, count in type_counts.items():
                lines.append(f"  {label}: {count}")
            lines.append("")

        # Runtime graph — agent-produced work (tasks, findings, actions)
        rt_nodes = view.get("runtime", {}).get("nodes", [])
        all_work_nodes = exec_nodes + rt_nodes  # merge for task display

        tasks = [n for n in all_work_nodes if n.get("label") == "Task"]
        if tasks:
            lines.append("#### Tasks")
            for t in tasks:
                assigned = t.get("assigned_to", "unassigned")
                status = t.get("status", "?")
                lines.append(f"  [{t.get('priority', 'medium').upper()}] {t.get('title', '?')} (assigned: {assigned}, status: {status}, id: {t.get('id', '?')})")
            lines.append("")

        findings = [n for n in rt_nodes if n.get("label") in ("Finding", "Insight", "Observation")]
        if findings:
            lines.append("#### Agent Findings")
            for f in findings[:10]:
                author = f.get("_agent_name", f.get("created_by", "?"))
                lines.append(f"  [{author}] {f.get('name', f.get('content', '?'))[:100]}")
            lines.append("")

        return "\n".join(lines)
