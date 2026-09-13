"""Macro Indicator Engine — structured, configurable macro scoring.

Reads indicator definitions from YAML config files. Each indicator has a value,
thresholds, direction, and weight. The engine computes a weighted macro score
with full evidence trail — every score point is traceable to a real data point.

Usage:
    engine = MacroIndicatorEngine()
    engine.load_config("config/indicators/india_macro.yaml")
    engine.update("gdp_growth", 7.8, source="MOSPI Q1 FY27")
    result = engine.score()
    # result.score = 0.62
    # result.evidence = [{indicator: "gdp_growth", value: 7.8, signal: "strong", ...}, ...]
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class IndicatorReading:
    """A single indicator's current value and computed signal."""
    indicator_id: str
    name: str
    value: float
    unit: str
    source: str
    source_url: str
    updated_at: str
    frequency: str

    # Computed
    signal: str              # "strong_positive", "positive", "neutral", "negative", "strong_negative"
    signal_score: float      # -1.0 to 1.0
    weight: float            # contribution weight
    weighted_score: float    # signal_score * weight

    # Context
    previous_value: Optional[float] = None
    change: Optional[float] = None
    change_direction: str = ""   # "improving", "declining", "stable"
    threshold_label: str = ""    # which threshold was crossed


@dataclass
class MacroScoreResult:
    """Complete macro score with evidence trail."""
    entity: str
    name: str
    score: float                 # -1.0 to 1.0
    score_pct: float             # -100 to 100
    direction: str               # "bullish", "bearish", "neutral"
    total_indicators: int
    active_indicators: int       # how many have data
    evidence: List[IndicatorReading]
    positive_drivers: List[IndicatorReading]
    negative_drivers: List[IndicatorReading]
    computed_at: str
    config_file: str


@dataclass
class IndicatorConfig:
    """Configuration for one indicator from YAML."""
    id: str
    name: str
    unit: str
    frequency: str
    source: str
    source_url: str
    direction: str               # "higher_is_better" or "lower_is_better"
    thresholds: Dict[str, float]
    weight: float
    scoring: str                 # "threshold" (default) or "action_based"
    actions: Dict[str, float]    # for action_based scoring
    sector_impact: Dict[str, float]


class MacroIndicatorEngine:
    """Configurable macro indicator scoring engine."""

    def __init__(self):
        self._configs: Dict[str, IndicatorConfig] = {}
        self._values: Dict[str, Dict[str, Any]] = {}  # indicator_id → {value, source, updated_at, previous}
        self._entity: str = ""
        self._name: str = ""
        self._config_file: str = ""

    def load_config(self, config_path: str) -> int:
        """Load indicator definitions from a YAML file. Returns count loaded."""
        path = Path(config_path)
        if not path.is_absolute():
            path = Path(os.getcwd()) / path
        if not path.exists():
            logger.warning("[MACRO] Config not found: %s", path)
            return 0

        with open(path, "r") as f:
            raw = yaml.safe_load(f)

        self._entity = raw.get("entity", "")
        self._name = raw.get("name", "")
        self._config_file = str(config_path)

        indicators = raw.get("indicators", {})
        count = 0
        for ind_id, ind in indicators.items():
            self._configs[ind_id] = IndicatorConfig(
                id=ind_id,
                name=ind.get("name", ind_id),
                unit=ind.get("unit", ""),
                frequency=ind.get("frequency", ""),
                source=ind.get("source", ""),
                source_url=ind.get("source_url", ""),
                direction=ind.get("direction", "higher_is_better"),
                thresholds=ind.get("thresholds", {}),
                weight=float(ind.get("weight", 0.05)),
                scoring=ind.get("scoring", "threshold"),
                actions=ind.get("actions", {}),
                sector_impact=ind.get("sector_impact", {}),
            )
            count += 1

        logger.info("[MACRO] Loaded %d indicators from %s", count, config_path)
        return count

    def update(
        self,
        indicator_id: str,
        value: float,
        source: str = "",
        source_url: str = "",
        action: str = "",
    ) -> bool:
        """Update an indicator's current value."""
        if indicator_id not in self._configs:
            logger.warning("[MACRO] Unknown indicator: %s", indicator_id)
            return False

        previous = self._values.get(indicator_id, {}).get("value")
        self._values[indicator_id] = {
            "value": value,
            "previous": previous,
            "source": source or self._configs[indicator_id].source,
            "source_url": source_url or self._configs[indicator_id].source_url,
            "action": action,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        }
        return True

    def update_batch(self, readings: Dict[str, float], source: str = "") -> int:
        """Update multiple indicators at once. Returns count updated."""
        count = 0
        for ind_id, value in readings.items():
            if self.update(ind_id, value, source=source):
                count += 1
        return count

    def score(self) -> MacroScoreResult:
        """Compute the weighted macro score with full evidence trail."""
        evidence = []
        total_weight = 0
        weighted_sum = 0

        for ind_id, config in self._configs.items():
            data = self._values.get(ind_id)
            if not data:
                continue

            value = data["value"]
            previous = data.get("previous")

            # Compute signal
            if config.scoring == "action_based":
                action = data.get("action", "hold")
                signal_score = config.actions.get(action, 0.0)
                signal = self._action_to_signal(signal_score)
                threshold_label = f"action={action}"
            else:
                signal_score, signal, threshold_label = self._threshold_score(
                    value, config.direction, config.thresholds
                )

            weighted = signal_score * config.weight
            total_weight += config.weight
            weighted_sum += weighted

            # Change direction
            change = None
            change_dir = "stable"
            if previous is not None:
                change = value - previous
                if config.direction == "higher_is_better":
                    change_dir = "improving" if change > 0 else "declining" if change < 0 else "stable"
                else:
                    change_dir = "improving" if change < 0 else "declining" if change > 0 else "stable"

            reading = IndicatorReading(
                indicator_id=ind_id,
                name=config.name,
                value=value,
                unit=config.unit,
                source=data.get("source", config.source),
                source_url=data.get("source_url", config.source_url),
                updated_at=data.get("updated_at", ""),
                frequency=config.frequency,
                signal=signal,
                signal_score=signal_score,
                weight=config.weight,
                weighted_score=weighted,
                previous_value=previous,
                change=change,
                change_direction=change_dir,
                threshold_label=threshold_label,
            )
            evidence.append(reading)

        # Normalize score
        final_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        final_score = max(-1.0, min(1.0, final_score))

        direction = "bullish" if final_score > 0.1 else "bearish" if final_score < -0.1 else "neutral"

        positive = sorted(
            [e for e in evidence if e.signal_score > 0],
            key=lambda e: e.weighted_score, reverse=True,
        )
        negative = sorted(
            [e for e in evidence if e.signal_score < 0],
            key=lambda e: e.weighted_score,
        )

        return MacroScoreResult(
            entity=self._entity,
            name=self._name,
            score=round(final_score, 3),
            score_pct=round(final_score * 100, 1),
            direction=direction,
            total_indicators=len(self._configs),
            active_indicators=len(evidence),
            evidence=sorted(evidence, key=lambda e: abs(e.weighted_score), reverse=True),
            positive_drivers=positive,
            negative_drivers=negative,
            computed_at=datetime.now(timezone.utc).isoformat(),
            config_file=self._config_file,
        )

    def get_indicators(self) -> List[Dict[str, Any]]:
        """List all configured indicators with their current values."""
        result = []
        for ind_id, config in self._configs.items():
            data = self._values.get(ind_id, {})
            result.append({
                "id": ind_id,
                "name": config.name,
                "unit": config.unit,
                "frequency": config.frequency,
                "source": config.source,
                "weight": config.weight,
                "direction": config.direction,
                "has_data": ind_id in self._values,
                "current_value": data.get("value"),
                "updated_at": data.get("updated_at", ""),
            })
        return result

    # ── Scoring helpers ──────────────────────────────────

    def _threshold_score(
        self, value: float, direction: str, thresholds: Dict[str, float]
    ) -> tuple:
        """Score a value against thresholds. Returns (score, signal_name, threshold_label)."""
        strong = thresholds.get("strong", 0)
        positive = thresholds.get("positive", 0)
        neutral = thresholds.get("neutral", 0)
        negative = thresholds.get("negative", 0)

        if direction == "higher_is_better":
            if value >= strong:
                return (1.0, "strong_positive", f">= {strong}")
            elif value >= positive:
                return (0.5, "positive", f">= {positive}")
            elif value >= neutral:
                return (0.0, "neutral", f">= {neutral}")
            elif value >= negative:
                return (-0.5, "negative", f">= {negative}")
            else:
                return (-1.0, "strong_negative", f"< {negative}")
        else:  # lower_is_better
            if value <= strong:
                return (1.0, "strong_positive", f"<= {strong}")
            elif value <= positive:
                return (0.5, "positive", f"<= {positive}")
            elif value <= neutral:
                return (0.0, "neutral", f"<= {neutral}")
            elif value <= negative:
                return (-0.5, "negative", f"<= {negative}")
            else:
                return (-1.0, "strong_negative", f"> {negative}")

    def _action_to_signal(self, score: float) -> str:
        if score > 0.5:
            return "strong_positive"
        elif score > 0:
            return "positive"
        elif score == 0:
            return "neutral"
        elif score > -0.5:
            return "negative"
        return "strong_negative"
