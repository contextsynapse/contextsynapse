"""Smart Ingestion Pipeline — the main entry point.

One function to ingest any web page into a rich knowledge graph:
  Clean -> Chunk -> Extract -> Link -> Embed -> Index -> Validate

Pipelines:
  - "smart_article"  : For news/blog articles. Uses Trafilatura + hybrid chunking +
                        local LLM extraction. Produces Document, Passage, Entity, Fact,
                        Link nodes with full relationship edges.
  - "smart_text"     : For raw text (no HTML). Same extraction but skips Trafilatura.

Usage:
    from contextsynapse.ingestion.smart_ingest import ingest_url, ingest_text

    result = ingest_url("https://example.com/article", db, schema)
    # result.document_id, result.passage_ids, result.entity_ids, etc.

    result = ingest_text("Some article text...", db, title="My Article")
"""
from __future__ import annotations

import logging
import uuid as _uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

from .cleaner import clean_webpage, clean_text, CleanDocument
from .chunker import chunk_document, PassageChunk
from .graph_builder import build_graph, BuildResult, ChunkExtraction
from .schema_extractor import (
    IngestionSchema, load_schema, regex_extract, llm_extract,
    merge_extractions, build_llm_prompt,
)
from .schema_validator import validate_and_flag
from .dedup import dedup_check
from .filters import FilterConfig, FilterResult, filter_chain, structural_pre_filter, post_extraction_filter
from .strategies import ContentStrategy, get_strategy, list_strategies
from .amplifier import amplify_extractions
from ..intelligence.tagger import tag_extractions as _tag_extractions
from ..intelligence.dedup import ContentFingerprint, SemanticDedup

logger = logging.getLogger(__name__)

# Module-level dedup instances (shared across ingestion calls within same process)
_content_fingerprint: Optional[ContentFingerprint] = None
_semantic_dedup: Optional[SemanticDedup] = None


def _get_content_fingerprint() -> ContentFingerprint:
    global _content_fingerprint
    if _content_fingerprint is None:
        _content_fingerprint = ContentFingerprint()
    return _content_fingerprint


def _get_semantic_dedup() -> SemanticDedup:
    global _semantic_dedup
    if _semantic_dedup is None:
        _semantic_dedup = SemanticDedup()
    return _semantic_dedup

# In-memory staging store for review mode previews
_staging_store: Dict[str, "StagingResult"] = {}


@dataclass
class StagingResult:
    """Preview of what would be created — not yet committed to graph."""
    staging_id: str = ""
    clean_doc: Any = None
    chunks: List = field(default_factory=list)
    extractions: List = field(default_factory=list)
    passages: List[Dict] = field(default_factory=list)
    entities: List[Dict] = field(default_factory=list)
    facts: List[Dict] = field(default_factory=list)
    edges: List[Dict] = field(default_factory=list)
    links: List[Dict] = field(default_factory=list)
    quality_score: float = 0.0
    filter_log: List[Dict] = field(default_factory=list)
    schema_suggestions: List[Dict] = field(default_factory=list)
    schema_used: str = ""
    pipeline: str = ""
    created_at: str = ""

    def commit(self, db, schema=None, exclude_ids=None):
        """Commit staged results to graph."""
        from .graph_builder import build_graph
        exclude = set(exclude_ids or [])

        # Filter out excluded items
        if exclude:
            self.extractions = [
                ChunkExtraction(
                    chunk_index=ext.chunk_index,
                    entities=[e for e in ext.entities if e.get("_staging_id") not in exclude],
                    facts=[f for f in ext.facts if f.get("_staging_id") not in exclude],
                    relationships=ext.relationships,
                )
                for ext in self.extractions
            ]

        result = build_graph(self.clean_doc, self.chunks, self.extractions, db, schema, self.pipeline)
        _embed_nodes(result, db)
        _index_bm25(result, db)

        # Clean up staging
        if self.staging_id in _staging_store:
            del _staging_store[self.staging_id]

        return result

    def discard(self):
        """Discard staged results."""
        if self.staging_id in _staging_store:
            del _staging_store[self.staging_id]

    def to_dict(self) -> Dict:
        """Serialize for API response."""
        d = {
            "staging_id": self.staging_id,
            "passages": self.passages,
            "entities": self.entities,
            "facts": self.facts,
            "edges": self.edges,
            "links": self.links,
            "quality_score": self.quality_score,
            "filter_log": self.filter_log,
            "schema_used": self.schema_used,
            "pipeline": self.pipeline,
        }
        if self.schema_suggestions:
            d["schema_suggestions"] = self.schema_suggestions
        return d


def _calc_quality(passages, entities, facts, edges) -> float:
    """Calculate quality score 0-100. Uses amplifier signals when available."""
    if not passages:
        return 0.0

    # Passage quality (unchanged — longer, well-chunked passages are better)
    avg_tokens = sum(p.get("token_count", 0) for p in passages) / len(passages) if passages else 0
    passage_q = min(100, (avg_tokens / 500) * 100) if avg_tokens > 0 else 0

    # Entity quality — reward relevant entities, penalize noise
    if entities:
        relevant_entities = [e for e in entities if not e.get("_noise_flags")]
        noise_ratio = 1.0 - (len(relevant_entities) / len(entities)) if entities else 0
        relevances = [e.get("_amplifier_relevance", 0.5) for e in relevant_entities]
        avg_ent_relevance = sum(relevances) / len(relevances) if relevances else 0.5
        ent_score = min(100, (len(relevant_entities) / max(1, len(passages))) * 50) * avg_ent_relevance
        # Penalize high noise ratio
        ent_score *= max(0.3, 1.0 - noise_ratio)
    else:
        ent_score = 0

    # Fact quality — reward relevant facts
    if facts:
        fact_relevances = [f.get("_amplifier_relevance", 0.5) for f in facts]
        avg_fact_relevance = sum(fact_relevances) / len(fact_relevances)
        fact_score = min(100, (len(facts) / max(1, len(passages))) * 100) * avg_fact_relevance
    else:
        fact_score = 0

    # Edge score (unchanged)
    edge_score = min(100, len(edges) * 5)

    return round(passage_q * 0.3 + ent_score * 0.3 + fact_score * 0.25 + edge_score * 0.15, 1)


def _auto_detect_strategy(text: str) -> "ContentStrategy":
    """Auto-detect the best content strategy based on text signals."""
    import re as _re
    text_sample = text[:2000].lower()

    # Financial/business signals
    financial_signals = len(_re.findall(
        r'\b(?:revenue|earnings|profit|stock|market|billion|million|quarter|Q[1-4]|'
        r'CEO|IPO|shares|dividend|fiscal|GDP|inflation|interest rate)\b',
        text_sample, _re.IGNORECASE
    ))
    if financial_signals >= 3:
        return get_strategy("business_report")

    # News signals (dates, quotes, said/announced/reported)
    news_signals = len(_re.findall(
        r'\b(?:said|announced|reported|according to|confirmed|statement|officials?)\b',
        text_sample, _re.IGNORECASE
    ))
    if news_signals >= 2:
        return get_strategy("news_article")

    # Technical doc signals
    tech_signals = len(_re.findall(
        r'\b(?:API|function|class|install|import|config|parameter|returns?|module)\b',
        text_sample, _re.IGNORECASE
    ))
    if tech_signals >= 3:
        return get_strategy("technical_doc")

    # Default to news_article (richer extraction than general)
    return get_strategy("news_article")


def _entity_near_text(entity_name: str, text: str, window: int = 200) -> bool:
    """Check if entity name appears in text (for entity-level tag refinement)."""
    if not entity_name or not text:
        return False
    return entity_name.lower() in text.lower()


def _find_entity_sentiment(entity_name: str, chunk_text: str) -> str | None:
    """Determine entity-specific sentiment by looking at the sentence containing the entity."""
    import re as _re
    name_lower = entity_name.lower()
    text_lower = chunk_text.lower()

    if name_lower not in text_lower:
        return None

    # Find the sentence containing the entity
    sentences = _re.split(r'[.!?]+', chunk_text)
    for sentence in sentences:
        if name_lower in sentence.lower():
            from ..intelligence.heuristics import detect_sentiment
            result = detect_sentiment(sentence)
            if result.confidence > 0.3:
                return result.sentiment

    return None


def _apply_intelligence_tags(extractions, tag_result, chunk_texts):
    """Apply intelligence tags to entities with entity-level refinement.

    Instead of broadcasting all chunk-level tags to every entity, this checks:
    1. Sentiment: per-entity by analyzing the sentence containing the entity
    2. Geography: only if the entity appears near the geographic mention
    3. Domain/Impact: applied to all entities in the chunk (these are content-level)
    """
    for ext in extractions:
        chunk_tags = [t for t in tag_result.tags if t.chunk_index == ext.chunk_index]
        chunk_text = chunk_texts[ext.chunk_index] if ext.chunk_index < len(chunk_texts) else ""

        # Get chunk-level tags as defaults
        chunk_sentiment = None
        chunk_sentiment_conf = 0.0
        sentiments = [t for t in chunk_tags if t.dimension == "sentiment"]
        if sentiments:
            best = max(sentiments, key=lambda t: t.confidence)
            chunk_sentiment = best.value
            chunk_sentiment_conf = best.confidence

        chunk_impact = None
        impacts = [t for t in chunk_tags if t.dimension == "impact"]
        if impacts:
            chunk_impact = max(impacts, key=lambda t: t.confidence).value

        chunk_geos = [t.value for t in chunk_tags if t.dimension == "geography"]
        chunk_domains = [t.value for t in chunk_tags if t.dimension == "domain"]

        for ent in ext.entities:
            props = ent.setdefault("properties", {})
            ent_name = props.get("name", "")

            # Sentiment: try entity-specific first, fall back to chunk-level
            if chunk_text and ent_name:
                entity_sentiment = _find_entity_sentiment(ent_name, chunk_text)
                if entity_sentiment:
                    props["_sentiment"] = entity_sentiment
                    props["_sentiment_confidence"] = 0.7
                elif chunk_sentiment:
                    props["_sentiment"] = chunk_sentiment
                    props["_sentiment_confidence"] = chunk_sentiment_conf * 0.8  # lower confidence for inherited
            elif chunk_sentiment:
                props["_sentiment"] = chunk_sentiment
                props["_sentiment_confidence"] = chunk_sentiment_conf

            # Impact: content-level, apply to all
            if chunk_impact:
                props["_impact"] = chunk_impact

            # Geography: only geos that appear near the entity in text
            if chunk_geos and chunk_text and ent_name:
                import re as _re
                relevant_geos = []
                # Find sentences containing this entity
                entity_sentences = []
                for sentence in _re.split(r'[.!?]+', chunk_text):
                    if ent_name.lower() in sentence.lower():
                        entity_sentences.append(sentence)

                if entity_sentences:
                    entity_context = " ".join(entity_sentences)
                    for geo in chunk_geos:
                        if geo.lower() in entity_context.lower():
                            relevant_geos.append(geo)

                # If no geo found near entity, don't assign any (rather than wrong ones)
                if relevant_geos:
                    props["_geography"] = relevant_geos
            elif chunk_geos:
                props["_geography"] = chunk_geos

            # Domain: content-level, apply to all
            if chunk_domains:
                props["_domain"] = chunk_domains

        # Facts get chunk-level sentiment (facts are statements, not entities)
        for fact in ext.facts:
            fact_props = fact.setdefault("properties", {})
            if chunk_sentiment:
                fact_props["_sentiment"] = chunk_sentiment

        # Freshness: use article publish date first, then extract from text
        try:
            from ..intelligence.freshness import detect_event_date, score_freshness
            from datetime import datetime, timezone as _tz
            now_iso = datetime.now(_tz.utc).isoformat()

            # Priority 1: article's published_at from metadata (most reliable)
            # Priority 2: event date extracted from content text
            event_date = ""
            date_source = ""

            # Check if any entity already has _published_at from document metadata
            for ent in ext.entities:
                pub = ent.get("properties", {}).get("_published_at", "")
                if pub:
                    event_date = pub[:10]  # ISO date portion
                    date_source = "article_metadata"
                    break

            # Fallback: extract from content text
            if not event_date:
                freshness_result = detect_event_date(chunk_text)
                if freshness_result.event_date:
                    event_date = freshness_result.event_date
                    date_source = "content_extraction"

            if event_date:
                scored = score_freshness(event_date, now_iso)
                for ent in ext.entities:
                    props = ent.setdefault("properties", {})
                    props["_event_date"] = event_date
                    props["_freshness"] = scored.freshness
                    props["_staleness_days"] = scored.staleness_days
                    props["_date_source"] = date_source
                    props["_ingested_at"] = now_iso
                for fact in ext.facts:
                    fact_props = fact.setdefault("properties", {})
                    fact_props["_event_date"] = event_date
                    fact_props["_freshness"] = scored.freshness
                    fact_props["_staleness_days"] = scored.staleness_days
                    fact_props["_date_source"] = date_source
        except Exception:
            pass


# Pipeline registry — describes available pipelines for UI/API
#
# "smart_pipeline" is the DEFAULT and only pipeline most users need.
# It auto-detects input type (URL, text, file, chat export) and
# auto-selects the appropriate schema. The stages are always the same:
#   PARSE → CHUNK → EXTRACT → LINK → EMBED → INDEX → VALIDATE → CU
#
# The other pipelines exist for backward compatibility and edge cases.

PIPELINES = {
    "smart_pipeline": {
        "name": "Smart Pipeline",
        "description": "Auto-detects content type (articles, documents, conversations, "
                       "structured data) and extracts entities, facts, and relationships. "
                       "Uses LLM for high-quality extraction and vector embeddings for search.",
        "input_types": ["url", "html", "text", "file", "zip", "json", "csv"],
        "stages": [
            "parse",
            "chunk:hierarchical",   # section-aware: Document→Passage(section)→Fact→Entity
            "extract:llm+regex",
            "amplify:semantic",     # relevance scoring via bi-encoder cosine similarity
            "link:graph",
            "embed:qdrant",         # passage + entity vectors → Qdrant
            "index:bm25",
            "validate:schema",
        ],
        "chunk_strategy": "hierarchical",
        "llm_required": True,
        "produces": ["Document", "Passage", "Entity", "Fact", "Conversation", "Message", "Topic"],
        "edge_types": ["CONTAINS", "NEXT", "MENTIONS", "STATES", "DERIVED_FROM",
                       "LINKS_TO", "HAS_CONVERSATION", "HAS_MESSAGE", "DISCUSSES"],
        "default": True,
    },
    "website": {
        "name": "Website Pipeline",
        "description": "Structured ingestion for web pages — preserves heading hierarchy "
                       "(H1→H2→H3) as Section nodes. Uses trafilatura markdown output so "
                       "heading depth is retained. Best for IR pages, about pages, blog posts.",
        "input_types": ["url", "html"],
        "stages": [
            "parse",
            "chunk:hierarchical:header-subheader",  # depth-aware: Section(H1)→Section(H2)→Passage→Fact→Entity
            "extract:llm+regex",
            "amplify:semantic",
            "link:graph",
            "embed:qdrant",
            "index:bm25",
            "validate:schema",
        ],
        "chunk_strategy": "hierarchical",
        "content_strategy": "website",
        "llm_required": True,
        "produces": ["Document", "Section", "Passage", "Entity", "Fact"],
        "edge_types": ["CONTAINS", "HAS_SECTION", "NEXT", "MENTIONS", "STATES", "DERIVED_FROM"],
    },
    "fast_ingest": {
        "name": "Fast (No LLM)",
        "description": "Regex-only extraction without LLM calls. Fastest option — good "
                       "for bulk imports where speed matters more than extraction depth.",
        "input_types": ["url", "html", "text", "file", "csv"],
        "stages": [
            "clean",
            "chunk:paragraph",      # flat paragraph splitting, no section detection
            "regex_extract",
            "link:graph",
            "validate",
        ],
        "chunk_strategy": "paragraph",
        "llm_required": False,
        "produces": ["Document", "Passage", "Entity", "Fact"],
        "edge_types": ["CONTAINS", "NEXT", "MENTIONS"],
    },
}

# Legacy aliases — all route to smart_pipeline for backward compatibility
_PIPELINE_ALIASES = {
    "smart_article": "smart_pipeline",
    "smart_text": "smart_pipeline",
    "chat_history": "smart_pipeline",
    # website aliases
    "ir_page": "website",
    "webpage": "website",
}


def resolve_named_pipeline(pipeline_name: str) -> Optional[Dict]:
    """Resolve a named pipeline from PipelineStore and return its config.

    Returns dict with: schema_name, parser, filters, graph_config, default_params
    Or None if pipeline not found or is a built-in alias.
    """
    # Don't resolve built-in names
    if pipeline_name in PIPELINES or pipeline_name in _PIPELINE_ALIASES:
        return None
    try:
        from .pipeline_store import PipelineStore
        store = PipelineStore()
        pipelines = store.list_pipelines(include_builtins=True)
        for p in pipelines:
            if p.get("name") == pipeline_name or p.get("id") == pipeline_name:
                return {
                    "schema_name": p.get("schema_name", ""),
                    "parser": p.get("parser", ""),
                    "filters": p.get("filters", {}),
                    "graph_config": p.get("graph_config", {}),
                    "default_params": p.get("default_params", {}),
                }
    except Exception:
        pass
    return None


def _get_embed_fn() -> Optional[Callable]:
    """Get embedding callable for semantic filtering."""
    try:
        from ..context.vector_integration import get_session_vector_store
        svs = get_session_vector_store()
        if svs and svs.available and hasattr(svs, 'embed_text'):
            def embed_fn(text: str):
                return svs.embed_text(text)
            # Quick test
            test = embed_fn("test")
            if test and len(test) > 0:
                return embed_fn
    except Exception as e:
        logger.debug("Embed function not available: %s", e)
    return None


def _get_llm_fn(db=None) -> Optional[Callable]:
    """Get LLM callable — tries Ollama (free) first, then cloud."""
    try:
        from ..llm.client import get_llm_client
        client = get_llm_client()
        if client:
            def llm_fn(prompt: str) -> str:
                return client.generate(prompt=prompt, max_tokens=3000)
            return llm_fn
    except Exception as e:
        logger.warning("LLM client not available: %s", e)
    return None


def _extract_chunk(
    passage: PassageChunk,
    schema: IngestionSchema,
    llm_fn: Optional[Callable] = None,
    use_regex: bool = True,
    strategy: Optional[ContentStrategy] = None,
) -> ChunkExtraction:
    """Extract entities, facts, relationships from a single passage.

    Phase A: LLM extraction (local Ollama or cloud), with strategy hints
    Phase B: Regex supplement + strategy-specific fact patterns
    Merge results.
    """
    text = passage.content
    extraction = ChunkExtraction(chunk_index=passage.chunk_index)

    # Phase A: LLM extraction (with strategy hints appended to prompt)
    llm_nodes = []
    llm_edges = []
    if llm_fn:
        try:
            # Temporarily add strategy hints to schema for prompt building
            if strategy:
                original_hints = schema.field_hints.get("_strategy_hints", "")
                schema.field_hints["_strategy_hints"] = strategy.get_extraction_hints()
            result = llm_extract(text, schema, llm_fn=llm_fn)
            if strategy:
                schema.field_hints["_strategy_hints"] = original_hints  # restore
            if isinstance(result, tuple):
                llm_nodes, llm_edges = result
            else:
                llm_nodes = result
        except Exception as e:
            logger.warning("LLM extraction failed for chunk %d: %s", passage.chunk_index, e)

    # Phase B: Regex extraction
    regex_nodes = []
    if use_regex:
        try:
            regex_nodes = regex_extract(text, schema)
        except Exception as e:
            logger.warning("Regex extraction failed for chunk %d: %s", passage.chunk_index, e)

    # Phase C: Strategy-specific fact patterns (extract facts regex missed)
    strategy_facts = []
    if strategy:
        for pattern in strategy.get_fact_patterns():
            for match in pattern.findall(text):
                match = match.strip()
                if len(match) > 15:
                    strategy_facts.append({
                        "label": "Fact",
                        "properties": {
                            "name": match[:80],
                            "statement": match,
                            "confidence": 0.7,
                            "_extraction_method": "strategy_pattern",
                        },
                    })

    # Merge
    if llm_nodes and regex_nodes:
        merged_nodes = merge_extractions(regex_nodes, llm_nodes)
    elif llm_nodes:
        merged_nodes = llm_nodes
    else:
        merged_nodes = regex_nodes

    # Add strategy-extracted facts (dedup by statement + substring check)
    existing_statements = {n.get("properties", {}).get("statement", "").lower() for n in merged_nodes if n.get("label") == "Fact"}
    for sf in strategy_facts:
        stmt = sf["properties"].get("statement", "").lower()
        if not stmt:
            continue
        # Skip if exact duplicate or substring of an existing fact
        if stmt in existing_statements:
            continue
        is_substring = any(stmt in existing or existing in stmt for existing in existing_statements if existing)
        if is_substring:
            continue
        if stmt:
            merged_nodes.append(sf)
            existing_statements.add(stmt)

    # Separate entities and facts
    for node in merged_nodes:
        label = node.get("label", "")
        if label == "Fact":
            extraction.facts.append(node)
        else:
            extraction.entities.append(node)

    # Relationships from LLM + co-occurrence inference
    extraction.relationships = list(llm_edges) if llm_edges else []

    # Infer additional edges from co-occurrence in text (schema-driven)
    try:
        from .schema_extractor import infer_edges
        inferred = infer_edges(extraction.entities, text, schema)
        if inferred:
            # Dedup against LLM edges
            existing_keys = set()
            for rel in extraction.relationships:
                k = f"{rel.get('label', '')}:{rel.get('source_name', '').lower()}:{rel.get('target_name', '').lower()}"
                existing_keys.add(k)
            for edge in inferred:
                k = f"{edge['label']}:{edge['source_name'].lower()}:{edge['target_name'].lower()}"
                if k not in existing_keys:
                    extraction.relationships.append(edge)
    except Exception:
        pass

    return extraction


def _embed_nodes(result: BuildResult, db, namespace: str = ""):
    """Embed passages + entities into vector store."""
    try:
        from ..context.vector_integration import get_session_vector_store
        svs = get_session_vector_store()
        if not svs or not svs.available:
            logger.warning("Vector store or embedding service not available — skipping embedding")
            return

        ns = namespace or db.name
        embedded_count = 0

        # Embed passages
        for pid in result.passage_ids:
            node = db.csr_adapter.get_node(pid)
            if node:
                text = node.properties.get("content", "")
                if text:
                    try:
                        ok = svs.add_text(ns, text, pid, metadata={
                            "label": "Passage",
                            "name": node.properties.get("name", ""),
                            "chunk_index": node.properties.get("chunk_index", 0),
                            "node_id": pid,
                        })
                        if ok:
                            node.properties["_embedded"] = True
                            db.csr_adapter.update_node_properties(pid, node.properties)
                            embedded_count += 1
                    except Exception as e:
                        logger.warning("Failed to embed passage %s: %s", pid[:12], e)

        # Embed entities
        for key, eid in result.entity_ids.items():
            node = db.csr_adapter.get_node(eid)
            if node:
                name = node.properties.get("name", "")
                desc = node.properties.get("description", node.properties.get("role", ""))
                text = f"{name}. {desc}" if desc else name
                if text:
                    try:
                        ok = svs.add_text(ns, text, eid, metadata={
                            "label": node.label if hasattr(node, 'label') else node.node_type,
                            "name": name,
                            "node_id": eid,
                        })
                        if ok:
                            node.properties["_embedded"] = True
                            db.csr_adapter.update_node_properties(eid, node.properties)
                            embedded_count += 1
                    except Exception as e:
                        logger.warning("Failed to embed entity %s: %s", eid[:12], e)

        logger.info("Embedded %d/%d nodes", embedded_count,
                     len(result.passage_ids) + len(result.entity_ids))

    except Exception as e:
        logger.warning("Embedding stage failed: %s", e)


def _compute_semantic_neighbors(result: BuildResult, db, graph_name: str, limit: int = 5):
    """For each embedded node, find top-k semantic neighbors via Qdrant and store in LMDB."""
    try:
        from ..search.lmdb_index import get_lmdb_index
        from ..search.embedding_cache import get_embedding_cache
        from ..context.vector_integration import get_session_vector_store

        svs = get_session_vector_store()
        if not svs or not svs.available:
            return

        lmdb_idx = get_lmdb_index(graph_name)
        adapter = getattr(db, 'csr_adapter', None) or db
        cache = get_embedding_cache()
        stored = 0

        all_ids = list(result.passage_ids.values()) + list(result.entity_ids.values())
        for nid in all_ids:
            try:
                node = adapter.get_node(nid)
                if not node or not (node.properties or {}).get("_embedded"):
                    continue

                text = (node.properties or {}).get("content") or (node.properties or {}).get("statement") or (node.properties or {}).get("name", "")
                if not text or len(text) < 10:
                    continue

                # Search for semantic neighbors
                neighbors = svs.search(graph_name, text, k=limit + 1)
                neighbor_ids = [h["node_id"] for h in neighbors if h.get("node_id") != nid][:limit]

                if neighbor_ids:
                    lmdb_idx.store_neighbors(nid, neighbor_ids)
                    stored += 1
            except Exception:
                continue

        if stored:
            logger.info("[SEMANTIC] Stored neighbors for %d nodes in LMDB", stored)
    except Exception as e:
        logger.debug("[SEMANTIC] Neighbor computation failed: %s", e)


def _assemble_context_units(result, db, graph_name, schema_name: str = ""):
    """Incrementally add new facts to existing Context Units.

    On each ingestion:
    - If NO CUs exist: build from scratch (full graph clustering)
    - If CUs exist: add new facts to the nearest existing CU by keyword match
    - Full rebuild only happens via explicit backfill or when CU count = 0

    This keeps ingestion fast (<100ms for CU step) instead of re-clustering
    all facts on every document (which takes 18s+ on large graphs).
    """
    try:
        from ..context.context_units import cluster_facts_for_cus, build_context_unit, _tokenize
        from ..search.lmdb_index import get_lmdb_index
        from ..core.hybrid_graph_storage import GraphNode, GraphEdge
        import uuid as _uuid

        # Resolve domain-specific CU config from schema (if available)
        cu_config = None
        if schema_name:
            try:
                from ..extraction.schema_loader import load_schema as _load_extraction_schema
                from pathlib import Path as _Path
                _schema_path = _Path(__file__).parent.parent / "config" / "schemas" / f"{schema_name}.yaml"
                if _schema_path.exists():
                    _ext_schema = _load_extraction_schema(str(_schema_path))
                    cu_config = _ext_schema.context_unit
            except Exception:
                pass

        lmdb_idx = get_lmdb_index(graph_name)

        # Determine which node labels to include in CU clustering.
        # Default: only Fact nodes. With domain schema: also priority_labels types.
        cu_labels = {"Fact"}
        if cu_config is not None:
            priority = getattr(cu_config, "priority_labels", [])
            if priority:
                cu_labels.update(priority)

        # Load new claimable nodes from this batch (facts + domain entities)
        adapter = getattr(db, 'csr_adapter', None) or db
        new_facts = []

        # From fact_ids
        fact_id_values = result.fact_ids if isinstance(result.fact_ids, dict) else {i: v for i, v in enumerate(result.fact_ids)}
        for fid in fact_id_values.values():
            try:
                node = adapter.get_node(fid)
                if node:
                    props = node.properties or {}
                    if props.get("statement") or props.get("name") or props.get("description"):
                        new_facts.append({"id": fid, "label": "Fact", "properties": props})
            except Exception:
                pass

        # From entity_ids — include domain entities that match priority_labels
        if len(cu_labels) > 1:  # has domain labels beyond just "Fact"
            for eid in result.entity_ids.values():
                try:
                    node = adapter.get_node(eid)
                    if node:
                        label = getattr(node, "node_type", None) or getattr(node, "label", "")
                        if label in cu_labels:
                            props = node.properties or {}
                            # Normalize: use name/description as statement for clustering
                            if not props.get("statement"):
                                desc = props.get("description", "")
                                name = props.get("name", "")
                                props = dict(props)  # copy
                                props["statement"] = f"{name}: {desc}" if desc else name
                            new_facts.append({"id": eid, "label": label, "properties": props})
                except Exception:
                    pass

        logger.info("[CU] Domain labels: %s | New claimable nodes: %d", cu_labels, len(new_facts))

        # Check if CUs already exist
        existing_cus = list(db.get_all_nodes(label="ContextUnit"))

        # Skip only if no new nodes AND CUs already exist (nothing to do)
        if len(new_facts) < 1 and existing_cus:
            return
        # If no new nodes but also no CUs, fall through to full graph scan below

        if not existing_cus:
            # No CUs yet — build from scratch using ALL eligible nodes in the graph
            all_facts = []
            for label in cu_labels:
                for node in db.get_all_nodes(label=label):
                    props = node.properties if hasattr(node, 'properties') else {}
                    if props.get("statement") or props.get("name") or props.get("description"):
                        p = dict(props)
                        if not p.get("statement"):
                            desc = p.get("description", "")
                            name = p.get("name", "")
                            p["statement"] = f"{name}: {desc}" if desc else name
                        all_facts.append({"id": node.id, "label": label, "properties": p})

            logger.info("[CU] Full build: %d eligible nodes across labels %s", len(all_facts), cu_labels)
            if len(all_facts) < 3:
                return

            clusters = cluster_facts_for_cus(all_facts, min_cluster_size=3, db=db)
            logger.info("[CU] Clustered into %d groups", len(clusters))
            for cluster in clusters:
                cu = build_context_unit(topic=cluster["topic"], facts=cluster["facts"], entities=[], use_llm=False, cu_config=cu_config)
                db.add_node(GraphNode(id=cu["id"], label="ContextUnit", properties=cu["properties"]))
                for fid in cu["evidence_ids"]:
                    db.add_edge(GraphEdge(id=str(_uuid.uuid4()), source=cu["id"], target=fid, label="HAS_EVIDENCE", properties={}))
                lmdb_idx.index_node(cu["id"], "ContextUnit", cu["properties"])

            if clusters:
                logger.info("[CU] Initial build: %d CUs from %d facts", len(clusters), len(all_facts))
        else:
            # CUs exist — assign new facts to the nearest CU by keyword overlap
            # Build CU topic → CU mapping
            cu_topics = {}
            for cu_node in existing_cus:
                props = cu_node.properties if hasattr(cu_node, 'properties') else {}
                topic = props.get("topic", "")
                cu_id = cu_node.id if hasattr(cu_node, 'id') else ""
                if topic and cu_id:
                    cu_topics[cu_id] = set(_tokenize(topic))

            assigned = 0
            for fact in new_facts:
                stmt = fact["properties"].get("statement", "")
                fact_tokens = set(_tokenize(stmt))
                if not fact_tokens:
                    continue

                # Find best matching CU by keyword overlap
                best_cu = None
                best_score = 0
                for cu_id, topic_tokens in cu_topics.items():
                    score = len(fact_tokens & topic_tokens)
                    if score > best_score:
                        best_score = score
                        best_cu = cu_id

                if best_cu and best_score > 0:
                    # Check for duplicate edge before adding
                    already_linked = False
                    try:
                        existing_edges = adapter.get_edges_from(best_cu) if hasattr(adapter, 'get_edges_from') else []
                        for e in existing_edges:
                            if getattr(e, 'target', '') == fact["id"] and getattr(e, 'label', '') == "HAS_EVIDENCE":
                                already_linked = True
                                break
                    except Exception:
                        pass
                    if not already_linked:
                        db.add_edge(GraphEdge(
                            id=str(_uuid.uuid4()), source=best_cu, target=fact["id"],
                            label="HAS_EVIDENCE", properties={},
                        ))
                        assigned += 1

            if assigned:
                logger.info("[CU] Incremental: assigned %d new facts to existing CUs", assigned)

            # Trigger full rebuild if too many facts have been added incrementally
            # Track via a counter on the graph metadata
            try:
                import redis as _redis_mod
                import os as _os
                _r = _redis_mod.from_url(_os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379"))
                counter_key = f"cu_incremental_count:{graph_name}"
                current = _r.incrby(counter_key, len(new_facts))
                rebuild_threshold = 50  # Full re-cluster after 50 new facts

                if current >= rebuild_threshold:
                    _r.set(counter_key, 0)
                    logger.info("[CU] Threshold reached (%d facts since last rebuild) — triggering full re-cluster", current)

                    # Delete old CUs and their HAS_EVIDENCE edges
                    try:
                        old_cu_ids = [cu.id for cu in existing_cus if hasattr(cu, 'id')]
                        for old_id in old_cu_ids:
                            try:
                                adapter.remove_node(old_id)
                            except Exception:
                                pass
                        logger.info("[CU] Deleted %d old CUs before rebuild", len(old_cu_ids))
                    except Exception:
                        pass

                    # Full rebuild — include domain entity types if schema has priority_labels
                    all_facts = []
                    for _lbl in cu_labels:
                        for node in db.get_all_nodes(label=_lbl):
                            props = node.properties if hasattr(node, 'properties') else {}
                            if props.get("statement") or props.get("name") or props.get("description"):
                                p = dict(props)
                                if not p.get("statement"):
                                    desc = p.get("description", "")
                                    name = p.get("name", "")
                                    p["statement"] = f"{name}: {desc}" if desc else name
                                all_facts.append({"id": node.id, "label": _lbl, "properties": p})

                    if len(all_facts) >= 3:
                        import threading
                        def _rebuild():
                            try:
                                clusters = cluster_facts_for_cus(all_facts, min_cluster_size=3, db=db)
                                for cluster in clusters:
                                    cu = build_context_unit(topic=cluster["topic"], facts=cluster["facts"], entities=[], use_llm=False, cu_config=cu_config)
                                    db.add_node(GraphNode(id=cu["id"], label="ContextUnit", properties=cu["properties"]))
                                    for fid in cu["evidence_ids"]:
                                        db.add_edge(GraphEdge(id=str(_uuid.uuid4()), source=cu["id"], target=fid, label="HAS_EVIDENCE", properties={}))
                                    lmdb_idx.index_node(cu["id"], "ContextUnit", cu["properties"])
                                logger.info("[CU] Full rebuild complete: %d CUs from %d facts", len(clusters), len(all_facts))
                            except Exception as _e:
                                logger.warning("[CU] Background rebuild failed: %s", _e)

                        threading.Thread(target=_rebuild, daemon=True, name="cu-rebuild").start()
            except Exception:
                pass  # Redis not available — skip threshold tracking

    except Exception as e:
        logger.debug("[CU] Assembly failed: %s", e)


def _index_bm25(result: BuildResult, db):
    """Update BM25 index (LMDB + legacy Whoosh) with new nodes."""
    # LMDB incremental update — index only new nodes, not full rebuild
    try:
        from ..search.lmdb_index import get_lmdb_index
        graph_name = getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or getattr(db, 'name', 'default')
        lmdb_idx = get_lmdb_index(graph_name)
        adapter = getattr(db, 'csr_adapter', None) or db
        batch = []
        for nid in list(result.passage_ids.values()) + list(result.entity_ids.values()) + list(result.fact_ids.values()):
            try:
                node = adapter.get_node(nid)
                if node:
                    batch.append({
                        "id": nid,
                        "label": getattr(node, 'label', getattr(node, 'node_type', '')),
                        "properties": node.properties or {},
                    })
            except Exception:
                pass
        if batch:
            lmdb_idx.index_batch(batch)

            # Store full passages
            for nid in result.passage_ids.values():
                try:
                    node = adapter.get_node(nid)
                    if node:
                        text = (node.properties or {}).get("content", "")
                        if text:
                            lmdb_idx.store_passage(nid, text)
                except Exception:
                    pass

            # Store edges for graph expansion
            all_nids = list(result.passage_ids.values()) + list(result.entity_ids.values()) + list(result.fact_ids.values())
            for nid in all_nids:
                try:
                    edges_out = adapter.get_edges_for_node(nid) if hasattr(adapter, 'get_edges_for_node') else []
                    edge_list = []
                    for e in edges_out[:20]:
                        target_id = e.target if hasattr(e, 'target') else e.get('target', '')
                        if target_id == nid:
                            target_id = e.source if hasattr(e, 'source') else e.get('source', '')
                        try:
                            target = adapter.get_node(target_id)
                            if target:
                                t_name = (target.properties or {}).get("name", "")
                                t_label = getattr(target, 'label', getattr(target, 'node_type', ''))
                                if t_name:
                                    edge_list.append({
                                        "target_id": target_id, "target_name": t_name,
                                        "target_type": t_label,
                                        "label": e.label if hasattr(e, 'label') else e.get('label', ''),
                                    })
                        except Exception:
                            pass
                    if edge_list:
                        lmdb_idx.store_edges(nid, edge_list)
                except Exception:
                    pass

            logger.info("[LMDB] Indexed %d nodes + passages + edges for %s", len(batch), graph_name)
    except Exception as e:
        logger.debug("[LMDB] Incremental index failed: %s", e)

    # Legacy Whoosh rebuild
    try:
        from ..core.graph_intelligence import build_indexes
        stats = build_indexes(db, namespace=db.name)
        logger.info("BM25 indexes rebuilt for %s: %s", db.name, stats)
    except Exception as e:
        logger.warning("BM25 indexing failed: %s", e)


def ingest_url(
    url: str,
    db,
    schema: Optional[IngestionSchema] = None,
    pipeline: str = "smart_article",
    mode: str = "auto",
    filter_config: Optional[FilterConfig] = None,
    strategy: Optional[str] = None,
    on_stage: Optional[Callable] = None,
    debug: bool = False,
    owner: str = "",
    context_purpose: str = "",
):
    """Ingest a web page URL into a rich knowledge graph.

    Runs all 7 stages: Clean -> Chunk -> Extract -> Link -> Embed -> Index -> Validate.

    Args:
        url: Web page URL to ingest.
        db: AIContextDB instance (target graph).
        schema: Optional IngestionSchema. If None, loads from graph's ContextMeta.
        pipeline: Pipeline name (smart_article, smart_text, fast_ingest).
        mode: "auto" (commit immediately) or "review" (return StagingResult for preview).
        filter_config: Optional FilterConfig for content filtering.
        on_stage: Optional callback(stage_name, details) for progress tracking.

    Returns:
        mode="auto": BuildResult with all created node/edge IDs.
        mode="review": StagingResult with preview (not committed).
    """
    if not schema:
        schema = load_schema(db)

    llm_fn = _get_llm_fn(db) if pipeline != "fast_ingest" else None

    # Stage 1: Clean
    if on_stage:
        on_stage("clean", {"url": url, "message": f"Fetching and cleaning {url}"})
    clean_doc = clean_webpage(url)
    if not clean_doc.body:
        if on_stage:
            on_stage("clean_failed", {"message": f"No content extracted from {url}"})
        return BuildResult(errors=[f"No content extracted from {url}"])
    if on_stage:
        on_stage("clean_done", {"message": f"Extracted: '{clean_doc.title}' ({clean_doc.word_count} words, {len(clean_doc.links)} links)"})

    # Stage 0: Content Fingerprint Dedup (Layer 1) — reject exact duplicates early
    try:
        fp = _get_content_fingerprint()
        dedup_result = fp.check(clean_doc.body, url=url)
        if dedup_result.status == "exact_duplicate":
            if on_stage:
                on_stage("dedup_rejected", {"status": "exact_duplicate", "url": url})
            logger.info("[DEDUP] Rejected exact duplicate: %s", url)
            return BuildResult(errors=[f"Duplicate content (already ingested)"])
        elif dedup_result.status == "content_changed":
            if on_stage:
                on_stage("dedup_versioned", {"status": "content_changed", "url": url})
            logger.info("[DEDUP] Content changed for URL %s — versioning", url)
    except Exception as exc:
        logger.warning("[DEDUP] Fingerprint check failed (non-fatal): %s", exc)

    # Auto-classify sensitivity
    from ..security.auto_tagger import AutoTagger
    _tagger = AutoTagger()
    _classification = _tagger.classify_text(clean_doc.body[:3000])
    sensitivity = _classification.sensitivity  # "public" | "internal" | "confidential" | "restricted"

    # Structural pre-filter (cheap, catches CSV/code/JSON mismatches before LLM)
    if filter_config:
        struct_result = structural_pre_filter(clean_doc.body[:2000], filter_config, schema=schema)
        if struct_result and struct_result.status == "filtered":
            if on_stage:
                on_stage("filter_structural", {"status": "filtered", "reason": struct_result.reason})
            return BuildResult(errors=[f"Filtered (structural): {struct_result.reason}"])

    # Filter chain (if configured)
    if filter_config:
        if on_stage:
            on_stage("filter", {"config": "active"})
        debug_log = [] if debug else None
        f_result = filter_chain(clean_doc.title, clean_doc.body, filter_config,
                                llm_fn=llm_fn, embed_fn=_get_embed_fn(), debug_log=debug_log)
        if debug_log and on_stage:
            on_stage("filter_debug", {"filter_decisions": debug_log, "result": f_result.status, "reason": f_result.reason})
        if f_result.status == "filtered":
            logger.info("Filtered: %s — %s", clean_doc.title, f_result.reason)
            if mode == "review":
                staging = StagingResult(
                    staging_id=str(_uuid.uuid4()),
                    filter_log=[{"url": url, "title": clean_doc.title, "status": "filtered", "reason": f_result.reason}],
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                _staging_store[staging.staging_id] = staging
                return staging
            return BuildResult(errors=[f"Filtered: {f_result.reason}"])
        if f_result.status == "review" and mode == "auto":
            mode = "review"  # force review for borderline content

    # Resolve content strategy early (needed for chunk method + extraction hints)
    # Priority: explicit strategy arg → pipeline's content_strategy → news_article default
    if strategy:
        content_strategy = get_strategy(strategy)
    else:
        _pipeline_def = PIPELINES.get(pipeline) or PIPELINES.get(_PIPELINE_ALIASES.get(pipeline, ""), {})
        _strategy_name = (_pipeline_def.get("content_strategy", "news_article") if _pipeline_def else "news_article")
        content_strategy = get_strategy(_strategy_name)
    chunk_method = content_strategy.chunk_config.method

    # Stage 2: Chunk
    if on_stage:
        on_stage("chunk", {
            "message": f"Chunking {clean_doc.word_count} words into passages",
            "chunk_strategy": chunk_method,
            "content_strategy": content_strategy.name,
            "token_limits": f"min={content_strategy.chunk_config.min_tokens} max={content_strategy.chunk_config.max_tokens} target={content_strategy.chunk_config.target_tokens}",
        })
    link_dicts = [{"url": l.url, "anchor": l.anchor} for l in clean_doc.links]
    cc = content_strategy.chunk_config
    chunks = chunk_document(
        clean_doc.body,
        links=link_dicts,
        method=chunk_method,
        overlap_sentences=cc.overlap_sentences,
        min_tokens=cc.min_tokens,
        max_tokens=cc.max_tokens,
        target_tokens=cc.target_tokens,
    )
    if not chunks:
        if on_stage:
            on_stage("chunk_failed", {"message": "No chunks produced — content may be too short or empty"})
        return BuildResult(errors=["No chunks produced"])
    avg_chars = sum(len(getattr(c, 'content', None) or getattr(c, 'body', '') or '') for c in chunks) // len(chunks)
    if on_stage:
        on_stage("chunk_done", {"message": f"Produced {len(chunks)} chunks (avg {avg_chars} chars each)",
                                "chunk_count": len(chunks), "avg_chars": avg_chars})
    logger.info("Chunked: %d passages (avg %d chars)", len(chunks), avg_chars)

    # Stage 2b: Semantic Dedup (Layer 2) — catch rewritten duplicates via embedding similarity
    try:
        _embed_fn_dedup = _get_embed_fn()
        if _embed_fn_dedup and chunks:
            sd = _get_semantic_dedup()
            first_chunk_text = chunks[0].content if hasattr(chunks[0], 'content') else chunks[0].body
            first_embedding = _embed_fn_dedup(first_chunk_text[:500])
            sem_result = sd.check(first_embedding, text=first_chunk_text)
            if sem_result.status == "semantic_duplicate":
                if on_stage:
                    on_stage("dedup_semantic", {"status": "semantic_duplicate", "similarity": sem_result.similarity})
                logger.info("[DEDUP] Semantic duplicate detected (similarity=%.2f) — rejecting", sem_result.similarity)
                return BuildResult(errors=[f"Semantic duplicate (similarity={sem_result.similarity:.2f})"])
            elif sem_result.status == "factual_update":
                # Numbers changed — this is a correction/update, not a duplicate
                # Proceed with ingestion (time-travel will version it)
                if on_stage:
                    on_stage("dedup_factual_update", {"status": "factual_update", "similarity": sem_result.similarity, "numbers_changed": True})
                logger.info("[DEDUP] Factual update detected (similarity=%.2f, numbers changed) — versioning", sem_result.similarity)
            elif sem_result.status == "related_coverage":
                if on_stage:
                    on_stage("dedup_related", {"status": "related_coverage", "similarity": sem_result.similarity})
                logger.info("[DEDUP] Related coverage detected (similarity=%.2f) — proceeding with extraction", sem_result.similarity)
            # Register this content's embedding + text for future dedup
            sd.add(first_embedding, doc_id=url or clean_doc.title, text=first_chunk_text)
    except Exception as exc:
        logger.warning("[DEDUP] Semantic dedup failed (non-fatal): %s", exc)

    # Stage 3: Extract (per chunk)
    if on_stage:
        on_stage("extract", {"message": f"Extracting entities/facts from {len(chunks)} passages (LLM: {'yes' if llm_fn else 'no'}, strategy: {content_strategy.name})"})
    extractions = []
    for chunk in chunks:
        ext = _extract_chunk(chunk, schema, llm_fn=llm_fn, use_regex=True, strategy=content_strategy)
        extractions.append(ext)
    total_entities = sum(len(e.entities) for e in extractions)
    total_facts = sum(len(e.facts) for e in extractions)
    logger.info("Extracted: %d entities, %d facts across %d chunks", total_entities, total_facts, len(chunks))

    # Stage 3a: Quality Amplifier — score relevance against context purpose
    _embed_fn = _get_embed_fn()
    if context_purpose and _embed_fn:
        if on_stage:
            on_stage("amplify", {"message": f"Scoring {total_entities} entities + {total_facts} facts for relevance"})
        amp_result = amplify_extractions(
            extractions=extractions,
            context_purpose=context_purpose,
            embed_fn=_embed_fn,
        )
        if on_stage:
            on_stage("amplify_done", amp_result.stats)
        logger.info("[AMPLIFIER] %s", amp_result.stats)

    # Stage 3b: Intelligence Tagger — sentiment, geography, domain, category, impact
    try:
        _llm_fn_for_tags = llm_fn or _get_llm_fn()
        chunk_texts = [c.content if hasattr(c, 'content') else c.body for c in chunks]
        if on_stage:
            on_stage("intelligence_tag", {"message": f"Tagging {len(chunks)} chunks with intelligence dimensions"})
        tag_result = _tag_extractions(
            extractions=extractions,
            chunk_texts=chunk_texts,
            llm_fn=_llm_fn_for_tags,
        )
        _apply_intelligence_tags(extractions, tag_result, chunk_texts)
        if on_stage:
            on_stage("intelligence_tag_done", tag_result.stats)
        logger.info("[TAGGER] %s", tag_result.stats)
    except Exception as exc:
        logger.warning("[TAGGER] Intelligence tagging failed (non-fatal): %s", exc)

    # Stage 3c: Correlation Signals — fire on-ingest signals from templates
    try:
        from ..intelligence.correlation_engine import CorrelationEngine
        from ..intelligence.correlation_template import list_templates, load_template
        _corr_engine = CorrelationEngine()
        for tmpl_name in list_templates():
            tmpl = load_template(tmpl_name)
            if tmpl:
                _corr_engine.register_template_object(tmpl)

        # Build entity list for signal processing
        _signal_entities = []
        for ext in extractions:
            for ent in ext.entities:
                props = ent.get("properties", {})
                _signal_entities.append({
                    "name": props.get("name", ""),
                    "label": ent.get("label", "Entity"),
                    "_sentiment": props.get("_sentiment", "neutral"),
                })

        for tmpl_name in _corr_engine.list_registered():
            tmpl = _corr_engine.get_template(tmpl_name)
            if tmpl:
                signals = _corr_engine.process_ingest(_signal_entities, tmpl)
                if signals:
                    if on_stage:
                        on_stage("correlation_signals", {
                            "template": tmpl_name,
                            "signals_fired": len(signals),
                            "signals": [{"type": s.type, "entity": s.entity_name, "severity": s.severity} for s in signals[:5]],
                        })
                    logger.info("[CORR] %d signals fired from template '%s'", len(signals), tmpl_name)

                    # Stage 3d: Reactive — trigger pipeline actions from signals
                    try:
                        from ..intelligence.reactive import ReactiveController
                        _reactive = ReactiveController()
                        _reactive.add_default_rules()
                        graph_name = getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or 'default'
                        for sig in signals:
                            reactions = _reactive.react(sig, graph_name)
                            if reactions and on_stage:
                                on_stage("reactive", {
                                    "reactions": [r.to_dict() for r in reactions[:3]],
                                })
                    except Exception as react_exc:
                        logger.debug("[REACTIVE] Reaction processing failed: %s", react_exc)

    except Exception as exc:
        logger.warning("[CORR] Correlation signal processing failed (non-fatal): %s", exc)

    # REVIEW MODE: pause here and return preview
    if mode == "review":
        # Build preview data
        passage_previews = [
            {"content": c.content[:200], "token_count": c.token_count, "chunk_index": c.chunk_index,
             "_staging_id": f"p_{i}"}
            for i, c in enumerate(chunks)
        ]
        entity_previews = []
        fact_previews = []
        edge_previews = []
        sid = 0
        for ext in extractions:
            for e in ext.entities:
                e["_staging_id"] = f"e_{sid}"
                entity_previews.append({
                    "label": e.get("label", "Entity"),
                    "name": e.get("properties", {}).get("name", ""),
                    "properties": e.get("properties", {}),
                    "_staging_id": e["_staging_id"],
                })
                sid += 1
            for f in ext.facts:
                f["_staging_id"] = f"f_{sid}"
                fact_previews.append({
                    "statement": f.get("properties", f).get("statement", f.get("properties", f).get("name", "")),
                    "_staging_id": f["_staging_id"],
                })
                sid += 1
            for r in ext.relationships:
                edge_previews.append({
                    "label": r.get("label", "RELATED_TO"),
                    "source": r.get("source_name", ""),
                    "target": r.get("target_name", ""),
                })

        link_previews = [{"url": l.url, "anchor": l.anchor} for l in clean_doc.links] if clean_doc.links else []

        quality = _calc_quality(passage_previews, entity_previews, fact_previews, edge_previews)

        # Schema suggestions — discover types not in schema
        from .graph_builder import _suggest_schema_updates
        suggestions = _suggest_schema_updates(extractions, schema) if schema else []

        staging = StagingResult(
            staging_id=str(_uuid.uuid4()),
            clean_doc=clean_doc,
            chunks=chunks,
            extractions=extractions,
            passages=passage_previews,
            entities=entity_previews,
            facts=fact_previews,
            edges=edge_previews,
            links=link_previews,
            quality_score=quality,
            filter_log=[],
            schema_suggestions=[s.to_dict() for s in suggestions],
            schema_used=schema.name if schema else "default",
            pipeline=pipeline,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _staging_store[staging.staging_id] = staging
        logger.info("Staged for review: %s (quality: %.0f%%, %d entities, %d facts)",
                     staging.staging_id[:12], quality, len(entity_previews), len(fact_previews))
        return staging

    # Post-extraction filter (precise, uses structured entities)
    if filter_config and (filter_config.semantic_rules or schema):
        post_result = post_extraction_filter(extractions, filter_config, schema=schema, embed_fn=_get_embed_fn())
        if post_result and post_result.status == "filtered":
            if on_stage:
                on_stage("filter_post", {"status": "filtered", "reason": post_result.reason})
            return BuildResult(errors=[f"Filtered (post-extraction): {post_result.reason}"])

    # Stage 3b: ValidationGate — accept/quarantine/reject extracted entities
    _compiled_schema = None
    try:
        from ..schema.compiler import SchemaCompiler
        from ..schema.sdl import EnhancedSchema
        from ..schema.validation_gate import ValidationGate
        from pathlib import Path as _Path
        _schema_path = _Path(__file__).parent.parent / "config" / "schemas" / f"{schema.name}.yaml"
        if _schema_path.exists():
            import yaml
            with open(_schema_path) as _sf:
                _raw = yaml.safe_load(_sf)
            _enhanced = EnhancedSchema.from_dict(_raw)
            _compiled_schema = SchemaCompiler.compile(_enhanced)
            gate = ValidationGate(_compiled_schema)
            quarantined_count = 0
            rejected_count = 0
            for ext in extractions:
                accepted_entities = []
                for ent in ext.entities:
                    verdict = gate.validate_node(ent)
                    if verdict.verdict == "accept":
                        accepted_entities.append(ent)
                    elif verdict.verdict == "quarantine":
                        ent.setdefault("properties", {})["_quarantine_reason"] = verdict.reason
                        ent["properties"]["confidence"] = ent["properties"].get("confidence", 0.85) * 0.5
                        accepted_entities.append(ent)  # keep with penalty
                        quarantined_count += 1
                    else:
                        rejected_count += 1
                ext.entities = accepted_entities
            if quarantined_count or rejected_count:
                logger.info("[GATE] Validation: %d quarantined, %d rejected", quarantined_count, rejected_count)
                if on_stage:
                    on_stage("validate_gate", {"quarantined": quarantined_count, "rejected": rejected_count})
    except ImportError:
        pass  # schema package not available — skip gate
    except Exception as e:
        logger.debug("[GATE] Validation gate skipped: %s", e)

    # Stage 4: Link (build graph)
    if on_stage:
        on_stage("link", {"message": f"Building graph: {total_entities} entities, {total_facts} facts"})
    result = build_graph(clean_doc, chunks, extractions, db, schema, pipeline_name=pipeline,
                         owner=owner, sensitivity=sensitivity, compiled_schema=_compiled_schema)

    # Stage 5: Embed
    if on_stage:
        on_stage("embed", {"message": f"Embedding {len(result.passage_ids)} passages + {len(result.entity_ids)} entities into vector store"})
    _embed_nodes(result, db)

    # Stage 5b: Semantic neighbors (pre-compute for LMDB hybrid search)
    graph_name = getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or getattr(db, 'name', 'default')
    _compute_semantic_neighbors(result, db, graph_name)

    # Stage 6: Index
    if on_stage:
        on_stage("index", {"message": "Building BM25 keyword search index"})
    _index_bm25(result, db)
    _assemble_context_units(result, db, graph_name, schema_name=schema.name if schema else "")

    # Stage 6b: DerivationEngine — fire schema-defined rules against graph
    if _compiled_schema and _compiled_schema.derivation_rules:
        try:
            from ..schema.derivation import DerivationEngine
            engine = DerivationEngine(_compiled_schema.derivation_rules)
            actions = engine.evaluate(db)
            if actions:
                logger.info("[DERIVE] %d derivation actions fired", len(actions))
                if on_stage:
                    on_stage("derivation", {"actions": len(actions),
                             "types": [a["action"] for a in actions[:5]]})
                # Execute boost actions immediately (update projection priority)
                for action in actions:
                    if action["action"] == "boost":
                        nid = action.get("node_id", "")
                        priority = action.get("priority", 1.5)
                        if nid:
                            try:
                                node = db.csr_adapter.get_node(nid)
                                if node:
                                    node.properties["_derivation_boost"] = priority
                                    db.csr_adapter.update_node_properties(nid, node.properties)
                            except Exception:
                                pass
        except ImportError:
            pass
        except Exception as e:
            logger.debug("[DERIVE] Derivation skipped: %s", e)

    # Stage 7: Validate (legacy fallback — skipped if ValidationGate ran pre-build)
    if schema and _compiled_schema is None:
        if on_stage:
            on_stage("validate", {"message": "Validating nodes against schema + dedup check"})
        for key, eid in result.entity_ids.items():
            node = db.csr_adapter.get_node(eid)
            if node:
                node_dict = {"label": getattr(node, 'node_type', getattr(node, 'label', '')),
                             "properties": dict(node.properties)}
                validated = validate_and_flag(node_dict, schema)
                for vk in ("validation_status", "validation_errors"):
                    if vk in validated["properties"]:
                        node.properties[vk] = validated["properties"][vk]
                db.csr_adapter.update_node_properties(eid, node.properties)

    logger.info(
        "Ingestion complete: %s -> %d passages, %d entities, %d facts, %d links, %d edges",
        clean_doc.title[:40], len(result.passage_ids), len(result.entity_ids),
        len(result.fact_ids), len(result.link_ids), result.edge_count,
    )

    # Rebuild context manifest (non-blocking)
    try:
        from ..context.quality import EntityIndex, ManifestBuilder
        graph_name = getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or 'default'
        redis_client = None
        try:
            if hasattr(db, '_redis') and db._redis:
                redis_client = db._redis
            elif hasattr(db, 'csr_adapter') and hasattr(db.csr_adapter, '_redis'):
                redis_client = db.csr_adapter._redis
        except Exception:
            pass

        eidx = EntityIndex(redis_client=redis_client)
        builder = ManifestBuilder(entity_index=eidx, redis_client=redis_client)

        adapter = getattr(db, 'csr_adapter', None) or db
        qualities = []
        label_counts = {}
        for nid in list(result.entity_ids.values()) + list(result.fact_ids.values()) + list(result.passage_ids.values()):
            try:
                node = adapter.get_node(nid)
                if node:
                    q = (node.properties or {}).get("_quality", 50)
                    qualities.append(q)
                    lbl = getattr(node, 'label', getattr(node, 'node_type', ''))
                    label_counts[lbl] = label_counts.get(lbl, 0) + 1
            except Exception:
                pass

        context_id = graph_name
        builder.build(graph_name, context_id, node_qualities=qualities, label_counts=label_counts)
        try:
            from ..context.quality import ResultCache
            ResultCache(redis_client=redis_client).build(graph_name, adapter, eidx)
            logger.info("[QUALITY] Manifest + result cache rebuilt for %s (%d nodes)", graph_name, len(qualities))
        except Exception:
            logger.info("[QUALITY] Manifest rebuilt for %s (%d nodes)", graph_name, len(qualities))
    except Exception as e:
        logger.debug("[QUALITY] Manifest rebuild skipped: %s", e)

    return result


def ingest_chat_export(
    file_path: str,
    db,
    graph_name: str,
    on_stage: Optional[Callable] = None,
    max_conversations: int = 100,
):
    """Ingest a chat history export (ZIP or JSON) through the full pipeline.

    Pipeline stages:
    1. PARSE_CHAT — extract ZIP → detect source (ChatGPT/Claude/Gemini) → parse conversations
    2. BUILD_GRAPH — create Conversation/Message nodes + edges via connector
    3. EXTRACT — run entity/fact extraction on conversation text via smart pipeline
    4. INDEX — BM25 + vector indexing
    5. CONTEXT_UNITS — cluster facts into CUs

    Args:
        file_path: Path to ZIP or JSON file
        db: AIContextDB instance
        graph_name: Target graph namespace
        on_stage: Progress callback
        max_conversations: Max conversations to process
    """
    import zipfile
    import json as _json
    import os
    import tempfile

    if on_stage:
        on_stage("parse_chat", {"message": "Parsing chat export..."})

    # Step 1: PARSE — extract conversations.json from ZIP or read JSON directly
    conversations_path = file_path
    tmp_extracted = None

    if file_path.lower().endswith('.zip'):
        try:
            with zipfile.ZipFile(file_path) as zf:
                conv_files = [n for n in zf.namelist()
                              if 'conversations' in n.lower() and n.endswith('.json')]
                if not conv_files:
                    return BuildResult(errors=["No conversations.json found in ZIP"])
                fd, tmp_extracted = tempfile.mkstemp(suffix='.json', prefix='chat_')
                os.write(fd, zf.read(conv_files[0]))
                os.close(fd)
                conversations_path = tmp_extracted
                logger.info("[CHAT] Extracted %s from ZIP (%d bytes)", conv_files[0], os.path.getsize(tmp_extracted))
        except Exception as e:
            return BuildResult(errors=[f"ZIP extraction failed: {e}"])

    # Step 2: Detect source and create connector
    try:
        with open(conversations_path, 'r', encoding='utf-8') as f:
            data = _json.load(f)
    except Exception as e:
        if tmp_extracted:
            os.unlink(tmp_extracted)
        return BuildResult(errors=[f"JSON parse failed: {e}"])

    conversations = data if isinstance(data, list) else data.get("conversations", data.get("items", []))
    if not conversations:
        if tmp_extracted:
            os.unlink(tmp_extracted)
        return BuildResult(errors=["No conversations found in file"])

    # Auto-detect source
    sample = conversations[0] if conversations else {}
    if "mapping" in sample:
        source = "chatgpt"
    elif "chat_messages" in sample or "sender" in str(sample.get("messages", [{}])[0] if sample.get("messages") else {}):
        source = "claude"
    else:
        source = "chatgpt"  # default

    if on_stage:
        on_stage("parse_chat", {
            "message": f"Detected {source} export: {len(conversations)} conversations",
            "source": source,
            "conversations": len(conversations),
        })

    logger.info("[CHAT] Detected %s export: %d conversations", source, len(conversations))

    # Step 3: BUILD_GRAPH — use connector to create graph nodes
    if on_stage:
        on_stage("build_graph", {"message": f"Building graph from {source} conversations..."})

    from .connectors import get_ingestor
    ingestor = get_ingestor(source, {
        "export_file": conversations_path,
        "max_conversations": max_conversations,
    }, {})

    if not ingestor:
        if tmp_extracted:
            os.unlink(tmp_extracted)
        return BuildResult(errors=[f"No connector for source: {source}"])

    connector_result = ingestor.pull_data(graph_name)

    # Write connector nodes + edges to graph
    from ..core.hybrid_graph_storage import GraphNode, GraphEdge
    import uuid as _uuid

    for node in connector_result.nodes:
        nid = node.get("id", str(_uuid.uuid4()))
        label = node.get("label", "Document")
        props = node.get("properties", {})
        db.add_node(GraphNode(id=nid, label=label, properties=props))

    for edge in connector_result.edges:
        db.add_edge(GraphEdge(
            id=edge.get("id", str(_uuid.uuid4())),
            source=edge.get("source", ""), target=edge.get("target", ""),
            label=edge.get("label", "RELATED"), properties=edge.get("properties", {}),
        ))

    stats = connector_result.stats or {}
    logger.info("[CHAT] Built %d nodes, %d edges from %s",
                stats.get("nodes", len(connector_result.nodes)),
                len(connector_result.edges), source)

    if on_stage:
        on_stage("build_graph", {
            "message": f"Built {len(connector_result.nodes)} nodes from {source}",
            "nodes": len(connector_result.nodes),
            "edges": len(connector_result.edges),
        })

    # Step 4: EXTRACT — run entity/fact extraction on conversation text
    if connector_result.data_items:
        if on_stage:
            on_stage("extract", {"message": f"Extracting entities from {len(connector_result.data_items)} conversations..."})

        items_to_process = connector_result.data_items[:max_conversations]
        for i, item in enumerate(items_to_process):
            text = item.get("text", "")[:5000]  # cap per conversation
            if not text.strip():
                continue
            try:
                ingest_text(
                    text=text,
                    db=db,
                    title=item.get("metadata", {}).get("title", f"Conversation {i+1}"),
                    pipeline="smart_text",
                    on_stage=None,
                )
            except Exception as e:
                logger.debug("[CHAT] Extraction failed for conversation %d: %s", i, e)

            if on_stage and (i + 1) % 10 == 0:
                on_stage("extract", {"message": f"Processed {i+1}/{len(items_to_process)} conversations"})

    if on_stage:
        on_stage("index", {"message": "Indexing..."})

    # Step 5: INDEX + CU assembly is handled by ingest_text calls above

    # Cleanup
    if tmp_extracted:
        try:
            os.unlink(tmp_extracted)
        except Exception:
            pass

    if on_stage:
        on_stage("complete", {
            "message": f"Imported {len(conversations[:max_conversations])} {source} conversations",
            "source": source,
            "conversations": len(conversations[:max_conversations]),
            "nodes": len(connector_result.nodes),
        })

    logger.info("[CHAT] Pipeline complete: %d conversations, %d nodes", len(conversations[:max_conversations]), len(connector_result.nodes))
    return BuildResult()


def ingest_text(
    text: str,
    db,
    title: str = "",
    source_url: str = "",
    schema: Optional[IngestionSchema] = None,
    pipeline: str = "smart_text",
    mode: str = "auto",
    filter_config: Optional[FilterConfig] = None,
    strategy: Optional[str] = None,
    on_stage: Optional[Callable] = None,
    debug: bool = False,
    owner: str = "",
    context_purpose: str = "",
):
    """Ingest raw text into a rich knowledge graph.

    Same pipeline as ingest_url but skips HTML cleaning.

    Returns:
        mode="auto": BuildResult (committed)
        mode="review": StagingResult (preview, not committed)
    """
    # Resolve named pipeline config if provided
    _pipeline_config = resolve_named_pipeline(pipeline)
    if _pipeline_config:
        if _pipeline_config.get("schema_name") and not schema:
            try:
                from .schema_extractor import load_schema_by_name
                schema = load_schema_by_name(_pipeline_config["schema_name"], db)
            except Exception:
                pass
        logger.info("[PIPELINE] Using named pipeline: %s (schema=%s)", pipeline, _pipeline_config.get("schema_name", ""))

    if not schema:
        schema = load_schema(db)

    # Route to universal pipeline if schema has extraction: block
    try:
        from ..schema.sdl import EnhancedSchema
        from ..schema.compiler import SchemaCompiler
        from pathlib import Path as _Path
        _schema_path = _Path(__file__).parent.parent / "config" / "schemas" / f"{schema.name}.yaml"
        if _schema_path.exists():
            import yaml
            with open(_schema_path) as _sf:
                _raw = yaml.safe_load(_sf)
            _enhanced = EnhancedSchema.from_dict(_raw)
            _compiled = SchemaCompiler.compile(_enhanced)
            if _compiled.execution_plan:
                from .universal.stage_executor import StageExecutor
                from .universal.ingest_content import IngestContent
                from .universal._operator_registry import resolve_operators
                from .universal.parsers.turn_parser import TurnParser

                content = IngestContent(
                    content_type="conversation" if _compiled.execution_plan.parser == "turn" else "text",
                    text=text, title=title, source=source_url,
                )

                if _compiled.execution_plan.parser == "turn":
                    parser = TurnParser()
                    session_meta, turns = parser.parse(text)
                    chunks = parser.to_chunks(turns, conversation_title=title or "")
                    content.messages = [{"role": t.role, "content": t.content} for t in turns]
                else:
                    from .universal.ingest_content import Chunk
                    from .chunker import chunk_document as _chunk_document_universal
                    raw_chunks = _chunk_document_universal(text)
                    chunks = [Chunk(content=c.content, index=i) for i, c in enumerate(raw_chunks)]

                operators = resolve_operators(_compiled.execution_plan, _compiled)
                executor = StageExecutor(
                    operators=operators, db=db,
                    namespace=getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or getattr(db, 'name', 'default'),
                    compiled_schema=_compiled,
                )
                logger.info("[UNIVERSAL] Routed to universal pipeline: %s (%s, %d stages)",
                            schema.name, _compiled.execution_plan.parser, len(operators))
                return executor.execute(content, chunks)
    except ImportError as e:
        logger.warning("[UNIVERSAL] Import error during routing: %s", e)
    except Exception as e:
        logger.warning("[UNIVERSAL] Routing failed, using legacy pipeline: %s", e, exc_info=True)

    # Legacy pipeline below
    llm_fn = _get_llm_fn(db) if pipeline != "fast_ingest" else None

    clean_doc = clean_text(text, title=title, source_url=source_url)
    if not clean_doc.body:
        return BuildResult(errors=["Empty text"])

    # Stage 0: Content Fingerprint Dedup (Layer 1)
    try:
        fp = _get_content_fingerprint()
        dedup_result = fp.check(clean_doc.body, url=source_url)
        if dedup_result.status == "exact_duplicate":
            if on_stage:
                on_stage("dedup_rejected", {"status": "exact_duplicate"})
            logger.info("[DEDUP] Rejected exact duplicate content")
            return BuildResult(errors=["Duplicate content (already ingested)"])
        elif dedup_result.status == "content_changed":
            logger.info("[DEDUP] Content changed — versioning")
    except Exception as exc:
        logger.warning("[DEDUP] Fingerprint check failed (non-fatal): %s", exc)

    # Auto-classify sensitivity
    from ..security.auto_tagger import AutoTagger
    _tagger = AutoTagger()
    _classification = _tagger.classify_text(clean_doc.body[:3000])
    sensitivity = _classification.sensitivity

    # Structural pre-filter (cheap, catches CSV/code/JSON mismatches before LLM)
    if filter_config:
        struct_result = structural_pre_filter(clean_doc.body[:2000], filter_config, schema=schema)
        if struct_result and struct_result.status == "filtered":
            if on_stage:
                on_stage("filter_structural", {"status": "filtered", "reason": struct_result.reason})
            return BuildResult(errors=[f"Filtered (structural): {struct_result.reason}"])

    # Filter chain
    if filter_config:
        debug_log = [] if debug else None
        f_result = filter_chain(clean_doc.title, clean_doc.body, filter_config,
                                llm_fn=llm_fn, embed_fn=_get_embed_fn(), debug_log=debug_log)
        if debug_log and on_stage:
            on_stage("filter_debug", {"filter_decisions": debug_log, "result": f_result.status, "reason": f_result.reason})
        if f_result.status == "filtered":
            if mode == "review":
                staging = StagingResult(
                    staging_id=str(_uuid.uuid4()),
                    filter_log=[{"title": title, "status": "filtered", "reason": f_result.reason}],
                    created_at=datetime.now(timezone.utc).isoformat(),
                )
                _staging_store[staging.staging_id] = staging
                return staging
            return BuildResult(errors=[f"Filtered: {f_result.reason}"])
        if f_result.status == "review" and mode == "auto":
            mode = "review"

    link_dicts = [{"url": l.url, "anchor": l.anchor} for l in clean_doc.links]
    chunks = chunk_document(clean_doc.body, links=link_dicts)
    if not chunks:
        return BuildResult(errors=["No chunks produced"])

    # Stage 2b: Semantic Dedup (Layer 2)
    try:
        _embed_fn_dedup = _get_embed_fn()
        if _embed_fn_dedup and chunks:
            sd = _get_semantic_dedup()
            first_chunk_text = chunks[0].content if hasattr(chunks[0], 'content') else chunks[0].body
            first_embedding = _embed_fn_dedup(first_chunk_text[:500])
            sem_result = sd.check(first_embedding, text=first_chunk_text)
            if sem_result.status == "semantic_duplicate":
                if on_stage:
                    on_stage("dedup_semantic", {"status": "semantic_duplicate", "similarity": sem_result.similarity})
                logger.info("[DEDUP] Semantic duplicate (similarity=%.2f) — rejecting", sem_result.similarity)
                return BuildResult(errors=[f"Semantic duplicate (similarity={sem_result.similarity:.2f})"])
            elif sem_result.status == "factual_update":
                if on_stage:
                    on_stage("dedup_factual_update", {"status": "factual_update", "similarity": sem_result.similarity, "numbers_changed": True})
                logger.info("[DEDUP] Factual update (similarity=%.2f, numbers changed) — versioning", sem_result.similarity)
            elif sem_result.status == "related_coverage":
                logger.info("[DEDUP] Related coverage (similarity=%.2f) — proceeding", sem_result.similarity)
            sd.add(first_embedding, doc_id=source_url or title, text=first_chunk_text)
    except Exception as exc:
        logger.warning("[DEDUP] Semantic dedup failed (non-fatal): %s", exc)

    content_strategy = get_strategy(strategy) if strategy else _auto_detect_strategy(clean_doc.body)

    extractions = []
    for chunk in chunks:
        ext = _extract_chunk(chunk, schema, llm_fn=llm_fn, use_regex=True, strategy=content_strategy)
        extractions.append(ext)

    # Stage 3a: Quality Amplifier — score relevance against context purpose
    _embed_fn = _get_embed_fn()
    if context_purpose and _embed_fn:
        if on_stage:
            _te = sum(len(e.entities) for e in extractions)
            _tf = sum(len(e.facts) for e in extractions)
            on_stage("amplify", {"message": f"Scoring {_te} entities + {_tf} facts for relevance"})
        amp_result = amplify_extractions(
            extractions=extractions,
            context_purpose=context_purpose,
            embed_fn=_embed_fn,
        )
        if on_stage:
            on_stage("amplify_done", amp_result.stats)
        logger.info("[AMPLIFIER] %s", amp_result.stats)

    # Stage 3b: Intelligence Tagger — sentiment, geography, domain, category, impact
    try:
        _llm_fn_for_tags = llm_fn or _get_llm_fn()
        chunk_texts = [c.content if hasattr(c, 'content') else c.body for c in chunks]
        if on_stage:
            on_stage("intelligence_tag", {"message": f"Tagging {len(chunks)} chunks with intelligence dimensions"})
        tag_result = _tag_extractions(
            extractions=extractions,
            chunk_texts=chunk_texts,
            llm_fn=_llm_fn_for_tags,
        )
        _apply_intelligence_tags(extractions, tag_result, chunk_texts)
        if on_stage:
            on_stage("intelligence_tag_done", tag_result.stats)
        logger.info("[TAGGER] %s", tag_result.stats)
    except Exception as exc:
        logger.warning("[TAGGER] Intelligence tagging failed (non-fatal): %s", exc)

    # Stage 3c: Correlation Signals
    try:
        from ..intelligence.correlation_engine import CorrelationEngine
        from ..intelligence.correlation_template import list_templates, load_template
        _corr_engine = CorrelationEngine()
        for tmpl_name in list_templates():
            tmpl = load_template(tmpl_name)
            if tmpl:
                _corr_engine.register_template_object(tmpl)

        _signal_entities = []
        for ext in extractions:
            for ent in ext.entities:
                props = ent.get("properties", {})
                _signal_entities.append({
                    "name": props.get("name", ""),
                    "label": ent.get("label", "Entity"),
                    "_sentiment": props.get("_sentiment", "neutral"),
                })

        for tmpl_name in _corr_engine.list_registered():
            tmpl = _corr_engine.get_template(tmpl_name)
            if tmpl:
                signals = _corr_engine.process_ingest(_signal_entities, tmpl)
                if signals:
                    if on_stage:
                        on_stage("correlation_signals", {
                            "template": tmpl_name,
                            "signals_fired": len(signals),
                            "signals": [{"type": s.type, "entity": s.entity_name, "severity": s.severity} for s in signals[:5]],
                        })
                    logger.info("[CORR] %d signals fired from template '%s'", len(signals), tmpl_name)

                    # Reactive — trigger pipeline actions
                    try:
                        from ..intelligence.reactive import ReactiveController
                        _reactive = ReactiveController()
                        _reactive.add_default_rules()
                        graph_name = getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or 'default'
                        for sig in signals:
                            _reactive.react(sig, graph_name)
                    except Exception:
                        pass

    except Exception as exc:
        logger.warning("[CORR] Correlation signal processing failed (non-fatal): %s", exc)

    # Review mode: return preview
    if mode == "review":
        total_ents = sum(len(e.entities) for e in extractions)
        total_facts = sum(len(e.facts) for e in extractions)
        passage_previews = [{"content": c.content[:200], "token_count": c.token_count, "chunk_index": c.chunk_index} for c in chunks]
        entity_previews = []
        fact_previews = []
        edge_previews = []
        sid = 0
        for ext in extractions:
            for e in ext.entities:
                e["_staging_id"] = f"e_{sid}"
                entity_previews.append({"label": e.get("label", ""), "name": e.get("properties", {}).get("name", ""), "_staging_id": e["_staging_id"]})
                sid += 1
            for f in ext.facts:
                f["_staging_id"] = f"f_{sid}"
                fact_previews.append({"statement": f.get("properties", f).get("statement", ""), "_staging_id": f["_staging_id"]})
                sid += 1
            for r in ext.relationships:
                edge_previews.append({"label": r.get("label", ""), "source": r.get("source_name", ""), "target": r.get("target_name", "")})

        from .graph_builder import _suggest_schema_updates
        suggestions = _suggest_schema_updates(extractions, schema) if schema else []

        staging = StagingResult(
            staging_id=str(_uuid.uuid4()), clean_doc=clean_doc, chunks=chunks, extractions=extractions,
            passages=passage_previews, entities=entity_previews, facts=fact_previews,
            edges=edge_previews, links=[{"url": l.url, "anchor": l.anchor} for l in clean_doc.links] if clean_doc.links else [],
            quality_score=_calc_quality(passage_previews, entity_previews, fact_previews, edge_previews),
            schema_suggestions=[s.to_dict() for s in suggestions],
            schema_used=schema.name if schema else "default", pipeline=pipeline,
            created_at=datetime.now(timezone.utc).isoformat(),
        )
        _staging_store[staging.staging_id] = staging
        return staging

    # Post-extraction filter (precise, uses structured entities)
    if filter_config and (filter_config.semantic_rules or schema):
        post_result = post_extraction_filter(extractions, filter_config, schema=schema, embed_fn=_get_embed_fn())
        if post_result and post_result.status == "filtered":
            if on_stage:
                on_stage("filter_post", {"status": "filtered", "reason": post_result.reason})
            return BuildResult(errors=[f"Filtered (post-extraction): {post_result.reason}"])

    result = build_graph(clean_doc, chunks, extractions, db, schema, pipeline_name=pipeline,
                         owner=owner, sensitivity=sensitivity, compiled_schema=None)
    _embed_nodes(result, db)
    _graph_name = getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or getattr(db, 'name', 'default')
    _compute_semantic_neighbors(result, db, _graph_name)
    _index_bm25(result, db)
    _assemble_context_units(result, db, _graph_name, schema_name=schema.name if schema else "")

    # Rebuild context manifest (non-blocking)
    try:
        from ..context.quality import EntityIndex, ManifestBuilder
        graph_name = getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or 'default'
        redis_client = None
        try:
            if hasattr(db, '_redis') and db._redis:
                redis_client = db._redis
            elif hasattr(db, 'csr_adapter') and hasattr(db.csr_adapter, '_redis'):
                redis_client = db.csr_adapter._redis
        except Exception:
            pass

        eidx = EntityIndex(redis_client=redis_client)
        builder = ManifestBuilder(entity_index=eidx, redis_client=redis_client)

        adapter = getattr(db, 'csr_adapter', None) or db
        qualities = []
        label_counts = {}
        for nid in list(result.entity_ids.values()) + list(result.fact_ids.values()) + list(result.passage_ids.values()):
            try:
                node = adapter.get_node(nid)
                if node:
                    q = (node.properties or {}).get("_quality", 50)
                    qualities.append(q)
                    lbl = getattr(node, 'label', getattr(node, 'node_type', ''))
                    label_counts[lbl] = label_counts.get(lbl, 0) + 1
            except Exception:
                pass

        context_id = graph_name
        builder.build(graph_name, context_id, node_qualities=qualities, label_counts=label_counts)
        logger.info("[QUALITY] Manifest rebuilt for %s (%d nodes scored)", graph_name, len(qualities))
    except Exception as e:
        logger.debug("[QUALITY] Manifest rebuild skipped: %s", e)

    return result


def ingest_crawl(
    start_url: str,
    db,
    max_pages: int = 20,
    schema: Optional[IngestionSchema] = None,
    pipeline: str = "smart_article",
    mode: str = "auto",
    filter_config: Optional[FilterConfig] = None,
    strategy: Optional[str] = None,
    on_stage: Optional[Callable] = None,
    debug: bool = False,
    owner: str = "",
) -> Dict[str, Any]:
    """Crawl a website and ingest matching pages — PARALLEL version.

    Phase 1: Parallel fetch (ThreadPool) — discover all URLs + fetch HTML
    Phase 2: Parallel filter — run filter chain on all fetched pages
    Phase 3: Batch extract — ingest pages that passed filters
    """
    from .cleaner import clean_webpage
    from urllib.parse import urlparse, urljoin
    from concurrent.futures import ThreadPoolExecutor, as_completed
    import re
    import trafilatura
    import time

    if not schema:
        schema = load_schema(db)

    logger.info("[CRAWL] filter_config=%s, has_rules=%s",
                filter_config is not None,
                len(filter_config.semantic_rules) if filter_config else 0)

    domain = urlparse(start_url).netloc
    results = {"ingested": [], "filtered": [], "failed": [], "total_crawled": 0}
    embed_fn = _get_embed_fn()
    llm_fn = _get_llm_fn(db) if pipeline != "fast_ingest" else None

    def _extract_links_from_html(html: str, base_url: str) -> List[str]:
        links = set()
        for match in re.finditer(r'href=["\']([^"\']+)["\']', html):
            href = match.group(1)
            if href.startswith(('#', 'javascript:', 'mailto:')):
                continue
            full = urljoin(base_url, href)
            if urlparse(full).netloc == domain:
                clean = full.split('#')[0].split('?')[0].rstrip('/')
                links.add(clean)
        return list(links)

    # ── Phase 1: Discover URLs (BFS) + parallel fetch ─────────────
    if on_stage:
        on_stage("crawl_discover", {"message": f"Discovering pages from {domain} (max {max_pages})"})

    visited = set()
    queue = [start_url]
    urls_to_fetch = []

    # BFS to discover URLs (quick — just fetches HTML headers/links)
    while queue and len(visited) < max_pages:
        url = queue.pop(0)
        if url in visited:
            continue
        visited.add(url)
        urls_to_fetch.append(url)

        # Quick fetch just for link discovery (first page only for seed URLs)
        if len(visited) <= 3:  # only deep-crawl first few pages for more links
            try:
                raw = trafilatura.fetch_url(url)
                if raw:
                    for link in _extract_links_from_html(raw, url):
                        if link not in visited:
                            queue.append(link)
            except Exception:
                pass

    # ── URL-level dedup: skip URLs already ingested into this graph ───
    _already_ingested = set()
    try:
        import os as _os
        import redis as _redis_dedup
        _rurl = _os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
        if _rurl:
            _rconn = _redis_dedup.from_url(_rurl)
            _graph_name = getattr(db, 'namespace', '') or getattr(db, '_namespace', '') or getattr(db, 'name', '')
            _dedup_key = f"contextcore:ingested_urls:{_graph_name}"
            _already_ingested = {u.decode() if isinstance(u, bytes) else u for u in _rconn.smembers(_dedup_key)}
    except Exception:
        pass

    # Only dedup article URLs (with unique slugs), not listing/index pages
    # Listing pages (short paths like /india, /technology) get new content daily
    def _is_article_url(url: str) -> bool:
        from urllib.parse import urlparse
        path = urlparse(url).path.rstrip("/")
        # Short paths (0-2 segments) are likely listing/index pages
        segments = [s for s in path.split("/") if s]
        if len(segments) <= 2:
            return False
        # URLs with numbers/IDs are likely articles
        if any(c.isdigit() for c in path):
            return True
        # Long slugs are likely articles
        if len(path) > 40:
            return True
        return False

    before_dedup = len(urls_to_fetch)
    if _already_ingested:
        # Skip article URLs already ingested; always re-crawl listing pages (they get new content)
        urls_to_fetch = [u for u in urls_to_fetch if not (_is_article_url(u) and u in _already_ingested)]
        skipped = before_dedup - len(urls_to_fetch)
        if skipped:
            logger.info("[CRAWL] URL dedup: skipped %d already-ingested article URLs (listing pages re-crawled)", skipped)

    if on_stage:
        on_stage("crawl_fetch", {"message": f"Fetching {len(urls_to_fetch)} pages in parallel"})

    if not urls_to_fetch:
        if on_stage:
            on_stage("crawl_done", {"message": "All URLs already ingested — nothing new to crawl"})
        return {"total_crawled": 0, "ingested": 0, "filtered": 0, "failed": [], "nodes_created": 0, "edges_created": 0}

    # ── Phase 2: Parallel fetch + clean ───────────────────────────
    t0 = time.time()
    fetched_pages = []  # (url, CleanDocument)

    def _fetch_one(url):
        try:
            raw = trafilatura.fetch_url(url)
            if not raw:
                return url, None, "fetch failed"
            # Also discover more links from this page
            new_links = _extract_links_from_html(raw, url)
            doc = clean_webpage(raw, source_url=url)
            if not doc.body or doc.word_count < 20:
                return url, None, "no content"
            return url, doc, new_links
        except Exception as e:
            return url, None, str(e)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(_fetch_one, url): url for url in urls_to_fetch}
        for future in as_completed(futures):
            url, doc, extra = future.result()
            results["total_crawled"] += 1
            if doc is None:
                results["failed"].append({"url": url, "reason": extra if isinstance(extra, str) else "unknown"})
            else:
                fetched_pages.append((url, doc))
                # Add discovered links for more pages
                if isinstance(extra, list) and len(visited) < max_pages:
                    for link in extra[:10]:
                        if link not in visited:
                            visited.add(link)
                            # Quick fetch in background if we need more pages
                            if len(fetched_pages) + len(results["failed"]) < max_pages:
                                try:
                                    r = trafilatura.fetch_url(link)
                                    if r:
                                        d = clean_webpage(r, source_url=link)
                                        if d and d.body and d.word_count >= 20:
                                            fetched_pages.append((link, d))
                                            results["total_crawled"] += 1
                                except Exception:
                                    pass

    fetch_time = time.time() - t0
    if on_stage:
        on_stage("crawl_fetched", {"message": f"Fetched {len(fetched_pages)} pages in {fetch_time:.1f}s ({len(results['failed'])} failed)"})

    # ── Phase 3: Filter all pages ─────────────────────────────────
    if filter_config and fetched_pages:
        if on_stage:
            on_stage("crawl_filter", {"message": f"Filtering {len(fetched_pages)} pages"})

        passed_pages = []
        for url, doc in fetched_pages:
            f_result = filter_chain(doc.title, doc.body, filter_config,
                                    llm_fn=llm_fn, embed_fn=embed_fn)
            if f_result.status == "filtered":
                results["filtered"].append({
                    "url": url, "title": doc.title,
                    "reason": f_result.reason, "scores": f_result.scores,
                })
            else:
                passed_pages.append((url, doc))

        if on_stage:
            on_stage("crawl_filtered", {"message": f"{len(passed_pages)} passed, {len(results['filtered'])} filtered"})
    else:
        passed_pages = fetched_pages

    # ── Phase 4: Chunk + Extract all pages (batch) ─────────────────
    if on_stage:
        on_stage("crawl_chunk", {"message": f"Chunking {len(passed_pages)} pages"})

    content_strategy = get_strategy(strategy) if strategy else get_strategy("news_article")
    all_page_data = []  # [(url, doc, chunks, extractions)]

    # Chunk all pages (fast, no LLM)
    for url, doc in passed_pages:
        link_dicts = [{"url": l.url, "anchor": l.anchor} for l in doc.links] if doc.links else []
        chunks = chunk_document(doc.body, links=link_dicts)
        if chunks:
            all_page_data.append((url, doc, chunks, []))

    skipped_pages = len(passed_pages) - len(all_page_data)
    total_chunks = sum(len(d[2]) for d in all_page_data)
    if on_stage:
        on_stage("crawl_chunk_done", {
            "message": (
                f"Chunked {len(all_page_data)} pages → {total_chunks} chunks"
                + (f" ({skipped_pages} pages skipped — empty content)" if skipped_pages else "")
            ),
            "pages_chunked": len(all_page_data),
            "pages_skipped": skipped_pages,
            "total_chunks": total_chunks,
        })
        on_stage("crawl_extract", {"message": f"Extracting entities from {total_chunks} chunks across {len(all_page_data)} pages (LLM: {'yes' if llm_fn else 'no'})"})

    # Extract all chunks (LLM calls — this is the slow part)
    for i, (url, doc, chunks, _) in enumerate(all_page_data):
        if on_stage:
            on_stage("extract_page", {"message": f"[{i+1}/{len(all_page_data)}] Extracting: {doc.title[:40]} ({len(chunks)} chunks)"})
        page_extractions = []
        for chunk in chunks:
            ext = _extract_chunk(chunk, schema, llm_fn=llm_fn, use_regex=True, strategy=content_strategy)
            page_extractions.append(ext)
        all_page_data[i] = (url, doc, chunks, page_extractions)

        # Log extraction results per page
        page_ents = sum(len(e.entities) for e in page_extractions)
        page_facts = sum(len(e.facts) for e in page_extractions)
        page_rels = sum(len(e.relationships) for e in page_extractions)
        ent_names = []
        for ext in page_extractions:
            for e in ext.entities:
                name = e.get("properties", {}).get("name", "")
                label = e.get("label", "")
                if name:
                    ent_names.append(f"{label}:{name}")
        rel_descs = []
        for ext in page_extractions:
            for r in ext.relationships:
                rel_descs.append(f"{r.get('source_name','')} --{r.get('label','')}--> {r.get('target_name','')}")

        if on_stage:
            on_stage("extract_result", {
                "message": f"  Found {page_ents} entities, {page_facts} facts, {page_rels} relationships",
                "entities": ent_names[:15],
                "relationships": rel_descs[:10],
            })

    # ── Phase 5: Build graphs + embed + index (batch) ─────────────
    if on_stage:
        on_stage("crawl_build", {"message": f"Building graph for {len(all_page_data)} pages"})

    from .graph_builder import build_graph
    logger.info("[CRAWL_BUILD] db type: %s, has add_node: %s, has csr_adapter: %s",
                type(db).__name__, hasattr(db, 'add_node'), hasattr(db, 'csr_adapter'))

    # Accumulate node IDs across all pages for LMDB indexing
    _all_passage_ids: dict = {}
    _all_entity_ids: dict = {}
    _all_fact_ids: dict = {}

    for i, (url, doc, chunks, extractions) in enumerate(all_page_data):
        try:
            if on_stage:
                on_stage("build_page", {"message": f"[{i+1}/{len(all_page_data)}] Linking: {doc.title[:40]}"})

            page_result = build_graph(doc, chunks, extractions, db, schema, pipeline_name=pipeline)
            _all_passage_ids.update(page_result.passage_ids or {})
            _all_entity_ids.update(page_result.entity_ids or {})
            _all_fact_ids.update(page_result.fact_ids or {})
            logger.info("[CRAWL_BUILD] Page '%s': doc_id=%s, passages=%d, entities=%d, facts=%d, edges=%d",
                        doc.title[:30], page_result.document_id, len(page_result.passage_ids),
                        len(page_result.entity_ids), len(page_result.fact_ids), page_result.edge_count)

            if page_result.document_id:
                # Collect entity names for logging
                ent_detail = []
                for key, eid in list(page_result.entity_ids.items())[:10]:
                    node = db.csr_adapter.get_node(eid)
                    if node:
                        ent_detail.append(f"[{getattr(node, 'node_type', getattr(node, 'label', ''))}] {node.properties.get('name', '')}")

                results["ingested"].append({
                    "url": url, "title": doc.title,
                    "document_id": page_result.document_id,
                    "passages": len(page_result.passage_ids),
                    "entities": len(page_result.entity_ids),
                    "facts": len(page_result.fact_ids),
                    "edges": page_result.edge_count,
                    "entity_names": ent_detail,
                })
                if on_stage:
                    on_stage("build_result", {
                        "message": f"  Created: {len(page_result.passage_ids)} passages, {len(page_result.entity_ids)} entities, {len(page_result.fact_ids)} facts, {page_result.edge_count} edges",
                        "entities": ent_detail,
                    })
            else:
                results["failed"].append({"url": url, "reason": "build returned no document_id"})
                logger.warning("[CRAWL_BUILD] No document_id for '%s' — build_graph returned empty result", url[:60])
                if on_stage:
                    on_stage("build_error", {"message": f"  [WARN] No document created for: {doc.title[:50]}"})
        except Exception as e:
            results["failed"].append({"url": url, "reason": str(e)})
            logger.error("[CRAWL_BUILD] Exception building graph for '%s': %s", url[:60], e, exc_info=True)
            if on_stage:
                on_stage("build_error", {"message": f"  [ERROR] {doc.title[:40]}: {str(e)[:80]}"})

    # ── Phase 6: Batch embed + index ─────────────────────────────
    if results["ingested"]:
        if on_stage:
            on_stage("crawl_embed", {"message": f"Embedding nodes from {len(results['ingested'])} pages"})
        try:
            from ..context.vector_integration import get_session_vector_store
            svs = get_session_vector_store()
            ns = db.name
            if svs and svs.available:
                all_nodes = db.csr_adapter.get_all_nodes()
                embed_candidates = [
                    n for n in all_nodes
                    if getattr(n, 'node_type', getattr(n, 'label', '')) in
                       ('Passage', 'Entity', 'Person', 'Organization', 'Location', 'Event', 'Fact')
                    and not n.properties.get('_embedded')
                ]
                if on_stage:
                    on_stage("crawl_embed_start", {
                        "message": f"Embedding {len(embed_candidates)} nodes (vector store: {type(svs).__name__})",
                        "nodes_to_embed": len(embed_candidates),
                    })
                embedded = 0
                skipped_embed = 0
                for node in embed_candidates:
                    label = getattr(node, 'node_type', getattr(node, 'label', ''))
                    text = node.properties.get('content', node.properties.get('statement', ''))
                    if not text:
                        text = node.properties.get('name', '')
                    if text and len(text) > 5:
                        try:
                            ok = svs.add_text(ns, text[:1000], node.id, metadata={
                                "label": label, "name": node.properties.get("name", ""),
                            })
                            if ok:
                                node.properties["_embedded"] = True
                                db.csr_adapter.update_node_properties(node.id, node.properties)
                                embedded += 1
                        except Exception as e:
                            logger.warning("Failed to embed node %s: %s", node.id[:12], e)
                    else:
                        skipped_embed += 1
                if on_stage:
                    on_stage("crawl_embedded", {
                        "message": (
                            f"Embedded {embedded}/{len(embed_candidates)} nodes"
                            + (f" ({skipped_embed} skipped — no text)" if skipped_embed else "")
                        ),
                        "embedded": embedded,
                        "skipped": skipped_embed,
                    })
        except Exception as e:
            logger.warning("Batch embed failed: %s", e)

        if on_stage:
            on_stage("crawl_index", {"message": "Building BM25 search indexes"})
        try:
            class _CrawlResult:
                passage_ids = _all_passage_ids
                entity_ids = _all_entity_ids
                fact_ids = _all_fact_ids
            _index_bm25(_CrawlResult(), db)
        except Exception:
            pass

    total_time = time.time() - t0

    # Record ingested URLs in Redis for future dedup
    if results.get("ingested"):
        try:
            import os as _os
            import redis as _redis_dedup
            _rurl = _os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
            if _rurl:
                _rconn = _redis_dedup.from_url(_rurl)
                _graph_name = getattr(db, 'namespace', '') or getattr(db, '_namespace', '') or getattr(db, 'name', '')
                _dedup_key = f"contextcore:ingested_urls:{_graph_name}"
                ingested_urls = [p["url"] for p in results["ingested"] if isinstance(p, dict) and p.get("url")]
                if not ingested_urls:
                    ingested_urls = list(visited)  # fallback to all visited URLs
                if ingested_urls:
                    _rconn.sadd(_dedup_key, *ingested_urls)
                    logger.info("[CRAWL] Recorded %d URLs as ingested for future dedup", len(ingested_urls))
        except Exception:
            pass

    if on_stage:
        on_stage("crawl_done", {
            "message": f"Crawl complete in {total_time:.0f}s: {len(results['ingested'])} ingested, {len(results['filtered'])} filtered, {len(results['failed'])} failed out of {results['total_crawled']} pages"
        })

    return results


def get_staging(staging_id: str) -> Optional[StagingResult]:
    """Retrieve a staged preview by ID."""
    return _staging_store.get(staging_id)


def list_pipelines() -> Dict[str, Dict]:
    """List available ingestion pipelines with descriptions."""
    return dict(PIPELINES)


