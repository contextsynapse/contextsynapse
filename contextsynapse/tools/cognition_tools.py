"""Cognition tools — lineage tracing and invalidation for agents."""
from __future__ import annotations

from .registry import ToolContext, ToolParam, tool


@tool(
    "trace_lineage", "cognition",
    "Trace the derivation lineage of a node — what it was derived from (sources) "
    "and what was derived from it (downstream). Used for provenance and debugging.",
    params=[
        ToolParam("node_id", "string", "ID of the node to trace"),
    ],
)
def _trace_lineage(ctx: ToolContext, node_id: str) -> str:
    """Trace lineage of a node through the cognition event stream, with graph-edge fallback."""
    try:
        from ..cognition import get_cognition_stream, DerivationTracker
        ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '') or 'default'
        stream = get_cognition_stream(ns)
        tracker = DerivationTracker(stream)

        sources = tracker.get_lineage(node_id)
        derived = tracker.get_derived_from(node_id)

        # Fallback: walk graph DERIVED_FROM edges when cognition stream has no data
        if not sources and not derived:
            try:
                db = ctx.conn.contextcore
                adapter = db.csr_adapter
                # BFS upstream: follow DERIVED_FROM edges to collect all ancestors
                visited, queue, graph_sources = set(), [node_id], []
                while queue:
                    cur = queue.pop(0)
                    for nid, _ in adapter.get_neighbors(cur, edge_type="DERIVED_FROM"):
                        if nid not in visited:
                            visited.add(nid)
                            graph_sources.append(nid)
                            queue.append(nid)
                # Walk downstream: find nodes whose DERIVED_FROM points to node_id
                graph_derived = []
                for n in adapter.get_all_nodes():
                    nid = getattr(n, 'id', None)
                    if nid and nid != node_id:
                        for neighbor_id, _ in adapter.get_neighbors(nid, edge_type="DERIVED_FROM"):
                            if neighbor_id == node_id:
                                graph_derived.append(nid)
                                break
                if graph_sources or graph_derived:
                    sources = graph_sources
                    derived = graph_derived
            except Exception:
                pass

        lines = [f"Lineage for node: {node_id}"]
        if sources:
            lines.append(f"\nDerived FROM ({len(sources)} sources):")
            for s in sources[:10]:
                lines.append(f"  \u2190 {s}")
        else:
            lines.append("\nNo known sources (root context or untracked).")

        if derived:
            lines.append(f"\nDerived TO ({len(derived)} downstream):")
            for d in derived[:10]:
                lines.append(f"  \u2192 {d}")
        else:
            lines.append("\nNo downstream derivations.")

        ctx.record_provenance("read", "trace_lineage", node_id)
        return "\n".join(lines)
    except Exception as e:
        return f"Error tracing lineage: {e}"


@tool(
    "invalidate_node", "cognition",
    "Mark a context node as invalid and cascade invalidation to all nodes derived from it. "
    "Use when a source is retracted, incorrect, or superseded.",
    params=[
        ToolParam("node_id", "string", "ID of the node to invalidate"),
        ToolParam("reason", "string", "Why this node is being invalidated"),
    ],
)
def _invalidate_node(ctx: ToolContext, node_id: str, reason: str) -> str:
    """Invalidate a node and cascade through derivation chain."""
    try:
        from ..cognition import get_cognition_stream, DerivationTracker, InvalidationEngine
        ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '') or 'default'
        stream = get_cognition_stream(ns)
        derivation = DerivationTracker(stream)
        engine = InvalidationEngine(stream, derivation)

        agent_id = ctx.agent_id or "unknown"
        invalidated = engine.invalidate(node_id, reason=reason, agent_id=agent_id)

        # Remove invalidated nodes from LMDB search index so they no longer appear in results
        try:
            from ..search.lmdb_index import get_lmdb_index
            graph_ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '')
            if graph_ns:
                lmdb_idx = get_lmdb_index(graph_ns)
                for nid in invalidated:
                    lmdb_idx.delete_node(str(nid))
        except Exception:
            pass  # LMDB cleanup must never block the invalidation response

        lines = [f"Invalidated {len(invalidated)} node(s) (reason: {reason})"]
        lines.append(f"Root: {node_id}")
        if len(invalidated) > 1:
            lines.append(f"Cascade: {', '.join(invalidated[1:5])}")
            if len(invalidated) > 5:
                lines.append(f"  ... and {len(invalidated) - 5} more")

        ctx.record_provenance("write", "invalidate_node", node_id)
        return "\n".join(lines)
    except Exception as e:
        return f"Error invalidating node: {e}"
