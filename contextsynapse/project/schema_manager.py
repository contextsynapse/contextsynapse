"""Schema-driven pipeline infrastructure.

Loads domain schemas from YAML, binds them to namespaces, and provides
query APIs for consumers. The schema defines what types exist, how they
connect, how to process documents, and how consumers should behave.

To add a new domain: create a YAML file in schemas/, restart the server.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)


# ── Dataclasses ──────────────────────────────────────────────────────────────

@dataclass(frozen=True)
class SchemaMeta:
    name: str
    version: str
    description: str


@dataclass(frozen=True)
class NodeTypeDef:
    description: str
    properties: Dict[str, str]  # field_name -> "required" | "optional"
    id_prefix: str


@dataclass(frozen=True)
class RelationshipDef:
    source: str
    target: str
    description: str


@dataclass
class Schema:
    meta: SchemaMeta
    node_types: Dict[str, NodeTypeDef]
    relationships: Dict[str, RelationshipDef]
    processing: Dict[str, Any] = field(default_factory=dict)
    consumer_hints: Dict[str, Any] = field(default_factory=dict)
    layers: Optional[Dict] = None
    sources: Optional[Dict] = None
    scanners: Optional[Dict] = None
    _raw: Dict[str, Any] = field(default_factory=dict, repr=False)

    # ── Queries ──────────────────────────────────────────────────────

    def is_valid_node_type(self, label: str) -> bool:
        return label in self.node_types

    def is_valid_edge_type(self, edge: str) -> bool:
        return edge in self.relationships

    def validate_node(self, node_type: str, properties: dict) -> List[str]:
        """Return list of missing required field names."""
        nt = self.node_types.get(node_type)
        if not nt:
            return []
        return [f for f, req in nt.properties.items()
                if req == "required" and not properties.get(f)]

    def has_extension(self, name: str) -> bool:
        return getattr(self, name, None) is not None

    def get_consumer_hint(self, key: str, default: Any = None) -> Any:
        return self.consumer_hints.get(key, default)

    def get_processing(self, section: str, default: Any = None) -> Any:
        return self.processing.get(section, default)

    def layer_for(self, node_type: str) -> Optional[str]:
        """Return the layer name for a node type, or None."""
        if not self.layers:
            return None
        for layer_name, layer_def in self.layers.items():
            if node_type in layer_def.get("node_types", []):
                return layer_name
        return None

    # ── Factory ──────────────────────────────────────────────────────

    @classmethod
    def from_dict(cls, data: dict) -> "Schema":
        """Parse and validate a schema from a raw dict (e.g. loaded YAML)."""
        # ── Validate required sections ───────────────────────────────
        if "meta" not in data:
            raise ValueError("Schema missing required section: meta")
        if "relationships" not in data:
            raise ValueError("Schema missing required section: relationships")
        # node_types can be top-level OR extracted from layers
        has_node_types = "node_types" in data
        has_layers = "layers" in data and isinstance(data.get("layers"), dict)
        if not has_node_types and not has_layers:
            raise ValueError("Schema missing required section: node_types")

        raw_meta = data["meta"]
        meta = SchemaMeta(
            name=raw_meta.get("name", ""),
            version=str(raw_meta.get("version", "0.0")),
            description=raw_meta.get("description", ""),
        )

        # ── Parse node types ─────────────────────────────────────────
        node_types: Dict[str, NodeTypeDef] = {}

        def _parse_node_type(type_name: str, type_def) -> NodeTypeDef:
            if not isinstance(type_def, dict):
                type_def = {}
            return NodeTypeDef(
                description=type_def.get("description", ""),
                properties=type_def.get("properties", {}),
                id_prefix=type_def.get("id_prefix", type_name.lower()),
            )

        if has_node_types:
            for type_name, type_def in data["node_types"].items():
                node_types[type_name] = _parse_node_type(type_name, type_def)

        # Also extract node types from layers (SDLC format)
        if has_layers:
            for layer_def in data["layers"].values():
                if not isinstance(layer_def, dict):
                    continue
                layer_node_types = layer_def.get("node_types", {})
                if isinstance(layer_node_types, dict):
                    for type_name, type_def in layer_node_types.items():
                        if type_name not in node_types:
                            node_types[type_name] = _parse_node_type(type_name, type_def)

        # ── Parse relationships ──────────────────────────────────────
        relationships: Dict[str, RelationshipDef] = {}
        for edge_name, edge_def in data["relationships"].items():
            if not isinstance(edge_def, dict):
                continue
            source = edge_def.get("source", "")
            target = edge_def.get("target", "")
            # Validate source/target reference declared node types
            if source != "*" and source not in node_types:
                raise ValueError(
                    f"Relationship '{edge_name}' references unknown source type: {source}"
                )
            if target != "*" and target not in node_types:
                raise ValueError(
                    f"Relationship '{edge_name}' references unknown target type: {target}"
                )
            relationships[edge_name] = RelationshipDef(
                source=source,
                target=target,
                description=edge_def.get("description", ""),
            )

        return cls(
            meta=meta,
            node_types=node_types,
            relationships=relationships,
            processing=data.get("processing", {}),
            consumer_hints=data.get("consumer_hints", {}),
            layers=data.get("layers"),
            sources=data.get("sources"),
            scanners=data.get("scanners"),
            _raw=dict(data),
        )


# ── SchemaManager ────────────────────────────────────────────────────────────

class SchemaManager:
    """Loads, caches, and serves named schemas. One instance per process."""

    def __init__(self, schema_dir: Optional[Path] = None):
        self._schemas: Dict[str, Schema] = {}
        self._namespace_bindings: Dict[str, str] = {}
        self._schema_paths: Dict[str, Path] = {}
        self._schema_dir = schema_dir or Path(__file__).parent / "schemas"

    # ── Loading ──────────────────────────────────────────────────────

    def load(self, name: str, path: Path) -> Schema:
        """Parse YAML, validate base spec, cache by name."""
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
        except Exception as e:
            raise ValueError(f"Failed to load schema from {path}: {e}") from e

        schema = Schema.from_dict(data)
        self._schemas[name] = schema
        self._schema_paths[name] = path
        logger.info("Loaded schema '%s' v%s from %s", name, schema.meta.version, path)
        return schema

    def load_all(self) -> None:
        """Scan schema_dir for *.yaml, load each. File stem becomes schema name."""
        if not self._schema_dir.exists():
            logger.warning("Schema directory does not exist: %s", self._schema_dir)
            return
        for path in sorted(self._schema_dir.glob("*.yaml")):
            name = path.stem
            try:
                self.load(name, path)
            except Exception as e:
                logger.warning("Failed to load schema '%s': %s", name, e)

    def get(self, name: str) -> Schema:
        """Return cached schema by name. Raises KeyError if not loaded."""
        if name not in self._schemas:
            raise KeyError(f"Schema '{name}' not loaded")
        return self._schemas[name]

    def list_schemas(self) -> List[SchemaMeta]:
        """Return metadata for all loaded schemas."""
        return [s.meta for s in self._schemas.values()]

    # ── Namespace binding ────────────────────────────────────────────

    def bind(self, namespace: str, schema_name: str) -> None:
        """Bind a namespace to a schema. Raises KeyError if schema not loaded."""
        if schema_name not in self._schemas:
            raise KeyError(f"Schema '{schema_name}' not loaded")
        self._namespace_bindings[namespace] = schema_name
        logger.info("Bound namespace '%s' to schema '%s'", namespace, schema_name)

    def unbind(self, namespace: str) -> None:
        """Remove namespace binding."""
        self._namespace_bindings.pop(namespace, None)

    def for_namespace(self, namespace: str) -> Optional[Schema]:
        """Look up bound schema. Returns None if unbound."""
        schema_name = self._namespace_bindings.get(namespace)
        if schema_name is None:
            return None
        return self._schemas.get(schema_name)

    # ── Queries (used by consumers) ──────────────────────────────────

    def is_valid_node_type(self, ns: str, label: str) -> bool:
        """Check if label is valid. True if no schema bound (accept anything)."""
        schema = self.for_namespace(ns)
        if schema is None:
            return True
        return schema.is_valid_node_type(label)

    def is_valid_edge_type(self, ns: str, edge: str) -> bool:
        """Check if edge type is valid. True if no schema bound."""
        schema = self.for_namespace(ns)
        if schema is None:
            return True
        return schema.is_valid_edge_type(edge)

    def get_consumer_hint(self, ns: str, key: str, default: Any = None) -> Any:
        """Read consumer hint. Returns default if no schema or no hint."""
        schema = self.for_namespace(ns)
        if schema is None:
            return default
        return schema.get_consumer_hint(key, default)

    def get_layers(self, ns: str) -> Optional[Dict]:
        """Return layers if schema has them. None otherwise."""
        schema = self.for_namespace(ns)
        if schema is None:
            return None
        return schema.layers

    def get_node_types(self, ns: str) -> Optional[List[str]]:
        """Return node type names. None if no schema (accept anything)."""
        schema = self.for_namespace(ns)
        if schema is None:
            return None
        return list(schema.node_types.keys())

    def get_relationships(self, ns: str) -> Optional[Dict[str, Tuple[str, str]]]:
        """Return relationship name -> (source, target). None if no schema."""
        schema = self.for_namespace(ns)
        if schema is None:
            return None
        return {name: (r.source, r.target) for name, r in schema.relationships.items()}

    def get_processing_config(self, ns: str, section: str, default: Any = None) -> Any:
        """Read processing config section (chunking, search, etc.)."""
        schema = self.for_namespace(ns)
        if schema is None:
            return default
        return schema.get_processing(section, default)

    # ── Hot reload ───────────────────────────────────────────────────

    def reload(self, name: str) -> Schema:
        """Reload schema from disk."""
        path = self._schema_paths.get(name)
        if path is None:
            raise KeyError(f"Schema '{name}' has no known path")
        return self.load(name, path)


# ── Module-level singleton ───────────────────────────────────────────────────

_schema_manager: Optional[SchemaManager] = None


def get_schema_manager() -> SchemaManager:
    """Module-level convenience. Lazily creates and loads all schemas."""
    global _schema_manager
    if _schema_manager is None:
        _schema_manager = SchemaManager()
        _schema_manager.load_all()
    return _schema_manager


def reset_schema_manager() -> None:
    """Reset singleton (for testing)."""
    global _schema_manager
    _schema_manager = None
