"""Correlation template model — defines what the correlation engine watches and computes.

Templates are YAML configs that tell the engine:
- What entity/indicator types to watch
- What signals to fire on ingest (sentiment reversal, threshold, co-occurrence)
- What correlations to compute on schedule (entity vs indicator, cross-entity, geo-cluster)
- Quality gates (min samples, confidence, decay)

Usage:
    from contextsynapse.intelligence.correlation_template import load_template, list_templates

    template = load_template("market_analysis")
    template.signals[0].type  # "sentiment_reversal"
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_TEMPLATES_DIR = Path(__file__).parent.parent / "config" / "templates"


@dataclass
class SignalConfig:
    """Configuration for an on-ingest signal."""
    type: str             # sentiment_reversal | threshold_breach | co_occurrence
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class CorrelationConfig:
    """Configuration for a scheduled correlation computation."""
    type: str             # entity_vs_indicator | cross_entity | geo_sentiment_cluster
    config: Dict[str, Any] = field(default_factory=dict)


@dataclass
class QualityConfig:
    """Quality gates for correlations."""
    min_samples: int = 10
    min_confidence: float = 0.6
    decay_days: int = 30
    max_correlations: int = 1000


@dataclass
class CorrelationTemplate:
    """A complete correlation template definition."""
    name: str
    description: str = ""
    entity_types: List[str] = field(default_factory=list)
    indicator_types: List[str] = field(default_factory=list)
    signals: List[SignalConfig] = field(default_factory=list)
    correlations: List[CorrelationConfig] = field(default_factory=list)
    schedule: str = "daily"
    quality: QualityConfig = field(default_factory=QualityConfig)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "CorrelationTemplate":
        """Create template from a dict (parsed YAML or API input)."""
        signals = [
            SignalConfig(type=s["type"], config=s.get("config", {}))
            for s in data.get("signals", [])
        ]
        correlations = [
            CorrelationConfig(type=c["type"], config=c.get("config", {}))
            for c in data.get("correlations", [])
        ]
        quality_data = data.get("quality", {})
        quality = QualityConfig(
            min_samples=quality_data.get("min_samples", 10),
            min_confidence=quality_data.get("min_confidence", 0.6),
            decay_days=quality_data.get("decay_days", 30),
            max_correlations=quality_data.get("max_correlations", 1000),
        )
        return cls(
            name=data.get("name", "unnamed"),
            description=data.get("description", ""),
            entity_types=data.get("entity_types", []),
            indicator_types=data.get("indicator_types", []),
            signals=signals,
            correlations=correlations,
            schedule=data.get("schedule", "daily"),
            quality=quality,
        )


def load_template(name: str) -> Optional[CorrelationTemplate]:
    """Load a correlation template by name from the templates directory."""
    path = _TEMPLATES_DIR / f"{name}.yaml"
    if not path.exists():
        path = _TEMPLATES_DIR / f"{name}.yml"
    if not path.exists():
        logger.warning("Correlation template not found: %s", name)
        return None

    try:
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        return CorrelationTemplate.from_dict(data)
    except Exception as exc:
        logger.error("Failed to load template %s: %s", name, exc)
        return None


def list_templates() -> List[str]:
    """List available template names from the templates directory."""
    if not _TEMPLATES_DIR.exists():
        return []
    names = []
    for f in sorted(_TEMPLATES_DIR.iterdir()):
        if f.suffix in (".yaml", ".yml"):
            names.append(f.stem)
    return names
