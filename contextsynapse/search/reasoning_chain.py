"""
Graph Reasoning Chain — LLM follows edges to answer multi-hop questions.

Unlike flat RAG (retrieve chunks → answer), this traverses the graph:
  Q: "Who is responsible for billing security?"
  → Find: Billing --MAINTAINED_BY--> Alice --REPORTS_TO--> Bob (Security Lead)
  → Answer: "Bob, via Alice who maintains Billing"

The LLM decides which edges to follow at each hop, building a reasoning
chain that explains HOW it arrived at the answer.

Usage:
    from contextsynapse.search.reasoning_chain import reason_over_graph
    result = reason_over_graph(db, "Who maintains the auth module?")
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

MAX_HOPS = 4
MAX_NEIGHBORS = 15


def reason_over_graph(
    db,
    question: str,
    max_hops: int = MAX_HOPS,
    use_llm: bool = True,
) -> Dict[str, Any]:
    """Multi-hop graph reasoning to answer a question.

    1. Find seed nodes matching the question
    2. At each hop, decide which edges to follow (LLM or heuristic)
    3. Build reasoning chain explaining the path
    4. Generate final answer from collected evidence

    Returns:
        {answer, chain, nodes_visited, hops, evidence}
    """
    # Build adjacency
    all_nodes = db.get_all_nodes()
    all_edges = db.get_all_edges()

    node_map = {}
    for n in all_nodes:
        nid = n.id if hasattr(n, "id") else ""
        props = n.properties if hasattr(n, "properties") else {}
        label = n.label if hasattr(n, "label") else ""
        node_map[nid] = {"label": label, "props": props, "node": n}

    adj = {}  # node_id → [(edge_label, neighbor_id, edge_props)]
    for e in all_edges:
        src = e.source if hasattr(e, "source") else ""
        tgt = e.target if hasattr(e, "target") else ""
        lbl = e.label if hasattr(e, "label") else ""
        eprops = e.properties if hasattr(e, "properties") else {}
        adj.setdefault(src, []).append((lbl, tgt, eprops))
        adj.setdefault(tgt, []).append((lbl, src, eprops))

    # Step 1: Find seed nodes
    keywords = _extract_keywords(question)
    seeds = _find_seeds(node_map, keywords, max_seeds=5)

    if not seeds:
        return {"answer": "No relevant nodes found in the graph.", "chain": [], "nodes_visited": 0, "hops": 0}

    # Step 2: Multi-hop traversal
    visited: Set[str] = set()
    chain = []  # list of (hop, from_node, edge_label, to_node, relevance)
    evidence = []  # collected text from nodes along the path
    frontier = [(nid, 0) for nid in seeds]

    for nid in seeds:
        info = node_map.get(nid, {})
        props = info.get("props", {})
        name = props.get("name") or props.get("title") or props.get("content", "")[:60]
        evidence.append(f"[{info.get('label', '')}] {name}")

    while frontier and len(chain) < max_hops * len(seeds):
        current_id, hop = frontier.pop(0)
        if current_id in visited or hop >= max_hops:
            continue
        visited.add(current_id)

        neighbors = adj.get(current_id, [])[:MAX_NEIGHBORS]
        if not neighbors:
            continue

        # Score neighbors by keyword relevance
        scored_neighbors = []
        for edge_label, neighbor_id, eprops in neighbors:
            if neighbor_id in visited or neighbor_id not in node_map:
                continue
            n_info = node_map[neighbor_id]
            n_text = _node_text(n_info).lower()
            relevance = sum(1 for kw in keywords if kw in n_text) + (0.5 if any(kw in edge_label.lower() for kw in keywords) else 0)
            if relevance > 0 or hop == 0:  # always explore first hop
                scored_neighbors.append((relevance, edge_label, neighbor_id, eprops))

        scored_neighbors.sort(key=lambda x: x[0], reverse=True)

        for relevance, edge_label, neighbor_id, eprops in scored_neighbors[:5]:
            n_info = node_map[neighbor_id]
            from_info = node_map.get(current_id, {})
            from_name = _node_name(from_info)
            to_name = _node_name(n_info)

            chain.append({
                "hop": hop + 1,
                "from": from_name,
                "from_type": from_info.get("label", ""),
                "edge": edge_label,
                "to": to_name,
                "to_type": n_info.get("label", ""),
                "relevance": relevance,
            })

            # Collect evidence (resolve text for vector-ref nodes)
            props = n_info.get("props", {})
            content = props.get("content") or props.get("description") or props.get("statement") or ""
            if not content and props.get("vector_ref"):
                try:
                    from .text_resolver import resolve_text
                    content = resolve_text(neighbor_id, props, "")
                except Exception:
                    pass
            if content:
                evidence.append(f"[{n_info.get('label', '')}] {to_name}: {content[:150]}")

            if relevance > 0:
                frontier.append((neighbor_id, hop + 1))

    # Step 3: Generate answer
    if use_llm and chain:
        answer = _llm_answer(question, chain, evidence)
    else:
        answer = _heuristic_answer(question, chain, evidence)

    return {
        "answer": answer,
        "chain": chain[:20],
        "evidence": evidence[:10],
        "nodes_visited": len(visited),
        "hops": max(c["hop"] for c in chain) if chain else 0,
        "seeds": [_node_name(node_map.get(s, {})) for s in seeds],
    }


def _extract_keywords(question: str) -> List[str]:
    """Extract meaningful keywords from the question."""
    stop = {"what", "who", "how", "why", "when", "where", "which", "is", "are",
            "was", "the", "a", "an", "does", "did", "has", "have", "been",
            "for", "from", "with", "about", "that", "this", "show", "find",
            "tell", "get", "me", "all", "can", "do", "to", "of", "in", "on"}
    words = re.split(r'\W+', question.lower())
    return [w for w in words if len(w) > 2 and w not in stop]


def _find_seeds(node_map: dict, keywords: List[str], max_seeds: int = 5) -> List[str]:
    """Find starting nodes that match the question keywords."""
    scored = []
    for nid, info in node_map.items():
        text = _node_text(info).lower()
        score = sum(2 for kw in keywords if kw in text)
        if score > 0:
            scored.append((score, nid))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [nid for _, nid in scored[:max_seeds]]


def _node_text(info: dict) -> str:
    props = info.get("props", {})
    parts = [info.get("label", "")]
    for k in ("name", "title", "content", "description", "statement"):
        v = props.get(k, "")
        if v:
            parts.append(str(v))
    return " ".join(parts)


def _node_name(info: dict) -> str:
    props = info.get("props", {})
    return props.get("name") or props.get("title") or props.get("content", "")[:40] or info.get("label", "?")


def _llm_answer(question: str, chain: list, evidence: list) -> str:
    """Use LLM to synthesize answer from reasoning chain."""
    try:
        from contextsynapse.llm.client import LLMClient
        llm = LLMClient()

        chain_text = "\n".join(
            f"  Hop {c['hop']}: [{c['from_type']}] {c['from']} --{c['edge']}--> [{c['to_type']}] {c['to']}"
            for c in chain[:15]
        )
        evidence_text = "\n".join(f"  - {e}" for e in evidence[:8])

        prompt = (
            f"Answer the question using ONLY the graph traversal evidence below.\n"
            f"Explain the reasoning path (which nodes and edges you followed).\n\n"
            f"Question: {question}\n\n"
            f"Graph traversal path:\n{chain_text}\n\n"
            f"Evidence collected:\n{evidence_text}\n\n"
            f"Answer (include the reasoning path):"
        )
        answer = llm.generate(prompt, system="Answer based on graph evidence. Explain the path.", max_tokens=500)
        if answer and answer.strip():
            return answer.strip()
    except Exception as e:
        logger.debug("LLM reasoning failed: %s", e)

    return _heuristic_answer(question, chain, evidence)


def _heuristic_answer(question: str, chain: list, evidence: list) -> str:
    """Build answer from chain without LLM."""
    if not chain:
        return "No reasoning path found."
    lines = ["Reasoning path:"]
    for c in chain[:10]:
        lines.append(f"  [{c['from_type']}] {c['from']} --{c['edge']}--> [{c['to_type']}] {c['to']}")
    if evidence:
        lines.append("\nEvidence:")
        for e in evidence[:5]:
            lines.append(f"  {e}")
    return "\n".join(lines)
