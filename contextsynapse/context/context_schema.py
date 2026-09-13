"""
Context Graph Schema Loader
============================
Loads the category structure from context_schema.yaml (default)
or a per-context override YAML file.

Usage:
    from contextsynapse.context.context_schema import load_schema, get_categories

    schema = load_schema()                          # default schema
    schema = load_schema("path/to/custom.yaml")     # custom override
    cats = get_categories(schema)                    # {key: (label, edge, children)}
"""

import logging
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_DEFAULT_SCHEMA_PATH = Path(__file__).parent / "context_schema.yaml"

# Fallback if YAML can't be loaded (no pyyaml, file missing, etc.)
_FALLBACK_CATEGORIES = {
    "knowledge":  ("KnowledgeBase", "HAS_KNOWLEDGE_BASE", ["Document", "TextChunk", "Entity", "Fact"]),
    "code":       ("CodeBase", "HAS_CODE_BASE", ["ProjectSpec", "Module", "Task"]),
    "system":     ("SystemStore", "HAS_SYSTEM_STORE", ["Config", "Log", "PipelineRun"]),
    "user":       ("UserStore", "HAS_USER_STORE", ["Turn", "Decision", "AgentPresence"]),
    "web":        ("WebStore", "HAS_WEB_STORE", ["WebPage", "Document", "TextChunk"]),
    "generated":  ("GeneratedStore", "HAS_GENERATED_STORE", ["Summary", "Response", "Topic"]),
}


def load_schema(path: Optional[str] = None) -> Dict[str, Any]:
    """Load context schema from YAML file.

    Args:
        path: Path to YAML file. If None, uses the default bundled schema.

    Returns:
        Parsed schema dict with 'categories' key.
    """
    schema_path = Path(path) if path else _DEFAULT_SCHEMA_PATH
    try:
        import yaml
        with open(schema_path, "r", encoding="utf-8") as f:
            schema = yaml.safe_load(f)
        if not schema or "categories" not in schema:
            logger.warning("Schema file %s has no 'categories' key, using fallback", schema_path)
            return {"categories": _dict_from_fallback()}
        return schema
    except ImportError:
        logger.debug("pyyaml not installed, using fallback schema")
        return {"categories": _dict_from_fallback()}
    except FileNotFoundError:
        logger.debug("Schema file %s not found, using fallback", schema_path)
        return {"categories": _dict_from_fallback()}
    except Exception as e:
        logger.warning("Failed to load schema from %s: %s", schema_path, e)
        return {"categories": _dict_from_fallback()}


def _dict_from_fallback() -> Dict[str, Any]:
    """Convert _FALLBACK_CATEGORIES to schema dict format."""
    cats = {}
    for key, (label, edge, children) in _FALLBACK_CATEGORIES.items():
        cats[key] = {"label": label, "edge": edge, "children": children}
    return cats


def get_categories(schema: Optional[Dict[str, Any]] = None) -> Dict[str, Tuple[str, str, List[str]]]:
    """Extract categories as {key: (label, edge_label, children)} from schema.

    Args:
        schema: Parsed schema dict. If None, loads default.

    Returns:
        Dict mapping category key to (node_label, edge_label, child_types).
    """
    if schema is None:
        schema = load_schema()

    cats = schema.get("categories", {})
    result = {}
    for key, info in cats.items():
        label = info.get("label", key.capitalize())
        edge = info.get("edge", f"HAS_{key.upper()}")
        children = info.get("children", [])
        result[key] = (label, edge, children)
    return result
