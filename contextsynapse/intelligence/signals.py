"""On-ingest signal processors for the correlation engine.

Lightweight checks that fire immediately when new tagged content arrives.
Each signal function checks a specific condition and returns a Signal
dataclass if triggered, or None if not.

Usage:
    from contextsynapse.intelligence.signals import check_sentiment_reversal

    signal = check_sentiment_reversal("Tesla", "negative", history, config)
    if signal:
        emit_event(signal)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Signal:
    """A fired signal from on-ingest processing."""
    type: str               # sentiment_reversal | threshold_breach | co_occurrence
    entity_name: str        # primary entity involved
    severity: str = "info"  # info | warning | alert
    details: Dict[str, Any] = field(default_factory=dict)
    fired_at: str = ""

    def __post_init__(self):
        if not self.fired_at:
            self.fired_at = datetime.now(timezone.utc).isoformat()


def _parse_timestamp(ts: str) -> Optional[datetime]:
    """Parse ISO timestamp string to datetime."""
    try:
        if ts.endswith("Z"):
            ts = ts[:-1] + "+00:00"
        return datetime.fromisoformat(ts)
    except (ValueError, TypeError):
        return None


def check_sentiment_reversal(
    entity_name: str,
    current_sentiment: str,
    history: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Optional[Signal]:
    """Check if entity sentiment has reversed from its recent trend.

    Args:
        entity_name: Name of the entity
        current_sentiment: Current sentiment (positive/negative/neutral/mixed)
        history: List of {"sentiment": str, "timestamp": str} dicts, most recent first
        config: {"window_hours": int, "min_mentions": int}

    Returns Signal if reversal detected, None otherwise.
    """
    window_hours = config.get("window_hours", 48)
    min_mentions = config.get("min_mentions", 3)

    if not history or len(history) < min_mentions:
        return None

    # Filter to window
    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)
    recent = []
    for h in history:
        ts = _parse_timestamp(h.get("timestamp", ""))
        if ts and ts >= cutoff:
            recent.append(h)

    if len(recent) < min_mentions:
        return None

    # Determine dominant sentiment in history
    sentiment_counts: Dict[str, int] = {}
    for h in recent:
        s = h.get("sentiment", "neutral")
        sentiment_counts[s] = sentiment_counts.get(s, 0) + 1

    if not sentiment_counts:
        return None

    dominant = max(sentiment_counts, key=sentiment_counts.__getitem__)

    # Check for reversal — only fire when both sides are polar (positive/negative)
    if current_sentiment != dominant and current_sentiment in ("positive", "negative"):
        if dominant in ("positive", "negative"):
            return Signal(
                type="sentiment_reversal",
                entity_name=entity_name,
                severity="warning",
                details={
                    "from": dominant,
                    "to": current_sentiment,
                    "history_count": len(recent),
                    "window_hours": window_hours,
                },
            )

    return None


def check_threshold_breach(
    entity_name: str,
    history: List[Dict[str, Any]],
    config: Dict[str, Any],
) -> Optional[Signal]:
    """Check if sentiment count exceeds threshold in window.

    Args:
        entity_name: Name of the entity
        history: List of {"sentiment": str, "timestamp": str} dicts
        config: {"metric": str, "threshold": int, "window_hours": int}

    Returns Signal if threshold breached, None otherwise.
    """
    metric = config.get("metric", "negative_sentiment_count")
    threshold = config.get("threshold", 5)
    window_hours = config.get("window_hours", 24)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

    # Derive target sentiment from metric name
    target_sentiment = "negative" if "negative" in metric else "positive"
    count = 0
    for h in history:
        ts = _parse_timestamp(h.get("timestamp", ""))
        if ts and ts >= cutoff:
            if h.get("sentiment") == target_sentiment:
                count += 1

    if count >= threshold:
        return Signal(
            type="threshold_breach",
            entity_name=entity_name,
            severity="alert",
            details={
                "metric": metric,
                "count": count,
                "threshold": threshold,
                "window_hours": window_hours,
            },
        )

    return None


def check_co_occurrence(
    entity_a: str,
    entity_b: str,
    co_occurrence_count: int,
    config: Dict[str, Any],
) -> Optional[Signal]:
    """Check if two entities are mentioned together frequently enough.

    Args:
        entity_a: First entity name
        entity_b: Second entity name
        co_occurrence_count: How many times they co-occurred in window
        config: {"min_count": int, "window_days": int}

    Returns Signal if co-occurrence threshold met, None otherwise.
    """
    min_count = config.get("min_count", 3)

    if co_occurrence_count >= min_count:
        return Signal(
            type="co_occurrence",
            entity_name=entity_a,
            severity="info",
            details={
                "paired_with": entity_b,
                "count": co_occurrence_count,
                "min_count": min_count,
            },
        )

    return None
