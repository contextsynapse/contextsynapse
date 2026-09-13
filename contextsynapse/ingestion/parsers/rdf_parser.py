"""
RDF / Turtle Parser
===================
Lightweight parser for RDF triples in Turtle (.ttl), N-Triples (.nt),
and basic RDF/XML formats.

Converts triples into normalised node/edge dicts.

If ``rdflib`` is installed it is used for full spec compliance;
otherwise a simple regex-based fallback handles common Turtle/N-Triples.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any, Dict, List, Set, Tuple

logger = logging.getLogger(__name__)


def _try_rdflib(text: str, fmt: str) -> Tuple[List[Dict], List[Dict]] | None:
    """Attempt to parse with rdflib if available."""
    try:
        import rdflib  # type: ignore
    except ImportError:
        return None

    g = rdflib.Graph()
    try:
        g.parse(data=text, format=fmt)
    except Exception as e:
        logger.warning("rdflib parse failed (%s): %s", fmt, e)
        return None

    nodes_map: Dict[str, Dict[str, Any]] = {}
    edges: List[Dict[str, Any]] = []

    for s, p, o in g:
        s_id = str(s)
        p_label = _local_name(str(p))

        if isinstance(o, rdflib.Literal):
            # Literal → property on subject node
            node = nodes_map.setdefault(s_id, {"id": s_id, "label": _local_name(s_id), "properties": {}})
            node["properties"][p_label] = str(o)
        else:
            # URI → edge between two nodes
            o_id = str(o)
            nodes_map.setdefault(s_id, {"id": s_id, "label": _local_name(s_id), "properties": {}})
            nodes_map.setdefault(o_id, {"id": o_id, "label": _local_name(o_id), "properties": {}})
            if p_label.lower() in ("type", "rdf:type", "a"):
                nodes_map[s_id]["label"] = _local_name(o_id)
            else:
                edges.append({
                    "id": str(uuid.uuid4()),
                    "source": s_id,
                    "target": o_id,
                    "label": p_label,
                    "properties": {},
                })

    return list(nodes_map.values()), edges


def _local_name(uri: str) -> str:
    """Extract local name from a URI (after # or last /)."""
    for sep in ("#", "/"):
        if sep in uri:
            return uri.rsplit(sep, 1)[-1]
    return uri


class RDFParser:
    """Parse RDF triples into node/edge dicts."""

    # Simple Turtle/N-Triples pattern:
    #   <subject> <predicate> <object> .
    #   <subject> <predicate> "literal" .
    _TRIPLE_RE = re.compile(
        r'<([^>]+)>\s+<([^>]+)>\s+(?:<([^>]+)>|"([^"]*)")\s*[.;]',
    )

    def parse(self, text: str, format_hint: str = "auto") -> Tuple[List[Dict], List[Dict]]:
        """
        Parse RDF text.

        Args:
            text: RDF content (Turtle, N-Triples, or RDF/XML).
            format_hint: "turtle", "nt", "xml", or "auto".

        Returns:
            (nodes, edges)
        """
        fmt = self._detect_format(text) if format_hint == "auto" else format_hint

        # Try rdflib first
        result = _try_rdflib(text, fmt)
        if result is not None:
            nodes, edges = result
            logger.info("RDF parsed (rdflib, %s): %d nodes, %d edges", fmt, len(nodes), len(edges))
            return nodes, edges

        # Fallback: regex-based for Turtle/N-Triples
        return self._parse_simple(text)

    def _detect_format(self, text: str) -> str:
        stripped = text.strip()
        if stripped.startswith("<?xml") or stripped.startswith("<rdf:RDF"):
            return "xml"
        if "@prefix" in text[:500] or "@base" in text[:500]:
            return "turtle"
        return "nt"

    def _parse_simple(self, text: str) -> Tuple[List[Dict], List[Dict]]:
        """Regex fallback for simple Turtle / N-Triples."""
        nodes_map: Dict[str, Dict[str, Any]] = {}
        edges: List[Dict[str, Any]] = []

        for m in self._TRIPLE_RE.finditer(text):
            s_uri, p_uri = m.group(1), m.group(2)
            o_uri, o_lit = m.group(3), m.group(4)

            s_id = s_uri
            p_label = _local_name(p_uri)

            nodes_map.setdefault(s_id, {"id": s_id, "label": _local_name(s_id), "properties": {}})

            if o_lit is not None:
                # Literal → property
                nodes_map[s_id]["properties"][p_label] = o_lit
            elif o_uri:
                # Object → edge
                o_id = o_uri
                nodes_map.setdefault(o_id, {"id": o_id, "label": _local_name(o_id), "properties": {}})
                if p_label.lower() in ("type",):
                    nodes_map[s_id]["label"] = _local_name(o_id)
                else:
                    edges.append({
                        "id": str(uuid.uuid4()),
                        "source": s_id,
                        "target": o_id,
                        "label": p_label,
                        "properties": {},
                    })

        nodes = list(nodes_map.values())
        logger.info("RDF parsed (regex fallback): %d nodes, %d edges", len(nodes), len(edges))
        return nodes, edges
