"""Impact Tracker — records predicted vs actual impact for weight learning.

Every propagated signal has a predicted weight (how much impact we expect).
This module tracks what actually happened after the signal, so link weights
can be adjusted over time.

The feedback loop:
    1. Signal propagates with predicted weight (from link weights)
    2. Actual outcome observed (price change, sentiment shift, volume spike)
    3. Impact score computed: how close was prediction to reality?
    4. Link weights adjusted toward better predictions

This produces training data for:
    - Simple weight adjustment (exponential moving average)
    - Reinforcement learning (state=graph, action=weights, reward=prediction accuracy)
    - Correlation discovery (which links actually carry signal?)

Usage:
    from contextsynapse.intelligence.impact_tracker import ImpactTracker

    tracker = ImpactTracker()

    # Record a prediction (when signal propagates)
    tracker.record_prediction(
        signal_id="sig_001",
        source_context="TSMC",
        target_context="Apple",
        predicted_weight=0.63,
        signal_type="sentiment_reversal",
        relationship="supplies_to",
    )

    # Later, record what actually happened
    tracker.record_outcome(
        signal_id="sig_001",
        target_context="Apple",
        actual_impact=0.45,  # measured from price/sentiment change
        metric="stock_price_change_pct",
        observed_value=-2.3,
    )

    # Get weight adjustment recommendation
    adjustment = tracker.recommend_weight_adjustment("TSMC", "Apple")
    # → {"current": 0.63, "recommended": 0.52, "confidence": 0.7, "samples": 15}
"""
from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class PredictionRecord:
    """A predicted impact from signal propagation."""
    signal_id: str
    source_context: str
    target_context: str
    predicted_weight: float
    signal_type: str
    relationship: str
    predicted_at: str = ""
    outcome: Optional["OutcomeRecord"] = None

    def __post_init__(self):
        if not self.predicted_at:
            self.predicted_at = datetime.now(timezone.utc).isoformat()


@dataclass
class OutcomeRecord:
    """What actually happened after a signal."""
    actual_impact: float           # 0.0-1.0 normalized impact
    metric: str                     # stock_price_change_pct | sentiment_shift | volume_change
    observed_value: float           # raw observed value (e.g., -2.3%)
    observed_at: str = ""
    observation_window_hours: float = 24.0

    def __post_init__(self):
        if not self.observed_at:
            self.observed_at = datetime.now(timezone.utc).isoformat()


@dataclass
class WeightAdjustment:
    """Recommended weight adjustment for a link."""
    source_context: str
    target_context: str
    current_weight: float
    recommended_weight: float
    confidence: float              # 0.0-1.0 based on sample count
    sample_count: int
    avg_prediction_error: float    # mean absolute error
    direction: str                 # increase | decrease | maintain

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source_context,
            "target": self.target_context,
            "current_weight": round(self.current_weight, 3),
            "recommended_weight": round(self.recommended_weight, 3),
            "confidence": round(self.confidence, 3),
            "sample_count": self.sample_count,
            "avg_error": round(self.avg_prediction_error, 3),
            "direction": self.direction,
        }


class ImpactTracker:
    """Tracks predicted vs actual impact for weight learning.

    Stores prediction-outcome pairs per link (source→target).
    Recommends weight adjustments using exponential moving average
    of prediction errors.
    """

    def __init__(self, learning_rate: float = 0.1, min_samples: int = 5):
        self._predictions: Dict[str, PredictionRecord] = {}  # signal_id -> record
        self._link_history: Dict[str, List[Tuple[float, float]]] = {}  # "src:tgt" -> [(predicted, actual)]
        self._learning_rate = learning_rate
        self._min_samples = min_samples

    def record_prediction(
        self,
        signal_id: str,
        source_context: str,
        target_context: str,
        predicted_weight: float,
        signal_type: str = "",
        relationship: str = "",
    ):
        """Record a predicted impact when a signal propagates."""
        self._predictions[signal_id] = PredictionRecord(
            signal_id=signal_id,
            source_context=source_context,
            target_context=target_context,
            predicted_weight=predicted_weight,
            signal_type=signal_type,
            relationship=relationship,
        )

    def record_outcome(
        self,
        signal_id: str,
        target_context: str,
        actual_impact: float,
        metric: str = "",
        observed_value: float = 0.0,
        observation_window_hours: float = 24.0,
    ) -> Optional[Dict[str, Any]]:
        """Record what actually happened after a signal.

        Returns the prediction-outcome pair for analysis, or None if
        no matching prediction found.
        """
        pred = self._predictions.get(signal_id)
        if not pred or pred.target_context != target_context:
            return None

        outcome = OutcomeRecord(
            actual_impact=actual_impact,
            metric=metric,
            observed_value=observed_value,
            observation_window_hours=observation_window_hours,
        )
        pred.outcome = outcome

        # Store in link history
        link_key = f"{pred.source_context}:{pred.target_context}"
        self._link_history.setdefault(link_key, []).append(
            (pred.predicted_weight, actual_impact)
        )

        error = abs(pred.predicted_weight - actual_impact)
        logger.info("[IMPACT] %s→%s: predicted=%.2f, actual=%.2f, error=%.2f",
                     pred.source_context, pred.target_context,
                     pred.predicted_weight, actual_impact, error)

        return {
            "signal_id": signal_id,
            "source": pred.source_context,
            "target": pred.target_context,
            "predicted": pred.predicted_weight,
            "actual": actual_impact,
            "error": error,
            "metric": metric,
            "observed_value": observed_value,
        }

    def recommend_weight_adjustment(
        self,
        source_context: str,
        target_context: str,
        current_weight: float = 0.0,
    ) -> WeightAdjustment:
        """Recommend a weight adjustment for a link based on historical accuracy.

        Uses exponential moving average of actual impacts to suggest
        what the link weight should be.
        """
        link_key = f"{source_context}:{target_context}"
        history = self._link_history.get(link_key, [])

        if len(history) < self._min_samples:
            return WeightAdjustment(
                source_context=source_context,
                target_context=target_context,
                current_weight=current_weight,
                recommended_weight=current_weight,
                confidence=0.0,
                sample_count=len(history),
                avg_prediction_error=0.0,
                direction="maintain",
            )

        # Compute EMA of actual impacts (recent observations weighted more)
        actuals = [actual for _, actual in history]
        ema = actuals[0]
        for actual in actuals[1:]:
            ema = self._learning_rate * actual + (1 - self._learning_rate) * ema

        # Compute average prediction error
        errors = [abs(pred - actual) for pred, actual in history]
        avg_error = sum(errors) / len(errors)

        # Confidence based on sample count (saturates at 30 samples)
        confidence = min(1.0, len(history) / 30.0)

        # Blend current weight toward EMA
        recommended = current_weight + self._learning_rate * (ema - current_weight)
        recommended = max(0.05, min(1.0, recommended))  # clamp to [0.05, 1.0]

        if recommended > current_weight + 0.02:
            direction = "increase"
        elif recommended < current_weight - 0.02:
            direction = "decrease"
        else:
            direction = "maintain"

        return WeightAdjustment(
            source_context=source_context,
            target_context=target_context,
            current_weight=current_weight,
            recommended_weight=recommended,
            confidence=confidence,
            sample_count=len(history),
            avg_prediction_error=avg_error,
            direction=direction,
        )

    def apply_adjustments(self, hierarchy, min_confidence: float = 0.5) -> List[Dict[str, Any]]:
        """Apply recommended weight adjustments to a SignalHierarchy.

        Only applies adjustments with confidence >= min_confidence.
        Returns list of adjustments made.
        """
        applied = []

        for link_key, history in self._link_history.items():
            if len(history) < self._min_samples:
                continue

            src, tgt = link_key.split(":", 1)

            # Find the link in the hierarchy
            for link_list in [hierarchy._downstream_map.get(src, []),
                              hierarchy._lateral_map.get(src, [])]:
                for link in link_list:
                    target = link.downstream if link.upstream == src else link.upstream
                    if target == tgt:
                        adj = self.recommend_weight_adjustment(src, tgt, link.weight)
                        if adj.confidence >= min_confidence and adj.direction != "maintain":
                            old_weight = link.weight
                            link.weight = adj.recommended_weight
                            record = {
                                "link": f"{src} → {tgt}",
                                "old_weight": round(old_weight, 3),
                                "new_weight": round(adj.recommended_weight, 3),
                                "confidence": round(adj.confidence, 3),
                                "samples": adj.sample_count,
                                "direction": adj.direction,
                            }
                            applied.append(record)
                            logger.info("[IMPACT] Weight adjusted: %s %.3f → %.3f (%s, conf=%.2f)",
                                         record["link"], old_weight, adj.recommended_weight,
                                         adj.direction, adj.confidence)

        return applied

    def get_training_data(self) -> List[Dict[str, Any]]:
        """Export prediction-outcome pairs as training data for RL.

        Each record is a (state, action, reward) tuple:
        - state: (source_context, target_context, signal_type, relationship)
        - action: predicted_weight
        - reward: -abs(predicted - actual)  (negative error = reward)
        """
        data = []
        for pred in self._predictions.values():
            if pred.outcome is None:
                continue
            data.append({
                "state": {
                    "source": pred.source_context,
                    "target": pred.target_context,
                    "signal_type": pred.signal_type,
                    "relationship": pred.relationship,
                },
                "action": pred.predicted_weight,
                "reward": -abs(pred.predicted_weight - pred.outcome.actual_impact),
                "actual_impact": pred.outcome.actual_impact,
                "metric": pred.outcome.metric,
                "observed_value": pred.outcome.observed_value,
                "predicted_at": pred.predicted_at,
                "observed_at": pred.outcome.observed_at,
            })
        return data

    def get_link_stats(self) -> Dict[str, Dict[str, Any]]:
        """Get accuracy statistics per link."""
        stats = {}
        for link_key, history in self._link_history.items():
            errors = [abs(p - a) for p, a in history]
            actuals = [a for _, a in history]
            stats[link_key] = {
                "sample_count": len(history),
                "avg_error": round(sum(errors) / len(errors), 3) if errors else 0,
                "avg_actual_impact": round(sum(actuals) / len(actuals), 3) if actuals else 0,
                "min_actual": round(min(actuals), 3) if actuals else 0,
                "max_actual": round(max(actuals), 3) if actuals else 0,
            }
        return stats
