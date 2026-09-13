"""
Tabular-to-Graph Mapper
=======================
Converts classified tabular data (Excel sheets, CSV, PDF tables)
into GraphNode and GraphEdge lists.

Supports three modes based on classification:
  - GRAPH_COLUMNS: rows have explicit source/target/relation columns
  - FLAT_RECORDS: each row is an entity; relationships derived from FK patterns
  - HYBRID_SHEETS: multiple sheets with different roles (nodes, edges, data)
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Optional, Set, Tuple

from ..pipeline_context import ClassificationResult

logger = logging.getLogger(__name__)


def _slugify(s: str) -> str:
    """Safe label from string."""
    return re.sub(r"[^a-zA-Z0-9_]", "_", str(s).strip())[:64]


def _canonical_key(value: Any) -> str:
    """Canonical key for node deduplication."""
    return str(value).strip().lower()


class TabularToGraphMapper:
    """
    Maps classified tabular data to graph nodes and edges.

    Returns lists of node/edge dicts (not GraphNode/GraphEdge objects)
    so they remain JSON-serializable in PipelineContext.
    """

    def map(
        self,
        tables: List[Dict[str, Any]],
        classification: ClassificationResult,
    ) -> Tuple[List[Dict], List[Dict]]:
        """
        Map tables to nodes and edges based on classification.

        Returns:
            (nodes, edges) — lists of serializable dicts.
        """
        structure = classification.structure_type

        if structure == "graph_columns":
            return self._map_graph_columns(tables, classification)
        elif structure == "hybrid_sheets":
            return self._map_hybrid_sheets(tables, classification)
        else:
            # flat_records, table_heavy, or fallback
            return self._map_flat_records(tables, classification)

    # ------------------------------------------------------------------
    # GRAPH_COLUMNS: explicit source/target/relation columns
    # ------------------------------------------------------------------

    def _map_graph_columns(
        self,
        tables: List[Dict],
        classification: ClassificationResult,
    ) -> Tuple[List[Dict], List[Dict]]:
        nodes: Dict[str, Dict] = {}  # canonical_key -> node dict
        edges: List[Dict] = []
        col_roles = classification.column_roles

        # Find source/target/relation column names
        source_col = next((c for c, r in col_roles.items() if r == "source"), None)
        target_col = next((c for c, r in col_roles.items() if r == "target"), None)
        relation_col = next((c for c, r in col_roles.items() if r == "relation"), None)
        property_cols = [c for c, r in col_roles.items() if r == "property"]

        for table in tables:
            headers = table.get("headers", [])
            rows = table.get("rows", [])

            # Map header names to indices
            h_idx = {h.strip().lower(): i for i, h in enumerate(headers)}
            src_i = h_idx.get(source_col.lower()) if source_col else None
            tgt_i = h_idx.get(target_col.lower()) if target_col else None
            rel_i = h_idx.get(relation_col.lower()) if relation_col else None

            if src_i is None or tgt_i is None:
                logger.warning("Graph columns not found in table %s, skipping", table.get("id"))
                continue

            for row in rows:
                if len(row) <= max(src_i, tgt_i):
                    continue

                src_val = str(row[src_i]).strip() if row[src_i] is not None else ""
                tgt_val = str(row[tgt_i]).strip() if row[tgt_i] is not None else ""
                if not src_val or not tgt_val:
                    continue

                rel_val = str(row[rel_i]).strip() if rel_i is not None and row[rel_i] is not None else "RELATED_TO"

                # Create/dedup source node
                src_key = _canonical_key(src_val)
                if src_key not in nodes:
                    nodes[src_key] = {
                        "id": str(uuid.uuid4()),
                        "label": "Entity",
                        "properties": {"name": src_val},
                    }

                # Create/dedup target node
                tgt_key = _canonical_key(tgt_val)
                if tgt_key not in nodes:
                    nodes[tgt_key] = {
                        "id": str(uuid.uuid4()),
                        "label": "Entity",
                        "properties": {"name": tgt_val},
                    }

                # Build edge properties from remaining columns
                edge_props = {}
                for pc in property_cols:
                    pc_i = h_idx.get(pc.lower())
                    if pc_i is not None and pc_i < len(row) and row[pc_i] is not None:
                        edge_props[_slugify(pc)] = row[pc_i]

                edges.append({
                    "id": str(uuid.uuid4()),
                    "source": nodes[src_key]["id"],
                    "target": nodes[tgt_key]["id"],
                    "label": _slugify(rel_val) or "RELATED_TO",
                    "properties": edge_props,
                })

        return list(nodes.values()), edges

    # ------------------------------------------------------------------
    # FLAT_RECORDS: each row is a node, derive edges from FK/shared values
    # ------------------------------------------------------------------

    def _map_flat_records(
        self,
        tables: List[Dict],
        classification: ClassificationResult,
    ) -> Tuple[List[Dict], List[Dict]]:
        nodes: List[Dict] = []
        edges: List[Dict] = []
        col_roles = classification.column_roles

        for table in tables:
            headers = table.get("headers", [])
            rows = table.get("rows", [])
            sheet_name = table.get("sheet_name", "Record")
            label = _slugify(sheet_name) or "Record"

            id_col_name = next((c for c, r in col_roles.items() if r == "id"), None)
            fk_cols = [c for c, r in col_roles.items() if r == "foreign_key"]
            h_idx = {h.strip().lower(): i for i, h in enumerate(headers)}

            # Track nodes by their ID value for FK edge creation
            id_to_node_id: Dict[str, str] = {}

            for row in rows:
                node_id = str(uuid.uuid4())
                props = {}
                raw_id = None

                for i, h in enumerate(headers):
                    if i < len(row) and row[i] is not None:
                        props[_slugify(h)] = row[i]

                # Use ID column value for dedup/FK linking
                if id_col_name:
                    id_i = h_idx.get(id_col_name.lower())
                    if id_i is not None and id_i < len(row) and row[id_i] is not None:
                        raw_id = _canonical_key(row[id_i])
                        id_to_node_id[raw_id] = node_id

                nodes.append({
                    "id": node_id,
                    "label": label,
                    "properties": props,
                })

            # Create edges from FK columns (link to nodes in same or other tables)
            for node in nodes:
                for fk in fk_cols:
                    fk_i = h_idx.get(fk.lower())
                    if fk_i is None:
                        continue
                    fk_val = node["properties"].get(_slugify(fk))
                    if fk_val is None:
                        continue
                    fk_key = _canonical_key(fk_val)
                    target_id = id_to_node_id.get(fk_key)
                    if target_id and target_id != node["id"]:
                        rel_label = _slugify(fk.replace("_id", "").replace("_key", "")) or "REFERENCES"
                        edges.append({
                            "id": str(uuid.uuid4()),
                            "source": node["id"],
                            "target": target_id,
                            "label": rel_label,
                            "properties": {},
                        })

        return nodes, edges

    # ------------------------------------------------------------------
    # HYBRID_SHEETS: multiple sheets with different roles
    # ------------------------------------------------------------------

    def _map_hybrid_sheets(
        self,
        tables: List[Dict],
        classification: ClassificationResult,
    ) -> Tuple[List[Dict], List[Dict]]:
        all_nodes: List[Dict] = []
        all_edges: List[Dict] = []
        sheet_roles = classification.sheet_roles

        # Process node sheets first
        node_id_map: Dict[str, str] = {}  # canonical_name -> node_id (for cross-sheet linking)

        node_tables = [t for t in tables if sheet_roles.get(t.get("sheet_name", t.get("id", ""))) == "nodes"]
        edge_tables = [t for t in tables if sheet_roles.get(t.get("sheet_name", t.get("id", ""))) == "edges"]
        data_tables = [t for t in tables if sheet_roles.get(t.get("sheet_name", t.get("id", ""))) == "data"]

        # Process node sheets as flat records
        for table in node_tables:
            sub_class = ClassificationResult(
                structure_type="flat_records",
                column_roles=self._quick_classify_cols(table.get("headers", [])),
            )
            n, e = self._map_flat_records([table], sub_class)
            for node in n:
                name = node["properties"].get("name", node["properties"].get("id", ""))
                if name:
                    node_id_map[_canonical_key(name)] = node["id"]
            all_nodes.extend(n)
            all_edges.extend(e)

        # Process edge sheets as graph_columns
        for table in edge_tables:
            sub_class = ClassificationResult(
                structure_type="graph_columns",
                column_roles=self._quick_classify_cols(table.get("headers", [])),
            )
            n, e = self._map_graph_columns([table], sub_class)
            all_nodes.extend(n)
            all_edges.extend(e)

        # Process remaining data sheets as flat records
        for table in data_tables:
            sub_class = ClassificationResult(
                structure_type="flat_records",
                column_roles=self._quick_classify_cols(table.get("headers", [])),
            )
            n, e = self._map_flat_records([table], sub_class)
            all_nodes.extend(n)
            all_edges.extend(e)

        return all_nodes, all_edges

    def _quick_classify_cols(self, headers: List[str]) -> Dict[str, str]:
        """Quick column role classification for sub-processing."""
        from ..input_classifier import SOURCE_PATTERNS, TARGET_PATTERNS, RELATION_PATTERNS, ID_PATTERNS, FK_SUFFIXES
        roles = {}
        headers_lower = [h.strip().lower() for h in headers]
        for h, hl in zip(headers, headers_lower):
            normalized = re.sub(r"[^a-z0-9]", "_", hl)
            if normalized in SOURCE_PATTERNS:
                roles[h] = "source"
            elif normalized in TARGET_PATTERNS:
                roles[h] = "target"
            elif normalized in RELATION_PATTERNS:
                roles[h] = "relation"
            elif normalized in ID_PATTERNS:
                roles[h] = "id"
            elif any(hl.endswith(s) for s in FK_SUFFIXES):
                roles[h] = "foreign_key"
            else:
                roles[h] = "property"
        return roles
