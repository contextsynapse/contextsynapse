"""Parse plain markdown spec into typed graph node dicts.

Recognised heading prefixes:
    ## REQ: <name>   → Requirement node
    ## FEAT: <name>  → Feature node
    ## CON: <name>   → Constraint node

Everything until the next recognised heading (or EOF) is the description.
Priority is inferred from MoSCoW keywords in the heading + description text.
"""
from __future__ import annotations

import re
import uuid
from typing import Any

_HEADING_RE = re.compile(r"^#{1,4}\s+(REQ|FEAT|CON|CHAR|MIG):\s*(.+)$", re.MULTILINE)

_PRIORITY_WORDS: dict[str, str] = {
    "must": "must",
    "shall": "must",
    "should": "should",
    "could": "could",
    "may": "could",
    "wont": "wont",
    "won't": "wont",
}

_LABEL_MAP = {
    "REQ": "Requirement",
    "FEAT": "Feature",
    "CON": "Constraint",
    "CHAR": "CharacterizationTest",
    "MIG": "MigrationBoundary",
}
_ID_PREFIX = {"REQ": "req", "FEAT": "feat", "CON": "con", "CHAR": "char", "MIG": "mig"}


def _detect_priority(text: str) -> str:
    lower = text.lower()
    for word, level in _PRIORITY_WORDS.items():
        if re.search(r"\b" + re.escape(word) + r"\b", lower):
            return level
    return "should"


def parse_spec(text: str) -> list[dict[str, Any]]:
    """Parse markdown text into a list of node dicts.

    Each dict has keys: node_id, label, name, description, priority, tags.
    Unknown heading prefixes (e.g. ``## Introduction``) are silently skipped.

    Args:
        text: Raw markdown string.

    Returns:
        List of node dicts, one per recognised heading.
    """
    if not text or not text.strip():
        return []

    nodes: list[dict[str, Any]] = []
    matches = list(_HEADING_RE.finditer(text))

    for i, m in enumerate(matches):
        prefix = m.group(1)
        name = m.group(2).strip()
        body_start = m.end()
        body_end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[body_start:body_end].strip()

        hex8 = uuid.uuid4().hex[:8]
        node_id = f"{_ID_PREFIX[prefix]}_{hex8}"
        priority = _detect_priority(f"{name} {body}")

        nodes.append({
            "node_id": node_id,
            "label": _LABEL_MAP[prefix],
            "name": name,
            "description": body,
            "priority": priority,
            "tags": [],
        })

    return nodes
