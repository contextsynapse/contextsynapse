"""
Context Tools — briefing, ask, session context.

These are the high-level tools agents use to understand the project
without needing to know graph structure or query syntax.
"""

from __future__ import annotations

import logging

from .registry import ToolContext, ToolParam, tool

logger = logging.getLogger(__name__)


def _record_shadow(ctx: ToolContext, tool_name: str, actual_result: str):
    """Record shadow token count: actual result vs full graph dump baseline.

    Controlled by AICONTEXTDB_SHADOW_COUNTING env var (default: on).
    Set to 'off' or '0' to disable.
    """
    import os
    if os.environ.get("CONTEXTSYNAPSE_SHADOW_COUNTING") or os.environ.get("AICONTEXTDB_SHADOW_COUNTING", "on").lower() in ("off", "0", "false", "no"):
        return
    try:
        ns = ctx.conn._namespace if ctx.conn else ""
        if not ns:
            return
        # Actual: what we're sending to the agent
        actual_chars = len(actual_result)

        # Baseline: what a full graph dump would be (without AIContextDB)
        registry = getattr(ctx.conn, "_graph_registry", None)
        if not registry:
            return
        db = registry.get_graph(ns)
        if not db:
            return
        total_chars = 0
        for node in db.get_all_nodes():
            props = node.properties if hasattr(node, "properties") else {}
            for v in props.values():
                if isinstance(v, str):
                    total_chars += len(v)
        if total_chars == 0:
            return

        from contextsynapse.gateway.cost_tracker import get_cost_tracker
        get_cost_tracker().record_shadow(
            namespace=ns,
            agent_id=ctx.agent_id or "unknown",
            tool_name=tool_name,
            actual_chars=actual_chars,
            baseline_chars=total_chars,
        )
    except Exception:
        pass  # shadow counting should never break the tool


@tool(
    "briefing", "context",
    "Get your full project briefing — requirements, tasks, decisions, "
    "code files, attached context data, and what other agents have done. "
    "Call this to understand the project before starting work.",
    requires_project=True,
)
def _briefing(ctx: ToolContext) -> str:
    # Inject manifest summary if available
    manifest_text = ""
    try:
        from ..context.quality import get_manifest, ManifestBuilder
        ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '')
        redis_client = getattr(ctx.conn.db, '_redis', None) if hasattr(ctx.conn, 'db') else None
        manifest = get_manifest(ns, redis_client)
        if manifest:
            manifest_text = ManifestBuilder.to_text(manifest)
    except Exception:
        pass

    if not ctx.project:
        # Fallback: build a basic briefing from the graph directly
        try:
            result = ctx.conn.query("SELECT *")
            nodes = result.get("nodes", [])
            if not nodes:
                return "No data in the current graph. Ingest some content first."
            type_counts = {}
            for n in nodes:
                label = n.get("label", "Unknown") if isinstance(n, dict) else getattr(n, "label", "Unknown")
                type_counts[label] = type_counts.get(label, 0) + 1
            lines = [f"Graph briefing ({len(nodes)} nodes):"]
            for label, count in sorted(type_counts.items(), key=lambda x: -x[1]):
                lines.append(f"  - {label}: {count}")
            edges = ctx.conn.get_edges()
            if edges:
                lines.append(f"\n{len(edges)} relationships in the graph.")
            return "\n".join(lines)
        except Exception:
            return "No project loaded and could not read graph directly."
    briefing = ctx.project.build_agent_context(
        agent_id=ctx.agent_id, agent_name=ctx.agent_name,
    )

    # Apply Context Relevance Gravity — re-rank based on agent behavior
    try:
        from contextsynapse.context.gravity import get_gravity
        intent = get_gravity().get_intent(ctx.agent_id or "")
        if intent:
            top_keywords = sorted(intent.items(), key=lambda x: x[1], reverse=True)[:5]
            focus_terms = ", ".join(k for k, _ in top_keywords)
            briefing += f"\n\n### Your Current Focus\nBased on your recent activity, you seem focused on: {focus_terms}"
    except Exception:
        pass

    # Cross-Agent Propagation — inject updates from other agents
    try:
        from contextsynapse.context.propagation import get_propagator
        ns = ctx.conn._namespace if ctx.conn else ""
        updates = get_propagator().get_pending(ctx.agent_id or "", ns)
        if updates:
            briefing += "\n\n### Updates from Other Agents\n" + "\n".join(f"- {u}" for u in updates)
    except Exception:
        pass

    # Append summary of attached contexts
    try:
        if ctx.conn and ctx.conn._graph_registry:
            registry = ctx.conn._graph_registry
            ns = ctx.conn._namespace
            graph = registry.get_graph(ns)
            if graph:
                context_summaries = []
                for node in graph.get_all_nodes():
                    label = node.label if hasattr(node, "label") else ""
                    if label == "ContextRef":
                        props = node.properties if hasattr(node, "properties") else {}
                        ctx_name = props.get("name", node.id[:12] if hasattr(node, "id") else "?")
                        ctx_type = props.get("context_type", "")
                        item_count = props.get("item_count", 0)
                        context_summaries.append(f"- {ctx_name} ({ctx_type}, {item_count} items)")
                if context_summaries:
                    briefing += "\n\n### Attached Contexts\n" + "\n".join(context_summaries)
                    briefing += "\n\nUse ask(question) to search across these contexts."
    except Exception:
        pass

    # Runtime Context Assembly — pull in linked upstream/lateral contexts
    try:
        from ..intelligence.runtime_context import RuntimeContextAssembler
        from ..intelligence.signal_hierarchy import SignalHierarchy

        ns = getattr(ctx.conn, '_namespace', '') or getattr(ctx.conn, 'namespace', '') or ''
        if ns and hasattr(ctx.conn, '_graph_registry') and ctx.conn._graph_registry:
            assembler = RuntimeContextAssembler(
                graph_registry=ctx.conn._graph_registry,
            )
            # Auto-discover linked contexts from hierarchy
            linked_contexts = [ns]
            try:
                from ..intelligence.persistence import IntelligencePersistence
                # Try to load hierarchy and find upstream/lateral contexts
                hierarchy = None
                if hasattr(ctx.conn, '_context_manager'):
                    store = IntelligencePersistence(ctx.conn._context_manager)
                    hierarchy = store.load_hierarchy()
                if hierarchy:
                    upstream = hierarchy.get_upstream(ns)
                    lateral = hierarchy.get_lateral(ns)
                    linked_contexts.extend(upstream[:3])  # top 3 upstream
                    linked_contexts.extend(lateral[:3])   # top 3 peers
            except Exception:
                pass

            if len(linked_contexts) > 1:
                view = assembler.assemble(contexts=linked_contexts, focus_entity=ns, days=7)
                if view.facts or view.indicators:
                    runtime_lines = ["\n\n### Upstream & Peer Context"]
                    # Cross-context facts
                    external_facts = [f for f in view.facts if f.source_context != ns]
                    if external_facts:
                        runtime_lines.append(f"\n{len(external_facts)} recent signals from linked contexts:")
                        for fact in external_facts[:5]:
                            sent_tag = f" [{fact.sentiment}]" if fact.sentiment else ""
                            runtime_lines.append(f"  [{fact.source_context}] {fact.statement[:100]}{sent_tag}")
                    # Cross-context indicators
                    external_indicators = [i for i in view.indicators if i.source_context != ns]
                    if external_indicators:
                        runtime_lines.append(f"\n{len(external_indicators)} indicators from linked contexts:")
                        for ind in external_indicators[:5]:
                            runtime_lines.append(f"  [{ind.source_context}] {ind.name}: {ind.value} {ind.unit}")
                    # Sentiment comparison
                    if view.sentiment_comparison:
                        runtime_lines.append("\nSentiment across contexts:")
                        for entity, counts in list(view.sentiment_comparison.items())[:5]:
                            net = counts.get("net", 0)
                            arrow = "+" if net > 0 else "" if net < 0 else "~"
                            runtime_lines.append(f"  {entity}: {arrow}{net} (pos={counts.get('positive',0)}, neg={counts.get('negative',0)})")
                    briefing += "\n".join(runtime_lines)
    except Exception:
        pass

    ctx.record_provenance("read", "briefing", "")
    if manifest_text:
        return manifest_text + "\n\n---\n\n" + briefing
    return briefing


@tool(
    "ask", "context",
    "Ask about the project in plain English. Searches the execution graph "
    "AND all attached contexts (ingested documents, requirements, etc.). "
    "Examples: 'What are the requirements?', 'What has codex done?', "
    "'Show me the decisions', 'What tasks are open?'",
    params=[ToolParam("question", "string", "Your question in plain English")],
)
def _ask(ctx: ToolContext, question: str) -> str:
    from contextsynapse.search.semantic_query import semantic_search
    # Track re-queries: if agent asks right after orient(), projection didn't surface this
    try:
        from contextsynapse.context.projection import record_requery
        _ns = getattr(ctx.conn, 'namespace', '') or getattr(ctx.conn, '_namespace', '')
        if _ns and ctx.agent_id:
            record_requery(_ns, ctx.agent_id)
    except Exception as _e:
        import logging as _logging
        _logging.getLogger(__name__).debug("record_requery call error: %s", _e)

    # 1. Search the boundary's own graph
    result = semantic_search(question, ctx.conn, ctx.project)
    summary = result.get("summary", "")
    all_nodes = result.get("nodes", [])

    # 2. Also search all attached context graphs
    try:
        if ctx.conn and ctx.conn._graph_registry:
            registry = ctx.conn._graph_registry
            ns = ctx.conn._namespace

            # Find attached contexts via ContextRef nodes or session_contexts table
            context_namespaces = []
            graph = registry.get_graph(ns)
            if graph:
                for node in graph.get_all_nodes():
                    label = node.label if hasattr(node, "label") else ""
                    if label == "ContextRef":
                        props = node.properties if hasattr(node, "properties") else {}
                        ctx_id = props.get("context_id") or node.id
                        # Try to find the context's graph namespace
                        try:
                            from contextsynapse.context.context_manager import ContextManager
                            cm = ContextManager(graph_registry=registry)
                            ctx_obj = cm.get_context(ctx_id)
                            if ctx_obj:
                                context_namespaces.append((ctx_obj.name, ctx_obj.graph_namespace))
                        except Exception:
                            pass

            # Search each attached context
            from contextsynapse.adapters._base import AIContextDBConnection
            for ctx_name, ctx_ns in context_namespaces:
                try:
                    ctx_conn = AIContextDBConnection(namespace=ctx_ns, graph_registry=registry)
                    ctx_result = semantic_search(question, ctx_conn, None)
                    ctx_summary = ctx_result.get("summary", "")
                    ctx_nodes = ctx_result.get("nodes", [])
                    if ctx_nodes and "No " not in ctx_summary and "not " not in ctx_summary.lower():
                        summary += f"\n\n--- From context '{ctx_name}' ---\n{ctx_summary}"
                        all_nodes.extend(ctx_nodes)
                except Exception:
                    pass
    except Exception as e:
        logger.debug("Cross-context search failed: %s", e)

    if not summary or summary.strip().startswith("No "):
        # Fallback: keyword search across all graphs
        try:
            q_terms = question.lower().split()
            found = []
            if ctx.conn and ctx.conn._graph_registry:
                for ctx_name, ctx_ns in context_namespaces:
                    try:
                        ctx_graph = registry.get_graph(ctx_ns)
                        if not ctx_graph:
                            continue
                        for node in ctx_graph.get_all_nodes():
                            props = node.properties if hasattr(node, "properties") else {}
                            text = " ".join(str(v) for v in props.values()).lower()
                            if any(t in text for t in q_terms):
                                name = props.get("name") or props.get("title") or ""
                                content = props.get("content") or props.get("description") or props.get("statement") or ""
                                label = node.label if hasattr(node, "label") else ""
                                if name or content:
                                    found.append(f"[{label}] {name}: {content[:150]}")
                    except Exception:
                        pass
            if found:
                summary = f"Found {len(found)} results across attached contexts:\n" + "\n".join(f"- {f}" for f in found[:10])
        except Exception:
            pass

    result = summary if summary.strip() else "No results found across any context."
    _record_shadow(ctx, "ask", result)
    return result


@tool(
    "task_context", "context",
    "Get everything about a specific task — its requirements, "
    "related decisions, dependencies, and code files.",
    params=[ToolParam("task_id", "string", "UUID or partial ID of the task")],
    requires_project=True,
)
def _task_context(ctx: ToolContext, task_id: str) -> str:
    if not ctx.project:
        return "Error: No project loaded."

    node = ctx.project._resolve_task_id(task_id)
    if not node:
        return f"Task {task_id} not found."

    props = node.properties if hasattr(node, "properties") else {}
    tid = node.id if hasattr(node, "id") else task_id

    lines = [
        f"Task: {props.get('title', '?')}",
        f"Status: {props.get('status', '?')}",
        f"Priority: {props.get('priority', '?')}",
        f"Assigned to: {props.get('assigned_to', 'unassigned')}",
    ]
    if props.get("description"):
        lines.append(f"Description: {props['description']}")

    # Find related nodes via edges
    edges = ctx.conn.get_edges()
    for e in edges:
        src = e.source if hasattr(e, "source") else ""
        tgt = e.target if hasattr(e, "target") else ""
        lbl = e.label if hasattr(e, "label") else ""
        if src == tid or tgt == tid:
            other_id = tgt if src == tid else src
            other = ctx.conn.get_node(other_id)
            if other:
                other_label = other.label if hasattr(other, "label") else "?"
                other_props = other.properties if hasattr(other, "properties") else {}
                name = other_props.get("title", other_props.get("path", other_props.get("name", other_id[:8])))
                lines.append(f"  {lbl} -> [{other_label}] {name}")

    return "\n".join(lines)


@tool(
    "rag_query", "context",
    "Ask a question and get an answer grounded in your graph data (RAG). "
    "Uses vector similarity + keyword search to find relevant nodes, "
    "then generates a cited answer via LLM. Best for questions about "
    "ingested documents, knowledge bases, and detailed content.",
    params=[
        ToolParam("question", "string", "Your question in natural language"),
        ToolParam("graph", "string", "Graph name to search (optional, defaults to current)", required=False, default=""),
    ],
)
def _rag_query(ctx: ToolContext, question: str, graph: str = "") -> str:
    import time as _t
    steps = []

    db, graph_name = _get_graph_db(ctx, graph)
    if not db:
        return "No graph data available. Ingest documents or add knowledge first."

    try:
        from contextsynapse.search.rag import graph_retrieve, generate_answer
    except ImportError:
        return "RAG retrieval not available."

    # Step 1: Vector-first + BM25-first retrieval with graph expansion
    t0 = _t.time()
    top, sources = graph_retrieve(db, question, graph_name)
    retrieve_ms = int((_t.time() - t0) * 1000)

    signal_names = set()
    all_entities = []
    for r in top:
        for s in r.get("signals_used", []):
            signal_names.add(s)
        for e in r.get("entities", []):
            if e.get("name") and e["name"] not in [x["name"] for x in all_entities]:
                all_entities.append(e)

    steps.append(f"[Retrieve] {len(top)} content nodes via {', '.join(signal_names) or 'search'} ({retrieve_ms}ms)")
    if all_entities:
        entity_names = [f"{e['name']} ({e['label']})" for e in all_entities[:8]]
        steps.append(f"[Expand] {len(all_entities)} entities: {', '.join(entity_names)}")

    if not top:
        return f"No relevant information found for: {question}"

    # Step 2: Check answer cache
    from contextsynapse.search.rag_cache import get_rag_cache, make_cache_key
    cache = get_rag_cache()
    node_ids = [r.get("node_id", "") for r in top]
    cache_key = make_cache_key(graph_name, question, node_ids)

    cached = cache.get(cache_key)
    if cached:
        answer = cached.get("answer", "")
        steps.append(f"[Cache HIT] Answer served from cache")
    else:
        # Step 3: Generate answer with LLM
        t0 = _t.time()
        result = generate_answer(question, top, sources)
        gen_ms = int((_t.time() - t0) * 1000)
        answer = result.get("answer", "")
        steps.append(f"[Generate] LLM answer ({gen_ms}ms)")

        if result.get("llm_errors"):
            steps.append(f"[Warning] {'; '.join(result['llm_errors'][:2])}")

        # Cache the answer
        if answer and not result.get("llm_errors"):
            cache.put(cache_key, {"answer": answer, "sources": [s.get("label", "") for s in sources[:5]]})

    # Build response
    output = "— RAG Pipeline —\n" + "\n".join(steps) + "\n\n"
    output += "— Answer —\n" + answer

    if sources:
        output += "\n\n— Sources —"
        for s in sources[:5]:
            sigs = s.get("signals", 1)
            output += f"\n  • [{s.get('node_type', '')}] {s.get('label', '')[:80]} ({s.get('score', 0):.0%}, {sigs} signal{'s' if sigs != 1 else ''})"

    ctx.record_provenance("read", "rag_query", question[:100])
    _record_shadow(ctx, "rag_query", answer)
    return output


def _get_graph_db(ctx: ToolContext, graph: str = ""):
    """Helper: resolve graph DB from tool context."""
    graph_name = graph or (ctx.conn._namespace if ctx.conn else "")

    # First: use the already-loaded db from conn (PlaygroundConn resolves scoped names)
    db = getattr(ctx.conn, "db", None) if ctx.conn else None
    if db:
        return db, graph_name

    # Fallback: try registry with scoped name, then bare name
    if ctx.conn:
        registry = getattr(ctx.conn, "_graph_registry", None)
        if registry:
            db = registry.get_graph(graph_name, load_if_missing=True)
            if db is None and ":" in graph_name:
                bare = graph_name.split(":", 1)[1]
                db = registry.get_graph(bare, load_if_missing=True)
                if db:
                    graph_name = bare
    return db, graph_name


@tool(
    "rag_graph", "context",
    "Deep graph-aware RAG — searches using BM25 full-text, vector similarity, "
    "AND entity extraction simultaneously. Nodes found by multiple signals "
    "get boosted ranking (dedup scoring). Best for complex questions that "
    "span multiple documents or topics.",
    params=[
        ToolParam("question", "string", "Your question in natural language"),
        ToolParam("graph", "string", "Graph name to search (optional)", required=False, default=""),
    ],
)
def _rag_graph(ctx: ToolContext, question: str, graph: str = "") -> str:
    db, graph_name = _get_graph_db(ctx, graph)
    if not db:
        return "No graph data available."

    from contextsynapse.search.rag import graph_retrieve, generate_answer

    top, sources = graph_retrieve(db, question, graph_name)
    if not top:
        return f"No relevant information found for: {question}"

    result = generate_answer(question, top, sources)
    answer = result.get("answer", "")

    # Append source citations with signal count
    if sources:
        answer += "\n\nSources:"
        for s in sources[:8]:
            sig = s.get("signals", 1)
            sig_label = f", {sig} signals" if sig > 1 else ""
            answer += f"\n  - [{s.get('node_type', '')}] {s.get('label', '')} (relevance: {s.get('score', 0):.0%}{sig_label})"

    ctx.record_provenance("read", "rag_graph", question[:100])
    _record_shadow(ctx, "rag_graph", answer)
    return answer


@tool(
    "search", "context",
    "Plain keyword search across graph nodes. No LLM, no vectors — just fast "
    "text matching. Returns matching nodes with snippets. Use for quick lookups "
    "when you know what you're looking for.",
    params=[
        ToolParam("query", "string", "Search keywords"),
        ToolParam("graph", "string", "Graph name (optional)", required=False, default=""),
        ToolParam("limit", "string", "Max results (default 10)", required=False, default="10"),
    ],
)
def _plain_search(ctx: ToolContext, query: str = "", graph: str = "", limit: str = "10", keywords: str = "", **kwargs) -> str:
    # Accept 'keywords' as alias for 'query' (LLMs sometimes use wrong param name)
    query = query or keywords or kwargs.get("q", "")
    if not query:
        return "Please provide a query. Usage: search(query=\"your search terms\")"
    db, _ = _get_graph_db(ctx, graph)
    if not db:
        return "No graph data available."

    # Track previous searches to detect repeated queries
    if not hasattr(ctx, '_search_history'):
        ctx._search_history = []
    query_lower = query.lower().strip()
    similar_count = sum(1 for q in ctx._search_history if q in query_lower or query_lower in q)
    ctx._search_history.append(query_lower)

    from contextsynapse.search.rag import plain_search, hybrid_retrieve, parallel_retrieve

    # Infrastructure labels — always hidden from search results
    INFRA_LABELS = {
        "AgentThought", "AgentAction", "AgentMessage", "AgentPresence",
        "ExperimentRun", "ExperimentScore", "PipelineRun",
        "VectorIndex", "BM25Index", "Session", "ContextRef",
        "Context", "Project", "KnowledgeBase", "CodeBase",
        "SystemStore", "UserStore", "WebStore", "GeneratedStore",
        "MemoryStore", "ArtifactStore", "ToolStore",
    }

    k = min(int(limit) if limit.isdigit() else 15, 50)
    ns = getattr(db, "name", "") or graph

    # Fast mode: skip vector search (3-5s) when in experiment context or repeated search
    # Use plain_search (keyword + BM25, ~200ms) as primary, parallel_retrieve as fallback
    results = []
    try:
        raw = plain_search(db, query, k=k + 20)
        results = [r for r in raw if r.get("label", "") not in INFRA_LABELS][:k]
    except Exception:
        pass

    # If plain_search found nothing, try parallel (includes vector — slower but broader)
    if not results:
        try:
            raw = parallel_retrieve(db, query, graph_name=ns, k=k)
            results = [r for r in raw if r.get("label", "") not in INFRA_LABELS]
        except Exception:
            pass

    if not results:
        return f"No results for: {query}"

    lines = [f"Found {len(results)} result(s):"]
    for r in results:
        label = r.get("label", "")
        name = r.get("name", "")
        content = r.get("content", "") or r.get("snippet", "")

        # Show full content if available, otherwise name
        if content and content != name:
            text = str(content)[:250]
        else:
            text = name

        signals = r.get("signals", 0)
        sig_tag = f" [{signals}sig]" if signals and signals > 1 else ""
        line = f"- [{label}]{sig_tag} {text}"
        lines.append(line)

    # 1-hop entity expansion: surface Person/Location/Event connected to result nodes
    try:
        ENTITY_TYPES = {"Person", "Organization", "Location", "Event"}
        seen_entities = set()
        entity_lines = []
        adapter = getattr(db, 'csr_adapter', None) or db
        for r in results[:5]:  # expand top 5 results only
            nid = r.get("node_id", "")
            if not nid:
                continue
            try:
                edges = adapter.get_edges_for_node(nid) if hasattr(adapter, 'get_edges_for_node') else []
                for edge in edges[:20]:
                    target_id = edge.target if hasattr(edge, 'target') else edge.get('target', '')
                    if target_id == nid:
                        target_id = edge.source if hasattr(edge, 'source') else edge.get('source', '')
                    if target_id in seen_entities:
                        continue
                    try:
                        target = adapter.get_node(target_id)
                        if target:
                            t_label = getattr(target, 'label', getattr(target, 'node_type', ''))
                            if t_label in ENTITY_TYPES:
                                t_name = (target.properties or {}).get('name', '')
                                if t_name and t_name not in seen_entities:
                                    seen_entities.add(t_name)
                                    entity_lines.append(f"- [{t_label}] {t_name}")
                    except Exception:
                        pass
            except Exception:
                pass
        if entity_lines:
            lines.append("\nConnected entities:")
            lines.extend(entity_lines[:8])
    except Exception:
        pass

    ctx.record_provenance("read", "search", query[:100])
    result = "\n".join(lines)

    # Redirect if searching same thing repeatedly — suggest alternatives from manifest
    if similar_count >= 1:
        alt_lines = []
        try:
            from ..context.quality import get_manifest
            import os as _os
            _redis = None
            try:
                import redis as _redis_mod
                _redis = _redis_mod.from_url(_os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379"))
            except Exception:
                pass
            manifest = get_manifest(ns, _redis) if _redis else None
            if manifest:
                hints = manifest.get("tool_hints", [])
                top_ents = manifest.get("top_entities", {})
                # Suggest entity-type searches the agent hasn't tried
                for label, entities in list(top_ents.items())[:3]:
                    top_name = entities[0]["name"] if entities else ""
                    alt_lines.append(f'  - search_nodes(label="{label}") -> {top_name}...')
                if not alt_lines:
                    for h in hints[:3]:
                        alt_lines.append(f'  - {h["tool"]}({h["example"]})')
        except Exception:
            pass

        if similar_count >= 2:
            redirect = "\n\n>>> You searched this before. Try a DIFFERENT approach:"
            if alt_lines:
                redirect += "\n" + "\n".join(alt_lines)
            redirect += "\n  Or say done with what you have."
            result += redirect
        else:
            redirect = "\n\n> Similar search done. Try different queries:"
            if alt_lines:
                redirect += "\n" + "\n".join(alt_lines)
            result += redirect

    _record_shadow(ctx, "search", result)
    return result


@tool(
    "web_fetch", "context",
    "Fetch a web page and return its text content. Use this to get live, "
    "fresh data from a URL. The content is NOT auto-saved to the graph — "
    "use add_knowledge to save what's relevant.",
    params=[
        ToolParam("url", "string", "URL to fetch"),
    ],
)
def _web_fetch(ctx: ToolContext, url: str) -> str:
    from contextsynapse.ingestion.web_crawler import fetch_single_page

    if not url.startswith("http"):
        url = "https://" + url

    page = fetch_single_page(url)
    if not page:
        return f"Failed to fetch content from {url}"

    title = page.get("title", "")
    content = page.get("content", "")
    char_count = page.get("char_count", 0)

    # Truncate for agent context window
    if len(content) > 8000:
        content = content[:8000] + f"\n\n... (truncated, {char_count} total chars)"

    ctx.record_provenance("read", "web_fetch", url[:100])
    return f"# {title}\nSource: {url}\n\n{content}"


@tool(
    "ingest_url", "context",
    "Ingest a web page into the knowledge graph. Runs the full smart pipeline: "
    "clean HTML (Trafilatura) -> chunk into passages -> extract entities/facts/relationships "
    "(LLM + regex) -> create graph edges -> embed for vector search -> BM25 index. "
    "Produces Document, Passage, Entity, Fact, and Link nodes with full relationship edges.",
    params=[
        ToolParam("url", "string", "URL of the web page to ingest"),
        ToolParam("pipeline", "string",
                  "Pipeline to use: 'smart_article' (default, full LLM extraction), "
                  "'fast_ingest' (regex only, no LLM, faster)",
                  required=False, default="smart_article"),
        ToolParam("schema_id", "string",
                  "Schema to use for extraction (e.g. 'healthcare', 'finance', 'news_article'). "
                  "Controls entity types, edge types, and domain-aware context unit generation. "
                  "Use list_schemas to see available schemas.",
                  required=False, default=""),
    ],
)
def _ingest_url(ctx: ToolContext, url: str, pipeline: str = "smart_article", schema_id: str = "") -> str:
    if not url.startswith("http"):
        url = "https://" + url

    try:
        from ..ingestion.smart_ingest import ingest_url

        # Resolve schema if specified
        schema = None
        if schema_id:
            from ..ingestion.schema_extractor import IngestionSchema
            from ..extraction.schema_loader import load_schema as _load_ext_schema
            from pathlib import Path as _Path
            _schema_path = _Path(__file__).parent.parent / "config" / "schemas" / f"{schema_id}.yaml"
            if _schema_path.exists():
                ext_schema = _load_ext_schema(str(_schema_path))
                schema = IngestionSchema.from_extraction_schema(ext_schema) if hasattr(IngestionSchema, 'from_extraction_schema') else None
                if schema is None:
                    # Build IngestionSchema manually from ExtractionSchema
                    schema = IngestionSchema(
                        name=ext_schema.name,
                        node_types={k: {"fields": v.fields, "required": v.required, "description": v.description}
                                    for k, v in ext_schema.node_types.items()},
                        edge_types={k: {"source": v.source, "target": v.target}
                                    for k, v in ext_schema.edge_types.items()},
                    )

        result = ingest_url(url, ctx.conn.contextcore, schema=schema, pipeline=pipeline)

        if result.errors:
            return f"Ingestion failed: {'; '.join(result.errors)}"

        lines = [
            f"Ingested: {url}",
            f"  Document: {result.document_id}",
            f"  Passages: {len(result.passage_ids)}",
            f"  Entities: {len(result.entity_ids)}",
            f"  Facts: {len(result.fact_ids)}",
            f"  Links: {len(result.link_ids)}",
            f"  Edges: {result.edge_count}",
            f"  Pipeline: {pipeline}",
            f"  Schema: {schema_id or 'default'}",
        ]

        # List extracted entities
        if result.entity_ids:
            lines.append("\n  Entities found:")
            for key, eid in list(result.entity_ids.items())[:10]:
                node = ctx.conn.contextsynapse.csr_adapter.get_node(eid)
                if node:
                    label = getattr(node, 'node_type', getattr(node, 'label', ''))
                    name = node.properties.get('name', '?')
                    lines.append(f"    [{label}] {name}")

        # Persist graph to disk and update metadata so it appears in graph listings
        try:
            ns = ctx.conn.namespace
            reg = ctx.conn.graph_registry
            if reg and ns:
                reg.save_graph(ns, create_checkpoint=False)
        except Exception:
            pass  # best-effort save

        ctx.record_provenance("write", "ingest_url", url[:100])
        return "\n".join(lines)
    except Exception as e:
        return f"Ingestion error: {e}"


@tool(
    "list_pipelines", "context",
    "List available ingestion pipelines with descriptions. "
    "Use this to understand which pipeline to choose for different content types.",
    params=[],
)
def _list_pipelines(ctx: ToolContext) -> str:
    from ..ingestion.smart_ingest import list_pipelines
    pipelines = list_pipelines()
    lines = ["Available ingestion pipelines:\n"]
    for name, info in pipelines.items():
        lines.append(f"**{name}**: {info['name']}")
        lines.append(f"  {info['description']}")
        lines.append(f"  Input: {info['input_types']}")
        lines.append(f"  Produces: {', '.join(info['produces'])}")
        lines.append(f"  LLM required: {info['llm_required']}")
        lines.append("")
    return "\n".join(lines)


@tool(
    "reason", "context",
    "Answer complex questions by traversing the knowledge graph. Follows edges "
    "between nodes to find multi-hop connections. Best for 'who/what/how' questions "
    "that require understanding relationships. Example: 'Who is responsible for auth security?'",
    params=[ToolParam("question", "string", "Your question")],
)
def _reason(ctx: ToolContext, question: str) -> str:
    db, _ = _get_graph_db(ctx)
    if not db:
        return "No graph data available."
    from contextsynapse.search.reasoning_chain import reason_over_graph
    result = reason_over_graph(db, question)
    answer = result.get("answer", "No path found.")
    hops = result.get("hops", 0)
    visited = result.get("nodes_visited", 0)
    seeds = result.get("seeds", [])
    footer = f"\n\n[Traversed {visited} nodes in {hops} hops from: {', '.join(seeds[:3])}]"
    _record_shadow(ctx, "reason", answer)
    return answer + footer


@tool(
    "graph_timeline", "context",
    "See the activity timeline for the current graph — what was created when, "
    "by which agent. Use to understand project history and recent changes.",
    params=[
        ToolParam("hours", "string", "How many hours of history (default 24)", required=False, default="24"),
    ],
)
def _graph_timeline(ctx: ToolContext, hours: str = "24") -> str:
    db, graph_name = _get_graph_db(ctx)
    if not db:
        return "No graph data available."
    from contextsynapse.core.time_travel import GraphTimeTraveler
    h = int(hours) if hours.isdigit() else 24
    timeline = GraphTimeTraveler(db).timeline(hours=h)
    if not timeline:
        return f"No activity in the last {h} hours."
    lines = [f"Graph activity (last {h}h):"]
    for bucket in timeline:
        agents = ", ".join(a[:12] for a in bucket.get("agents", []))
        labels = ", ".join(f"{k}:{v}" for k, v in bucket.get("labels", {}).items())
        lines.append(f"  {bucket['timestamp'][:16]} — {bucket['count']} nodes ({labels}) by {agents or 'system'}")
    return "\n".join(lines)


@tool(
    "graph_diff", "context",
    "Compare the graph between two timestamps — what was added, removed, or modified. "
    "Use to understand what changed between agent runs.",
    params=[
        ToolParam("from_time", "string", "Start timestamp (ISO format, e.g. 2026-03-24T10:00:00)"),
        ToolParam("to_time", "string", "End timestamp (ISO format, e.g. 2026-03-25T10:00:00)"),
    ],
)
def _graph_diff(ctx: ToolContext, from_time: str, to_time: str) -> str:
    db, _ = _get_graph_db(ctx)
    if not db:
        return "No graph data available."
    from contextsynapse.core.time_travel import GraphTimeTraveler
    diff = GraphTimeTraveler(db).diff(from_time, to_time)
    lines = [diff["summary"]]
    if diff["added_nodes"]:
        lines.append(f"\nAdded ({diff['added']}):")
        for n in diff["added_nodes"][:10]:
            lines.append(f"  + [{n['label']}] {n['name']} (by {n.get('agent','')[:12]})")
    if diff["modified_nodes"]:
        lines.append(f"\nModified ({diff['modified']}):")
        for n in diff["modified_nodes"][:5]:
            lines.append(f"  ~ [{n['label']}] {n['name']} (by {n.get('updated_by','')[:12]})")
    return "\n".join(lines)


@tool(
    "explain_context", "context",
    "Show why context items were selected — scoring breakdowns and rankings.",
    params=[],
)
def _explain_context(ctx: ToolContext) -> str:
    from ..context.hub import ContextHub, ContextRole, ContextItem
    from ..context.scoping import ContextScoper, ScopingConfig

    hub = ContextHub(system_prompt="Context explanation", max_tokens=4000)
    hub._db = ctx.conn.contextcore
    nodes = ctx.conn.contextsynapse.csr_adapter.get_all_nodes()
    content_nodes = [n for n in nodes if getattr(n, 'node_type', getattr(n, 'label', '')) not in
                     ("ContextMeta", "VectorIndex", "BM25Index", "Session", "ContextRef", "Agent")]
    if not content_nodes:
        return "No context available — graph is empty."
    hub.add_nodes(content_nodes[:20])
    hub.set_scoping(ScopingConfig(max_tokens=4000))
    hub.to_prompt()
    scored = hub.get_last_scored_items()
    if not scored:
        return "No scored items."

    lines = [f"**Context Explanation** ({len(scored)} items)\n"]
    for i, si in enumerate(scored[:15], 1):
        preview = si.item.content[:60].replace('\n', ' ')
        role = si.item.role.value if hasattr(si.item.role, 'value') else str(si.item.role)
        bd = ", ".join(f"{k}: {v:.2f}" for k, v in si.breakdown.items() if v > 0) if si.breakdown else "n/a"
        lines.append(f"{i}. **{role}** (score: {si.score:.3f}) — {preview}...")
        lines.append(f"   Breakdown: {bd}")
        if si.reason:
            lines.append(f"   Reason: {si.reason}")
    ctx.record_provenance("read", "explain_context", "")
    return "\n".join(lines)
