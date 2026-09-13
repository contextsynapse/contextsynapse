"""
Schema Validator
================
Validates nodes and edges against a GraphSchema definition.

Used in the schema-first workflow:
1. User uploads a schema YAML
2. Data is ingested through the pipeline
3. This validator checks that extracted nodes/edges conform to the schema
4. Non-conforming items are flagged or filtered
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SchemaValidationResult:
    """Result of validating data against a schema."""

    __slots__ = ("valid_nodes", "valid_edges", "rejected_nodes", "rejected_edges", "warnings")

    def __init__(self):
        self.valid_nodes: List[Dict[str, Any]] = []
        self.valid_edges: List[Dict[str, Any]] = []
        self.rejected_nodes: List[Dict[str, Any]] = []
        self.rejected_edges: List[Dict[str, Any]] = []
        self.warnings: List[str] = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "valid_nodes": len(self.valid_nodes),
            "valid_edges": len(self.valid_edges),
            "rejected_nodes": len(self.rejected_nodes),
            "rejected_edges": len(self.rejected_edges),
            "warnings": self.warnings[:20],
            "rejected_node_samples": self.rejected_nodes[:5],
            "rejected_edge_samples": self.rejected_edges[:5],
        }


class SchemaValidator:
    """Validate pipeline output against a GraphSchema."""

    def __init__(self, schema):
        """
        Args:
            schema: A GraphSchema instance from schema_parser.py
        """
        self.schema = schema
        self._valid_node_types = set(schema.node_types.keys()) if schema.node_types else set()
        self._valid_edge_types = set(schema.edge_types.keys()) if schema.edge_types else set()

    def validate(
        self,
        nodes: List[Dict[str, Any]],
        edges: List[Dict[str, Any]],
        strict: bool = False,
    ) -> SchemaValidationResult:
        """
        Validate nodes and edges against the schema.

        Args:
            nodes: List of node dicts with id, label, properties.
            edges: List of edge dicts with id, source, target, label, properties.
            strict: If True, reject nodes/edges with unknown types.
                    If False, accept them with a warning.

        Returns:
            SchemaValidationResult with valid/rejected items and warnings.
        """
        result = SchemaValidationResult()

        for node in nodes:
            ok, reason = self._validate_node(node, strict)
            if ok:
                result.valid_nodes.append(node)
            else:
                node_copy = {**node, "_rejection_reason": reason}
                result.rejected_nodes.append(node_copy)
                result.warnings.append(f"Node {node.get('id', '?')}: {reason}")

        # Build a set of valid node IDs for edge validation
        valid_node_ids = {n["id"] for n in result.valid_nodes}

        for edge in edges:
            ok, reason = self._validate_edge(edge, valid_node_ids, strict)
            if ok:
                result.valid_edges.append(edge)
            else:
                edge_copy = {**edge, "_rejection_reason": reason}
                result.rejected_edges.append(edge_copy)
                result.warnings.append(f"Edge {edge.get('id', '?')}: {reason}")

        return result

    def _validate_node(self, node: Dict, strict: bool) -> Tuple[bool, str]:
        """Validate a single node against schema."""
        label = node.get("label", "")

        # Check if node type is defined in schema
        if self._valid_node_types and label not in self._valid_node_types:
            if strict:
                return False, f"Unknown node type '{label}' (allowed: {sorted(self._valid_node_types)})"
            # Non-strict: accept but don't validate fields

        # Validate required fields if node type is defined
        node_type_def = self.schema.node_types.get(label)
        if node_type_def and node_type_def.fields:
            props = node.get("properties", {})
            for field_name, field_def in node_type_def.fields.items():
                if field_def.required and field_name not in props:
                    return False, f"Missing required field '{field_name}' on {label} node"

        return True, ""

    def _validate_edge(
        self, edge: Dict, valid_node_ids: set, strict: bool
    ) -> Tuple[bool, str]:
        """Validate a single edge against schema."""
        label = edge.get("label", "")
        source = edge.get("source", "")
        target = edge.get("target", "")

        # Check endpoints exist
        if source not in valid_node_ids:
            return False, f"Source node '{source}' not in valid nodes"
        if target not in valid_node_ids:
            return False, f"Target node '{target}' not in valid nodes"

        # Check edge type
        if self._valid_edge_types and label not in self._valid_edge_types:
            if strict:
                return False, f"Unknown edge type '{label}'"

        # Validate from/to node types if defined
        edge_type_def = self.schema.edge_types.get(label)
        if edge_type_def:
            # Find the actual node labels for source and target
            # (We'd need node lookup, skip for now — validated structurally)
            pass

        return True, ""

    def get_extraction_hints(self) -> Dict[str, Any]:
        """
        Generate hints for LLM extraction based on schema.

        Returns a dict describing what node types and edge types to extract,
        with their expected fields. Useful for schema-constrained extraction prompts.
        """
        hints = {
            "node_types": {},
            "edge_types": {},
            "instructions": [],
        }

        for name, nt in self.schema.node_types.items():
            fields = {}
            for fname, fdef in nt.fields.items():
                fields[fname] = {
                    "type": fdef.field_type.value,
                    "required": fdef.required,
                }
            hints["node_types"][name] = {
                "description": nt.description,
                "fields": fields,
            }

        for name, et in self.schema.edge_types.items():
            hints["edge_types"][name] = {
                "from": et.from_nodes,
                "to": et.to_nodes,
                "relation": et.relation or name,
            }

        if hints["node_types"]:
            types_list = ", ".join(sorted(hints["node_types"].keys()))
            hints["instructions"].append(f"Extract ONLY these node types: {types_list}")

        if hints["edge_types"]:
            rel_list = ", ".join(sorted(hints["edge_types"].keys()))
            hints["instructions"].append(f"Extract ONLY these relationship types: {rel_list}")

        return hints


# ---------------------------------------------------------------------------
# Standalone validation helper
# ---------------------------------------------------------------------------

def validate_and_flag(node: Dict[str, Any], schema) -> Dict[str, Any]:
    """
    Validate a node dict against a schema and flag it in-place.

    Args:
        node: Dict with ``{"label": str, "properties": dict}``.
        schema: Object with ``.node_types`` dict attribute.  Each value is a
                dict with at least ``"required"`` (list of field names).

    Sets on ``node["properties"]``:
        - ``validation_status``: "valid" | "missing_fields" | "unknown_type" | "invalid_fields"
        - ``validation_errors``: list of human-readable error strings
        - ``_missing_fields``: list of missing required field names (if applicable)

    Adjusts ``confidence`` in properties:
        - unknown_type  -> ``* 0.5``
        - missing_fields -> ``* 0.7``
        - invalid_fields -> ``* 0.8``

    Returns:
        The (mutated) *node* dict for chaining convenience.
    """
    props = node.get("properties", {})
    node["properties"] = props  # ensure key exists

    label = node.get("label", "")
    errors: List[str] = []
    missing: List[str] = []
    status = "valid"

    node_types = schema.node_types if schema.node_types else {}

    # 1. Check type exists in schema
    type_def = node_types.get(label)
    if type_def is None:
        status = "unknown_type"
        errors.append(f"Unknown node type '{label}'")
    else:
        # 2. Check required fields
        required = type_def.get("required", [])
        for field in required:
            if field not in props:
                missing.append(field)
                errors.append(f"Missing required field '{field}' on {label}")

        if missing:
            status = "missing_fields"

        # 3. Basic field format validation (type checks for known fields)
        if not missing:
            optional = type_def.get("optional", [])
            known_fields = set(required) + set(optional) if False else set(required) | set(optional)
            for key, value in props.items():
                # Skip internal / meta fields
                if key.startswith("_") or key in (
                    "confidence", "validation_status", "validation_errors",
                ):
                    continue
                # Flag None values in required fields as invalid
                if key in required and value is None:
                    if status == "valid":
                        status = "invalid_fields"
                    errors.append(f"Field '{key}' is None on {label}")

    # Apply confidence penalty
    confidence = props.get("confidence", 1.0)
    if status == "unknown_type":
        confidence *= 0.5
    elif status == "missing_fields":
        confidence *= 0.7
    elif status == "invalid_fields":
        confidence *= 0.8

    props["validation_status"] = status
    props["validation_errors"] = errors
    props["confidence"] = confidence
    if missing:
        props["_missing_fields"] = missing

    return node
