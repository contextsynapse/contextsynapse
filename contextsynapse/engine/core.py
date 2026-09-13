"""ContextEngine -- the single entry point for all context operations.

Wires together:
  StorageRouter   -- routes data to Graph / DuckDB / Qdrant
  ContentResolver -- fetches content from DuckDB for thin graph nodes
  ContextHub      -- assembles LLM-ready context
  MemoryEngine    -- persistent agent memory
  Graph           -- entity relationships

An agent calls the engine, never the stores directly.
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_global_engine: Optional["ContextEngine"] = None
_engine_lock = threading.Lock()


class ContextEngine:
    """Unified context API for agents and applications.

    Usage:
        engine = get_engine(graph=db)

        # Ingest data (auto-routes to correct stores)
        engine.ingest([
            {"id": "doc_1", "type": "Document", "properties": {"title": "..."}},
            {"id": "p_1", "type": "Passage", "properties": {"text": "..."}},
            {"type": "Price", "properties": {"ticker": "TCS", "close": 4200}},
        ])

        # Search (vector + graph expansion + content resolve)
        results = engine.search("TCS revenue growth", top_k=5)

        # Get entity context (relationships + prices + facts + memories)
        ctx = engine.context("ent_TCS", include=["neighbors", "prices", "facts", "memories"])

        # Build LLM-ready messages
        messages = engine.ask("What are TCS's key risks?")

        # Memory
        engine.remember("agent-1", "User prefers dark mode")
        memories = engine.recall("agent-1", "preferences")
    """

    def __init__(self, graph=None, namespace: str = "default"):
        self._graph = graph
        self._namespace = namespace
        self._router = None
        self._resolver = None
        self._memory = None
        self._hub_class = None

    # ── Lazy-initialized components ──

    @property
    def router(self):
        if self._router is None:
            from contextsynapse.storage.router import get_storage_router
            self._router = get_storage_router(graph=self._graph, namespace=self._namespace)
        return self._router

    @property
    def resolver(self):
        if self._resolver is None:
            from contextsynapse.storage.router import get_content_resolver
            self._resolver = get_content_resolver()
        return self._resolver

    @property
    def store(self):
        return self.router.content

    @property
    def memory(self):
        if self._memory is None:
            from contextsynapse.memory import get_memory_engine
            self._memory = get_memory_engine(graph=self._graph)
        return self._memory

    @property
    def graph(self):
        return self._graph

    def set_graph(self, graph):
        self._graph = graph
        if self._router:
            self._router.set_graph(graph)
        if self._memory:
            self._memory.set_graph(graph)

    # ── Ingest ──

    def ingest(self, items: List[Dict[str, Any]], namespace: str = None) -> Dict[str, int]:
        """Ingest data -- auto-routes to the correct stores.

        Items can be nodes, edges, passages, facts, prices -- anything.
        The StorageRouter decides where each item goes.

        Returns counts: {graph_nodes, graph_edges, content_rows, prices}
        """
        return self.router.ingest(items, namespace=namespace or self._namespace)

    # ── Get (auto-resolve) ──

    def get(self, item_id: str) -> Optional[Dict[str, Any]]:
        """Get any item by ID -- auto-resolves from the correct store.

        If the graph node has a _ref, fetches full content from DuckDB.
        If it's a pure graph entity, returns graph properties.
        If it's not in graph, tries the content store directly.
        """
        # Try graph first
        if self._graph:
            node = self._graph.get_node(item_id)
            if node:
                return self.resolver.resolve(node)

        # Try content store directly
        item = self.store.get_item(item_id) if hasattr(self.store, "get_item") else None
        if item:
            return item

        return None

    def get_batch(self, item_ids: List[str]) -> List[Dict[str, Any]]:
        """Batch get -- efficient multi-ID lookup."""
        if not item_ids:
            return []

        if self._graph:
            nodes = [self._graph.get_node(iid) for iid in item_ids]
            return self.resolver.resolve_batch([n for n in nodes if n])

        # Fallback: content store
        texts = self.store.get_texts_batch(item_ids) if hasattr(self.store, "get_texts_batch") else {}
        return [{"id": iid, "text": texts.get(iid, "")} for iid in item_ids]

    # ── Search ──

    def search(
        self,
        query: str,
        top_k: int = 5,
        entity_id: str = None,
        include: List[str] = None,
        namespace: str = None,
    ) -> Dict[str, Any]:
        """Search across all stores -- the main RAG entry point.

        Steps:
          1. Content store text search (keyword match)
          2. Resolve full content
          3. Graph expansion (entities, facts)
          4. Entity context (prices, neighbors)
          5. Assemble results

        Args:
            query: Search query text.
            top_k: Number of results.
            entity_id: Optional entity to scope the search.
            include: What to include: "passages", "entities", "facts", "prices", "memories"
            namespace: Namespace to search in.

        Returns:
            {passages: [...], entities: [...], facts: [...], prices: [...], memories: [...]}
        """
        include = set(include or ["passages", "entities", "facts"])
        t0 = time.perf_counter()
        result = {"query": query, "passages": [], "entities": [], "facts": [],
                  "prices": [], "memories": [], "timings": {}}

        # ── Step 1: Find passages ──
        t1 = time.perf_counter()
        if "passages" in include:
            try:
                # Search content store for matching passages
                query_lower = query.lower()
                keywords = [w for w in query_lower.split() if len(w) > 2]
                if keywords and hasattr(self.store, "query"):
                    # Build OR conditions for keyword match
                    conditions = " OR ".join(["text LIKE ?" for _ in keywords])
                    params = [f"%{kw}%" for kw in keywords]
                    sql = (f"SELECT id, text, parent_id, position, metadata FROM content "
                           f"WHERE type = 'passage' AND ({conditions}) "
                           f"ORDER BY position LIMIT ?")
                    params.append(top_k * 3)  # fetch more, rank later
                    rows = self.store.query(sql, params)

                    # Simple relevance scoring (count keyword matches)
                    scored = []
                    for row in rows:
                        text_lower = row.get("text", "").lower()
                        score = sum(1 for kw in keywords if kw in text_lower) / max(len(keywords), 1)
                        scored.append((score, row))
                    scored.sort(key=lambda x: x[0], reverse=True)

                    for score, row in scored[:top_k]:
                        passage = {
                            "id": row.get("id", ""),
                            "text": row.get("text", ""),
                            "score": round(score, 3),
                            "doc_id": row.get("parent_id", ""),
                            "entities": [],
                            "facts": [],
                        }

                        # Graph expand: find entities mentioned by this passage
                        if self._graph:
                            for nid, edge in self._graph.get_neighbors(row["id"]):
                                if edge.edge_type == "MENTIONS":
                                    ent_node = self._graph.get_node(nid)
                                    if ent_node:
                                        passage["entities"].append({
                                            "id": nid,
                                            "name": ent_node.properties.get("name", nid),
                                            "type": ent_node.node_type,
                                        })
                                elif edge.edge_type == "STATES":
                                    fact = self.resolver.resolve(self._graph.get_node(nid))
                                    if fact:
                                        passage["facts"].append(
                                            fact.get("text", fact.get("statement", ""))
                                        )

                        result["passages"].append(passage)
            except Exception as e:
                logger.warning("Passage search failed: %s", e)

            # Apply retrieval quality improvements
            if result["passages"]:
                try:
                    from contextsynapse.search.retrieval_quality import improve_retrieval
                    result["passages"] = improve_retrieval(
                        result["passages"], query, graph=self._graph, top_k=top_k,
                    )
                except Exception as e:
                    logger.debug("Retrieval quality step skipped: %s", e)

        result["timings"]["passages"] = round((time.perf_counter() - t1) * 1000, 2)

        # ── Step 2: Entity context ──
        t2 = time.perf_counter()
        unique_entities = set()
        for p in result["passages"]:
            for ent in p.get("entities", []):
                unique_entities.add(ent["id"])
        if entity_id:
            unique_entities.add(entity_id)

        if "entities" in include and self._graph:
            for eid in unique_entities:
                node = self._graph.get_node(eid)
                if not node:
                    continue
                entity = {
                    "id": eid,
                    "name": node.properties.get("name", eid),
                    "type": node.node_type,
                    "neighbors": {},
                }
                # Outgoing relationships
                for nid, edge in self._graph.get_neighbors(eid):
                    target = self._graph.get_node(nid)
                    name = target.properties.get("name", nid) if target else nid
                    entity["neighbors"].setdefault(edge.edge_type, []).append(name)
                # Incoming (how many mention this entity)
                incoming = self._graph.get_incoming_neighbors(eid)
                entity["mentioned_in"] = sum(1 for _, e in incoming if e.edge_type == "MENTIONS")
                entity["docs_about"] = sum(1 for _, e in incoming if e.edge_type == "ABOUT")

                result["entities"].append(entity)
        result["timings"]["entities"] = round((time.perf_counter() - t2) * 1000, 2)

        # ── Step 3: Prices ──
        t3 = time.perf_counter()
        if "prices" in include:
            for eid in unique_entities:
                node = self._graph.get_node(eid) if self._graph else None
                if node and node.node_type in ("Company", "Stock"):
                    ticker = node.properties.get("name", node.properties.get("ticker", ""))
                    if ticker:
                        prices = self.store.get_timeseries(ticker, limit=5) if hasattr(self.store, "get_timeseries") else []
                        if prices:
                            result["prices"].append({
                                "entity": ticker,
                                "latest": prices[0].get("value"),
                                "date": prices[0].get("ts"),
                                "history": [{"date": p.get("ts"), "close": p.get("value")} for p in prices],
                            })
        result["timings"]["prices"] = round((time.perf_counter() - t3) * 1000, 2)

        # ── Step 4: Facts for entities ──
        t4 = time.perf_counter()
        if "facts" in include:
            for eid in unique_entities:
                if hasattr(self.store, "get_facts_by_entity"):
                    entity_facts = self.store.get_facts_by_entity(eid)
                    for f in entity_facts[:5]:
                        result["facts"].append({
                            "text": f.get("text", f.get("statement", "")),
                            "entity": eid,
                            "confidence": f.get("confidence", 0),
                        })
        result["timings"]["facts"] = round((time.perf_counter() - t4) * 1000, 2)

        # ── Step 5: Memories ──
        t5 = time.perf_counter()
        if "memories" in include:
            try:
                mems = self.memory.recall(query=query, limit=5)
                result["memories"] = mems
            except Exception:
                pass
        result["timings"]["memories"] = round((time.perf_counter() - t5) * 1000, 2)

        result["timings"]["total"] = round((time.perf_counter() - t0) * 1000, 2)
        result["result_count"] = len(result["passages"])
        return result

    # ── Context (entity-centric) ──

    def context(
        self,
        entity_id: str,
        include: List[str] = None,
        max_tokens: int = 8000,
    ) -> Dict[str, Any]:
        """Build complete context for an entity.

        Assembles everything known about an entity into a structured dict
        that can be fed to ContextHub or directly to an LLM.

        Args:
            entity_id: The entity to build context for.
            include: What to include (default: all).
            max_tokens: Token budget.

        Returns:
            {entity: {...}, passages: [...], facts: [...], prices: [...],
             neighbors: [...], memories: [...], hub: ContextHub}
        """
        include = set(include or ["entity", "passages", "facts", "prices", "neighbors", "memories"])
        result = {"entity_id": entity_id}

        # Entity details
        if "entity" in include:
            result["entity"] = self.get(entity_id) or {}

        # Passages mentioning this entity (via incoming graph edges)
        if "passages" in include and self._graph:
            incoming = self._graph.get_incoming_neighbors(entity_id)
            passage_ids = [nid for nid, e in incoming if e.edge_type == "MENTIONS"]
            if passage_ids:
                texts = self.resolver.get_texts_batch(passage_ids[:20])
                result["passages"] = [{"id": pid, "text": texts.get(pid, "")}
                                       for pid in passage_ids[:20] if texts.get(pid)]

        # Documents about this entity
        if "passages" in include and self._graph:
            doc_ids = [nid for nid, e in self._graph.get_incoming_neighbors(entity_id)
                       if e.edge_type == "ABOUT"]
            result["documents"] = []
            for did in doc_ids[:10]:
                doc = self.get(did)
                if doc:
                    result["documents"].append(doc)

        # Facts
        if "facts" in include:
            if hasattr(self.store, "get_facts_by_entity"):
                result["facts"] = self.store.get_facts_by_entity(entity_id)
            else:
                result["facts"] = []

        # Prices
        if "prices" in include:
            node = self._graph.get_node(entity_id) if self._graph else None
            if node:
                ticker = node.properties.get("name", node.properties.get("ticker", ""))
                if ticker and hasattr(self.store, "get_timeseries"):
                    result["prices"] = self.store.get_timeseries(ticker, limit=30)

        # Neighbors
        if "neighbors" in include and self._graph:
            neighbors = []
            for nid, edge in self._graph.get_neighbors(entity_id):
                target = self._graph.get_node(nid)
                neighbors.append({
                    "id": nid,
                    "name": target.properties.get("name", nid) if target else nid,
                    "type": target.node_type if target else "Unknown",
                    "edge": edge.edge_type,
                })
            result["neighbors"] = neighbors

        # Memories
        if "memories" in include:
            try:
                result["memories"] = self.memory.recall(entity_id=entity_id, limit=10)
            except Exception:
                result["memories"] = []

        # Build ContextHub
        from contextsynapse.context.hub import ContextHub
        hub = ContextHub(max_tokens=max_tokens)

        entity_name = result.get("entity", {}).get("name", entity_id)
        hub.add_text(f"Context for: {entity_name}", role="system")

        if result.get("passages"):
            for p in result["passages"][:10]:
                hub.add_text(p["text"], role="retrieved", label=f"passage:{p['id']}")

        if result.get("facts"):
            fact_text = "\n".join(f.get("text", f.get("statement", "")) for f in result["facts"][:10])
            if fact_text:
                hub.add_text(f"Known facts:\n{fact_text}", role="background")

        if result.get("prices"):
            price_lines = [f"{p.get('ts', '?')}: {p.get('value', '?')}" for p in result["prices"][:10]]
            if price_lines:
                hub.add_text(f"Price history:\n" + "\n".join(price_lines), role="background")

        if result.get("neighbors"):
            neighbor_text = ", ".join(f"{n['name']} ({n['edge']})" for n in result["neighbors"][:10])
            hub.add_text(f"Related: {neighbor_text}", role="background")

        if result.get("memories"):
            mem_text = "\n".join(m.get("text", "") for m in result["memories"][:5])
            if mem_text:
                hub.add_text(f"Memories:\n{mem_text}", role="background")

        result["hub"] = hub
        result["messages"] = hub.to_messages()
        result["token_estimate"] = hub.estimate_tokens()
        return result

    # ── Ask (full RAG pipeline) ──

    def ask(
        self,
        question: str,
        system_prompt: str = "You are a helpful analyst. Use the provided context to answer accurately.",
        top_k: int = 5,
        max_tokens: int = 8000,
    ) -> Dict[str, Any]:
        """Full RAG: search -> resolve -> expand -> assemble -> return LLM messages.

        This is the highest-level API. Returns everything an LLM needs.

        Returns:
            {messages: [{role, content}], sources: [...], token_estimate: int, timings: {...}}
        """
        # Search
        search_results = self.search(question, top_k=top_k, include=["passages", "entities", "facts", "prices", "memories"])

        # Build ContextHub
        from contextsynapse.context.hub import ContextHub
        hub = ContextHub(system_prompt=system_prompt, max_tokens=max_tokens)

        # Add passages
        for p in search_results["passages"]:
            source = f"[{p['doc_id']}]" if p.get("doc_id") else ""
            hub.add_text(p["text"], role="retrieved", label=f"passage {source}",
                         source=p.get("doc_id"))

        # Add entity context
        for ent in search_results["entities"]:
            neighbors = ent.get("neighbors", {})
            neighbor_str = ", ".join(f"{k}: {', '.join(v[:3])}" for k, v in neighbors.items())
            hub.add_text(
                f"{ent['name']} ({ent['type']}): {ent.get('mentioned_in', 0)} mentions, "
                f"{ent.get('docs_about', 0)} docs. {neighbor_str}",
                role="background", label=f"entity:{ent['id']}"
            )

        # Add facts
        if search_results["facts"]:
            fact_lines = [f.get("text", "") for f in search_results["facts"][:5]]
            hub.add_text("Key facts:\n" + "\n".join(f"- {f}" for f in fact_lines if f),
                         role="background", label="facts")

        # Add prices
        for price_data in search_results["prices"]:
            latest = price_data.get("latest")
            if latest:
                hub.add_text(
                    f"{price_data['entity']} price: {latest} (as of {price_data.get('date', '?')})",
                    role="background", label=f"price:{price_data['entity']}"
                )

        # Add memories
        if search_results["memories"]:
            mem_lines = [m.get("text", "") for m in search_results["memories"][:3]]
            hub.add_text("Agent memories:\n" + "\n".join(f"- {m}" for m in mem_lines if m),
                         role="background", label="memories")

        # Add the question
        hub.add_text(question, role="user")

        return {
            "messages": hub.to_messages(),
            "sources": [{"id": p["id"], "score": p.get("score", 0)} for p in search_results["passages"]],
            "passages": len(search_results["passages"]),
            "entities": len(search_results["entities"]),
            "facts": len(search_results["facts"]),
            "token_estimate": hub.estimate_tokens(),
            "timings": search_results["timings"],
        }

    # ── Memory shortcuts ──

    def remember(self, agent_id: str, content: str, memory_type: str = "general", **kwargs) -> str:
        return self.memory.remember(agent_id, content, memory_type=memory_type, **kwargs)

    def recall(self, agent_id: str = None, query: str = None, **kwargs) -> List[Dict]:
        return self.memory.recall(agent_id=agent_id, query=query, **kwargs)

    # ── Timeseries shortcuts ──

    def get_prices(self, ticker: str, days: int = 30) -> List[Dict]:
        if hasattr(self.store, "get_timeseries"):
            return self.store.get_timeseries(ticker, days)
        return self.store.get_prices(ticker, days)

    # ── Stats ──

    def stats(self) -> Dict[str, Any]:
        result = {"content": self.store.stats()}
        if self._graph:
            result["graph"] = {
                "nodes": self._graph.get_node_count(),
                "edges": self._graph.get_edge_count(),
            }
        result["memory"] = self.memory.stats()
        return result


def get_engine(graph=None, namespace: str = "default") -> ContextEngine:
    """Get or create the global ContextEngine singleton."""
    global _global_engine
    if _global_engine is None:
        with _engine_lock:
            if _global_engine is None:
                _global_engine = ContextEngine(graph=graph, namespace=namespace)
    if graph and _global_engine._graph is None:
        _global_engine.set_graph(graph)
    return _global_engine
