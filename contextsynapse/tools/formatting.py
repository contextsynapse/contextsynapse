"""
Shared formatting helpers for tool results.

Used by all tool implementations to produce consistent, LLM-friendly output.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional


def format_nodes(nodes) -> str:
    """Format a list of nodes into readable text."""
    if not nodes:
        return "No nodes found."
    lines = []
    for n in nodes:
        if isinstance(n, dict):
            props = {k: v for k, v in n.items() if k not in ("id", "uuid", "label", "domain")}
            label = n.get("label", "Node")
            nid = n.get("id", n.get("uuid", "?"))
        else:
            props = getattr(n, "properties", {})
            label = getattr(n, "label", "Node")
            nid = getattr(n, "id", "?")
        prop_str = ", ".join(f"{k}: {v}" for k, v in props.items() if k not in ("domain",))
        lines.append(f"[{label}] (id:{nid}) {prop_str}")
    return "\n".join(lines)


def format_result(result) -> str:
    """Format an AIQL query result into readable text."""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        if not result.get("success", True):
            return f"Error: {result.get('error', 'Unknown error')}"
        nodes = result.get("nodes", [])
        edges = result.get("edges", [])
        message = result.get("message", "")
        parts = []
        if message:
            parts.append(message)
        if nodes:
            parts.append(f"Nodes ({len(nodes)}):\n{format_nodes(nodes)}")
        if edges:
            edge_lines = []
            for e in edges:
                if isinstance(e, dict):
                    src = str(e.get("source", "?"))
                    tgt = str(e.get("target", "?"))
                    lbl = e.get("label", "EDGE")
                    edge_lines.append(f"({src}) -[{lbl}]-> ({tgt})")
                else:
                    edge_lines.append(str(e))
            parts.append(f"Edges ({len(edges)}):\n" + "\n".join(edge_lines))
        if not parts:
            return json.dumps(result, indent=2, default=str)[:2000]
        return "\n\n".join(parts)
    if isinstance(result, list):
        return format_nodes(result)
    return str(result)


def serialize_props_to_aiql(props: dict) -> str:
    """Convert a properties dict to AIQL property string.

    Example: {"name": "Alice", "age": 30} -> 'name: "Alice", age: 30'
    """
    parts = []
    for k, v in props.items():
        if isinstance(v, str):
            escaped = v.replace('"', '\\"')
            parts.append(f'{k}: "{escaped}"')
        elif isinstance(v, bool):
            parts.append(f'{k}: {"TRUE" if v else "FALSE"}')
        elif isinstance(v, (int, float)):
            parts.append(f'{k}: {v}')
        else:
            escaped = str(v).replace('"', '\\"')
            parts.append(f'{k}: "{escaped}"')
    return ", ".join(parts)


def prop(node, key: str, default: str = "") -> str:
    """Get a property from a node (dict or object)."""
    if isinstance(node, dict):
        return str(node.get(key, default))
    if hasattr(node, "properties"):
        return str(node.properties.get(key, default))
    return str(getattr(node, key, default))
