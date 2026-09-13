"""
Natural Language → AIQL Query Translation

Uses LLM to convert plain English questions into executable AIQL queries.
Called as a fallback when regex intent matching in semantic_query.py fails.

Usage:
    from contextsynapse.search.nl_to_aiql import nl_to_aiql
    result = nl_to_aiql("bugs Alice reported last week", conn)
    # {"summary": "Found 3 bugs...", "nodes": [...], "aiql": "MATCH ..."}
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── AIQL Reference (injected into LLM prompt) ────────────────────────

_AIQL_REFERENCE = """\
AIQL is a graph query language. Key syntax:

QUERIES:
  SELECT * FROM <NodeType>
  SELECT * FROM <NodeType> WHERE <property> = "<value>"
  SELECT * FROM <NodeType> WHERE <property> > "<value>" ORDER BY <property> LIMIT <n>
  MATCH (n:<NodeType>) WHERE n.<property> = "<value>" RETURN n
  MATCH (n:<NodeType>)-[r:<EdgeType>]->(m:<NodeType>) RETURN n, m

WHERE OPERATORS: =, !=, >, <, >=, <=, CONTAINS, IN, LIKE
LOGICAL: AND, OR, NOT
PAGINATION: LIMIT <n>, OFFSET <n>
ORDERING: ORDER BY <property> ASC|DESC

PROVENANCE FIELDS (available on all nodes):
  _created_at  — ISO timestamp when node was created (string, e.g. "2026-03-24T10:00:00+00:00")
  _agent_id    — ID of the agent that created the node
  _origin      — how it was created: "tool", "ingest", "api", "a2a", "system"
  _verified    — boolean, true if from a trusted path
  _updated_at  — ISO timestamp of last update (if updated)
  _updated_by  — agent that last updated (if updated)

COMMON NODE TYPES: Requirement, Task, Decision, Action, CodeFile, Bug, Finding, Knowledge, Document, TextChunk, Entity, Agent, Insight

EXAMPLES:
  SELECT * FROM Task WHERE status = "open" ORDER BY priority LIMIT 10
  SELECT * FROM Bug WHERE _agent_id = "alice" AND _created_at > "2026-03-17"
  MATCH (n:Task)-[r:DEPENDS_ON]->(m:Task) RETURN n, m
  SELECT * FROM Document WHERE _origin = "ingest" ORDER BY _created_at DESC LIMIT 5
"""


def _extract_graph_schema(conn) -> str:
    """Pull node types and sample properties from the graph for LLM context."""
    try:
        graph = conn.contextcore if hasattr(conn, "contextsynapse") else None
        if not graph:
            return "No graph schema available."

        nodes = graph.get_all_nodes()
        if not nodes:
            return "Graph is empty."

        # Group by label, collect property keys
        label_props: Dict[str, Dict[str, int]] = {}
        label_counts: Dict[str, int] = {}
        for n in nodes:
            label = n.label if hasattr(n, "label") else "Unknown"
            props = n.properties if hasattr(n, "properties") else {}
            label_counts[label] = label_counts.get(label, 0) + 1
            if label not in label_props:
                label_props[label] = {}
            for k in props:
                if not k.startswith("_") and k not in ("domain", "content_hash", "hash_algorithm"):
                    label_props[label][k] = label_props[label].get(k, 0) + 1

        lines = []
        for label in sorted(label_counts, key=lambda x: label_counts[x], reverse=True)[:15]:
            count = label_counts[label]
            props = sorted(label_props.get(label, {}).keys())[:10]
            lines.append(f"  {label} ({count} nodes): {', '.join(props) if props else 'no properties'}")

        return "NODE TYPES IN THIS GRAPH:\n" + "\n".join(lines)

    except Exception as e:
        logger.debug("Schema extraction failed: %s", e)
        return "Schema extraction failed."


def _build_prompt(question: str, schema: str) -> str:
    """Build the LLM prompt for NL→AIQL translation."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return f"""\
Convert the following natural language question into an AIQL query.

{_AIQL_REFERENCE}

{schema}

TODAY'S DATE: {today}
When the user says "last week", "yesterday", "last 7 days" etc., calculate the actual date.

QUESTION: {question}

Respond with ONLY the AIQL query, nothing else. No explanation, no markdown.
If the question cannot be answered with AIQL, respond with: CANNOT_TRANSLATE"""


def nl_to_aiql(
    question: str,
    conn,
    project_graph=None,
) -> Dict[str, Any]:
    """Translate a natural language question to AIQL and execute it.

    Returns:
        {"summary": str, "nodes": list, "aiql": str}
    """
    # 1. Get LLM client
    try:
        from contextsynapse.llm.client import LLMClient
        llm = LLMClient()
    except Exception as e:
        logger.debug("LLM not available for NL→AIQL: %s", e)
        return {"summary": "", "nodes": [], "aiql": ""}

    # 2. Extract graph schema
    schema = _extract_graph_schema(conn)

    # 3. Ask LLM to generate AIQL
    prompt = _build_prompt(question, schema)
    try:
        aiql = llm.generate(prompt, system="You are an AIQL query generator. Output only the query.").strip()
    except Exception as e:
        logger.warning("LLM generation failed: %s", e)
        return {"summary": "", "nodes": [], "aiql": ""}

    if not aiql or "CANNOT_TRANSLATE" in aiql:
        return {"summary": "", "nodes": [], "aiql": ""}

    # Clean up: remove markdown fences if LLM wrapped it
    aiql = re.sub(r"^```\w*\n?", "", aiql)
    aiql = re.sub(r"\n?```$", "", aiql)
    aiql = aiql.strip()

    # 4. Validate by parsing (don't execute blind LLM output)
    try:
        from contextsynapse.aiql.parser.aiql_parser import AIQLParser
        parser = AIQLParser()
        parser.parse(aiql)  # throws if invalid
    except Exception as e:
        logger.warning("LLM generated invalid AIQL: %s — query: %s", e, aiql)
        return {"summary": "", "nodes": [], "aiql": aiql}

    # 5. Execute the validated query
    try:
        result = conn.query(aiql)
        nodes = result.get("nodes", [])
        rows = result.get("rows", [])
        matches = result.get("matches", [])

        # Combine results from different query types
        all_items = nodes or rows or matches or []

        if not all_items:
            return {
                "summary": f"Query executed but returned no results.\nAIQL: {aiql}",
                "nodes": [],
                "aiql": aiql,
            }

        # Format results
        lines = []
        for item in all_items[:15]:
            if isinstance(item, dict):
                name = item.get("title") or item.get("name") or item.get("content", "")[:80] or str(item)[:80]
                label = item.get("label", "")
                lines.append(f"- [{label}] {name}" if label else f"- {name}")
            else:
                props = item.properties if hasattr(item, "properties") else {}
                label = item.label if hasattr(item, "label") else ""
                name = props.get("title") or props.get("name") or props.get("content", "")[:80]
                lines.append(f"- [{label}] {name}")

        summary = f"Found {len(all_items)} result(s):\n" + "\n".join(lines)
        return {"summary": summary, "nodes": all_items, "aiql": aiql}

    except Exception as e:
        logger.warning("AIQL execution failed: %s — query: %s", e, aiql)
        return {
            "summary": f"Query generated but execution failed: {e}\nAIQL: {aiql}",
            "nodes": [],
            "aiql": aiql,
        }
