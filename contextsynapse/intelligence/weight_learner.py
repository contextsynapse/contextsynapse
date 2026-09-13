"""Weight Learner — auto-adjust sensor weights from prediction vs actual outcomes.

Closes the feedback loop:
  Prediction (fused score) → Trade → P&L Outcome → Attribution → Weight Adjustment

After N trades, sensors that consistently contributed to profitable decisions
get their weight increased. Sensors that contributed to losses get decreased.

The FM can enable/disable this via a toggle. When enabled, weights auto-adjust.
When disabled, weights stay at manual/default values.

Usage:
    learner = WeightLearner(redis_client)
    result = learner.compute_optimal_weights()
    # result.changes = [{sensor: "india_economy", before: 1.0, after: 1.15, reason: "72% win rate"}]

    learner.save_learned_weights(result.weights_after)
    learner.set_enabled(True)  # FM enables auto-adjustment
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

WEIGHT_MIN = 0.1
WEIGHT_MAX = 3.0
DEFAULT_LEARNING_RATE = 0.05

DEFAULT_WEIGHTS = {
    "company_news": 1.0, "fundamentals": 1.0, "price": 1.0,
    "india_economy": 1.0, "us_economy": 1.0, "global_macro": 1.0,
    "it_services_sector": 1.0, "geopolitical_risk": 1.0,
    "negative_signals": 1.0, "regulatory_legal": 1.0,
    "currency_forex": 1.0, "fii_flow": 1.0, "mf_holding": 1.0,
    "social_buzz": 1.0,
}


@dataclass
class SensorPerformance:
    sensor: str
    total_trades: int
    profitable: int
    losses: int
    total_pnl: float
    avg_pnl: float
    win_rate: float


@dataclass
class WeightChange:
    sensor: str
    before: float
    after: float
    direction: str  # "up" | "down" | "unchanged"
    reason: str
    win_rate: float
    trades: int


@dataclass
class LearningResult:
    total_trades: int
    weights_before: Dict[str, float]
    weights_after: Dict[str, float]
    changes: List[WeightChange]
    computed_at: str


class WeightLearner:

    def __init__(self, redis_client, learning_rate: float = DEFAULT_LEARNING_RATE):
        self._r = redis_client
        self._lr = learning_rate

    def get_sensor_performance(self, min_trades: int = 3) -> Dict[str, SensorPerformance]:
        """Compute per-sensor performance from attribution history."""
        sensor_stats: Dict[str, Dict] = {}

        keys = self._r.keys("attribution:market:*") or []
        for key in keys:
            if isinstance(key, bytes):
                key = key.decode()
            if ":trade:" in key:
                continue

            entries = self._r.lrange(key, 0, -1)
            for raw in entries:
                try:
                    entry = json.loads(raw if isinstance(raw, str) else raw.decode())
                except (json.JSONDecodeError, TypeError):
                    continue

                entity = entry.get("entity", "")
                pnl = float(entry.get("pnl", 0))
                trade_id = entry.get("trade_id", "")

                sensor = entity.replace("market:", "")

                if sensor not in sensor_stats:
                    sensor_stats[sensor] = {"trades": set(), "pnl": 0, "wins": 0, "losses": 0}

                stats = sensor_stats[sensor]
                if trade_id not in stats["trades"]:
                    stats["trades"].add(trade_id)
                    stats["pnl"] += pnl
                    if pnl > 0:
                        stats["wins"] += 1
                    elif pnl < 0:
                        stats["losses"] += 1

        result = {}
        for sensor, stats in sensor_stats.items():
            total = len(stats["trades"])
            if total < min_trades:
                continue
            result[sensor] = SensorPerformance(
                sensor=sensor,
                total_trades=total,
                profitable=stats["wins"],
                losses=stats["losses"],
                total_pnl=stats["pnl"],
                avg_pnl=stats["pnl"] / total,
                win_rate=stats["wins"] / total if total > 0 else 0.5,
            )
        return result

    def compute_optimal_weights(
        self,
        current_weights: Optional[Dict[str, float]] = None,
        min_trades: int = 3,
    ) -> LearningResult:
        """Compute weight adjustments from performance data.

        Win rate > 50% → reward (increase weight)
        Win rate < 50% → penalize (decrease weight)
        Confidence scales with number of trades.
        """
        w_before = dict(current_weights or self.load_learned_weights() or DEFAULT_WEIGHTS)
        w_after = dict(w_before)
        changes = []

        perf = self.get_sensor_performance(min_trades=min_trades)
        total_trades = sum(p.total_trades for p in perf.values())

        for sensor, p in perf.items():
            current = w_before.get(sensor, 1.0)

            deviation = p.win_rate - 0.5  # -0.5 to +0.5
            confidence = min(1.0, p.total_trades / 20)
            adjustment = deviation * self._lr * confidence * 2

            new_w = max(WEIGHT_MIN, min(WEIGHT_MAX, current + adjustment))

            direction = "up" if new_w > current + 0.001 else "down" if new_w < current - 0.001 else "unchanged"
            reason = f"{p.win_rate:.0%} win rate over {p.total_trades} trades, avg P&L {p.avg_pnl:+,.0f}"

            changes.append(WeightChange(
                sensor=sensor, before=round(current, 3), after=round(new_w, 3),
                direction=direction, reason=reason,
                win_rate=p.win_rate, trades=p.total_trades,
            ))
            w_after[sensor] = round(new_w, 3)

        return LearningResult(
            total_trades=total_trades,
            weights_before=w_before,
            weights_after=w_after,
            changes=sorted(changes, key=lambda c: abs(c.after - c.before), reverse=True),
            computed_at=datetime.now(timezone.utc).isoformat(),
        )

    def save_learned_weights(self, weights: Dict[str, float]) -> None:
        self._r.set("feedback:learned_weights", json.dumps({
            "weights": weights,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }))

    def load_learned_weights(self) -> Optional[Dict[str, float]]:
        raw = self._r.get("feedback:learned_weights")
        if raw is None:
            return None
        try:
            return json.loads(raw if isinstance(raw, str) else raw.decode()).get("weights")
        except (json.JSONDecodeError, TypeError):
            return None

    def is_enabled(self) -> bool:
        val = self._r.get("feedback:enabled")
        if isinstance(val, bytes):
            val = val.decode()
        return val == "true"

    def set_enabled(self, enabled: bool) -> None:
        self._r.set("feedback:enabled", "true" if enabled else "false")

    def get_status(self) -> Dict[str, Any]:
        enabled = self.is_enabled()
        learned = self.load_learned_weights()
        perf = self.get_sensor_performance(min_trades=1)

        return {
            "enabled": enabled,
            "has_learned_weights": learned is not None,
            "learned_weights": learned or {},
            "default_weights": DEFAULT_WEIGHTS,
            "sensors": {
                name: {
                    "win_rate": round(p.win_rate, 3),
                    "trades": p.total_trades,
                    "avg_pnl": round(p.avg_pnl, 2),
                    "current_weight": (learned or DEFAULT_WEIGHTS).get(name, 1.0),
                }
                for name, p in perf.items()
            },
        }

    def run_learning_cycle(self) -> Optional[LearningResult]:
        """Run one learning cycle if enabled. Returns result or None if disabled."""
        if not self.is_enabled():
            return None

        result = self.compute_optimal_weights()
        if result.changes:
            self.save_learned_weights(result.weights_after)
            logger.info("[FEEDBACK] Weight update: %d sensors adjusted from %d trades",
                       len([c for c in result.changes if c.direction != "unchanged"]),
                       result.total_trades)
        return result
