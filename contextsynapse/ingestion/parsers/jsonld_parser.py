"""
JSON-LD Parser
==============
Parse JSON-LD documents into normalised node/edge dicts.

Handles:
- Single objects with @id, @type
- @graph arrays
- Nested objects (flattened to edges)
- Plain JSON with {nodes, edges} structure (passthrough)
"""

from __future__ import annotations

import json
import logging
import uuid
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def _local_name(uri: str) -> str:
    """Extract local name from a URI or compact IRI."""
    if not isinstance(uri, str):
        return str(uri)
    for sep in ("#", "/", ":"):
        if sep in uri:
            return uri.rsplit(sep, 1)[-1]
    return uri


class JSONLDParser:
    """Parse JSON-LD into node/edge dicts."""

    def parse(self, text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Parse JSON-LD text.

        Returns:
            (nodes, edges)
        """
        try:
            data = json.loads(text)
        except json.JSONDecodeError as e:
            logger.error("JSON-LD parse error: %s", e)
            return [], []

        # Plain graph JSON: {nodes: [...], edges: [...]}
        if isinstance(data, dict) and ("nodes" in data or "vertices" in data):
            return self._parse_plain_graph(data)

        # JSON-LD with @graph
        if isinstance(data, dict) and "@graph" in data:
            return self._parse_graph_array(data["@graph"], data.get("@context", {}))

        # JSON-LD single object or array
        if isinstance(data, list):
            return self._parse_graph_array(data, {})

        if isinstance(data, dict) and ("@id" in data or "@type" in data):
            return self._parse_graph_array([data], {})

        # Fallback: try as plain graph
        return self._parse_plain_graph(data)

    def _parse_plain_graph(self, data: dict) -> Tuple[List[Dict], List[Dict]]:
        """Parse plain JSON {nodes/vertices, edges/links}."""
        raw_nodes = data.get("nodes", data.get("vertices", []))
        raw_edges = data.get("edges", data.get("links", data.get("relationships", [])))

        nodes = []
        for n in (raw_nodes or []):
            if not isinstance(n, dict):
                continue
            nodes.append({
                "id": n.get("id", str(uuid.uuid4())),
                "label": n.get("label", n.get("type", "Entity")),
                "properties": {k: v for k, v in n.items() if k not in ("id", "label", "type")},
            })

        edges = []
        for e in (raw_edges or []):
            if not isinstance(e, dict):
                continue
            edges.append({
                "id": e.get("id", str(uuid.uuid4())),
                "source": e.get("source", e.get("from", "")),
                "target": e.get("target", e.get("to", "")),
                "label": e.get("label", e.get("type", e.get("relation", "RELATED_TO"))),
                "properties": {k: v for k, v in e.items()
                               if k not in ("id", "source", "target", "from", "to", "label", "type", "relation")},
            })

        logger.info("Plain graph JSON: %d nodes, %d edges", len(nodes), len(edges))
        return nodes, edges

    def _parse_graph_array(
        self, items: List[Dict], context: Any
    ) -> Tuple[List[Dict], List[Dict]]:
        """Parse JSON-LD @graph array into nodes and edges."""
        nodes_map: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []

        for item in items:
            if not isinstance(item, dict):
                continue
            node_id = item.get("@id", str(uuid.uuid4()))
            node_type = item.get("@type", "Entity")
            if isinstance(node_type, list):
                node_type = node_type[0] if node_type else "Entity"
            label = _local_name(node_type)

            props: Dict[str, Any] = {}
            for key, value in item.items():
                if key.startswith("@"):
                    continue
                prop_name = _local_name(key)

                # Nested object → edge
                if isinstance(value, dict) and ("@id" in value or "@type" in value):
                    target_id = value.get("@id", str(uuid.uuid4()))
                    edges.append({
                        "id": str(uuid.uuid4()),
                        "source": node_id,
                        "target": target_id,
                        "label": prop_name,
                        "properties": {},
                    })
                    # Ensure target node exists
                    if target_id not in nodes_map:
                        t_type = value.get("@type", "Entity")
                        if isinstance(t_type, list):
                            t_type = t_type[0] if t_type else "Entity"
                        nodes_map[target_id] = {
                            "id": target_id,
                            "label": _local_name(t_type),
                            "properties": {},
                        }
                elif isinstance(value, list):
                    # Array of references or literals
                    literals = []
                    for v in value:
                        if isinstance(v, dict) and "@id" in v:
                            edges.append({
                                "id": str(uuid.uuid4()),
                                "source": node_id,
                                "target": v["@id"],
                                "label": prop_name,
                                "properties": {},
                            })
                        elif isinstance(v, dict) and "@value" in v:
                            literals.append(v["@value"])
                        else:
                            literals.append(v)
                    if literals:
                        props[prop_name] = literals if len(literals) > 1 else literals[0]
                elif isinstance(value, dict) and "@value" in value:
                    props[prop_name] = value["@value"]
                else:
                    props[prop_name] = value

            nodes_map[node_id] = {
                "id": node_id,
                "label": label,
                "properties": props,
            }

        nodes = list(nodes_map.values())
        logger.info("JSON-LD parsed: %d nodes, %d edges", len(nodes), len(edges))
        return nodes, edges
