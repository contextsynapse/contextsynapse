"""InferDomainsOperator -- cluster entities into Domain nodes."""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Dict, List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

_MIN_CLUSTER_SIZE = 3


class InferDomainsOperator(StageOperator):
    name = "infer_domains"

    def process(self, chunks, graph_ctx):
        # Group entities by label
        label_groups = defaultdict(list)
        for key, node_id in graph_ctx.entity_ids.items():
            parts = key.split(":", 1)
            if len(parts) == 2:
                label, name = parts
                label_groups[label].append((name, node_id))

        created = 0
        for label, entities in label_groups.items():
            if len(entities) < _MIN_CLUSTER_SIZE:
                continue

            entity_names = [name for name, _ in entities]
            domain_name = self._infer_name(label, entity_names)

            domain_id = graph_ctx.add_node("Domain", {
                "name": domain_name,
                "entity_label": label,
                "entity_count": len(entities),
            })

            for _, node_id in entities:
                graph_ctx.add_edge(node_id, domain_id, "BELONGS_TO")

            created += 1

        if created:
            logger.info("infer_domains: created %d domain nodes", created)
        return chunks

    def _infer_name(self, label, entity_names):
        """Try local LLM, fall back to label plural."""
        try:
            from contextsynapse.llm import get_llm_client
            llm = get_llm_client(provider="ollama")
            names_str = ", ".join(entity_names[:10])
            response = llm.chat(
                messages=[{"role": "user", "content": f"These are {label} entities: {names_str}. What domain? Reply with 2-4 words only."}],
                max_tokens=20,
            )
            name = response.get("content", "").strip().strip('"\'')
            if name and len(name) < 50:
                return name
        except Exception:
            pass
        return f"{label}s"
