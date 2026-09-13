"""
Schema Loader
==============
Parses YAML extraction schemas and generates LLM prompts that constrain
entity extraction to only the types, fields, and relationships defined
in the schema.

Usage:
    from contextsynapse.extraction.schema_loader import load_schema, schema_to_prompt

    schema = load_schema("my_project_schema.yaml")
    prompt = schema_to_prompt(schema)
    # → LLM prompt that only extracts Person, System, Decision, etc.

Schema format:
    node_types:
      Person:
        fields: [name, role, department]
        required: [name]
      System:
        fields: [name, status, owner]
        required: [name]

    edge_types:
      MAINTAINS:
        source: Person
        target: System
      WORKS_AT:
        source: Person
        target: Organization

    fact_types:
      - type: Claim
        fields: [statement, confidence, source_text]
      - type: Metric
        fields: [name, value, unit, period]
"""

from __future__ import annotations

import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)


@dataclass
class NodeTypeDef:
    name: str
    fields: List[str] = field(default_factory=list)
    required: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class EdgeTypeDef:
    name: str
    source: str = ""  # source node type
    target: str = ""  # target node type
    properties: List[str] = field(default_factory=list)


@dataclass
class FactTypeDef:
    type: str
    fields: List[str] = field(default_factory=list)
    description: str = ""


@dataclass
class ContextUnitConfig:
    """Schema-driven configuration for ContextUnit generation.

    Allows each domain schema to control how CUs are built:
    - confidence_weights: evidence-type → weight (replaces flat 0.1 per fact)
    - priority_labels: node types that SubgraphProjector should always surface
    - claim_template: domain-specific phrasing for synthesized claims
    - domain_questions: seed questions for questions_answered field
    """
    confidence_weights: Dict[str, float] = field(default_factory=dict)
    priority_labels: List[str] = field(default_factory=list)
    claim_template: str = ""
    domain_questions: List[str] = field(default_factory=list)
    zoom_levels: Dict[str, str] = field(default_factory=dict)

    @property
    def has_custom_confidence(self) -> bool:
        return bool(self.confidence_weights)


@dataclass
class ExtractionSchema:
    """Parsed YAML schema that drives LLM extraction."""
    name: str = "default"
    node_types: Dict[str, NodeTypeDef] = field(default_factory=dict)
    edge_types: Dict[str, EdgeTypeDef] = field(default_factory=dict)
    fact_types: List[FactTypeDef] = field(default_factory=list)
    context_unit: Optional[ContextUnitConfig] = None

    @property
    def node_type_names(self) -> List[str]:
        return list(self.node_types.keys())

    @property
    def edge_type_names(self) -> List[str]:
        return list(self.edge_types.keys())

    @property
    def fact_type_names(self) -> List[str]:
        return [f.type for f in self.fact_types]


def load_schema(path: str) -> ExtractionSchema:
    """Load an extraction schema from a YAML file.

    Args:
        path: Path to YAML schema file.

    Returns:
        ExtractionSchema ready for use with EntityExtractor.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Schema file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    schema = ExtractionSchema(name=raw.get("name", path.stem))

    # Parse node types
    for type_name, typedef in raw.get("node_types", {}).items():
        if isinstance(typedef, dict):
            schema.node_types[type_name] = NodeTypeDef(
                name=type_name,
                fields=typedef.get("fields", []),
                required=typedef.get("required", []),
                description=typedef.get("description", ""),
            )
        elif isinstance(typedef, list):
            # Shorthand: just a list of fields
            schema.node_types[type_name] = NodeTypeDef(
                name=type_name, fields=typedef,
            )

    # Parse edge types
    for edge_name, edgedef in raw.get("edge_types", {}).items():
        if isinstance(edgedef, dict):
            schema.edge_types[edge_name] = EdgeTypeDef(
                name=edge_name,
                source=edgedef.get("source", ""),
                target=edgedef.get("target", ""),
                properties=edgedef.get("properties", []),
            )
        else:
            schema.edge_types[edge_name] = EdgeTypeDef(name=edge_name)

    # Parse fact types
    for factdef in raw.get("fact_types", []):
        if isinstance(factdef, dict):
            schema.fact_types.append(FactTypeDef(
                type=factdef.get("type", "Fact"),
                fields=factdef.get("fields", []),
                description=factdef.get("description", ""),
            ))

    # Parse context_unit config (domain-specific CU generation rules)
    cu_raw = raw.get("context_unit")
    if isinstance(cu_raw, dict):
        schema.context_unit = ContextUnitConfig(
            confidence_weights=cu_raw.get("confidence_weights", {}),
            priority_labels=cu_raw.get("priority_labels", []),
            claim_template=cu_raw.get("claim_template", ""),
            domain_questions=cu_raw.get("domain_questions", []),
            zoom_levels=cu_raw.get("zoom_levels", {}),
        )

    logger.info(
        "Loaded schema '%s': %d node types, %d edge types, %d fact types%s",
        schema.name, len(schema.node_types), len(schema.edge_types), len(schema.fact_types),
        " (with context_unit config)" if schema.context_unit else "",
    )
    return schema


def schema_to_prompt(schema: ExtractionSchema) -> str:
    """Generate an LLM extraction prompt from a schema.

    The prompt constrains the LLM to only extract entities, relationships,
    and facts that match the schema definition.
    """
    parts = [
        "Extract structured information from the text below.",
        "ONLY extract the entity types, relationship types, and fact types listed.",
        "Return valid JSON — no markdown, no explanation, ONLY the JSON object.",
        "",
    ]

    # Node types
    if schema.node_types:
        parts.append("ENTITY TYPES to extract:")
        for name, nt in schema.node_types.items():
            fields_str = ", ".join(nt.fields) if nt.fields else "any properties"
            req_str = f" (required: {', '.join(nt.required)})" if nt.required else ""
            desc = f" — {nt.description}" if nt.description else ""
            parts.append(f"  - {name}: fields=[{fields_str}]{req_str}{desc}")
        parts.append("")

    # Edge types
    if schema.edge_types:
        parts.append("RELATIONSHIP TYPES to extract:")
        for name, et in schema.edge_types.items():
            constraint = ""
            if et.source and et.target:
                constraint = f" ({et.source} -> {et.target})"
            parts.append(f"  - {name}{constraint}")
        parts.append("")

    # Fact types
    if schema.fact_types:
        parts.append("FACT TYPES to extract:")
        for ft in schema.fact_types:
            fields_str = ", ".join(ft.fields) if ft.fields else "statement, confidence"
            desc = f" — {ft.description}" if ft.description else ""
            parts.append(f"  - {ft.type}: fields=[{fields_str}]{desc}")
        parts.append("")

    # Output format — explicit and unambiguous
    parts.append("Return JSON with this EXACT structure:")
    parts.append('{')
    parts.append('  "entities": [')
    parts.append('    {')
    parts.append('      "name": "<entity name or title>",')
    parts.append('      "type": "<one of the entity types above>",')
    parts.append('      "description": "<brief description>",')
    parts.append('      "properties": {<field>: <value>, ...}')
    parts.append('    }')
    parts.append('  ],')
    parts.append('  "relationships": [')
    parts.append('    {')
    parts.append('      "source": "<source entity name>",')
    parts.append('      "target": "<target entity name>",')
    parts.append('      "type": "<one of the relationship types above>",')
    parts.append('      "properties": {}')
    parts.append('    }')
    parts.append('  ],')
    if schema.fact_types:
        parts.append('  "facts": [')
        parts.append('    {')
        parts.append('      "type": "<one of the fact types above>",')
        parts.append('      "statement": "<the fact as a self-contained sentence>",')
        parts.append('      "confidence": 0.9,')
        parts.append('      "source_text": "<exact quote from the text>"')
        parts.append('    }')
        parts.append('  ]')
    parts.append('}')
    parts.append("")
    parts.append("Rules:")
    parts.append("- For each entity, put the schema fields into the properties object")
    parts.append("- Use the entity's name/title as the \"name\" field")
    parts.append("- Create relationships BETWEEN entities you extracted — use the exact entity names")
    parts.append("- Only use relationship types from the list above")
    parts.append("- Return empty arrays if nothing matches")
    parts.append("- Do NOT wrap in markdown code blocks")
    parts.append("")
    parts.append("Text:")

    return "\n".join(parts)


# =====================================================================
# Default schemas per context type
# =====================================================================

_SCHEMAS_DIR = Path(__file__).parent.parent / "config" / "schemas"

_DEFAULT_SCHEMA_FILES: Dict[str, str] = {
    "software_dev": "sdlc.yaml",
    "knowledge_base": "knowledge_base.yaml",
    "rules": "rules.yaml",
    "database": "database.yaml",
    "decision": "decision.yaml",
    "web": "web.yaml",
    "education": "education.yaml",
    "finance": "finance.yaml",
    "healthcare": "healthcare.yaml",
    "legal": "legal.yaml",
    "retail": "retail.yaml",
    "session_graph": "session_graph.yaml",
    # domain schemas added 2026-04-14
    "news_article": "news_article.yaml",
    "meeting_notes": "meeting_notes.yaml",
    "support_ticket": "support_ticket.yaml",
    "hr_document": "hr_document.yaml",
    "marketing_content": "marketing_content.yaml",
    "api_spec": "api_spec.yaml",
    "architecture_doc": "architecture_doc.yaml",
    "requirements_doc": "requirements_doc.yaml",
    "invoice": "invoice.yaml",
    "resume": "resume.yaml",
    "contract": "contract.yaml",
    "research_paper": "research_paper.yaml",
    "ecommerce": "ecommerce.yaml",
    "real_estate": "real_estate.yaml",
}


def get_default_schema_path(context_type: str) -> Optional[Path]:
    """Return the filesystem path to the default schema YAML for a context type."""
    filename = _DEFAULT_SCHEMA_FILES.get(context_type)
    if not filename:
        return None
    path = _SCHEMAS_DIR / filename
    return path if path.exists() else None


def get_default_schema(context_type: str) -> Optional[ExtractionSchema]:
    """Load the default extraction schema for a context type."""
    path = get_default_schema_path(context_type)
    if path:
        return load_schema(str(path))
    return None


def get_default_schema_yaml(context_type: str) -> Optional[str]:
    """Return the raw YAML string of the default schema for a context type."""
    path = get_default_schema_path(context_type)
    if path and path.exists():
        return path.read_text(encoding="utf-8")
    return None


def list_default_schemas() -> Dict[str, Dict[str, Any]]:
    """List all default schemas with metadata."""
    result = {}
    for ctx_type, filename in _DEFAULT_SCHEMA_FILES.items():
        path = get_default_schema_path(ctx_type)
        if path and path.exists():
            try:
                schema = load_schema(str(path))
                result[ctx_type] = {
                    "name": schema.name,
                    "node_types": schema.node_type_names,
                    "edge_types": schema.edge_type_names,
                    "fact_types": schema.fact_type_names,
                    "file": filename,
                }
            except Exception as e:
                logger.warning("Failed to load default schema for %s: %s", ctx_type, e)
    return result


def load_schema_from_yaml_string(yaml_str: str, name: str = "custom") -> ExtractionSchema:
    """Parse an ExtractionSchema from a YAML string (no file needed)."""
    raw = yaml.safe_load(yaml_str)
    if not isinstance(raw, dict):
        raise ValueError("Schema must be a YAML mapping")

    schema = ExtractionSchema(name=raw.get("name", name))

    for type_name, typedef in raw.get("node_types", {}).items():
        if isinstance(typedef, dict):
            schema.node_types[type_name] = NodeTypeDef(
                name=type_name,
                fields=typedef.get("fields", []),
                required=typedef.get("required", []),
                description=typedef.get("description", ""),
            )
        elif isinstance(typedef, list):
            schema.node_types[type_name] = NodeTypeDef(name=type_name, fields=typedef)

    for edge_name, edgedef in raw.get("edge_types", {}).items():
        if isinstance(edgedef, dict):
            schema.edge_types[edge_name] = EdgeTypeDef(
                name=edge_name,
                source=edgedef.get("source", ""),
                target=edgedef.get("target", ""),
                properties=edgedef.get("properties", []),
            )
        else:
            schema.edge_types[edge_name] = EdgeTypeDef(name=edge_name)

    for factdef in raw.get("fact_types", []):
        if isinstance(factdef, dict):
            schema.fact_types.append(FactTypeDef(
                type=factdef.get("type", "Fact"),
                fields=factdef.get("fields", []),
                description=factdef.get("description", ""),
            ))

    return schema


def validate_schema_yaml(yaml_str: str) -> Dict[str, Any]:
    """Validate a YAML schema string.

    Returns ``{"valid": bool, "errors": [...], "warnings": [...]}``.
    """
    errors: List[str] = []
    warnings: List[str] = []

    try:
        raw = yaml.safe_load(yaml_str)
    except Exception as e:
        return {"valid": False, "errors": [f"Invalid YAML: {e}"], "warnings": []}

    if not isinstance(raw, dict):
        return {"valid": False, "errors": ["Schema must be a YAML mapping"], "warnings": []}

    if "node_types" not in raw:
        errors.append("Missing required field: node_types")
    elif not isinstance(raw["node_types"], dict):
        errors.append("node_types must be a mapping")
    else:
        for name, typedef in raw["node_types"].items():
            if isinstance(typedef, dict):
                if "fields" in typedef and not isinstance(typedef["fields"], list):
                    errors.append(f"node_types.{name}.fields must be a list")
                if "required" in typedef:
                    if not isinstance(typedef["required"], list):
                        errors.append(f"node_types.{name}.required must be a list")
                    else:
                        fields = typedef.get("fields", [])
                        for r in typedef["required"]:
                            if r not in fields:
                                warnings.append(
                                    f"node_types.{name}: required field '{r}' not in fields list"
                                )

    node_names = set(raw.get("node_types", {}).keys()) if isinstance(raw.get("node_types"), dict) else set()

    if "edge_types" in raw:
        if not isinstance(raw["edge_types"], dict):
            errors.append("edge_types must be a mapping")
        else:
            for ename, edef in raw["edge_types"].items():
                if isinstance(edef, dict):
                    src = edef.get("source", "")
                    tgt = edef.get("target", "")
                    if src and src not in node_names:
                        warnings.append(f"edge_types.{ename}: source '{src}' not in node_types")
                    if tgt and tgt not in node_names:
                        warnings.append(f"edge_types.{ename}: target '{tgt}' not in node_types")

    if "fact_types" in raw:
        if not isinstance(raw["fact_types"], list):
            errors.append("fact_types must be a list")
        else:
            for i, ft in enumerate(raw["fact_types"]):
                if not isinstance(ft, dict):
                    errors.append(f"fact_types[{i}] must be a mapping")
                elif "type" not in ft:
                    errors.append(f"fact_types[{i}] missing required field: type")

    return {"valid": len(errors) == 0, "errors": errors, "warnings": warnings}


def schema_to_yaml(schema: ExtractionSchema) -> str:
    """Serialize an ExtractionSchema back to YAML string."""
    data: Dict[str, Any] = {"name": schema.name}

    if schema.node_types:
        data["node_types"] = {}
        for name, nt in schema.node_types.items():
            entry: Dict[str, Any] = {}
            if nt.fields:
                entry["fields"] = nt.fields
            if nt.required:
                entry["required"] = nt.required
            if nt.description:
                entry["description"] = nt.description
            data["node_types"][name] = entry

    if schema.edge_types:
        data["edge_types"] = {}
        for name, et in schema.edge_types.items():
            entry = {}
            if et.source:
                entry["source"] = et.source
            if et.target:
                entry["target"] = et.target
            if et.properties:
                entry["properties"] = et.properties
            data["edge_types"][name] = entry

    if schema.fact_types:
        data["fact_types"] = []
        for ft in schema.fact_types:
            ft_entry: Dict[str, Any] = {"type": ft.type}
            if ft.fields:
                ft_entry["fields"] = ft.fields
            if ft.description:
                ft_entry["description"] = ft.description
            data["fact_types"].append(ft_entry)

    return yaml.dump(data, default_flow_style=False, sort_keys=False)


# =====================================================================
# Schema Registry — persist schemas as graph nodes
# =====================================================================

class SchemaRegistry:
    """Persist and manage extraction schemas as graph nodes.

    Schemas are stored in the default graph as Schema nodes with their
    full definition serialized in properties. This makes them:
    - Queryable via AIQL: SELECT * FROM Schema
    - Reusable across contexts
    - Versionable (update replaces the node)

    Usage:
        registry = SchemaRegistry(graph_registry)
        registry.save(schema)                          # persist
        schema = registry.get("software_project")      # load by name
        schemas = registry.list()                       # list all
        registry.delete("old_schema")                   # remove
    """

    def __init__(self, graph_registry):
        self._registry = graph_registry

    def _get_default_graph(self):
        graph = self._registry.get_graph("default")
        if not graph:
            graph = self._registry.create_graph("default")
        return graph

    def save(self, schema: ExtractionSchema) -> str:
        """Save a schema to the graph. Updates if name already exists."""
        from ..core.graph_structures import GraphNode

        graph = self._get_default_graph()
        now = datetime.now(timezone.utc).isoformat()

        # Check if schema with this name already exists
        existing_id = None
        for n in graph.get_all_nodes():
            if (getattr(n, 'label', '') == 'Schema' and
                    getattr(n, 'properties', {}).get('name') == schema.name):
                existing_id = n.id
                break

        schema_id = existing_id or secrets.token_hex(12)

        # Serialize the full schema definition
        definition = {
            "node_types": {
                name: {"fields": nt.fields, "required": nt.required, "description": nt.description}
                for name, nt in schema.node_types.items()
            },
            "edge_types": {
                name: {"source": et.source, "target": et.target, "properties": et.properties}
                for name, et in schema.edge_types.items()
            },
            "fact_types": [
                {"type": ft.type, "fields": ft.fields, "description": ft.description}
                for ft in schema.fact_types
            ],
        }

        node = GraphNode(
            id=schema_id,
            label="Schema",
            properties={
                "name": schema.name,
                "node_type_count": len(schema.node_types),
                "edge_type_count": len(schema.edge_types),
                "fact_type_count": len(schema.fact_types),
                "node_types": ",".join(schema.node_type_names),
                "edge_types": ",".join(schema.edge_type_names),
                "fact_types": ",".join(schema.fact_type_names),
                "definition": json.dumps(definition),
                "updated_at": now,
            },
        )
        graph.add_node(node, write_through=True)
        logger.info("Schema '%s' saved (%d nodes, %d edges, %d facts)",
                     schema.name, len(schema.node_types), len(schema.edge_types), len(schema.fact_types))
        return schema_id

    def get(self, name: str) -> Optional[ExtractionSchema]:
        """Load a schema by name from the graph."""
        graph = self._get_default_graph()
        for n in graph.get_all_nodes():
            if (getattr(n, 'label', '') == 'Schema' and
                    getattr(n, 'properties', {}).get('name') == name):
                return self._node_to_schema(n)
        return None

    def list(self) -> List[Dict[str, Any]]:
        """List all schemas."""
        graph = self._get_default_graph()
        schemas = []
        for n in graph.get_all_nodes():
            if getattr(n, 'label', '') == 'Schema':
                props = getattr(n, 'properties', {})
                schemas.append({
                    "name": props.get("name", "?"),
                    "node_types": props.get("node_types", "").split(",") if props.get("node_types") else [],
                    "edge_types": props.get("edge_types", "").split(",") if props.get("edge_types") else [],
                    "fact_types": props.get("fact_types", "").split(",") if props.get("fact_types") else [],
                    "updated_at": props.get("updated_at", ""),
                })
        return schemas

    def delete(self, name: str) -> bool:
        """Delete a schema by name."""
        graph = self._get_default_graph()
        for n in graph.get_all_nodes():
            if (getattr(n, 'label', '') == 'Schema' and
                    getattr(n, 'properties', {}).get('name') == name):
                try:
                    graph.remove_node(n.id)
                    return True
                except Exception:
                    return False
        return False

    def save_from_yaml(self, path: str) -> str:
        """Load a YAML schema file and save it to the registry."""
        schema = load_schema(path)
        return self.save(schema)

    @staticmethod
    def _node_to_schema(node) -> ExtractionSchema:
        """Convert a Schema graph node back to an ExtractionSchema."""
        props = getattr(node, 'properties', {})
        definition = json.loads(props.get("definition", "{}"))

        schema = ExtractionSchema(name=props.get("name", "unknown"))

        for name, typedef in definition.get("node_types", {}).items():
            schema.node_types[name] = NodeTypeDef(
                name=name,
                fields=typedef.get("fields", []),
                required=typedef.get("required", []),
                description=typedef.get("description", ""),
            )

        for name, edgedef in definition.get("edge_types", {}).items():
            schema.edge_types[name] = EdgeTypeDef(
                name=name,
                source=edgedef.get("source", ""),
                target=edgedef.get("target", ""),
                properties=edgedef.get("properties", []),
            )

        for factdef in definition.get("fact_types", []):
            schema.fact_types.append(FactTypeDef(
                type=factdef.get("type", "Fact"),
                fields=factdef.get("fields", []),
                description=factdef.get("description", ""),
            ))

        return schema
