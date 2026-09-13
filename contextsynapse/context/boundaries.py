"""
Context Boundaries
==================
Defines category boundaries within a context's backing graph.

Each context gets a ContextBoundary root node and CategoryBoundary nodes
for each category, connected by BELONGS_TO edges.  Content nodes link
to their category via IN_CATEGORY edges and carry a ``category`` property.

Graph structure::

    ContextBoundary
      |-- BELONGS_TO --> CategoryBoundary("knowledge_base")
      |-- BELONGS_TO --> CategoryBoundary("connectors")
      |-- BELONGS_TO --> CategoryBoundary("user_message")
      |-- BELONGS_TO --> CategoryBoundary("system_message")
      +-- BELONGS_TO --> CategoryBoundary("generated_message")
"""

from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, List, Optional

from ..core.graph_structures import GraphEdge, GraphNode

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CONTEXT_CATEGORIES: List[str] = [
    "knowledge_base",
    "connectors",
    "user_message",
    "system_message",
    "generated_message",
    "software_dev",
]

CATEGORY_LABELS: Dict[str, str] = {
    "knowledge_base": "Knowledge Base",
    "connectors": "Connectors",
    "user_message": "User Messages",
    "system_message": "System Messages",
    "generated_message": "Generated Messages",
    "software_dev": "Software Development",
}

BOUNDARY_NODE_LABELS = {"ContextBoundary", "CategoryBoundary"}

# Role -> category mapping for automatic tagging
ROLE_TO_CATEGORY: Dict[str, str] = {
    "user": "user_message",
    "system": "system_message",
    "assistant": "generated_message",
    "knowledge": "knowledge_base",
    "connector": "connectors",
}


# ---------------------------------------------------------------------------
# Seed helpers
# ---------------------------------------------------------------------------

def seed_boundary_nodes(
    db,
    context_id: str,
    context_name: str,
) -> Dict[str, Any]:
    """Create the ContextBoundary + CategoryBoundary nodes and BELONGS_TO edges.

    Args:
        db: AIContextDB graph instance.
        context_id: The owning context's ID.
        context_name: Human-readable context name.

    Returns:
        ``{"context_boundary_id": str, "category_ids": {"knowledge_base": str, ...}}``
    """
    cb_id = str(uuid.uuid4())
    cb_node = GraphNode(
        id=cb_id,
        label="ContextBoundary",
        properties={
            "name": context_name,
            "context_id": context_id,
            "boundary_type": "context",
        },
    )
    db.add_node(cb_node)

    category_ids: Dict[str, str] = {}

    for cat in CONTEXT_CATEGORIES:
        cat_id = str(uuid.uuid4())
        cat_node = GraphNode(
            id=cat_id,
            label="CategoryBoundary",
            properties={
                "name": CATEGORY_LABELS[cat],
                "category": cat,
                "context_id": context_id,
                "boundary_type": "category",
            },
        )
        db.add_node(cat_node)
        category_ids[cat] = cat_id

        # ContextBoundary --> CategoryBoundary
        edge = GraphEdge(
            id=str(uuid.uuid4()),
            source=cb_id,
            target=cat_id,
            label="BELONGS_TO",
            properties={"category": cat},
        )
        db.add_edge(edge)

    logger.info(
        "Seeded boundary nodes for context '%s': 1 ContextBoundary + %d categories",
        context_name,
        len(category_ids),
    )

    return {
        "context_boundary_id": cb_id,
        "category_ids": category_ids,
    }


# ---------------------------------------------------------------------------
# Category tagging helpers (for bulk_ingest node/edge dicts)
# ---------------------------------------------------------------------------

def tag_nodes_with_category(nodes: List[Dict[str, Any]], category: str) -> None:
    """Add ``category`` property to each node dict in-place."""
    for nd in nodes:
        nd.setdefault("properties", {})["category"] = category


def build_category_edges(
    nodes: List[Dict[str, Any]],
    category_boundary_id: str,
) -> List[Dict[str, Any]]:
    """Return IN_CATEGORY edge dicts linking each node to its CategoryBoundary."""
    edges: List[Dict[str, Any]] = []
    for nd in nodes:
        nid = nd.get("id")
        if not nid:
            continue
        # Skip boundary nodes themselves
        if nd.get("label") in BOUNDARY_NODE_LABELS:
            continue
        edges.append({
            "id": str(uuid.uuid4()),
            "source": nid,
            "target": category_boundary_id,
            "label": "IN_CATEGORY",
            "properties": {},
        })
    return edges


# ---------------------------------------------------------------------------
# Config accessors
# ---------------------------------------------------------------------------

def get_category_boundary_id(
    ctx_config: Dict[str, Any],
    category: str,
) -> Optional[str]:
    """Extract a specific category's boundary node ID from context config."""
    boundary_ids = ctx_config.get("_boundary_ids", {})
    return boundary_ids.get("category_ids", {}).get(category)


def get_context_boundary_id(ctx_config: Dict[str, Any]) -> Optional[str]:
    """Extract the ContextBoundary node ID from context config."""
    return ctx_config.get("_boundary_ids", {}).get("context_boundary_id")


def role_to_category(role: str) -> str:
    """Map a message role to a category string."""
    return ROLE_TO_CATEGORY.get(role, "knowledge_base")
