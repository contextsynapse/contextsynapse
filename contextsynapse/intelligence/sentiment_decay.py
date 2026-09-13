"""Sentiment Decay Model — signals decay over time unless reinforced.

News sentiment doesn't switch off. It decays exponentially.
New same-direction signals reinforce (reset decay clock).
Opposite-direction signals start a new decay.

This is a PLATFORM feature — generic, works for any context.

Usage:
    from contextsynapse.intelligence.sentiment_decay import SentimentDecayEngine

    engine = SentimentDecayEngine()

    # Add signals as they arrive
    engine.add_signal("TCS", sentiment="positive", impact="high",
                      timestamp="2026-08-31T22:00:00+05:30",
                      evidence="TCS wins $2.5B deal")

    engine.add_signal("TCS", sentiment="positive", impact="medium",
                      timestamp="2026-08-31T22:30:00+05:30",
                      evidence="TCS raises guidance")

    # Query current sentiment (with decay applied)
    score = engine.get_score("TCS")
    # → {"score": 0.72, "direction": "positive", "signals": 2,
    #    "last_signal": "2h ago", "decay_rate": "slow"}

    # Query at a specific time
    score = engine.get_score_at("TCS", "2026-09-01T06:00:00+05:30")
    # → {"score": 0.45, "direction": "positive", "decayed_from": 1.0}
"""
from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# DEFAULT decay config — platform provides sensible defaults.
# Domain plugins OVERRIDE these via DecayConfig.
#
# Formula: score × (1 / (1 + α × ln(1 + hours_since_silence)))
# Small α = slow decay. Large α = faster decay.
#
# α=0.05: 1day=0.99  1week=0.92  1month=0.83 (major events)
# α=0.15: 1day=0.96  1week=0.77  1month=0.50 (normal news)
# α=0.35: 1day=0.91  1week=0.60  1month=0.33 (rumors)

DEFAULT_DECAY_RATES = {
    "breaking": 0.05,
    "high": 0.10,
    "medium": 0.15,
    "low": 0.22,
    "rumor": 0.35,
}

DEFAULT_SILENCE_THRESHOLD_HOURS = 6.0


class DecayConfig:
    """Configurable decay parameters — set by domain plugins.

    Usage:
        # Stock analysis plugin — normal market conditions
        config = DecayConfig(
            rates={"breaking": 0.03, "high": 0.08, "medium": 0.12, "low": 0.20, "rumor": 0.30},
            silence_hours=8.0,
        )

        # During crisis — everything decays faster (news flow is intense)
        crisis_config = DecayConfig(
            rates={"breaking": 0.08, "high": 0.15, "medium": 0.25, "low": 0.35, "rumor": 0.50},
            silence_hours=2.0,  # shorter silence window during crisis
        )

        # Pharma — FDA approvals last longer
        pharma_config = DecayConfig(
            rates={"breaking": 0.02, "high": 0.05, "medium": 0.10, "low": 0.15, "rumor": 0.30},
            silence_hours=24.0,  # longer silence window for regulatory events
        )

        engine = SentimentDecayEngine(config=config)
    """

    def __init__(
        self,
        rates: dict = None,
        silence_hours: float = None,
        impact_multipliers: dict = None,
    ):
        self.rates = rates or dict(DEFAULT_DECAY_RATES)
        self.silence_hours = silence_hours if silence_hours is not None else DEFAULT_SILENCE_THRESHOLD_HOURS
        self.impact_multipliers = impact_multipliers or dict(IMPACT_MULTIPLIERS)

    def get_rate(self, impact: str) -> float:
        return self.rates.get(impact, self.rates.get("medium", 0.15))

    def to_dict(self) -> dict:
        return {"rates": self.rates, "silence_hours": self.silence_hours, "impact_multipliers": self.impact_multipliers}

    @classmethod
    def from_dict(cls, data: dict) -> "DecayConfig":
        return cls(rates=data.get("rates"), silence_hours=data.get("silence_hours"),
                   impact_multipliers=data.get("impact_multipliers"))


# Active config — starts as default, overridden by plugins
DECAY_RATES = dict(DEFAULT_DECAY_RATES)
SILENCE_THRESHOLD_HOURS = DEFAULT_SILENCE_THRESHOLD_HOURS

# Sentiment to numeric
SENTIMENT_VALUES = {
    "positive": 1.0,
    "negative": -1.0,
    "neutral": 0.0,
    "mixed": 0.0,
}

# Impact to magnitude multiplier
IMPACT_MULTIPLIERS = {
    "breaking": 1.5,
    "high": 1.0,
    "medium": 0.7,
    "low": 0.4,
    "rumor": 0.2,
}


@dataclass
class SentimentSignal:
    """A single sentiment signal with timestamp."""
    entity: str
    sentiment: str            # positive | negative | neutral | mixed
    impact: str = "medium"    # breaking | high | medium | low | rumor
    value: float = 0.0        # computed: sentiment × impact multiplier
    decay_rate: float = 0.07  # λ for exponential decay
    timestamp: str = ""
    evidence: str = ""
    source: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()
        self.value = SENTIMENT_VALUES.get(self.sentiment, 0.0) * IMPACT_MULTIPLIERS.get(self.impact, 0.7)
        self.decay_rate = DECAY_RATES.get(self.impact, 0.07)

    @property
    def datetime(self) -> datetime:
        try:
            dt = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt
        except (ValueError, TypeError):
            return datetime.now(timezone.utc)

    def decayed_value(self, at: datetime, silence_start: Optional[datetime] = None) -> float:
        """Get the signal's value at a specific time, with logarithmic decay.

        Decay only starts after SILENCE_THRESHOLD_HOURS of no new signals.
        Uses logarithmic curve: 1/(1 + α × ln(1 + hours_since_silence))
        This is barely perceptible in hours, noticeable in days, significant in weeks.
        """
        if silence_start is None:
            silence_start = self.datetime

        # How long since the last signal on this entity?
        hours_silent = max(0, (at - silence_start).total_seconds() / 3600)

        # No decay during silence threshold
        if hours_silent <= SILENCE_THRESHOLD_HOURS:
            return self.value

        # Decay only counts hours AFTER the silence threshold
        decay_hours = hours_silent - SILENCE_THRESHOLD_HOURS
        decay_factor = 1.0 / (1.0 + self.decay_rate * math.log(1.0 + decay_hours))

        return self.value * decay_factor

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity": self.entity,
            "sentiment": self.sentiment,
            "impact": self.impact,
            "value": round(self.value, 3),
            "decay_rate": self.decay_rate,
            "timestamp": self.timestamp,
            "evidence": self.evidence,
            "source": self.source,
        }


@dataclass
class DecayedScore:
    """Current sentiment score with decay applied."""
    entity: str
    score: float              # -1.5 to +1.5 (can exceed 1 with reinforcement)
    normalized: float         # -1.0 to +1.0 (clamped)
    direction: str            # positive | negative | neutral
    strength: str             # strong | moderate | weak | neutral
    active_signals: int       # signals still contributing (value > 0.01)
    total_signals: int
    last_signal_age_hours: float
    dominant_decay: str       # the decay rate category of strongest signal

    def to_dict(self) -> Dict[str, Any]:
        return {
            "entity": self.entity,
            "score": round(self.score, 3),
            "normalized": round(self.normalized, 3),
            "direction": self.direction,
            "strength": self.strength,
            "active_signals": self.active_signals,
            "total_signals": self.total_signals,
            "last_signal_age_hours": round(self.last_signal_age_hours, 1),
            "dominant_decay": self.dominant_decay,
        }


class SentimentDecayEngine:
    """Manages decaying sentiment signals for entities.

    Signals accumulate and decay over time. Same-direction signals reinforce.
    Score = sum of all decayed signal values at query time.

    Decay behavior is configurable via DecayConfig — domain plugins
    can set different rates for different industries or market conditions.
    """

    def __init__(self, config: DecayConfig = None):
        self._signals: Dict[str, List[SentimentSignal]] = defaultdict(list)
        self.config = config or DecayConfig()

    def set_config(self, config: DecayConfig):
        """Update decay configuration (e.g., switch to crisis mode)."""
        self.config = config
        # Update global rates for new signals
        global DECAY_RATES, SILENCE_THRESHOLD_HOURS
        DECAY_RATES = dict(config.rates)
        SILENCE_THRESHOLD_HOURS = config.silence_hours

    def add_signal(
        self,
        entity: str,
        sentiment: str,
        impact: str = "medium",
        timestamp: str = "",
        evidence: str = "",
        source: str = "",
    ) -> SentimentSignal:
        """Add a sentiment signal for an entity."""
        signal = SentimentSignal(
            entity=entity,
            sentiment=sentiment,
            impact=impact,
            timestamp=timestamp,
            evidence=evidence,
            source=source,
        )
        self._signals[entity].append(signal)
        logger.debug("[DECAY] %s: %s %s (value=%.2f, λ=%.3f) — %s",
                      entity, sentiment, impact, signal.value, signal.decay_rate, evidence[:40])
        return signal

    def get_score(self, entity: str, at: Optional[datetime] = None) -> DecayedScore:
        """Get current sentiment score with decay applied.

        Args:
            entity: Entity name
            at: Point in time to compute score. Default: now.
        """
        if at is None:
            at = datetime.now(timezone.utc)
        if at.tzinfo is None:
            at = at.replace(tzinfo=timezone.utc)

        signals = self._signals.get(entity, [])
        if not signals:
            return DecayedScore(
                entity=entity, score=0.0, normalized=0.0,
                direction="neutral", strength="neutral",
                active_signals=0, total_signals=0,
                last_signal_age_hours=0, dominant_decay="none",
            )

        # Find the latest signal time — decay only starts after silence from this point
        latest_signal_dt = max(sig.datetime for sig in signals)

        # Compute decayed values
        total_score = 0.0
        active = 0
        dominant_signal = None
        dominant_abs = 0

        for sig in signals:
            decayed = sig.decayed_value(at, silence_start=latest_signal_dt)
            if abs(decayed) > 0.01:
                active += 1
                total_score += decayed
                if abs(decayed) > dominant_abs:
                    dominant_abs = abs(decayed)
                    dominant_signal = sig

        # Normalize to -1.0 to +1.0
        normalized = max(-1.0, min(1.0, total_score))

        # Direction
        if normalized > 0.1:
            direction = "positive"
        elif normalized < -0.1:
            direction = "negative"
        else:
            direction = "neutral"

        # Strength
        abs_norm = abs(normalized)
        if abs_norm > 0.7:
            strength = "strong"
        elif abs_norm > 0.3:
            strength = "moderate"
        elif abs_norm > 0.1:
            strength = "weak"
        else:
            strength = "neutral"

        # Last signal age
        latest = max(signals, key=lambda s: s.timestamp)
        age_hours = (at - latest.datetime).total_seconds() / 3600

        return DecayedScore(
            entity=entity,
            score=total_score,
            normalized=normalized,
            direction=direction,
            strength=strength,
            active_signals=active,
            total_signals=len(signals),
            last_signal_age_hours=max(0, age_hours),
            dominant_decay=dominant_signal.impact if dominant_signal else "none",
        )

    def get_score_at(self, entity: str, timestamp: str) -> DecayedScore:
        """Get score at a specific timestamp string."""
        try:
            dt = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return self.get_score(entity, at=dt)
        except (ValueError, TypeError):
            return self.get_score(entity)

    def get_timeline(
        self, entity: str, hours: int = 24, resolution_minutes: int = 30,
    ) -> List[Dict[str, Any]]:
        """Get sentiment score over time for charting.

        Returns list of {time, score, direction, active_signals} at regular intervals.
        """
        now = datetime.now(timezone.utc)
        start = now - timedelta(hours=hours)
        step = timedelta(minutes=resolution_minutes)

        timeline = []
        current = start
        while current <= now:
            score = self.get_score(entity, at=current)
            timeline.append({
                "time": current.isoformat(),
                "score": score.normalized,
                "direction": score.direction,
                "strength": score.strength,
                "active_signals": score.active_signals,
            })
            current += step

        return timeline

    def get_all_scores(self, at: Optional[datetime] = None) -> Dict[str, DecayedScore]:
        """Get current scores for all entities."""
        return {entity: self.get_score(entity, at) for entity in self._signals}

    def load_from_context(self, db, entity_filter: str = ""):
        """Load signals from a context graph's entities/facts.

        Reads _sentiment, _event_date, _impact from existing nodes
        and populates the decay engine.
        """
        try:
            adapter = getattr(db, 'csr_adapter', None) or db
            for node in adapter.get_all_nodes():
                label = getattr(node, 'label', getattr(node, 'node_type', ''))
                if label in ('Document', 'Passage', 'ContextIntelligence', 'Chunk',
                             'Table', 'Image', 'Indicator'):
                    continue

                props = getattr(node, 'properties', {}) or {}
                name = props.get('name', '')
                sentiment = props.get('_sentiment', '')
                if not name or not sentiment:
                    continue

                if entity_filter and entity_filter.lower() not in name.lower():
                    continue

                self.add_signal(
                    entity=name,
                    sentiment=sentiment,
                    impact=props.get('_impact', 'medium'),
                    timestamp=props.get('_published_at', '') or props.get('_event_date', '') or props.get('_created_at', ''),
                    evidence=props.get('statement', props.get('description', ''))[:100],
                    source=props.get('_source_title', props.get('_source_name', '')),
                )
        except Exception as exc:
            logger.warning("[DECAY] Failed to load from context: %s", exc)

    def clear(self, entity: str = ""):
        """Clear signals for an entity or all."""
        if entity:
            self._signals.pop(entity, None)
        else:
            self._signals.clear()
