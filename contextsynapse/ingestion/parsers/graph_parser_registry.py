"""
Graph Parser Registry
=====================
Auto-detect graph format from file content and route to the right parser.
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


def detect_graph_format(text: str, filename: str = "") -> str:
    """
    Detect graph interchange format from content and filename.

    Returns:
        One of: "graphml", "rdf_xml", "turtle", "ntriples", "jsonld",
                "json_graph", "unknown".
    """
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Extension-based hints
    ext_map = {
        "graphml": "graphml",
        "gml": "graphml",
        "ttl": "turtle",
        "nt": "ntriples",
        "nq": "ntriples",
        "rdf": "rdf_xml",
        "owl": "rdf_xml",
        "jsonld": "jsonld",
    }
    if ext in ext_map:
        return ext_map[ext]

    # Content-based detection
    stripped = text.strip()

    # GraphML (XML with graphml tag)
    if "<graphml" in stripped[:500] or "graphml.graphdrawing.org" in stripped[:500]:
        return "graphml"

    # RDF/XML
    if stripped.startswith("<?xml") and ("<rdf:RDF" in stripped[:1000] or "rdf:Description" in stripped[:1000]):
        return "rdf_xml"

    # Turtle
    if "@prefix" in stripped[:500] or "@base" in stripped[:500]:
        return "turtle"

    # N-Triples (lines of <uri> <uri> <uri>|"lit" .)
    lines = stripped.split("\n", 5)
    if len(lines) >= 2 and all(l.strip().startswith("<") and l.strip().endswith(".") for l in lines[:3] if l.strip()):
        return "ntriples"

    # JSON-LD or plain JSON graph
    if stripped.startswith("{") or stripped.startswith("["):
        if "@graph" in stripped[:2000] or "@context" in stripped[:2000] or "@id" in stripped[:2000]:
            return "jsonld"
        if '"nodes"' in stripped[:2000] or '"vertices"' in stripped[:2000]:
            return "json_graph"

    return "unknown"


class GraphParserRegistry:
    """Route to the correct graph parser based on format detection."""

    def parse(
        self, text: str, filename: str = "", format_hint: str = "auto"
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """
        Parse graph text into (nodes, edges).

        Args:
            text: Raw file content.
            filename: Original filename for extension-based hints.
            format_hint: Override auto-detection.

        Returns:
            (nodes, edges)
        """
        fmt = detect_graph_format(text, filename) if format_hint == "auto" else format_hint

        if fmt == "graphml":
            from .graphml_parser import GraphMLParser
            return GraphMLParser().parse(text)

        if fmt in ("turtle", "ntriples", "rdf_xml"):
            from .rdf_parser import RDFParser
            rdf_fmt = {"turtle": "turtle", "ntriples": "nt", "rdf_xml": "xml"}[fmt]
            return RDFParser().parse(text, format_hint=rdf_fmt)

        if fmt in ("jsonld", "json_graph"):
            from .jsonld_parser import JSONLDParser
            return JSONLDParser().parse(text)

        logger.warning("Unknown graph format for '%s', trying JSON-LD fallback", filename)
        from .jsonld_parser import JSONLDParser
        return JSONLDParser().parse(text)
