"""Shield tools — trust and report card for agents."""
from __future__ import annotations

from .registry import ToolContext, ToolParam, tool


@tool(
    "context_report_card", "cognition",
    "Get a health report card for the shared context — quality grade, agent leaderboard, "
    "risk indicators, and activity summary. Use to assess context reliability before making decisions.",
    params=[],
)
def _context_report_card(ctx: ToolContext) -> str:
    """Generate context report card."""
    try:
        from ..shield import get_agent_shield
        from ..cognition import get_cognition_stream

        shield = get_agent_shield()
        ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '') or 'default'
        stream = get_cognition_stream(ns)
        events = stream.get_events(limit=500)

        # Quality
        fb = [e for e in events if e.event_type == "feedback"]
        useful = sum(1 for e in fb if e.metadata.get("signal") == "useful")
        bad = sum(1 for e in fb if e.metadata.get("signal") in ("misleading", "incorrect"))
        total_fb = len(fb)
        quality = useful / total_fb if total_fb > 0 else 1.0

        if quality >= 0.9: grade = "A"
        elif quality >= 0.75: grade = "B"
        elif quality >= 0.6: grade = "C"
        elif quality >= 0.4: grade = "D"
        else: grade = "F"

        # Activity
        reads = sum(1 for e in events if e.event_type == "read")
        writes = sum(1 for e in events if e.event_type in ("write", "derive"))
        derivations = sum(1 for e in events if e.event_type == "derive")
        invalidations = sum(len(e.node_ids) for e in events if e.event_type == "invalidate")

        # Risk
        inv_rate = invalidations / max(writes, 1)
        risk = "high" if inv_rate > 0.2 else "medium" if inv_rate > 0.1 else "low"

        # Agents
        agents = set(e.agent_id for e in events if e.agent_id)
        agent_lines = []
        for aid in list(agents)[:5]:
            score = shield.get_trust_score(aid)
            level = shield.get_trust_level(aid)
            frozen = " [FROZEN]" if shield.permissions.is_frozen(aid) else ""
            agent_lines.append(f"  {aid[:12]}  {level} ({score:.2f}){frozen}")

        lines = [
            f"Context Report Card — {ns}",
            f"",
            f"Quality Grade: {grade} ({quality:.0%} useful feedback)",
            f"  Feedback: {useful} useful, {bad} negative, {total_fb} total",
            f"",
            f"Activity: {len(events)} events",
            f"  Reads: {reads}  Writes: {writes}  Derivations: {derivations}",
            f"  Invalidations: {invalidations}",
            f"",
            f"Risk Level: {risk.upper()} (invalidation rate: {inv_rate:.1%})",
            f"",
            f"Agents ({len(agents)}):",
        ]
        lines.extend(agent_lines or ["  No agents tracked yet"])

        ctx.record_provenance("read", "context_report_card", ns)
        return "\n".join(lines)
    except Exception as e:
        return f"Report card unavailable: {e}"


@tool(
    "context_summary", "cognition",
    "Get a health summary of the knowledge graph itself — node distribution, freshness, "
    "validation rate, source diversity, and connection density. Use to understand context coverage.",
    params=[],
)
def _context_summary(ctx: ToolContext) -> str:
    """Generate context summary."""
    try:
        ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '') or 'default'

        # Get graph
        db = ctx.conn.db if hasattr(ctx.conn, 'db') else ctx.conn.contextcore if hasattr(ctx.conn, 'contextcore') else None
        nodes = []
        edges = []
        if db:
            try:
                adapter = getattr(db, 'csr_adapter', None) or db
                nodes = list(adapter.get_all_nodes()) if hasattr(adapter, 'get_all_nodes') else []
                edges = list(adapter.get_all_edges()) if hasattr(adapter, 'get_all_edges') else []
            except Exception:
                pass

        total_nodes = len(nodes)
        total_edges = len(edges)

        # Label distribution
        labels = {}
        for n in nodes:
            label = getattr(n, 'label', getattr(n, 'node_type', '?'))
            labels[label] = labels.get(label, 0) + 1

        # Freshness
        from datetime import datetime, timezone, timedelta
        now = datetime.now(timezone.utc)
        fresh = 0
        for n in nodes:
            props = getattr(n, 'properties', {}) or {}
            ts = props.get("created_at") or props.get("_created_at") or ""
            if ts:
                try:
                    if isinstance(ts, (int, float)):
                        dt = datetime.fromtimestamp(ts, tz=timezone.utc)
                    else:
                        dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                    if (now - dt) < timedelta(days=7):
                        fresh += 1
                except Exception:
                    pass
        fresh_pct = fresh / total_nodes if total_nodes > 0 else 0

        # Connectivity
        avg_edges = total_edges / total_nodes if total_nodes > 0 else 0

        # Invalidation
        try:
            from ..cognition import get_cognition_stream
            stream = get_cognition_stream(ns)
            inv = stream.get_events(event_type="invalidate", limit=100)
            inv_count = len(set(nid for e in inv for nid in e.node_ids))
        except Exception:
            inv_count = 0

        # Format
        label_lines = [f"  {label}: {count}" for label, count in sorted(labels.items(), key=lambda x: -x[1])[:8]]

        lines = [
            f"Context Summary — {ns}",
            f"",
            f"Size: {total_nodes} nodes, {total_edges} edges",
            f"Freshness: {fresh_pct:.0%} within last 7 days",
            f"Connectivity: {avg_edges:.1f} edges/node",
            f"Invalidated: {inv_count} nodes",
            f"",
            f"Node types:",
        ]
        lines.extend(label_lines or ["  (empty graph)"])

        ctx.record_provenance("read", "context_summary", ns)
        return "\n".join(lines)
    except Exception as e:
        return f"Context summary unavailable: {e}"
