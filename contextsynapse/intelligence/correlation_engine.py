"""Correlation Engine — template-driven correlation computation.

The engine is generic — it doesn't know about stocks, geopolitics, or any
specific domain. It reads templates that define what to watch and compute.

Two modes:
1. On-ingest: lightweight signal checks (sentiment reversal, threshold, co-occurrence)
2. Scheduled: deep correlation computations (entity vs indicator, cross-entity, geo-cluster)

Usage:
    from contextsynapse.intelligence.correlation_engine import CorrelationEngine

    engine = CorrelationEngine()
    engine.register_template("market_analysis")

    # On ingest
    signals = engine.process_ingest(entities, template, sentiment_history=history)

    # Scheduled (background job)
    result = engine.run_scheduled(template, db)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from .correlation_template import CorrelationTemplate, load_template
from .signals import Signal, check_sentiment_reversal, check_threshold_breach, check_co_occurrence

logger = logging.getLogger(__name__)


@dataclass
class CorrelationRunResult:
    """Result of a scheduled correlation run."""
    correlations_created: int = 0
    correlations_updated: int = 0
    correlations_expired: int = 0
    signals_fired: int = 0
    details: Dict[str, Any] = field(default_factory=dict)


class CorrelationEngine:
    """Template-driven correlation engine."""

    def __init__(self):
        self._templates: Dict[str, CorrelationTemplate] = {}

    def register_template(self, name: str) -> bool:
        """Load and register a template by name from the templates directory."""
        template = load_template(name)
        if template:
            self._templates[name] = template
            logger.info("[CORR] Registered template: %s", name)
            return True
        return False

    def register_template_object(self, template: CorrelationTemplate):
        """Register a template object directly."""
        self._templates[template.name] = template

    def get_template(self, name: str) -> Optional[CorrelationTemplate]:
        """Get a registered template by name."""
        return self._templates.get(name)

    def list_registered(self) -> List[str]:
        """List names of registered templates."""
        return list(self._templates.keys())

    def process_ingest(
        self,
        entities: List[Dict[str, Any]],
        template: CorrelationTemplate,
        sentiment_history: Optional[Dict[str, List[Dict]]] = None,
        co_occurrence_counts: Optional[Dict[str, int]] = None,
    ) -> List[Signal]:
        """Process newly ingested entities against template signals.

        Args:
            entities: List of entity dicts with "name", "label", "_sentiment"
            template: The correlation template defining what signals to check
            sentiment_history: Per-entity history: {"Tesla": [{"sentiment": "positive", "timestamp": "..."}]}
            co_occurrence_counts: Per entity-pair co-occurrence counts: {"Tesla:NVIDIA": 5}

        Returns list of fired Signals.
        """
        if not entities or not template.signals:
            return []

        sentiment_history = sentiment_history or {}
        co_occurrence_counts = co_occurrence_counts or {}
        fired: List[Signal] = []

        # Filter entities by template entity_types
        allowed_types = set(template.entity_types)
        relevant = [e for e in entities if e.get("label", "") in allowed_types]

        for signal_config in template.signals:
            sig_type = signal_config.type

            if sig_type == "sentiment_reversal":
                for entity in relevant:
                    name = entity.get("name", "")
                    sentiment = entity.get("_sentiment", "neutral")
                    history = sentiment_history.get(name, [])
                    signal = check_sentiment_reversal(name, sentiment, history, signal_config.config)
                    if signal:
                        fired.append(signal)

            elif sig_type == "threshold_breach":
                for entity in relevant:
                    name = entity.get("name", "")
                    history = sentiment_history.get(name, [])
                    signal = check_threshold_breach(name, history, signal_config.config)
                    if signal:
                        fired.append(signal)

            elif sig_type == "co_occurrence":
                # Check all entity pairs
                entity_names = [e.get("name", "") for e in relevant if e.get("name")]
                for pair_key, count in co_occurrence_counts.items():
                    parts = pair_key.split(":", 1)
                    if len(parts) == 2:
                        a, b = parts
                        if a in entity_names or b in entity_names:
                            signal = check_co_occurrence(a, b, count, signal_config.config)
                            if signal:
                                fired.append(signal)

        if fired:
            logger.info("[CORR] %d signals fired for template '%s'", len(fired), template.name)

        return fired

    def run_scheduled(
        self,
        template: CorrelationTemplate,
        db=None,
    ) -> CorrelationRunResult:
        """Run scheduled correlation computations.

        Queries the graph for sentiment time-series, computes correlations,
        and creates/updates CORRELATES_WITH edges.
        """
        result = CorrelationRunResult()

        if not template.correlations or not db:
            return result

        # Collect entity sentiment time-series from graph
        sentiment_series = self._build_sentiment_series(db, template)

        for corr_config in template.correlations:
            corr_type = corr_config.type
            config = corr_config.config
            min_samples = template.quality.min_samples
            min_confidence = template.quality.min_confidence

            try:
                if corr_type == "cross_entity":
                    found = self._compute_cross_entity(
                        sentiment_series, config, min_samples, min_confidence, db, template,
                    )
                    result.correlations_created += found
                    result.details[corr_type] = {"correlations_found": found}

                elif corr_type == "entity_vs_indicator":
                    found = self._compute_entity_vs_indicator(
                        sentiment_series, db, config, min_samples, min_confidence, template,
                    )
                    result.correlations_created += found
                    result.details[corr_type] = {"correlations_found": found}

                elif corr_type == "geo_sentiment_cluster":
                    found = self._compute_geo_sentiment(
                        db, config, template,
                    )
                    result.details[corr_type] = {"clusters_found": found}

            except Exception as exc:
                logger.warning("[CORR] %s failed: %s", corr_type, exc)
                result.details[corr_type] = {"error": str(exc)}

        # Expire old correlations
        expired = self._expire_correlations(db, template)
        result.correlations_expired = expired

        logger.info("[CORR] Scheduled run for '%s': %d created, %d expired",
                     template.name, result.correlations_created, result.correlations_expired)
        return result

    def _build_sentiment_series(self, db, template) -> Dict[str, List[Dict]]:
        """Query graph for entity sentiment history.

        Returns {entity_name: [{"sentiment": str, "timestamp": str, "confidence": float}]}
        """
        series: Dict[str, List[Dict]] = {}
        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            allowed_types = set(template.entity_types) if template.entity_types else None

            for node in adapter.get_all_nodes():
                label = getattr(node, 'label', getattr(node, 'node_type', ''))
                if label in ('Document', 'Passage', 'TextChunk', 'Link', 'Table', 'Image',
                             'ContextIntelligence', 'ContextUnit'):
                    continue
                if allowed_types and label not in allowed_types:
                    continue

                props = getattr(node, 'properties', {}) or {}
                name = props.get('name', '')
                sentiment = props.get('_sentiment', '')
                created_at = props.get('_created_at', props.get('created_at', ''))

                if name and sentiment:
                    series.setdefault(name, []).append({
                        "sentiment": sentiment,
                        "timestamp": created_at,
                        "confidence": props.get('_sentiment_confidence', 0.5),
                    })
        except Exception as exc:
            logger.warning("[CORR] Failed to build sentiment series: %s", exc)

        return series

    def _sentiment_to_numeric(self, sentiment: str) -> float:
        """Convert sentiment label to numeric value for correlation."""
        return {"positive": 1.0, "neutral": 0.0, "negative": -1.0, "mixed": 0.0}.get(sentiment, 0.0)

    def _pearson_correlation(self, x: List[float], y: List[float]) -> float:
        """Compute Pearson correlation coefficient between two series."""
        n = len(x)
        if n < 3:
            return 0.0

        mean_x = sum(x) / n
        mean_y = sum(y) / n

        cov = sum((xi - mean_x) * (yi - mean_y) for xi, yi in zip(x, y))
        std_x = (sum((xi - mean_x) ** 2 for xi in x)) ** 0.5
        std_y = (sum((yi - mean_y) ** 2 for yi in y)) ** 0.5

        if std_x == 0 or std_y == 0:
            return 0.0

        return cov / (std_x * std_y)

    def _compute_cross_entity(
        self, sentiment_series, config, min_samples, min_confidence, db, template,
    ) -> int:
        """Compute sentiment correlations between entity pairs."""
        from ..core.graph_structures import GraphNode, GraphEdge
        import uuid
        from datetime import datetime, timezone

        same_domain = config.get("same_domain", False)
        lag_max_days = config.get("lag_max_days", 7)
        found = 0

        entities = list(sentiment_series.keys())
        now = datetime.now(timezone.utc).isoformat()

        for i, entity_a in enumerate(entities):
            for entity_b in entities[i + 1:]:
                series_a = [self._sentiment_to_numeric(s["sentiment"]) for s in sentiment_series[entity_a]]
                series_b = [self._sentiment_to_numeric(s["sentiment"]) for s in sentiment_series[entity_b]]

                # Align to same length
                min_len = min(len(series_a), len(series_b))
                if min_len < min_samples:
                    continue

                series_a = series_a[:min_len]
                series_b = series_b[:min_len]

                corr = self._pearson_correlation(series_a, series_b)

                if abs(corr) >= min_confidence:
                    # Create CORRELATES_WITH edge
                    try:
                        edge_id = f"corr_{uuid.uuid4().hex[:10]}"
                        db.add_edge(GraphEdge(
                            id=edge_id,
                            source=entity_a, target=entity_b,
                            label="CORRELATES_WITH",
                            properties={
                                "correlation_score": round(corr, 3),
                                "sample_size": min_len,
                                "confidence": round(abs(corr), 3),
                                "template_name": template.name,
                                "computed_at": now,
                                "lag_days": 0,
                            },
                        ))
                        found += 1
                    except Exception:
                        pass

        return found

    def _compute_entity_vs_indicator(
        self, sentiment_series, db, config, min_samples, min_confidence, template,
    ) -> int:
        """Correlate entity sentiment with Indicator node values."""
        from ..core.graph_structures import GraphEdge
        import uuid
        from datetime import datetime, timezone

        found = 0
        now = datetime.now(timezone.utc).isoformat()

        # Collect indicator values from graph
        indicators: Dict[str, List[float]] = {}
        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            for node in adapter.get_all_nodes():
                label = getattr(node, 'label', getattr(node, 'node_type', ''))
                if label != 'Indicator':
                    continue
                props = getattr(node, 'properties', {}) or {}
                name = props.get('name', '')
                value = props.get('value')
                if name and value is not None:
                    try:
                        indicators.setdefault(name, []).append(float(value))
                    except (ValueError, TypeError):
                        pass
        except Exception:
            pass

        # Correlate each entity's sentiment with each indicator
        for entity_name, sentiments in sentiment_series.items():
            sent_values = [self._sentiment_to_numeric(s["sentiment"]) for s in sentiments]

            for ind_name, ind_values in indicators.items():
                min_len = min(len(sent_values), len(ind_values))
                if min_len < min_samples:
                    continue

                corr = self._pearson_correlation(sent_values[:min_len], ind_values[:min_len])

                if abs(corr) >= min_confidence:
                    try:
                        db.add_edge(GraphEdge(
                            id=f"corr_{uuid.uuid4().hex[:10]}",
                            source=entity_name, target=ind_name,
                            label="CORRELATES_WITH",
                            properties={
                                "correlation_score": round(corr, 3),
                                "sample_size": min_len,
                                "confidence": round(abs(corr), 3),
                                "template_name": template.name,
                                "computed_at": now,
                                "correlation_type": "entity_vs_indicator",
                            },
                        ))
                        found += 1
                    except Exception:
                        pass

        return found

    def _compute_geo_sentiment(self, db, config, template) -> int:
        """Aggregate sentiment by geography to find clusters."""
        level = config.get("level", "country")
        geo_sentiment: Dict[str, List[str]] = {}

        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            for node in adapter.get_all_nodes():
                props = getattr(node, 'properties', {}) or {}
                geo_list = props.get('_geography', [])
                sentiment = props.get('_sentiment', '')
                if sentiment and geo_list:
                    for geo in (geo_list if isinstance(geo_list, list) else [geo_list]):
                        geo_sentiment.setdefault(geo, []).append(sentiment)
        except Exception:
            pass

        clusters = 0
        for geo, sentiments in geo_sentiment.items():
            neg_count = sum(1 for s in sentiments if s == "negative")
            total = len(sentiments)
            if total >= 3 and neg_count / total > 0.6:
                clusters += 1
                logger.info("[CORR] Negative sentiment cluster: %s (%d/%d negative)", geo, neg_count, total)

        return clusters

    def _expire_correlations(self, db, template) -> int:
        """Remove CORRELATES_WITH edges older than decay_days."""
        from datetime import datetime, timezone, timedelta

        decay_days = template.quality.decay_days
        cutoff = (datetime.now(timezone.utc) - timedelta(days=decay_days)).isoformat()
        expired = 0

        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            for edge in adapter.get_all_edges():
                label = getattr(edge, 'label', '')
                if label != 'CORRELATES_WITH':
                    continue
                props = getattr(edge, 'properties', {}) or {}
                computed_at = props.get('computed_at', '')
                if computed_at and computed_at < cutoff:
                    try:
                        adapter.remove_edge(edge.id)
                        expired += 1
                    except Exception:
                        pass
        except Exception:
            pass

        return expired
