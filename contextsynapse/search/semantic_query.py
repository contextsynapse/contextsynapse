"""
Semantic Query — natural language → graph results.

Agents ask questions in plain English. This module routes them to
the right graph operations without needing AIQL or label names.

Usage:
    from contextsynapse.search.semantic_query import semantic_search

    result = semantic_search("What are the requirements?", conn, project_graph)
    # {"summary": "5 requirements found:\n- ...", "nodes": [...]}

    result = semantic_search("What has codex done?", conn, project_graph)
    # {"summary": "codex completed 3 tasks:\n- ...", "nodes": [...]}
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Intent patterns: regex → action (order matters — first match wins)
_INTENTS = [
    (r"\brequirement|what.*build|what.*need|\bspec\b", "requirements"),
    (r"\bfeature|planned feature|capability", "features"),
    (r"\bdecision|decided|chose|why did", "decisions"),
    (r"what happened|recent|activity|action|log|done", "actions"),
    (r"\bcode\b|codebase|produced|written|source file", "code_files"),
    (r"\btask|todo|work item|assigned|open task|backlog", "tasks"),
    (r"\bwho\b|agent|working|team|member|tool.*use", "agents"),
    (r"\bstatus|progress|overview|summary", "status"),
    (r"\bfinding|discovered|learned|insight", "findings"),
    (r"\bfact|stated|document says|passage", "facts"),
]


def semantic_search(
    question: str,
    conn,  # AIContextDBConnection
    project_graph=None,  # ProjectGraph (optional)
) -> Dict[str, Any]:
    """Route a natural language question to graph operations.

    Returns:
        {"summary": str, "nodes": list}
    """
    q = question.lower().strip()

    # 1. Intent matching
    for pattern, intent in _INTENTS:
        if re.search(pattern, q):
            return _execute_intent(intent, question, conn, project_graph)

    # 2. Agent-specific queries: "what has codex done", "claude's work"
    # 2. Agent-specific queries: "what has claude done", "codex's work"
    _stop_words = {"the", "this", "that", "what", "how", "why", "when", "where", "which",
                   "a", "an", "it", "is", "are", "was", "do", "did", "has", "have", "been",
                   "all", "any", "my", "our", "about", "for", "from", "with", "show", "tell",
                   "find", "get", "list", "give", "can", "does", "document", "passage", "fact",
                   "feature", "requirement", "task", "node", "graph", "search", "query"}
    agent_match = re.search(r"(?:what (?:has|did|is) )(\w+)(?: do| done| working| built| created| completed)", q)
    if agent_match:
        agent_name = agent_match.group(1)
        if agent_name not in _stop_words:
            return _search_by_agent(agent_name, conn)

    # 3. Graph-aware search — find nodes + follow edges for related context
    try:
        graph_result = _graph_traversal_search(question, conn)
        if graph_result.get("nodes"):
            return graph_result
    except Exception as e:
        logger.debug("Graph traversal search failed: %s", e)

    # 4. LLM-powered NL→AIQL translation (if available)
    try:
        from .nl_to_aiql import nl_to_aiql
        llm_result = nl_to_aiql(question, conn, project_graph)
        if llm_result.get("nodes") or (llm_result.get("summary") and "no results" not in llm_result["summary"].lower()):
            return llm_result
    except Exception as e:
        logger.debug("NL→AIQL fallback failed: %s", e)

    # 4. Keyword search across all nodes
    return _keyword_search(question, conn)


def _execute_intent(
    intent: str, question: str, conn, pg
) -> Dict[str, Any]:
    """Execute a matched intent."""

    if intent == "requirements":
        result = conn.query("SELECT * FROM Requirement")
        nodes = result.get("nodes", [])
        if not nodes:
            return {"summary": "No requirements found in the project.", "nodes": []}
        lines = [f"- {_prop(n, 'name', _prop(n, 'title'))}: {_prop(n, 'description', _prop(n, 'content'))[:100]}" for n in nodes]
        return {
            "summary": f"{len(nodes)} requirements:\n" + "\n".join(lines),
            "nodes": nodes,
        }

    if intent == "decisions":
        result = conn.query("SELECT * FROM Decision")
        nodes = result.get("nodes", [])
        if not nodes:
            return {"summary": "No decisions recorded yet.", "nodes": []}
        lines = [f"- {_prop(n, 'title')}: {_prop(n, 'rationale')}" for n in nodes]
        return {
            "summary": f"{len(nodes)} decisions:\n" + "\n".join(lines),
            "nodes": nodes,
        }

    if intent == "actions":
        result = conn.query("SELECT * FROM Action")
        nodes = result.get("nodes", [])
        if not nodes:
            return {"summary": "No actions logged yet.", "nodes": []}
        lines = [f"- [{_prop(n, 'type')}] {_prop(n, 'summary')} (by {_prop(n, 'agent_id', '?')[:8]})" for n in nodes]
        return {
            "summary": f"{len(nodes)} actions:\n" + "\n".join(lines),
            "nodes": nodes,
        }

    if intent == "code_files":
        result = conn.query("SELECT * FROM CodeFile")
        nodes = result.get("nodes", [])
        if not nodes:
            return {"summary": "No code files produced yet.", "nodes": []}
        lines = [f"- {_prop(n, 'path')} (by {_prop(n, 'created_by', '?')[:8]})" for n in nodes]
        return {
            "summary": f"{len(nodes)} code files:\n" + "\n".join(lines),
            "nodes": nodes,
        }

    if intent == "tasks":
        if pg:
            tasks = pg.get_open_tasks()
            if not tasks:
                return {"summary": "No open tasks.", "nodes": []}
            lines = [f"- [{t['priority'].upper()}] {t['title']} (assigned: {t['assigned_to']}, status: {t['status']})" for t in tasks]
            return {
                "summary": f"{len(tasks)} tasks:\n" + "\n".join(lines),
                "nodes": tasks,
            }
        result = conn.query("SELECT * FROM Task")
        nodes = result.get("nodes", [])
        lines = [f"- {_prop(n, 'title')} (status: {_prop(n, 'status')})" for n in nodes]
        return {"summary": f"{len(nodes)} tasks:\n" + "\n".join(lines), "nodes": nodes}

    if intent == "agents":
        # Check AgentRef nodes + ToolCall activity
        agents_result = conn.query("SELECT * FROM AgentRef")
        agent_nodes = agents_result.get("nodes", [])
        tools_result = conn.query("SELECT * FROM ToolCall")
        tool_nodes = tools_result.get("nodes", [])

        lines = []
        for a in agent_nodes:
            name = _prop(a, 'name', _prop(a, 'agent_name'))
            role = _prop(a, 'role', 'agent')
            lines.append(f"- {name} ({role})")

        if tool_nodes:
            # Group tool calls by agent
            by_agent = {}
            for t in tool_nodes:
                agent = _prop(t, 'agent_name', _prop(t, 'agent_id', '?')[:12])
                tool = _prop(t, 'tool_name', _prop(t, 'action_type', '?'))
                by_agent.setdefault(agent, []).append(tool)
            lines.append(f"\nTool usage ({len(tool_nodes)} calls):")
            for agent, tools in by_agent.items():
                unique_tools = list(set(tools))
                lines.append(f"  {agent}: {', '.join(unique_tools[:8])} ({len(tools)} calls)")

        if pg:
            status = pg.get_project_status()
            working = status.get("agents_working", {})
            if working:
                lines.append("\nCurrently working on:")
                for aid, titles in working.items():
                    lines.append(f"  {aid[:12]}: {', '.join(titles)}")

        if lines:
            return {"summary": "\n".join(lines), "nodes": agent_nodes + tool_nodes[:10]}
        return {"summary": "No agent activity found.", "nodes": []}

    if intent == "status":
        if pg:
            status = pg.get_project_status()
            return {
                "summary": (
                    f"Project: {status['project']}\n"
                    f"Tasks: {status['tasks']['total']} ({status['tasks']['by_status']})\n"
                    f"Decisions: {status['decisions']}\n"
                    f"Documents: {status['documents']}\n"
                    f"Code files: {status['code_files']}"
                ),
                "nodes": [],
            }
        return {"summary": "Project status not available.", "nodes": []}

    if intent == "findings":
        result = conn.query("SELECT * FROM Finding")
        nodes = result.get("nodes", [])
        if not nodes:
            result = conn.query("SELECT * FROM Knowledge")
            nodes = result.get("nodes", [])
        if not nodes:
            return {"summary": "No findings recorded.", "nodes": []}
        lines = [f"- {_prop(n, 'content', _prop(n, 'title', '?'))}" for n in nodes]
        return {"summary": f"{len(nodes)} findings:\n" + "\n".join(lines), "nodes": nodes}

    if intent == "features":
        result = conn.query("SELECT * FROM Feature")
        nodes = result.get("nodes", [])
        if not nodes:
            return {"summary": "No features found.", "nodes": []}
        lines = [f"- {_prop(n, 'name')}: {_prop(n, 'description')[:100]}" for n in nodes]
        return {"summary": f"{len(nodes)} features:\n" + "\n".join(lines), "nodes": nodes}

    if intent == "facts":
        # Get Fact + Passage nodes
        fact_result = conn.query("SELECT * FROM Fact")
        passage_result = conn.query("SELECT * FROM Passage")
        facts = fact_result.get("nodes", [])
        passages = passage_result.get("nodes", [])
        all_items = passages + facts
        if not all_items:
            return {"summary": "No facts or passages found.", "nodes": []}
        lines = []
        for p in passages:
            lines.append(f"[Passage] {_prop(p, 'name')}: {_prop(p, 'content')[:120]}")
        for f in facts[:15]:
            lines.append(f"[Fact] {_prop(f, 'name', _prop(f, 'content', '?'))[:120]}")
        return {
            "summary": f"{len(passages)} passages, {len(facts)} facts:\n" + "\n".join(lines),
            "nodes": all_items,
        }

    return {"summary": f"I don't know how to answer that yet.", "nodes": []}


def _graph_traversal_search(question: str, conn) -> Dict[str, Any]:
    """Search by keyword matching + 1-hop edge traversal.

    Finds matching nodes, then follows their edges to get related context.
    This makes answers relationship-aware — not just flat property matching.
    """
    keywords = [w for w in re.split(r'\W+', question.lower())
                if len(w) > 2 and w not in ("what", "the", "how", "does", "did", "has", "are",
                                              "was", "been", "this", "that", "from", "with",
                                              "show", "tell", "find", "get", "about", "which")]
    if not keywords:
        return {"summary": "", "nodes": []}

    # Get all nodes and edges
    try:
        all_nodes = conn.get_nodes() if hasattr(conn, 'get_nodes') else []
        all_edges = conn.get_edges() if hasattr(conn, 'get_edges') else []
    except Exception:
        return {"summary": "", "nodes": []}

    if not all_nodes:
        return {"summary": "", "nodes": []}

    # Build node map and adjacency
    node_map = {}
    for n in all_nodes:
        nid = n.id if hasattr(n, "id") else (n.get("id", "") if isinstance(n, dict) else "")
        props = n.properties if hasattr(n, "properties") else (n if isinstance(n, dict) else {})
        label = n.label if hasattr(n, "label") else (n.get("label", "") if isinstance(n, dict) else "")
        node_map[nid] = {"label": label, "props": props, "node": n}

    # Build adjacency: node_id → [(edge_label, neighbor_id, edge_score)]
    adj = {}
    for e in all_edges:
        src = e.source if hasattr(e, "source") else (e.get("source", "") if isinstance(e, dict) else "")
        tgt = e.target if hasattr(e, "target") else (e.get("target", "") if isinstance(e, dict) else "")
        lbl = e.label if hasattr(e, "label") else (e.get("label", "") if isinstance(e, dict) else "")
        # Edge-weight scoring (C4): weight * confidence
        e_props = e.properties if hasattr(e, "properties") else (e if isinstance(e, dict) else {})
        e_weight = float(e_props.get("weight", 1.0) if isinstance(e_props, dict) else 1.0)
        e_conf = float(e_props.get("confidence", 1.0) if isinstance(e_props, dict) else 1.0)
        e_score = e_weight * e_conf
        if src not in adj:
            adj[src] = []
        adj[src].append((lbl, tgt, e_score))
        if tgt not in adj:
            adj[tgt] = []
        adj[tgt].append((lbl, src, e_score))

    # Score nodes by keyword match
    scored = []
    for nid, info in node_map.items():
        props = info["props"]
        text = " ".join(str(v) for v in props.values() if isinstance(v, str)).lower()
        label_text = info["label"].lower()
        score = 0
        for kw in keywords:
            if kw in text:
                score += 2
            if kw in label_text:
                score += 1
        if score > 0:
            scored.append((nid, score))

    if not scored:
        return {"summary": "", "nodes": []}

    scored.sort(key=lambda x: x[1], reverse=True)
    top_ids = [nid for nid, _ in scored[:5]]

    # Follow edges from top nodes (1-hop traversal, edge-weighted — C4)
    related_ids = set()
    edge_descriptions = []
    for nid in top_ids:
        for edge_label, neighbor_id, e_score in adj.get(nid, [])[:10]:
            if e_score < 0.3:
                continue  # weak/stale relationship — skip
            if neighbor_id in node_map and neighbor_id not in top_ids:
                related_ids.add(neighbor_id)
                src_name = _node_name(node_map[nid])
                tgt_name = _node_name(node_map[neighbor_id])
                edge_descriptions.append(f"{src_name} --{edge_label}--> {tgt_name}")

    # Build summary
    lines = []
    for nid in top_ids:
        info = node_map[nid]
        name = _node_name(info)
        content = info["props"].get("content") or info["props"].get("description") or info["props"].get("statement") or ""
        label = info["label"]
        line = f"[{label}] {name}"
        if content:
            line += f": {content[:120]}"
        lines.append(f"- {line}")

    # Add related context from traversal
    if edge_descriptions:
        lines.append(f"\nRelated (via graph edges):")
        for desc in edge_descriptions[:8]:
            lines.append(f"  → {desc}")

    # Add related node details
    for rid in list(related_ids)[:5]:
        info = node_map[rid]
        name = _node_name(info)
        content = info["props"].get("content") or info["props"].get("description") or ""
        if content:
            lines.append(f"  [{info['label']}] {name}: {content[:100]}")

    all_result_nodes = [node_map[nid]["node"] for nid in top_ids]
    all_result_nodes += [node_map[rid]["node"] for rid in list(related_ids)[:5] if rid in node_map]

    summary = f"Found {len(top_ids)} direct matches + {len(related_ids)} related nodes:\n" + "\n".join(lines)
    return {"summary": summary, "nodes": all_result_nodes}


def _node_name(info: dict) -> str:
    """Extract a display name from a node info dict."""
    props = info.get("props", {})
    return props.get("name") or props.get("title") or props.get("statement", "")[:40] or info.get("label", "?")


def _search_by_agent(agent_name: str, conn) -> Dict[str, Any]:
    """Find what a specific agent has done."""
    # Search Action nodes by this agent
    result = conn.query("SELECT * FROM Action")
    actions = [n for n in result.get("nodes", []) if agent_name in str(_prop(n, "agent_id", "")).lower() or agent_name in str(_prop(n, "_agent_name", "")).lower()]

    # Search Task completions
    result = conn.query("SELECT * FROM Task")
    tasks = [n for n in result.get("nodes", []) if _prop(n, "completed_by", "").lower() == agent_name or _prop(n, "assigned_to", "").lower() == agent_name]

    lines = []
    if tasks:
        for t in tasks:
            status = _prop(t, "status", "?")
            lines.append(f"- [{status}] {_prop(t, 'title')}")
    if actions:
        for a in actions:
            lines.append(f"- Action: {_prop(a, 'summary')}")

    if not lines:
        return {"summary": f"No activity found for '{agent_name}'.", "nodes": []}

    return {
        "summary": f"{agent_name}'s activity ({len(lines)} items):\n" + "\n".join(lines),
        "nodes": tasks + actions,
    }


def _keyword_search(question: str, conn) -> Dict[str, Any]:
    """Fall back to keyword matching across all node properties."""
    keywords = [w for w in re.split(r'\W+', question.lower()) if len(w) > 2 and w not in ("what", "the", "how", "does", "did", "has", "are", "was", "been", "this", "that", "from", "with")]

    if not keywords:
        return {"summary": "Could not extract keywords from question.", "nodes": []}

    # Search across common node types
    matches = []
    # Search ALL node types — discover labels dynamically from the graph
    all_labels = set()
    try:
        for n in conn.get_nodes():
            lbl = n.label if hasattr(n, "label") else (n.get("label", "") if isinstance(n, dict) else "")
            if lbl:
                all_labels.add(lbl)
    except Exception:
        all_labels = {"Requirement", "Task", "Decision", "Action", "CodeFile", "Finding",
                       "Knowledge", "Document", "ProjectSpec", "Fact", "Feature", "Passage",
                       "Entity", "Insight", "Assumption", "ToolCall", "AgentRef"}
    for label in all_labels:
        result = conn.query(f"SELECT * FROM {label}")
        for n in result.get("nodes", []):
            # Score by keyword matches in properties
            text = " ".join(str(v) for v in n.values()).lower()
            score = sum(1 for kw in keywords if kw in text)
            if score > 0:
                matches.append((score, label, n))

    matches.sort(key=lambda x: x[0], reverse=True)

    if not matches:
        return {"summary": f"No results found for: {question}", "nodes": []}

    lines = [f"- [{label}] {_prop(n, 'title', _prop(n, 'name', _prop(n, 'path', str(n)[:60])))}" for _, label, n in matches[:10]]
    return {
        "summary": f"Found {len(matches)} results for '{question}':\n" + "\n".join(lines),
        "nodes": [n for _, _, n in matches[:10]],
    }


def _prop(node, key: str, default: str = "") -> str:
    """Get a property from a node (dict or object).

    Handles both flat dicts (``{name: ...}``) and nested dicts
    (``{properties: {name: ...}}``) returned by AIQL queries.
    """
    if isinstance(node, dict):
        # Try top-level first, then nested properties dict
        val = node.get(key)
        if val is None and "properties" in node and isinstance(node["properties"], dict):
            val = node["properties"].get(key)
        return str(val) if val is not None else default
    # Object: try attribute, then .properties dict
    val = getattr(node, key, None)
    if val is None and hasattr(node, "properties"):
        val = (getattr(node, "properties", {}) or {}).get(key)
    return str(val) if val is not None else default
