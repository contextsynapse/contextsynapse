"""BuildEdgesOperator -- create entity-to-entity edges from co-occurrence + schema."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, Set, Tuple, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)


class BuildEdgesOperator(StageOperator):
    name = "build_edges"

    def __init__(self, min_cooccurrence: int = 2):
        self.min_cooccurrence = min_cooccurrence

    def process(self, chunks, graph_ctx):
        # Build edge_type lookup from schema
        schema = getattr(graph_ctx, "compiled_schema", None)
        edge_type_map: Dict[Tuple[str, str], str] = {}
        if schema and hasattr(schema, "edge_types"):
            for edge_name, edge_def in (schema.edge_types.items() if isinstance(schema.edge_types, dict) else []):
                src = getattr(edge_def, "source", "") or (edge_def.get("source", "") if isinstance(edge_def, dict) else "")
                tgt = getattr(edge_def, "target", "") or (edge_def.get("target", "") if isinstance(edge_def, dict) else "")
                if src and tgt:
                    edge_type_map[(src, tgt)] = edge_name

        # Build reverse lookup: entity_node_id -> label
        id_to_label: Dict[str, str] = {}
        for key, nid in graph_ctx.entity_ids.items():
            label = key.split(":")[0]
            id_to_label[nid] = label

        # Count co-occurrences from chunks
        cooccurrence: Dict[Tuple[str, str], int] = defaultdict(int)
        for chunk in chunks:
            eids = chunk.metadata.get("entity_node_ids", [])
            for i in range(len(eids)):
                for j in range(i + 1, len(eids)):
                    pair = tuple(sorted([eids[i], eids[j]]))
                    cooccurrence[pair] += 1

        # Create edges for pairs meeting threshold
        created = 0
        for (id_a, id_b), count in cooccurrence.items():
            if count < self.min_cooccurrence:
                continue
            label_a = id_to_label.get(id_a, "")
            label_b = id_to_label.get(id_b, "")
            if not label_a or not label_b:
                continue

            edge_name = edge_type_map.get((label_a, label_b))
            if edge_name:
                graph_ctx.add_edge(id_a, id_b, edge_name, {"weight": count})
                created += 1
            else:
                edge_name = edge_type_map.get((label_b, label_a))
                if edge_name:
                    graph_ctx.add_edge(id_b, id_a, edge_name, {"weight": count})
                    created += 1
                else:
                    graph_ctx.add_edge(id_a, id_b, "RELATED_TO", {"weight": count})
                    created += 1

        graph_ctx.log(f"{len(cooccurrence)} co-occurring pairs, {created} edges created (min_cooccurrence={self.min_cooccurrence})")
        if created:
            logger.info("build_edges: created %d co-occurrence edges", created)
        return chunks
