"""
Queries Router
==============
AIQL query execution, saved queries, and query history.

POST   /dashboard/query/execute       — Execute AIQL query
GET    /dashboard/queries/saved        — List saved queries
POST   /dashboard/queries/saved        — Save a query
DELETE /dashboard/queries/saved/{id}   — Delete saved query
GET    /dashboard/queries/history      — Query execution history
"""

from __future__ import annotations

import json
import logging
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from .models import paginate
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class ExecuteQueryRequest(BaseModel):
    query: str = Field(..., min_length=1, description="AIQL query")
    graph: Optional[str] = Field(None, description="Target graph name")


class SaveQueryRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    query: str = Field(..., min_length=1)
    description: Optional[str] = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_queries_router(
    user_registry,
    tenant_registry,
    graph_registry,
    usage_meter=None,
) -> APIRouter:
    """Create the /dashboard/query and /dashboard/queries routers."""

    from .auth import UserAuth
    from ..aiql.engine import AIQLExecutor

    router = APIRouter(tags=["queries"])
    user_auth = UserAuth(user_registry)

    def _get_tenant(user):
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id:
            raise HTTPException(status_code=404, detail="No workspace found")
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return tenant

    # ------------------------------------------------------------------
    # POST /dashboard/query/execute
    # ------------------------------------------------------------------
    @router.post("/dashboard/query/execute")
    async def execute_query(req: ExecuteQueryRequest, user=Depends(user_auth)):
        tenant = _get_tenant(user)

        try:
            executor = AIQLExecutor(graph_registry=graph_registry)

            # Scope to tenant's graph if specified
            if req.graph:
                scoped_name = f"{tenant.tenant_id}:{req.graph}"
                try:
                    executor.execute(f"USE GRAPH {scoped_name}")
                except Exception:
                    # Try without namespace prefix
                    executor.execute(f"USE GRAPH {req.graph}")

            start = time.time()
            result = executor.execute(req.query)
            duration_ms = int((time.time() - start) * 1000)

            # Record in metering
            if usage_meter:
                try:
                    usage_meter.record(tenant.tenant_id, "query")
                except Exception:
                    pass

            # Record in query history
            _record_history(tenant.tenant_id, user.user_id, req.query, duration_ms, "success")

            # Normalize result for frontend
            if isinstance(result, list):
                data = result
            elif isinstance(result, dict):
                data = result
            else:
                data = str(result)

            # Also return graph nodes/edges for visualization
            nodes_out = []
            edges_out = []
            try:
                scoped_name = f"{tenant.tenant_id}:{req.graph}" if req.graph else None
                graph = graph_registry.get_graph(scoped_name) if scoped_name else None
                if not graph and req.graph:
                    graph = graph_registry.get_graph(req.graph)
                if graph:
                    for n in graph.get_all_nodes():
                        nodes_out.append({
                            "id": n.id, "label": n.label,
                            "name": n.name or (n.properties or {}).get("name", ""),
                            "properties": n.properties or {},
                        })
                    for e in graph.get_all_edges():
                        edges_out.append({
                            "id": e.id, "source": e.source, "target": e.target,
                            "label": e.label, "properties": e.properties or {},
                        })
            except Exception:
                pass

            return {
                "data": data,
                "query": req.query,
                "duration_ms": duration_ms,
                "row_count": len(data) if isinstance(data, list) else None,
                "nodes": nodes_out,
                "edges": edges_out,
            }

        except Exception as e:
            _record_history(tenant.tenant_id, user.user_id, req.query, 0, "error", str(e))
            raise HTTPException(status_code=400, detail=str(e))

    # ------------------------------------------------------------------
    # Saved queries — stored in user_registry's DB
    # ------------------------------------------------------------------

    @router.get("/dashboard/queries/saved")
    async def list_saved(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        user=Depends(user_auth),
    ):
        tenant = _get_tenant(user)
        queries = _get_saved_queries(tenant.tenant_id, user.user_id)
        page = paginate(queries, limit, offset)
        return {"queries": page["items"], "total": page["total"], "limit": page["limit"], "offset": page["offset"]}

    @router.post("/dashboard/queries/saved")
    async def save_query(req: SaveQueryRequest, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        query_id = secrets.token_hex(12)
        now = datetime.now(timezone.utc).isoformat()
        _insert_saved_query(query_id, tenant.tenant_id, user.user_id, req.name, req.query, req.description, now)
        return {"query_id": query_id, "message": "Query saved"}

    @router.delete("/dashboard/queries/saved/{query_id}")
    async def delete_saved(query_id: str, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        _delete_saved_query(query_id, tenant.tenant_id)
        return {"message": "Query deleted"}

    # ------------------------------------------------------------------
    # Query history
    # ------------------------------------------------------------------

    @router.get("/dashboard/queries/history")
    async def get_history(
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        user=Depends(user_auth),
    ):
        tenant = _get_tenant(user)
        history = _get_query_history(tenant.tenant_id, user.user_id)
        page = paginate(history, limit, offset)
        return {"history": page["items"], "total": page["total"], "limit": page["limit"], "offset": page["offset"]}

    # ------------------------------------------------------------------
    # SQLite helpers (reuse user_registry's DB connection pattern)
    # ------------------------------------------------------------------
    import sqlite3
    from pathlib import Path

    _db_path = Path(__file__).resolve().parents[2] / "contextcore_data" / "queries.db"
    _db_path.parent.mkdir(parents=True, exist_ok=True)

    def _get_conn():
        conn = sqlite3.connect(str(_db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    # Init tables
    with _get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS saved_queries (
                query_id TEXT PRIMARY KEY,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                name TEXT NOT NULL,
                query TEXT NOT NULL,
                description TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_saved_queries_tenant
                ON saved_queries(tenant_id, user_id);

            CREATE TABLE IF NOT EXISTS query_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                tenant_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                query TEXT NOT NULL,
                duration_ms INTEGER DEFAULT 0,
                status TEXT DEFAULT 'success',
                error TEXT,
                timestamp TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_query_history_tenant
                ON query_history(tenant_id, user_id);
        """)

    def _get_saved_queries(tenant_id, user_id):
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM saved_queries WHERE tenant_id = ? AND user_id = ? ORDER BY created_at DESC",
                (tenant_id, user_id),
            ).fetchall()
        return [dict(r) for r in rows]

    def _insert_saved_query(query_id, tenant_id, user_id, name, query, description, created_at):
        with _get_conn() as conn:
            conn.execute(
                "INSERT INTO saved_queries (query_id, tenant_id, user_id, name, query, description, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (query_id, tenant_id, user_id, name, query, description, created_at),
            )

    def _delete_saved_query(query_id, tenant_id):
        with _get_conn() as conn:
            conn.execute("DELETE FROM saved_queries WHERE query_id = ? AND tenant_id = ?", (query_id, tenant_id))

    def _record_history(tenant_id, user_id, query, duration_ms, status, error=None):
        try:
            now = datetime.now(timezone.utc).isoformat()
            with _get_conn() as conn:
                conn.execute(
                    "INSERT INTO query_history (tenant_id, user_id, query, duration_ms, status, error, timestamp) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (tenant_id, user_id, query, duration_ms, status, error, now),
                )
                # Keep only last 100 entries per user
                conn.execute(
                    """DELETE FROM query_history WHERE id NOT IN (
                        SELECT id FROM query_history WHERE tenant_id = ? AND user_id = ?
                        ORDER BY timestamp DESC LIMIT 100
                    ) AND tenant_id = ? AND user_id = ?""",
                    (tenant_id, user_id, tenant_id, user_id),
                )
        except Exception:
            pass

    def _get_query_history(tenant_id, user_id):
        with _get_conn() as conn:
            rows = conn.execute(
                "SELECT query, duration_ms, status, error, timestamp FROM query_history WHERE tenant_id = ? AND user_id = ? ORDER BY timestamp DESC LIMIT 20",
                (tenant_id, user_id),
            ).fetchall()
        return [dict(r) for r in rows]

    return router
