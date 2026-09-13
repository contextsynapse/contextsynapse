"""LinkCrossReferenceOperator -- link related signals across chunks."""
from __future__ import annotations

import logging
from typing import Dict, List, Set, Tuple, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

# How many chunks ahead to look for a matching signal
_PROXIMITY_WINDOW = 3


def _signal_label(signal_type: str) -> str:
    """Map signal type to node label: 'problem' -> 'Problem'."""
    return signal_type.capitalize()


def _build_edge_type_map(compiled_schema) -> Dict[Tuple[str, str], str]:
    """Build (source_label, target_label) -> edge_name from schema edge_rules."""
    edge_map: Dict[Tuple[str, str], str] = {}
    if compiled_schema is None:
        return edge_map
    edge_rules = getattr(compiled_schema, "edge_rules", None)
    if not edge_rules:
        return edge_map
    for edge_name, edge_def in edge_rules.items():
        source = getattr(edge_def, "source", None) or edge_def.get("source", "") if isinstance(edge_def, dict) else getattr(edge_def, "source", "")
        target = getattr(edge_def, "target", None) or edge_def.get("target", "") if isinstance(edge_def, dict) else getattr(edge_def, "target", "")
        if source and target:
            edge_map[(source, target)] = edge_name
    return edge_map


def _build_entity_label_map(graph_ctx: "GraphContext") -> Dict[str, str]:
    """Build node_id -> label from entity_ids ('Label:name' -> node_id)."""
    label_map: Dict[str, str] = {}
    for key, node_id in graph_ctx.entity_ids.items():
        label = key.split(":")[0] if ":" in key else ""
        if label:
            label_map[node_id] = label
    return label_map


# ── Hardcoded fallback rules (used when no schema is available) ───────

_FALLBACK_SIGNAL_EDGES: Dict[Tuple[str, str], str] = {
    ("Problem", "Solution"): "SOLVED_BY",
}

_FALLBACK_ENTITY_EDGES: Dict[Tuple[str, str], str] = {
    ("Decision", "*"): "RECOMMENDS",  # Decision -> any entity
}


class LinkCrossReferenceOperator(StageOperator):
    """Find cross-references between chunks and create linking edges.

    When a compiled_schema is available on the GraphContext, edge types are
    read from ``compiled_schema.edge_rules`` and matched by node labels.

    Without a schema, falls back to hardcoded rules:
    - SOLVED_BY: Problem -> Solution (within proximity window)
    - RECOMMENDS: Decision -> any Entity (same chunk)
    """

    name = "link_cross_reference"

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        edge_type_map = _build_edge_type_map(graph_ctx.compiled_schema)

        if edge_type_map:
            self._link_signals_by_proximity(chunks, graph_ctx, edge_type_map)
            self._link_signal_entity(chunks, graph_ctx, edge_type_map)
        else:
            # Fallback to hardcoded behaviour
            self._link_problem_solution_fallback(chunks, graph_ctx)
            self._link_decision_entity_fallback(chunks, graph_ctx)
        return chunks

    # ── Schema-driven linking ─────────────────────────────────────────

    def _link_signals_by_proximity(
        self,
        chunks: List["Chunk"],
        graph_ctx: "GraphContext",
        edge_type_map: Dict[Tuple[str, str], str],
    ) -> None:
        """Link signal nodes across adjacent chunks based on schema edges."""
        created: Set[Tuple[str, str, str]] = set()  # (src, tgt, label) dedup

        for i, chunk in enumerate(chunks):
            signals_i = chunk.metadata.get("signals", [])
            ids_i = chunk.metadata.get("signal_node_ids", [])
            if not signals_i or not ids_i:
                continue

            for j in range(i + 1, min(i + 1 + _PROXIMITY_WINDOW, len(chunks))):
                signals_j = chunks[j].metadata.get("signals", [])
                ids_j = chunks[j].metadata.get("signal_node_ids", [])
                if not signals_j or not ids_j:
                    continue

                for sig_a, nid_a in zip(signals_i, ids_i):
                    label_a = _signal_label(sig_a.get("type", ""))
                    for sig_b, nid_b in zip(signals_j, ids_j):
                        label_b = _signal_label(sig_b.get("type", ""))

                        # Check both directions: A->B and B->A
                        for src_label, tgt_label, src_id, tgt_id in [
                            (label_a, label_b, nid_a, nid_b),
                            (label_b, label_a, nid_b, nid_a),
                        ]:
                            edge_name = edge_type_map.get((src_label, tgt_label))
                            if edge_name and (src_id, tgt_id, edge_name) not in created:
                                graph_ctx.add_edge(src_id, tgt_id, edge_name, {
                                    "distance": j - i,
                                })
                                created.add((src_id, tgt_id, edge_name))

    def _link_signal_entity(
        self,
        chunks: List["Chunk"],
        graph_ctx: "GraphContext",
        edge_type_map: Dict[Tuple[str, str], str],
    ) -> None:
        """Link signal nodes to entity nodes in the same chunk via schema edges."""
        entity_label_map = _build_entity_label_map(graph_ctx)
        created: Set[Tuple[str, str, str]] = set()

        for chunk in chunks:
            signals = chunk.metadata.get("signals", [])
            signal_ids = chunk.metadata.get("signal_node_ids", [])
            entity_ids = chunk.metadata.get("entity_node_ids", [])
            if not signal_ids or not entity_ids:
                continue

            for sig, sid in zip(signals, signal_ids):
                sig_label = _signal_label(sig.get("type", ""))
                for eid in entity_ids:
                    ent_label = entity_label_map.get(eid, "")
                    if not ent_label:
                        continue

                    # Check both directions: signal->entity and entity->signal
                    for src_label, tgt_label, src_id, tgt_id in [
                        (sig_label, ent_label, sid, eid),
                        (ent_label, sig_label, eid, sid),
                    ]:
                        edge_name = edge_type_map.get((src_label, tgt_label))
                        if edge_name and (src_id, tgt_id, edge_name) not in created:
                            graph_ctx.add_edge(src_id, tgt_id, edge_name)
                            created.add((src_id, tgt_id, edge_name))

    # ── Hardcoded fallback (no schema) ────────────────────────────────

    def _link_problem_solution_fallback(
        self, chunks: List["Chunk"], graph_ctx: "GraphContext",
    ) -> None:
        """Find Problem->Solution pairs in adjacent chunks (legacy)."""
        for i, chunk in enumerate(chunks):
            signals = chunk.metadata.get("signals", [])
            problem_ids = [
                nid
                for sig, nid in zip(
                    signals,
                    chunk.metadata.get("signal_node_ids", []),
                )
                if sig.get("type") == "problem"
            ]
            if not problem_ids:
                continue

            for j in range(i + 1, min(i + 1 + _PROXIMITY_WINDOW, len(chunks))):
                future_signals = chunks[j].metadata.get("signals", [])
                future_ids = chunks[j].metadata.get("signal_node_ids", [])

                for sig, nid in zip(future_signals, future_ids):
                    if sig.get("type") == "solution":
                        for pid in problem_ids:
                            graph_ctx.add_edge(pid, nid, "SOLVED_BY", {
                                "distance": j - i,
                            })

    def _link_decision_entity_fallback(
        self, chunks: List["Chunk"], graph_ctx: "GraphContext",
    ) -> None:
        """Link Decision nodes to Entity nodes in the same chunk (legacy)."""
        for chunk in chunks:
            signals = chunk.metadata.get("signals", [])
            signal_ids = chunk.metadata.get("signal_node_ids", [])
            entity_ids = chunk.metadata.get("entity_node_ids", [])

            if not entity_ids:
                continue

            decision_ids = [
                nid
                for sig, nid in zip(signals, signal_ids)
                if sig.get("type") == "decision"
            ]

            for did in decision_ids:
                for eid in entity_ids:
                    graph_ctx.add_edge(did, eid, "RECOMMENDS")
