"""Context Layers — enrichment layers for the universal context graph.

A context layer is NOT a silo. It's a lens that:
  1. ADDS node types, edge types, sensors to the shared graph
  2. ADDS extraction patterns for signal classification
  3. CONNECTS to other layers via cross-layer edges
  4. ENRICHES entities it understands (adds properties, relationships)

Multiple layers can enrich the same entity:
  "Reliance" is enriched by finance layer (stock, earnings)
                             AND macro layer (GDP dependency)
                             AND supply_chain layer (crude oil input)
                             AND psychology layer (retail herding signal)

Layers don't own entities. The graph owns entities. Layers just add knowledge.
"""

from __future__ import annotations

import logging
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Schema data classes
# ---------------------------------------------------------------------------


@dataclass
class NodeSchema:
    """A node type contributed by a context layer."""

    type_name: str          # "Earnings", "ClinicalTrial", "WeatherEvent"
    properties: List[str]   # property names this node type carries
    layer: str              # which layer defined this type
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type_name": self.type_name,
            "properties": list(self.properties),
            "layer": self.layer,
            "description": self.description,
        }


@dataclass
class EdgeSchema:
    """A relationship type contributed by a context layer."""

    edge_type: str              # "IMPACTS", "DEPENDS_ON", "REGULATED_BY"
    from_types: List[str]       # can connect from these node types
    to_types: List[str]         # can connect to these node types
    propagation_weight: float = 1.0   # how much signal strength carries across
    decay_per_hop: float = 0.3        # signal loses 30% per hop by default
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edge_type": self.edge_type,
            "from_types": list(self.from_types),
            "to_types": list(self.to_types),
            "propagation_weight": self.propagation_weight,
            "decay_per_hop": self.decay_per_hop,
            "description": self.description,
        }


@dataclass
class SensorSpec:
    """A data sensor that feeds the context graph."""

    name: str
    sensor_type: str          # "moving", "decaying", "event"
    description: str
    sources: List[str] = field(default_factory=list)
    interval: str = "daily"
    weight: float = 1.0
    decay_rate: float = 0.15
    layer: str = ""

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
            "layer": self.layer,
        }


@dataclass
class ExtractionRule:
    """A regex pattern for signal classification, contributed by a layer."""

    name: str
    pattern: str              # regex string
    node_type: str            # what node type to create when matched
    sentiment_hint: str = ""  # "positive", "negative", or ""
    layer: str = ""

    _compiled: Any = field(default=None, repr=False, compare=False)

    def compile(self) -> re.Pattern:
        """Compile and cache the regex pattern."""
        if self._compiled is None:
            self._compiled = re.compile(self.pattern, re.IGNORECASE)
        return self._compiled

    def match(self, text: str) -> Optional[re.Match]:
        """Test if text matches this extraction rule."""
        return self.compile().search(text)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "pattern": self.pattern,
            "node_type": self.node_type,
            "sentiment_hint": self.sentiment_hint,
            "layer": self.layer,
        }


@dataclass
class CrossLayerEdge:
    """Defines how signals propagate BETWEEN layers.

    These edges connect entity types from different layers, enabling
    cross-domain signal propagation.  For example, a supply_chain layer
    might define that "commodity" entities impact "company" entities from
    the finance layer.
    """

    from_entity_type: str       # e.g. "commodity" (supply_chain layer)
    to_entity_type: str         # e.g. "company" (finance layer)
    edge_type: str              # e.g. "DEPENDS_ON"
    propagation_weight: float = 0.7   # signal carries 70% across
    description: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "from_entity_type": self.from_entity_type,
            "to_entity_type": self.to_entity_type,
            "edge_type": self.edge_type,
            "propagation_weight": self.propagation_weight,
            "description": self.description,
        }


# ---------------------------------------------------------------------------
# ContextLayer
# ---------------------------------------------------------------------------


class ContextLayer:
    """A context layer enriches the universal graph with domain knowledge.

    A layer does NOT own a silo.  It contributes node types, edge types,
    sensors, extraction rules, and cross-layer edges to the *shared* graph.
    Multiple layers can enrich the same entity — the graph owns entities,
    layers just add knowledge.

    Usage::

        layer = ContextLayer.from_yaml("layers/finance.yaml")
        if layer.enriches("Reliance Industries"):
            data = layer.get_entity_data("Reliance Industries")
            sensors = layer.get_sensors_for("Reliance Industries", sector="Energy")
    """

    def __init__(
        self,
        layer_id: str,
        layer_name: str,
        *,
        version: str = "1.0",
        description: str = "",
        node_schemas: Optional[List[NodeSchema]] = None,
        edge_schemas: Optional[List[EdgeSchema]] = None,
        sensors: Optional[List[SensorSpec]] = None,
        extraction_rules: Optional[List[ExtractionRule]] = None,
        cross_layer_edges: Optional[List[CrossLayerEdge]] = None,
        seed_prompt_template: str = "",
        known_entities: Optional[Dict[str, dict]] = None,
        categories: Optional[List[dict]] = None,
    ):
        self.layer_id = layer_id
        self.layer_name = layer_name
        self.version = version
        self.description = description

        self.node_schemas: List[NodeSchema] = node_schemas or []
        self.edge_schemas: List[EdgeSchema] = edge_schemas or []
        self.sensors: List[SensorSpec] = sensors or []
        self.extraction_rules: List[ExtractionRule] = extraction_rules or []
        self.cross_layer_edges: List[CrossLayerEdge] = cross_layer_edges or []

        self.seed_prompt_template = seed_prompt_template
        self.known_entities: Dict[str, dict] = known_entities or {}
        self.categories: List[dict] = categories or []

        # Stamp layer_id on child objects that reference it
        for ns in self.node_schemas:
            ns.layer = self.layer_id
        for sr in self.sensors:
            sr.layer = self.layer_id
        for er in self.extraction_rules:
            er.layer = self.layer_id

    # ------------------------------------------------------------------
    # Entity knowledge queries
    # ------------------------------------------------------------------

    def enriches(self, entity_name: str) -> bool:
        """Does this layer have knowledge about this entity?

        Checks the known_entities registry (case-insensitive) and also
        scans aliases stored within entity data.
        """
        key = entity_name.lower().strip()
        if key in self.known_entities:
            return True
        # Check aliases inside entity data values
        for _ek, ev in self.known_entities.items():
            aliases = ev.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [aliases]
            if any(key == a.lower().strip() for a in aliases):
                return True
            # Partial match — entity name contains the known key or vice-versa
            if key in _ek or _ek in key:
                return True
        return False

    def get_entity_data(self, entity_name: str) -> dict:
        """Return what this layer knows about an entity.

        Returns a dict with whatever keys the layer stores (aliases,
        sector, people, dependencies, etc.).  Empty dict if unknown.
        """
        key = entity_name.lower().strip()
        # Exact match
        if key in self.known_entities:
            return dict(self.known_entities[key])
        # Alias / partial match
        for ek, ev in self.known_entities.items():
            if key in ek or ek in key:
                return dict(ev)
            aliases = ev.get("aliases", [])
            if isinstance(aliases, str):
                aliases = [aliases]
            if any(key == a.lower().strip() for a in aliases):
                return dict(ev)
        return {}

    def get_sensors_for(
        self, entity_name: str, sector: str = ""
    ) -> List[SensorSpec]:
        """Which sensors should activate for this entity?

        Returns all sensors from this layer that are relevant:
          - Moving sensors always activate (they track live data).
          - Event sensors activate if entity sector overlaps with sensor
            source keywords.
          - Decaying sensors activate if entity data mentions related terms.
        """
        if not self.enriches(entity_name):
            return []

        entity_data = self.get_entity_data(entity_name)
        entity_text = f"{entity_name} {sector} {' '.join(str(v) for v in entity_data.values())}"
        entity_lower = entity_text.lower()

        relevant: List[SensorSpec] = []
        for sensor in self.sensors:
            if sensor.sensor_type == "moving":
                # Moving sensors always apply — they track real-time data
                relevant.append(sensor)
            elif sensor.sensor_type == "event":
                # Event sensors: low cost, activate broadly
                relevant.append(sensor)
            elif sensor.sensor_type == "decaying":
                # Decaying sensors: activate only when entity context overlaps
                sensor_text = f"{sensor.name} {sensor.description} {' '.join(sensor.sources)}"
                sensor_keywords = [
                    w for w in sensor_text.lower().split() if len(w) > 3
                ]
                overlap = sum(1 for kw in sensor_keywords if kw in entity_lower)
                if overlap >= 1:
                    relevant.append(sensor)
            else:
                relevant.append(sensor)

        return relevant

    def classify(self, entity_name: str) -> str:
        """Classify entity into this layer's categories.

        Checks entity_name and known entity data against category
        keywords.  Returns the first matching category name, or
        ``"uncategorized"``.
        """
        entity_data = self.get_entity_data(entity_name)
        # If entity data has explicit category/sector, use it
        for cat_key in ("category", "sector", "type"):
            if entity_data.get(cat_key):
                return str(entity_data[cat_key])

        # Match against category keyword lists
        entity_text = f"{entity_name} {' '.join(str(v) for v in entity_data.values())}".lower()
        for cat in self.categories:
            cat_name = cat.get("name", "")
            keywords = cat.get("keywords", [])
            if any(kw.lower() in entity_text for kw in keywords):
                return cat_name

        return "uncategorized"

    # ------------------------------------------------------------------
    # Extraction
    # ------------------------------------------------------------------

    def extract_signals(self, text: str) -> List[Dict[str, Any]]:
        """Run all extraction rules against text.

        Returns list of dicts: {name, node_type, sentiment_hint, match, layer}.
        """
        results = []
        for rule in self.extraction_rules:
            m = rule.match(text)
            if m:
                results.append({
                    "name": rule.name,
                    "node_type": rule.node_type,
                    "sentiment_hint": rule.sentiment_hint,
                    "match": m.group(0),
                    "layer": self.layer_id,
                })
        return results

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def info(self) -> Dict[str, Any]:
        """Return layer metadata as a dict."""
        return {
            "layer_id": self.layer_id,
            "layer_name": self.layer_name,
            "version": self.version,
            "description": self.description,
            "node_schemas": len(self.node_schemas),
            "edge_schemas": len(self.edge_schemas),
            "sensors": len(self.sensors),
            "extraction_rules": len(self.extraction_rules),
            "cross_layer_edges": len(self.cross_layer_edges),
            "known_entities": len(self.known_entities),
            "categories": len(self.categories),
            "has_seed_prompt": bool(self.seed_prompt_template),
        }

    def to_dict(self) -> Dict[str, Any]:
        """Full serialization for persistence / debugging."""
        return {
            "layer_id": self.layer_id,
            "layer_name": self.layer_name,
            "version": self.version,
            "description": self.description,
            "node_schemas": [ns.to_dict() for ns in self.node_schemas],
            "edge_schemas": [es.to_dict() for es in self.edge_schemas],
            "sensors": [s.to_dict() for s in self.sensors],
            "extraction_rules": [er.to_dict() for er in self.extraction_rules],
            "cross_layer_edges": [cl.to_dict() for cl in self.cross_layer_edges],
            "seed_prompt_template": self.seed_prompt_template,
            "known_entities": dict(self.known_entities),
            "categories": list(self.categories),
        }

    # ------------------------------------------------------------------
    # YAML loading
    # ------------------------------------------------------------------

    @classmethod
    def from_yaml(cls, path: str) -> "ContextLayer":
        """Load a context layer from a YAML file.

        Expected YAML structure::

            layer_id: finance
            layer_name: Financial Markets
            version: "1.0"
            description: Stock, earnings, analyst, regulatory signals

            node_schemas:
              - type_name: Earnings
                properties: [revenue, net_income, eps, period]
                description: Quarterly/annual earnings report

            edge_schemas:
              - edge_type: IMPACTS
                from_types: [Regulation, Policy]
                to_types: [Entity, Sector]
                propagation_weight: 0.9
                decay_per_hop: 0.3

            sensors:
              - name: price_feed
                sensor_type: moving
                description: Real-time price data
                sources: [exchange_api]
                interval: "1m"

            extraction_rules:
              - name: acquisition
                pattern: "acquir|takeover|merger"
                node_type: CorporateEvent
                sentiment_hint: ""

            cross_layer_edges:
              - from_entity_type: commodity
                to_entity_type: company
                edge_type: DEPENDS_ON
                propagation_weight: 0.7
                description: Commodity price impacts dependent companies

            categories:
              - name: Technology
                keywords: [software, hardware, cloud, SaaS]

            known_entities:
              reliance industries:
                aliases: [RIL, Reliance, RELIANCE.NS]
                sector: Conglomerate

            seed_prompt_template: |
              Discover key information about {{ entity_name }} ...

        Args:
            path: Path to the YAML file.

        Returns:
            A populated ContextLayer instance.

        Raises:
            FileNotFoundError: If the file does not exist.
            ValueError: If ``layer_id`` is missing.
        """
        import yaml

        filepath = Path(path)
        if not filepath.exists():
            raise FileNotFoundError(f"Layer YAML not found: {path}")

        with open(filepath, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        layer_id = data.get("layer_id", "")
        if not layer_id:
            raise ValueError(f"layer_id is required in {path}")

        node_schemas = [
            NodeSchema(
                type_name=ns["type_name"],
                properties=ns.get("properties", []),
                layer=layer_id,
                description=ns.get("description", ""),
            )
            for ns in data.get("node_schemas", [])
        ]

        edge_schemas = [
            EdgeSchema(
                edge_type=es["edge_type"],
                from_types=es.get("from_types", []),
                to_types=es.get("to_types", []),
                propagation_weight=float(es.get("propagation_weight", 1.0)),
                decay_per_hop=float(es.get("decay_per_hop", 0.3)),
                description=es.get("description", ""),
            )
            for es in data.get("edge_schemas", [])
        ]

        sensors = [
            SensorSpec(
                name=s["name"],
                sensor_type=s["sensor_type"],
                description=s.get("description", ""),
                sources=s.get("sources", []),
                interval=s.get("interval", "daily"),
                weight=float(s.get("weight", 1.0)),
                decay_rate=float(s.get("decay_rate", 0.15)),
                layer=layer_id,
            )
            for s in data.get("sensors", [])
        ]

        extraction_rules = [
            ExtractionRule(
                name=er["name"],
                pattern=er["pattern"],
                node_type=er["node_type"],
                sentiment_hint=er.get("sentiment_hint", ""),
                layer=layer_id,
            )
            for er in data.get("extraction_rules", [])
        ]

        cross_layer_edges = [
            CrossLayerEdge(
                from_entity_type=cl["from_entity_type"],
                to_entity_type=cl["to_entity_type"],
                edge_type=cl["edge_type"],
                propagation_weight=float(cl.get("propagation_weight", 0.7)),
                description=cl.get("description", ""),
            )
            for cl in data.get("cross_layer_edges", [])
        ]

        known_entities = {
            k.lower().strip(): v
            for k, v in data.get("known_entities", {}).items()
        }

        categories = data.get("categories", [])

        layer = cls(
            layer_id=layer_id,
            layer_name=data.get("layer_name", layer_id),
            version=data.get("version", "1.0"),
            description=data.get("description", ""),
            node_schemas=node_schemas,
            edge_schemas=edge_schemas,
            sensors=sensors,
            extraction_rules=extraction_rules,
            cross_layer_edges=cross_layer_edges,
            seed_prompt_template=data.get("seed_prompt_template", ""),
            known_entities=known_entities,
            categories=categories,
        )

        logger.info(
            "Loaded context layer '%s' v%s from %s "
            "(%d node types, %d edge types, %d sensors, %d cross-layer edges)",
            layer.layer_id,
            layer.version,
            path,
            len(layer.node_schemas),
            len(layer.edge_schemas),
            len(layer.sensors),
            len(layer.cross_layer_edges),
        )

        return layer

    def __repr__(self) -> str:
        return (
            f"ContextLayer(layer_id={self.layer_id!r}, "
            f"layer_name={self.layer_name!r}, version={self.version!r})"
        )
