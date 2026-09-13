"""
Scenario Router
===============
Maps (input classification, user intent) to an ordered list of pipeline stages.

This is the decision table that replaces manual pipeline selection.
Given what the user uploaded and what they want, return the best stage sequence.
"""

from __future__ import annotations

import logging
from typing import List, Optional

from .pipeline_context import ClassificationResult

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# User intent enum values
# ---------------------------------------------------------------------------

INTENT_BUILD_GRAPH = "build_graph"
INTENT_SEARCH_ONLY = "search_only"
INTENT_GRAPH_RAG = "graph_rag"

# ---------------------------------------------------------------------------
# Stage names (canonical)
# ---------------------------------------------------------------------------

STAGE_PARSE_FILE = "PARSE_FILE"
STAGE_CLASSIFY = "CLASSIFY"
STAGE_CHUNK = "CHUNK"
STAGE_DEDUP = "DEDUP"
STAGE_MAP_TABULAR = "MAP_TABULAR"
STAGE_EXTRACT = "EXTRACT"
STAGE_EXTRACT_FACTS = "EXTRACT_FACTS"
STAGE_EMBED = "EMBED"
STAGE_INDEX_BM25 = "INDEX_BM25"
STAGE_STORE_VECTORS = "STORE_VECTORS"
STAGE_CANONICALIZE = "CANONICALIZE"
STAGE_ENHANCE = "ENHANCE_GRAPH"
STAGE_PERSIST = "PERSIST"
STAGE_IMPORT_GRAPH = "IMPORT_GRAPH"
STAGE_VALIDATE_SCHEMA = "VALIDATE_SCHEMA"
STAGE_SDLC_SCAN = "SDLC_SCAN"
# SDLC sub-stages (resumable)
STAGE_SDLC_SCAN_FILES = "SDLC_SCAN_FILES"
STAGE_SDLC_SCAN_GITHUB = "SDLC_SCAN_GITHUB"
STAGE_SDLC_SCAN_GIT = "SDLC_SCAN_GIT"
STAGE_SDLC_SCAN_EDGES = "SDLC_SCAN_EDGES"
STAGE_SDLC_SCAN_CU = "SDLC_SCAN_CU"


class ScenarioRouter:
    """
    Route (classification, intent) to a pipeline stage sequence.

    The decision table encodes the 6 pipeline archetypes:
    1. Direct Structured Graph (graph_columns)
    2. Record-to-Graph (flat_records)
    3. Semantic Text Graph (prose)
    4. Multi-Source Hybrid (mixed)
    5. Graph Import (graph_format)
    6. Schema-Driven (schema)
    """

    def route(
        self,
        classification: ClassificationResult,
        intent: str = INTENT_BUILD_GRAPH,
    ) -> List[str]:
        """
        Return ordered list of stage names for this scenario.

        Args:
            classification: Result from InputClassifier.
            intent: What the user wants (build_graph, search_only, graph_rag, analytics).

        Returns:
            List of stage name strings.
        """
        structure = classification.structure_type
        method = self._routes.get(structure, self._route_default)
        stages = method(self, intent)
        logger.info(
            "Routed structure=%s intent=%s → stages=%s",
            structure, intent, stages,
        )
        return stages

    # ------------------------------------------------------------------
    # Route methods per structure type
    # ------------------------------------------------------------------

    def _route_graph_columns(self, intent: str) -> List[str]:
        """Excel/CSV with explicit source/target/relation. No LLM needed."""
        base = [STAGE_MAP_TABULAR, STAGE_PERSIST]
        if intent == INTENT_GRAPH_RAG:
            base.insert(-1, STAGE_EMBED)
        return base

    def _route_flat_records(self, intent: str) -> List[str]:
        """Excel/CSV flat rows — derive entities and relationships."""
        if intent == INTENT_SEARCH_ONLY:
            return [STAGE_MAP_TABULAR, STAGE_EMBED, STAGE_PERSIST]
        elif intent == INTENT_GRAPH_RAG:
            return [STAGE_MAP_TABULAR, STAGE_CANONICALIZE, STAGE_EMBED, STAGE_PERSIST]
        else:
            return [STAGE_MAP_TABULAR, STAGE_CANONICALIZE, STAGE_PERSIST]

    def _route_hybrid_sheets(self, intent: str) -> List[str]:
        """Multiple sheets with different roles."""
        base = [STAGE_MAP_TABULAR, STAGE_CANONICALIZE, STAGE_PERSIST]
        if intent in (INTENT_GRAPH_RAG, INTENT_SEARCH_ONLY):
            base.insert(-1, STAGE_EMBED)
        return base

    def _route_table_heavy(self, intent: str) -> List[str]:
        """PDF/doc dominated by tables — extract tables as facts."""
        base = [STAGE_MAP_TABULAR, STAGE_EXTRACT, STAGE_PERSIST]
        if intent in (INTENT_GRAPH_RAG, INTENT_SEARCH_ONLY):
            base.insert(-1, STAGE_EMBED)
        return base

    def _route_prose(self, intent: str) -> List[str]:
        """Prose text — the classic chunk → extract → embed path.

        For graph_rag: full multi-layer extraction with facts, BM25, and vectors.
        For build_graph: entity extraction + fact decomposition + BM25.
        For search_only: just chunk and embed for vector search.
        """
        if intent == INTENT_SEARCH_ONLY:
            return [STAGE_CHUNK, STAGE_DEDUP, STAGE_EMBED, STAGE_STORE_VECTORS, STAGE_PERSIST]
        elif intent == INTENT_GRAPH_RAG:
            return [
                STAGE_CHUNK, STAGE_DEDUP, STAGE_EXTRACT_FACTS, STAGE_EXTRACT,
                STAGE_EMBED, STAGE_INDEX_BM25, STAGE_STORE_VECTORS,
                STAGE_CANONICALIZE, STAGE_ENHANCE, STAGE_PERSIST,
            ]
        else:
            # build_graph: extract entities + facts, BM25 index, no vector storage
            return [
                STAGE_CHUNK, STAGE_DEDUP, STAGE_EXTRACT, STAGE_EXTRACT_FACTS,
                STAGE_INDEX_BM25, STAGE_CANONICALIZE, STAGE_ENHANCE, STAGE_PERSIST,
            ]

    def _route_mixed(self, intent: str) -> List[str]:
        """Mixed text and tables."""
        if intent == INTENT_GRAPH_RAG:
            return [
                STAGE_CHUNK, STAGE_DEDUP, STAGE_MAP_TABULAR, STAGE_EXTRACT, STAGE_EXTRACT_FACTS,
                STAGE_EMBED, STAGE_INDEX_BM25, STAGE_STORE_VECTORS,
                STAGE_CANONICALIZE, STAGE_PERSIST,
            ]
        base = [STAGE_CHUNK, STAGE_DEDUP, STAGE_MAP_TABULAR, STAGE_EXTRACT, STAGE_CANONICALIZE, STAGE_PERSIST]
        if intent == INTENT_SEARCH_ONLY:
            base.insert(-1, STAGE_EMBED)
        return base

    def _route_graph_format(self, intent: str) -> List[str]:
        """Already a graph file — import and optionally enrich."""
        base = [STAGE_IMPORT_GRAPH, STAGE_PERSIST]
        if intent == INTENT_GRAPH_RAG:
            base.insert(-1, STAGE_EMBED)
        return base

    def _route_schema(self, intent: str) -> List[str]:
        """Schema-driven ingestion — validate against schema after extraction."""
        if intent == INTENT_BUILD_GRAPH:
            return [STAGE_CHUNK, STAGE_DEDUP, STAGE_EXTRACT, STAGE_VALIDATE_SCHEMA, STAGE_PERSIST]
        elif intent == INTENT_GRAPH_RAG:
            return [STAGE_CHUNK, STAGE_DEDUP, STAGE_EXTRACT, STAGE_VALIDATE_SCHEMA, STAGE_EMBED, STAGE_PERSIST]
        else:
            return [STAGE_PERSIST]

    def _route_software_dev(self, intent: str) -> List[str]:
        """Repository source code — scan into SDLC graph.

        BM25 indexing and embedding are run inline inside the SDLC_SCAN handler
        (same pattern as ingest_sdlc_nodes), so no separate INDEX_BM25 or EMBED
        stages are needed here.
        """
        return [STAGE_SDLC_SCAN]

    def _route_default(self, intent: str) -> List[str]:
        """Fallback: treat as prose."""
        return self._route_prose(intent)

    # Dispatch table
    _routes = {
        "graph_columns": _route_graph_columns,
        "flat_records": _route_flat_records,
        "hybrid_sheets": _route_hybrid_sheets,
        "table_heavy": _route_table_heavy,
        "prose": _route_prose,
        "mixed": _route_mixed,
        "graph_format": _route_graph_format,
        "schema": _route_schema,
        "software_dev": _route_software_dev,
    }

    # ------------------------------------------------------------------
    # Auto-route: classify + route in one call
    # ------------------------------------------------------------------

    def auto_route(
        self,
        extraction_result: dict,
        filename: str,
        intent: str = INTENT_BUILD_GRAPH,
    ) -> tuple:
        """
        Convenience: classify then route.

        Returns:
            (classification, stages) tuple.
        """
        from .input_classifier import InputClassifier
        classifier = InputClassifier()
        classification = classifier.classify(extraction_result, filename)
        stages = self.route(classification, intent)
        return classification, stages
