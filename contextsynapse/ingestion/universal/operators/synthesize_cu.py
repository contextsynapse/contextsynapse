"""SynthesizeCUOperator -- build Context Units from graph nodes.

For SDLC content, creates CUs per layer (intent, design, build, verify, evolution)
with sub-clustering by topic within each layer. Non-SDLC content uses generic
keyword clustering.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

# Node labels that represent infrastructure, not content worth clustering.
_INFRASTRUCTURE_LABELS = frozenset({
    "Session", "Turn", "ContextMeta", "ContextIntelligence",
})

# SDLC layer mapping for layer-aware CU synthesis
from contextsynapse.project.sdlc_schema import _TYPE_TO_LAYER as _SDLC_LAYER_MAP

# Property names to extract text from, in priority order
_TEXT_PROPS = ("statement", "content", "summary", "decision", "description", "what", "name")


def _extract_text(props: Dict[str, Any]) -> str:
    """Extract the best text representation from node properties."""
    for key in _TEXT_PROPS:
        val = props.get(key)
        if val and isinstance(val, str) and len(val.strip()) > 5:
            return val.strip()
    return ""


_DEFAULT_MAX_CUS = 8


class SynthesizeCUOperator(StageOperator):
    """Build Context Units from ingested nodes.

    For SDLC content: creates CUs per layer, then sub-clusters within
    each layer by topic. Each layer gets at least one CU.

    For non-SDLC content: uses generic keyword clustering.

    Args:
        max_cus: Maximum total context units to create (default: 8).
                 Budget is split across layers proportionally.
    """

    name = "synthesize_cu"

    def __init__(self, max_cus: int = _DEFAULT_MAX_CUS):
        self.max_cus = max_cus

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        try:
            from contextsynapse.context.context_units import (
                build_context_unit,
                cluster_facts_for_cus,
            )
        except ImportError:
            logger.debug("synthesize_cu: context_units not available, skipping")
            return chunks

        try:
            # Collect all content nodes grouped by SDLC layer
            layer_facts: Dict[str, List[Dict]] = defaultdict(list)
            generic_facts: List[Dict] = []

            for node_id in graph_ctx.node_ids:
                node_obj = graph_ctx.get_node(node_id)
                if node_obj is None:
                    continue
                label = getattr(node_obj, "label", "")
                if label in _INFRASTRUCTURE_LABELS or label == "ContextUnit":
                    continue
                props = dict(getattr(node_obj, "properties", {}))
                statement = _extract_text(props)
                if not statement:
                    continue

                fact = {"id": node_id, "properties": {"statement": statement}, "_label": label}

                layer = _SDLC_LAYER_MAP.get(label)
                if layer:
                    layer_facts[layer].append(fact)
                else:
                    generic_facts.append(fact)

            cu_count = 0
            has_sdlc = bool(layer_facts)
            max_cus = self.max_cus

            if has_sdlc:
                # ── Budget allocation: split max_cus across layers proportionally ──
                active_layers = {k: v for k, v in layer_facts.items() if v}
                total_facts = sum(len(f) for f in active_layers.values())
                layer_budget = {}
                for layer_name, facts in active_layers.items():
                    # At least 1 CU per layer, proportional share of the rest
                    share = max(1, round(max_cus * len(facts) / total_facts)) if total_facts > 0 else 1
                    layer_budget[layer_name] = share

                # Trim if total budget exceeds max_cus
                while sum(layer_budget.values()) > max_cus and max_cus > 0:
                    # Reduce the layer with the highest budget
                    biggest = max(layer_budget, key=layer_budget.get)
                    if layer_budget[biggest] > 1:
                        layer_budget[biggest] -= 1
                    else:
                        break

                # ── SDLC-aware: CUs per layer within budget ──
                for layer_name, facts in sorted(active_layers.items()):
                    budget = layer_budget.get(layer_name, 1)

                    if budget <= 1 or len(facts) <= 5:
                        # One CU for the whole layer
                        cu = build_context_unit(
                            topic=f"{layer_name.title()} Layer",
                            facts=facts,
                        )
                        if cu:
                            cu_props = cu.get("properties", {})
                            cu_props["sdlc_layer"] = layer_name
                            cu_node_id = graph_ctx.add_node("ContextUnit", cu_props, node_id=cu.get("id", ""))
                            cu_count += 1
                            for eid in cu.get("evidence_ids", []):
                                graph_ctx.add_edge(cu_node_id, eid, "HAS_EVIDENCE")
                    else:
                        # Sub-cluster within budget
                        sub_clusters = cluster_facts_for_cus(facts, min_cluster_size=2, max_clusters=budget)
                        if not sub_clusters:
                            sub_clusters = [{"topic": f"{layer_name.title()} Layer", "facts": facts}]

                        for cluster in sub_clusters[:budget]:
                            topic = f"{layer_name.title()}: {cluster.get('topic', 'General')}"
                            cluster_facts = cluster.get("facts", [])
                            if not cluster_facts:
                                continue
                            cu = build_context_unit(topic=topic, facts=cluster_facts)
                            if cu is None:
                                continue
                            cu_props = cu.get("properties", {})
                            cu_props["sdlc_layer"] = layer_name
                            cu_node_id = graph_ctx.add_node("ContextUnit", cu_props, node_id=cu.get("id", ""))
                            cu_count += 1
                            for eid in cu.get("evidence_ids", []):
                                graph_ctx.add_edge(cu_node_id, eid, "HAS_EVIDENCE")

            else:
                # ── Generic: keyword clustering (non-SDLC content) ──
                all_facts = generic_facts
                if len(all_facts) < 3:
                    return chunks
                clusters = cluster_facts_for_cus(all_facts, max_clusters=max_cus)
                for cluster in clusters[:max_cus]:
                    cu = build_context_unit(topic=cluster.get("topic", "unknown"), facts=cluster.get("facts", []))
                    if cu is None:
                        continue
                    cu_node_id = graph_ctx.add_node("ContextUnit", cu.get("properties", {}), node_id=cu.get("id", ""))
                    cu_count += 1
                    for eid in cu.get("evidence_ids", []):
                        graph_ctx.add_edge(cu_node_id, eid, "HAS_EVIDENCE")

            if cu_count:
                logger.info("synthesize_cu: created %d context units", cu_count)

        except Exception as exc:
            logger.debug("synthesize_cu error: %s", exc)

        return chunks
