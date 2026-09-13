"""Graph Fusion — derive new intelligence by fusing multiple atomic contexts.

Reads from N source contexts, discovers correlations, causal chains,
and patterns, then writes derived edges to a fusion graph.

The fusion graph is itself an atomic context — containing only DERIVED
intelligence, no raw data. It's the "so what?" layer.

Three fusion strategies:
1. Temporal Alignment — match events across contexts by timestamp
2. Causal Chain Detection — A happened then B happened, compute correlation
3. Pattern Mining — recurring patterns across time

Usage:
    from contextsynapse.intelligence.graph_fusion import GraphFusionEngine

    engine = GraphFusionEngine(graph_registry)
    result = engine.fuse(
        source_contexts=["tcs", "india_economy", "it_services_sector"],
        fusion_context="it_sector_fusion",
    )
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class FusedEdge:
    """A derived edge discovered by fusing multiple contexts."""
    edge_type: str          # CORRELATES_WITH | CAUSES | FOLLOWS | COINCIDES_WITH
    source_entity: str
    source_context: str
    target_entity: str
    target_context: str
    correlation: float = 0.0
    lag_minutes: int = 0
    occurrences: int = 0
    confidence: float = 0.0
    evidence: List[str] = field(default_factory=list)
    pattern: str = ""       # description of the pattern

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_type": self.edge_type,
            "source": f"{self.source_entity} ({self.source_context})",
            "target": f"{self.target_entity} ({self.target_context})",
            "correlation": round(self.correlation, 3),
            "lag_minutes": self.lag_minutes,
            "occurrences": self.occurrences,
            "confidence": round(self.confidence, 3),
            "evidence": self.evidence[:5],
            "pattern": self.pattern,
        }


@dataclass
class FusionResult:
    """Result of a graph fusion run."""
    source_contexts: List[str] = field(default_factory=list)
    fusion_context: str = ""
    edges_discovered: int = 0
    temporal_alignments: int = 0
    causal_chains: int = 0
    patterns_found: int = 0
    fused_edges: List[FusedEdge] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source_contexts": self.source_contexts,
            "fusion_context": self.fusion_context,
            "edges_discovered": self.edges_discovered,
            "temporal_alignments": self.temporal_alignments,
            "causal_chains": self.causal_chains,
            "patterns_found": self.patterns_found,
            "fused_edges": [e.to_dict() for e in self.fused_edges],
        }


@dataclass
class ContextEvent:
    """An event extracted from an atomic context for fusion."""
    entity: str
    context: str
    event_type: str         # entity | fact | indicator
    sentiment: str
    value: float = 0.0      # for indicators
    timestamp: str = ""
    date: str = ""          # YYYY-MM-DD
    content: str = ""

    @property
    def datetime(self) -> Optional[datetime]:
        try:
            ts = self.timestamp or self.date
            if ts:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                return dt
        except (ValueError, TypeError):
            pass
        return None


class GraphFusionEngine:
    """Fuses multiple atomic context graphs to discover cross-context intelligence."""

    def __init__(self, graph_registry=None):
        self._registry = graph_registry

    def fuse(
        self,
        source_contexts: List[str],
        fusion_context: str = "",
        time_window_hours: int = 24,
        min_occurrences: int = 2,
        min_confidence: float = 0.5,
    ) -> FusionResult:
        """Run graph fusion across multiple source contexts.

        1. Collect events from all source contexts
        2. Temporally align events across contexts
        3. Detect causal chains (A → B within time window)
        4. Mine recurring patterns
        5. Write derived edges to fusion context

        Args:
            source_contexts: list of graph namespace names
            fusion_context: namespace for the fusion graph (created if needed)
            time_window_hours: max lag between related events
            min_occurrences: minimum times a pattern must occur
            min_confidence: minimum correlation/confidence to create edge
        """
        result = FusionResult(
            source_contexts=source_contexts,
            fusion_context=fusion_context,
        )

        # Step 1: Collect events from all contexts
        all_events = {}  # context -> [ContextEvent]
        for ctx_name in source_contexts:
            events = self._collect_events(ctx_name)
            if events:
                all_events[ctx_name] = events

        if len(all_events) < 2:
            logger.info("[FUSION] Need at least 2 contexts with data, got %d", len(all_events))
            return result

        total_events = sum(len(v) for v in all_events.values())
        logger.info("[FUSION] Collected %d events from %d contexts", total_events, len(all_events))

        # Step 2: Temporal alignment
        alignments = self._temporal_align(all_events, time_window_hours)
        result.temporal_alignments = len(alignments)

        # Step 3: Causal chain detection
        chains = self._detect_causal_chains(alignments, min_occurrences)
        result.causal_chains = len(chains)

        # Step 4: Sentiment co-movement
        co_movements = self._detect_sentiment_co_movement(all_events)

        # Step 5: Cross-context entity correlation
        entity_correlations = self._correlate_entities(all_events, min_confidence)

        # Combine all discovered edges
        all_fused = chains + co_movements + entity_correlations
        result.fused_edges = [e for e in all_fused if e.confidence >= min_confidence]
        result.edges_discovered = len(result.fused_edges)
        result.patterns_found = len(co_movements) + len(entity_correlations)

        # Step 6: Write to fusion graph
        if fusion_context and result.fused_edges:
            self._write_fusion_graph(fusion_context, result.fused_edges)

        logger.info("[FUSION] Discovered %d edges (%d alignments, %d chains, %d patterns)",
                     result.edges_discovered, result.temporal_alignments,
                     result.causal_chains, result.patterns_found)

        return result

    def _collect_events(self, ctx_name: str) -> List[ContextEvent]:
        """Collect all timestamped events from a context graph."""
        if not self._registry:
            return []

        db = self._registry.get_graph(ctx_name, load_if_missing=True)
        if not db:
            return []

        events = []
        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            for node in adapter.get_all_nodes():
                label = getattr(node, 'label', getattr(node, 'node_type', ''))
                props = getattr(node, 'properties', {}) or {}
                name = props.get('name', '')

                _SKIP_LABELS = {'Document', 'Passage', 'ContextIntelligence', 'Chunk', 'Table', 'Image',
                          'ContextUnit', 'BM25Index', 'VectorIndex', 'CategoryNode',
                          'ArtifactStore', 'CodeBase', 'KnowledgeBase', 'ToolStore',
                          'GeneratedStore', 'SessionStore', 'SystemStore', 'UserStore',
                          'WebStore', 'MemoryStore', 'Context', 'ContextRef'}
                if label in _SKIP_LABELS:
                    continue
                if not name:
                    continue

                timestamp = props.get('_published_at', '') or props.get('_created_at', '') or props.get('_event_date', '')
                date = props.get('_event_date', '') or (timestamp[:10] if timestamp else '')

                event = ContextEvent(
                    entity=name,
                    context=ctx_name,
                    event_type='indicator' if label == 'Indicator' else 'fact' if label == 'Fact' else 'entity',
                    sentiment=props.get('_sentiment', ''),
                    value=float(props.get('value', 0)) if label == 'Indicator' else 0,
                    timestamp=timestamp,
                    date=date,
                    content=props.get('statement', props.get('description', name))[:200],
                )
                events.append(event)
        except Exception as exc:
            logger.warning("[FUSION] Error collecting from %s: %s", ctx_name, exc)

        return events

    def _temporal_align(
        self, all_events: Dict[str, List[ContextEvent]], window_hours: int,
    ) -> List[Tuple[ContextEvent, ContextEvent]]:
        """Find events across different contexts that occurred within the time window."""
        alignments = []
        contexts = list(all_events.keys())

        for i, ctx_a in enumerate(contexts):
            for ctx_b in contexts[i + 1:]:
                for event_a in all_events[ctx_a]:
                    dt_a = event_a.datetime
                    if not dt_a:
                        # Try date-only comparison
                        if not event_a.date:
                            continue

                    for event_b in all_events[ctx_b]:
                        if dt_a:
                            dt_b = event_b.datetime
                            if not dt_b:
                                continue
                            # Ensure both are timezone-aware
                            if dt_a.tzinfo is None:
                                dt_a = dt_a.replace(tzinfo=timezone.utc)
                            if dt_b.tzinfo is None:
                                dt_b = dt_b.replace(tzinfo=timezone.utc)
                            delta = abs((dt_a - dt_b).total_seconds())
                            if delta <= window_hours * 3600:
                                alignments.append((event_a, event_b))
                        elif event_a.date and event_a.date == event_b.date:
                            alignments.append((event_a, event_b))

        return alignments

    def _detect_causal_chains(
        self, alignments: List[Tuple[ContextEvent, ContextEvent]], min_occurrences: int,
    ) -> List[FusedEdge]:
        """Detect causal chains: entity A event followed by entity B event."""
        # Count how often entity pairs co-occur in time-aligned events
        pair_counts: Dict[str, Dict] = defaultdict(lambda: {"count": 0, "sentiments": [], "evidence": [], "lags": []})

        for event_a, event_b in alignments:
            key = f"{event_a.entity}|{event_a.context}→{event_b.entity}|{event_b.context}"

            dt_a = event_a.datetime
            dt_b = event_b.datetime
            lag = 0
            if dt_a and dt_b:
                lag = int((dt_b - dt_a).total_seconds() / 60)

            pair_counts[key]["count"] += 1
            pair_counts[key]["lags"].append(lag)
            pair_counts[key]["evidence"].append(f"{event_a.content[:50]} → {event_b.content[:50]}")

            if event_a.sentiment and event_b.sentiment:
                pair_counts[key]["sentiments"].append((event_a.sentiment, event_b.sentiment))

        chains = []
        for key, data in pair_counts.items():
            if data["count"] < min_occurrences:
                continue

            parts = key.split("→")
            src_parts = parts[0].split("|")
            tgt_parts = parts[1].split("|")

            avg_lag = sum(data["lags"]) / len(data["lags"]) if data["lags"] else 0
            confidence = min(1.0, data["count"] / 5.0)  # saturates at 5 occurrences

            # Check if sentiments align
            if data["sentiments"]:
                same_sent = sum(1 for a, b in data["sentiments"] if a == b)
                sent_alignment = same_sent / len(data["sentiments"])
                confidence *= (0.5 + 0.5 * sent_alignment)

            chains.append(FusedEdge(
                edge_type="FOLLOWS" if avg_lag > 0 else "COINCIDES_WITH",
                source_entity=src_parts[0],
                source_context=src_parts[1] if len(src_parts) > 1 else "",
                target_entity=tgt_parts[0],
                target_context=tgt_parts[1] if len(tgt_parts) > 1 else "",
                lag_minutes=int(avg_lag),
                occurrences=data["count"],
                confidence=confidence,
                evidence=data["evidence"][:5],
                pattern=f"{src_parts[0]} events in {src_parts[1] if len(src_parts) > 1 else '?'} followed by {tgt_parts[0]} events in {tgt_parts[1] if len(tgt_parts) > 1 else '?'} ({data['count']} times, avg lag {int(avg_lag)}min)",
            ))

        return chains

    def _detect_sentiment_co_movement(
        self, all_events: Dict[str, List[ContextEvent]],
    ) -> List[FusedEdge]:
        """Detect when sentiment in one context correlates with another."""
        edges = []
        contexts = list(all_events.keys())

        for i, ctx_a in enumerate(contexts):
            for ctx_b in contexts[i + 1:]:
                # Get sentiment distributions per date
                sent_a = self._daily_sentiment(all_events[ctx_a])
                sent_b = self._daily_sentiment(all_events[ctx_b])

                # Find overlapping dates
                common_dates = set(sent_a.keys()) & set(sent_b.keys())
                if len(common_dates) < 2:
                    continue

                # Compute correlation of sentiment scores
                scores_a = [sent_a[d] for d in sorted(common_dates)]
                scores_b = [sent_b[d] for d in sorted(common_dates)]

                corr = self._pearson(scores_a, scores_b)

                if abs(corr) >= 0.5:
                    direction = "positive" if corr > 0 else "negative"
                    edges.append(FusedEdge(
                        edge_type="CORRELATES_WITH",
                        source_entity=ctx_a,
                        source_context=ctx_a,
                        target_entity=ctx_b,
                        target_context=ctx_b,
                        correlation=corr,
                        occurrences=len(common_dates),
                        confidence=min(1.0, abs(corr) * (len(common_dates) / 5)),
                        pattern=f"Sentiment in {ctx_a} {direction}ly correlates with {ctx_b} (r={corr:.2f}, {len(common_dates)} data points)",
                    ))

        return edges

    def _correlate_entities(
        self, all_events: Dict[str, List[ContextEvent]], min_confidence: float,
    ) -> List[FusedEdge]:
        """Find entities that appear across multiple contexts — cross-context significance."""
        entity_contexts: Dict[str, List[Tuple[str, str, str]]] = defaultdict(list)  # entity -> [(context, sentiment, date)]

        for ctx_name, events in all_events.items():
            for event in events:
                if event.event_type == 'entity':
                    entity_contexts[event.entity.lower()].append((ctx_name, event.sentiment, event.date))

        edges = []
        for entity, appearances in entity_contexts.items():
            contexts_seen = set(ctx for ctx, _, _ in appearances)
            if len(contexts_seen) < 2:
                continue

            # This entity appears in multiple contexts — it's a bridge entity
            sentiments = [s for _, s, _ in appearances if s]
            dominant_sentiment = max(set(sentiments), key=sentiments.count) if sentiments else "unknown"
            consistent = len(set(sentiments)) <= 1 if sentiments else True

            ctx_list = list(contexts_seen)
            edges.append(FusedEdge(
                edge_type="BRIDGE_ENTITY",
                source_entity=entity,
                source_context=ctx_list[0],
                target_entity=entity,
                target_context=ctx_list[1] if len(ctx_list) > 1 else ctx_list[0],
                occurrences=len(appearances),
                confidence=min(1.0, len(appearances) / 3.0),
                pattern=f"'{entity}' appears in {len(contexts_seen)} contexts ({', '.join(contexts_seen)}), sentiment: {dominant_sentiment} ({'consistent' if consistent else 'conflicting'})",
                evidence=[f"{ctx}: {sent}" for ctx, sent, _ in appearances[:5]],
            ))

        return edges

    def _daily_sentiment(self, events: List[ContextEvent]) -> Dict[str, float]:
        """Aggregate sentiment by date. Returns {date: score} where positive=1, negative=-1."""
        daily: Dict[str, List[float]] = defaultdict(list)
        sent_map = {"positive": 1.0, "negative": -1.0, "neutral": 0.0, "mixed": 0.0}

        for event in events:
            if event.date and event.sentiment in sent_map:
                daily[event.date].append(sent_map[event.sentiment])

        return {d: sum(scores) / len(scores) for d, scores in daily.items() if scores}

    def _pearson(self, x: List[float], y: List[float]) -> float:
        """Pearson correlation coefficient."""
        n = len(x)
        if n < 3:
            return 0.0
        mx, my = sum(x) / n, sum(y) / n
        cov = sum((xi - mx) * (yi - my) for xi, yi in zip(x, y))
        sx = math.sqrt(sum((xi - mx) ** 2 for xi in x))
        sy = math.sqrt(sum((yi - my) ** 2 for yi in y))
        if sx == 0 or sy == 0:
            return 0.0
        return cov / (sx * sy)

    def _write_fusion_graph(self, fusion_context: str, edges: List[FusedEdge]):
        """Write discovered edges to the fusion graph."""
        if not self._registry:
            return

        db = self._registry.get_graph(fusion_context, load_if_missing=True)
        if not db:
            db = self._registry.create_graph(fusion_context)

        from ..core.graph_structures import GraphNode, GraphEdge
        import uuid

        now = datetime.now(timezone.utc).isoformat()

        for edge in edges:
            # Create edge node (since source/target may be in different graphs)
            node_id = f"fusion_{uuid.uuid4().hex[:10]}"
            db.add_node(GraphNode(
                id=node_id,
                label="FusedInsight",
                properties={
                    "name": edge.pattern[:80],
                    "edge_type": edge.edge_type,
                    "source_entity": edge.source_entity,
                    "source_context": edge.source_context,
                    "target_entity": edge.target_entity,
                    "target_context": edge.target_context,
                    "correlation": edge.correlation,
                    "lag_minutes": edge.lag_minutes,
                    "occurrences": edge.occurrences,
                    "confidence": edge.confidence,
                    "pattern": edge.pattern,
                    "evidence": edge.evidence[:5],
                    "computed_at": now,
                },
            ))

        logger.info("[FUSION] Wrote %d FusedInsight nodes to '%s'", len(edges), fusion_context)
