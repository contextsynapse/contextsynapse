"""
Base Connector Interface
=========================
All live connectors (RSS, Kafka, Webhook, Scraper, API, Database)
implement this interface. Connectors pull data from external sources
and route it through the existing ingestion pipeline into a Context.

Connector → Document → Existing Pipeline → Context → Session → Agents

Usage::

    class MyConnector(BaseConnector):
        def poll(self) -> List[ConnectorDocument]:
            return [ConnectorDocument(title="...", content="...", url="...")]

    mgr = ConnectorManager(graph_registry, context_manager, session_manager)
    mgr.register(MyConnector(config={...}), target_context_id="ctx_123")
    mgr.poll_all()  # triggers ingestion for each new document
"""

from __future__ import annotations

import hashlib
import logging
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ConnectorDocument:
    """A document produced by a connector — fed into the ingestion pipeline."""
    title: str = ""
    content: str = ""
    url: str = ""
    source: str = ""            # connector name
    published_at: str = ""      # ISO timestamp
    author: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)
    doc_type: str = "article"   # article | event | commit | issue | message

    @property
    def content_hash(self) -> str:
        """Dedup key."""
        return hashlib.sha256(
            (self.url + self.title + self.content[:500]).encode()
        ).hexdigest()[:16]


@dataclass
class ConnectorConfig:
    """Configuration for a connector instance."""
    connector_id: str = ""
    connector_type: str = ""       # rss | webhook | kafka | scraper | api | database
    name: str = ""
    target_context_id: str = ""    # which Context to ingest into
    pipeline: str = "builtin:knowledge-graph"  # ingestion pipeline to use
    llm_model: str = ""            # LLM for enrichment (optional)
    embedding_model: str = ""      # embedding model (optional)
    poll_interval_minutes: int = 5
    active: bool = True
    config: Dict[str, Any] = field(default_factory=dict)  # type-specific config

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "connector_type": self.connector_type,
            "name": self.name,
            "target_context_id": self.target_context_id,
            "pipeline": self.pipeline,
            "llm_model": self.llm_model,
            "poll_interval_minutes": self.poll_interval_minutes,
            "active": self.active,
            "config": {k: v for k, v in self.config.items() if k != "api_key"},
        }


@dataclass
class ConnectorStatus:
    """Status of a connector."""
    connector_id: str
    active: bool
    last_polled: Optional[str] = None
    documents_ingested: int = 0
    errors: int = 0
    last_error: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "connector_id": self.connector_id,
            "active": self.active,
            "last_polled": self.last_polled,
            "documents_ingested": self.documents_ingested,
            "errors": self.errors,
            "last_error": self.last_error,
        }


class BaseConnector(ABC):
    """Base class for all live connectors.

    Subclasses implement either:
    - poll() for batch/scheduled connectors (RSS, API, Scraper, Database)
    - subscribe(callback) for streaming connectors (Kafka, Redis Streams, WebSocket)
    """

    def __init__(self, config: ConnectorConfig):
        self.config = config
        self._status = ConnectorStatus(
            connector_id=config.connector_id,
            active=config.active,
        )
        self._seen_hashes: set = set()  # dedup cache

    @property
    def connector_type(self) -> str:
        return self.config.connector_type

    @property
    def status(self) -> ConnectorStatus:
        return self._status

    @abstractmethod
    def poll(self) -> List[ConnectorDocument]:
        """Pull new documents from the source (batch connectors).

        Returns only NEW documents since last poll (dedup by content_hash).
        """
        ...

    def subscribe(self, callback: Callable[[ConnectorDocument], None]):
        """Subscribe to streaming documents (streaming connectors).

        Default: not implemented. Override for Kafka, Redis Streams, WebSocket.
        """
        raise NotImplementedError(f"{self.__class__.__name__} doesn't support streaming")

    def health_check(self) -> bool:
        """Check if the source is reachable."""
        return True

    def _dedup(self, docs: List[ConnectorDocument]) -> List[ConnectorDocument]:
        """Filter out already-seen documents."""
        new = []
        for doc in docs:
            h = doc.content_hash
            if h not in self._seen_hashes:
                self._seen_hashes.add(h)
                new.append(doc)
        return new


class ConnectorManager:
    """Manages all active connectors and routes documents to the ingestion pipeline.

    This is the thin orchestration layer:
    1. Connector produces ConnectorDocuments
    2. Manager calls the EXISTING ingestion pipeline (same as dashboard ingest)
    3. Documents land in the target Context
    4. Context is already attached to Sessions → agents see new data
    """

    def __init__(self, graph_registry=None, context_manager=None,
                 session_manager=None, ingest_fn=None):
        """
        Args:
            ingest_fn: Optional function(context_id, content, source, pipeline, llm_model)
                that triggers the existing ingestion pipeline. If not provided,
                documents are written directly to the context graph.
        """
        self._registry = graph_registry
        self._context_manager = context_manager
        self._session_manager = session_manager
        self._ingest_fn = ingest_fn
        self._connectors: Dict[str, BaseConnector] = {}

    def register(self, connector: BaseConnector) -> str:
        """Register a connector. Returns connector_id."""
        cid = connector.config.connector_id or str(uuid.uuid4())[:8]
        connector.config.connector_id = cid
        connector._status.connector_id = cid
        self._connectors[cid] = connector
        logger.info("[CONNECTOR] Registered %s '%s' → context %s",
                    connector.connector_type, connector.config.name,
                    connector.config.target_context_id)
        return cid

    def unregister(self, connector_id: str) -> bool:
        return self._connectors.pop(connector_id, None) is not None

    def get(self, connector_id: str) -> Optional[BaseConnector]:
        return self._connectors.get(connector_id)

    def list_connectors(self) -> List[ConnectorConfig]:
        return [c.config for c in self._connectors.values()]

    def list_status(self) -> List[ConnectorStatus]:
        return [c.status for c in self._connectors.values()]

    def poll_one(self, connector_id: str) -> List[ConnectorDocument]:
        """Poll a single connector and ingest new documents."""
        connector = self._connectors.get(connector_id)
        if not connector or not connector.config.active:
            return []

        try:
            new_docs = connector.poll()

            for doc in new_docs:
                self._ingest_document(connector.config, doc)
                connector._status.documents_ingested += 1

            connector._status.last_polled = datetime.now(timezone.utc).isoformat()
            return new_docs

        except Exception as e:
            connector._status.errors += 1
            connector._status.last_error = str(e)[:200]
            logger.error("[CONNECTOR] Poll failed for %s: %s", connector.config.name, e)
            return []

    def poll_all(self) -> Dict[str, int]:
        """Poll all active connectors. Returns {connector_id: new_doc_count}."""
        results = {}
        for cid, connector in self._connectors.items():
            if connector.config.active:
                docs = self.poll_one(cid)
                results[cid] = len(docs)
        return results

    def _ingest_document(self, config: ConnectorConfig, doc: ConnectorDocument):
        """Route a document through the existing ingestion pipeline."""
        # Option 1: Use the provided ingest function (calls dashboard pipeline)
        if self._ingest_fn:
            try:
                self._ingest_fn(
                    context_id=config.target_context_id,
                    content=doc.content,
                    title=doc.title,
                    source=doc.url or doc.source,
                    pipeline=config.pipeline,
                    llm_model=config.llm_model,
                    metadata={
                        "url": doc.url,
                        "author": doc.author,
                        "published_at": doc.published_at,
                        "tags": doc.tags,
                        "connector_id": config.connector_id,
                        "doc_type": doc.doc_type,
                    },
                )
                return
            except Exception as e:
                logger.warning("[CONNECTOR] Pipeline ingest failed, falling back to direct: %s", e)

        # Option 2: Direct graph write (fallback when no pipeline available)
        self._direct_ingest(config, doc)

    def _direct_ingest(self, config: ConnectorConfig, doc: ConnectorDocument):
        """Write document directly to context graph (fallback path)."""
        if not self._context_manager or not self._registry:
            return

        try:
            ctx = self._context_manager.get_context(config.target_context_id)
            if not ctx:
                return

            db = self._registry.get_graph(ctx.graph_namespace, load_if_missing=True)
            if not db:
                return

            from ..core.graph_structures import GraphNode, GraphEdge

            now = datetime.now(timezone.utc).isoformat()
            doc_id = f"doc_{doc.content_hash}"

            # Create document node
            db.add_node(GraphNode(
                id=doc_id,
                label="Document",
                properties={
                    "name": doc.title,
                    "title": doc.title,
                    "url": doc.url,
                    "source": doc.source,
                    "author": doc.author,
                    "published_at": doc.published_at or now,
                    "content": doc.content[:500],
                    "tags": doc.tags,
                    "connector_id": config.connector_id,
                    "doc_type": doc.doc_type,
                    "_created_at": now,
                    "sensitivity": "public",
                },
            ), write_through=True)

            # Chunk + create TextChunk nodes
            chunks = self._chunk(doc.content)
            for i, chunk_text in enumerate(chunks):
                chunk_id = f"{doc_id}_c{i}"
                db.add_node(GraphNode(
                    id=chunk_id, label="TextChunk",
                    properties={
                        "name": f"{doc.title[:40]} (chunk {i+1}/{len(chunks)})",
                        "content": chunk_text,
                        "chunk_index": i,
                        "doc_id": doc_id,
                        "_created_at": now,
                    },
                ), write_through=True)
                db.add_edge(GraphEdge(
                    id=str(uuid.uuid4()), source=doc_id, target=chunk_id,
                    label="CONTAINS", properties={},
                ))

            # Auto-embed if available
            try:
                from ..context.vector_integration import SessionVectorStore
                svs = SessionVectorStore()
                if svs.available:
                    svs.add_text(ctx.graph_namespace,
                                 f"{doc.title}. {doc.content[:300]}",
                                 doc_id,
                                 metadata={"label": "Document", "name": doc.title})
            except Exception:
                pass

            logger.info("[CONNECTOR] Direct ingested: %s → %s",
                        doc.title[:40], ctx.graph_namespace)

        except Exception as e:
            logger.error("[CONNECTOR] Direct ingest failed: %s", e)

    @staticmethod
    def _chunk(text: str, max_size: int = 500) -> List[str]:
        import re
        if len(text) <= max_size:
            return [text] if text.strip() else []
        chunks = []
        sentences = re.split(r'\.\s+', text)
        current = ""
        for s in sentences:
            if len(current) + len(s) > max_size and current:
                chunks.append(current.strip())
                current = s
            else:
                current += ". " + s if current else s
        if current.strip():
            chunks.append(current.strip())
        return chunks
