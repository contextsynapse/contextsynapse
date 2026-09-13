"""SDLC Context Schema — loads from schemas/sdlc.yaml.

Single source of truth. All scanners, pipelines, and tools import from here.
The YAML file defines the schema. This module loads it and provides lookups.

To change the schema: edit schemas/sdlc.yaml. Everything else picks it up.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import yaml

logger = logging.getLogger(__name__)

# ── Load schema from YAML ────────────────────────────────────────────────────

_SCHEMA_PATH = Path(__file__).parent / "schemas" / "sdlc.yaml"


def _load_schema() -> Dict[str, Any]:
    """Load the SDLC schema from YAML."""
    try:
        with open(_SCHEMA_PATH, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except Exception as e:
        logger.warning("Failed to load SDLC schema from %s: %s", _SCHEMA_PATH, e)
        return {}


_SCHEMA = _load_schema()


def reload_schema():
    """Reload schema from disk (for hot-reload during development)."""
    global _SCHEMA, SDLC_LAYERS, ALL_NODE_TYPES, SDLC_EDGE_TYPES, NODE_REQUIRED_FIELDS
    global ISSUE_LABEL_MAP, DEFAULT_ISSUE_TYPE, JIRA_TYPE_MAP, SPEC_SECTION_MAP
    global _TYPE_TO_LAYER, _NODE_ID_PREFIXES
    _SCHEMA = _load_schema()
    _build_lookups()


# ── Build derived lookups from schema ────────────────────────────────────────

SDLC_LAYERS: Dict[str, dict] = {}
ALL_NODE_TYPES: List[str] = []
SDLC_EDGE_TYPES: Dict[str, Tuple[str, str]] = {}
NODE_REQUIRED_FIELDS: Dict[str, List[str]] = {}
ISSUE_LABEL_MAP: Dict[str, str] = {}
DEFAULT_ISSUE_TYPE: str = "UserStory"
JIRA_TYPE_MAP: Dict[str, str] = {}
SPEC_SECTION_MAP: List[Tuple[List[str], str]] = []
_TYPE_TO_LAYER: Dict[str, str] = {}
_NODE_ID_PREFIXES: Dict[str, str] = {}


def _build_lookups():
    """Build all lookup dicts from the loaded schema."""
    global SDLC_LAYERS, ALL_NODE_TYPES, SDLC_EDGE_TYPES, NODE_REQUIRED_FIELDS
    global ISSUE_LABEL_MAP, DEFAULT_ISSUE_TYPE, JIRA_TYPE_MAP, SPEC_SECTION_MAP
    global _TYPE_TO_LAYER, _NODE_ID_PREFIXES

    # ── Layers + Node Types ──────────────────────────────────────────
    SDLC_LAYERS.clear()
    all_types = []
    _TYPE_TO_LAYER.clear()
    _NODE_ID_PREFIXES.clear()

    for layer_name, layer_def in _SCHEMA.get("layers", {}).items():
        node_type_names = []
        node_types = layer_def.get("node_types", {})

        # Handle both formats: list of strings or dict of definitions
        if isinstance(node_types, list):
            node_type_names = node_types
        elif isinstance(node_types, dict):
            for type_name, type_def in node_types.items():
                node_type_names.append(type_name)
                if isinstance(type_def, dict):
                    # Extract properties schema
                    props = type_def.get("properties", {})
                    required = [k for k, v in props.items() if v == "required"]
                    NODE_REQUIRED_FIELDS[type_name] = required
                    # Extract ID prefix
                    prefix = type_def.get("id_prefix", type_name.lower())
                    _NODE_ID_PREFIXES[type_name] = prefix

        SDLC_LAYERS[layer_name] = {
            "node_types": node_type_names,
            "weight": layer_def.get("weight", 0.0),
            "optional": layer_def.get("optional", False),
        }

        for t in node_type_names:
            _TYPE_TO_LAYER[t] = layer_name

        all_types.extend(node_type_names)

    ALL_NODE_TYPES.clear()
    ALL_NODE_TYPES.extend(all_types)

    # ── Edge Types ───────────────────────────────────────────────────
    SDLC_EDGE_TYPES.clear()
    for edge_name, edge_def in _SCHEMA.get("relationships", {}).items():
        if isinstance(edge_def, dict):
            SDLC_EDGE_TYPES[edge_name] = (
                edge_def.get("source", ""),
                edge_def.get("target", ""),
            )

    # ── Source Mappings ──────────────────────────────────────────────
    sources = _SCHEMA.get("sources", {})

    # GitHub
    github = sources.get("github", {})
    ISSUE_LABEL_MAP.clear()
    ISSUE_LABEL_MAP.update(github.get("issue_label_map", {}))
    DEFAULT_ISSUE_TYPE = github.get("default_issue_type", "UserStory")

    # Jira
    jira = sources.get("jira", {})
    JIRA_TYPE_MAP.clear()
    JIRA_TYPE_MAP.update(jira.get("type_map", {}))

    # Spec document
    spec = sources.get("spec_document", {})
    SPEC_SECTION_MAP.clear()
    for entry in spec.get("section_map", []):
        SPEC_SECTION_MAP.append((
            entry.get("keywords", []),
            entry.get("node_type", ""),
        ))


# Build on import
_build_lookups()


# ── Public API ───────────────────────────────────────────────────────────────

def layer_for_node_type(node_type: str) -> Optional[str]:
    """Return the SDLC layer name for a node type, or None if unknown."""
    return _TYPE_TO_LAYER.get(node_type)


def is_sdlc_node_type(node_type: str) -> bool:
    """Return True if node_type is a recognised SDLC node type."""
    return node_type in _TYPE_TO_LAYER


def validate_node(node_type: str, properties: dict) -> List[str]:
    """Return list of missing required field names for the given node type."""
    required = NODE_REQUIRED_FIELDS.get(node_type, [])
    return [f for f in required if not properties.get(f)]


def id_prefix_for(node_type: str) -> str:
    """Return the ID prefix for a node type (e.g. 'req' for Requirement)."""
    return _NODE_ID_PREFIXES.get(node_type, node_type.lower())


def get_edge_description(edge_type: str) -> str:
    """Return the human-readable description of an edge type."""
    edge_def = _SCHEMA.get("relationships", {}).get(edge_type, {})
    return edge_def.get("description", "") if isinstance(edge_def, dict) else ""


def get_schema() -> Dict[str, Any]:
    """Return the raw schema dict (for UI/API exposure)."""
    return dict(_SCHEMA)
