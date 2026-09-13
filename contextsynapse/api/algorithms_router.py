"""
Graph Algorithms Router
=======================
Endpoints for running graph algorithms on stored graphs.

GET  /dashboard/graphs/{name}/algorithms           — List available algorithms
POST /dashboard/graphs/{name}/algorithms/shortest-path  — Shortest path between two nodes
POST /dashboard/graphs/{name}/algorithms/pagerank       — PageRank centrality
POST /dashboard/graphs/{name}/algorithms/components     — Connected components
POST /dashboard/graphs/{name}/algorithms/neighbors      — N-hop neighborhood
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict, deque
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class ShortestPathRequest(BaseModel):
    source: str = Field(..., description="Source node ID")
    target: str = Field(..., description="Target node ID")
    max_depth: int = Field(20, ge=1, le=100)


class PageRankRequest(BaseModel):
    damping: float = Field(0.85, ge=0.0, le=1.0)
    iterations: int = Field(30, ge=1, le=200)
    top_k: int = Field(20, ge=1, le=500)


class ComponentsRequest(BaseModel):
    min_size: int = Field(1, ge=1, description="Minimum component size to return")


class NeighborsRequest(BaseModel):
    node_id: str = Field(..., description="Center node ID")
    hops: int = Field(2, ge=1, le=5)
    limit: int = Field(100, ge=1, le=1000)


# ---------------------------------------------------------------------------
# Pure-Python graph algorithms (no networkx dependency at runtime)
# ---------------------------------------------------------------------------

def _build_adjacency(nodes, edges):
    """Build adjacency list from nodes/edges."""
    adj = defaultdict(set)
    node_ids = {n.id for n in nodes}
    for e in edges:
        src = getattr(e, "source", None) or getattr(e, "source_id", None)
        tgt = getattr(e, "target", None) or getattr(e, "target_id", None)
        if src in node_ids and tgt in node_ids:
            adj[src].add(tgt)
            adj[tgt].add(src)  # undirected for algorithms
    return adj, node_ids


def _shortest_path(adj, source, target, max_depth):
    """BFS shortest path."""
    if source == target:
        return [source]
    visited = {source}
    queue = deque([(source, [source])])
    while queue:
        current, path = queue.popleft()
        if len(path) > max_depth:
            break
        for neighbor in adj.get(current, []):
            if neighbor == target:
                return path + [neighbor]
            if neighbor not in visited:
                visited.add(neighbor)
                queue.append((neighbor, path + [neighbor]))
    return None


def _pagerank(adj, node_ids, damping, iterations):
    """Simple PageRank."""
    n = len(node_ids)
    if n == 0:
        return {}
    nodes_list = list(node_ids)
    rank = {nid: 1.0 / n for nid in nodes_list}
    for _ in range(iterations):
        new_rank = {}
        for nid in nodes_list:
            incoming_sum = 0.0
            for other in nodes_list:
                if nid in adj.get(other, set()):
                    out_degree = len(adj.get(other, set()))
                    if out_degree > 0:
                        incoming_sum += rank[other] / out_degree
            new_rank[nid] = (1 - damping) / n + damping * incoming_sum
        rank = new_rank
    return rank


def _connected_components(adj, node_ids):
    """Find connected components via BFS."""
    visited = set()
    components = []
    for nid in node_ids:
        if nid in visited:
            continue
        component = []
        queue = deque([nid])
        while queue:
            current = queue.popleft()
            if current in visited:
                continue
            visited.add(current)
            component.append(current)
            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    queue.append(neighbor)
        components.append(component)
    return components


def _n_hop_neighbors(adj, start, hops, limit):
    """Get all nodes within N hops."""
    visited = {start: 0}
    queue = deque([(start, 0)])
    result = []
    while queue and len(result) < limit:
        current, depth = queue.popleft()
        if depth > 0:
            result.append({"node_id": current, "distance": depth})
        if depth < hops:
            for neighbor in adj.get(current, []):
                if neighbor not in visited:
                    visited[neighbor] = depth + 1
                    queue.append((neighbor, depth + 1))
    return result[:limit]


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_algorithms_router(
    user_registry,
    tenant_registry,
    graph_registry,
) -> APIRouter:
    """Create the /dashboard/graphs/{name}/algorithms router."""

    from .auth import UserAuth

    router = APIRouter(tags=["algorithms"])
    user_auth = UserAuth(user_registry)

    def _get_graph(name: str, user):
        """Resolve tenant-scoped graph."""
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id:
            raise HTTPException(status_code=404, detail="No workspace found")
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Workspace not found")
        scoped = f"{tenant.tenant_id}:{name}"
        db = graph_registry.get_graph(scoped, load_if_missing=True)
        if not db:
            raise HTTPException(status_code=404, detail=f"Graph '{name}' not found")
        return db

    # ------------------------------------------------------------------
    # GET /dashboard/graphs/{name}/algorithms — list available
    # ------------------------------------------------------------------
    @router.get("/dashboard/graphs/{name}/algorithms")
    async def list_algorithms(name: str, user=Depends(user_auth)):
        _get_graph(name, user)  # verify access
        return {
            "algorithms": [
                {"id": "shortest-path", "name": "Shortest Path", "description": "BFS shortest path between two nodes"},
                {"id": "pagerank", "name": "PageRank", "description": "Compute PageRank centrality scores"},
                {"id": "components", "name": "Connected Components", "description": "Find connected components"},
                {"id": "neighbors", "name": "N-Hop Neighbors", "description": "Get all nodes within N hops of a node"},
            ]
        }

    # ------------------------------------------------------------------
    # POST /dashboard/graphs/{name}/algorithms/shortest-path
    # ------------------------------------------------------------------
    @router.post("/dashboard/graphs/{name}/algorithms/shortest-path")
    async def algo_shortest_path(name: str, req: ShortestPathRequest, user=Depends(user_auth)):
        db = _get_graph(name, user)
        t0 = time.time()
        nodes = db.get_all_nodes()
        edges = db.get_all_edges()
        adj, node_ids = _build_adjacency(nodes, edges)

        if req.source not in node_ids:
            raise HTTPException(status_code=404, detail=f"Source node '{req.source}' not found")
        if req.target not in node_ids:
            raise HTTPException(status_code=404, detail=f"Target node '{req.target}' not found")

        path = _shortest_path(adj, req.source, req.target, req.max_depth)
        elapsed = time.time() - t0

        return {
            "algorithm": "shortest-path",
            "found": path is not None,
            "path": path or [],
            "length": len(path) - 1 if path else None,
            "execution_time_ms": round(elapsed * 1000, 2),
        }

    # ------------------------------------------------------------------
    # POST /dashboard/graphs/{name}/algorithms/pagerank
    # ------------------------------------------------------------------
    @router.post("/dashboard/graphs/{name}/algorithms/pagerank")
    async def algo_pagerank(name: str, req: PageRankRequest, user=Depends(user_auth)):
        db = _get_graph(name, user)
        t0 = time.time()
        nodes = db.get_all_nodes()
        edges = db.get_all_edges()
        adj, node_ids = _build_adjacency(nodes, edges)

        ranks = _pagerank(adj, node_ids, req.damping, req.iterations)

        # Sort by rank descending, take top_k
        sorted_ranks = sorted(ranks.items(), key=lambda x: x[1], reverse=True)[:req.top_k]

        # Enrich with node labels
        node_map = {n.id: n for n in nodes}
        results = []
        for nid, score in sorted_ranks:
            node = node_map.get(nid)
            results.append({
                "node_id": nid,
                "label": getattr(node, "label", ""),
                "name": (node.properties or {}).get("name", "") if node else "",
                "score": round(score, 6),
            })

        elapsed = time.time() - t0
        return {
            "algorithm": "pagerank",
            "damping": req.damping,
            "iterations": req.iterations,
            "results": results,
            "total_nodes": len(node_ids),
            "execution_time_ms": round(elapsed * 1000, 2),
        }

    # ------------------------------------------------------------------
    # POST /dashboard/graphs/{name}/algorithms/components
    # ------------------------------------------------------------------
    @router.post("/dashboard/graphs/{name}/algorithms/components")
    async def algo_components(name: str, req: ComponentsRequest, user=Depends(user_auth)):
        db = _get_graph(name, user)
        t0 = time.time()
        nodes = db.get_all_nodes()
        edges = db.get_all_edges()
        adj, node_ids = _build_adjacency(nodes, edges)

        components = _connected_components(adj, node_ids)
        filtered = [c for c in components if len(c) >= req.min_size]
        filtered.sort(key=len, reverse=True)

        elapsed = time.time() - t0
        return {
            "algorithm": "connected-components",
            "total_components": len(components),
            "components": [
                {"id": i, "size": len(c), "node_ids": c[:100]}  # cap at 100 per component
                for i, c in enumerate(filtered)
            ],
            "execution_time_ms": round(elapsed * 1000, 2),
        }

    # ------------------------------------------------------------------
    # POST /dashboard/graphs/{name}/algorithms/neighbors
    # ------------------------------------------------------------------
    @router.post("/dashboard/graphs/{name}/algorithms/neighbors")
    async def algo_neighbors(name: str, req: NeighborsRequest, user=Depends(user_auth)):
        db = _get_graph(name, user)
        t0 = time.time()
        nodes = db.get_all_nodes()
        edges = db.get_all_edges()
        adj, node_ids = _build_adjacency(nodes, edges)

        if req.node_id not in node_ids:
            raise HTTPException(status_code=404, detail=f"Node '{req.node_id}' not found")

        neighbors = _n_hop_neighbors(adj, req.node_id, req.hops, req.limit)

        # Enrich with labels
        node_map = {n.id: n for n in nodes}
        for nb in neighbors:
            node = node_map.get(nb["node_id"])
            nb["label"] = getattr(node, "label", "") if node else ""
            nb["name"] = (node.properties or {}).get("name", "") if node else ""

        elapsed = time.time() - t0
        return {
            "algorithm": "neighbors",
            "center": req.node_id,
            "hops": req.hops,
            "neighbors": neighbors,
            "count": len(neighbors),
            "execution_time_ms": round(elapsed * 1000, 2),
        }

    return router
