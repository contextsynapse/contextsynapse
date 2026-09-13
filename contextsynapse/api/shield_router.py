"""REST API for AgentShield — trust management + notifications."""
from __future__ import annotations
from fastapi import APIRouter, Body, Depends, HTTPException


def create_shield_router(user_auth):
    router = APIRouter(prefix="/shield", tags=["shield"])

    def _shield():
        from ..shield import get_agent_shield
        return get_agent_shield()

    @router.get("/agents")
    async def list_agent_trust(user=Depends(user_auth)):
        """List all agents with trust scores and profiles."""
        shield = _shield()
        # Get all agents from registry
        try:
            from ..context.store_factory import create_agent_registry
            registry = create_agent_registry()
            agents = registry.list_agents()
        except Exception:
            agents = []
        result = []
        for a in agents:
            agent_id = a.get("agent_id", a.agent_id if hasattr(a, "agent_id") else "")
            name = a.get("name", getattr(a, "name", ""))
            score = shield.get_trust_score(agent_id)
            level = shield.get_trust_level(agent_id)
            profile = shield.get_profile(agent_id)
            result.append({
                "agent_id": agent_id, "name": name,
                "trust_score": score, "trust_level": level,
                "total_tool_calls": profile.get("total_tool_calls", 0),
                "feedback_quality": profile.get("feedback_quality", 0.5),
                "invalidations_caused": profile.get("invalidations_caused", 0),
                "frozen": shield.permissions.is_frozen(agent_id),
            })
        return {"agents": result}

    @router.get("/agents/{agent_id}")
    async def get_agent_shield_detail(agent_id: str, user=Depends(user_auth)):
        """Get detailed shield info for an agent."""
        shield = _shield()
        return {
            "agent_id": agent_id,
            "trust_score": shield.get_trust_score(agent_id),
            "trust_level": shield.get_trust_level(agent_id),
            "profile": shield.get_profile(agent_id),
            "frozen": shield.permissions.is_frozen(agent_id),
        }

    @router.post("/agents/{agent_id}/freeze")
    async def freeze_agent(agent_id: str, body: dict = Body(default={}), user=Depends(user_auth)):
        """Manually freeze an agent to read-only."""
        shield = _shield()
        shield.permissions.freeze(agent_id)
        shield._notify(agent_id, "manual_freeze", {"by": "admin", "reason": body.get("reason", "")})
        return {"frozen": True, "agent_id": agent_id}

    @router.post("/agents/{agent_id}/unfreeze")
    async def unfreeze_agent(agent_id: str, user=Depends(user_auth)):
        """Unfreeze an agent."""
        shield = _shield()
        shield.permissions.unfreeze(agent_id)
        shield._notify(agent_id, "manual_unfreeze", {"by": "admin"})
        return {"frozen": False, "agent_id": agent_id}

    @router.post("/agents/{agent_id}/trust")
    async def set_trust(agent_id: str, body: dict = Body(...), user=Depends(user_auth)):
        """Manually set an agent's trust score (admin override)."""
        score = body.get("score")
        if score is None or not (0.0 <= score <= 1.0):
            from fastapi import HTTPException
            raise HTTPException(400, "score must be between 0.0 and 1.0")
        shield = _shield()
        old_level = shield.get_trust_level(agent_id)
        shield.trust._set_score(agent_id, score)
        new_level = shield.get_trust_level(agent_id)
        shield._notify(agent_id, "manual_trust_override", {
            "old_level": old_level, "new_level": new_level, "score": score, "by": "admin",
        })
        return {"agent_id": agent_id, "trust_score": score, "trust_level": new_level}

    @router.get("/report-card")
    async def context_report_card(namespace: str = "default", user=Depends(user_auth)):
        """Context Report Card — health and quality summary of the shared graph."""
        shield = _shield()

        # Gather data from cognition stream
        try:
            from ..cognition import get_cognition_stream
            stream = get_cognition_stream(namespace)
            all_events = stream.get_events(limit=500)
        except Exception:
            all_events = []

        # 1. Overall Quality Grade
        feedback_events = [e for e in all_events if e.event_type == "feedback"]
        total_feedback = len(feedback_events)
        useful_count = sum(1 for e in feedback_events if e.metadata.get("signal") == "useful")
        misleading_count = sum(1 for e in feedback_events if e.metadata.get("signal") in ("misleading", "incorrect"))
        outdated_count = sum(1 for e in feedback_events if e.metadata.get("signal") == "outdated")
        quality_ratio = useful_count / total_feedback if total_feedback > 0 else 1.0

        if quality_ratio >= 0.9:
            quality_grade = "A"
        elif quality_ratio >= 0.75:
            quality_grade = "B"
        elif quality_ratio >= 0.6:
            quality_grade = "C"
        elif quality_ratio >= 0.4:
            quality_grade = "D"
        else:
            quality_grade = "F"

        # 2. Activity Summary
        read_events = sum(1 for e in all_events if e.event_type == "read")
        write_events = sum(1 for e in all_events if e.event_type in ("write", "derive"))
        derive_events = sum(1 for e in all_events if e.event_type == "derive")
        invalidate_events = [e for e in all_events if e.event_type == "invalidate"]
        total_invalidated = sum(len(e.node_ids) for e in invalidate_events)

        # 3. Agent Leaderboard
        agent_stats = {}
        for e in all_events:
            if e.agent_id not in agent_stats:
                agent_stats[e.agent_id] = {"reads": 0, "writes": 0, "feedback_useful": 0, "feedback_negative": 0}
            if e.event_type == "read":
                agent_stats[e.agent_id]["reads"] += 1
            elif e.event_type in ("write", "derive"):
                agent_stats[e.agent_id]["writes"] += 1
            elif e.event_type == "feedback":
                sig = e.metadata.get("signal", "")
                if sig == "useful":
                    agent_stats[e.agent_id]["feedback_useful"] += 1
                elif sig in ("misleading", "incorrect"):
                    agent_stats[e.agent_id]["feedback_negative"] += 1

        leaderboard = []
        for agent_id, stats in agent_stats.items():
            total_fb = stats["feedback_useful"] + stats["feedback_negative"]
            quality = stats["feedback_useful"] / total_fb if total_fb > 0 else None
            score = shield.get_trust_score(agent_id)
            leaderboard.append({
                "agent_id": agent_id,
                "trust_score": score,
                "trust_level": shield.get_trust_level(agent_id),
                "reads": stats["reads"],
                "writes": stats["writes"],
                "quality": round(quality, 2) if quality is not None else None,
            })
        leaderboard.sort(key=lambda x: x["trust_score"], reverse=True)

        # 4. Trust Distribution
        trust_dist = {"untrusted": 0, "provisional": 0, "verified": 0, "trusted": 0}
        for a in leaderboard:
            level = a["trust_level"]
            if level in trust_dist:
                trust_dist[level] += 1

        # 5. Risk Indicators
        frozen_count = sum(1 for a in leaderboard if shield.permissions.is_frozen(a["agent_id"]))
        invalidation_rate = total_invalidated / max(write_events, 1)

        risk_level = "low"
        if invalidation_rate > 0.2 or frozen_count > 0:
            risk_level = "high"
        elif invalidation_rate > 0.1 or misleading_count > useful_count * 0.3:
            risk_level = "medium"

        # 6. Lineage Depth
        derivation_chains = sum(1 for e in all_events if e.event_type == "derive" and len(e.derived_from) > 0)

        return {
            "namespace": namespace,
            "quality": {
                "grade": quality_grade,
                "ratio": round(quality_ratio, 3),
                "total_feedback": total_feedback,
                "useful": useful_count,
                "misleading": misleading_count,
                "outdated": outdated_count,
            },
            "activity": {
                "total_events": len(all_events),
                "reads": read_events,
                "writes": write_events,
                "derivations": derive_events,
                "invalidations": total_invalidated,
            },
            "agents": {
                "total": len(leaderboard),
                "leaderboard": leaderboard[:10],
                "trust_distribution": trust_dist,
                "frozen": frozen_count,
            },
            "risk": {
                "level": risk_level,
                "invalidation_rate": round(invalidation_rate, 3),
                "frozen_agents": frozen_count,
            },
            "lineage": {
                "derivation_chains": derivation_chains,
            },
        }

    @router.get("/context/{namespace}/summary")
    async def context_summary(namespace: str, user=Depends(user_auth)):
        """Context Summary — health and coverage of the knowledge graph itself."""
        # Get graph
        try:
            from ..core.registry import GraphRegistry
            import os
            storage = os.environ.get("CONTEXTSYNAPSE_STORAGE_DIR") or os.environ.get("AICONTEXTDB_STORAGE_DIR", "contextcore_data")
            registry = GraphRegistry(storage_dir=storage)
            graph = registry.get_graph(namespace)
        except Exception:
            graph = None

        nodes = []
        edges = []
        if graph:
            try:
                adapter = getattr(graph, 'csr_adapter', None) or graph
                nodes = list(adapter.get_all_nodes()) if hasattr(adapter, 'get_all_nodes') else []
                edges = list(adapter.get_all_edges()) if hasattr(adapter, 'get_all_edges') else []
            except Exception:
                pass

        # 1. Node distribution by label
        label_dist = {}
        for n in nodes:
            label = getattr(n, 'label', getattr(n, 'node_type', 'Unknown'))
            label_dist[label] = label_dist.get(label, 0) + 1

        # 2. Freshness — age distribution
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        freshness = {"last_24h": 0, "last_7d": 0, "last_30d": 0, "older": 0}
        for n in nodes:
            props = getattr(n, 'properties', {}) or {}
            created = props.get("created_at") or props.get("_created_at") or props.get("timestamp", "")
            if created:
                try:
                    if isinstance(created, (int, float)):
                        dt = datetime.fromtimestamp(created, tz=timezone.utc)
                    else:
                        dt = datetime.fromisoformat(str(created).replace("Z", "+00:00"))
                    age = now - dt
                    if age < timedelta(hours=24):
                        freshness["last_24h"] += 1
                    elif age < timedelta(days=7):
                        freshness["last_7d"] += 1
                    elif age < timedelta(days=30):
                        freshness["last_30d"] += 1
                    else:
                        freshness["older"] += 1
                except Exception:
                    freshness["older"] += 1
            else:
                freshness["older"] += 1

        total_nodes = len(nodes)
        fresh_pct = (freshness["last_24h"] + freshness["last_7d"]) / total_nodes if total_nodes > 0 else 0

        # 3. Validation rate — how much content has feedback
        try:
            from ..cognition import get_cognition_stream
            stream = get_cognition_stream(namespace)
            feedback_events = stream.get_events(event_type="feedback", limit=500)
            rated_nodes = set()
            for e in feedback_events:
                rated_nodes.update(e.node_ids)
            validation_rate = len(rated_nodes) / total_nodes if total_nodes > 0 else 0
        except Exception:
            rated_nodes = set()
            validation_rate = 0

        # 4. Invalidation rate
        try:
            inv_events = stream.get_events(event_type="invalidate", limit=200)
            invalidated_nodes = set()
            for e in inv_events:
                invalidated_nodes.update(e.node_ids)
            invalidation_rate = len(invalidated_nodes) / total_nodes if total_nodes > 0 else 0
        except Exception:
            invalidated_nodes = set()
            invalidation_rate = 0

        # 5. Source diversity
        sources = set()
        for n in nodes:
            props = getattr(n, 'properties', {}) or {}
            src = props.get("_source") or props.get("source") or props.get("_source_url", "")
            if isinstance(src, dict):
                src = src.get("uri", src.get("url", ""))
            if src:
                # Normalize to domain
                try:
                    from urllib.parse import urlparse
                    parsed = urlparse(str(src))
                    domain = parsed.netloc or str(src)[:50]
                    sources.add(domain)
                except Exception:
                    sources.add(str(src)[:50])

        # 6. Connection density
        avg_edges = len(edges) / total_nodes if total_nodes > 0 else 0

        # 7. Derivation depth
        try:
            derive_events = stream.get_events(event_type="derive", limit=500)
            derived_count = len(set(nid for e in derive_events for nid in e.node_ids))
            original_count = total_nodes - derived_count
        except Exception:
            derived_count = 0
            original_count = total_nodes

        # Overall health grade
        health_score = 0
        if total_nodes > 0:
            health_score += min(fresh_pct * 30, 30)  # freshness: up to 30
            health_score += min(validation_rate * 20, 20)  # validation: up to 20
            health_score += min((1 - invalidation_rate) * 20, 20)  # low invalidation: up to 20
            health_score += min(avg_edges / 2 * 15, 15)  # connectivity: up to 15
            health_score += min(len(sources) / 5 * 15, 15)  # source diversity: up to 15

        if health_score >= 80: health_grade = "A"
        elif health_score >= 65: health_grade = "B"
        elif health_score >= 50: health_grade = "C"
        elif health_score >= 35: health_grade = "D"
        else: health_grade = "F"

        return {
            "namespace": namespace,
            "health": {
                "grade": health_grade,
                "score": round(health_score, 1),
            },
            "size": {
                "total_nodes": total_nodes,
                "total_edges": len(edges),
                "label_distribution": dict(sorted(label_dist.items(), key=lambda x: -x[1])),
            },
            "freshness": {
                "distribution": freshness,
                "fresh_percent": round(fresh_pct * 100, 1),
            },
            "quality": {
                "validated_nodes": len(rated_nodes),
                "validation_rate": round(validation_rate * 100, 1),
                "invalidated_nodes": len(invalidated_nodes),
                "invalidation_rate": round(invalidation_rate * 100, 1),
            },
            "sources": {
                "unique_sources": len(sources),
                "top_sources": sorted(sources)[:10],
            },
            "structure": {
                "avg_edges_per_node": round(avg_edges, 2),
                "derived_nodes": derived_count,
                "original_nodes": original_count,
            },
        }

    @router.get("/notifications")
    async def get_notifications(limit: int = 50, user=Depends(user_auth)):
        """Get recent shield notifications."""
        shield = _shield()
        return {"notifications": shield.get_notifications(limit)}

    @router.get("/review-queue")
    async def review_queue(limit: int = 50, user=Depends(user_auth)):
        """List all pending review items across all sessions."""
        from ..context.promotion import PromotionQueue
        from ..context.session import ContextSessionManager
        import os

        all_items = []
        try:
            sm = ContextSessionManager()
            sessions = sm.list_sessions()
            for s in sessions:
                ns = s.graph_namespace
                q = PromotionQueue(ns)
                items = q.pending(limit=20)
                for item in items:
                    item["session_name"] = s.name
                    item["namespace"] = ns
                all_items.extend(items)
        except Exception:
            pass

        # Sort by score descending
        all_items.sort(key=lambda x: x.get("score", 0), reverse=True)
        return {"items": all_items[:limit], "total": len(all_items)}

    @router.post("/review/{node_id}/approve")
    async def approve_review(node_id: str, body: dict = Body(default={}), user=Depends(user_auth)):
        """Approve a queued finding — promote to atomic context."""
        namespace = body.get("namespace", "")
        if not namespace:
            raise HTTPException(400, "namespace required")

        from ..context.promotion import PromotionQueue, promote_node
        from ..adapters._base import AIContextDBConnection

        q = PromotionQueue(namespace)
        ok = q.approve(node_id, reviewer_id="admin")
        if not ok:
            raise HTTPException(404, "Item not found in review queue")

        # Promote the node
        try:
            rt_ns = f"{namespace}_rt"
            rt_conn = AIContextDBConnection(namespace=rt_ns)
            at_conn = AIContextDBConnection(namespace=namespace)
            promote_node(node_id, rt_conn, at_conn, promoted_by="admin_review")
        except Exception as e:
            return {"approved": True, "promoted": False, "error": str(e)}

        return {"approved": True, "promoted": True, "node_id": node_id}

    @router.post("/review/{node_id}/reject")
    async def reject_review(node_id: str, body: dict = Body(default={}), user=Depends(user_auth)):
        """Reject a queued finding."""
        namespace = body.get("namespace", "")
        if not namespace:
            raise HTTPException(400, "namespace required")

        from ..context.promotion import PromotionQueue
        q = PromotionQueue(namespace)
        ok = q.reject(node_id, reviewer_id="admin", reason=body.get("reason", ""))
        if not ok:
            raise HTTPException(404, "Item not found in review queue")

        return {"rejected": True, "node_id": node_id}

    @router.get("/working-memory/stats")
    async def working_memory_stats(user=Depends(user_auth)):
        """Get working memory cache statistics."""
        from ..context.working_memory import get_working_memory
        wm = get_working_memory()
        return wm.stats()

    return router
