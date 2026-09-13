"""Storage Router -- routes ingestion data to the right store.

Graph nodes stay thin (~200 bytes). Content goes to DuckDB.
Vectors go to the vector store. The router decides based on data type.

Usage:
    router = get_storage_router(graph=db)
    router.ingest([
        {"id": "doc_1", "type": "Document", "store": "content", "properties": {...}},
        {"id": "p_1", "type": "Passage", "store": "content", "properties": {"text": "..."}},
        {"id": "ent_1", "type": "Company", "store": "graph", "properties": {"name": "TCS"}},
        {"type": "timeseries", "store": "content", "table": "prices", "rows": [...]},
        {"source_id": "p_1", "target_id": "ent_1", "edge_type": "MENTIONS"},
    ])
"""
from __future__ import annotations

import logging
import threading
import uuid
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_global_router: Optional["StorageRouter"] = None
_router_lock = threading.Lock()

# Labels that should stay as full graph nodes (they ARE relationship hubs)
GRAPH_LABELS = frozenset({
    "Company", "Person", "Organization", "Location", "Event",
    "Sector", "Industry", "Product", "Technology", "Topic",
    "Agent", "AgentPresence", "Session", "Context",
})

# Labels whose content goes to DuckDB, graph gets thin pointer
CONTENT_LABELS = frozenset({
    "Document", "WebPage", "Article", "Report",
    "Passage", "Chunk", "TextChunk", "Section",
    "Fact", "Finding", "Insight", "Statement",
    "Indicator", "Price", "PricePoint",
})


class StorageRouter:
    """Routes data to the correct store based on type.

    Routing rules:
        - Entities (Company, Person, etc.) -> Graph (thin node, details to DuckDB)
        - Passages, Facts -> DuckDB (content) + Graph (thin pointer node)
        - Prices, time-series -> DuckDB only (no graph node)
        - Edges -> Graph
        - Vectors -> Vector store (handled externally)
    """

    def __init__(self, graph=None, namespace: str = "default"):
        self._graph = None
        self._graph_is_csr = False
        self._namespace = namespace
        self._content_store = None  # lazy init
        if graph:
            self.set_graph(graph)

    @property
    def content(self):
        """The content store (DuckDB, PostgreSQL, etc.). Lazy-initialized from config."""
        if self._content_store is None:
            from contextsynapse.storage.router.factory import get_content_store
            self._content_store = get_content_store()
        return self._content_store

    # Keep .duckdb as alias for backward compat
    @property
    def duckdb(self):
        return self.content

    def set_graph(self, graph):
        """Set the graph backend (can be set after construction)."""
        self._graph = graph
        # Detect if graph is AIContextDB (uses GraphNode) or CSRGraphStorage (uses id, type, props)
        self._graph_is_csr = hasattr(graph, "node_id_to_index") if graph else False

    def _add_node(self, node_id: str, label: str, properties: dict):
        """Add a node to the graph, adapting to the backend API."""
        if self._graph_is_csr:
            self._graph.add_node(node_id, label, properties)
        else:
            from contextsynapse.core.graph_structures import GraphNode
            self._graph.add_node(GraphNode(id=node_id, label=label, properties=properties))

    def _add_edge(self, source: str, target: str, label: str, properties: dict):
        """Add an edge to the graph, adapting to the backend API."""
        if self._graph_is_csr:
            self._graph.add_edge(source, target, label, properties)
        else:
            from contextsynapse.core.graph_structures import GraphEdge
            self._graph.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=source, target=target,
                label=label, properties=properties,
            ))

    def ingest(self, items: List[Dict[str, Any]], namespace: str = None) -> Dict[str, int]:
        """Route a batch of items to the correct stores.

        Args:
            items: List of dicts, each describing a node, edge, or data row.
            namespace: Override namespace for this batch.

        Returns:
            Counts: {"graph_nodes": N, "graph_edges": N, "content_rows": N, "prices": N}
        """
        ns = namespace or self._namespace
        counts = {"graph_nodes": 0, "graph_edges": 0, "content_rows": 0, "prices": 0}

        # Batch collectors for DuckDB (batch insert is much faster)
        passages_batch = []
        facts_batch = []
        prices_batch = []
        documents_batch = []
        entity_details_batch = []

        for item in items:
            # ── Edge ──
            if "source_id" in item and "target_id" in item:
                if self._graph:
                    self._add_edge(
                        item["source_id"], item["target_id"],
                        item.get("edge_type", item.get("type", "RELATED")),
                        item.get("properties", {}),
                    )
                    counts["graph_edges"] += 1
                continue

            # ── Time-series (prices) ── no graph node
            _PRICE_TYPES = frozenset({"Price", "PricePoint", "Indicator"})
            if item.get("table") == "prices" or item.get("type") in _PRICE_TYPES:
                if "rows" in item:
                    prices_batch.extend(item["rows"])
                else:
                    props = item.get("properties", item)
                    prices_batch.append({
                        "ticker": props.get("ticker", ""),
                        "date": props.get("date", ""),
                        "open": props.get("open"),
                        "high": props.get("high"),
                        "low": props.get("low"),
                        "close": props.get("close"),
                        "volume": props.get("volume"),
                        "change_pct": props.get("change_pct"),
                    })
                    counts["prices"] += 1
                continue

            item_type = item.get("type", item.get("label", "Unknown"))
            item_id = item.get("id", str(uuid.uuid4()))
            props = item.get("properties", {})

            # ── Document metadata ──
            if item_type in ("Document", "WebPage", "Article", "Report"):
                documents_batch.append({
                    "id": item_id,
                    "title": props.get("title", ""),
                    "url": props.get("url", ""),
                    "author": props.get("author", ""),
                    "date": props.get("date", ""),
                    "source": props.get("source", ""),
                    "content_hash": props.get("content_hash", ""),
                    "metadata": {k: v for k, v in props.items()
                                 if k not in ("title", "url", "author", "date", "source", "content_hash")},
                })
                # Thin graph node
                if self._graph:
                    self._add_node(
                        item_id, item_type,
{"title": props.get("title", ""), "_ref": f"duckdb:documents:{item_id}"},
                    )
                    counts["graph_nodes"] += 1
                counts["content_rows"] += 1
                continue

            # ── Passage / Chunk ──
            if item_type in ("Passage", "Chunk", "TextChunk", "Section"):
                passages_batch.append({
                    "id": item_id,
                    "doc_id": props.get("doc_id", ""),
                    "text": props.get("text", props.get("content", "")),
                    "section": props.get("section", ""),
                    "position": props.get("position", 0),
                    "token_count": props.get("token_count", 0),
                    "metadata": {k: v for k, v in props.items()
                                 if k not in ("text", "content", "doc_id", "section", "position", "token_count")},
                })
                # Thin graph node (NO text)
                if self._graph:
                    self._add_node(
                        item_id, item_type,
{
                            "doc_id": props.get("doc_id", ""),
                            "position": props.get("position", 0),
                            "_ref": f"duckdb:passages:{item_id}",
                        },
                    )
                    counts["graph_nodes"] += 1
                counts["content_rows"] += 1
                continue

            # ── Fact / Finding ──
            if item_type in ("Fact", "Finding", "Insight", "Statement"):
                facts_batch.append({
                    "id": item_id,
                    "passage_id": props.get("passage_id", ""),
                    "statement": props.get("statement", props.get("text", "")),
                    "fact_type": props.get("fact_type", "general"),
                    "confidence": props.get("confidence", 0.5),
                    "entity_id": props.get("entity_id", ""),
                    "metadata": {k: v for k, v in props.items()
                                 if k not in ("passage_id", "statement", "text", "fact_type", "confidence", "entity_id")},
                })
                # Thin graph node
                if self._graph:
                    self._add_node(
                        item_id, item_type,
{
                            "fact_type": props.get("fact_type", "general"),
                            "_ref": f"duckdb:facts:{item_id}",
                        },
                    )
                    counts["graph_nodes"] += 1
                counts["content_rows"] += 1
                continue

            # ── Entity (graph-native + details to DuckDB) ──
            if item_type in GRAPH_LABELS or item.get("store") == "graph":
                if self._graph:
                    from contextsynapse.core.graph_structures import GraphNode
                    # Keep essential props in graph, heavy description to DuckDB
                    thin_props = {"name": props.get("name", ""), "sector": props.get("sector", "")}
                    if props.get("description") and len(props.get("description", "")) > 200:
                        thin_props["_ref"] = f"duckdb:entity_details:{item_id}"
                        entity_details_batch.append({
                            "id": item_id, "name": props.get("name", ""),
                            "type": item_type,
                            "description": props.get("description", ""),
                            "aliases": props.get("aliases", []),
                            "metadata": {k: v for k, v in props.items()
                                         if k not in ("name", "type", "sector", "description", "aliases")},
                        })
                    else:
                        thin_props.update({k: v for k, v in props.items() if k != "description" or len(str(v)) <= 200})
                    self._add_node(item_id, item_type, thin_props)
                    counts["graph_nodes"] += 1
                continue

            # ── Default: graph node ──
            if self._graph:
                self._add_node(item_id, item_type, props)
                counts["graph_nodes"] += 1

        # ── Flush batches to content store (single bulk insert per type) ──
        store = self.content
        if hasattr(store, "store_items"):
            # Fast path: generic bulk insert (DuckDB with PyArrow)
            if documents_batch:
                store.store_items(documents_batch, "document", ns)
            if passages_batch:
                store.store_items(passages_batch, "passage", ns)
            if facts_batch:
                store.store_items(facts_batch, "fact", ns)
            if entity_details_batch:
                store.store_items(entity_details_batch, "entity_detail", ns)
            if prices_batch:
                store.store_timeseries(prices_batch, ns)
                counts["prices"] += len(prices_batch)
        else:
            # Fallback: interface methods
            if documents_batch:
                store.store_passages(documents_batch, ns)  # documents use same store
            if passages_batch:
                store.store_passages(passages_batch, ns)
            if facts_batch:
                store.store_facts(facts_batch, ns)
            if prices_batch:
                store.store_prices(prices_batch, ns)
                counts["prices"] += len(prices_batch)
            if entity_details_batch:
                for ent in entity_details_batch:
                    store.store_entity_details(ent, ns)

        return counts


def get_storage_router(graph=None, namespace: str = "default") -> StorageRouter:
    """Get or create the global storage router."""
    global _global_router
    if _global_router is None:
        with _router_lock:
            if _global_router is None:
                _global_router = StorageRouter(graph=graph, namespace=namespace)
    if graph and _global_router._graph is None:
        _global_router.set_graph(graph)
    return _global_router
