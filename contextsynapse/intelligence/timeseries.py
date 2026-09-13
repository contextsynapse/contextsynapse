"""Time-Series Convenience API — query sentiment/indicators over time.

Provides simple functions for common temporal queries without AIQL:
- Sentiment trajectory for an entity over N days
- Indicator values over time
- Sentiment comparison across entities

Usage:
    from contextsynapse.intelligence.timeseries import TimeSeriesQuery

    ts = TimeSeriesQuery(db)
    trajectory = ts.sentiment_over_time("TCS", days=30)
    # → [{"date": "2026-08-01", "positive": 5, "negative": 1, "neutral": 2}, ...]

    comparison = ts.compare_sentiment(["TCS", "Infosys"], days=7)
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class TimeSeriesQuery:
    """Convenience API for temporal queries on the graph."""

    def __init__(self, db=None):
        self._db = db

    def sentiment_over_time(
        self,
        entity_name: str,
        days: int = 30,
        bucket: str = "day",
    ) -> List[Dict[str, Any]]:
        """Get sentiment distribution over time for an entity.

        Args:
            entity_name: Entity to query
            days: Look back N days
            bucket: "day" | "week" | "hour"

        Returns list of {"date": str, "positive": int, "negative": int, "neutral": int, "mixed": int}
        """
        if not self._db:
            return []

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        buckets: Dict[str, Dict[str, int]] = defaultdict(lambda: {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0})

        try:
            adapter = getattr(self._db, 'csr_adapter', None) or self._db
            for node in adapter.get_all_nodes():
                props = getattr(node, 'properties', {}) or {}
                name = props.get('name', '')
                if name.lower() != entity_name.lower():
                    continue

                sentiment = props.get('_sentiment', '')
                created = props.get('_created_at', props.get('created_at', ''))
                if not sentiment or not created or created < cutoff:
                    continue

                bucket_key = self._to_bucket(created, bucket)
                if sentiment in buckets[bucket_key]:
                    buckets[bucket_key][sentiment] += 1
        except Exception as exc:
            logger.warning("[TS] sentiment_over_time failed: %s", exc)

        result = []
        for date_key in sorted(buckets.keys()):
            entry = {"date": date_key, **buckets[date_key]}
            entry["total"] = sum(buckets[date_key].values())
            entry["net_sentiment"] = entry["positive"] - entry["negative"]
            result.append(entry)

        return result

    def indicator_over_time(
        self,
        indicator_name: str,
        days: int = 90,
    ) -> List[Dict[str, Any]]:
        """Get indicator values over time.

        Returns list of {"date": str, "value": float, "entity": str, "period": str}
        """
        if not self._db:
            return []

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        results = []

        try:
            adapter = getattr(self._db, 'csr_adapter', None) or self._db
            for node in adapter.get_all_nodes():
                label = getattr(node, 'label', getattr(node, 'node_type', ''))
                if label != 'Indicator':
                    continue

                props = getattr(node, 'properties', {}) or {}
                col_header = props.get('column_header', '')
                name = props.get('name', '')

                if indicator_name.lower() not in name.lower() and indicator_name.lower() not in col_header.lower():
                    continue

                created = props.get('_created_at', props.get('created_at', ''))

                results.append({
                    "date": created[:10] if created else "",
                    "value": props.get('value', 0),
                    "raw_value": props.get('raw_value', ''),
                    "entity": props.get('entity', ''),
                    "period": props.get('period', ''),
                    "unit": props.get('unit', ''),
                })
        except Exception as exc:
            logger.warning("[TS] indicator_over_time failed: %s", exc)

        return sorted(results, key=lambda x: x.get("date", ""))

    def compare_sentiment(
        self,
        entity_names: List[str],
        days: int = 7,
    ) -> Dict[str, Dict[str, Any]]:
        """Compare sentiment across multiple entities.

        Returns {"TCS": {"positive": 5, "negative": 1, "net": 4}, ...}
        """
        result = {}
        for name in entity_names:
            timeline = self.sentiment_over_time(name, days=days)
            totals = {"positive": 0, "negative": 0, "neutral": 0, "mixed": 0}
            for entry in timeline:
                for key in totals:
                    totals[key] += entry.get(key, 0)
            totals["total"] = sum(totals.values())
            totals["net_sentiment"] = totals["positive"] - totals["negative"]
            result[name] = totals
        return result

    def entity_timeline(
        self,
        entity_name: str,
        days: int = 30,
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        """Get a chronological timeline of all facts/events for an entity.

        Returns list of {"date": str, "type": str, "content": str, "sentiment": str}
        """
        if not self._db:
            return []

        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        events = []

        try:
            adapter = getattr(self._db, 'csr_adapter', None) or self._db
            entity_lower = entity_name.lower()

            for node in adapter.get_all_nodes():
                props = getattr(node, 'properties', {}) or {}
                label = getattr(node, 'label', getattr(node, 'node_type', ''))

                # Match by name or by mention in content
                name = props.get('name', '').lower()
                content = props.get('statement', props.get('content', props.get('description', ''))).lower() if props else ''

                if entity_lower not in name and entity_lower not in content:
                    continue

                created = props.get('_created_at', props.get('created_at', ''))
                if created and created < cutoff:
                    continue

                events.append({
                    "date": created[:10] if created else "",
                    "timestamp": created,
                    "type": label,
                    "content": props.get('name', props.get('statement', ''))[:200],
                    "sentiment": props.get('_sentiment', ''),
                    "impact": props.get('_impact', ''),
                    "geography": props.get('_geography', []),
                })
        except Exception as exc:
            logger.warning("[TS] entity_timeline failed: %s", exc)

        events.sort(key=lambda x: x.get("timestamp", ""), reverse=True)
        return events[:limit]

    def _to_bucket(self, timestamp: str, bucket: str) -> str:
        """Convert timestamp to bucket key."""
        if bucket == "hour":
            return timestamp[:13]  # "2026-08-30T14"
        elif bucket == "week":
            try:
                dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
                monday = dt - timedelta(days=dt.weekday())
                return monday.strftime("%Y-%W")
            except (ValueError, TypeError):
                return timestamp[:10]
        else:  # day
            return timestamp[:10]  # "2026-08-30"
