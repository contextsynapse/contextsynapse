"""Runtime Context Assembler — combine multiple atomic contexts on-the-fly.

Each atomic context has its own graph. The runtime context reads from
N graphs and assembles a combined view for analysis — without merging
or duplicating data.

Usage:
    from contextsynapse.intelligence.runtime_context import RuntimeContextAssembler

    assembler = RuntimeContextAssembler(graph_registry)

    # Analyst says: "Show me TCS in context of India Macro + Banking Sector"
    view = assembler.assemble(
        contexts=["TCS", "India Economy", "Banking Sector"],
        focus_entity="TCS",  # optional — what to center the view on
    )

    # view.entities — merged entity list across all contexts
    # view.facts — all facts, tagged with source context
    # view.indicators — all indicators across contexts
    # view.sentiment_comparison — per-entity sentiment across contexts
    # view.cross_correlations — discovered correlations between contexts
    # view.timeline — unified chronological timeline
"""
from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class RuntimeEntity:
    """An entity from an atomic context, tagged with its source."""
    name: str
    label: str
    source_context: str
    sentiment: str = ""
    sentiment_confidence: float = 0.0
    geography: List[str] = field(default_factory=list)
    domain: List[str] = field(default_factory=list)
    impact: str = ""
    confidence: float = 0.0
    relevance: float = 0.0
    properties: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "label": self.label,
            "source_context": self.source_context,
            "sentiment": self.sentiment,
            "sentiment_confidence": self.sentiment_confidence,
            "geography": self.geography,
            "domain": self.domain,
            "impact": self.impact,
            "confidence": self.confidence,
            "relevance": self.relevance,
        }


@dataclass
class RuntimeFact:
    """A fact from an atomic context."""
    statement: str
    source_context: str
    sentiment: str = ""
    created_at: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "statement": self.statement,
            "source_context": self.source_context,
            "sentiment": self.sentiment,
            "created_at": self.created_at,
        }


@dataclass
class RuntimeIndicator:
    """A structured indicator from an atomic context."""
    name: str
    value: float
    unit: str
    period: str
    entity: str
    source_context: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "value": self.value,
            "unit": self.unit,
            "period": self.period,
            "entity": self.entity,
            "source_context": self.source_context,
        }


@dataclass
class RuntimeView:
    """Assembled view across multiple atomic contexts."""
    contexts: List[str] = field(default_factory=list)
    focus_entity: str = ""
    entities: List[RuntimeEntity] = field(default_factory=list)
    facts: List[RuntimeFact] = field(default_factory=list)
    indicators: List[RuntimeIndicator] = field(default_factory=list)
    sentiment_comparison: Dict[str, Dict[str, int]] = field(default_factory=dict)
    timeline: List[Dict[str, Any]] = field(default_factory=list)
    cross_entity_pairs: List[Dict[str, Any]] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)
    assembled_at: str = ""

    def __post_init__(self):
        if not self.assembled_at:
            self.assembled_at = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "contexts": self.contexts,
            "focus_entity": self.focus_entity,
            "entities": [e.to_dict() for e in self.entities],
            "facts": [f.to_dict() for f in self.facts],
            "indicators": [i.to_dict() for i in self.indicators],
            "sentiment_comparison": self.sentiment_comparison,
            "timeline": self.timeline[:100],
            "cross_entity_pairs": self.cross_entity_pairs,
            "stats": self.stats,
            "assembled_at": self.assembled_at,
        }


# Node labels to skip when collecting entities
_SKIP_LABELS = {
    "Document", "Passage", "TextChunk", "Link", "Table", "Image",
    "ContextIntelligence", "ContextUnit", "Chunk",
}


class RuntimeContextAssembler:
    """Assembles views across multiple atomic contexts without merging data."""

    def __init__(self, graph_registry=None, context_manager=None):
        self._graph_registry = graph_registry
        self._context_manager = context_manager
        self._cache: Dict[str, RuntimeView] = {}  # cache key -> view
        self._cache_ttl = 300  # 5 minutes

    def assemble(
        self,
        contexts: List[str],
        focus_entity: str = "",
        days: int = 30,
        max_entities: int = 100,
        max_facts: int = 200,
    ) -> RuntimeView:
        """Assemble a runtime view across multiple atomic contexts.

        Args:
            contexts: List of context names to combine
            focus_entity: Optional entity to center the view on
            days: Look back N days for timeline
            max_entities: Cap entity count
            max_facts: Cap fact count

        Returns RuntimeView with combined data from all contexts.
        """
        cache_key = f"{','.join(sorted(contexts))}:{focus_entity}:{days}"

        # Check cache
        if cache_key in self._cache:
            cached = self._cache[cache_key]
            try:
                cache_age = (datetime.now(timezone.utc) -
                             datetime.fromisoformat(cached.assembled_at.replace("Z", "+00:00")))
                if cache_age.total_seconds() < self._cache_ttl:
                    return cached
            except (ValueError, TypeError):
                pass

        view = RuntimeView(contexts=contexts, focus_entity=focus_entity)
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

        # Collect from each atomic context
        for ctx_name in contexts:
            db = self._get_graph(ctx_name)
            if not db:
                continue

            self._collect_from_context(db, ctx_name, view, cutoff, focus_entity)

        # Sort and cap
        view.entities = view.entities[:max_entities]
        view.facts = sorted(view.facts, key=lambda f: f.created_at or "", reverse=True)[:max_facts]

        # Build sentiment comparison
        view.sentiment_comparison = self._build_sentiment_comparison(view.entities)

        # Build unified timeline
        view.timeline = self._build_timeline(view, cutoff)

        # Find cross-context entity pairs (entities that appear in multiple contexts)
        view.cross_entity_pairs = self._find_cross_context_entities(view.entities)

        # Stats
        view.stats = {
            "contexts_queried": len(contexts),
            "total_entities": len(view.entities),
            "total_facts": len(view.facts),
            "total_indicators": len(view.indicators),
            "cross_context_entities": len(view.cross_entity_pairs),
        }

        # Cache
        self._cache[cache_key] = view

        logger.info("[RUNTIME] Assembled view across %d contexts: %d entities, %d facts, %d indicators",
                     len(contexts), len(view.entities), len(view.facts), len(view.indicators))

        return view

    def _get_graph(self, context_name: str):
        """Get the graph for an atomic context by name."""
        if not self._graph_registry:
            return None

        # Try direct namespace lookup
        ns = context_name.lower().replace(" ", "_")
        db = self._graph_registry.get_graph(ns, load_if_missing=True)
        if db:
            return db

        # Try via context manager
        if self._context_manager:
            try:
                for ctx in self._context_manager.list_contexts(status="active"):
                    if ctx.name.lower() == context_name.lower():
                        return self._graph_registry.get_graph(
                            ctx.graph_namespace, load_if_missing=True)
            except Exception:
                pass

        return None

    def _collect_from_context(self, db, ctx_name: str, view: RuntimeView,
                               cutoff: str, focus_entity: str):
        """Collect entities, facts, and indicators from one atomic context."""
        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            focus_lower = focus_entity.lower() if focus_entity else ""

            for node in adapter.get_all_nodes():
                label = getattr(node, 'label', getattr(node, 'node_type', ''))
                if label in _SKIP_LABELS:
                    continue

                props = getattr(node, 'properties', {}) or {}
                name = props.get('name', '')
                if not name:
                    continue

                created_at = props.get('_created_at', props.get('created_at', ''))

                if label == 'Indicator':
                    view.indicators.append(RuntimeIndicator(
                        name=name,
                        value=props.get('value', 0),
                        unit=props.get('unit', ''),
                        period=props.get('period', ''),
                        entity=props.get('entity', ''),
                        source_context=ctx_name,
                    ))
                elif label == 'Fact':
                    statement = props.get('statement', props.get('name', ''))
                    view.facts.append(RuntimeFact(
                        statement=statement,
                        source_context=ctx_name,
                        sentiment=props.get('_sentiment', ''),
                        created_at=created_at,
                    ))
                else:
                    # Entity — apply focus filter if set
                    entity = RuntimeEntity(
                        name=name,
                        label=label,
                        source_context=ctx_name,
                        sentiment=props.get('_sentiment', ''),
                        sentiment_confidence=props.get('_sentiment_confidence', 0),
                        geography=props.get('_geography', []),
                        domain=props.get('_domain', []),
                        impact=props.get('_impact', ''),
                        confidence=props.get('confidence', 0),
                        relevance=props.get('_amplifier_relevance', 0),
                    )

                    # If focus entity is set, boost relevance for matching entities
                    if focus_lower and focus_lower in name.lower():
                        entity.relevance = max(entity.relevance, 0.9)

                    view.entities.append(entity)

        except Exception as exc:
            logger.warning("[RUNTIME] Failed to collect from '%s': %s", ctx_name, exc)

    def _build_sentiment_comparison(self, entities: List[RuntimeEntity]) -> Dict[str, Dict[str, int]]:
        """Build sentiment counts per entity across all contexts."""
        comparison: Dict[str, Dict[str, int]] = {}

        for ent in entities:
            if not ent.sentiment:
                continue

            key = ent.name
            if key not in comparison:
                comparison[key] = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0, "contexts": []}

            if ent.sentiment in comparison[key]:
                comparison[key][ent.sentiment] += 1
            if ent.source_context not in comparison[key]["contexts"]:
                comparison[key]["contexts"].append(ent.source_context)

        # Add net sentiment
        for key, counts in comparison.items():
            counts["net"] = counts["positive"] - counts["negative"]
            counts["total"] = counts["positive"] + counts["negative"] + counts["neutral"] + counts["mixed"]

        return comparison

    def _build_timeline(self, view: RuntimeView, cutoff: str) -> List[Dict[str, Any]]:
        """Build a unified chronological timeline across all contexts."""
        events = []

        for fact in view.facts:
            if fact.created_at and fact.created_at >= cutoff:
                events.append({
                    "date": fact.created_at[:10],
                    "timestamp": fact.created_at,
                    "type": "fact",
                    "content": fact.statement[:150],
                    "source_context": fact.source_context,
                    "sentiment": fact.sentiment,
                })

        for ind in view.indicators:
            events.append({
                "type": "indicator",
                "content": f"{ind.name}: {ind.value} {ind.unit}",
                "source_context": ind.source_context,
                "entity": ind.entity,
                "period": ind.period,
            })

        events.sort(key=lambda e: e.get("timestamp", e.get("period", "")), reverse=True)
        return events[:100]

    def _find_cross_context_entities(self, entities: List[RuntimeEntity]) -> List[Dict[str, Any]]:
        """Find entities that appear in multiple atomic contexts."""
        entity_contexts: Dict[str, Set[str]] = defaultdict(set)

        for ent in entities:
            entity_contexts[ent.name.lower()].add(ent.source_context)

        pairs = []
        for name_lower, ctxs in entity_contexts.items():
            if len(ctxs) >= 2:
                # Same entity mentioned in multiple contexts — potential correlation point
                matching = [e for e in entities if e.name.lower() == name_lower]
                sentiments = [e.sentiment for e in matching if e.sentiment]
                pairs.append({
                    "entity": matching[0].name,
                    "contexts": list(ctxs),
                    "sentiments": sentiments,
                    "consistent": len(set(sentiments)) <= 1 if sentiments else True,
                })

        return pairs

    def invalidate_cache(self, context_name: str = ""):
        """Invalidate cached views when a context changes."""
        if not context_name:
            self._cache.clear()
            return

        to_remove = [k for k in self._cache if context_name in k]
        for k in to_remove:
            del self._cache[k]
