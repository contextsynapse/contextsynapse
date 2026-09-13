"""Graph builder — creates nodes + edges from extracted content.

Takes CleanDocument + chunks + extractions and builds the full graph:
Document, Passage, Entity, Fact, Link nodes with all relationships.
"""
from __future__ import annotations

import hashlib
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from ..core.graph_structures import GraphNode, GraphEdge
from ..context.quality import score_node, EntityIndex

logger = logging.getLogger(__name__)


def _entity_matches_text(entity_name: str, text: str) -> bool:
    """Check if entity name appears as a whole word/phrase in text.

    Avoids false positives like 'IBM' matching 'nihilism' or
    'Apple' matching 'pineapple'.
    """
    import re
    if len(entity_name) < 3:
        return False
    # Use word boundary matching for names 3+ chars
    pattern = r'\b' + re.escape(entity_name) + r'\b'
    return bool(re.search(pattern, text, re.IGNORECASE))

_entity_index = None


def _get_entity_index(db=None):
    global _entity_index
    if _entity_index is None:
        redis_client = None
        try:
            if hasattr(db, '_redis') and db._redis:
                redis_client = db._redis
            elif hasattr(db, 'csr_adapter') and hasattr(db.csr_adapter, '_redis'):
                redis_client = db.csr_adapter._redis
        except Exception:
            pass
        _entity_index = EntityIndex(redis_client=redis_client)
    return _entity_index


@dataclass
class ChunkExtraction:
    """Extraction results for a single passage."""
    chunk_index: int
    entities: List[Dict[str, Any]] = field(default_factory=list)
    facts: List[Dict[str, Any]] = field(default_factory=list)
    relationships: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SchemaSuggestion:
    """A suggested addition to the schema based on extraction results."""
    suggestion_type: str = ""   # new_node_type | new_edge_type | new_field
    name: str = ""              # type or field name
    count: int = 0              # how many times it appeared
    examples: List[str] = field(default_factory=list)  # sample values
    source_type: str = ""       # for new_edge_type: source node type
    target_type: str = ""       # for new_edge_type: target node type

    def to_dict(self) -> Dict:
        d = {"type": self.suggestion_type, "name": self.name, "count": self.count, "examples": self.examples[:3]}
        if self.source_type:
            d["source"] = self.source_type
        if self.target_type:
            d["target"] = self.target_type
        return d


@dataclass
class BuildResult:
    """Result of building a graph from ingested content."""
    document_id: str = ""
    passage_ids: List[str] = field(default_factory=list)
    entity_ids: Dict[str, str] = field(default_factory=dict)  # name_lower -> node_id
    fact_ids: List[str] = field(default_factory=list)
    link_ids: List[str] = field(default_factory=list)
    edge_count: int = 0
    errors: List[str] = field(default_factory=list)
    schema_suggestions: List[SchemaSuggestion] = field(default_factory=list)


def _entity_key(label: str, name: str) -> str:
    """Dedup key for entities: type:name_lower."""
    return f"{label}:{name.strip().lower()}"


def _make_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def build_graph(
    clean_doc,
    chunks: list,
    extractions: List[ChunkExtraction],
    db,
    schema=None,
    pipeline_name: str = "smart_ingest",
    owner: str = "",
    sensitivity: str = "public",
    compiled_schema=None,
) -> BuildResult:
    """Build full graph from cleaned document + chunked passages + extractions.

    Creates: Document, Passage, Entity, Fact, Link nodes.
    Creates: CONTAINS, NEXT, MENTIONS, STATES, DERIVED_FROM, LINKS_TO, HAS_LINK,
             and typed relationship edges from LLM.

    Args:
        clean_doc: CleanDocument from cleaner.py
        chunks: List[PassageChunk] from chunker.py
        extractions: List[ChunkExtraction] per chunk
        db: AIContextDB instance
        schema: Optional IngestionSchema for validation
        pipeline_name: Name of the pipeline that produced this

    Returns:
        BuildResult with all created node IDs.
    """
    result = BuildResult()
    now = datetime.now(timezone.utc).isoformat()

    # Source metadata to propagate to all entities/facts
    # Source credibility scoring
    _credibility = 0.50
    try:
        from plugins.stock_analysis.source_credibility import CredibilityScorer
        _cred_scorer = CredibilityScorer()
        _credibility = _cred_scorer.score(
            url=clean_doc.source_url or "",
            source_type=pipeline_name or "",
            text=(clean_doc.title or "")[:200],
        )
    except Exception:
        pass

    _source_meta = {
        "_source_url": clean_doc.source_url or "",
        "_source_title": clean_doc.title or "",
        "_published_at": getattr(clean_doc, 'date', '') or "",
        "_source_name": getattr(clean_doc, 'source_name', '') or "",
        "_credibility": _credibility,
    }

    # ── 1. Document node (skip if URL already exists) ─────────────
    content_hash = hashlib.sha256(clean_doc.body.encode()).hexdigest() if clean_doc.body else ""

    # Check for existing document with same URL (use search if available, else scan)
    existing_doc_id = None
    if clean_doc.source_url:
        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            # Try search-based lookup first (fast)
            found = False
            if hasattr(db, 'search') and clean_doc.source_url:
                try:
                    hits = db.search(clean_doc.source_url, max_results=5)
                    for hit in (hits or []):
                        h_props = getattr(hit, 'properties', {}) or (hit if isinstance(hit, dict) else {})
                        h_label = getattr(hit, 'label', getattr(hit, 'node_type', h_props.get('label', '')))
                        if h_label not in ('Document', 'WebPage'):
                            continue
                        h_url = h_props.get('url') or h_props.get('source_url', '')
                        if h_url == clean_doc.source_url:
                            h_hash = h_props.get('content_hash', '')
                            existing_doc_id = getattr(hit, 'id', h_props.get('id'))
                            if h_hash == content_hash:
                                logger.info("Skipping duplicate document: %s (same URL + content)", clean_doc.source_url)
                                result.document_id = existing_doc_id
                                return result
                            else:
                                logger.info("Document URL exists but content changed: %s", clean_doc.source_url)
                            found = True
                            break
                except Exception:
                    pass
            # Fallback: scan only Document/WebPage nodes (not all nodes)
            if not found:
                for existing in adapter.get_all_nodes():
                    ex_label = getattr(existing, 'label', getattr(existing, 'node_type', ''))
                    if ex_label not in ('Document', 'WebPage'):
                        continue
                    ex_props = getattr(existing, 'properties', {}) or {}
                    ex_url = ex_props.get('url') or ex_props.get('source_url', '')
                    if ex_url == clean_doc.source_url:
                        ex_hash = ex_props.get('content_hash', '')
                        existing_doc_id = existing.id
                        if ex_hash == content_hash:
                            logger.info("Skipping duplicate document: %s (same URL + content)", clean_doc.source_url)
                            result.document_id = existing.id
                            return result
                        else:
                            logger.info("Document URL exists but content changed: %s", clean_doc.source_url)
                        break
        except Exception:
            pass

    doc_id = existing_doc_id or _make_id("doc")

    doc_props = {
        "name": clean_doc.title or clean_doc.source_url or "Untitled",
        "title": clean_doc.title,
        "url": clean_doc.source_url,
        "source": clean_doc.source_name,
        "author": clean_doc.author,
        "published_at": clean_doc.date,
        "word_count": clean_doc.word_count,
        "content_hash": content_hash,
        "passage_count": len(chunks),
        "status": "ingesting",
        "pipeline": pipeline_name,
        "source_type": "pipeline",
        "created_by": f"pipeline:{pipeline_name}",
        "ingested_at": now,
        "_owner": owner,
        "_sensitivity": sensitivity,
    }
    doc_props["_quality"] = score_node({"label": "Document", "properties": doc_props, "edge_count": 0})
    db.add_node(GraphNode(
        id=doc_id,
        label="Document",
        properties=doc_props,
    ))
    result.document_id = doc_id

    # ── 1b. Section nodes for heading hierarchy ───────────────────
    # Build one Section node per unique heading path seen across all chunks.
    # Edges: Document → HAS_SECTION → Section (root sections)
    #        Section  → HAS_SECTION → Section (nested sections)
    section_node_map: Dict[str, str] = {}  # tuple(heading_path) → node_id

    def _get_or_create_section(heading_path: list, heading_depth: int) -> Optional[str]:
        if not heading_path:
            return None
        key = tuple(heading_path)
        if key in section_node_map:
            return section_node_map[key]

        sec_id = _make_id("section")
        title = heading_path[-1]
        sec_props = {
            "name": title,
            "heading_path": " > ".join(heading_path),
            "heading_depth": heading_depth,
            "document_id": doc_id,
            "source_url": clean_doc.source_url,
            "_owner": owner,
        }
        db.add_node(GraphNode(id=sec_id, label="Section", properties=sec_props))
        section_node_map[key] = sec_id

        # Connect to parent: Document or parent Section
        if len(heading_path) == 1:
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=doc_id, target=sec_id,
                label="HAS_SECTION", properties={"depth": heading_depth},
            ))
            result.edge_count += 1
        else:
            parent_key = tuple(heading_path[:-1])
            parent_id = section_node_map.get(parent_key)
            if parent_id:
                db.add_edge(GraphEdge(
                    id=str(uuid.uuid4()), source=parent_id, target=sec_id,
                    label="HAS_SECTION", properties={"depth": heading_depth},
                ))
                result.edge_count += 1

        return sec_id

    # Pre-create all section nodes in order (ensures parents exist before children)
    for chunk in chunks:
        hp = getattr(chunk, "heading_path", [])
        hd = getattr(chunk, "heading_depth", 0)
        if hp:
            # Ensure all ancestor sections exist first
            for i in range(1, len(hp) + 1):
                _get_or_create_section(hp[:i], i)

    # ── 2. Passage nodes + CONTAINS/NEXT edges ───────────────────
    prev_passage_id = None
    for chunk in chunks:
        passage_id = _make_id("passage")
        passage_props = {
            "name": f"{clean_doc.title or 'Untitled'} (passage {chunk.chunk_index + 1}/{len(chunks)})",
            "content": chunk.content,
            "overlap_prefix": chunk.overlap_prefix,
            "chunk_index": chunk.chunk_index,
            "token_count": chunk.token_count,
            "char_count": chunk.char_count,
            "document_id": doc_id,
            "section_title": getattr(chunk, "section_title", ""),
            "section_index": getattr(chunk, "section_index", 0),
            "heading_path": " > ".join(getattr(chunk, "heading_path", [])),
            "heading_depth": getattr(chunk, "heading_depth", 0),
            "source_url": clean_doc.source_url,
            "source_type": "pipeline",
            "created_by": f"pipeline:{pipeline_name}",
            "_entity_count": 0,
            "_fact_count": 0,
            "_link_count": len(chunk.links_in_chunk),
            "_owner": owner,
            "_sensitivity": sensitivity,
        }
        passage_props["_quality"] = score_node({"label": "Passage", "properties": passage_props, "edge_count": 0})
        db.add_node(GraphNode(
            id=passage_id,
            label="Passage",
            properties=passage_props,
        ))
        result.passage_ids.append(passage_id)

        # Document --CONTAINS--> Passage
        db.add_edge(GraphEdge(
            id=str(uuid.uuid4()), source=doc_id, target=passage_id,
            label="CONTAINS",
            properties={"chunk_index": chunk.chunk_index},
        ))
        result.edge_count += 1

        # Section --CONTAINS--> Passage (if this passage belongs to a heading section)
        hp = getattr(chunk, "heading_path", [])
        if hp:
            sec_id = section_node_map.get(tuple(hp))
            if sec_id:
                db.add_edge(GraphEdge(
                    id=str(uuid.uuid4()), source=sec_id, target=passage_id,
                    label="CONTAINS", properties={"chunk_index": chunk.chunk_index},
                ))
                result.edge_count += 1

        # Passage --NEXT--> Passage (reading order)
        if prev_passage_id:
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=prev_passage_id, target=passage_id,
                label="NEXT", properties={},
            ))
            result.edge_count += 1
        prev_passage_id = passage_id

    # ── 3. Entity nodes (deduped) + MENTIONS edges ───────────────
    entity_registry: Dict[str, str] = {}  # entity_key -> node_id
    entity_mentions: Dict[str, int] = {}  # entity_key -> mention_count

    # Schema-driven dedup (if compiled schema available) or EntityIndex fallback
    _schema_dedup = None
    if compiled_schema:
        try:
            from ..schema.dedup import SchemaDedupStrategy
            _schema_dedup = SchemaDedupStrategy(compiled_schema.dedup_strategies)
        except ImportError:
            pass

    eidx = _get_entity_index(db)
    graph_name = getattr(db, '_namespace', '') or getattr(db, 'namespace', '') or 'default'

    for ext in extractions:
        passage_id = result.passage_ids[ext.chunk_index] if ext.chunk_index < len(result.passage_ids) else None
        if not passage_id:
            continue

        for ent in ext.entities:
            label = ent.get("label", "Entity")
            props = ent.get("properties", {})
            name = props.get("name", "")
            if not name:
                continue

            # Schema-driven dedup key (composite keys like [name, dosage]) or legacy fallback
            if _schema_dedup:
                key = _schema_dedup.dedup_key(label, props)
            else:
                key = _entity_key(label, name)
            existing_entry = eidx.get(graph_name, label, name)

            # Layer 3: Fuzzy entity resolution if exact match fails
            if not existing_entry and key not in entity_registry:
                try:
                    from ..intelligence.dedup import EntityResolver
                    _resolver = EntityResolver()
                    _existing_for_resolve = [
                        {"name": k.split(":", 1)[1] if ":" in k else k, "label": k.split(":", 1)[0] if ":" in k else label, "id": v}
                        for k, v in entity_registry.items()
                    ]
                    _res = _resolver.resolve(name, label, _existing_for_resolve)
                    if _res.status == "merge" and _res.matched_id:
                        existing_entry = {"node_id": _res.matched_id, "mention_count": entity_mentions.get(key, 0)}
                        logger.debug("[RESOLVE] Fuzzy merged '%s' -> '%s'", name, _res.matched_name)
                    elif _res.status == "alias" and _res.matched_id:
                        existing_entry = {"node_id": _res.matched_id, "mention_count": entity_mentions.get(key, 0)}
                        logger.debug("[RESOLVE] Alias '%s' -> '%s'", name, _res.matched_name)
                except Exception:
                    pass

            if existing_entry:
                # Check keep_both policy — create separate node instead of merging
                if _schema_dedup and not _schema_dedup.should_dedup(label):
                    # keep_both: treat as new entity, skip dedup
                    pass
                else:
                    ent_id = existing_entry["node_id"]
                    entity_registry[key] = ent_id
                    entity_mentions[key] = existing_entry["mention_count"] + 1
                    try:
                        adapter = getattr(db, 'csr_adapter', None) or db
                        node = adapter.get_node(ent_id)
                        if node:
                            node.properties["mention_count"] = entity_mentions[key]
                            # Schema-driven merge or default fill-gaps
                            if _schema_dedup:
                                merged = _schema_dedup.merge(label, dict(node.properties), props)
                                if merged is not None:
                                    merged["mention_count"] = entity_mentions[key]
                                    node.properties.update(merged)
                            else:
                                for k, v in props.items():
                                    if v and not node.properties.get(k):
                                        node.properties[k] = v
                            adapter.update_node_properties(ent_id, node.properties)
                    except Exception:
                        pass
                eidx.put(graph_name, label, name, ent_id, quality=existing_entry.get("quality", 50))
            elif key not in entity_registry:
                ent_id = _make_id("ent")
                props["source_type"] = "pipeline"
                props["created_by"] = f"pipeline:{pipeline_name}"
                props["mention_count"] = 1
                props["_owner"] = owner
                props["_sensitivity"] = sensitivity
                # Propagate source metadata from document
                for sk, sv in _source_meta.items():
                    if sv and sk not in props:
                        props[sk] = sv
                node_for_scoring = {"label": label, "properties": props, "edge_count": 0}
                props["_quality"] = score_node(node_for_scoring)
                db.add_node(GraphNode(id=ent_id, label=label, properties=props))
                entity_registry[key] = ent_id
                entity_mentions[key] = 1
                eidx.put(graph_name, label, name, ent_id, quality=props["_quality"])
            else:
                ent_id = entity_registry[key]
                entity_mentions[key] = entity_mentions.get(key, 0) + 1

            # Passage --MENTIONS--> Entity
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=passage_id, target=entity_registry[key],
                label="MENTIONS", properties={},
            ))
            result.edge_count += 1

        # Update passage entity count
        try:
            p_node = db.csr_adapter.get_node(passage_id)
            if p_node:
                p_node.properties["_entity_count"] = len(ext.entities)
                db.csr_adapter.update_node_properties(passage_id, p_node.properties)
        except Exception:
            pass

    result.entity_ids = entity_registry

    # ── 4. Fact nodes + STATES/DERIVED_FROM edges ────────────────
    for ext in extractions:
        passage_id = result.passage_ids[ext.chunk_index] if ext.chunk_index < len(result.passage_ids) else None
        if not passage_id:
            continue

        for fact in ext.facts:
            props = fact.get("properties", fact)
            statement = props.get("statement", props.get("name", ""))
            if not statement:
                continue

            fact_id = _make_id("fact")
            fact_props = {
                "name": statement[:80],
                "statement": statement,
                "subject": props.get("subject", ""),
                "passage_id": passage_id,
                "source_type": "pipeline",
                "created_by": f"pipeline:{pipeline_name}",
                "confidence": props.get("confidence", 0.8),
                "_owner": owner,
                "_sensitivity": sensitivity,
                **{k: v for k, v in _source_meta.items() if v},  # source URL, title, published_at
            }
            fact_props["_quality"] = score_node({"label": "Fact", "properties": fact_props, "edge_count": 0})
            db.add_node(GraphNode(
                id=fact_id,
                label="Fact",
                properties=fact_props,
            ))
            result.fact_ids.append(fact_id)

            # Passage --STATES--> Fact
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=passage_id, target=fact_id,
                label="STATES", properties={},
            ))
            # Fact --DERIVED_FROM--> Passage (provenance)
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=fact_id, target=passage_id,
                label="DERIVED_FROM", properties={},
            ))
            result.edge_count += 2

            # Fact --MENTIONS--> Entity (entities named in this fact's statement)
            stmt_lower = statement.lower()
            _mentioned: set = set()
            for ent_key, ent_id in entity_registry.items():
                ent_name = ent_key.split(":", 1)[1] if ":" in ent_key else ent_key
                if len(ent_name) >= 3 and _entity_matches_text(ent_name, stmt_lower) and ent_id not in _mentioned:
                    db.add_edge(GraphEdge(
                        id=str(uuid.uuid4()), source=fact_id, target=ent_id,
                        label="MENTIONS", properties={"via": "statement"},
                    ))
                    _mentioned.add(ent_id)
                    result.edge_count += 1

        # Update passage fact count
        try:
            p_node = db.csr_adapter.get_node(passage_id)
            if p_node:
                p_node.properties["_fact_count"] = len(ext.facts)
                db.csr_adapter.update_node_properties(passage_id, p_node.properties)
        except Exception:
            pass

    # ── 5. Relationship edges (from LLM) ─────────────────────────
    for ext in extractions:
        for rel in ext.relationships:
            src_name = rel.get("source_name", "")
            tgt_name = rel.get("target_name", "")
            edge_label = rel.get("label", "RELATED_TO")

            # Find source/target node IDs — exact match first, then suffix match
            src_id = None
            tgt_id = None
            src_lower = src_name.lower()
            tgt_lower = tgt_name.lower()

            # Pass 1: exact key match (e.g. "company:apple")
            for key, nid in entity_registry.items():
                # key format is "label:entity_name_lower"
                key_name = key.split(":", 1)[1] if ":" in key else key
                if key_name == src_lower and src_id is None:
                    src_id = nid
                if key_name == tgt_lower and tgt_id is None:
                    tgt_id = nid

            # Pass 2: prefix match only if exact match failed
            if src_id is None or tgt_id is None:
                for key, nid in entity_registry.items():
                    key_name = key.split(":", 1)[1] if ":" in key else key
                    if src_id is None and (key_name.startswith(src_lower) or src_lower.startswith(key_name)):
                        src_id = nid
                    if tgt_id is None and (key_name.startswith(tgt_lower) or tgt_lower.startswith(key_name)):
                        tgt_id = nid

            if src_id and tgt_id and src_id != tgt_id:
                db.add_edge(GraphEdge(
                    id=str(uuid.uuid4()), source=src_id, target=tgt_id,
                    label=edge_label,
                    properties=rel.get("properties", {"confidence": 0.8}),
                ))
                result.edge_count += 1

    # ── 6. Link nodes + LINKS_TO/HAS_LINK edges ──────────────────
    link_registry: Dict[str, str] = {}  # url -> link_node_id

    for i, chunk in enumerate(chunks):
        passage_id = result.passage_ids[i] if i < len(result.passage_ids) else None
        if not passage_id:
            continue

        links = chunk.links_in_chunk if hasattr(chunk, "links_in_chunk") else []
        # Also get links from clean_doc that appear in this chunk
        if hasattr(clean_doc, "links"):
            for lnk in clean_doc.links:
                url = lnk.url if hasattr(lnk, "url") else lnk.get("url", "")
                if url and url.lower() in chunk.content.lower():
                    if not any(l.get("url") == url for l in links):
                        links.append({"url": url, "anchor": getattr(lnk, "anchor", ""), "domain": getattr(lnk, "domain", "")})

        for lnk in links:
            url = lnk.get("url", "") if isinstance(lnk, dict) else getattr(lnk, "url", "")
            if not url:
                continue

            if url not in link_registry:
                link_id = _make_id("link")
                anchor = lnk.get("anchor", "") if isinstance(lnk, dict) else getattr(lnk, "anchor", "")
                domain = lnk.get("domain", "") if isinstance(lnk, dict) else getattr(lnk, "domain", "")
                db.add_node(GraphNode(
                    id=link_id,
                    label="Link",
                    properties={
                        "name": anchor or url[:80],
                        "url": url,
                        "anchor_text": anchor,
                        "domain": domain,
                        "link_type": "internal" if domain == clean_doc.source_name else "external",
                    },
                ))
                link_registry[url] = link_id
                result.link_ids.append(link_id)

                # Document --HAS_LINK--> Link
                db.add_edge(GraphEdge(
                    id=str(uuid.uuid4()), source=doc_id, target=link_id,
                    label="HAS_LINK", properties={},
                ))
                result.edge_count += 1

            # Passage --LINKS_TO--> Link
            db.add_edge(GraphEdge(
                id=str(uuid.uuid4()), source=passage_id, target=link_registry[url],
                label="LINKS_TO", properties={},
            ))
            result.edge_count += 1

    # ── 7. Finalize Document node ─────────────────────────────────
    try:
        doc_node = db.csr_adapter.get_node(doc_id)
        if doc_node:
            doc_node.properties["status"] = "ingested"
            doc_node.properties["entity_count"] = len(entity_registry)
            doc_node.properties["fact_count"] = len(result.fact_ids)
            doc_node.properties["link_count"] = len(link_registry)
            db.csr_adapter.update_node_properties(doc_id, doc_node.properties)
    except Exception as e:
        result.errors.append(f"Failed to finalize document: {e}")

    logger.info(
        "Built graph: doc=%s, passages=%d, entities=%d, facts=%d, links=%d, edges=%d",
        doc_id[:12], len(result.passage_ids), len(entity_registry),
        len(result.fact_ids), len(link_registry), result.edge_count,
    )

    # ── 8. Schema suggestions — discover types/edges not in schema ────
    if schema:
        result.schema_suggestions = _suggest_schema_updates(extractions, schema)

    return result


def _suggest_schema_updates(
    extractions: List[ChunkExtraction],
    schema,
) -> List[SchemaSuggestion]:
    """Compare extracted types against schema, suggest additions."""
    schema_types = set(schema.node_types.keys()) if hasattr(schema, 'node_types') else set()
    schema_edges = set(schema.edge_types.keys()) if hasattr(schema, 'edge_types') else set()

    # Count extracted types not in schema
    type_counts: Dict[str, List[str]] = {}  # type -> [example names]
    edge_counts: Dict[str, Dict] = {}       # edge_type -> {count, sources, targets}

    for ext in extractions:
        for ent in ext.entities:
            label = ent.get("label", "")
            name = ent.get("properties", {}).get("name", "")
            if label and label not in schema_types:
                type_counts.setdefault(label, []).append(name)

        for rel in ext.relationships:
            edge_label = rel.get("label", "")
            if edge_label and edge_label not in schema_edges:
                if edge_label not in edge_counts:
                    edge_counts[edge_label] = {"count": 0, "sources": set(), "targets": set()}
                edge_counts[edge_label]["count"] += 1
                edge_counts[edge_label]["sources"].add(rel.get("source_name", ""))
                edge_counts[edge_label]["targets"].add(rel.get("target_name", ""))

    suggestions = []

    for type_name, examples in sorted(type_counts.items(), key=lambda x: -len(x[1])):
        if len(examples) >= 1:  # at least 1 occurrence
            suggestions.append(SchemaSuggestion(
                suggestion_type="new_node_type",
                name=type_name,
                count=len(examples),
                examples=list(dict.fromkeys(examples))[:5],  # unique, max 5
            ))

    for edge_name, info in sorted(edge_counts.items(), key=lambda x: -x[1]["count"]):
        suggestions.append(SchemaSuggestion(
            suggestion_type="new_edge_type",
            name=edge_name,
            count=info["count"],
            source_type=", ".join(list(info["sources"])[:3]),
            target_type=", ".join(list(info["targets"])[:3]),
        ))

    return suggestions
