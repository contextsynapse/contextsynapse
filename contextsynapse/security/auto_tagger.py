"""
Auto-Tagging & Classification
===============================
Automatically classify graph nodes, contexts, and graphs with sensitivity levels
based on content analysis (PII detection, keyword matching, pattern recognition).

Tags: public | internal | confidential | restricted

Usage::

    tagger = AutoTagger()
    tags = tagger.classify_node(node_properties)
    # {"sensitivity": "confidential", "tags": ["pii:email", "personal_data"], "pii_detected": True}

    tagger.tag_context(context_id, graph_registry, context_manager)
    # Scans all nodes, sets context-level sensitivity to highest found
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class ClassificationResult:
    """Result of classifying a piece of content."""
    sensitivity: str = "public"         # public | internal | confidential | restricted
    tags: List[str] = field(default_factory=list)
    pii_detected: bool = False
    confidence: float = 1.0
    reason: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "sensitivity": self.sensitivity,
            "tags": self.tags,
            "pii_detected": self.pii_detected,
            "confidence": self.confidence,
            "reason": self.reason,
        }


# Keyword patterns that indicate sensitivity levels
_CONFIDENTIAL_KEYWORDS = {
    "salary", "compensation", "ssn", "social security", "bank account",
    "credit card", "password", "secret key", "private key", "api key",
    "medical", "diagnosis", "patient", "health record", "prescription",
    "passport number", "aadhaar", "pan card",
}

_RESTRICTED_KEYWORDS = {
    "classified", "top secret", "board meeting", "acquisition target",
    "insider", "material nonpublic", "merger", "confidential strategy",
}

_INTERNAL_KEYWORDS = {
    "internal only", "do not share", "draft", "work in progress",
    "internal memo", "staff only", "employee",
}


class AutoTagger:
    """Auto-classify content and tag with sensitivity levels."""

    def __init__(self):
        from .pii import get_pii_detector
        self._pii = get_pii_detector()

    def classify_text(self, text: str) -> ClassificationResult:
        """Classify a text string by sensitivity level."""
        if not text:
            return ClassificationResult(sensitivity="public")

        tags = []
        reasons = []
        text_lower = text.lower()

        # 1. PII detection (highest priority)
        pii_result = self._pii.scan(text)
        pii_detected = pii_result.pii_found

        if pii_detected:
            for _, pii_type in pii_result.entities:
                tags.append(f"pii:{pii_type}")
            reasons.append(f"PII detected: {', '.join(set(t for _,t in pii_result.entities))}")

        # 2. Keyword-based classification
        sensitivity = pii_result.sensitivity if pii_detected else "public"

        for kw in _RESTRICTED_KEYWORDS:
            if kw in text_lower:
                sensitivity = "restricted"
                tags.append("classified")
                reasons.append(f"Restricted keyword: {kw}")
                break

        if sensitivity not in ("restricted",):
            for kw in _CONFIDENTIAL_KEYWORDS:
                if kw in text_lower:
                    if sensitivity not in ("restricted",):
                        sensitivity = "confidential"
                    tags.append("sensitive_data")
                    reasons.append(f"Confidential keyword: {kw}")
                    break

        if sensitivity == "public":
            for kw in _INTERNAL_KEYWORDS:
                if kw in text_lower:
                    sensitivity = "internal"
                    tags.append("internal")
                    reasons.append(f"Internal keyword: {kw}")
                    break

        return ClassificationResult(
            sensitivity=sensitivity,
            tags=tags,
            pii_detected=pii_detected,
            confidence=0.9 if pii_detected else 0.7,
            reason="; ".join(reasons) if reasons else "No sensitive content detected",
        )

    def classify_node(self, properties: Dict[str, Any]) -> ClassificationResult:
        """Classify a graph node's properties."""
        # Combine all text fields for analysis
        text_fields = ["content", "description", "name", "title", "statement",
                        "body", "text", "summary", "rationale"]
        combined = " ".join(
            str(properties.get(f, "")) for f in text_fields if properties.get(f)
        )
        return self.classify_text(combined)

    def tag_node(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Classify and add tags directly to node properties.

        Returns updated properties dict with sensitivity, tags, pii_detected.
        """
        result = self.classify_node(properties)
        tagged = dict(properties)
        tagged["sensitivity"] = result.sensitivity
        tagged["pii_detected"] = result.pii_detected

        existing_tags = tagged.get("tags", [])
        if isinstance(existing_tags, str):
            existing_tags = [t.strip() for t in existing_tags.split(",") if t.strip()]
        tagged["tags"] = list(set(existing_tags + result.tags))
        tagged["_auto_classified"] = True

        return tagged

    def tag_context(self, context_id: str, graph_registry=None,
                     context_manager=None) -> Dict[str, Any]:
        """Scan all nodes in a context and set context-level sensitivity.

        The context gets the HIGHEST sensitivity found in any node.
        """
        if not context_manager or not graph_registry:
            return {"error": "Missing context_manager or graph_registry"}

        ctx = context_manager.get_context(context_id)
        if not ctx:
            return {"error": f"Context {context_id} not found"}

        db = graph_registry.get_graph(ctx.graph_namespace, load_if_missing=True)
        if not db:
            return {"error": "Graph not available"}

        # Scan all nodes
        sensitivity_levels = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}
        max_sensitivity = "public"
        total_nodes = 0
        pii_count = 0
        tagged_count = 0

        for node in db.get_all_nodes():
            props = getattr(node, "properties", {}) or {}
            total_nodes += 1

            result = self.classify_node(props)

            if result.pii_detected:
                pii_count += 1

            # Update node with classification
            if not props.get("_auto_classified"):
                tagged_props = self.tag_node(props)
                try:
                    from ..core.graph_structures import GraphNode
                    db.add_node(GraphNode(
                        id=node.id, label=getattr(node, "label", ""),
                        properties=tagged_props,
                    ), write_through=True)
                    tagged_count += 1
                except Exception:
                    pass

            # Track highest sensitivity
            if sensitivity_levels.get(result.sensitivity, 0) > sensitivity_levels.get(max_sensitivity, 0):
                max_sensitivity = result.sensitivity

        # Update context-level sensitivity
        try:
            context_manager.update_context(context_id, {"sensitivity": max_sensitivity})
        except Exception:
            pass

        result = {
            "context_id": context_id,
            "total_nodes": total_nodes,
            "nodes_tagged": tagged_count,
            "pii_nodes": pii_count,
            "context_sensitivity": max_sensitivity,
        }
        logger.info("[AUTO-TAG] Context %s: %d nodes scanned, %d PII, sensitivity=%s",
                    context_id[:12], total_nodes, pii_count, max_sensitivity)
        return result


# Global singleton
_tagger: Optional[AutoTagger] = None

def get_auto_tagger() -> AutoTagger:
    global _tagger
    if _tagger is None:
        _tagger = AutoTagger()
    return _tagger
