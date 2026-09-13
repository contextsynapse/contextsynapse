"""StageExecutor -- runs an ordered list of StageOperators over chunks."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from ...core.graph_structures import GraphNode, GraphEdge
from ...ingestion.graph_builder import BuildResult
from .ingest_content import IngestContent, Chunk

logger = logging.getLogger(__name__)


def _make_id(prefix: str = "n") -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


# ---------------------------------------------------------------------------
# GraphContext — mutable accumulator for graph writes during a pipeline run
# ---------------------------------------------------------------------------

class GraphContext:
    """Accumulates graph writes (nodes + edges) during stage execution."""

    def __init__(
        self,
        db,
        namespace: str = "",
        compiled_schema=None,
        debug: bool = False,
    ):
        self.db = db
        self.namespace = namespace
        self.compiled_schema = compiled_schema
        self.debug = debug

        # Tracking
        self.entity_ids: Dict[str, str] = {}   # "Label:name_lower" -> node_id
        self.node_ids: List[str] = []
        self.edge_count: int = 0
        self.session_id: str = ""
        self.errors: List[str] = []

        # Logging
        self.log_entries: List[Dict[str, Any]] = []
        self.stage_metrics: Dict[str, Dict[str, Any]] = {}
        self._active_stage: str = ""

    # -- logging helpers -----------------------------------------------------

    def log(self, message: str, details: Dict[str, Any] = None):
        """Add an info-level log entry."""
        from datetime import datetime, timezone
        self.log_entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": self._active_stage,
            "message": message,
            "level": "info",
            "details": details or {},
        })

    def log_debug(self, message: str, details: Dict[str, Any] = None):
        """Add a debug-level log entry. No-op when debug=False."""
        if not self.debug:
            return
        from datetime import datetime, timezone
        self.log_entries.append({
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "stage": self._active_stage,
            "message": message,
            "level": "debug",
            "details": details or {},
        })

    def stage_complete(self, items: int = 0, nodes_created: int = 0, edges_created: int = 0):
        """Record metrics for the current stage."""
        self.stage_metrics[self._active_stage] = {
            "items": items,
            "nodes_created": nodes_created,
            "edges_created": edges_created,
        }

    # -- write helpers -------------------------------------------------------

    def add_node(self, label: str, properties: Dict[str, Any], node_id: str = "") -> str:
        """Create a node in the graph and track it."""
        if not node_id:
            node_id = _make_id(label.lower())
        try:
            node = GraphNode(id=node_id, label=label, properties=dict(properties))
            self.db.add_node(node)
            self.node_ids.append(node_id)

            # Auto-register in entity_ids if the node has a name
            name = properties.get("name", "")
            if name:
                key = f"{label}:{name.strip().lower()}"
                self.entity_ids[key] = node_id
        except Exception as exc:  # pragma: no cover
            self.errors.append(f"add_node({label}): {exc}")
        return node_id

    def add_edge(
        self,
        source_id: str,
        target_id: str,
        label: str,
        properties: Optional[Dict[str, Any]] = None,
    ) -> str:
        """Create an edge in the graph."""
        edge_id = _make_id("e")
        try:
            edge = GraphEdge(
                id=edge_id,
                source=source_id,
                target=target_id,
                label=label,
                properties=dict(properties or {}),
            )
            self.db.add_edge(edge)
            self.edge_count += 1
        except Exception as exc:  # pragma: no cover
            self.errors.append(f"add_edge({label}): {exc}")
        return edge_id

    def get_node(self, node_id: str):
        """Read a node back from the graph."""
        return self.db.get_node(node_id)

    def find_entity(self, label: str, name: str) -> Optional[str]:
        """Look up a previously-created entity by label+name."""
        key = f"{label}:{name.strip().lower()}"
        return self.entity_ids.get(key)

    # -- result builder ------------------------------------------------------

    def build_result(self) -> BuildResult:
        return BuildResult(
            entity_ids=dict(self.entity_ids),
            edge_count=self.edge_count,
            errors=list(self.errors),
        )


# ---------------------------------------------------------------------------
# StageExecutor — runs operators in sequence
# ---------------------------------------------------------------------------

class StageExecutor:
    """Execute an ordered list of StageOperators against chunks."""

    def __init__(
        self,
        operators: list,
        db,
        namespace: str = "",
        compiled_schema=None,
    ):
        self.operators = list(operators)
        self.db = db
        self.namespace = namespace
        self.compiled_schema = compiled_schema

    def execute(self, content: IngestContent, chunks: List[Chunk]) -> BuildResult:
        """Run all operators in order, returning the accumulated BuildResult."""
        graph_ctx = GraphContext(
            db=self.db,
            namespace=self.namespace,
            compiled_schema=self.compiled_schema,
        )

        # If the content is a conversation, create a Session root node
        if getattr(content, "content_type", "") in ("conversation", "chat", "dialogue"):
            session_id = _make_id("session")
            props = {"title": content.title or content.source, "source": content.source}
            graph_ctx.add_node("Session", props, node_id=session_id)
            graph_ctx.session_id = session_id

        # Build turn chain for conversations
        if content.content_type == "conversation" and chunks:
            prev_turn_id = None
            for chunk in chunks:
                if chunk.chunk_type != "turn":
                    continue
                turn_id = graph_ctx.add_node("Turn", {
                    "role": chunk.metadata.get("role", ""),
                    "content": chunk.content[:500],
                    "turn_index": chunk.metadata.get("turn_index", chunk.index),
                    "significance": 0.5,
                })
                chunk.metadata["turn_node_id"] = turn_id
                if graph_ctx.session_id:
                    graph_ctx.add_edge(graph_ctx.session_id, turn_id, "CONTAINS_TURN")
                if prev_turn_id:
                    graph_ctx.add_edge(prev_turn_id, turn_id, "NEXT")
                prev_turn_id = turn_id

        # Run each operator with timing
        import time as _time
        for op in self.operators:
            graph_ctx._active_stage = op.name
            t0 = _time.time()
            try:
                chunks = op.process(chunks, graph_ctx)
            except Exception as exc:
                name = getattr(op, "name", op.__class__.__name__)
                graph_ctx.errors.append(f"operator {name}: {exc}")
                logger.error("Stage operator %s failed: %s", name, exc, exc_info=True)
            elapsed_ms = int((_time.time() - t0) * 1000)
            graph_ctx.stage_metrics[op.name] = {
                **graph_ctx.stage_metrics.get(op.name, {}),
                "duration_ms": elapsed_ms,
            }
            if elapsed_ms > 100:
                logger.info("[PIPELINE] %s: %dms", op.name, elapsed_ms)

        return graph_ctx.build_result()
