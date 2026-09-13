"""
Execution Graph Schema — Universal structure for agent collaboration.

Domain-agnostic: works for software, research, support, legal, or any
multi-agent workflow. Defines how agents interact with shared context,
not the domain content itself.

Three layers:
    1. Session layer — root node, agent roster, configuration
    2. Activity layer — what agents did (queries, writes, decisions, errors)
    3. Content layer — what's in the graph (knowledge, documents, entities)

Domain-specific node types (Task, CodeFile, Requirement, Bug, etc.) are
created by agents dynamically — they're NOT part of this base schema.
The schema only defines the collaboration infrastructure.

Usage:
    from contextsynapse.context.execution_schema import seed_execution_graph
    seed_execution_graph(db, session_name="Research Project", goal="Analyze dataset")
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Session Layer — Root + Agent Roster ───────────────────────────────

SESSION_NODES = {
    "Session": {
        "description": "Root node — anchors the execution graph for a boundary",
        "properties": ["name", "goal", "status", "owner_agent_id", "created_at", "region"],
        "color": "#6366f1",
        "singleton": True,
    },
    "AgentRef": {
        "description": "Registered agent participating in this session",
        "properties": ["agent_id", "name", "role", "platform", "adapter", "trust_level"],
        "color": "#00d2ff",
    },
    "Configuration": {
        "description": "Session configuration snapshot (workspace, integrations, etc.)",
        "properties": ["key", "value", "updated_at"],
        "color": "#94a3b8",
    },
}

# ── Activity Layer — Agent Actions ────────────────────────────────────

ACTIVITY_NODES = {
    "Action": {
        "description": "Any action performed by an agent (auto-logged)",
        "properties": ["action_type", "summary", "agent_id", "agent_name", "tool_name", "duration_ms", "success", "timestamp"],
        "color": "#f59e0b",
    },
    "Query": {
        "description": "A question or search an agent performed",
        "properties": ["question", "tool_name", "result_count", "tokens_used", "agent_id", "timestamp"],
        "color": "#3b82f6",
    },
    "Decision": {
        "description": "A choice or judgment made by an agent",
        "properties": ["title", "rationale", "alternatives", "impact", "decided_by", "created_at"],
        "color": "#8b5cf6",
    },
    "ErrorEvent": {
        "description": "Error or failure during agent execution",
        "properties": ["error_type", "message", "agent_id", "timestamp", "severity"],
        "color": "#ef4444",
    },
    "PipelineRun": {
        "description": "Record of an ingestion pipeline execution",
        "properties": ["pipeline_id", "status", "nodes_created", "edges_created", "started_at", "completed_at"],
        "color": "#84cc16",
    },
}

# ── Content Layer — Knowledge & Documents ─────────────────────────────

CONTENT_NODES = {
    "Knowledge": {
        "description": "A piece of knowledge added by an agent",
        "properties": ["name", "content", "source", "confidence", "tags"],
        "color": "#22c55e",
    },
    "Insight": {
        "description": "Agent-contributed insight derived from analysis",
        "properties": ["content", "insight_type", "derived_from", "agent_id", "confidence"],
        "color": "#a855f7",
    },
    "Document": {
        "description": "Ingested document (any format)",
        "properties": ["name", "source", "mime_type", "char_count", "chunk_count"],
        "color": "#78716c",
    },
    "TextChunk": {
        "description": "Chunk of a document for RAG retrieval",
        "properties": ["content", "chunk_index", "source", "char_count"],
        "color": "#a8a29e",
    },
    "Entity": {
        "description": "Named entity extracted from content",
        "properties": ["name", "type", "description", "mention_count"],
        "color": "#06b6d4",
    },
    "Artifact": {
        "description": "Any output produced by an agent (file, report, code, etc.)",
        "properties": ["name", "type", "path", "content", "created_by", "description"],
        "color": "#f97316",
    },
    "ContextRef": {
        "description": "Reference to an attached context store",
        "properties": ["context_id", "name", "context_type", "item_count"],
        "color": "#d946ef",
    },
}

# ── All Node Types ────────────────────────────────────────────────────

NODE_TYPES = {**SESSION_NODES, **ACTIVITY_NODES, **CONTENT_NODES}

# ── Edge Types ────────────────────────────────────────────────────────

EDGE_TYPES = {
    # Session structure
    "HAS_AGENT": {"from": "Session", "to": "AgentRef", "description": "Agent is a member of this session"},
    "HAS_CONFIG": {"from": "Session", "to": "Configuration", "description": "Session has this config"},

    # Agent → Activity
    "PERFORMED": {"from": "AgentRef", "to": ["Action", "Query", "Decision"], "description": "Agent performed this activity"},
    "QUERIED": {"from": "AgentRef", "to": "Query", "description": "Agent asked this question"},
    "DECIDED": {"from": "AgentRef", "to": "Decision", "description": "Agent made this decision"},
    "CAUSED": {"from": "AgentRef", "to": "ErrorEvent", "description": "Agent triggered this error"},

    # Activity → Content
    "PRODUCED": {"from": ["Action", "AgentRef"], "to": ["Knowledge", "Artifact", "Insight"], "description": "Activity produced this content"},
    "DERIVED_FROM": {"from": "*", "to": "*", "description": "Node derived from source — tracks data lineage"},
    "SUPERSEDES": {"from": "*", "to": "*", "description": "Newer version supersedes older — temporal lineage"},
    "CONTRADICTS": {"from": "*", "to": "*", "description": "Nodes contain conflicting information"},
    "REFERENCES": {"from": ["Action", "Decision", "Query"], "to": ["Knowledge", "Entity", "Document"], "description": "References another node"},

    # Content structure
    "CONTAINS": {"from": "Document", "to": "TextChunk", "description": "Document contains chunk"},
    "EXTRACTED_FROM": {"from": "Entity", "to": ["TextChunk", "Document"], "description": "Entity extracted from source"},
    "RELATED_TO": {"from": "Entity", "to": "Entity", "description": "Entities are related"},
    "SIMILAR_TO": {"from": "Entity", "to": "Entity", "description": "Entities are similar (dedup candidate)"},

    # Context
    "HAS_CONTENT": {"from": "ContextRef", "to": ["Document", "Knowledge", "Entity"], "description": "Context contains this content"},
    "HAS_PIPELINE_RUN": {"from": "ContextRef", "to": "PipelineRun", "description": "Context has pipeline run"},
    "ATTACHED_TO": {"from": "ContextRef", "to": "Session", "description": "Context attached to session"},

    # Provenance
    "CREATED_BY": {"from": ["Knowledge", "Artifact", "Insight", "Entity"], "to": "AgentRef", "description": "Created by this agent"},
    "UPDATED_BY": {"from": ["Knowledge", "Entity"], "to": "AgentRef", "description": "Last updated by this agent"},
}

# ── Domain Extension Points ───────────────────────────────────────────
# Agents can create ANY node type beyond this schema.
# Domain-specific types are registered dynamically:
#   Software: Task, Requirement, CodeFile, Bug, Sprint
#   Research: Hypothesis, Experiment, Dataset, Paper
#   Support: Ticket, Customer, Resolution, FAQ
#   Legal: Case, Clause, Precedent, Filing
# The schema validates base types; domain types are permissive.

DOMAIN_PRESETS = {
    "software": {
        "node_types": ["Task", "Requirement", "CodeFile", "Bug", "Sprint", "TestCase"],
        "edge_types": ["HAS_TASK", "DEPENDS_ON", "IMPLEMENTS", "ASSIGNED_TO", "COMPLETED_BY", "BLOCKS"],
    },
    "research": {
        "node_types": ["Hypothesis", "Experiment", "Dataset", "Paper", "Methodology", "Result"],
        "edge_types": ["TESTS", "USES_DATA", "CITES", "SUPPORTS", "CONTRADICTS", "REPLICATES"],
    },
    "support": {
        "node_types": ["Ticket", "Customer", "Resolution", "FAQ", "Escalation", "SLA"],
        "edge_types": ["FILED_BY", "RESOLVED_BY", "ESCALATED_TO", "RELATES_TO", "ANSWERED_BY"],
    },
    "legal": {
        "node_types": ["Case", "Clause", "Precedent", "Filing", "Party", "Statute"],
        "edge_types": ["CITES", "FILED_BY", "GOVERNS", "AMENDS", "SUPERSEDES", "APPLIES_TO"],
    },
    "general": {
        "node_types": [],
        "edge_types": [],
    },
}


def get_schema_summary(domain: str = "general") -> Dict[str, Any]:
    """Get the schema as a JSON-serializable summary."""
    preset = DOMAIN_PRESETS.get(domain, DOMAIN_PRESETS["general"])

    return {
        "domain": domain,
        "layers": {
            "session": list(SESSION_NODES.keys()),
            "activity": list(ACTIVITY_NODES.keys()),
            "content": list(CONTENT_NODES.keys()),
        },
        "node_types": {
            name: {"description": info["description"], "properties": info["properties"], "color": info["color"]}
            for name, info in NODE_TYPES.items()
        },
        "edge_types": {
            name: {"description": info["description"]}
            for name, info in EDGE_TYPES.items()
        },
        "domain_extensions": preset,
        "node_count": len(NODE_TYPES) + len(preset.get("node_types", [])),
        "edge_count": len(EDGE_TYPES) + len(preset.get("edge_types", [])),
    }


def seed_execution_graph(
    db,
    session_name: str = "Untitled",
    goal: str = "",
    owner_agent_id: str = "",
    domain: str = "general",
) -> str:
    """Seed a fresh execution graph with the Session root node.

    Returns the session node ID.
    """
    from contextsynapse.core.graph_structures import GraphNode

    session_node_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()

    from contextsynapse.core.write_context import REGION
    db.add_node(GraphNode(
        id=session_node_id,
        label="Session",
        properties={
            "name": session_name,
            "goal": goal,
            "status": "active",
            "domain": domain,
            "owner_agent_id": owner_agent_id,
            "created_at": now,
            "region": REGION,
        },
    ))

    logger.info("[SCHEMA] Seeded execution graph for '%s' (domain=%s, node_id=%s)",
                session_name, domain, session_node_id[:12])
    return session_node_id


def validate_node(label: str, properties: Dict[str, Any]) -> Dict[str, Any]:
    """Validate a node against the schema. Permissive — warns but doesn't reject."""
    warnings = []
    if label in NODE_TYPES:
        expected = set(NODE_TYPES[label]["properties"])
        provided = set(k for k in properties if not k.startswith("_"))
        missing = expected - provided
        if missing:
            warnings.append(f"Missing properties for {label}: {missing}")
    # Domain types are always valid — agents can create anything
    return {"valid": True, "warnings": warnings}


def get_node_color(label: str) -> str:
    """Get the display color for a node type."""
    if label in NODE_TYPES:
        return NODE_TYPES[label]["color"]
    # Default colors for domain-specific types
    return "#64748b"
