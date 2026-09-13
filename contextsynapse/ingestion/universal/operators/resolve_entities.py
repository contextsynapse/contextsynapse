"""ResolveEntitiesOperator -- deduplicate entities across the full graph."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

_ENTITY_LABELS = frozenset({
    "Tool", "Concept", "Technology", "Person", "Organization",
    "Topic", "Decision", "Problem", "Solution", "Task",
    "Fact", "Finding", "Insight",
})


def _normalize_name(name: str) -> str:
    return " ".join(name.lower().strip().split())


class ResolveEntitiesOperator(StageOperator):
    name = "resolve_entities"

    def process(self, chunks, graph_ctx):
        db = graph_ctx.db
        if not hasattr(db, "get_all_nodes"):
            return chunks

        # Determine entity labels from schema or default
        entity_labels = set(_ENTITY_LABELS)
        schema = getattr(graph_ctx, "compiled_schema", None)
        if schema and hasattr(schema, "node_types"):
            schema_labels = set()
            for nt in (schema.node_types if isinstance(schema.node_types, dict) else []):
                name = nt if isinstance(nt, str) else getattr(nt, "name", str(nt))
                schema_labels.add(name)
            if schema_labels:
                entity_labels = schema_labels

        # Group nodes by label + normalized name
        groups = defaultdict(list)
        try:
            all_nodes = list(db.get_all_nodes())
        except Exception:
            return chunks

        for node in all_nodes:
            label = getattr(node, "label", "") or getattr(node, "node_type", "")
            if label not in entity_labels:
                continue
            name = (getattr(node, "properties", {}) or {}).get("name", "")
            if not name:
                continue
            key = f"{label}:{_normalize_name(name)}"
            groups[key].append(node)

        dup_groups = sum(1 for g in groups.values() if len(g) > 1)
        graph_ctx.log(f"Scanned {len(all_nodes)} nodes, {dup_groups} duplicate groups found")

        # Merge duplicates
        merged = 0
        for key, nodes in groups.items():
            if len(nodes) <= 1:
                continue
            survivor = nodes[0]
            for duplicate in nodes[1:]:
                # Redirect edges
                try:
                    neighbors = db.get_neighbors(duplicate.id)
                    for neighbor_id, edge_obj in (neighbors or []):
                        edge_type = getattr(edge_obj, "edge_type", "RELATED_TO")
                        graph_ctx.add_edge(survivor.id, neighbor_id, edge_type)
                except Exception:
                    pass
                # Remove duplicate
                try:
                    db.remove_node(duplicate.id)
                    merged += 1
                except Exception:
                    pass
            # Update entity_ids
            graph_ctx.entity_ids[key] = survivor.id

        if merged:
            graph_ctx.log(f"Merged {merged} duplicate entities")
            logger.info("resolve_entities: merged %d duplicate entities", merged)
        return chunks


# ── Cross-Ingestion Entity Resolution ──────────────────────────────

def _jaccard_words(a: str, b: str) -> float:
    """Word-level Jaccard similarity between two strings."""
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    if not wa or not wb:
        return 0.0
    return len(wa & wb) / len(wa | wb)


def _count_edges(db, node_id: str) -> int:
    """Count total edges (in + out) for a node."""
    try:
        neighbors = db.get_neighbors(node_id)
        return len(list(neighbors or []))
    except Exception:
        return 0


def resolve_graph_entities(db, fuzzy_threshold: float = 0.85) -> Dict[str, Any]:
    """Run entity resolution across the ENTIRE graph (post-ingestion).

    Unlike the per-conversation operator, this scans all entities and
    merges duplicates across different ingestions.

    Strategy:
      1. Exact match: same label + normalized name
      2. Fuzzy match: same label + word Jaccard >= threshold
      3. Merge: keep node with most edges, redirect edges, delete dupes

    Returns:
        {"merged": int, "groups": int, "total_scanned": int, "fuzzy_merged": int}
    """
    try:
        all_nodes = list(db.get_all_nodes())
    except Exception:
        return {"merged": 0, "groups": 0, "total_scanned": 0, "fuzzy_merged": 0}

    entity_labels = _ENTITY_LABELS | {"Location", "Event"}

    # Collect entity nodes
    entities = []
    for node in all_nodes:
        label = getattr(node, "label", "") or ""
        if label not in entity_labels:
            continue
        props = dict(getattr(node, "properties", {}) or {})
        name = props.get("name", "")
        if not name or len(name) < 2:
            continue
        entities.append((node, label, name))

    total_scanned = len(entities)
    if total_scanned < 2:
        return {"merged": 0, "groups": 0, "total_scanned": total_scanned, "fuzzy_merged": 0}

    # Phase 1: Exact match grouping
    exact_groups: Dict[str, List] = defaultdict(list)
    for node, label, name in entities:
        key = f"{label}:{_normalize_name(name)}"
        exact_groups[key].append(node)

    merged = 0
    groups_merged = 0

    for key, nodes in exact_groups.items():
        if len(nodes) <= 1:
            continue
        # Pick survivor: node with most edges
        nodes.sort(key=lambda n: _count_edges(db, n.id), reverse=True)
        survivor = nodes[0]
        for dup in nodes[1:]:
            _merge_nodes(db, survivor, dup)
            merged += 1
        groups_merged += 1

    # Phase 2: Fuzzy match within same label (only singletons)
    singletons: Dict[str, List] = defaultdict(list)
    for key, nodes in exact_groups.items():
        if len(nodes) == 1:
            label = key.split(":")[0]
            node = nodes[0]
            name = (getattr(node, "properties", {}) or {}).get("name", "")
            singletons[label].append((node, name))

    fuzzy_merged = 0
    for label, items in singletons.items():
        if len(items) < 2:
            continue
        # Simple O(n^2) — fine for < 10K entities
        consumed = set()
        for i in range(len(items)):
            if i in consumed:
                continue
            node_a, name_a = items[i]
            group = [node_a]
            for j in range(i + 1, len(items)):
                if j in consumed:
                    continue
                node_b, name_b = items[j]
                if _jaccard_words(name_a, name_b) >= fuzzy_threshold:
                    group.append(node_b)
                    consumed.add(j)
            if len(group) > 1:
                group.sort(key=lambda n: _count_edges(db, n.id), reverse=True)
                survivor = group[0]
                for dup in group[1:]:
                    _merge_nodes(db, survivor, dup)
                    fuzzy_merged += 1
                    merged += 1
                groups_merged += 1

    if merged:
        logger.info("[RESOLVE] Cross-ingestion: merged %d entities (%d fuzzy) from %d scanned",
                     merged, fuzzy_merged, total_scanned)

    return {
        "merged": merged,
        "groups": groups_merged,
        "total_scanned": total_scanned,
        "fuzzy_merged": fuzzy_merged,
    }


def _merge_nodes(db, survivor, duplicate):
    """Merge duplicate into survivor: redirect edges, merge properties, delete."""
    # Merge properties (union, survivor wins on conflict)
    surv_props = dict(getattr(survivor, "properties", {}) or {})
    dup_props = dict(getattr(duplicate, "properties", {}) or {})
    for k, v in dup_props.items():
        if k not in surv_props or not surv_props[k]:
            surv_props[k] = v
    try:
        if hasattr(db, "update_node_properties"):
            db.update_node_properties(survivor.id, surv_props)
    except Exception:
        pass

    # Redirect edges
    try:
        neighbors = db.get_neighbors(duplicate.id)
        for neighbor_id, edge_obj in (neighbors or []):
            edge_label = getattr(edge_obj, "label", None) or getattr(edge_obj, "edge_type", "RELATED_TO")
            # Determine direction
            source = getattr(edge_obj, "source", "")
            if source == duplicate.id:
                db.add_edge(type(edge_obj)(
                    id=f"re_{survivor.id[:8]}_{neighbor_id[:8]}",
                    source=survivor.id, target=neighbor_id,
                    label=edge_label, properties=dict(getattr(edge_obj, "properties", {}) or {}),
                ))
            else:
                db.add_edge(type(edge_obj)(
                    id=f"re_{neighbor_id[:8]}_{survivor.id[:8]}",
                    source=neighbor_id, target=survivor.id,
                    label=edge_label, properties=dict(getattr(edge_obj, "properties", {}) or {}),
                ))
    except Exception:
        pass

    # Delete duplicate
    try:
        db.remove_node(duplicate.id)
    except Exception:
        pass
