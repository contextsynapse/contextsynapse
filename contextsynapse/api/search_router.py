"""
Search & RAG Router
===================
Semantic/hybrid search and RAG question-answering endpoints.

POST /dashboard/search  — Search across graph data
POST /dashboard/rag     — RAG question-answering
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------

class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1)
    mode: str = Field("hybrid", description="keyword, semantic, or hybrid")
    graph: Optional[str] = None
    limit: int = Field(20, ge=1, le=100)


class RAGRequest(BaseModel):
    question: str = Field(..., min_length=1)
    graph: Optional[str] = None


# ---------------------------------------------------------------------------
# Router factory
# ---------------------------------------------------------------------------

def create_search_router(
    user_registry,
    tenant_registry,
    graph_registry,
) -> APIRouter:
    """Create the /dashboard/search and /dashboard/rag routers."""

    from .auth import UserAuth, AdminAwareAuth

    router = APIRouter(tags=["search"])
    user_auth = AdminAwareAuth(user_registry)

    def _get_tenant(user):
        tenant_id = user_registry.get_primary_tenant_id(user.user_id)
        if not tenant_id and getattr(user, "is_super_admin", False):
            tenants = tenant_registry.list_tenants()
            if tenants:
                tenant_id = tenants[0].tenant_id
                try: user_registry.link_tenant(user.user_id, tenant_id, "owner")
                except: pass
        if not tenant_id:
            raise HTTPException(status_code=404, detail="No workspace found")
        tenant = tenant_registry.get(tenant_id)
        if not tenant:
            raise HTTPException(status_code=404, detail="Workspace not found")
        return tenant

    def _get_graph_db(tenant, graph_name=None):
        """Get the AIContextDB instance for the tenant's graph."""
        if graph_name:
            # Try tenant-scoped first, then raw name
            scoped = f"{tenant.tenant_id}:{graph_name}"
            db = graph_registry.get_graph(scoped)
            if not db:
                db = graph_registry.get_graph(graph_name)
            return db
        # Return first available graph (tenant-scoped or unscoped)
        all_graphs = graph_registry.list_graphs()
        prefix = f"{tenant.tenant_id}:"
        candidates = []
        for g in all_graphs:
            name = g.get("name", "") if isinstance(g, dict) else g
            if name == "default":
                continue
            if name.startswith(prefix) or name.startswith("ctx_") or ":" not in name:
                node_count = g.get("num_nodes", 0) if isinstance(g, dict) else 0
                candidates.append((name, node_count))
        # Pick the graph with the most nodes
        if candidates:
            candidates.sort(key=lambda x: x[1], reverse=True)
            return graph_registry.get_graph(candidates[0][0])
        return None

    # ------------------------------------------------------------------
    # POST /dashboard/search
    # ------------------------------------------------------------------
    @router.post("/dashboard/search")
    async def search(req: SearchRequest, user=Depends(user_auth)):
        tenant = _get_tenant(user)
        query_lower = req.query.lower().split()

        # Determine which graphs to search
        all_graphs = graph_registry.list_graphs()
        prefix = f"{tenant.tenant_id}:"
        graph_names = []
        for g in all_graphs:
            name = g.get("name", "") if isinstance(g, dict) else g
            if name == "default":
                continue
            if req.graph:
                # Specific graph requested
                if name == f"{prefix}{req.graph}" or name == req.graph:
                    graph_names.append(name)
            else:
                # All graphs: tenant-scoped + unscoped (sessions, contexts)
                if name.startswith(prefix) or name.startswith("ctx_") or not ":" in name:
                    graph_names.append(name)

        if not graph_names:
            return {"results": [], "message": "No graphs found"}

        results = []
        for gname in graph_names:
            db = graph_registry.get_graph(gname)
            if not db:
                continue
            short_name = gname.split(":", 1)[-1] if ":" in gname else gname

            # Hybrid first: BM25 + graph-hop traversal + multi-signal rerank
            try:
                from ..search.graph_search import graph_search as _hybrid
                gsr = _hybrid(db, req.query, graph_name=gname, k=req.limit)
                if gsr.nodes:
                    for sn in gsr.nodes:
                        results.append({
                            "node_id": sn.node_id,
                            "node_type": sn.label,
                            "label": sn.props.get("name", "") or sn.label,
                            "graph": short_name,
                            "snippet": sn.props.get("snippet", "")[:200],
                            "properties": sn.props,
                            "score": round(sn.score, 3),
                        })
                    continue  # skip fallback scan for this graph
            except Exception:
                pass

            # Fallback: naive O(N) keyword scan
            try:
                nodes = db.get_all_nodes()
            except Exception:
                continue
            for node in nodes:
                props = node.properties if hasattr(node, "properties") else (node if isinstance(node, dict) else {})
                node_id = getattr(node, "id", None) or (node.get("id") if isinstance(node, dict) else str(node))
                label = getattr(node, "label", None) or (node.get("label", "") if isinstance(node, dict) else "")
                name = props.get("name", "") if isinstance(props, dict) else ""
                score = 0.0
                snippet = ""
                name_lower = name.lower()
                label_lower = label.lower()
                for term in query_lower:
                    if term == name_lower:
                        score = max(score, 1.0); snippet = name
                    elif term in name_lower:
                        score = max(score, 0.8); snippet = name
                    elif term in label_lower:
                        score = max(score, 0.7); snippet = label
                if score < 0.5 and isinstance(props, dict):
                    for k, v in props.items():
                        v_str = str(v).lower()
                        for term in query_lower:
                            if term in v_str:
                                if k == "content":
                                    score = max(score, 0.5)
                                    i = v_str.find(term)
                                    start = max(0, i - 40)
                                    end = min(len(str(v)), i + len(term) + 40)
                                    snippet = ("..." if start > 0 else "") + str(v)[start:end] + ("..." if end < len(str(v)) else "")
                                else:
                                    score = max(score, 0.3); snippet = f"{k}: {str(v)[:80]}"
                                break
                if score > 0:
                    results.append({
                        "node_id": node_id, "node_type": label,
                        "label": name or label, "graph": short_name,
                        "snippet": snippet,
                        "properties": props if isinstance(props, dict) else {},
                        "score": round(score, 2),
                    })

        # Sort by score descending, limit
        results.sort(key=lambda r: r["score"], reverse=True)
        return {"results": results[:req.limit]}

    # ------------------------------------------------------------------
    # POST /dashboard/rag — Hybrid retrieval (vector + keyword) + LLM
    # ------------------------------------------------------------------
    @router.post("/dashboard/rag")
    async def rag_query(req: RAGRequest, user=Depends(user_auth)):
        from ..search.rag import hybrid_retrieve, generate_answer

        tenant = _get_tenant(user)
        db = _get_graph_db(tenant, req.graph)
        if not db:
            return {"answer": "No graph data found. Create a graph and add data first.", "sources": []}

        top, sources = hybrid_retrieve(db, req.question, req.graph)
        if not top:
            return {"answer": f"No relevant information found for '{req.question}'.", "sources": []}

        return generate_answer(req.question, top, sources)

    # ------------------------------------------------------------------
    # POST /dashboard/search/fulltext  — Whoosh-powered full-text search
    # ------------------------------------------------------------------
    @router.post("/dashboard/search/fulltext")
    async def fulltext_search(req: SearchRequest, user=Depends(user_auth)):
        """Full-text search using Whoosh index."""
        tenant = _get_tenant(user)

        try:
            from ..search.fulltext import fulltext_index
        except Exception:
            raise HTTPException(status_code=501, detail="Full-text search not available")

        results = fulltext_index.search(req.query, limit=req.limit)
        return {"results": results, "engine": "whoosh"}

    # ------------------------------------------------------------------
    # POST /dashboard/search/fulltext/rebuild — Rebuild Whoosh index
    # ------------------------------------------------------------------
    @router.post("/dashboard/search/fulltext/rebuild")
    async def rebuild_fulltext_index(user=Depends(user_auth)):
        """Rebuild the Whoosh full-text index from all graphs."""
        tenant = _get_tenant(user)

        try:
            from ..search.fulltext import fulltext_index
        except Exception:
            raise HTTPException(status_code=501, detail="Full-text search not available")

        all_graphs = graph_registry.list_graphs()
        prefix = f"{tenant.tenant_id}:"
        all_nodes = []
        for g in all_graphs:
            name = g.get("name", "") if isinstance(g, dict) else g
            if name.startswith(prefix):
                db = graph_registry.get_graph(name)
                if db:
                    for node in db.get_all_nodes():
                        all_nodes.append({
                            "node_id": getattr(node, "id", str(node)),
                            "label": getattr(node, "label", ""),
                            "properties": node.properties if hasattr(node, "properties") else {},
                        })

        fulltext_index.rebuild(all_nodes)
        return {"status": "rebuilt", "nodes_indexed": len(all_nodes)}

    return router
