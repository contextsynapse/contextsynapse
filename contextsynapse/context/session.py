"""
Context Sessions
================
Named, persistent workspaces where multiple agents can read/write shared
context backed by graph + vector + document stores.
"""

from __future__ import annotations

import json
import logging
import secrets
import sqlite3
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Prefix to avoid collisions with user-created namespaces
SESSION_NS_PREFIX = "ctx_"


@dataclass
class ContextSession:
    """A shared context workspace."""
    session_id: str
    name: str
    created_at: str
    updated_at: str
    owner_agent_id: Optional[str] = None
    graph_namespace: str = ""
    runtime_namespace: str = ""    # separate graph for agent work (tasks, findings, actions)
    vector_collection: str = ""
    document_collection: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    status: str = "active"  # active, archived, deleted
    region: str = "local"   # region where session was created

    @property
    def security_config(self) -> Dict[str, Any]:
        """Get the runtime security configuration for this session.

        Stored in config['security']. Controls how agents access data.
        """
        defaults = {
            "audit_trail": True,
            "redaction_mode": "mask",
            "agent_clearances": {},  # agent_id → {level, pii_access}
        }
        return {**defaults, **self.config.get("security", {})}

    @security_config.setter
    def security_config(self, value: Dict[str, Any]):
        self.config["security"] = value

    def get_agent_clearance(self, agent_id: str) -> Dict[str, Any]:
        """Get clearance for a specific agent in this session."""
        clearances = self.security_config.get("agent_clearances", {})
        return clearances.get(agent_id, {"level": "public", "pii_access": []})

    def set_agent_clearance(self, agent_id: str, level: str = "public",
                             pii_access: Optional[List[str]] = None):
        """Set clearance for an agent in this session."""
        sec = self.config.get("security", {})
        clearances = sec.get("agent_clearances", {})
        clearances[agent_id] = {"level": level, "pii_access": pii_access or []}
        sec["agent_clearances"] = clearances
        self.config["security"] = sec

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ContextSessionManager:
    """
    Manages context sessions and their backing stores.

    Each session maps 1-to-1 to:
      - A graph namespace  (via GraphRegistry)
      - A vector collection (name only — actual vector ops are done by caller)
      - A document collection (name only)

    Access control is stored in the same SQLite DB as the agent registry
    (``contextcore_data/context.db``).
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS context_sessions (
        session_id TEXT PRIMARY KEY,
        name TEXT NOT NULL UNIQUE,
        created_at TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        owner_agent_id TEXT,
        graph_namespace TEXT NOT NULL,
        runtime_namespace TEXT DEFAULT '',
        vector_collection TEXT NOT NULL,
        document_collection TEXT NOT NULL,
        config TEXT DEFAULT '{}',
        status TEXT DEFAULT 'active'
    );

    CREATE TABLE IF NOT EXISTS session_access (
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        access_level TEXT NOT NULL DEFAULT 'read',
        allowed_tags TEXT DEFAULT '[]',
        granted_at TEXT NOT NULL,
        PRIMARY KEY (session_id, agent_id)
    );

    CREATE TABLE IF NOT EXISTS session_contexts (
        session_id TEXT NOT NULL,
        context_id TEXT NOT NULL,
        attached_at TEXT NOT NULL,
        role TEXT DEFAULT 'input',
        last_synced_at TEXT,
        PRIMARY KEY (session_id, context_id)
    );

    CREATE INDEX IF NOT EXISTS idx_sessions_status ON context_sessions(status);
    CREATE INDEX IF NOT EXISTS idx_sessions_owner ON context_sessions(owner_agent_id);
    CREATE INDEX IF NOT EXISTS idx_access_agent ON session_access(agent_id);
    CREATE INDEX IF NOT EXISTS idx_session_contexts_ctx ON session_contexts(context_id);
    """

    _MIGRATIONS = [
        # v1 → v2: add allowed_tags column to session_access
        "ALTER TABLE session_access ADD COLUMN allowed_tags TEXT DEFAULT '[]'",
        # v2 → v3: add region column for multi-region support
        "ALTER TABLE context_sessions ADD COLUMN region TEXT DEFAULT 'local'",
        # v3 → v4: runtime graph for agent work (tasks, findings, actions)
        "ALTER TABLE context_sessions ADD COLUMN runtime_namespace TEXT DEFAULT ''",
    ]

    _POST_SCHEMA = """
    CREATE TABLE IF NOT EXISTS session_join_requests (
        request_id TEXT PRIMARY KEY,
        session_id TEXT NOT NULL,
        agent_id TEXT NOT NULL,
        requested_level TEXT NOT NULL DEFAULT 'read',
        status TEXT NOT NULL DEFAULT 'pending',
        reason TEXT,
        reviewed_by TEXT,
        reviewed_at TEXT,
        created_at TEXT NOT NULL,
        expires_at TEXT,
        UNIQUE(session_id, agent_id, status)
    );

    CREATE INDEX IF NOT EXISTS idx_join_req_session ON session_join_requests(session_id);
    CREATE INDEX IF NOT EXISTS idx_join_req_agent ON session_join_requests(agent_id);
    CREATE INDEX IF NOT EXISTS idx_join_req_status ON session_join_requests(status);
    """

    def __init__(self, db_path: str = "contextcore_data/context.db", graph_registry=None):
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(self._SCHEMA)
        self._conn.executescript(self._POST_SCHEMA)
        self._run_migrations()
        self._graph_registry = graph_registry

    def _run_migrations(self):
        """Apply schema migrations idempotently."""
        for sql in self._MIGRATIONS:
            try:
                self._conn.execute(sql)
                self._conn.commit()
            except sqlite3.OperationalError:
                pass  # column/table already exists

    # ------------------------------------------------------------------
    # Session CRUD
    # ------------------------------------------------------------------

    def create_session(
        self,
        name: str,
        owner_agent_id: Optional[str] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> ContextSession:
        """Create a new context session with backing stores."""
        # Check for duplicate name (among active sessions)
        existing = self._conn.execute(
            "SELECT session_id FROM context_sessions WHERE name = ? AND status = 'active'",
            (name,),
        ).fetchone()
        if existing:
            raise ValueError(f"A boundary with name '{name}' already exists")

        session_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()
        # Sanitize name for use as filesystem path (Windows forbids : ? * etc.)
        import re
        safe_name = re.sub(r"[^a-zA-Z0-9_-]", "_", name)
        # Include session_id prefix for unique namespace
        ns = f"{SESSION_NS_PREFIX}{safe_name}_{session_id[:8]}"
        rt_ns = f"{ns}_rt"  # runtime graph for agent work (tasks, findings, actions)

        from contextsynapse.core.write_context import REGION
        session = ContextSession(
            session_id=session_id,
            name=name,
            created_at=now,
            updated_at=now,
            owner_agent_id=owner_agent_id,
            graph_namespace=ns,
            runtime_namespace=rt_ns,
            vector_collection=ns,
            document_collection=ns,
            config=config or {},
            status="active",
            region=REGION,
        )

        # Graph namespace is reserved but NOT created until a context is attached
        # or data is ingested. This keeps session creation lightweight.

        # Persist session
        self._conn.execute(
            "INSERT INTO context_sessions "
            "(session_id, name, created_at, updated_at, owner_agent_id, "
            "graph_namespace, runtime_namespace, vector_collection, document_collection, config, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session.session_id,
                session.name,
                session.created_at,
                session.updated_at,
                session.owner_agent_id,
                session.graph_namespace,
                session.runtime_namespace,
                session.vector_collection,
                session.document_collection,
                json.dumps(session.config),
                session.status,
            ),
        )

        # Owner gets admin access automatically
        if owner_agent_id:
            self._conn.execute(
                "INSERT OR REPLACE INTO session_access (session_id, agent_id, access_level, granted_at) "
                "VALUES (?, ?, 'admin', ?)",
                (session_id, owner_agent_id, now),
            )

        self._conn.commit()

        # Graph-first: add Session root node in BOTH the session's own graph AND default graph
        if self._graph_registry:
            from ..core.graph_structures import GraphNode
            session_props = {
                "name": session.name,
                "graph_namespace": session.graph_namespace,
                "owner_agent_id": session.owner_agent_id or "",
                "status": session.status,
                "created_at": session.created_at,
            }

            # Root node in session's own graph
            try:
                session_graph = self._graph_registry.get_graph(session.graph_namespace)
                if session_graph:
                    session_graph.add_node(GraphNode(
                        id=session.session_id, label="Session", properties=session_props,
                    ), write_through=True)
            except Exception:
                pass

            # Reference node in default graph
            try:
                default_graph = self._graph_registry.get_graph("default") or self._graph_registry.create_graph("default")
                default_graph.add_node(GraphNode(
                    id=session.session_id, label="Session", properties=session_props,
                ), write_through=True)
            except Exception:
                pass

        return session

    def rotate_session_id(self, old_session_id: str) -> Optional[str]:
        """Rotate session ID — generates new ID, old one stops working.

        Updates all references: session_access, session_contexts, join_requests.
        Returns the new session_id, or None if session not found.
        """
        session = self.get_session(old_session_id)
        if not session:
            return None

        new_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()

        # Update main session record
        self._conn.execute(
            "UPDATE context_sessions SET session_id = ?, updated_at = ? WHERE session_id = ?",
            (new_id, now, old_session_id),
        )
        # Update access table
        self._conn.execute(
            "UPDATE session_access SET session_id = ? WHERE session_id = ?",
            (new_id, old_session_id),
        )
        # Update session_contexts
        self._conn.execute(
            "UPDATE session_contexts SET session_id = ? WHERE session_id = ?",
            (new_id, old_session_id),
        )
        # Update join requests
        try:
            self._conn.execute(
                "UPDATE session_join_requests SET session_id = ? WHERE session_id = ?",
                (new_id, old_session_id),
            )
        except Exception:
            pass
        self._conn.commit()

        # Update graph node if exists
        if self._graph_registry:
            try:
                graph = self._graph_registry.get_graph(session.graph_namespace)
                if graph:
                    old_node = graph.get_node(old_session_id)
                    if old_node:
                        from ..core.graph_structures import GraphNode
                        graph.add_node(GraphNode(
                            id=new_id, label="Session",
                            properties={**old_node.properties, "rotated_from": old_session_id, "rotated_at": now},
                        ), write_through=True)
                        try:
                            graph.remove_node(old_session_id)
                        except Exception:
                            pass
            except Exception:
                pass

        logger.info("Rotated session ID: %s → %s", old_session_id[:8], new_id[:8])
        return new_id

    def get_session(self, session_id: str) -> Optional[ContextSession]:
        row = self._conn.execute(
            "SELECT * FROM context_sessions WHERE session_id = ? AND status != 'deleted'",
            (session_id,),
        ).fetchone()
        return self._row_to_session(row) if row else None

    def get_session_by_name(self, name: str) -> Optional[ContextSession]:
        row = self._conn.execute(
            "SELECT * FROM context_sessions WHERE name = ? AND status != 'deleted'",
            (name,),
        ).fetchone()
        return self._row_to_session(row) if row else None

    def list_sessions(self, agent_id: Optional[str] = None, user_id: Optional[str] = None) -> List[ContextSession]:
        """List sessions, optionally filtered to those a user/agent has access to.

        When *user_id* is provided the result includes sessions where the user
        is the owner **or** has been granted access via ``session_access``.
        """
        if user_id:
            rows = self._conn.execute(
                "SELECT DISTINCT cs.* FROM context_sessions cs "
                "LEFT JOIN session_access sa ON cs.session_id = sa.session_id "
                "WHERE cs.status != 'deleted' "
                "AND (cs.owner_agent_id = ? OR sa.agent_id = ?) "
                "ORDER BY cs.updated_at DESC",
                (user_id, user_id),
            ).fetchall()
        elif agent_id:
            rows = self._conn.execute(
                "SELECT cs.* FROM context_sessions cs "
                "JOIN session_access sa ON cs.session_id = sa.session_id "
                "WHERE sa.agent_id = ? AND cs.status != 'deleted' "
                "ORDER BY cs.updated_at DESC",
                (agent_id,),
            ).fetchall()
        else:
            rows = self._conn.execute(
                "SELECT * FROM context_sessions WHERE status != 'deleted' ORDER BY updated_at DESC"
            ).fetchall()
        return [self._row_to_session(r) for r in rows]

    def update_session(self, session_id: str, config: Optional[Dict[str, Any]] = None) -> bool:
        """Update session fields (currently config)."""
        now = datetime.now(timezone.utc).isoformat()
        if config is not None:
            self._conn.execute(
                "UPDATE context_sessions SET config = ?, updated_at = ? WHERE session_id = ?",
                (json.dumps(config), now, session_id),
            )
            self._conn.commit()
            return True
        return False

    def delete_session(self, session_id: str) -> bool:
        """Soft-delete a session."""
        # Final promotion sweep before deleting session
        try:
            session = self.get_session(session_id)
            if session:
                from .promotion import PromotionScorer, PromotionQueue, promote_node, PROMOTION_DEFAULTS
                rt_ns = getattr(session, 'runtime_namespace', '') or f"{session.graph_namespace}_rt"
                at_ns = session.graph_namespace
                # Get graph registry from global
                from ..core.registry import GraphRegistry
                import os
                reg = GraphRegistry(storage_dir=os.environ.get("CONTEXTSYNAPSE_STORAGE_DIR") or os.environ.get("AICONTEXTDB_STORAGE_DIR", "contextcore_data"))
                rt_graph = reg.get_graph(rt_ns, load_if_missing=False)
                if rt_graph:
                    scorer = PromotionScorer(PROMOTION_DEFAULTS)
                    for label in ("Finding", "Insight", "Observation", "Note"):
                        for n in rt_graph.get_all_nodes(label=label):
                            props = n.properties or {}
                            if props.get("_promotion_scored_at"):
                                continue
                            score = scorer.score(n, rt_graph)
                            if score.score >= 0.3:
                                try:
                                    from ..adapters._base import AIContextDBConnection
                                    at_conn = AIContextDBConnection(namespace=at_ns)
                                    rt_conn = AIContextDBConnection(namespace=rt_ns)
                                    promote_node(n.id, rt_conn, at_conn, promoted_by=props.get("_agent_name", "system"))
                                except Exception:
                                    pass
        except Exception:
            pass
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "UPDATE context_sessions SET status = 'deleted', updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def touch_session(self, session_id: str):
        """Update the updated_at timestamp."""
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE context_sessions SET updated_at = ? WHERE session_id = ?",
            (now, session_id),
        )
        self._conn.commit()

    # ------------------------------------------------------------------
    # Access control
    # ------------------------------------------------------------------

    def grant_access(
        self,
        session_id: str,
        agent_id: str,
        level: str = "",
        allowed_tags: Optional[List[str]] = None,
        agent_registry=None,
    ) -> bool:
        """Grant access with optional tag-based scope.

        If level is empty, it's auto-derived from the agent's role/capabilities:
          - admin role or 'admin' capability → 'admin'
          - 'write' capability → 'write'
          - fallback → 'read'
        """
        # Auto-derive level from agent registration
        if not level and agent_registry:
            try:
                agent = agent_registry.get(agent_id)
                if agent:
                    caps = agent.capabilities or []
                    if agent.role == "admin" or "admin" in caps:
                        level = "admin"
                    elif "write" in caps:
                        level = "write"
                    else:
                        level = "read"
            except Exception:
                pass
        if not level:
            level = "read"
        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "INSERT OR REPLACE INTO session_access "
            "(session_id, agent_id, access_level, allowed_tags, granted_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, agent_id, level, json.dumps(allowed_tags or []), now),
        )
        self._conn.commit()
        return True

    def revoke_access(self, session_id: str, agent_id: str) -> bool:
        cur = self._conn.execute(
            "DELETE FROM session_access WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def check_access(self, session_id: str, agent_id: str, required: str = "read") -> bool:
        """Check if agent has the required access level.

        Levels: read < write < admin.
        """
        row = self._conn.execute(
            "SELECT access_level FROM session_access WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
        if not row:
            return False

        levels = {"read": 0, "write": 1, "admin": 2}
        return levels.get(row["access_level"], -1) >= levels.get(required, 99)

    def get_agent_access(self, session_id: str, agent_id: str) -> Optional[Dict[str, Any]]:
        """Return the full access record for an agent on a session.

        Returns ``{"access_level": str, "allowed_tags": List[str]}`` or None.
        """
        row = self._conn.execute(
            "SELECT access_level, allowed_tags FROM session_access "
            "WHERE session_id = ? AND agent_id = ?",
            (session_id, agent_id),
        ).fetchone()
        if not row:
            return None
        return {
            "access_level": row["access_level"],
            "allowed_tags": json.loads(row["allowed_tags"]) if row["allowed_tags"] else [],
        }

    def get_access_list(self, session_id: str) -> List[Dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT agent_id, access_level, allowed_tags, granted_at FROM session_access WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        result = []
        for r in rows:
            entry = dict(r)
            entry["allowed_tags"] = json.loads(entry.get("allowed_tags") or "[]")
            result.append(entry)
        return result

    # ------------------------------------------------------------------
    # Join requests (approval gate)
    # ------------------------------------------------------------------

    def request_join(
        self,
        session_id: str,
        agent_id: str,
        level: str = "read",
        reason: Optional[str] = None,
        expires_in_hours: int = 72,
    ) -> Dict[str, Any]:
        """Agent requests to join a session. Returns the request record.

        The request stays pending until approved by the session owner or
        an agent with admin access.  If the requesting agent already has
        access, this is a no-op that returns the existing access info.
        """
        # Already has access?
        if self.check_access(session_id, agent_id, "read"):
            return {"status": "already_granted", "agent_id": agent_id, "session_id": session_id}

        # Already has a pending request?
        existing = self._conn.execute(
            "SELECT * FROM session_join_requests "
            "WHERE session_id = ? AND agent_id = ? AND status = 'pending'",
            (session_id, agent_id),
        ).fetchone()
        if existing:
            return dict(existing)

        request_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc)
        expires_at = (
            datetime(now.year, now.month, now.day, now.hour, now.minute, now.second, tzinfo=timezone.utc)
        )
        from datetime import timedelta
        expires_at = (now + timedelta(hours=expires_in_hours)).isoformat()

        self._conn.execute(
            "INSERT INTO session_join_requests "
            "(request_id, session_id, agent_id, requested_level, status, reason, created_at, expires_at) "
            "VALUES (?, ?, ?, ?, 'pending', ?, ?, ?)",
            (request_id, session_id, agent_id, level, reason, now.isoformat(), expires_at),
        )
        self._conn.commit()
        return {
            "request_id": request_id,
            "session_id": session_id,
            "agent_id": agent_id,
            "requested_level": level,
            "status": "pending",
            "reason": reason,
            "created_at": now.isoformat(),
            "expires_at": expires_at,
        }

    def approve_join(
        self,
        request_id: str,
        reviewed_by: str,
        level: Optional[str] = None,
        allowed_tags: Optional[List[str]] = None,
    ) -> bool:
        """Approve a pending join request. Grants access automatically."""
        row = self._conn.execute(
            "SELECT * FROM session_join_requests WHERE request_id = ? AND status = 'pending'",
            (request_id,),
        ).fetchone()
        if not row:
            return False

        now = datetime.now(timezone.utc).isoformat()
        self._conn.execute(
            "UPDATE session_join_requests SET status = 'approved', reviewed_by = ?, reviewed_at = ? "
            "WHERE request_id = ?",
            (reviewed_by, now, request_id),
        )

        # Grant actual access
        access_level = level or row["requested_level"]
        self.grant_access(row["session_id"], row["agent_id"], access_level, allowed_tags)
        return True

    def deny_join(self, request_id: str, reviewed_by: str) -> bool:
        """Deny a pending join request."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "UPDATE session_join_requests SET status = 'denied', reviewed_by = ?, reviewed_at = ? "
            "WHERE request_id = ? AND status = 'pending'",
            (reviewed_by, now, request_id),
        )
        self._conn.commit()
        return cur.rowcount > 0

    def get_pending_requests(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all pending join requests for a session."""
        rows = self._conn.execute(
            "SELECT * FROM session_join_requests WHERE session_id = ? AND status = 'pending' "
            "ORDER BY created_at ASC",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_agent_requests(self, agent_id: str) -> List[Dict[str, Any]]:
        """Get all join requests by an agent."""
        rows = self._conn.execute(
            "SELECT * FROM session_join_requests WHERE agent_id = ? ORDER BY created_at DESC",
            (agent_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def cleanup_expired_requests(self) -> int:
        """Remove expired pending requests. Returns count removed."""
        now = datetime.now(timezone.utc).isoformat()
        cur = self._conn.execute(
            "UPDATE session_join_requests SET status = 'expired' "
            "WHERE status = 'pending' AND expires_at < ?",
            (now,),
        )
        self._conn.commit()
        return cur.rowcount

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _row_to_session(self, row: sqlite3.Row) -> ContextSession:
        # region column may not exist in old DBs before migration
        try:
            region = row["region"]
        except (IndexError, KeyError):
            region = "local"
        # runtime_namespace added in v4 migration
        try:
            runtime_ns = row["runtime_namespace"] or ""
        except (IndexError, KeyError):
            runtime_ns = ""
        # Backfill: derive runtime_namespace from graph_namespace for old sessions
        if not runtime_ns:
            runtime_ns = f"{row['graph_namespace']}_rt"
        return ContextSession(
            session_id=row["session_id"],
            name=row["name"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            owner_agent_id=row["owner_agent_id"],
            graph_namespace=row["graph_namespace"],
            runtime_namespace=runtime_ns,
            vector_collection=row["vector_collection"],
            document_collection=row["document_collection"],
            config=json.loads(row["config"]) if row["config"] else {},
            status=row["status"],
            region=region or "local",
        )

    # ------------------------------------------------------------------
    # Session ↔ Context linkage
    # ------------------------------------------------------------------

    def attach_context(self, session_id: str, context_id: str, role: str = "input") -> bool:
        """Attach a context to a session — merges context graph into runtime graph."""
        now = datetime.now(timezone.utc).isoformat()
        try:
            self._conn.execute(
                "INSERT OR REPLACE INTO session_contexts (session_id, context_id, attached_at, role) "
                "VALUES (?, ?, ?, ?)",
                (session_id, context_id, now, role),
            )
            self._conn.commit()

            if self._graph_registry:
                try:
                    session = self.get_session(session_id)
                    if not session:
                        return True

                    from ..core.graph_structures import GraphNode, GraphEdge
                    import uuid as _uuid

                    runtime_graph = self._graph_registry.get_graph(session.graph_namespace)
                    if not runtime_graph:
                        runtime_graph = self._graph_registry.create_graph(session.graph_namespace)
                    if not runtime_graph:
                        return True

                    # Ensure Session root node
                    session_root_id = session_id
                    existing_sessions = [n for n in runtime_graph.get_all_nodes()
                                         if getattr(n, "label", "") == "Session"]
                    if existing_sessions:
                        session_root_id = existing_sessions[0].id
                    elif not runtime_graph.get_node(session_id):
                        runtime_graph.add_node(GraphNode(
                            id=session_id, label="Session",
                            properties={"name": session.name, "status": "active",
                                        "created_at": session.created_at},
                        ), write_through=True)

                    # Get context details
                    ctx_name = context_id[:12]
                    ctx_ns = ""
                    ctx_type = ""
                    ctx_desc = ""
                    ctx_items = 0
                    ctx_tokens = 0
                    ctx_tags: list = []
                    ctx_sensitivity = ""
                    try:
                        from .context_manager import ContextManager
                        cm = ContextManager(graph_registry=self._graph_registry)
                        ctx_obj = cm.get_context(context_id)
                        if ctx_obj:
                            ctx_name = ctx_obj.name
                            ctx_ns = ctx_obj.graph_namespace
                            ctx_type = ctx_obj.context_type or ""
                            ctx_desc = ctx_obj.description or ""
                            ctx_items = ctx_obj.item_count or 0
                            ctx_tokens = ctx_obj.estimated_tokens or 0
                            ctx_tags = ctx_obj.tags or []
                            ctx_sensitivity = ctx_obj.sensitivity or ""
                    except Exception:
                        pass

                    # Add ContextRef node with full context details
                    runtime_graph.add_node(GraphNode(
                        id=context_id, label="ContextRef",
                        properties={
                            "name": ctx_name,
                            "context_id": context_id,
                            "graph_namespace": ctx_ns,
                            "context_type": ctx_type,
                            "description": ctx_desc,
                            "item_count": ctx_items,
                            "estimated_tokens": ctx_tokens,
                            "tags": ctx_tags,
                            "sensitivity": ctx_sensitivity,
                            "role": role,
                            "attached_at": now,
                        },
                    ), write_through=True)
                    # Only add edge if it doesn't already exist (avoid duplicates on re-attach)
                    existing_edges = runtime_graph.get_all_edges()
                    has_edge = any(
                        e.source == session_root_id and e.target == context_id and e.label == "HAS_CONTEXT"
                        for e in existing_edges
                    )
                    if not has_edge:
                        runtime_graph.add_edge(GraphEdge(
                            id=str(_uuid.uuid4()), source=session_root_id, target=context_id,
                            label="HAS_CONTEXT", properties={"role": role},
                        ))

                    # ── REFERENCE-ONLY: store graph namespace, don't copy data ──
                    # Agents search across all attached graphs via fan-out.
                    # This is instant — no node/edge copying needed.
                    if ctx_ns:
                        log = logging.getLogger(__name__)
                        log.info("[SESSION] Attached context '%s' (%s) to runtime '%s' (reference-only, no merge)",
                                 ctx_name, ctx_ns, session.graph_namespace)

                    # Save — create a checkpoint BEFORE the merge so detach can
                    # restore to this exact state (like a git commit before a change)
                    try:
                        self._graph_registry.save_graph(
                            session.graph_namespace, create_checkpoint=True,
                        )
                        # Tag the checkpoint with the context being attached
                        try:
                            from ..core.checkpoint import checkpoint_manager
                            cps = checkpoint_manager.get_checkpoints(session.graph_namespace)
                            if cps:
                                cps[0].message = f"pre-attach:{context_id}"
                                checkpoint_manager._save_checkpoint_index(session.graph_namespace)
                        except Exception:
                            pass
                    except Exception:
                        pass

                except Exception as e:
                    logging.getLogger(__name__).debug("Graph merge for attach_context failed: %s", e)

            return True
        except Exception:
            return False

    def get_attached_contexts(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all contexts attached to a session with their graph namespaces."""
        rows = self._conn.execute(
            "SELECT context_id, role, attached_at FROM session_contexts WHERE session_id = ?",
            (session_id,),
        ).fetchall()
        result = []
        for row in rows:
            ctx_id = row["context_id"]
            entry = {"context_id": ctx_id, "role": row["role"], "attached_at": row["attached_at"]}
            # Resolve full context details
            try:
                from .context_manager import ContextManager
                cm = ContextManager(graph_registry=self._graph_registry)
                ctx_obj = cm.get_context(ctx_id)
                if ctx_obj:
                    entry["name"] = ctx_obj.name
                    entry["graph_namespace"] = ctx_obj.graph_namespace
                    entry["context_type"] = ctx_obj.context_type or ""
                    entry["description"] = ctx_obj.description or ""
                    entry["item_count"] = ctx_obj.item_count or 0
                    entry["estimated_tokens"] = ctx_obj.estimated_tokens or 0
                    entry["tags"] = ctx_obj.tags or []
                    entry["sensitivity"] = ctx_obj.sensitivity or ""
                else:
                    entry["graph_namespace"] = ""
            except Exception:
                entry["graph_namespace"] = ""
            result.append(entry)
        return result

    def detach_context(self, session_id: str, context_id: str) -> bool:
        """Detach a context from a session — removes merged nodes from runtime graph.

        Handles versioned context IDs (e.g. ``abc123:1``) by trying both
        the exact ID and the base ID (without the version suffix).  After
        removing the context's nodes the session graph falls back to its
        base state — only native session nodes remain.
        """
        log = logging.getLogger(__name__)

        # Try exact match first, then base ID (strip version suffix like ":1")
        cur = self._conn.execute(
            "DELETE FROM session_contexts WHERE session_id = ? AND context_id = ?",
            (session_id, context_id),
        )
        self._conn.commit()
        removed = cur.rowcount > 0

        # If exact match failed and ID has a version suffix, try the base ID
        base_context_id = context_id
        if not removed and ":" in context_id:
            base_context_id = context_id.rsplit(":", 1)[0]
            cur = self._conn.execute(
                "DELETE FROM session_contexts WHERE session_id = ? AND context_id = ?",
                (session_id, base_context_id),
            )
            self._conn.commit()
            removed = cur.rowcount > 0
            if removed:
                log.info("[SESSION] Detached via base context_id '%s' (requested '%s')",
                         base_context_id, context_id)

        # Also try matching by prefix if the stored ID has a version but the
        # request doesn't (opposite direction)
        if not removed:
            cur = self._conn.execute(
                "DELETE FROM session_contexts WHERE session_id = ? AND context_id LIKE ?",
                (session_id, f"{context_id}%"),
            )
            self._conn.commit()
            removed = cur.rowcount > 0

        # Revert the graph to its pre-attach state — like `git checkout` to the
        # checkpoint created before this context was merged.
        if removed and self._graph_registry:
            try:
                session = self.get_session(session_id)
                if session:
                    ns = session.graph_namespace
                    restored_from_checkpoint = False

                    # Strategy 1: Restore from the pre-attach checkpoint (clean revert)
                    try:
                        from ..core.checkpoint import checkpoint_manager
                        cps = checkpoint_manager.get_checkpoints(ns)
                        # Find the checkpoint tagged with this context attach
                        target_cp = None
                        for cp in cps:
                            msg = cp.message or ""
                            if msg == f"pre-attach:{context_id}" or msg == f"pre-attach:{base_context_id}":
                                target_cp = cp
                                break

                        if target_cp:
                            # Resolve the graph file path
                            ns_path = self._graph_registry._get_namespace_path(ns)
                            if ns_path:
                                result = checkpoint_manager.restore_checkpoint(
                                    ns, target_cp.checkpoint_id, str(ns_path),
                                )
                                if result.get("success"):
                                    # Reload the graph from the restored file
                                    self._graph_registry.load_graph(ns)
                                    restored_from_checkpoint = True
                                    log.info(
                                        "[SESSION] Restored graph '%s' from checkpoint '%s' "
                                        "(pre-attach:%s) — clean revert like git checkout",
                                        ns, target_cp.checkpoint_id, context_id,
                                    )
                    except Exception as e:
                        log.debug("Checkpoint restore failed, falling back to node removal: %s", e)

                    # Strategy 2: Fallback — manually remove tagged nodes
                    if not restored_from_checkpoint:
                        runtime_graph = self._graph_registry.get_graph(ns)
                        if runtime_graph:
                            removed_nodes = 0
                            match_ids = {context_id, base_context_id}

                            to_remove = []
                            for node in runtime_graph.get_all_nodes():
                                props = node.properties if hasattr(node, "properties") else {}
                                source_ctx = props.get("_source_context", "")
                                if source_ctx in match_ids:
                                    to_remove.append(node.id)
                                elif node.id in match_ids:  # ContextRef node
                                    to_remove.append(node.id)
                                elif getattr(node, "label", "") == "ContextRef":
                                    if props.get("context_id", "") in match_ids:
                                        to_remove.append(node.id)

                            for nid in to_remove:
                                try:
                                    runtime_graph.delete_node(nid)
                                    removed_nodes += 1
                                except Exception:
                                    try:
                                        from ..aiql import AIQLExecutor
                                        ex = AIQLExecutor(contextcore=runtime_graph, graph_registry=self._graph_registry)
                                        ex.active_namespace = ns
                                        ex.execute(f'DELETE NODE "{nid}"')
                                        removed_nodes += 1
                                    except Exception:
                                        pass
                                # Clean LMDB regardless of which delete path succeeded
                                try:
                                    from ..search.lmdb_index import lmdb_delete_node
                                    lmdb_delete_node(ns, nid)
                                except Exception:
                                    pass

                            log.info("[SESSION] Detached context '%s' from '%s': removed %d nodes "
                                     "(fallback — no checkpoint found)",
                                     context_id, ns, removed_nodes)

                    # Save the reverted graph (with a new checkpoint)
                    try:
                        self._graph_registry.save_graph(ns, create_checkpoint=True)
                    except Exception:
                        pass
            except Exception as e:
                log.debug("Graph cleanup for detach_context failed: %s", e)

        return removed

    def get_session_context_ids(self, session_id: str) -> List[Dict[str, Any]]:
        """Get all context attachments for a session."""
        rows = self._conn.execute(
            "SELECT context_id, role, attached_at FROM session_contexts WHERE session_id = ? ORDER BY attached_at",
            (session_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_context_session_ids(self, context_id: str) -> List[Dict[str, Any]]:
        """Get all sessions that use a given context."""
        rows = self._conn.execute(
            "SELECT session_id, role, attached_at FROM session_contexts WHERE context_id = ? ORDER BY attached_at",
            (context_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def sync_context_to_boundaries(self, context_id: str, context_manager=None) -> int:
        """Sync new nodes from a context to all boundaries that have it attached.

        Called after ingestion or data addition. Only syncs nodes added since
        the last sync (delta sync, not full re-copy).

        Returns number of nodes synced across all boundaries.
        """
        if not self._graph_registry:
            return 0

        # Find all sessions attached to this context
        sessions = self.get_context_session_ids(context_id)
        if not sessions:
            return 0

        # Get context graph
        ctx_obj = None
        ctx_graph = None
        if context_manager:
            ctx_obj = context_manager.get_context(context_id)
            if ctx_obj:
                ctx_graph = self._graph_registry.get_graph(ctx_obj.graph_namespace, load_if_missing=True)

        if not ctx_graph:
            return 0

        from ..core.graph_structures import GraphNode, GraphEdge
        from ..context.boundaries import BOUNDARY_NODE_LABELS
        import uuid as _sync_uuid

        # Structural labels to skip
        skip_labels = BOUNDARY_NODE_LABELS | {"Context", "KnowledgeBase", "CodeBase",
                                               "SystemStore", "UserStore", "WebStore",
                                               "GeneratedStore", "MemoryStore", "ArtifactStore", "ToolStore"}

        total_synced = 0
        now = datetime.now(timezone.utc).isoformat()

        for att in sessions:
            session_id = att.get("session_id", "")
            session = self.get_session(session_id)
            if not session:
                continue

            boundary_graph = self._graph_registry.get_graph(session.graph_namespace)
            if not boundary_graph:
                continue

            # Get existing node IDs in boundary to avoid duplicates
            existing_ids = set()
            for n in boundary_graph.get_all_nodes():
                existing_ids.add(n.id if hasattr(n, "id") else "")

            # Sync new nodes from context graph
            synced = 0
            for node in ctx_graph.get_all_nodes():
                label = node.label if hasattr(node, "label") else ""
                nid = node.id if hasattr(node, "id") else ""
                if label in skip_labels or nid in existing_ids:
                    continue

                props = node.properties if hasattr(node, "properties") else {}
                synced_props = dict(props)
                synced_props["_source_context"] = context_id
                synced_props["_source_context_name"] = ctx_obj.name if ctx_obj else ""
                synced_props["_synced_at"] = now

                boundary_graph.add_node(GraphNode(
                    id=nid, label=label, properties=synced_props,
                ), write_through=True)

                # Link to ContextRef if it exists
                if context_id in existing_ids:
                    boundary_graph.add_edge(GraphEdge(
                        id=str(_sync_uuid.uuid4()),
                        source=context_id, target=nid,
                        label="CONTAINS", properties={},
                    ))
                synced += 1

            # Sync edges between synced nodes
            all_boundary_ids = existing_ids | {n.id for n in boundary_graph.get_all_nodes() if hasattr(n, "id")}
            for edge in ctx_graph.get_all_edges():
                src = edge.source if hasattr(edge, "source") else edge.get("source", "")
                tgt = edge.target if hasattr(edge, "target") else edge.get("target", "")
                if src in all_boundary_ids and tgt in all_boundary_ids:
                    lbl = edge.label if hasattr(edge, "label") else edge.get("label", "")
                    try:
                        boundary_graph.add_edge(GraphEdge(
                            id=str(_sync_uuid.uuid4()),
                            source=src, target=tgt, label=lbl, properties={},
                        ))
                    except Exception:
                        pass

            if synced > 0:
                try:
                    self._graph_registry.save_graph(session.graph_namespace, create_checkpoint=False)
                except Exception:
                    pass
                total_synced += synced

            # Update last_synced_at
            try:
                self._conn.execute(
                    "UPDATE session_contexts SET last_synced_at = ? WHERE session_id = ? AND context_id = ?",
                    (now, session_id, context_id),
                )
                self._conn.commit()
            except Exception:
                pass

        if total_synced > 0:
            logging.getLogger(__name__).info(
                "Reactive sync: %d nodes from context %s to %d boundaries",
                total_synced, context_id[:12], len(sessions),
            )

        return total_synced

    def close(self):
        if self._conn:
            self._conn.close()


# Alias for simpler usage in demos and scripts
SessionStore = ContextSessionManager
