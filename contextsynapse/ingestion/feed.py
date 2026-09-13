"""
Real-Time Feed Ingestion
=========================
Ingest news articles from RSS feeds, web scraping, APIs, and webhooks
into the graph with streaming enrichment (fast ingest → background deep enrichment).

Architecture:
  Source → FeedManager.ingest_article() → Fast Ingest (2s, searchable)
                                        → Background Enrichment Queue (30s, connected)

Usage::

    feed = FeedManager(graph_registry, namespace="news_graph")

    # From RSS
    feed.add_rss_source("https://timesofindia.indiatimes.com/rssfeeds/...")
    feed.poll_all()  # fetches new articles

    # From webhook
    article = feed.ingest_article(url="https://...", title="...", content="...", source="toi")

    # From API
    feed.add_api_source("newsapi", api_key="...", query="India elections")
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class FeedSource:
    """A configured feed source."""
    source_id: str
    source_type: str  # rss | scrape | api | webhook
    name: str
    url: str = ""
    config: Dict[str, Any] = field(default_factory=dict)
    poll_interval_minutes: int = 5
    last_polled: Optional[str] = None
    active: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "name": self.name,
            "url": self.url,
            "config": self.config,
            "poll_interval_minutes": self.poll_interval_minutes,
            "last_polled": self.last_polled,
            "active": self.active,
        }


@dataclass
class ArticleIngest:
    """An article to be ingested into the graph."""
    url: str = ""
    title: str = ""
    content: str = ""
    source: str = ""           # e.g. "toi", "reuters", "ndtv"
    published_at: str = ""     # ISO timestamp
    author: str = ""
    tags: List[str] = field(default_factory=list)
    metadata: Dict[str, Any] = field(default_factory=dict)

    @property
    def content_hash(self) -> str:
        """Deduplicate by content hash."""
        return hashlib.sha256((self.url + self.title + self.content[:500]).encode()).hexdigest()[:16]


@dataclass
class IngestResult:
    """Result of ingesting an article."""
    article_id: str
    status: str  # "ingested" | "duplicate" | "error"
    nodes_created: int = 0
    enrichment_queued: bool = False
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "article_id": self.article_id,
            "status": self.status,
            "nodes_created": self.nodes_created,
            "enrichment_queued": self.enrichment_queued,
            "message": self.message,
        }


class FeedManager:
    """Manages feed sources and article ingestion with streaming enrichment.

    Integrates with the Context + Session architecture:
    - Each feed source maps to a Context (atomic data container)
    - Context has a graph namespace where articles live
    - Context attaches to Sessions (runtime boundaries)
    - Agents in the Session see feed articles automatically

    Flow: Feed Source → Context → Session → Agent sees articles
    """

    def __init__(self, graph_registry=None, namespace: str = "default",
                 context_manager=None, session_manager=None,
                 enrichment_enabled: bool = True):
        self._registry = graph_registry
        self._namespace = namespace
        self._context_manager = context_manager
        self._session_manager = session_manager
        self._enrichment_enabled = enrichment_enabled
        self._sources: Dict[str, FeedSource] = {}
        self._ingested_hashes: set = set()  # dedup cache
        self._enrichment_queue: List[Dict] = []
        self._enrichment_thread: Optional[threading.Thread] = None
        self._running = False
        # source_id → context_id mapping
        self._source_contexts: Dict[str, str] = {}
        # Smart pipeline: use new 7-stage ingestion (Clean→Chunk→Extract→Link→Embed→Index→Validate)
        self._use_smart_pipeline = True

    def _get_db(self):
        if self._registry:
            return self._registry.get_graph(self._namespace, load_if_missing=True)
        return None

    def _ensure_context_for_source(self, source: FeedSource) -> Optional[str]:
        """Create or get the Context for a feed source.

        Each feed source gets its own Context so articles are isolated
        and can be attached/detached from Sessions independently.
        """
        if source.source_id in self._source_contexts:
            return self._source_contexts[source.source_id]

        if not self._context_manager:
            return None

        # Check if context already exists for this source
        context_name = f"feed:{source.name}"
        try:
            existing = self._context_manager.get_context_by_name(context_name)
            if existing:
                self._source_contexts[source.source_id] = existing.context_id
                self._namespace = existing.graph_namespace
                return existing.context_id
        except Exception:
            pass

        # Create new context for this feed source
        try:
            ctx = self._context_manager.create_context(
                name=context_name,
                context_type="web",
                description=f"Real-time feed from {source.name} ({source.source_type})",
                source=source.url or source.source_type,
                sensitivity="public",
                tags=["feed", source.source_type, source.name],
            )
            self._source_contexts[source.source_id] = ctx.context_id
            self._namespace = ctx.graph_namespace
            logger.info("[FEED] Created context '%s' for source '%s' (ns: %s)",
                        context_name, source.name, ctx.graph_namespace)
            return ctx.context_id
        except Exception as e:
            logger.warning("[FEED] Could not create context for %s: %s", source.name, e)
            return None

    def attach_to_session(self, source_id: str, session_id: str) -> bool:
        """Attach a feed source's context to a session.

        After this, agents in the session see all articles from this feed.
        New articles auto-appear as they're ingested.
        """
        if not self._session_manager:
            logger.warning("[FEED] No session manager — cannot attach")
            return False

        context_id = self._source_contexts.get(source_id)
        if not context_id:
            source = self._sources.get(source_id)
            if source:
                context_id = self._ensure_context_for_source(source)
        if not context_id:
            return False

        try:
            ok = self._session_manager.attach_context(session_id, context_id, role="input")
            if ok:
                logger.info("[FEED] Attached feed '%s' to session '%s'", source_id, session_id[:12])
            return ok
        except Exception as e:
            logger.warning("[FEED] Attach failed: %s", e)
            return False

    # ── Source Management ──────────────────────────────────

    def add_rss_source(self, url: str, name: str = "", poll_minutes: int = 5) -> FeedSource:
        """Add an RSS/Atom feed source."""
        src = FeedSource(
            source_id=str(uuid.uuid4())[:8],
            source_type="rss",
            name=name or url.split("/")[2],
            url=url,
            poll_interval_minutes=poll_minutes,
        )
        self._sources[src.source_id] = src
        logger.info("[FEED] Added RSS source: %s (%s)", src.name, url)
        return src

    def add_scrape_source(self, url: str, name: str = "", selector: str = "article",
                          poll_minutes: int = 15) -> FeedSource:
        """Add a web scraping source."""
        src = FeedSource(
            source_id=str(uuid.uuid4())[:8],
            source_type="scrape",
            name=name or url.split("/")[2],
            url=url,
            config={"selector": selector},
            poll_interval_minutes=poll_minutes,
        )
        self._sources[src.source_id] = src
        return src

    def add_api_source(self, provider: str, api_key: str = "", query: str = "",
                       poll_minutes: int = 10) -> FeedSource:
        """Add a news API source (NewsAPI, Google News, etc.)."""
        src = FeedSource(
            source_id=str(uuid.uuid4())[:8],
            source_type="api",
            name=f"{provider}:{query[:30]}",
            url=provider,
            config={"api_key": api_key, "query": query, "provider": provider},
            poll_interval_minutes=poll_minutes,
        )
        self._sources[src.source_id] = src
        return src

    def remove_source(self, source_id: str) -> bool:
        return self._sources.pop(source_id, None) is not None

    def list_sources(self) -> List[FeedSource]:
        return list(self._sources.values())

    # ── Article Ingestion (Fast Path — 2s) ────────────────

    def ingest_article(self, article: ArticleIngest, source_id: str = "") -> IngestResult:
        """Ingest a single article into the graph (fast path).

        If source_id is provided and has a Context, articles are written
        to that Context's graph namespace. This means:
        - Articles are isolated per feed source
        - Sessions attached to the Context see articles automatically
        - Detaching the Context removes all articles cleanly

        Phase 1 (immediate, ~2s):
          - Create Article node with metadata
          - Chunk content into TextChunk nodes
          - Index in BM25 for keyword search
          - Deduplicate by content hash

        Phase 2 (background, ~30s):
          - Extract entities (people, places, orgs)
          - Extract facts
          - Generate vector embeddings
          - Link to existing graph nodes
        """
        # Route to source's Context namespace if available
        if source_id and source_id in self._sources:
            self._ensure_context_for_source(self._sources[source_id])

        # Dedup check
        content_hash = article.content_hash
        if content_hash in self._ingested_hashes:
            return IngestResult(
                article_id=content_hash,
                status="duplicate",
                message="Article already ingested (content hash match)",
            )

        db = self._get_db()
        if not db:
            return IngestResult(article_id="", status="error", message="No graph available")

        # Smart pipeline: use new ingestion if available
        if self._use_smart_pipeline:
            try:
                from .smart_ingest import ingest_text
                result = ingest_text(
                    text=article.content,
                    db=db,
                    title=article.title,
                    source_url=article.url,
                    pipeline="smart_article",
                )
                self._ingested_hashes.add(content_hash)
                return IngestResult(
                    article_id=result.document_id,
                    status="ingested" if not result.errors else "error",
                    message=f"Smart pipeline: {len(result.passage_ids)} passages, {len(result.entity_ids)} entities, {len(result.fact_ids)} facts, {result.edge_count} edges",
                    nodes_created=len(result.passage_ids) + len(result.entity_ids) + len(result.fact_ids) + 1,
                    edges_created=result.edge_count,
                )
            except Exception as e:
                logger.warning("Smart pipeline failed, falling back to legacy: %s", e)

        from ..core.graph_structures import GraphNode, GraphEdge

        now = datetime.now(timezone.utc).isoformat()
        article_id = f"article_{content_hash}"

        # ── Phase 1: Fast Ingest ──

        # 1. Create Article node
        article_props = {
            "name": article.title,
            "title": article.title,
            "url": article.url,
            "source": article.source,
            "author": article.author,
            "published_at": article.published_at or now,
            "content": article.content[:500],  # preview in node
            "content_hash": content_hash,
            "tags": article.tags,
            "ingested_at": now,
            "_created_at": now,
            "sensitivity": "public",
            "status": "ingested",  # becomes "enriched" after phase 2
        }
        article_props.update(article.metadata)

        db.add_node(GraphNode(
            id=article_id, label="Article", properties=article_props,
        ), write_through=True)
        nodes_created = 1

        # 2. Chunk content into TextChunk nodes
        chunks = self._chunk_text(article.content, max_chunk=500)
        for i, chunk_text in enumerate(chunks):
            chunk_id = f"{article_id}_chunk_{i}"
            db.add_node(GraphNode(
                id=chunk_id, label="TextChunk",
                properties={
                    "name": f"{article.title[:50]} (chunk {i+1}/{len(chunks)})",
                    "content": chunk_text,
                    "chunk_index": i,
                    "article_id": article_id,
                    "source": article.source,
                    "_created_at": now,
                },
            ), write_through=True)
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=article_id, target=chunk_id,
                label="CONTAINS", properties={"chunk_index": i},
            ))
            nodes_created += 1

        # 3. Extract quick facts (regex-based, no LLM needed)
        quick_facts = self._extract_quick_facts(article.content, article.title)
        for fact_text in quick_facts:
            fact_id = f"fact_{hashlib.sha256(fact_text.encode()).hexdigest()[:12]}"
            db.add_node(GraphNode(
                id=fact_id, label="Fact",
                properties={
                    "name": fact_text[:100],
                    "content": fact_text,
                    "source": article.source,
                    "article_id": article_id,
                    "_created_at": now,
                },
            ), write_through=True)
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=article_id, target=fact_id,
                label="STATES", properties={},
            ))
            nodes_created += 1

        # 4a. Index into LMDB BM25 for keyword search
        try:
            from ..search.lmdb_index import get_lmdb_index
            lmdb_idx = get_lmdb_index(self._namespace)
            lmdb_idx.index_node(article_id, "Article", article_props)
            for i, chunk_text in enumerate(chunks):
                chunk_id = f"{article_id}_chunk_{i}"
                lmdb_idx.index_node(chunk_id, "TextChunk", {
                    "name": f"{article.title[:50]} (chunk {i+1})",
                    "content": chunk_text,
                })
        except Exception:
            pass

        # 4b. Auto-embed for vector search (if available)
        enrichment_queued = False
        try:
            from ..context.vector_integration import SessionVectorStore
            svs = SessionVectorStore()
            if svs.available:
                # Embed the article summary + chunks
                embed_text = f"{article.title}. {article.content[:300]}"
                svs.add_text(self._namespace, embed_text, article_id,
                             metadata={"label": "Article", "name": article.title})
                for i, chunk_text in enumerate(chunks):
                    svs.add_text(self._namespace, chunk_text, f"{article_id}_chunk_{i}",
                                 metadata={"label": "TextChunk", "name": f"chunk {i+1}"})
        except Exception:
            pass

        # 5. Queue for background enrichment
        if self._enrichment_enabled:
            self._enrichment_queue.append({
                "article_id": article_id,
                "title": article.title,
                "content": article.content,
                "source": article.source,
                "namespace": self._namespace,
            })
            enrichment_queued = True
            self._start_enrichment_worker()

        self._ingested_hashes.add(content_hash)

        logger.info("[FEED] Ingested article: %s (%d nodes, enrichment=%s)",
                    article.title[:50], nodes_created, enrichment_queued)

        return IngestResult(
            article_id=article_id,
            status="ingested",
            nodes_created=nodes_created,
            enrichment_queued=enrichment_queued,
            message=f"Article '{article.title[:50]}' ingested with {nodes_created} nodes",
        )

    # ── RSS Polling ───────────────────────────────────────

    def poll_rss(self, source: FeedSource) -> List[IngestResult]:
        """Poll an RSS feed and ingest new articles."""
        results = []
        try:
            import feedparser
            feed = feedparser.parse(source.url)
            for entry in feed.entries[:20]:  # max 20 per poll
                article = ArticleIngest(
                    url=entry.get("link", ""),
                    title=entry.get("title", ""),
                    content=entry.get("summary", entry.get("description", "")),
                    source=source.name,
                    published_at=entry.get("published", ""),
                    author=entry.get("author", ""),
                    tags=[t.get("term", "") for t in entry.get("tags", [])],
                )
                result = self.ingest_article(article, source_id=source.source_id)
                results.append(result)
        except ImportError:
            logger.warning("[FEED] feedparser not installed: pip install feedparser")
        except Exception as e:
            logger.error("[FEED] RSS poll failed for %s: %s", source.name, e)

        source.last_polled = datetime.now(timezone.utc).isoformat()
        return results

    def poll_all(self) -> Dict[str, List[IngestResult]]:
        """Poll all active feed sources."""
        results = {}
        for src in self._sources.values():
            if not src.active:
                continue
            if src.source_type == "rss":
                results[src.source_id] = self.poll_rss(src)
            # scrape and api would go here
        return results

    # ── Background Enrichment (Deep Path — 30s) ──────────

    def _start_enrichment_worker(self):
        """Start background enrichment thread if not already running."""
        if self._enrichment_thread and self._enrichment_thread.is_alive():
            return
        self._running = True
        self._enrichment_thread = threading.Thread(
            target=self._enrichment_loop, daemon=True, name="feed-enrichment",
        )
        self._enrichment_thread.start()

    def _enrichment_loop(self):
        """Process enrichment queue in background."""
        while self._running and self._enrichment_queue:
            item = self._enrichment_queue.pop(0)
            try:
                self._enrich_article(item)
            except Exception as e:
                logger.error("[FEED] Enrichment failed for %s: %s", item.get("article_id"), e)
            time.sleep(0.5)  # pace between articles
        self._running = False

    def _enrich_article(self, item: Dict):
        """Deep enrichment: entities, facts, relationships, vectors."""
        db = self._get_db()
        if not db:
            return

        from ..core.graph_structures import GraphNode, GraphEdge

        article_id = item["article_id"]
        content = item["content"]
        now = datetime.now(timezone.utc).isoformat()

        # 1. Entity extraction (LLM-powered if available, regex fallback)
        entities = self._extract_entities(content)
        for ent_name, ent_type in entities:
            ent_id = f"entity_{hashlib.sha256(ent_name.lower().encode()).hexdigest()[:12]}"
            # Create or merge entity node
            existing = db.get_node(ent_id)
            if not existing:
                db.add_node(GraphNode(
                    id=ent_id, label="Entity",
                    properties={
                        "name": ent_name,
                        "type": ent_type,
                        "_created_at": now,
                        "mention_count": 1,
                    },
                ), write_through=True)
            else:
                # Increment mention count
                props = getattr(existing, "properties", {}) or {}
                props["mention_count"] = props.get("mention_count", 0) + 1
                props["_updated_at"] = now

            # Link article → entity
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=article_id, target=ent_id,
                label="MENTIONS", properties={"extracted_at": now},
            ))

        # 2. Update article status
        article_node = db.get_node(article_id)
        if article_node:
            props = getattr(article_node, "properties", {}) or {}
            props["status"] = "enriched"
            props["enriched_at"] = now
            props["entity_count"] = len(entities)

        logger.info("[FEED] Enriched article %s: %d entities", article_id[:20], len(entities))

    # ── Helpers ───────────────────────────────────────────

    @staticmethod
    def _chunk_text(text: str, max_chunk: int = 500) -> List[str]:
        """Split text into chunks at sentence boundaries."""
        if len(text) <= max_chunk:
            return [text] if text.strip() else []

        chunks = []
        sentences = text.replace(". ", ".\n").split("\n")
        current = ""
        for sent in sentences:
            if len(current) + len(sent) > max_chunk and current:
                chunks.append(current.strip())
                current = sent
            else:
                current += " " + sent if current else sent
        if current.strip():
            chunks.append(current.strip())
        return chunks

    @staticmethod
    def _extract_quick_facts(content: str, title: str) -> List[str]:
        """Extract obvious facts using patterns (no LLM needed).

        Looks for: percentages, dates, names with titles, quoted statements.
        """
        import re
        facts = []

        # Sentences with percentages — split on ". " (not bare ".") to avoid breaking "92.5%"
        sentences = re.split(r'\.\s+', content)
        for sent in sentences:
            sent = sent.strip()
            if re.search(r'\d+\.?\d*\s*%', sent) and len(sent) > 20:
                facts.append(sent[:200])

        # Sentences with years (2024, 2025, 2026)
        for sent in sentences:
            sent = sent.strip()
            if re.search(r'\b20[2-3]\d\b', sent) and len(sent) > 20 and sent not in facts:
                facts.append(sent[:200])

        # Quoted statements
        quotes = re.findall(r'"([^"]{20,200})"', content)
        for q in quotes[:3]:
            facts.append(q)

        return facts[:10]  # max 10 quick facts

    @staticmethod
    def _extract_entities(content: str) -> List[tuple]:
        """Extract named entities using regex patterns.

        Returns list of (name, type) tuples.
        """
        import re
        entities = set()

        # Capitalized multi-word names (Person/Org)
        for match in re.finditer(r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\b', content):
            name = match.group(1)
            if len(name) > 4 and name not in ("The Times", "India News", "Breaking News"):
                entities.add((name, "Person/Org"))

        # Indian state/city names
        known_places = {
            "Mumbai", "Delhi", "Kolkata", "Chennai", "Bengaluru", "Hyderabad",
            "Ahmedabad", "Pune", "Jaipur", "Lucknow", "Rajasthan", "Tamil Nadu",
            "Maharashtra", "West Bengal", "Karnataka", "Kerala", "Gujarat",
        }
        for place in known_places:
            if place in content:
                entities.add((place, "Place"))

        # Organization patterns
        for match in re.finditer(r'\b((?:BJP|Congress|PMK|DMK|TMC|AAP|EC|RBSE|KMC)\b)', content):
            entities.add((match.group(1), "Organization"))

        return list(entities)[:20]

    def stop(self):
        """Stop background enrichment."""
        self._running = False
