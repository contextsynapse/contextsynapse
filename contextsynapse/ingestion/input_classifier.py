"""
Input Classifier
================
Rule-based classifier that detects file type and content structure
to route data to the correct pipeline archetype.

No LLM calls — uses heuristics: column name patterns, data type
distributions, sheet count, text-vs-table ratios.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from .pipeline_context import ClassificationResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Column name pattern sets for graph structure detection
# ---------------------------------------------------------------------------

SOURCE_PATTERNS: Set[str] = {
    "source", "from", "src", "from_node", "source_node", "source_id",
    "from_id", "parent", "head", "start", "origin",
}

TARGET_PATTERNS: Set[str] = {
    "target", "to", "dst", "to_node", "target_node", "target_id",
    "to_id", "child", "tail", "end", "destination",
}

RELATION_PATTERNS: Set[str] = {
    "relation", "relationship", "edge", "edge_type", "rel", "rel_type",
    "type", "link", "connection", "predicate", "label",
}

ID_PATTERNS: Set[str] = {
    "id", "uuid", "key", "pk", "primary_key", "record_id", "row_id",
}

FK_SUFFIXES = ("_id", "_key", "_ref", "_fk", "_code")


class InputClassifier:
    """Classify extraction results into structure types for pipeline routing."""

    def classify(
        self,
        extraction_result: Dict[str, Any],
        filename: str,
    ) -> ClassificationResult:
        """
        Classify an ExtractionResult dict.

        Args:
            extraction_result: Serialized ExtractionResult (has pages, tables, metadata, etc.)
            filename: Original filename for extension-based hints.

        Returns:
            ClassificationResult with file_type, structure_type, confidence, and details.
        """
        ext = Path(filename).suffix.lower() if filename else ""
        tables = extraction_result.get("tables", [])
        pages = extraction_result.get("pages", [])
        metadata = extraction_result.get("metadata", {})

        file_type = self._detect_file_type(ext, metadata)
        result = ClassificationResult(file_type=file_type)

        if file_type in ("excel", "csv"):
            self._classify_tabular(tables, result)
        elif file_type in ("pdf", "docx", "html", "txt"):
            self._classify_document(tables, pages, result)
        elif file_type == "json":
            self._classify_json(extraction_result, result)
        elif file_type == "graph_file":
            result.structure_type = "graph_format"
            result.confidence = 0.9
        else:
            # Best guess from content
            if tables and not pages:
                self._classify_tabular(tables, result)
            elif pages and not tables:
                result.structure_type = "prose"
                result.confidence = 0.6
            else:
                result.structure_type = "mixed"
                result.confidence = 0.4

        return result

    # ------------------------------------------------------------------
    # File type detection
    # ------------------------------------------------------------------

    def _detect_file_type(self, ext: str, metadata: Dict) -> str:
        meta_type = metadata.get("file_type", "")
        if meta_type:
            return meta_type

        ext_map = {
            ".xlsx": "excel", ".xls": "excel",
            ".csv": "csv", ".tsv": "csv",
            ".pdf": "pdf",
            ".docx": "docx", ".doc": "docx",
            ".txt": "txt", ".md": "txt", ".rtf": "txt",
            ".html": "html", ".htm": "html",
            ".json": "json", ".jsonl": "json",
            ".graphml": "graph_file", ".gml": "graph_file",
            ".rdf": "graph_file", ".owl": "graph_file",
            ".ttl": "graph_file", ".nt": "graph_file", ".nq": "graph_file",
            ".jsonld": "graph_file",
            ".xml": "xml",
        }
        return ext_map.get(ext, "unknown")

    # ------------------------------------------------------------------
    # Tabular classification (Excel, CSV)
    # ------------------------------------------------------------------

    def _classify_tabular(self, tables: List[Dict], result: ClassificationResult):
        if not tables:
            result.structure_type = "flat_records"
            result.confidence = 0.3
            return

        # Analyze each sheet/table
        sheet_analyses = []
        for table in tables:
            headers = [str(h).strip().lower() for h in table.get("headers", [])]
            analysis = self._analyze_headers(headers, table)
            analysis["sheet_name"] = table.get("sheet_name", table.get("id", ""))
            sheet_analyses.append(analysis)

        # Check for explicit graph columns (source/target/relation)
        graph_sheets = [a for a in sheet_analyses if a["has_graph_columns"]]
        if graph_sheets:
            result.structure_type = "graph_columns"
            result.confidence = 0.95
            # Record column roles from the first graph sheet
            best = graph_sheets[0]
            result.column_roles = best.get("column_roles", {})
            result.details["graph_sheets"] = [a["sheet_name"] for a in graph_sheets]
            result.details["analysis"] = sheet_analyses
            return

        # Check for hybrid: multiple sheets with different roles
        if len(tables) > 1:
            node_sheets = [a for a in sheet_analyses if a["looks_like_nodes"]]
            edge_sheets = [a for a in sheet_analyses if a["has_graph_columns"]]
            if node_sheets and edge_sheets:
                result.structure_type = "hybrid_sheets"
                result.confidence = 0.85
                for a in sheet_analyses:
                    if a["has_graph_columns"]:
                        result.sheet_roles[a["sheet_name"]] = "edges"
                    elif a["looks_like_nodes"]:
                        result.sheet_roles[a["sheet_name"]] = "nodes"
                    else:
                        result.sheet_roles[a["sheet_name"]] = "data"
                result.details["analysis"] = sheet_analyses
                return

        # Default: flat records
        result.structure_type = "flat_records"
        result.confidence = 0.7
        if sheet_analyses:
            result.column_roles = sheet_analyses[0].get("column_roles", {})
        result.details["analysis"] = sheet_analyses

    def _analyze_headers(self, headers: List[str], table: Dict) -> Dict[str, Any]:
        """Analyze table headers for graph structure patterns."""
        header_set = set(headers)
        column_roles: Dict[str, str] = {}

        # Find source, target, relation columns
        source_col = self._find_matching_column(headers, SOURCE_PATTERNS)
        target_col = self._find_matching_column(headers, TARGET_PATTERNS)
        relation_col = self._find_matching_column(headers, RELATION_PATTERNS)

        has_graph_columns = source_col is not None and target_col is not None

        if source_col:
            column_roles[source_col] = "source"
        if target_col:
            column_roles[target_col] = "target"
        if relation_col:
            column_roles[relation_col] = "relation"

        # Detect ID column
        id_col = self._find_matching_column(headers, ID_PATTERNS)
        if id_col:
            column_roles[id_col] = "id"

        # Detect FK columns
        fk_cols = [h for h in headers if any(h.endswith(s) for s in FK_SUFFIXES)]
        for fk in fk_cols:
            if fk not in column_roles:
                column_roles[fk] = "foreign_key"

        # Remaining columns are properties
        for h in headers:
            if h not in column_roles:
                column_roles[h] = "property"

        # Does this look like a node list? (has ID + properties, no graph cols)
        looks_like_nodes = id_col is not None and not has_graph_columns

        return {
            "headers": headers,
            "has_graph_columns": has_graph_columns,
            "looks_like_nodes": looks_like_nodes,
            "source_col": source_col,
            "target_col": target_col,
            "relation_col": relation_col,
            "id_col": id_col,
            "fk_cols": fk_cols,
            "column_roles": column_roles,
            "row_count": table.get("row_count", len(table.get("rows", []))),
        }

    def _find_matching_column(self, headers: List[str], patterns: Set[str]) -> Optional[str]:
        """Find the first header that matches any pattern."""
        for h in headers:
            normalized = re.sub(r"[^a-z0-9]", "_", h.strip())
            if normalized in patterns:
                return h
            # Also check without underscores
            compact = normalized.replace("_", "")
            for p in patterns:
                if compact == p.replace("_", ""):
                    return h
        return None

    # ------------------------------------------------------------------
    # Document classification (PDF, DOCX, etc.)
    # ------------------------------------------------------------------

    def _classify_document(self, tables: List[Dict], pages: List[Dict], result: ClassificationResult):
        total_text_chars = sum(len(p.get("text", "")) for p in pages)
        total_table_rows = sum(t.get("row_count", len(t.get("rows", []))) for t in tables)
        table_text_chars = sum(
            sum(len(str(c)) for row in t.get("rows", []) for c in row)
            for t in tables
        )

        total_content = total_text_chars + table_text_chars
        if total_content == 0:
            result.structure_type = "prose"
            result.confidence = 0.3
            return

        table_ratio = table_text_chars / total_content if total_content > 0 else 0

        if table_ratio > 0.6 and total_table_rows > 5:
            result.structure_type = "table_heavy"
            result.confidence = 0.8
            # Also classify the tables
            if tables:
                self._classify_tabular(tables, result)
                # Override structure_type back to table_heavy
                result.details["inner_tabular_type"] = result.structure_type
                result.structure_type = "table_heavy"
        elif table_ratio > 0.2 and tables:
            result.structure_type = "mixed"
            result.confidence = 0.7
        else:
            result.structure_type = "prose"
            result.confidence = 0.85

        result.details["text_chars"] = total_text_chars
        result.details["table_rows"] = total_table_rows
        result.details["table_ratio"] = round(table_ratio, 2)

    # ------------------------------------------------------------------
    # JSON classification
    # ------------------------------------------------------------------

    def _classify_json(self, extraction_result: Dict, result: ClassificationResult):
        """Classify JSON data — could be graph, records, or schema."""
        pages = extraction_result.get("pages", [])
        if not pages:
            result.structure_type = "unknown"
            result.confidence = 0.3
            return

        text = pages[0].get("text", "") if pages else ""

        # Check for JSON-LD
        if any(kw in text for kw in ['"@graph"', '"@context"', '"@id"', '"@type"']):
            result.structure_type = "graph_format"
            result.confidence = 0.85
            result.details["json_subtype"] = "jsonld"
        # Check for graph-like structure
        elif any(kw in text.lower() for kw in ['"nodes"', '"edges"', '"vertices"', '"links"', '"graph"']):
            result.structure_type = "graph_format"
            result.confidence = 0.7
        elif any(kw in text.lower() for kw in ['"schema"', '"node_types"', '"edge_types"', '"namespace"']):
            result.structure_type = "schema"
            result.confidence = 0.8
        else:
            result.structure_type = "flat_records"
            result.confidence = 0.5
