"""SpecScanner — parse structured spec documents into SDLC nodes.

Parses markdown/text specs (from ChatGPT, Claude, PRDs, etc.) into typed
SDLC nodes by recognizing section headers:

  ## Requirements / ## Functional Requirements  → Requirement
  ## Architecture / ## Design Decisions          → ArchDecision
  ## Constraints / ## Non-Functional              → Constraint
  ## Acceptance Criteria / ## Test Cases          → TestCase
  ## Goals / ## Objectives                        → Goal
  ## User Stories                                 → UserStory

Usage::

    from contextsynapse.ingestion.universal.operators.spec_scanner import scan_spec_document
    nodes = scan_spec_document(spec_text, source="chatgpt-spec.md")
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)

# ── Section → Node Type mapping (loaded from schema) ─────────────────────────

from contextsynapse.project.sdlc_schema import SPEC_SECTION_MAP as _SECTION_MAP

_MAX_SPEC_NODES = 50


def _safe_id(text: str) -> str:
    """Convert text to a safe node ID slug."""
    s = re.sub(r'[^a-zA-Z0-9_-]', '-', text.lower())
    s = re.sub(r'-+', '-', s).strip('-')
    return s[:60]


def _classify_section(header: str) -> str:
    """Map a section header to an SDLC node type. Returns '' if no match."""
    header_lower = header.lower().strip()
    for keywords, node_type in _SECTION_MAP:
        for kw in keywords:
            if kw in header_lower:
                return node_type
    return ""


def _extract_items(body: str) -> List[str]:
    """Extract individual items from a section body.

    Handles:
    - Bullet lists (- item, * item, • item)
    - Numbered lists (1. item, 1) item)
    - Paragraphs separated by blank lines
    """
    # Try bullet/numbered list first
    items = re.findall(r'(?:^|\n)\s*(?:[-*•]|\d+[.)]\s)\s*(.+)', body)
    if items:
        return [i.strip() for i in items if len(i.strip()) > 10]

    # Fall back to paragraphs
    paragraphs = [p.strip() for p in body.split("\n\n") if p.strip()]
    return [p for p in paragraphs if len(p) > 10]


def _make_node(node_type: str, item_text: str, index: int, source: str) -> Dict[str, Any]:
    """Create an SDLC node dict from a parsed spec item."""
    node_id = f"spec:{_safe_id(item_text[:40])}-{index}"

    if node_type == "Requirement":
        props = {"content": item_text[:500], "source": source, "version": 1}
    elif node_type == "ArchDecision":
        props = {"decision": item_text[:500], "rationale": "", "source": source, "version": 1}
    elif node_type == "Constraint":
        props = {"description": item_text[:500], "source": source, "version": 1}
    elif node_type == "TestCase":
        props = {"what": item_text[:250], "how": "", "source": source, "version": 1}
    elif node_type == "Goal":
        props = {"content": item_text[:500], "source": source, "version": 1}
    elif node_type == "UserStory":
        props = {"content": item_text[:500], "source": source, "version": 1}
    elif node_type == "DataModel":
        props = {"summary": item_text[:500], "source": source, "version": 1}
    elif node_type == "APIContract":
        props = {"method": "", "path": "", "summary": item_text[:500], "source": source, "version": 1}
    else:
        props = {"content": item_text[:500], "source": source, "version": 1}

    return {"id": node_id, "label": node_type, "properties": props}


def scan_spec_document(
    text: str,
    source: str = "spec",
) -> List[Dict[str, Any]]:
    """Parse a structured spec document into SDLC nodes.

    Recognizes markdown headers (## Section Name) and maps them to SDLC
    node types based on keywords. Items within each section become individual
    nodes.

    Args:
        text: the spec document text (markdown or plain text)
        source: source identifier for traceability

    Returns:
        List of SDLC node dicts: [{id, label, properties}, ...]
    """
    if not text or not text.strip():
        return []

    # Split by markdown headers (## or ###)
    sections = re.split(r'\n#{2,3}\s+', text)
    if len(sections) <= 1:
        # No markdown headers — try plain text with "Section:" pattern
        sections = re.split(r'\n(?=[A-Z][A-Za-z\s]+:)', text)

    nodes: List[Dict[str, Any]] = []
    node_index = 0

    for section in sections:
        lines = section.strip().split("\n")
        if not lines:
            continue

        header = lines[0].strip().rstrip(":")
        body = "\n".join(lines[1:]).strip()

        node_type = _classify_section(header)
        if not node_type:
            continue

        # Extract individual items from the section
        items = _extract_items(body)
        if not items:
            # Treat the whole body as one item if no list structure
            if body and len(body) > 10:
                items = [body[:500]]

        for item_text in items:
            if node_index >= _MAX_SPEC_NODES:
                break
            nodes.append(_make_node(node_type, item_text, node_index, source))
            node_index += 1

    logger.info("scan_spec_document: %d nodes from '%s'", len(nodes), source)
    return nodes
