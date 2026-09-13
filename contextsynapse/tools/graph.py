"""
Graph Tools — universal implementations for all adapters.

8 tools: query_graph, add_knowledge, add_relationship, search_nodes,
         get_context, log_action, graph_summary, use_graph
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict

from .registry import ToolContext, ToolParam, tool
from .formatting import format_nodes, format_result, serialize_props_to_aiql

logger = logging.getLogger(__name__)


@tool(
    "query_graph", "graph",
    "Execute an AIQL query against the shared graph database. "
    "Supports CREATE NODE, SELECT, MATCH, CREATE EDGE, FIND, DELETE, SHOW GRAPHS, USE GRAPH.",
    params=[ToolParam("query", "string", "AIQL query string to execute")],
)
def _query_graph(ctx: ToolContext, query: str) -> str:
    result = ctx.conn.query(query)
    return format_result(result)


@tool(
    "add_knowledge", "graph",
    "Add a knowledge node to the shared graph. Store facts, findings, decisions, or insights.",
    params=[
        ToolParam("content", "string", 'The knowledge content to store'),
        ToolParam("node_type", "string", 'Node type: Fact, Finding, Insight, Decision, Knowledge', required=False, default="Knowledge"),
        ToolParam("label", "string", 'Node type (alias for node_type)', required=False, default=""),
        ToolParam("properties", "string", 'JSON properties (optional)', required=False, default="{}"),
        ToolParam("metadata", "string", 'JSON metadata (optional)', required=False, default="{}"),
    ],
)
def _add_knowledge(ctx: ToolContext, content: str = "", node_type: str = "Knowledge",
                   label: str = "", properties: str = "{}", metadata: str = "{}") -> str:
    # Support both calling styles: (content, node_type) and (label, properties)
    # Normalize to valid labels — agents often pass topic names as node_type
    _VALID_LABELS = {"Fact", "Finding", "Insight", "Decision", "Knowledge",
                     "Observation", "Note", "Pattern", "Preference"}
    # Agent-produced types go to the runtime graph, not the atomic context
    _RUNTIME_LABELS = {"Finding", "Insight", "Observation", "Note", "Pattern"}
    raw_label = label or node_type or "Knowledge"
    actual_label = raw_label if raw_label in _VALID_LABELS else "Finding"

    try:
        props = json.loads(properties) if properties and properties != "{}" else {}
    except json.JSONDecodeError:
        props = {}
    try:
        meta = json.loads(metadata) if metadata and metadata != "{}" else {}
    except json.JSONDecodeError:
        meta = {}

    if content:
        props["name"] = content[:100]
        props["content"] = content
        props["statement"] = content
    props.update(meta)

    props["_agent_id"] = ctx.agent_id or "unknown"
    props["_agent_name"] = ctx.agent_name

    # Schema validation (accept + flag)
    try:
        from ..ingestion.schema_extractor import load_schema
        from ..ingestion.schema_validator import validate_and_flag
        schema = load_schema(ctx.conn.contextcore)
        node_dict = {"label": actual_label, "properties": dict(props)}
        validated = validate_and_flag(node_dict, schema)
        v_props = validated["properties"]
        for vk in ("validation_status", "validation_errors", "_missing_fields"):
            if vk in v_props:
                props[vk] = v_props[vk]
        if v_props.get("confidence") != props.get("confidence"):
            props["confidence"] = v_props.get("confidence", props.get("confidence", 1.0))
    except Exception:
        pass  # validation failure should not block node creation

    prop_str = serialize_props_to_aiql(props)
    # Route by label type:
    # - Agent-produced intelligence (Finding, Insight, etc.) → runtime graph
    # - Source knowledge (Fact, Knowledge, Decision, Preference) → atomic graph
    target_conn = ctx.conn
    if ctx.runtime_conn is not None and ctx.agent_id and actual_label in _RUNTIME_LABELS:
        target_conn = ctx.runtime_conn

    result = target_conn.query(f'CREATE NODE {actual_label} {{{prop_str}}}')
    if result.get("success"):
        node_uuid = result.get("data", {}).get("uuid", "?")
        ctx.record_provenance("write", "node", str(node_uuid), {"label": actual_label})

        # Cognition: track derivation (what context did this agent recently consume?)
        try:
            from ..cognition import get_cognition_stream, DerivationTracker
            cog_ns = getattr(target_conn, 'namespace', '') or getattr(target_conn, '_namespace', '') or 'default'
            stream = get_cognition_stream(cog_ns)
            deriv_tracker = DerivationTracker(stream)
            recent_reads = stream.get_recent_reads(ctx.agent_id, limit=10)
            if recent_reads and ctx.agent_id:
                deriv_tracker.record_derivation(
                    agent_id=ctx.agent_id,
                    session_id=getattr(ctx, 'thread_id', ''),
                    output_node_id=str(node_uuid),
                    source_node_ids=recent_reads[:5],
                )
        except Exception:
            pass  # cognition tracking must never break writes

        # Reactive invalidation: invalidate other agents' working memory
        # whose topics overlap with the written content
        try:
            from ..context.working_memory import get_working_memory
            import re as _inv_re
            wm = get_working_memory()
            _inv_text = content or props.get("name", "")
            _inv_keywords = set(_inv_re.findall(r'\b[a-z]{4,}\b', _inv_text.lower()))
            _inv_ns = getattr(target_conn, 'namespace', '') or getattr(target_conn, '_namespace', '') or ''
            if _inv_ns and _inv_keywords:
                wm.invalidate_by_keywords(_inv_ns, _inv_keywords)
        except Exception:
            pass

        # Invalidate CGP projection cache for all agents when a runtime write happens.
        # Without this, other agents see stale projections for up to 60s.
        if target_conn is ctx.runtime_conn and ctx.conn:
            try:
                from ..context.projection import invalidate_projection_cache
                atomic_ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '')
                if atomic_ns:
                    invalidate_projection_cache(atomic_ns)
            except Exception as _e:
                import logging as _logging
                _logging.getLogger(__name__).debug("projection cache invalidation error: %s", _e)

        # Create CREATED_BY edge to agent
        if ctx.agent_id and target_conn:
            try:
                target_conn.query(f'CREATE EDGE CREATED_BY FROM "{node_uuid}" TO "{ctx.agent_id}" {{}}')
            except Exception:
                pass  # agent node may not exist yet

        # Projection feedback: when agent writes a Finding/Insight, detect which
        # projected nodes influenced it via keyword overlap
        if actual_label in _RUNTIME_LABELS and ctx.agent_id and content:
            try:
                from ..context.projection import record_usage, _projection_log
                log = _projection_log.get(ctx.agent_id)
                if log:
                    from ..context.projection import _extract_keywords
                    finding_kws = set(_extract_keywords(content).keys())
                    task_kws = set(log.get("task_keywords", []))
                    # Keywords that are NEW in the finding (not just repeating the task)
                    novel_kws = finding_kws - task_kws
                    if novel_kws:
                        # The agent learned something — signal it
                        for pid in log.get("projected_ids", set()):
                            record_usage(ctx.agent_id, pid)
            except Exception:
                pass

        # Auto-index into LMDB BM25 for keyword search
        text_for_index = content or props.get("name", "")
        if text_for_index:
            try:
                from ..search.lmdb_index import get_lmdb_index
                graph_ns = target_conn.namespace if hasattr(target_conn, "namespace") else ""
                if graph_ns:
                    lmdb_idx = get_lmdb_index(graph_ns)
                    lmdb_idx.index_node(str(node_uuid), actual_label, dict(props))
                    # Store full passage so hybrid search can return complete text
                    if content:
                        lmdb_idx.store_passage(str(node_uuid), content)
            except Exception:
                pass

        # Auto-embed into vector store for semantic search
        if text_for_index:
            try:
                from ..context.vector_integration import SessionVectorStore, get_session_vector_store
                graph_ns = target_conn.namespace if hasattr(target_conn, "namespace") else ""
                if graph_ns:
                    svs = get_session_vector_store()
                    if svs.available:
                        svs.add_text(
                            graph_ns, text_for_index, str(node_uuid),
                            metadata={"label": actual_label, "name": props.get("name", "")},
                        )
            except Exception:
                pass

        name = props.get("name", props.get("title", str(node_uuid)[:8]))
        response = f"Created [{actual_label}] node (id: {node_uuid})\nName: {name}"

        # Suggest related nodes (link suggestions)
        try:
            db = target_conn.contextcore
            all_nodes = db.csr_adapter.get_all_nodes()
            text_lower = content.lower() if content else ""
            suggestions = []
            for n in all_nodes:
                nid = n.id
                if nid == str(node_uuid):
                    continue
                nlabel = getattr(n, 'node_type', getattr(n, 'label', ''))
                if nlabel in ("ContextMeta", "VectorIndex", "BM25Index", "ToolCall"):
                    continue
                nname = n.properties.get("name", "")
                if nname and len(nname) > 2 and nname.lower() in text_lower:
                    suggestions.append(f"  - [{nlabel}] {nname} (id: {nid[:16]}...)")
            if suggestions:
                response += "\n\nRelated nodes found:\n" + "\n".join(suggestions[:5])
                response += "\n\nTo connect: add_relationship(\"" + str(node_uuid) + "\", \"<node_id>\", \"ABOUT\")"
        except Exception:
            pass

        return response
    return f"Error: {result.get('error', result.get('message', 'Unknown error'))}"


@tool(
    "request_promotion", "context",
    "Nominate a runtime node for promotion to atomic context. "
    "The node will be scored and either auto-promoted, sent to "
    "review queue, or kept in runtime based on quality score.",
    params=[
        ToolParam("node_id", "string", "ID of the runtime node to promote"),
        ToolParam("reason", "string", "Why this should be promoted", required=False, default=""),
    ],
)
def _request_promotion(ctx: ToolContext, node_id: str = "", reason: str = "") -> str:
    from ..context.promotion import PromotionScorer, PromotionQueue, promote_node, PROMOTION_DEFAULTS

    if not node_id:
        return "Error: node_id is required"

    rt_db = (getattr(ctx.runtime_conn, 'contextcore', None) or getattr(ctx.runtime_conn, 'db', None)) if ctx.runtime_conn else None
    if not rt_db:
        return "Error: no runtime connection available"

    node = rt_db.get_node(node_id) if hasattr(rt_db, "get_node") else None
    if not node:
        return f"Error: node {node_id} not found in runtime graph"

    config = dict(PROMOTION_DEFAULTS)
    if hasattr(ctx, "resolved_session") and ctx.resolved_session:
        session_config = getattr(ctx.resolved_session, "config", {}) or {}
        session_promo = session_config.get("promotion", {})
        config.update(session_promo)

    atomic_db = (getattr(ctx.conn, 'contextcore', None) or getattr(ctx.conn, 'db', None)) if ctx.conn else None
    scorer = PromotionScorer(config)
    result = scorer.score(node, rt_db, atomic_db)

    namespace = getattr(ctx.conn, "namespace", "") or getattr(ctx.conn, "_namespace", "")

    if result.action == "auto_promote":
        promoted_id = promote_node(node_id, ctx.runtime_conn, ctx.conn,
                                    promoted_by=ctx.agent_name or ctx.agent_id)
        if promoted_id:
            return (f"Auto-promoted to atomic context (score: {result.score:.2f}). "
                    f"New node ID: {promoted_id}. {result.reason}")
        return f"Promotion scored {result.score:.2f} but failed to create atomic node."

    elif result.action == "review":
        queue = PromotionQueue(namespace)
        submitted = queue.submit(
            node_id, result.score,
            reason=reason or result.reason,
            submitted_by=ctx.agent_name or ctx.agent_id,
        )
        if submitted:
            return (f"Submitted to review queue (score: {result.score:.2f}). "
                    f"A human reviewer will approve or reject. {result.reason}")
        return f"Already in review queue (score: {result.score:.2f})."

    else:
        return (f"Not eligible for promotion (score: {result.score:.2f}). "
                f"{result.reason}")


@tool(
    "add_relationship", "graph",
    "Create a relationship (edge) between two nodes in the graph.",
    params=[
        ToolParam("source_id", "string", "UUID of the source node"),
        ToolParam("target_id", "string", "UUID of the target node"),
        ToolParam("label", "string", 'Relationship type (e.g. "DEPENDS_ON", "FIXES")'),
        ToolParam("properties", "string", "Optional JSON string of edge properties", required=False, default="{}"),
    ],
)
def _add_relationship(ctx: ToolContext, source_id: str, target_id: str, label: str, properties: str = "{}") -> str:
    try:
        props = json.loads(properties)
    except json.JSONDecodeError:
        props = {}

    props["_agent_id"] = ctx.agent_id or "unknown"
    props["_agent_name"] = ctx.agent_name

    prop_str = " {" + serialize_props_to_aiql(props) + "}" if props else ""

    # Try runtime graph first — if either node exists there, edge goes to runtime
    target_conn = ctx.conn
    if ctx.runtime_conn is not None:
        try:
            rt_db = ctx.runtime_conn.db if hasattr(ctx.runtime_conn, 'db') else None
            if rt_db:
                adapter = getattr(rt_db, 'csr_adapter', None)
                if adapter and (adapter.get_node(source_id) or adapter.get_node(target_id)):
                    target_conn = ctx.runtime_conn
        except Exception:
            pass

    result = target_conn.query(f'CREATE EDGE {label} FROM "{source_id}" TO "{target_id}"{prop_str}')
    if result.get("success"):
        ctx.record_provenance("write", "edge", f"{source_id}->{target_id}", {"label": label})
        return f"Created -[{label}]-> edge\nFrom: {source_id} -> To: {target_id}"
    return f"Error: {result.get('error', result.get('message', 'Unknown'))}"


@tool(
    "search_nodes", "graph",
    "Smart search across the graph. Supports semantic query, label filter, and property filter. "
    "Automatically uses the best available method: vector search → BM25 → keyword scan → AIQL filter.",
    params=[
        ToolParam("query", "string", 'Search query (natural language or keywords)', required=False, default=""),
        ToolParam("label", "string", 'Node type to filter by (e.g. "Fact", "Entity")', required=False, default=""),
        ToolParam("where", "string", 'JSON string of property filters', required=False, default="{}"),
        ToolParam("limit", "string", 'Max results (default 20)', required=False, default="20"),
    ],
)
def _search_nodes(ctx: ToolContext, query: str = "", label: str = "", where: str = "{}", limit: str = "20") -> str:
    k = min(int(limit) if str(limit).isdigit() else 20, 50)

    # Fan-out: search attached graphs if available
    fan_out_graphs = getattr(ctx.conn, '_fan_out_graphs', [])
    fan_out_results = []
    if fan_out_graphs and query and query.strip():
        for fg_name, fg_db in fan_out_graphs:
            try:
                from ..search.lmdb_index import get_lmdb_index
                fg_idx = get_lmdb_index(fg_name)
                if fg_idx.stats().get("nodes", 0) > 0:
                    fg_results = fg_idx.hybrid_search(query, limit=k, label_filter=label.strip() if label else None)
                    for r in fg_results:
                        r["_source"] = fg_name
                        fan_out_results.append(r)
            except Exception:
                # Fallback: plain search on the graph
                try:
                    from ..search.rag import plain_search
                    raw = plain_search(fg_db, query, k=k)
                    if label:
                        raw = [r for r in raw if r.get("label", "") == label]
                    for r in raw:
                        r["_source"] = fg_name
                        fan_out_results.append(r)
                except Exception:
                    pass

            # Also search the attached graph's vector store (semantic matches BM25 misses)
            try:
                from ..context.vector_integration import get_session_vector_store
                svs = get_session_vector_store()
                if svs and svs.available:
                    vec_hits = svs.search(fg_name, query, k=k)
                    seen_fo = {r.get("name", "")[:50].lower() for r in fan_out_results if r.get("_source") == fg_name}
                    for h in vec_hits:
                        meta = h.get("metadata", {})
                        name = meta.get("name", meta.get("text", ""))[:120]
                        if name[:50].lower() not in seen_fo:
                            hlabel = meta.get("label", "")
                            if label and hlabel and hlabel != label.strip():
                                continue
                            fan_out_results.append({
                                "label": hlabel,
                                "name": name,
                                "snippet": meta.get("text", "")[:200],
                                "full_text": meta.get("text", "")[:200],
                                "score": h.get("score", 0),
                                "_source": fg_name,
                                "via": "semantic",
                            })
                            seen_fo.add(name[:50].lower())
            except Exception:
                pass

    # Ultra-fast path: LMDB BM25 (query-relevant) or Redis cache (popular)
    ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '')
    if label and label.strip():
        try:
            # Query provided → LMDB hybrid + Qdrant vector search
            if query and query.strip():
                from ..search.lmdb_index import get_lmdb_index
                lmdb_idx = get_lmdb_index(ns)
                lmdb_results = []
                if lmdb_idx.stats().get("nodes", 0) > 0:
                    lmdb_results = lmdb_idx.hybrid_search(query, limit=k, label_filter=label.strip())

                # Qdrant vector search on session namespace — skip if empty (fan-out handles attached graphs)
                vector_results = []
                _lmdb_has_content = lmdb_idx.stats().get("nodes", 0) > 0
                if _lmdb_has_content:  # only worth searching Qdrant if this namespace has indexed content
                    try:
                        from ..context.vector_integration import get_session_vector_store
                        svs = get_session_vector_store()
                        if svs and svs.available:
                            hits = svs.search(ns, query, k=k)
                            seen = {r.get("name", "").lower() for r in lmdb_results}
                            for h in hits:
                                meta = h.get("metadata", {})
                                name = meta.get("name", meta.get("text", ""))[:120]
                                if name.lower() not in seen:
                                    hlabel = meta.get("label", "")
                                    if label.strip() and hlabel != label.strip():
                                        continue  # respect label filter
                                    vector_results.append({
                                        "label": hlabel, "name": name,
                                        "snippet": meta.get("text", "")[:200],
                                        "score": h.get("score", 0),
                                        "via": "semantic",
                                    })
                                    seen.add(name.lower())
                    except Exception:
                        pass

                # Merge: LMDB first (keyword relevant), then Qdrant (semantic)
                all_results = lmdb_results + vector_results
                # Also include fan-out results from attached graphs
                if fan_out_results:
                    seen_names = {r.get("name", r.get("full_text", ""))[:50].lower() for r in all_results}
                    for fo in fan_out_results:
                        nm = fo.get("name", fo.get("full_text", fo.get("snippet", "")))[:50].lower()
                        if nm not in seen_names:
                            all_results.append(fo)
                            seen_names.add(nm)
                # Filter out noise: short text, navigation boilerplate, non-content
                all_results = [r for r in all_results
                               if len(r.get("full_text", r.get("snippet", r.get("name", "")))) > 30]
                if all_results:
                    lines = [f"Found {len(all_results)} node(s):"]
                    for r in all_results[:k]:
                        text = r.get("full_text", r.get("snippet", r.get("name", "")))[:200]
                        via = f" (via {r['via']})" if r.get("via") else ""
                        src = f" [from {r['_source']}]" if r.get("_source") else ""
                        lines.append(f"  [{r.get('label', '?')}] {text}{via}{src}")
                    ctx.record_provenance("read", "search_nodes", (query or label)[:100])
                    return "\n".join(lines)

            # No query → Redis cache (top by popularity)
            from ..context.quality import ResultCache
            import os as _os
            if not hasattr(_search_nodes, '_redis'):
                import redis as _redis_mod
                _search_nodes._redis = _redis_mod.from_url(_os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379"))
            cache = ResultCache(_search_nodes._redis)
            cached = cache.get_by_label(ns, label.strip())
            if cached:
                result = cache.format_results(cached[:k])
                ctx.record_provenance("read", "search_nodes", label[:100])
                return result
        except Exception:
            pass  # fall through to normal path

    # Fast path: hybrid search — BM25 + graph traversal + keyword fallback (no AIQL)
    if query and query.strip():
        db = getattr(ctx.conn, 'contextcore', None) or (ctx.conn.db if hasattr(ctx.conn, 'db') else None)
        ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '')

        def _hybrid_search_db(target_db, target_ns, search_query, limit):
            """Run graph_search (BM25 + hop traversal + rerank), fallback to plain_search."""
            hits = []

            def _run_graph_search():
                """Execute graph_search and convert SearchNode list to flat dicts."""
                results = []
                try:
                    from ..search.graph_search import graph_search as _gs
                    gsr = _gs(target_db, search_query, graph_name=target_ns, k=limit + 5, max_hops=1)
                    for sn in gsr.nodes:
                        name = sn.props.get("name", sn.props.get("title", ""))[:120]
                        snippet = (sn.props.get("statement") or sn.props.get("content")
                                   or sn.props.get("description") or "")[:200]
                        results.append({
                            "id": sn.node_id,
                            "label": sn.label, "name": name, "snippet": snippet,
                            "score": sn.score, "_hop": sn.distance,
                            "_via": sn.via_edge if sn.distance > 0 else "",
                        })
                except Exception:
                    logger.debug("graph_search failed for ns=%s query=%r", target_ns, search_query, exc_info=True)
                return results

            # Strategy 1: graph_search — LMDB BM25 seeds → edge traversal → RRF rerank
            hits = _run_graph_search()

            # If LMDB index is empty (graph loaded from disk without prior indexing), rebuild it
            if not hits:
                try:
                    from ..search.lmdb_index import get_lmdb_index, build_lmdb_index
                    idx = get_lmdb_index(target_ns)
                    if idx.stats().get("nodes", 0) == 0:
                        build_lmdb_index(target_db, target_ns)
                        hits = _run_graph_search()  # retry with populated index
                except Exception:
                    logger.debug("LMDB rebuild failed for ns=%s", target_ns, exc_info=True)

            # Strategy 2: plain keyword scan fallback (always works, no index needed)
            if not hits:
                try:
                    from ..search.rag import plain_search
                    kw = plain_search(target_db, search_query, k=limit + 10)
                    hits = kw
                    if not hits:
                        logger.debug("plain_search returned nothing for ns=%s query=%r", target_ns, search_query)
                except Exception:
                    logger.debug("plain_search failed for ns=%s", target_ns, exc_info=True)
            return hits

        if db:
            raw = _hybrid_search_db(db, ns, query, k)
            # Filter by label if specified
            if label:
                raw = [r for r in raw if r.get("label", "") == label]
            raw = raw[:k]
            # Fan-out: also search runtime graph for agent-produced findings/insights
            if ctx.runtime_conn is not None and not label:
                try:
                    rt_db = getattr(ctx.runtime_conn, 'contextcore', None) or (ctx.runtime_conn.db if hasattr(ctx.runtime_conn, 'db') else None)
                    rt_ns = getattr(ctx.runtime_conn, 'namespace', '') or getattr(ctx.runtime_conn, '_namespace', '')
                    if rt_db:
                        rt_raw = _hybrid_search_db(rt_db, rt_ns, query, k)
                        seen = {r.get("id", r.get("node_id", "")) for r in raw}
                        for r in rt_raw:
                            rid = r.get("id", r.get("node_id", ""))
                            if rid and rid not in seen:
                                r["_source"] = "runtime"
                                raw.append(r)
                                seen.add(rid)
                except Exception:
                    pass
            raw = raw[:k]
            # Merge with fan-out results (attached contexts)
            if fan_out_results:
                seen_ids = {r.get("id", r.get("node_id", "")) for r in raw}
                for fo in fan_out_results:
                    fid = fo.get("id", fo.get("node_id", ""))
                    if (not fid or fid not in seen_ids) and (not label or fo.get("label") == label):
                        raw.append(fo)
                        if fid:
                            seen_ids.add(fid)
                raw = raw[:k]
            if raw:
                lines = [f"Found {len(raw)} node(s) matching '{query}':"]
                for r in raw:
                    name = r.get("name", "")[:120]
                    snippet = r.get("snippet", "")[:200]
                    line = f"  [{r.get('label', '?')}] {name}"
                    if snippet and snippet not in name:
                        line += f" — {snippet}"
                    tags = []
                    if r.get("_source"):
                        tags.append(f"from {r['_source']}")
                    if r.get("_hop", 0) > 0:
                        tags.append(f"{r['_hop']}-hop via {r.get('_via', 'edge')}")
                    if tags:
                        line += f" [{', '.join(tags)}]"
                    lines.append(line)
                ctx.record_provenance("read", "search_nodes", (query or label)[:100])
                return "\n".join(lines)
            if label:
                # Label didn't match — try without label filter (agent may have used topic as label)
                raw_no_label = _hybrid_search_db(db, ns, query, k)
                if raw_no_label:
                    lines = [f"No '{label}' label found. Showing all matches for '{query}':"]
                    for r in raw_no_label:
                        name = r.get("name", "")[:120]
                        snippet = r.get("snippet", "")[:200]
                        line = f"  [{r.get('label', '?')}] {name}"
                        if snippet and snippet not in name:
                            line += f" — {snippet}"
                        lines.append(line)
                    ctx.record_provenance("read", "search_nodes", (query or label)[:100])
                    return "\n".join(lines)
                # Record context gap
                try:
                    from ..intelligence.context_gaps import record_gap
                    record_gap(query=query, label=label, agent_name=ctx.agent_name, namespace=ns)
                except Exception:
                    pass
                return f"No nodes found matching '{query}'. Try search(query=\"{query}\") instead."

    try:
        where_dict = json.loads(where) if where and where != "{}" else None
    except json.JSONDecodeError:
        where_dict = None

    # Infrastructure node types — filter these out of search results
    INFRA_LABELS = {
        "VectorIndex", "BM25Index", "Session", "ContextRef",
        "ExperimentRun", "ExperimentScore", "AgentAction", "AgentThought",
        "AgentPresence", "AgentStep", "AgentMessage", "PipelineRun",
        "Checkpoint", "ToolCall",
        "Context", "KnowledgeBase", "CodeBase", "SystemStore", "UserStore",
        "WebStore", "GeneratedStore", "MemoryStore", "ArtifactStore", "ToolStore",
    }

    results = []
    search_method = "aiql"
    db = getattr(ctx.conn, 'contextcore', None) or (ctx.conn.db if hasattr(ctx.conn, 'db') else None)
    graph_name = ctx.conn.namespace if hasattr(ctx.conn, 'namespace') else ""

    # ── Discover search indexes (cached — avoid full graph scan) ──
    vec_collection = None
    vec_model = None
    bm25_path = None
    bm25_ns = None
    if db:
        # Try cached lookup first (O(1) by node ID pattern)
        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            # Look for known pointer node IDs by label
            for n_id in [f"{graph_name}_vector_index", f"{graph_name}_bm25_index"]:
                try:
                    n = adapter.get_node(n_id)
                    if n:
                        lbl = getattr(n, "label", "")
                        props = getattr(n, "properties", {}) or {}
                        if lbl == "VectorIndex" and props.get("status") == "active":
                            vec_collection = props.get("collection", "")
                            vec_model = props.get("embedding_model", "")
                        elif lbl == "BM25Index" and props.get("status") == "active":
                            bm25_path = props.get("index_path", "")
                            bm25_ns = props.get("graph_namespace", graph_name)
                except Exception:
                    pass
            # If no cached IDs found, fall back to namespace-based collection name
            if not vec_collection:
                vec_collection = graph_name
            if not bm25_ns:
                bm25_ns = graph_name
        except Exception:
            vec_collection = graph_name
            bm25_ns = graph_name

    # ── Strategy 1: Semantic query → vector → BM25 → keyword ──
    if query and query.strip():

        # Helper: extract BM25 results from Whoosh response dict
        def _parse_bm25(response):
            hits = []
            if isinstance(response, dict) and response.get("success"):
                for item in response.get("results", []):
                    hits.append({
                        "id": item.get("id", item.get("doc_id", "")),
                        "label": item.get("label", "Fact"),
                        "properties": {"content": item.get("text", item.get("content", "")), "name": item.get("name", "")},
                        "score": item.get("score", 0),
                        "name": item.get("name", item.get("text", "")[:80]),
                    })
            return hits

        # Try vector search — use pointer node collection OR direct namespace lookup
        if not results:
            try:
                from ..context.vector_integration import get_session_vector_store
                vs = get_session_vector_store()
                if vs.available:
                    ns = vec_collection or graph_name
                    vec_results = vs.search(ns, query, k=k)
                    if vec_results:
                        search_method = "vector"
                        for vr in vec_results:
                            nid = vr.get("node_id") or vr.get("id", "")
                            meta = vr.get("metadata", {})
                            results.append({
                                "id": nid,
                                "label": vr.get("label", meta.get("label", "")),
                                "properties": meta,
                                "score": vr.get("score", 0),
                                "name": vr.get("text", "")[:100] or meta.get("name", ""),
                            })
            except Exception:
                pass

        # Try BM25 search — use pointer node path OR default namespace path
        if not results:
            try:
                import os
                paths_to_try = []
                if bm25_path:
                    paths_to_try.append(bm25_path)
                if graph_name:
                    safe_ns = graph_name.replace(":", "_")
                    paths_to_try.append(f"contextcore_data/bm25_index/{safe_ns}")
                for bp in paths_to_try:
                    if os.path.isdir(bp):
                        from ..search.whoosh_search import WhooshSearchEngine, WhooshConfig
                        engine = WhooshSearchEngine(WhooshConfig(index_dir=bp))
                        bm25_response = engine.search(query, limit=k)
                        hits = _parse_bm25(bm25_response)
                        if hits:
                            search_method = "bm25"
                            results = hits
                            break
            except Exception:
                pass

        # Try BM25 with source context paths (check _source_graph on nodes)
        if not results and db:
            try:
                import os
                source_graphs = set()
                for n in db.get_all_nodes():
                    sg = (n.properties or {}).get("_source_graph", "")
                    if sg:
                        source_graphs.add(sg)
                for sg in source_graphs:
                    safe_sg = sg.replace(":", "_")
                    fallback = f"contextcore_data/bm25_index/{safe_sg}"
                    if os.path.isdir(fallback):
                        from ..search.whoosh_search import WhooshSearchEngine, WhooshConfig
                        engine = WhooshSearchEngine(WhooshConfig(index_dir=fallback))
                        bm25_response = engine.search(query, limit=k)
                        hits = _parse_bm25(bm25_response)
                        if hits:
                            search_method = "bm25"
                            results = hits
                            break
            except Exception:
                pass

        # Keyword scan (always available — no index needed)
        if not results:
            db = db or getattr(ctx.conn, 'contextcore', None)
        if not results and db:
            try:
                from ..search.rag import plain_search
                kw_results = plain_search(db, query, k=k)
                if kw_results:
                    search_method = "keyword"
                    for kr in kw_results:
                        results.append({
                            "id": kr.get("node_id", ""), "label": kr.get("label", ""),
                            "properties": {"name": kr.get("name", ""), "snippet": kr.get("snippet", "")},
                            "score": kr.get("score", 0), "name": kr.get("name", ""),
                        })
            except Exception:
                pass

    # ── Strategy 2: Label/property filter via AIQL (exact match) ──
    if not results or (label and not query):
        where_clause = ""
        if where_dict:
            from ..security.sanitize import sanitize_aiql_identifier, sanitize_aiql_value
            conditions = " AND ".join(f'{sanitize_aiql_identifier(k)} = "{sanitize_aiql_value(v)}"' for k, v in where_dict.items())
            where_clause = f" WHERE {conditions}"
        safe_lbl = label
        try:
            from ..security.sanitize import sanitize_aiql_identifier
            safe_lbl = sanitize_aiql_identifier(label) if label else ""
        except Exception:
            pass
        aiql = f"SELECT * FROM {safe_lbl}{where_clause}" if safe_lbl else f"SELECT *{where_clause}"
        result = ctx.conn.query(aiql)
        aiql_nodes = result.get("nodes", [])

        # When both query and label provided, filter AIQL results by query keywords
        if query and aiql_nodes:
            q_terms = [w.lower() for w in query.split() if len(w) > 2]
            if q_terms:
                scored_nodes = []
                for n in aiql_nodes:
                    props = n.get("properties", {}) if isinstance(n, dict) else {}
                    text = " ".join(str(v) for v in props.values() if isinstance(v, str)).lower()
                    hits = sum(1 for t in q_terms if t in text)
                    if hits > 0:
                        n_copy = dict(n) if isinstance(n, dict) else n
                        if isinstance(n_copy, dict):
                            n_copy["score"] = round(hits / len(q_terms), 2)
                        scored_nodes.append(n_copy)
                scored_nodes.sort(key=lambda x: x.get("score", 0) if isinstance(x, dict) else 0, reverse=True)
                aiql_nodes = scored_nodes

        if query and results:
            # Merge: AIQL results filtered by label on top of semantic results
            if label:
                results = [r for r in results if r.get("label", "").lower() == label.lower()] + aiql_nodes[:k]
        elif aiql_nodes:
            search_method = "aiql"
            # Sort AIQL results by recency — newest first (default when no query)
            def _get_created(n):
                props = n.get("properties", {}) if isinstance(n, dict) else {}
                return props.get("_created_at", "")
            aiql_nodes.sort(key=_get_created, reverse=True)
            results = aiql_nodes

    # Apply label filter to semantic results if both query and label provided
    if label and query and results:
        filtered = [r for r in results if (r.get("label") or "").lower() == label.lower()]
        if filtered:
            results = filtered
        elif search_method == "vector":
            # Vector search returned no matching labels — fall back to AIQL for exact label match
            try:
                from ..security.sanitize import sanitize_aiql_identifier
                safe_label = sanitize_aiql_identifier(label)
                aiql_result = ctx.conn.query(f"SELECT * FROM {safe_label}")
                aiql_nodes = aiql_result.get("nodes", [])
                if aiql_nodes:
                    results = aiql_nodes[:k]
                    search_method = "aiql"
            except Exception:
                pass

    # Filter out infrastructure nodes (unless specifically searching for them)
    if label not in INFRA_LABELS:
        results = [r for r in results if (r.get("label") or "") not in INFRA_LABELS]

    # Also search runtime graph for agent-produced content (tasks, findings, actions)
    _RUNTIME_SEARCH_LABELS = {"Task", "Finding", "Insight", "AgentAction", "Decision",
                              "Observation", "Note", "Pattern"}
    if ctx.runtime_conn is not None and (
        label in _RUNTIME_SEARCH_LABELS or (not label and query)
    ):
        try:
            rt_db = getattr(ctx.runtime_conn, 'contextcore', None) or (ctx.runtime_conn.db if hasattr(ctx.runtime_conn, 'db') else None)
            if rt_db:
                from ..search.rag import plain_search
                rt_results = plain_search(rt_db, query or label or "", k=k)
                if label:
                    rt_results = [r for r in rt_results if (r.get("label") or "") == label]
                for r in rt_results:
                    r["_source"] = "runtime"
                results.extend(rt_results)
        except Exception:
            pass

    # Format output
    results = results[:k]
    if not results:
        filter_desc = f" label={label}" if label else ""
        query_desc = f" query='{query}'" if query else ""
        # Record context gap
        try:
            from ..intelligence.context_gaps import record_gap
            record_gap(query=query or label, label=label, agent_name=ctx.agent_name, namespace=ns)
        except Exception:
            pass
        return f"No nodes found.{filter_desc}{query_desc}"

    # ── Edge-neighbor expansion: show connected nodes for richer context ──
    neighbor_lines = []
    if db and results:
        try:
            all_edges = db.get_all_edges()
            if all_edges:
                result_ids = set()
                for r in results:
                    rid = r.get("id", "") if isinstance(r, dict) else str(getattr(r, "id", ""))
                    if rid:
                        result_ids.add(rid)

                # Build adjacency from edges
                adj = {}  # node_id -> [(edge_label, neighbor_id)]
                for e in all_edges:
                    src = str(getattr(e, "source", getattr(e, "from_id", ""))) if not isinstance(e, dict) else str(e.get("source", e.get("from_id", "")))
                    tgt = str(getattr(e, "target", getattr(e, "to_id", ""))) if not isinstance(e, dict) else str(e.get("target", e.get("to_id", "")))
                    elbl = getattr(e, "label", "RELATED") if not isinstance(e, dict) else e.get("label", e.get("edge_type", "RELATED"))
                    adj.setdefault(src, []).append((elbl, tgt))
                    adj.setdefault(tgt, []).append((elbl, src))

                # Build node name lookup
                node_lookup = {}
                for n in db.get_all_nodes():
                    nid = str(getattr(n, "id", ""))
                    props = getattr(n, "properties", {}) or {}
                    nlbl = getattr(n, "label", "")
                    nname = props.get("name") or props.get("title") or ""
                    node_lookup[nid] = (nlbl, nname)

                # Collect neighbors for top results (max 5 neighbors total)
                seen_neighbors = set()
                for rid in list(result_ids)[:5]:
                    for edge_label, neighbor_id in adj.get(rid, [])[:3]:
                        if neighbor_id not in result_ids and neighbor_id not in seen_neighbors:
                            seen_neighbors.add(neighbor_id)
                            nlbl, nname = node_lookup.get(neighbor_id, ("?", ""))
                            if nlbl not in INFRA_LABELS:
                                src_lbl, src_name = node_lookup.get(rid, ("?", ""))
                                neighbor_lines.append(
                                    f"    → {src_name or rid[:12]} -[{edge_label}]-> [{nlbl}] {nname or neighbor_id[:12]}"
                                )
                            if len(seen_neighbors) >= 5:
                                break
                    if len(seen_neighbors) >= 5:
                        break
        except Exception:
            pass

    # Format as readable text
    lines = [f"Found {len(results)} node(s) via {search_method}:"]
    for r in results:
        if isinstance(r, dict):
            nid = r.get("id", "")[:12]
            nlabel = r.get("label", "?")
            props = r.get("properties", {})
            name = props.get("name", "") or props.get("title", "") or r.get("name", "")
            snippet = props.get("snippet", "") or props.get("statement", "") or props.get("description", "") or props.get("content", "")
            score = r.get("score", "")
            line = f"  [{nlabel}] {name or nid}"
            if snippet:
                line += f" — {str(snippet)[:120]}"
            if score:
                line += f" (score: {score})"
            lines.append(line)
        else:
            lines.append(format_nodes([r]))

    if neighbor_lines:
        lines.append("\nRelated (via graph edges):")
        lines.extend(neighbor_lines)

    # Append fan-out results from attached graphs
    if fan_out_results:
        seen_names = set()
        for line in lines:
            if line.startswith("  ["):
                # Extract name from "  [Label] Name"
                parts = line.split("] ", 1)
                if len(parts) > 1:
                    seen_names.add(parts[1].strip()[:50].lower())

        extra_lines = []
        for r in fan_out_results[:k]:
            text = r.get("full_text", r.get("snippet", r.get("name", "")))[:200]
            if text[:50].lower() in seen_names:
                continue  # skip duplicates
            source = r.get("_source", "attached")
            extra_lines.append(f"  [{r.get('label', '?')}] {text} (from {source})")

        if extra_lines:
            if not lines or lines == [f"No results found for '{query}'."]:
                lines = [f"Found {len(extra_lines)} node(s) from attached contexts:"]
            else:
                lines.append(f"\nFrom attached contexts:")
            lines.extend(extra_lines[:k])

    # Cognition: track context reads
    try:
        from ..cognition import get_cognition_stream, ReadTracker
        cog_ns = ns or getattr(ctx.conn, '_namespace', '') or 'default'
        stream = get_cognition_stream(cog_ns)
        tracker = ReadTracker(stream)
        returned_ids = [r.get("id", "") for r in results if isinstance(r, dict) and r.get("id")]
        if returned_ids and ctx.agent_id:
            tracker.record_read(agent_id=ctx.agent_id, session_id=getattr(ctx, 'thread_id', ''), node_ids=returned_ids)
    except Exception:
        pass  # cognition tracking must never break search

    ctx.record_provenance("read", "search_nodes", (query or label or "all")[:100])
    return "\n".join(lines)


@tool(
    "get_context", "graph",
    "Build LLM-ready context from the shared graph. Returns formatted prompt "
    "containing the graph's knowledge, structured and token-budgeted.",
    params=[
        ToolParam("system_prompt", "string", "System instruction", required=False,
                  default="You are an AI assistant with access to a shared knowledge graph."),
        ToolParam("node_types", "string", 'Comma-separated node types (empty = all)', required=False, default=""),
        ToolParam("max_tokens", "integer", "Max token budget", required=False, default=4000),
    ],
)
def _get_context(ctx: ToolContext, system_prompt: str = "You are an AI assistant.", node_types: str = "", max_tokens: int = 4000) -> str:
    types_list = [t.strip() for t in node_types.split(",") if t.strip()] if node_types else None
    # Reserve 80% budget for atomic knowledge, 20% for runtime status
    rt_budget = max_tokens // 5
    knowledge_budget = max_tokens - rt_budget

    hub = ctx.conn.build_context(
        system_prompt=system_prompt,
        node_types=types_list,
        max_tokens=knowledge_budget,
    )
    result = hub.to_prompt()

    # Layer in runtime context (tasks, findings, agent actions) if available
    if ctx.runtime_conn is not None:
        try:
            rt_hub = ctx.runtime_conn.build_context(
                system_prompt="",
                max_tokens=rt_budget,
            )
            rt_text = rt_hub.to_prompt()
            if rt_text and len(rt_text.strip()) > 20:
                result += "\n\n--- RUNTIME (agent work in progress) ---\n" + rt_text
        except Exception:
            pass

    return result


@tool(
    "log_action", "graph",
    "Log an action taken by this agent so other agents can see what happened.",
    params=[
        ToolParam("action", "string", 'Action type (e.g. "bug_fix", "refactor", "decision")'),
        ToolParam("description", "string", "What you did and why"),
        ToolParam("files_changed", "string", "Comma-separated files affected", required=False, default=""),
        ToolParam("tags", "string", "Comma-separated tags", required=False, default=""),
    ],
)
def _log_action(ctx: ToolContext, action: str, description: str, files_changed: str = "", tags: str = "") -> str:
    props = {
        "action": action,
        "description": description,
        "agent": ctx.agent_name,
        "agent_id": ctx.agent_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    if files_changed:
        props["files"] = files_changed
    if tags:
        props["tags"] = tags

    # Agent actions are runtime artifacts, not source knowledge
    target_conn = ctx.runtime_conn if ctx.runtime_conn is not None else ctx.conn
    prop_str = serialize_props_to_aiql(props)
    result = target_conn.query(f'CREATE NODE AgentAction {{{prop_str}}}')
    if result.get("success"):
        node_uuid = result.get("data", {}).get("uuid", "?")
        ctx.record_provenance("write", "node", node_uuid, {"action": action, "label": "AgentAction"})
        return f"Logged [{action}] by {ctx.agent_name} (id: {node_uuid})\nDescription: {description}"
    return f"Error logging action: {result.get('error', 'Unknown')}"


@tool(
    "graph_summary", "graph",
    "Get a summary of the current graph — node counts by type, edge counts, available namespaces.",
)
def _graph_summary(ctx: ToolContext) -> str:
    INFRA = {
        "VectorIndex", "BM25Index", "Session", "ContextRef",
        "ExperimentRun", "ExperimentScore", "AgentAction", "AgentThought",
        "AgentMessage", "PipelineRun",
        "Context", "KnowledgeBase", "CodeBase", "SystemStore", "UserStore",
        "WebStore", "GeneratedStore", "MemoryStore", "ArtifactStore", "ToolStore",
    }

    # Use get_statistics() — label index-based, avoids full SELECT * scan
    db = getattr(ctx.conn, 'contextcore', None) or (ctx.conn.db if hasattr(ctx.conn, 'db') else None)
    stats_data: Dict[str, Any] = {}
    if db and hasattr(db, 'get_statistics'):
        try:
            stats_data = db.get_statistics()
        except Exception:
            pass

    label_counts: Dict[str, int] = stats_data.get("node_count_by_label", {})
    total_edges = stats_data.get("total_edges", 0)

    # Fallback: fetch all edges separately for edge count if stats didn't return them
    if not total_edges:
        try:
            total_edges = len(ctx.conn.get_edges())
        except Exception:
            total_edges = 0

    content_types: Dict[str, int] = {}
    infra_types: Dict[str, int] = {}
    vec_status = "none"
    bm25_status = "none"

    for label, count in label_counts.items():
        if label in INFRA:
            infra_types[label] = count
        else:
            content_types[label] = count

    # For VectorIndex / BM25Index status we need at most one node each — targeted fetch
    if "VectorIndex" in label_counts and db:
        try:
            vi_nodes = db.get_all_nodes(label="VectorIndex", limit=1)
            if vi_nodes:
                props = getattr(vi_nodes[0], "properties", {}) or {}
                vec_status = props.get("status", "pending")
        except Exception:
            vec_status = "present"
    if "BM25Index" in label_counts and db:
        try:
            bi_nodes = db.get_all_nodes(label="BM25Index", limit=1)
            if bi_nodes:
                props = getattr(bi_nodes[0], "properties", {}) or {}
                bm25_status = props.get("status", "pending")
        except Exception:
            bm25_status = "present"

    # Fallback to full scan only when get_statistics not available
    if not label_counts:
        all_result = ctx.conn.query("SELECT *")
        all_nodes = all_result.get("nodes", [])
        for n in all_nodes:
            label = n.get("label", "Unknown") if isinstance(n, dict) else getattr(n, "label", "Unknown")
            props = n.get("properties", {}) if isinstance(n, dict) else getattr(n, "properties", {})
            if label in INFRA:
                infra_types[label] = infra_types.get(label, 0) + 1
                if label == "VectorIndex":
                    vec_status = props.get("status", "pending")
                elif label == "BM25Index":
                    bm25_status = props.get("status", "pending")
            else:
                content_types[label] = content_types.get(label, 0) + 1

    content_count = sum(content_types.values())
    # Use total_edges from stats; fall back to zero for edge display
    edges = total_edges
    lines = [
        f"Graph: {ctx.conn.namespace}",
        f"Content nodes: {content_count}",
        f"Total edges: {edges}",
        "",
        "Content types (searchable):",
    ]
    if content_types:
        for t, c in sorted(content_types.items(), key=lambda x: -x[1]):
            lines.append(f"  {t}: {c}")
    else:
        lines.append("  (empty graph)")

    # Check LMDB index status (the actual live BM25 engine used by search_nodes)
    lmdb_status = "none"
    try:
        from ..search.lmdb_index import get_lmdb_index
        ns_name = ctx.conn.namespace if hasattr(ctx.conn, 'namespace') else ""
        if ns_name:
            lmdb_idx = get_lmdb_index(ns_name)
            lmdb_stats = lmdb_idx.stats()
            lmdb_node_count = lmdb_stats.get("nodes", 0)
            if lmdb_node_count > 0:
                lmdb_status = f"active ({lmdb_node_count} nodes, {lmdb_stats.get('backend', 'lmdb')})"
            else:
                lmdb_status = "empty (will rebuild on first search)"
    except Exception:
        pass

    lines.append("")
    lines.append("Search indexes:")
    lines.append(f"  Vector search: {vec_status}")
    lines.append(f"  BM25 + graph traversal: {lmdb_status}")
    lines.append(f"  Keyword scan: active (always available)")
    if bm25_status != "none":
        lines.append(f"  Whoosh BM25: {bm25_status}")

    return "\n".join(lines)


@tool(
    "topic_scan", "graph",
    "Scan graph content and return topic clusters with example nodes. "
    "No LLM needed — pure keyword analysis. Shows what topics exist "
    "and sample data for each. Use before searching to know what to look for.",
)
def _topic_scan(ctx: ToolContext) -> str:
    db = getattr(ctx.conn, 'contextcore', None) or (ctx.conn.db if hasattr(ctx.conn, 'db') else None)
    if not db:
        return "No graph available."
    ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '')
    from ..search.rag import topic_scan_text
    return topic_scan_text(db, ns)


def _build_presence_section(ctx) -> str:
    """Build the ACTIVE AGENTS section for orient() showing who is working on what.

    Only shows other agents' in-progress tasks (not the calling agent's own tasks).
    Returns an empty string if no other agents have claimed tasks.
    """
    if not ctx.project:
        return ""
    try:
        tasks = ctx.project.get_open_tasks()
        my_id = ctx.agent_id or ""
        my_name = (ctx.agent_name or "").lower()

        # Group in-progress tasks by agent, excluding self
        agent_tasks: dict = {}
        for t in tasks:
            assigned = t.get("assigned_to", "")
            if not assigned:
                continue
            if assigned == my_id or assigned.lower() == my_name:
                continue
            if t.get("status") == "in_progress":
                agent_tasks.setdefault(assigned, []).append(t.get("title", t.get("id", "?")))

        if not agent_tasks:
            return ""

        lines = ["\nACTIVE AGENTS (don't duplicate their work):"]
        for agent, task_titles in sorted(agent_tasks.items()):
            titles_str = ", ".join(t[:60] for t in task_titles[:3])
            if len(task_titles) > 3:
                titles_str += f" (+{len(task_titles) - 3} more)"
            lines.append(f"  • {agent}: {titles_str}")
        return "\n".join(lines)
    except Exception as _e:
        logger.debug("_build_presence_section error: %s", _e)
        return ""


@tool(
    "orient", "graph",
    "Get a complete orientation to this graph — summary, topics, your tasks, "
    "team status, and suggested next actions. Call this first when you connect.",
)
def _orient(ctx: ToolContext) -> str:
    """One-call orientation — full context intelligence for any agent."""
    # Tier 1: Working Memory cache (sub-5ms)
    try:
        from ..context.working_memory import get_working_memory
        wm = get_working_memory()
        _wm_ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '') or ''
        _wm_cached = wm.get(ctx.agent_id, _wm_ns)
        if _wm_cached:
            ctx.record_provenance("read", "orient", _wm_ns)
            return _wm_cached["content"]
    except Exception:
        pass

    # Tier 2: Session Memory — cross-agent signals (sub-10ms)
    _session_context = None
    try:
        from ..context.session_memory import get_session_memory
        sm = get_session_memory()
        _wm_ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '') or ''
        _sm_sid = getattr(ctx, 'session_id', '') or ''
        if _wm_ns and _sm_sid:
            _session_context = sm.get_context_for_agent(_wm_ns, _sm_sid, ctx.agent_id)
    except Exception:
        pass

    def _cache_orient_result(result_text):
        """Cache orient result in working memory before returning."""
        try:
            from ..context.working_memory import get_working_memory
            from ..context.session_memory import get_access_tracker
            import re as _wm_re
            from collections import Counter as _WmCounter
            wm = get_working_memory()
            ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '') or ''
            words = set(_wm_re.findall(r'\b[a-z]{4,}\b', result_text.lower()))
            stop = {"this","that","with","from","have","been","will","would","your","what",
                    "also","just","more","here","know","were","there","about","each","some"}
            words -= stop
            topic_kw = {w for w, _ in _WmCounter(words).most_common(20)}
            wm.set(ctx.agent_id, ns, result_text, topic_keywords=topic_kw)

            # Track node access for auto-promotion
            tracker = get_access_tracker()
            node_ids = _wm_re.findall(r'[a-f0-9]{8,}', result_text[:2000])
            if node_ids and ns:
                tracker.increment(ns, node_ids[:50])
        except Exception:
            pass

        # Append cross-agent signals from Tier 2
        if _session_context and _session_context.get("cross_agent_signals"):
            signals = _session_context["cross_agent_signals"]
            sig_lines = ["\nCROSS-AGENT SIGNALS (live):"]
            for s in signals[:5]:
                sig_lines.append(f"  [{s.get('agent_id', '?')}] ({s.get('signal_type', '?')}) {s.get('content', '')[:80]}")
            result_text = result_text + "\n" + "\n".join(sig_lines)

        return result_text

    db = ctx.conn.db if hasattr(ctx.conn, 'db') else None
    ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '')

    # Try manifest first (fast, <100ms)
    try:
        from ..context.quality import get_manifest, ManifestBuilder
        import os as _os
        redis_client = None
        try:
            import redis as _redis_mod
            redis_client = _redis_mod.from_url(_os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379"))
        except Exception:
            pass
        manifest = get_manifest(ns, redis_client)
        if manifest:
            result = ManifestBuilder.to_text(manifest)
            # Add Context Units to orient
            try:
                from ..search.lmdb_index import get_lmdb_index
                lmdb_idx = get_lmdb_index(ns)
                cu_results = lmdb_idx.search("", limit=10, label_filter="ContextUnit")
                if cu_results:
                    cu_lines = ["\nCONTEXT UNITS (pre-assembled understanding):"]
                    for cu in cu_results[:5]:
                        cu_lines.append(f"  [{cu.get('name', '?')}] {cu.get('snippet', '')[:100]}")
                    result = result + "\n" + "\n".join(cu_lines)
            except Exception:
                pass
            if result and len(result) > 50:
                # Try CGP projection (adaptive: only when agent has tasks)
                projection_section = ""
                try:
                    from ..context.projection import project_context
                    projection_section = project_context(ctx, max_tokens=2000, max_time_ms=1000, tier="standard")
                except Exception:
                    pass

                if projection_section:
                    # Agent has tasks → condensed manifest + projection
                    manifest_lines = result.strip().split("\n")
                    condensed = "\n".join(manifest_lines[:5])
                    ctx._has_oriented = True
                    return _cache_orient_result(f"{condensed}\n\n{projection_section}")

                # No tasks → classic orient with all sections
                task_section = ""
                try:
                    if ctx.project:
                        tasks = ctx.project.get_open_tasks(
                            agent_id=ctx.agent_id, agent_name=ctx.agent_name,
                        )
                        if tasks:
                            lines = ["YOUR TASKS:"]
                            for t in tasks[:5]:
                                lines.append(f"  [{t['priority'].upper()}] {t['title']} (id: {t['id']})")
                            task_section = "\n".join(lines)
                except Exception:
                    pass
                # Recent agent signals — read from runtime graph where agents write
                signal_section = ""
                try:
                    rt_db = ctx.runtime_conn.db if (ctx.runtime_conn and hasattr(ctx.runtime_conn, 'db')) else db
                    my_name = (ctx.agent_name or "").lower()
                    recent_findings = []
                    for slabel in ("Finding", "Insight", "Decision", "AgentAction", "Document"):
                        for n in (rt_db.get_all_nodes(label=slabel) if rt_db else []):
                            props = n.properties if hasattr(n, 'properties') else {}
                            author = (props.get("_agent_name", props.get("created_by", props.get("agent", ""))) or "").lower()
                            # Show only OTHER agents' recent work
                            if author and author != my_name and author != "system" and not author.startswith("pipeline"):
                                created = props.get("created_at", props.get("timestamp", ""))
                                name = (props.get("name", props.get("description", "")) or "")[:80]
                                if name:
                                    recent_findings.append((created, author, slabel, name))
                    if recent_findings:
                        recent_findings.sort(key=lambda x: x[0], reverse=True)
                        lines = ["\nSIGNALS FROM OTHER AGENTS:"]
                        for created, author, slabel, name in recent_findings[:5]:
                            lines.append(f"  [{author}] ({slabel}) {name}")
                        lines.append("  → Use search_nodes to read their full findings")
                        signal_section = "\n".join(lines)
                except Exception:
                    pass

                # Context gaps — what agents searched for but couldn't find
                gap_section = ""
                try:
                    from ..intelligence.context_gaps import get_top_gaps
                    gaps = get_top_gaps(namespace=ns, limit=5)
                    if gaps:
                        lines_g = ["\nKNOWLEDGE GAPS (frequently searched, not found):"]
                        for g in gaps:
                            lines_g.append(f"  [{g.get('count', 0)}x] \"{g['query']}\" (label: {g.get('label', 'any')})")
                        lines_g.append("  → Consider ingesting content to fill these gaps")
                        gap_section = "\n".join(lines_g)
                except Exception:
                    pass

                # Promotions — new knowledge recently added to atomic context
                promotion_section = ""
                try:
                    from ..context.projection import get_recent_promotions
                    import time as _promo_time
                    # Show promotions from last 10 minutes (or since last orient)
                    since = _promo_time.time() - 600
                    recent = get_recent_promotions(ns, since_ts=since)
                    if recent:
                        lines_p = ["\nNEW KNOWLEDGE (recently promoted to shared context):"]
                        for evt in recent[:5]:
                            lines_p.append(f"  [{evt['label']}] {evt['title']} (id: {evt['node_id']})")
                        lines_p.append("  → Use search_nodes or get_context to read details")
                        promotion_section = "\n".join(lines_p)
                except Exception as _e:
                    logger.debug("orient promotion_section error: %s", _e)

                ctx._has_oriented = True
                presence_section = _build_presence_section(ctx)
                parts = [result]
                if task_section:
                    parts.append(task_section)
                if presence_section:
                    parts.append(presence_section)
                if signal_section:
                    parts.append(signal_section)
                if promotion_section:
                    parts.append(promotion_section)
                if gap_section:
                    parts.append(gap_section)
                final = "\n".join(parts)
                # Stamp orient time so re-queries can be detected
                try:
                    from ..context.projection import record_orient_time
                    if ctx.agent_id and ns:
                        record_orient_time(ns, ctx.agent_id)
                except Exception as _e:
                    logger.debug("record_orient_time call error: %s", _e)
                return _cache_orient_result(final)
    except Exception:
        pass

    # Build manifest on-the-fly if not cached
    if db and redis_client:
        try:
            from ..context.quality import EntityIndex, ManifestBuilder
            eidx = EntityIndex(redis_client=redis_client)
            builder = ManifestBuilder(entity_index=eidx, redis_client=redis_client)
            manifest = builder.build(ns, ns)
            if manifest:
                result = ManifestBuilder.to_text(manifest)
                # Add Context Units to orient
                try:
                    from ..search.lmdb_index import get_lmdb_index
                    lmdb_idx = get_lmdb_index(ns)
                    cu_results = lmdb_idx.search("", limit=10, label_filter="ContextUnit")
                    if cu_results:
                        cu_lines = ["\nCONTEXT UNITS (pre-assembled understanding):"]
                        for cu in cu_results[:5]:
                            cu_lines.append(f"  [{cu.get('name', '?')}] {cu.get('snippet', '')[:100]}")
                        result = result + "\n" + "\n".join(cu_lines)
                except Exception:
                    pass
                if result and len(result) > 50:
                    task_section = ""
                    try:
                        if ctx.project:
                            tasks = ctx.project.get_open_tasks(
                                agent_id=ctx.agent_id, agent_name=ctx.agent_name,
                            )
                            if tasks:
                                tlines = ["YOUR TASKS:"]
                                for t in tasks[:5]:
                                    tlines.append(f"  [{t['priority'].upper()}] {t['title']} (id: {t['id']})")
                                task_section = "\n".join(tlines)
                    except Exception:
                        pass
                    ctx._has_oriented = True
                    parts = [result]
                    if task_section:
                        parts.append(task_section)
                    return _cache_orient_result("\n".join(parts))
        except Exception:
            pass

    # Last resort: basic graph summary + topics
    sections = []
    try:
        sections.append(_graph_summary(ctx))
    except Exception:
        sections.append("Graph: (unavailable)")

    if db:
        try:
            from ..search.rag import topic_scan_text
            topics = topic_scan_text(db, ns, max_topics=8, samples_per_topic=2)
            if topics:
                sections.append(topics)
        except Exception:
            pass

    sections.append("SUGGESTED ACTIONS:\n  - search(query=\"keywords\") — find data\n  - topic_scan() — see topics")
    ctx._has_oriented = True
    return _cache_orient_result("\n\n".join(sections))


@tool(
    "use_graph", "graph",
    "Switch to a different graph namespace. Each namespace is an isolated knowledge domain.",
    params=[ToolParam("namespace", "string", "Name of the graph namespace to switch to")],
)
def _use_graph(ctx: ToolContext, namespace: str) -> str:
    ctx.conn.query(f"CREATE GRAPH {namespace}")
    ctx.conn.query(f"USE GRAPH {namespace}")
    ctx.conn.executor.active_namespace = namespace
    return f"Switched to graph namespace: {namespace}"


# ── Delivery Tools (Group D) ─────────────────────────────────────────


@tool(
    "trace_lineage", "graph",
    "Trace the provenance lineage of a node by walking DERIVED_FROM edges.",
    params=[ToolParam("node_id", "string", "UUID of the node to trace")],
)
def _trace_lineage(ctx: ToolContext, node_id: str = "") -> str:
    if not node_id:
        return "Error: node_id is required"
    db = ctx.conn.contextcore
    adapter = db.csr_adapter
    start = adapter.get_node(node_id)
    if not start:
        return f"No node found with id '{node_id}'"

    visited = set()
    queue = [(node_id, 0)]
    chain = []
    while queue:
        cid, depth = queue.pop(0)
        if cid in visited:
            continue
        visited.add(cid)
        node = adapter.get_node(cid)
        if not node:
            continue
        label = getattr(node, 'node_type', getattr(node, 'label', 'Unknown'))
        name = node.properties.get('name', node.properties.get('title', cid[:12]))
        indent = "  " * depth
        chain.append(f"{indent}{'> ' if depth > 0 else ''}{label}: {name} [{cid[:12]}...]")
        neighbors = adapter.get_neighbors(cid, edge_type="DERIVED_FROM")
        for nid, edge in neighbors:
            if nid not in visited:
                queue.append((nid, depth + 1))

    if len(chain) <= 1:
        return f"Lineage for {chain[0] if chain else node_id}:\n  (no derivation sources — this is a root node)"
    ctx.record_provenance("read", "trace_lineage", node_id)
    return f"Lineage:\n" + "\n".join(chain)


@tool(
    "get_versions", "graph",
    "Show version history of a node — content snapshots from each change.",
    params=[ToolParam("node_id", "string", "UUID of the node")],
)
def _get_versions(ctx: ToolContext, node_id: str = "") -> str:
    if not node_id:
        return "Error: node_id is required"
    db = ctx.conn.contextcore
    from contextsynapse.core.time_travel import GraphTimeTraveler
    history = GraphTimeTraveler(db).get_node_history(node_id)
    if history:
        lines = [f"Version history ({len(history)} snapshot(s)):"]
        for rec in history:
            ver = rec.get("version", "?")
            op = rec.get("operation", "?")
            ts = rec.get("timestamp", "?")[:16]
            author = (rec.get("author") or "system")[:20]
            reason = rec.get("reason", "")
            line = f"  v{ver} [{op}] {ts} by {author}"
            if reason:
                line += f" — {reason}"
            lines.append(line)
        return "\n".join(lines)
    # Fallback: check current node version
    node = db.get_node(node_id) if hasattr(db, "get_node") else None
    if not node:
        return f"No node found with id '{node_id}'"
    v = node.properties.get("version", 1)
    return f"Node {node_id[:12]}... is at v{v} (no prior snapshots)"


@tool(
    "get_stale_nodes", "graph",
    "List all nodes whose source context has been updated — they may need review.",
    params=[],
)
def _get_stale_nodes(ctx: ToolContext) -> str:
    db = ctx.conn.contextcore
    all_nodes = db.get_all_nodes()
    stale = [n for n in all_nodes if n.properties.get("_stale") is True]
    if not stale:
        return "No stale nodes. All context is current."
    lines = [f"{len(stale)} stale node(s):"]
    for n in stale[:20]:
        name = n.properties.get("name", n.id[:8])
        reason = n.properties.get("_stale_reason", "source updated")
        since = n.properties.get("_stale_since", "")[:16]
        lines.append(f"  [{n.label}] {name} — {reason} (since {since})")
    if len(stale) > 20:
        lines.append(f"  ... and {len(stale) - 20} more")
    return "\n".join(lines)


@tool(
    "confirm_current", "graph",
    "Clear the stale flag on a node — confirm it is still valid after its source was updated.",
    params=[ToolParam("node_id", "string", "UUID of the node to confirm")],
)
def _confirm_current(ctx: ToolContext, node_id: str = "") -> str:
    if not node_id:
        return "Error: node_id is required"
    db = ctx.conn.contextcore
    agent_id = getattr(ctx.conn, "agent_id", "") or ""
    try:
        node = db.confirm_current(node_id, author=agent_id)
        name = node.properties.get("name", node_id[:8])
        return f"Confirmed current: [{node.label}] {name}"
    except KeyError:
        return f"No node found with id '{node_id}'"


@tool(
    "restore_version", "graph",
    "Restore a node to a previous version. Creates a new version — history is never lost.",
    params=[
        ToolParam("node_id", "string", "UUID of the node"),
        ToolParam("version", "string", "Version number to restore to (e.g. '2')"),
        ToolParam("reason", "string", "Why you are restoring", required=False, default=""),
    ],
)
def _restore_version(ctx: ToolContext, node_id: str = "", version: str = "", reason: str = "") -> str:
    if not node_id or not version:
        return "Error: node_id and version are required"
    db = ctx.conn.contextcore
    agent_id = getattr(ctx.conn, "agent_id", "") or ""
    from contextsynapse.core.time_travel import GraphTimeTraveler
    try:
        restored = GraphTimeTraveler(db).restore_node_version(
            node_id, int(version), author=agent_id, reason=reason
        )
    except (ValueError, KeyError) as e:
        return f"Error: {e}"
    if restored is None:
        return f"No version {version} found for node '{node_id}'"
    name = restored.properties.get("name", node_id[:8])
    new_v = restored.properties.get("version", "?")
    return f"Restored [{restored.label}] {name} to v{version} content (now v{new_v})"


@tool(
    "diff_versions", "graph",
    "Show what changed in a node between two versions.",
    params=[
        ToolParam("node_id", "string", "UUID of the node"),
        ToolParam("v1", "string", "Earlier version number (e.g. '1')"),
        ToolParam("v2", "string", "Later version number (e.g. '3')"),
    ],
)
def _diff_versions(ctx: ToolContext, node_id: str = "", v1: str = "", v2: str = "") -> str:
    if not node_id or not v1 or not v2:
        return "Error: node_id, v1, and v2 are required"
    db = ctx.conn.contextcore
    from contextsynapse.core.time_travel import GraphTimeTraveler
    try:
        diff = GraphTimeTraveler(db).diff_node_versions(node_id, int(v1), int(v2))
    except ValueError as e:
        return f"Error: {e}"
    if "error" in diff:
        return diff["error"]
    lines = [f"Node {node_id[:12]}... v{v1} → v{v2}:"]
    if diff["added"]:
        lines.append(f"  Added: {diff['added']}")
    if diff["removed"]:
        lines.append(f"  Removed: {diff['removed']}")
    for k, v in diff.get("changed", {}).items():
        lines.append(f"  '{k}': {str(v['from'])[:50]} → {str(v['to'])[:50]}")
    if not (diff["added"] or diff["removed"] or diff["changed"]):
        lines.append("  No meaningful changes between these versions.")
    return "\n".join(lines)


@tool(
    "get_context_meta", "graph",
    "Get metadata about the current graph — name, purpose, node types, counts.",
    params=[],
)
def _get_context_meta(ctx: ToolContext) -> str:
    db = ctx.conn.contextcore
    meta = db.csr_adapter.get_node("_context_meta")
    if not meta:
        return "No ContextMeta node found. Graph may not have been initialized with registry."
    p = meta.properties or {}
    lines = [
        f"**Graph: {p.get('name', '?')}**",
        f"Purpose: {p.get('purpose', '?')}",
        f"Description: {p.get('description', '(none)')}",
        f"Node types: {p.get('schema_summary', '?')}",
        f"Nodes: {p.get('node_count', '?')}, Edges: {p.get('edge_count', '?')}",
        f"Created: {p.get('created_at', '?')}",
        f"Updated: {p.get('updated_at', '?')}",
    ]
    if p.get("tags"):
        lines.append(f"Tags: {', '.join(p['tags'])}")
    return "\n".join(lines)
