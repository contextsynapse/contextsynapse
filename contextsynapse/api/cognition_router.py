"""REST API for the Cognitive Reliability Layer."""
from __future__ import annotations

from fastapi import APIRouter, Body, Depends, HTTPException


def create_cognition_router(graph_registry, user_auth):
    """Create cognition router with injected dependencies."""
    router = APIRouter(prefix="/cognition", tags=["cognition"])

    def _get_stream(namespace: str):
        from ..cognition import get_cognition_stream
        return get_cognition_stream(namespace)

    @router.get("/{namespace}/node/{node_id}/lineage")
    async def get_lineage(namespace: str, node_id: str, user=Depends(user_auth)):
        """Get derivation lineage for a node."""
        from ..cognition import DerivationTracker
        stream = _get_stream(namespace)
        tracker = DerivationTracker(stream)
        sources = tracker.get_lineage(node_id)
        derived = tracker.get_derived_from(node_id)
        return {"node_id": node_id, "sources": sources, "derived_to": derived}

    @router.get("/{namespace}/node/{node_id}/consumers")
    async def get_consumers(namespace: str, node_id: str, user=Depends(user_auth)):
        """Get which agents consumed this node."""
        from ..cognition import ReadTracker
        stream = _get_stream(namespace)
        tracker = ReadTracker(stream)
        consumers = tracker.get_consumers(node_id)
        return {"node_id": node_id, "consumers": consumers}

    @router.post("/{namespace}/node/{node_id}/feedback")
    async def post_feedback(namespace: str, node_id: str, body: dict = Body(...), user=Depends(user_auth)):
        """Submit feedback about a context node."""
        from ..cognition import FeedbackProcessor
        signal = body.get("signal", "")
        valid = {"useful", "misleading", "outdated", "incorrect"}
        if signal not in valid:
            raise HTTPException(400, f"signal must be one of: {', '.join(sorted(valid))}")

        stream = _get_stream(namespace)
        fp = FeedbackProcessor(stream)
        fp.record_feedback(
            agent_id=body.get("agent_id", "api"),
            node_id=node_id,
            signal=signal,
            reason=body.get("reason", ""),
            output_id=body.get("output_id", ""),
        )
        adjustment = FeedbackProcessor.score_adjustment(signal)
        return {"recorded": True, "signal": signal, "score_adjustment": adjustment}

    @router.get("/{namespace}/node/{node_id}/feedback")
    async def get_feedback(namespace: str, node_id: str, user=Depends(user_auth)):
        """Get all feedback for a node."""
        from ..cognition import FeedbackProcessor
        stream = _get_stream(namespace)
        fp = FeedbackProcessor(stream)
        return {"node_id": node_id, "feedback": fp.get_feedback(node_id)}

    @router.post("/{namespace}/node/{node_id}/invalidate")
    async def invalidate_node(namespace: str, node_id: str, body: dict = Body(...), user=Depends(user_auth)):
        """Invalidate a node and cascade through derivation chain."""
        from ..cognition import DerivationTracker, InvalidationEngine
        stream = _get_stream(namespace)
        derivation = DerivationTracker(stream)
        engine = InvalidationEngine(stream, derivation)
        reason = body.get("reason", "")
        agent_id = body.get("agent_id", "api")
        invalidated = engine.invalidate(node_id, reason=reason, agent_id=agent_id)

        # Remove invalidated nodes from LMDB search index (mirrors cognition_tools.py behaviour)
        try:
            from ..search.lmdb_index import get_lmdb_index
            if namespace:
                lmdb_idx = get_lmdb_index(namespace)
                for nid in invalidated:
                    lmdb_idx.delete_node(str(nid))
        except Exception:
            pass  # LMDB cleanup must never block the API response

        return {"invalidated": invalidated, "count": len(invalidated), "reason": reason}

    @router.post("/{namespace}/hallucination/trace")
    async def trace_hallucination(namespace: str, body: dict = Body(...), user=Depends(user_auth)):
        """Trace root cause of a hallucinated output."""
        from ..cognition import DerivationTracker, HallucinationTracer
        output_node_id = body.get("output_node_id", "")
        if not output_node_id:
            raise HTTPException(400, "output_node_id is required")

        stream = _get_stream(namespace)
        derivation = DerivationTracker(stream)
        tracer = HallucinationTracer(stream, derivation)

        # Build node_status_fn from the graph
        def node_status_fn(nid):
            inv_events = stream.get_events(event_type="invalidate", limit=50)
            invalidated_ids = set()
            for ev in inv_events:
                invalidated_ids.update(ev.node_ids)
            return {"invalidated": nid in invalidated_ids, "quality": 1.0}

        result = tracer.trace(output_node_id, node_status_fn=node_status_fn)
        return result

    @router.get("/{namespace}/events")
    async def get_events(namespace: str, agent_id: str = "", event_type: str = "", limit: int = 50, user=Depends(user_auth)):
        """Query cognition events with optional filters."""
        stream = _get_stream(namespace)
        events = stream.get_events(agent_id=agent_id, event_type=event_type, limit=min(limit, 200))
        return {"events": [e.to_dict() for e in events], "count": len(events)}

    return router
