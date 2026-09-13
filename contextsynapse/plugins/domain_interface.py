"""Domain Plugin Interface — contract for domain knowledge plugins.

A domain plugin provides:
  1. Entity types and their schemas
  2. Sector definitions with keywords, regulators, supply chain
  3. Sensor definitions (what to monitor)
  4. Seed prompt template (for LLM discovery)
  5. Entity data (people, brands, aliases for known entities)
  6. Extraction patterns (regex for signal classification)

Any domain (finance, life science, real estate, supply chain) implements
this interface. The contextsynapse engine is domain-agnostic — it uses
whatever domain plugin is loaded.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes — the building blocks of a domain definition
# ---------------------------------------------------------------------------


@dataclass
class EntityType:
    """A node type within the domain (e.g. "company", "drug", "property")."""

    name: str
    properties: List[str]
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "properties": list(self.properties),
            "description": self.description,
        }


@dataclass
class EdgeType:
    """A relationship type within the domain (e.g. "MANAGES", "DEVELOPS")."""

    name: str
    from_type: str
    to_type: str
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "from_type": self.from_type,
            "to_type": self.to_type,
            "description": self.description,
        }


@dataclass
class SectorDefinition:
    """A sector/industry grouping with keywords and metadata."""

    name: str
    keywords: List[str]
    regulators: List[str] = field(default_factory=list)
    supply_chain: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    rss_feeds: List[str] = field(default_factory=list)

    def matches(self, text: str) -> bool:
        """Return True if the text matches this sector's keywords
        and does not match any exclude keywords."""
        lower = text.lower()
        if any(kw.lower() in lower for kw in self.exclude_keywords):
            return False
        return any(kw.lower() in lower for kw in self.keywords)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "keywords": list(self.keywords),
            "regulators": list(self.regulators),
            "supply_chain": list(self.supply_chain),
            "exclude_keywords": list(self.exclude_keywords),
            "rss_feeds": list(self.rss_feeds),
        }


@dataclass
class SensorDefinition:
    """A data sensor that feeds the domain graph.

    Sensor types:
      - moving:  continuously updated value (e.g. stock price)
      - decaying: value loses relevance over time (e.g. sentiment)
      - event:   one-time signal (e.g. earnings announcement)
    """

    name: str
    sensor_type: str  # "moving", "decaying", "event"
    description: str
    sources: List[str] = field(default_factory=list)
    interval: str = "daily"
    weight: float = 1.0
    decay_rate: float = 0.15

    def __post_init__(self) -> None:
        valid_types = {"moving", "decaying", "event"}
        if self.sensor_type not in valid_types:
            raise ValueError(
                f"Invalid sensor_type '{self.sensor_type}', must be one of {valid_types}"
            )

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "sensor_type": self.sensor_type,
            "description": self.description,
            "sources": list(self.sources),
            "interval": self.interval,
            "weight": self.weight,
            "decay_rate": self.decay_rate,
        }


@dataclass
class ExtractionPattern:
    """A regex pattern used for signal classification during ingestion."""

    name: str
    pattern: str  # regex string
    node_type: str  # what node type to create when matched
    sentiment_hint: str = ""  # "positive", "negative", or ""

    _compiled: Any = field(default=None, repr=False, compare=False)

    def compile(self) -> re.Pattern:
        """Compile and cache the regex pattern."""
        if self._compiled is None:
            self._compiled = re.compile(self.pattern, re.IGNORECASE)
        return self._compiled

    def match(self, text: str) -> Optional[re.Match]:
        """Test if text matches this extraction pattern."""
        return self.compile().search(text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "pattern": self.pattern,
            "node_type": self.node_type,
            "sentiment_hint": self.sentiment_hint,
        }


# ---------------------------------------------------------------------------
# DomainPlugin — the contract
# ---------------------------------------------------------------------------


class DomainPlugin:
    """Base class for all domain plugins.

    A domain plugin encapsulates all domain-specific knowledge that the
    contextsynapse engine needs to operate in a particular vertical.
    The engine itself is domain-agnostic; it delegates to the active
    domain plugin for entity definitions, sector classification,
    extraction patterns, and LLM prompts.

    Subclasses can either:
      a) Define everything in Python by overriding attributes, or
      b) Load from a YAML file via ``DomainPlugin.from_yaml(path)``.

    Example::

        class FinanceDomain(DomainPlugin):
            domain_id = "finance"
            domain_name = "Financial Markets"
            version = "1.0"

            entity_types = [
                EntityType("company", ["name", "ticker", "sector", "market_cap"]),
                EntityType("person", ["name", "title", "company"]),
            ]
            edge_types = [
                EdgeType("MANAGES", from_type="person", to_type="company"),
            ]
            sectors = [
                SectorDefinition("Banking", keywords=["bank", "lending", "deposits"]),
            ]
            sensors = [
                SensorDefinition("price", "moving", "Stock price feed"),
            ]
            extraction_patterns = [
                ExtractionPattern("acquisition", r"acquir|takeover|merger", "event"),
            ]
            seed_prompt_template = "List key entities for {{ entity_name }}."
    """

    # -- Metadata ----------------------------------------------------------

    domain_id: str = ""
    domain_name: str = ""
    version: str = "1.0"

    # -- Schema ------------------------------------------------------------

    entity_types: List[EntityType] = []
    edge_types: List[EdgeType] = []

    # -- Sectors -----------------------------------------------------------

    sectors: List[SectorDefinition] = []

    # -- Sensors -----------------------------------------------------------

    sensors: List[SensorDefinition] = []

    # -- Extraction --------------------------------------------------------

    extraction_patterns: List[ExtractionPattern] = []

    # -- LLM ---------------------------------------------------------------

    seed_prompt_template: str = ""

    # -- Entity data store (override in subclass or populated from YAML) ---

    _entity_data: Dict[str, Dict[str, Any]] = {}

    # ======================================================================
    # Public API
    # ======================================================================

    def get_entity_data(self, entity_name: str) -> Dict[str, Any]:
        """Return known data for an entity (people, brands, aliases).

        Args:
            entity_name: Name to look up (case-insensitive).

        Returns:
            Dict with entity metadata, or empty dict if unknown.
        """
        key = entity_name.lower().strip()
        # Try exact match first
        if key in self._entity_data:
            return dict(self._entity_data[key])
        # Try partial match
        for ek, ev in self._entity_data.items():
            if key in ek or ek in key:
                return dict(ev)
            # Check aliases
            aliases = ev.get("aliases", [])
            if any(key == a.lower() for a in aliases):
                return dict(ev)
        return {}

    def get_sector(self, sector_name: str) -> Optional[SectorDefinition]:
        """Get sector definition by name (case-insensitive).

        Args:
            sector_name: Sector name to look up.

        Returns:
            SectorDefinition or None if not found.
        """
        lower = sector_name.lower().strip()
        for s in self.sectors:
            if s.name.lower() == lower:
                return s
        return None

    def get_sensor(self, sensor_name: str) -> Optional[SensorDefinition]:
        """Get sensor definition by name.

        Args:
            sensor_name: Sensor name to look up.

        Returns:
            SensorDefinition or None if not found.
        """
        for s in self.sensors:
            if s.name == sensor_name:
                return s
        return None

    def get_schema_template(self, entity_type: str) -> Dict[str, Any]:
        """Get schema node/edge types relevant to a given entity type.

        Returns a dict with ``node_types`` and ``edge_types`` that involve
        the requested entity type, useful for scoping graph operations.

        Args:
            entity_type: Entity type name (e.g. "company").

        Returns:
            Dict with ``node_types`` (list of EntityType dicts) and
            ``edge_types`` (list of EdgeType dicts).
        """
        node_types = [
            et.to_dict() for et in self.entity_types if et.name == entity_type
        ]
        edge_types = [
            et.to_dict()
            for et in self.edge_types
            if et.from_type == entity_type or et.to_type == entity_type
        ]
        return {"node_types": node_types, "edge_types": edge_types}

    def classify_entity(self, entity_name: str) -> str:
        """Classify what sector an entity belongs to.

        Checks the entity name against each sector's keyword list.
        Returns the first matching sector name, or "unknown".

        Args:
            entity_name: Entity name or description text.

        Returns:
            Sector name string.
        """
        # First check entity_data for explicit sector
        data = self.get_entity_data(entity_name)
        if data.get("sector"):
            return data["sector"]

        # Fall back to keyword matching
        for sector in self.sectors:
            if sector.matches(entity_name):
                return sector.name

        return "unknown"

    def extract_signals(self, text: str) -> List[Dict[str, Any]]:
        """Run all extraction patterns against text.

        Returns a list of matches with pattern name, node_type,
        sentiment_hint, and the matched span.

        Args:
            text: Text to scan for signals.

        Returns:
            List of dicts with keys: name, node_type, sentiment_hint, match.
        """
        results = []
        for ep in self.extraction_patterns:
            m = ep.match(text)
            if m:
                results.append(
                    {
                        "name": ep.name,
                        "node_type": ep.node_type,
                        "sentiment_hint": ep.sentiment_hint,
                        "match": m.group(0),
                    }
                )
        return results

    def info(self) -> Dict[str, Any]:
        """Return domain plugin metadata as a dict."""
        return {
            "domain_id": self.domain_id,
            "domain_name": self.domain_name,
            "version": self.version,
            "entity_types": len(self.entity_types),
            "edge_types": len(self.edge_types),
            "sectors": len(self.sectors),
            "sensors": len(self.sensors),
            "extraction_patterns": len(self.extraction_patterns),
            "has_seed_prompt": bool(self.seed_prompt_template),
            "entity_data_count": len(self._entity_data),
        }

    # ======================================================================
    # YAML loading
    # ======================================================================

    @classmethod
    def from_yaml(cls, yaml_path: str) -> "DomainPlugin":
        """Load a domain plugin from a YAML file.

        The YAML structure must have top-level keys matching the plugin
        attributes: domain_id, domain_name, version, entity_types,
        edge_types, sectors, sensors, extraction_patterns,
        seed_prompt_template, entity_data.

        Args:
            yaml_path: Path to the domain YAML file.

        Returns:
            A populated DomainPlugin instance.

        Raises:
            FileNotFoundError: If the YAML file does not exist.
            ValueError: If required fields are missing.
        """
        import yaml  # noqa: F811

        path = Path(yaml_path)
        if not path.exists():
            raise FileNotFoundError(f"Domain YAML not found: {yaml_path}")

        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        plugin = cls()

        # Metadata
        plugin.domain_id = data.get("domain_id", "")
        plugin.domain_name = data.get("domain_name", "")
        plugin.version = data.get("version", "1.0")

        if not plugin.domain_id:
            raise ValueError(f"domain_id is required in {yaml_path}")

        # Entity types
        plugin.entity_types = [
            EntityType(
                name=et["name"],
                properties=et.get("properties", []),
                description=et.get("description", ""),
            )
            for et in data.get("entity_types", [])
        ]

        # Edge types
        plugin.edge_types = [
            EdgeType(
                name=et["name"],
                from_type=et["from_type"],
                to_type=et["to_type"],
                description=et.get("description", ""),
            )
            for et in data.get("edge_types", [])
        ]

        # Sectors
        plugin.sectors = [
            SectorDefinition(
                name=s["name"],
                keywords=s.get("keywords", []),
                regulators=s.get("regulators", []),
                supply_chain=s.get("supply_chain", []),
                exclude_keywords=s.get("exclude_keywords", []),
                rss_feeds=s.get("rss_feeds", []),
            )
            for s in data.get("sectors", [])
        ]

        # Sensors
        plugin.sensors = [
            SensorDefinition(
                name=s["name"],
                sensor_type=s["sensor_type"],
                description=s.get("description", ""),
                sources=s.get("sources", []),
                interval=s.get("interval", "daily"),
                weight=float(s.get("weight", 1.0)),
                decay_rate=float(s.get("decay_rate", 0.15)),
            )
            for s in data.get("sensors", [])
        ]

        # Extraction patterns
        plugin.extraction_patterns = [
            ExtractionPattern(
                name=ep["name"],
                pattern=ep["pattern"],
                node_type=ep["node_type"],
                sentiment_hint=ep.get("sentiment_hint", ""),
            )
            for ep in data.get("extraction_patterns", [])
        ]

        # Seed prompt
        plugin.seed_prompt_template = data.get("seed_prompt_template", "")

        # Entity data — dict keyed by lowercase entity name
        raw_entities = data.get("entity_data", {})
        plugin._entity_data = {
            k.lower().strip(): v for k, v in raw_entities.items()
        }

        logger.info(
            "Loaded domain '%s' v%s from %s (%d entity types, %d sectors, %d sensors)",
            plugin.domain_id,
            plugin.version,
            yaml_path,
            len(plugin.entity_types),
            len(plugin.sectors),
            len(plugin.sensors),
        )

        return plugin

    def __repr__(self) -> str:
        return (
            f"DomainPlugin(domain_id={self.domain_id!r}, "
            f"domain_name={self.domain_name!r}, version={self.version!r})"
        )
