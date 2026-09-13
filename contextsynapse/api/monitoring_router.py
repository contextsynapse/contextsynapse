"""
Monitoring Router
=================
System health, slow queries, and agent activity.

GET /dashboard/monitoring/health   — System health
GET /dashboard/monitoring/queries  — Slow query log
GET /dashboard/monitoring/agents   — Connected agent activity
"""

from __future__ import annotations

import logging
import os
import time
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

_start_time = time.time()


def create_monitoring_router(
    user_registry,
    tenant_registry,
    graph_registry,
    agent_registry=None,
    usage_meter=None,
) -> APIRouter:
    """Create the /dashboard/monitoring router."""

    from .auth import UserAuth

    router = APIRouter(prefix="/dashboard/monitoring", tags=["monitoring"])
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
    # GET /health
    # ------------------------------------------------------------------
    @router.get("/health")
    async def get_health(user=Depends(user_auth)):
        tenant = _get_tenant(user)

        # Uptime
        uptime_seconds = int(time.time() - _start_time)
        hours = uptime_seconds // 3600
        minutes = (uptime_seconds % 3600) // 60
        uptime_str = f"{hours}h {minutes}m" if hours > 0 else f"{minutes}m"

        # Graph stats from metadata (no graph loading needed)
        all_graphs = graph_registry.list_graphs()
        total_nodes = 0
        total_edges = 0
        graph_count = 0
        for g in all_graphs:
            if isinstance(g, dict):
                total_nodes += g.get("num_nodes", 0) or 0
                total_edges += g.get("num_edges", 0) or 0
                graph_count += 1

        # Storage
        from pathlib import Path
        data_dir = Path(__file__).resolve().parents[2] / "contextcore_data"
        storage_used = "—"
        if data_dir.exists():
            try:
                # Quick estimate: only top-level files + first-level subdirs
                total_size = 0
                for item in data_dir.iterdir():
                    if item.is_file():
                        total_size += item.stat().st_size
                    elif item.is_dir():
                        for f in item.iterdir():
                            if f.is_file():
                                total_size += f.stat().st_size
                if total_size > 1_000_000_000:
                    storage_used = f"{total_size / 1_000_000_000:.1f} GB"
                elif total_size > 1_000_000:
                    storage_used = f"{total_size / 1_000_000:.1f} MB"
                else:
                    storage_used = f"{total_size / 1000:.0f} KB"
            except Exception:
                pass

        return {
            "status": "healthy",
            "uptime": uptime_str,
            "graphs_count": graph_count,
            "total_nodes": total_nodes,
            "total_edges": total_edges,
            "storage_used": storage_used,
            "python_version": os.sys.version.split()[0],
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

    # ------------------------------------------------------------------
    # GET /queries — slow query log
    # ------------------------------------------------------------------
    @router.get("/queries")
    async def get_slow_queries(user=Depends(user_auth)):
        tenant = _get_tenant(user)

        queries = []
        try:
            import sqlite3
            from pathlib import Path
            db_path = Path(__file__).resolve().parents[2] / "contextcore_data" / "queries.db"
            if db_path.exists():
                conn = sqlite3.connect(str(db_path))
                conn.row_factory = sqlite3.Row
                # Try tenant-scoped first, then all
                rows = conn.execute(
                    """SELECT query, duration_ms, status, timestamp
                       FROM query_history
                       WHERE tenant_id = ?
                       ORDER BY duration_ms DESC LIMIT 20""",
                    (tenant.tenant_id,),
                ).fetchall()
                if not rows:
                    rows = conn.execute(
                        """SELECT query, duration_ms, status, timestamp
                           FROM query_history
                           ORDER BY timestamp DESC LIMIT 20""",
                    ).fetchall()
                queries = [dict(r) for r in rows]
                conn.close()
        except Exception:
            pass

        return {"queries": queries}

    # ------------------------------------------------------------------
    # GET /agents — connected agent activity
    # ------------------------------------------------------------------
    @router.get("/agents")
    async def get_agent_activity(user=Depends(user_auth)):
        tenant = _get_tenant(user)
        agents = []

        if agent_registry:
            try:
                # Try tenant-scoped, fallback to all agents
                try:
                    all_agents = agent_registry.list_agents(tenant_id=tenant.tenant_id)
                except TypeError:
                    all_agents = agent_registry.list_agents()
                if not all_agents:
                    all_agents = agent_registry.list_agents()
                for a in all_agents:
                    agents.append({
                        "agent_id": getattr(a, "agent_id", str(a)),
                        "name": getattr(a, "name", getattr(a, "agent_id", "Unknown")),
                        "status": getattr(a, "status", "unknown"),
                        "last_seen": getattr(a, "last_seen", None),
                        "platform": getattr(a, "platform", None),
                        "request_count": getattr(a, "request_count", 0),
                    })
            except Exception as e:
                logger.debug("Agent registry query failed: %s", e)

        return {"agents": agents}

    return router
