"""
GraphML Parser
==============
Parse GraphML (XML-based graph format) into normalised node/edge dicts.

Spec: http://graphml.graphdrawing.org/
Handles: <node>, <edge>, <data> elements, nested <graph> (flattened).
"""

from __future__ import annotations

import logging
import uuid
import xml.etree.ElementTree as ET
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# GraphML namespace
_NS = {"gml": "http://graphml.graphdrawing.org/xmlns"}


class GraphMLParser:
    """Parse a GraphML string into lists of node/edge dicts."""

    def parse(self, text: str) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Parse GraphML XML text.

        Returns:
            (nodes, edges) — each item is a serialisable dict with
            id, label, properties.
        """
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            logger.error("GraphML parse error: %s", e)
            return [], []

        # Resolve namespace — some files omit the namespace entirely
        ns = ""
        if root.tag.startswith("{"):
            ns = root.tag.split("}")[0] + "}"

        # Build key map: key_id -> (attr_name, attr_type, for)
        key_map = self._parse_keys(root, ns)

        nodes: List[Dict[str, Any]] = []
        edges: List[Dict[str, Any]] = []

        # Walk all <graph> elements (may be nested)
        for graph_el in root.iter(f"{ns}graph"):
            self._parse_graph(graph_el, ns, key_map, nodes, edges)

        logger.info("GraphML parsed: %d nodes, %d edges", len(nodes), len(edges))
        return nodes, edges

    # ------------------------------------------------------------------

    def _parse_keys(self, root: ET.Element, ns: str) -> Dict[str, Dict[str, str]]:
        """Parse <key> definitions into a lookup map."""
        key_map: Dict[str, Dict[str, str]] = {}
        for key_el in root.iter(f"{ns}key"):
            kid = key_el.get("id", "")
            key_map[kid] = {
                "name": key_el.get("attr.name", kid),
                "type": key_el.get("attr.type", "string"),
                "for": key_el.get("for", "all"),
            }
        return key_map

    def _parse_graph(
        self,
        graph_el: ET.Element,
        ns: str,
        key_map: Dict,
        nodes: List[Dict],
        edges: List[Dict],
    ):
        """Parse a single <graph> element."""
        for node_el in graph_el.findall(f"{ns}node"):
            node = self._parse_node(node_el, ns, key_map)
            if node:
                nodes.append(node)

        for edge_el in graph_el.findall(f"{ns}edge"):
            edge = self._parse_edge(edge_el, ns, key_map)
            if edge:
                edges.append(edge)

    def _parse_node(self, el: ET.Element, ns: str, key_map: Dict) -> Dict[str, Any]:
        node_id = el.get("id", str(uuid.uuid4()))
        props = self._collect_data(el, ns, key_map)
        label = props.pop("label", props.pop("name", props.pop("type", "Entity")))
        return {
            "id": node_id,
            "label": label,
            "properties": {"_original_id": node_id, **props},
        }

    def _parse_edge(self, el: ET.Element, ns: str, key_map: Dict) -> Dict[str, Any]:
        source = el.get("source", "")
        target = el.get("target", "")
        if not source or not target:
            return {}
        edge_id = el.get("id", str(uuid.uuid4()))
        props = self._collect_data(el, ns, key_map)
        label = props.pop("label", props.pop("type", props.pop("relation", "RELATED_TO")))
        return {
            "id": edge_id,
            "source": source,
            "target": target,
            "label": label,
            "properties": props,
        }

    def _collect_data(self, el: ET.Element, ns: str, key_map: Dict) -> Dict[str, Any]:
        """Collect all <data> children into a property dict."""
        props: Dict[str, Any] = {}
        for data_el in el.findall(f"{ns}data"):
            key_id = data_el.get("key", "")
            value = (data_el.text or "").strip()
            if not value:
                continue
            info = key_map.get(key_id, {"name": key_id, "type": "string"})
            name = info["name"]
            props[name] = self._cast(value, info["type"])
        return props

    @staticmethod
    def _cast(value: str, type_hint: str) -> Any:
        try:
            if type_hint in ("int", "long"):
                return int(value)
            if type_hint in ("float", "double"):
                return float(value)
            if type_hint == "boolean":
                return value.lower() in ("true", "1", "yes")
        except (ValueError, TypeError):
            pass
        return value
