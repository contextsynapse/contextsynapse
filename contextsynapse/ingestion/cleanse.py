"""Context cleansing — improve quality of existing context without re-ingesting.

Runs 5 steps:
1. DEDUP    — merge duplicate entities
2. PRUNE    — remove low-confidence nodes (< threshold)
3. ORPHAN   — remove disconnected nodes (zero edges)
4. VALIDATE — re-run schema validation on all nodes
5. REINDEX  — rebuild BM25 + vector indexes
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class CleanseResult:
    """Result of cleansing a context."""
    duplicates_merged: int = 0
    low_quality_pruned: int = 0
    orphans_removed: int = 0
    nodes_revalidated: int = 0
    nodes_reindexed: int = 0
    errors: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict:
        return {
            "duplicates_merged": self.duplicates_merged,
            "low_quality_pruned": self.low_quality_pruned,
            "orphans_removed": self.orphans_removed,
            "nodes_revalidated": self.nodes_revalidated,
            "nodes_reindexed": self.nodes_reindexed,
            "errors": self.errors,
        }

    @property
    def summary(self) -> str:
        parts = []
        if self.duplicates_merged:
            parts.append(f"merged {self.duplicates_merged} duplicates")
        if self.low_quality_pruned:
            parts.append(f"pruned {self.low_quality_pruned} low-quality")
        if self.orphans_removed:
            parts.append(f"removed {self.orphans_removed} orphans")
        if self.nodes_revalidated:
            parts.append(f"re-validated {self.nodes_revalidated} nodes")
        if self.nodes_reindexed:
            parts.append(f"re-indexed {self.nodes_reindexed} nodes")
        return "Cleansed: " + ", ".join(parts) if parts else "No changes needed"


# Node types that should never be pruned or removed
PROTECTED_TYPES = {"ContextMeta", "VectorIndex", "BM25Index", "Session", "ContextRef", "Agent", "AgentRef"}


def cleanse_context(db, schema=None, min_confidence: float = 0.3) -> CleanseResult:
    """Cleanse an existing context — improve quality without re-ingesting.

    Args:
        db: AIContextDB instance.
        schema: Optional IngestionSchema for re-validation.
        min_confidence: Nodes below this confidence get pruned.

    Returns:
        CleanseResult with counts of what was changed.
    """
    result = CleanseResult()

    try:
        all_nodes = db.csr_adapter.get_all_nodes()
    except Exception as e:
        result.errors.append(f"Failed to get nodes: {e}")
        return result

    # ── Step 1: DEDUP — merge duplicate entities ──────────────────
    # Three levels of dedup:
    #   a) Exact name match (same label) — "Tehran" + "Tehran"
    #   b) Cross-label match — "Iran" as Location + "Iran" as Organization
    #   c) Substring containment — "Netanyahu" matches "Benjamin Netanyahu"
    try:
        entity_labels = {"Person", "Organization", "Location", "Event"}
        seen = {}           # "name_lower" -> (label, node)
        to_merge = []       # [(keep_id, remove_id)]
        junk_ids = []       # nodes that are clearly not entities

        # Junk entity patterns — not real entities
        import re as _re
        _JUNK_PATTERNS = [
            _re.compile(r'^(edition|auto|watch|talk|class|world her|what has|look|give|story)$', _re.I),
            _re.compile(r'^.{1,2}$'),  # 1-2 char names
            _re.compile(r'^\d+$'),     # pure numbers
            _re.compile(r'^(the|a|an|this|that|http|www)\b', _re.I),  # starts with article/url
        ]

        for node in all_nodes:
            label = getattr(node, 'node_type', getattr(node, 'label', ''))
            if label not in entity_labels:
                continue
            name = node.properties.get('name', '').strip()
            if not name:
                continue
            name_lower = name.lower()

            # Remove junk entities
            if any(p.search(name_lower) for p in _JUNK_PATTERNS):
                junk_ids.append(node.id)
                continue

            # Dedup key: name only (cross-label merge)
            if name_lower in seen:
                existing_label, existing = seen[name_lower]
                existing_props = len([v for v in existing.properties.values() if v])
                current_props = len([v for v in node.properties.values() if v])
                # Prefer the one with more properties, or the better label
                label_priority = {"Location": 3, "Organization": 2, "Event": 2, "Person": 1}
                existing_priority = label_priority.get(existing_label, 0)
                current_priority = label_priority.get(label, 0)
                if current_props > existing_props or (current_props == existing_props and current_priority > existing_priority):
                    to_merge.append((node.id, existing.id))
                    seen[name_lower] = (label, node)
                else:
                    to_merge.append((existing.id, node.id))
            else:
                seen[name_lower] = (label, node)

        # Substring containment dedup — "Netanyahu" + "Benjamin Netanyahu" → keep longer
        seen_names = sorted(seen.keys(), key=len, reverse=True)  # longest first
        for i, long_name in enumerate(seen_names):
            if len(long_name) < 5:
                continue
            long_tokens = set(long_name.split())
            for short_name in seen_names[i+1:]:
                if len(short_name) < 3 or short_name == long_name:
                    continue
                # Short name's tokens are a subset of long name's tokens
                short_tokens = set(short_name.split())
                if short_tokens and short_tokens.issubset(long_tokens) and len(short_tokens) < len(long_tokens):
                    long_label, long_node = seen[long_name]
                    short_label, short_node = seen[short_name]
                    # Only merge if same entity type (or both people/orgs)
                    if long_label == short_label or {long_label, short_label} <= entity_labels:
                        to_merge.append((long_node.id, short_node.id))  # keep longer name
                        logger.debug("[CLEANSE] Substring merge: '%s' ← '%s'", long_name, short_name)

        # Execute merges
        for keep_id, remove_id in to_merge:
            try:
                db.csr_adapter.remove_node(remove_id)
                result.duplicates_merged += 1
            except Exception:
                pass

        # Remove junk entities
        for junk_id in junk_ids:
            try:
                db.csr_adapter.remove_node(junk_id)
                result.duplicates_merged += 1
            except Exception:
                pass

        if junk_ids:
            logger.info("[CLEANSE] Removed %d junk entities", len(junk_ids))
    except Exception as e:
        result.errors.append(f"Dedup failed: {e}")

    # ── Step 2: PRUNE — remove low-confidence nodes ──────────────
    try:
        # Re-fetch after dedup
        all_nodes = db.csr_adapter.get_all_nodes()
        for node in all_nodes:
            label = getattr(node, 'node_type', getattr(node, 'label', ''))
            if label in PROTECTED_TYPES or label in ('Document', 'Passage'):
                continue  # never prune documents or passages
            confidence = node.properties.get('confidence', 1.0)
            if isinstance(confidence, (int, float)) and confidence < min_confidence:
                try:
                    db.csr_adapter.remove_node(node.id)
                    result.low_quality_pruned += 1
                except Exception:
                    pass
    except Exception as e:
        result.errors.append(f"Prune failed: {e}")

    # ── Step 3: ORPHAN — remove disconnected nodes ────────────────
    try:
        all_nodes = db.csr_adapter.get_all_nodes()
        for node in all_nodes:
            label = getattr(node, 'node_type', getattr(node, 'label', ''))
            if label in PROTECTED_TYPES or label in ('Document', 'ContextMeta'):
                continue
            # Check if node has any edges
            outgoing = db.csr_adapter.get_neighbors(node.id)
            incoming = db.csr_adapter.get_incoming_neighbors(node.id) if hasattr(db.csr_adapter, 'get_incoming_neighbors') else []
            if not outgoing and not incoming:
                try:
                    db.csr_adapter.remove_node(node.id)
                    result.orphans_removed += 1
                except Exception:
                    pass
    except Exception as e:
        result.errors.append(f"Orphan removal failed: {e}")

    # ── Step 4: VALIDATE — re-run schema validation ───────────────
    try:
        if schema:
            from .schema_validator import validate_and_flag
            all_nodes = db.csr_adapter.get_all_nodes()
            for node in all_nodes:
                label = getattr(node, 'node_type', getattr(node, 'label', ''))
                if label in PROTECTED_TYPES:
                    continue
                node_dict = {"label": label, "properties": dict(node.properties)}
                validated = validate_and_flag(node_dict, schema)
                for vk in ("validation_status", "validation_errors"):
                    if vk in validated["properties"]:
                        node.properties[vk] = validated["properties"][vk]
                db.csr_adapter.update_node_properties(node.id, node.properties)
                result.nodes_revalidated += 1
    except Exception as e:
        result.errors.append(f"Validation failed: {e}")

    # ── Step 5: REINDEX — rebuild search indexes ──────────────────
    try:
        all_nodes = db.csr_adapter.get_all_nodes()
        indexable = [n for n in all_nodes
                     if getattr(n, 'node_type', getattr(n, 'label', '')) not in PROTECTED_TYPES]
        if indexable:
            from ..search.whoosh_search import WhooshSearchEngine
            engine = WhooshSearchEngine(db.name)
            engine.index_nodes(indexable)
            result.nodes_reindexed = len(indexable)
    except Exception as e:
        result.errors.append(f"Reindex failed: {e}")

    # ── Update ContextMeta ────────────────────────────────────────
    try:
        meta = db.csr_adapter.get_node("_context_meta")
        if meta:
            all_nodes = db.csr_adapter.get_all_nodes()
            content = [n for n in all_nodes if getattr(n, 'node_type', getattr(n, 'label', '')) not in PROTECTED_TYPES]
            meta.properties["node_count"] = len(content)
            labels = set(getattr(n, 'node_type', getattr(n, 'label', '')) for n in content)
            meta.properties["schema_summary"] = ", ".join(sorted(labels))
            db.csr_adapter.update_node_properties("_context_meta", meta.properties)
    except Exception:
        pass

    logger.info("Cleanse complete: %s", result.summary)
    return result
