"""Intelligence tools — agent-facing tools for context intelligence."""
from __future__ import annotations

import logging

from .registry import ToolContext, ToolParam, tool

logger = logging.getLogger(__name__)


def _propagate_feedback(db, node_id: str, signal: str, comment: str = ""):
    """Propagate feedback to dependent nodes via SDLC edges.

    When a node is flagged outdated/misleading, all nodes that depend on it
    (via GOVERNS, IMPLEMENTS, TESTS, SATISFIES, etc.) get a warning flag.
    """
    try:
        edges = db.get_all_edges()
        # Find edges where this node is the source (outgoing dependencies)
        # e.g., ArchDecision --GOVERNS--> CodeModule
        dependents = set()
        for edge in edges:
            if edge.source == node_id:
                dependents.add(edge.target)
            # Also propagate to nodes that point AT this node
            # e.g., CodeModule --IMPLEMENTS--> Requirement (if req is outdated, code is affected)
            if edge.target == node_id:
                dependents.add(edge.source)

        now = __import__("time").time()
        for dep_id in dependents:
            dep_node = db.get_node(dep_id)
            if dep_node is None:
                continue
            # Don't overwrite a node that already has its own direct feedback
            props = dep_node.properties if hasattr(dep_node, "properties") else {}
            if props.get("_usefulness_last_signal") in ("outdated", "misleading"):
                continue  # already flagged directly

            db.update_node_properties(dep_id, {
                "_upstream_warning": f"depends on {node_id} which is {signal}",
                "_upstream_warning_source": node_id,
                "_upstream_warning_at": now,
            })

        if dependents:
            logger.info("Propagated '%s' feedback from %s to %d dependents", signal, node_id, len(dependents))
    except Exception as exc:
        logger.debug("Feedback propagation failed: %s", exc)


@tool(
    "check_freshness", "intelligence",
    "Check if a node's source is still current. Returns freshness status.",
    params=[
        ToolParam("node_id", "string", "ID of the node to check", required=False),
    ],
)
def _check_freshness(ctx: ToolContext, node_id: str = "") -> str:
    """Check freshness of a node's source."""
    try:
        from ..intelligence.source_watcher import should_refresh
        db = ctx.conn.db if ctx.conn and hasattr(ctx.conn, 'db') else None
        if not node_id:
            return "Provide a node_id to check freshness."
        if db is None:
            return "Graph database not available in this context."
        node = db.get_node(node_id) if hasattr(db, "get_node") else None
        if not node:
            return f"Node {node_id} not found."
        props = node.get("properties", node) if isinstance(node, dict) else {}
        source = props.get("_source")
        if not source:
            return f"Node {node_id} has no source tracking (_source metadata missing)."
        needs_refresh = should_refresh(source)
        status = "stale (needs refresh)" if needs_refresh else "fresh"
        return (
            f"Node: {node_id}\n"
            f"Source type: {source.get('type', 'unknown')}\n"
            f"URI: {source.get('uri', 'N/A')}\n"
            f"Status: {status}\n"
            f"Last fetched: {source.get('last_fetched', 'unknown')}\n"
            f"Refresh interval: {source.get('refresh_interval', 0)}s\n"
            f"Human override: {source.get('human_override', False)}"
        )
    except Exception as e:
        return f"Error checking freshness: {e}"


@tool(
    "rate_context", "intelligence",
    "Rate a context node's usefulness. Signals: helpful, critical, irrelevant, misleading, outdated.",
    params=[
        ToolParam("node_id", "string", "ID of the node to rate"),
        ToolParam("signal", "string", "One of: helpful, critical, irrelevant, misleading, outdated"),
        ToolParam("comment", "string", "Optional explanation", required=False),
    ],
)
def _rate_context(ctx: ToolContext, node_id: str, signal: str, comment: str = "") -> str:
    """Rate a context node's usefulness — wired to FeedbackLoop module."""
    valid = {"helpful", "critical", "irrelevant", "misleading", "outdated"}
    if signal not in valid:
        return f"Invalid signal '{signal}'. Must be one of: {', '.join(sorted(valid))}"
    agent_id = ctx.agent_id if hasattr(ctx, "agent_id") else "unknown"
    source = f"agent:{agent_id}"

    # Wire to cognition feedback stream
    try:
        from ..cognition import get_cognition_stream, FeedbackProcessor
        ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '') or 'default'
        stream = get_cognition_stream(ns)
        fp = FeedbackProcessor(stream)
        cognition_signal = {"helpful": "useful", "critical": "useful", "irrelevant": "outdated"}.get(signal, signal)
        fp.record_feedback(agent_id=agent_id, node_id=node_id, signal=cognition_signal, reason=comment)
    except Exception:
        pass  # graceful degradation

    try:
        from ..intelligence import _modules
        feedback = _modules.get("feedback_loop")
        if feedback:
            from ..intelligence.feedback_loop import FeedbackRecord
            import asyncio
            record = FeedbackRecord(
                node_id=node_id, signal=signal,
                source=source, comment=comment,
            )
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(feedback.record_feedback(record))
            except RuntimeError:
                asyncio.run(feedback.record_feedback(record))

            # Update _usefulness_score on the node
            from ..intelligence.feedback_loop import apply_signal
            db = ctx.db
            if hasattr(db, "get_node"):
                node = db.get_node(node_id)
                if node:
                    props = node.get("properties", node) if isinstance(node, dict) else {}
                    old_score = props.get("_usefulness_score", 0.5)
                    new_score = apply_signal(old_score, signal, feedback._config)
                    db.update_node_properties(node_id, {
                        "_usefulness_score": new_score,
                        "_usefulness_last_signal": signal,
                        "_usefulness_comment": comment[:200] if comment else "",
                        "_usefulness_last_updated": __import__("time").time(),
                    })

                    # ── Propagate to dependent nodes ──────────────────────
                    # If a node is flagged outdated/misleading, flag its dependents too
                    if signal in ("outdated", "misleading") and hasattr(db, "get_all_edges"):
                        _propagate_feedback(db, node_id, signal, comment)

    except Exception as e:
        pass  # Graceful degradation — still record provenance

    ctx.record_provenance("write", "rate_context", f"{node_id}:{signal}")
    return f"Feedback recorded: {signal} for node {node_id}" + (f" — {comment}" if comment else "")


@tool(
    "detect_conflicts", "intelligence",
    "Scan for contradictions around a specific node or across the context.",
    params=[
        ToolParam("node_id", "string", "Node ID to scan around (optional, omit for full scan)", required=False),
    ],
)
def _detect_conflicts(ctx: ToolContext, node_id: str = "") -> str:
    """Detect conflicts — wired to ConflictDetector module."""
    try:
        from ..intelligence import _modules
        detector = _modules.get("conflict_detector")
        if detector:
            conflicts = detector.list_conflicts(status="open")
            if not conflicts:
                return "No open conflicts detected."
            lines = [f"Found {len(conflicts)} open conflict(s):"]
            for c in conflicts[:10]:
                lines.append(f"  [{c.severity.upper()}] {c.conflict_type}: {c.summary} (id: {c.conflict_id[:12]}...)")
            return "\n".join(lines)
    except Exception:
        pass
    return "Conflict detection not available."


@tool(
    "resolve_conflict", "intelligence",
    "Mark a detected conflict as resolved with an explanation.",
    params=[
        ToolParam("conflict_id", "string", "ID of the conflict to resolve"),
        ToolParam("resolution", "string", "Explanation of how the conflict was resolved"),
    ],
)
def _resolve_conflict(ctx: ToolContext, conflict_id: str, resolution: str) -> str:
    """Resolve a conflict — wired to ConflictDetector module."""
    try:
        from ..intelligence import _modules
        detector = _modules.get("conflict_detector")
        if detector:
            result = detector.resolve_conflict(conflict_id, resolution)
            if result:
                ctx.record_provenance("write", "resolve_conflict", conflict_id)
                return f"Conflict {conflict_id} resolved: {resolution}"
            return f"Conflict {conflict_id} not found."
    except Exception:
        pass
    ctx.record_provenance("write", "resolve_conflict", conflict_id)
    return f"Conflict {conflict_id} resolved: {resolution}"


@tool(
    "get_suggestions", "intelligence",
    "Get proactive context suggestions from the Intelligence Radar.",
    params=[
        ToolParam("task_id", "string", "Task ID to get suggestions for (optional)", required=False),
    ],
)
def _get_suggestions(ctx: ToolContext, task_id: str = "") -> str:
    """Get suggestions — wired to ContextRadar module."""
    try:
        from ..intelligence import _modules
        radar = _modules.get("context_radar")
        if radar:
            agent_id = ctx.agent_id if hasattr(ctx, "agent_id") else "unknown"
            suggestions = radar.get_suggestions(agent_id)
            if not suggestions:
                return "No pending suggestions."
            lines = [f"{len(suggestions)} suggestion(s):"]
            for s in suggestions:
                lines.append(f"  [{s.trigger}] {s.message}")
            return "\n".join(lines)
    except Exception:
        pass
    return "No pending suggestions."
