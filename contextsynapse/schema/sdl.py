"""
Enhanced Schema Definition Language (SDL)
==========================================
Typed field definitions, dedup policies, derivation rules, and edge constraints.

Backward compatible with legacy flat schemas (fields as string lists).

Usage:
    from contextsynapse.schema.sdl import EnhancedSchema

    # Parse from dict (YAML-loaded)
    schema = EnhancedSchema.from_dict(yaml.safe_load(open("schema.yaml")))

    # Access typed fields
    for name, field in schema.node_types["Person"].fields.items():
        print(f"{name}: {field.type}, required={field.required}")
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


# ── FieldDef ──────────────────────────────────────────────────────────

@dataclass
class FieldDef:
    """Typed field definition with validation constraints."""

    name: str
    type: str = "string"          # string, int, float, bool, enum, date
    required: bool = False
    indexed: bool = False         # BM25/vector indexed
    pattern: Optional[str] = None  # regex for string fields
    enum_values: Optional[List[str]] = None
    range_min: Optional[float] = None
    range_max: Optional[float] = None
    default: Any = None

    def validate(self, value: Any) -> bool:
        """Validate a value against this field's constraints.

        Returns True if the value is acceptable, False otherwise.
        None is always accepted (optional field not provided).
        """
        if value is None:
            return True

        # Type checks
        if self.type == "int":
            if not isinstance(value, int) or isinstance(value, bool):
                return False
        elif self.type == "float":
            if not isinstance(value, (int, float)) or isinstance(value, bool):
                return False
        elif self.type == "bool":
            if not isinstance(value, bool):
                return False
        elif self.type == "enum":
            if not self.enum_values:
                return False
            if value not in self.enum_values:
                return False
        elif self.type == "date":
            if isinstance(value, str):
                # Basic ISO date validation
                if not re.match(r"^\d{4}-\d{2}-\d{2}", value):
                    return False
            else:
                return False
        # string type: accept anything (coerce)

        # Pattern check (string fields only)
        if self.pattern is not None and isinstance(value, str):
            if not re.search(self.pattern, value):
                return False

        # Range checks (numeric fields)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if self.range_min is not None and value < self.range_min:
                return False
            if self.range_max is not None and value > self.range_max:
                return False

        return True

    @classmethod
    def from_dict(cls, name: str, raw: Dict[str, Any]) -> FieldDef:
        """Parse a FieldDef from a dict (enhanced YAML format)."""
        return cls(
            name=name,
            type=raw.get("type", "string"),
            required=raw.get("required", False),
            indexed=raw.get("indexed", False),
            pattern=raw.get("pattern"),
            enum_values=raw.get("values"),  # YAML uses 'values' for enum
            range_min=raw.get("range_min"),
            range_max=raw.get("range_max"),
            default=raw.get("default"),
        )


# ── DedupPolicy ───────────────────────────────────────────────────────

@dataclass
class DedupPolicy:
    """Deduplication policy for node types."""

    key: List[str] = field(default_factory=lambda: ["name"])
    merge: str = "latest_wins"  # latest_wins, merge_properties, keep_both

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> DedupPolicy:
        return cls(
            key=raw.get("key", ["name"]),
            merge=raw.get("merge", "latest_wins"),
        )


# ── NodeTypeDef ───────────────────────────────────────────────────────

@dataclass
class NodeTypeDef:
    """Node type definition with typed fields and dedup policy."""

    name: str
    fields: Dict[str, FieldDef] = field(default_factory=dict)
    dedup: DedupPolicy = field(default_factory=DedupPolicy)
    min_confidence: float = 0.0
    description: str = ""
    constraints: List[Dict] = field(default_factory=list)

    @property
    def required_fields(self) -> List[str]:
        """Return names of required fields."""
        return [name for name, f in self.fields.items() if f.required]

    @property
    def indexed_fields(self) -> List[str]:
        """Return names of indexed fields."""
        return [name for name, f in self.fields.items() if f.indexed]


# ── EdgeTypeDef ───────────────────────────────────────────────────────

@dataclass
class EdgeTypeDef:
    """Edge type definition with cardinality and property constraints."""

    name: str
    source: str = ""
    target: str = ""
    cardinality: str = "many_to_many"  # one_to_one, one_to_many, many_to_many
    properties: Dict[str, FieldDef] = field(default_factory=dict)
    inference_rules: List[Dict] = field(default_factory=list)


# ── DerivationRule ────────────────────────────────────────────────────

@dataclass
class DerivationRule:
    """Rule that triggers actions when conditions are met in the graph."""

    when: Dict[str, Any] = field(default_factory=dict)
    action: str = ""       # create_cu, boost, create_edge, alert
    params: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> DerivationRule:
        return cls(
            when=raw.get("when", {}),
            action=raw.get("action", ""),
            params=raw.get("params", {}),
        )


# ── ExtractionConfig ─────────────────────────────────────────────────

@dataclass
class ExtractionConfig:
    """Schema-defined extraction strategy."""
    strategy: str = "article"
    parser: str = "text"
    chunk_method: str = "paragraph"
    stages: List[str] = field(default_factory=list)
    signals: List[str] = field(default_factory=list)
    significance_threshold: float = 0.3
    topic_clustering: bool = False
    topic_method: str = "llm_with_fallback"
    field_mapping: Dict[str, str] = field(default_factory=dict)
    edge_fields: Dict[str, str] = field(default_factory=dict)


# ── EnhancedSchema ────────────────────────────────────────────────────

@dataclass
class EnhancedSchema:
    """Full schema definition supporting both legacy flat and enhanced formats."""

    name: str = "default"
    version: str = "1.0"
    ontology: str = ""
    node_types: Dict[str, NodeTypeDef] = field(default_factory=dict)
    edge_types: Dict[str, EdgeTypeDef] = field(default_factory=dict)
    derivation_rules: List[DerivationRule] = field(default_factory=list)
    extraction: Optional[ExtractionConfig] = None
    strict_mode: bool = False

    @classmethod
    def from_dict(cls, raw: Dict[str, Any]) -> EnhancedSchema:
        """Parse a schema from a YAML-loaded dict.

        Handles both legacy flat format and enhanced dict format:

        Legacy:
            node_types:
              Person:
                fields: [name, age, role]
                required: [name]

        Enhanced:
            node_types:
              Person:
                fields:
                  name: {type: string, required: true, indexed: true}
                  age: {type: int, range_min: 0}
        """
        schema = cls(
            name=raw.get("name", "default"),
            version=raw.get("version", "1.0"),
            ontology=raw.get("ontology", ""),
            strict_mode=raw.get("strict_mode", False),
        )

        # Parse node types
        for type_name, typedef in raw.get("node_types", {}).items():
            if not isinstance(typedef, dict):
                # Bare node type name with no definition
                schema.node_types[type_name] = NodeTypeDef(name=type_name)
                continue

            raw_fields = typedef.get("fields", {})

            if isinstance(raw_fields, list):
                # Legacy flat format: fields is a list of strings
                required_names = set(typedef.get("required", []))
                fields = {
                    fname: FieldDef(
                        name=fname,
                        required=fname in required_names,
                    )
                    for fname in raw_fields
                }
            elif isinstance(raw_fields, dict):
                # Enhanced format: fields is a dict of dicts
                fields = {
                    fname: FieldDef.from_dict(fname, fdef) if isinstance(fdef, dict)
                    else FieldDef(name=fname)
                    for fname, fdef in raw_fields.items()
                }
            else:
                fields = {}

            # Parse dedup
            raw_dedup = typedef.get("dedup")
            dedup = DedupPolicy.from_dict(raw_dedup) if isinstance(raw_dedup, dict) else DedupPolicy()

            schema.node_types[type_name] = NodeTypeDef(
                name=type_name,
                fields=fields,
                dedup=dedup,
                min_confidence=typedef.get("min_confidence", 0.0),
                description=typedef.get("description", ""),
                constraints=typedef.get("constraints", []),
            )

        # Parse edge types
        for edge_name, edgedef in raw.get("edge_types", {}).items():
            if not isinstance(edgedef, dict):
                schema.edge_types[edge_name] = EdgeTypeDef(name=edge_name)
                continue

            # Parse edge properties (enhanced format: dict of field defs)
            raw_props = edgedef.get("properties", {})
            if isinstance(raw_props, dict):
                properties = {
                    pname: FieldDef.from_dict(pname, pdef) if isinstance(pdef, dict)
                    else FieldDef(name=pname)
                    for pname, pdef in raw_props.items()
                }
            else:
                properties = {}

            schema.edge_types[edge_name] = EdgeTypeDef(
                name=edge_name,
                source=edgedef.get("source", ""),
                target=edgedef.get("target", ""),
                cardinality=edgedef.get("cardinality", "many_to_many"),
                properties=properties,
                inference_rules=edgedef.get("inference_rules", []),
            )

        # Parse extraction config
        ext_raw = raw.get("extraction")
        if isinstance(ext_raw, dict):
            schema.extraction = ExtractionConfig(
                strategy=ext_raw.get("strategy", "article"),
                parser=ext_raw.get("parser", "text"),
                chunk_method=ext_raw.get("chunk_method", "paragraph"),
                stages=ext_raw.get("stages", []),
                signals=ext_raw.get("signals", []),
                significance_threshold=ext_raw.get("significance_threshold", 0.3),
                topic_clustering=ext_raw.get("topic_clustering", False),
                topic_method=ext_raw.get("topic_method", "llm_with_fallback"),
                field_mapping=ext_raw.get("field_mapping", {}),
                edge_fields=ext_raw.get("edge_fields", {}),
            )

        # Parse derivation rules
        for rule_raw in raw.get("derivation_rules", []):
            if isinstance(rule_raw, dict):
                schema.derivation_rules.append(DerivationRule.from_dict(rule_raw))

        return schema
