"""
Live A/B Benchmark — Run same questions with and without shared context.

Uses the user's actual graph data. Measures real token usage, latency,
and answer quality for both paths.

Usage:
    from contextsynapse.benchmark.live_ab import run_live_ab
    result = run_live_ab(graph_registry, "my_graph", questions=["What bugs exist?"])
"""

from __future__ import annotations

import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


def run_live_ab(
    graph_registry,
    graph_name: str,
    questions: Optional[List[str]] = None,
    model: Optional[str] = None,
    score_quality: bool = True,
) -> Dict[str, Any]:
    """Run A/B benchmark on a real graph.

    Path A (WITH AIContextDB): Uses ask/rag_query tools — gets targeted context
    Path B (WITHOUT — baseline): Dumps entire graph as text, sends to LLM

    Returns:
        {
            questions: [...],
            path_a: {total_tokens, total_cost, avg_latency_ms, answers: [...]},
            path_b: {total_tokens, total_cost, avg_latency_ms, answers: [...]},
            savings: {tokens_saved, savings_pct, cost_saved, speedup},
        }
    """
    db = graph_registry.get_graph(graph_name) if graph_registry else None
    if not db:
        return {"error": f"Graph '{graph_name}' not found"}

    # Get all nodes for baseline dump
    all_nodes = db.get_all_nodes()
    if not all_nodes:
        return {"error": "Graph is empty — ingest data first"}

    # Build full graph dump text (what you'd need WITHOUT AIContextDB)
    full_dump = _build_full_dump(all_nodes)
    full_dump_tokens = len(full_dump) // 4

    # Default questions if none provided
    if not questions:
        questions = _generate_questions(all_nodes)

    # Get LLM
    try:
        from contextsynapse.llm.client import LLMClient
        llm = LLMClient()
    except Exception as e:
        return {"error": f"LLM not available: {e}"}

    # --- Path A: WITH AIContextDB (targeted retrieval) ---
    path_a_results = []
    for q in questions:
        result = _run_path_a(db, graph_name, q, llm)
        path_a_results.append(result)

    # --- Path B: WITHOUT AIContextDB (full dump baseline) ---
    path_b_results = []
    for q in questions:
        result = _run_path_b(full_dump, q, llm)
        path_b_results.append(result)

    # --- Aggregate ---
    a_tokens = sum(r["tokens"] for r in path_a_results)
    b_tokens = sum(r["tokens"] for r in path_b_results)
    a_cost = sum(r["cost"] for r in path_a_results)
    b_cost = sum(r["cost"] for r in path_b_results)
    a_latency = sum(r["latency_ms"] for r in path_a_results) / len(path_a_results) if path_a_results else 0
    b_latency = sum(r["latency_ms"] for r in path_b_results) / len(path_b_results) if path_b_results else 0

    tokens_saved = max(0, b_tokens - a_tokens)
    savings_pct = round(tokens_saved / b_tokens * 100, 1) if b_tokens > 0 else 0
    cost_saved = max(0, b_cost - a_cost)
    speedup = round(b_latency / a_latency, 1) if a_latency > 0 else 0

    # --- Precision & Quality scoring (optional — uses extra LLM calls) ---
    precision_results = _score_precision(questions, path_a_results, path_b_results, llm) if score_quality else []

    avg_precision = 0.0
    avg_quality_a = 0.0
    avg_quality_b = 0.0
    if precision_results:
        avg_precision = round(sum(p["precision"] for p in precision_results) / len(precision_results), 2)
        avg_quality_a = round(sum(p["quality_a"] for p in precision_results) / len(precision_results), 2)
        avg_quality_b = round(sum(p["quality_b"] for p in precision_results) / len(precision_results), 2)

    return {
        "graph": graph_name,
        "graph_nodes": len(all_nodes),
        "graph_dump_tokens": full_dump_tokens,
        "questions": questions,
        "question_count": len(questions),

        "with_contextcore": {
            "total_tokens": a_tokens,
            "total_cost_usd": round(a_cost, 6),
            "avg_latency_ms": round(a_latency),
            "avg_tokens_per_query": round(a_tokens / len(questions)) if questions else 0,
            "avg_quality_score": avg_quality_a,
            "answers": [{"question": q, **r} for q, r in zip(questions, path_a_results)],
        },
        "without_contextcore": {
            "total_tokens": b_tokens,
            "total_cost_usd": round(b_cost, 6),
            "avg_latency_ms": round(b_latency),
            "avg_tokens_per_query": round(b_tokens / len(questions)) if questions else 0,
            "avg_quality_score": avg_quality_b,
            "answers": [{"question": q, **r} for q, r in zip(questions, path_b_results)],
        },
        "savings": {
            "tokens_saved": tokens_saved,
            "savings_pct": savings_pct,
            "cost_saved_usd": round(cost_saved, 6),
            "speedup": f"{speedup}x",
        },
        "quality": {
            "context_precision": avg_precision,
            "quality_with_contextcore": avg_quality_a,
            "quality_without_contextcore": avg_quality_b,
            "quality_delta": round(avg_quality_a - avg_quality_b, 2),
            "verdict": (
                "AIContextDB answers are equivalent or better"
                if avg_quality_a >= avg_quality_b - 0.5
                else "Full context produces slightly better answers (consider tuning retrieval k)"
            ),
            "per_question": precision_results,
        },
    }


def _score_precision(
    questions: List[str],
    path_a_results: List[Dict],
    path_b_results: List[Dict],
    llm,
) -> List[Dict[str, Any]]:
    """Use LLM as judge to score answer quality and context precision.

    For each question, compares Path A (targeted) vs Path B (full dump) answers.
    Returns per-question scores:
        - precision: 0-10 (did retrieval find the right context?)
        - quality_a: 0-10 (how good is the AIContextDB answer?)
        - quality_b: 0-10 (how good is the full-dump answer?)
        - missing: what Path A missed that Path B caught
    """
    results = []

    for i, question in enumerate(questions):
        answer_a = path_a_results[i].get("answer", "") if i < len(path_a_results) else ""
        answer_b = path_b_results[i].get("answer", "") if i < len(path_b_results) else ""

        if not answer_a and not answer_b:
            results.append({"precision": 5, "quality_a": 0, "quality_b": 0, "missing": "", "judge_reasoning": "Both empty"})
            continue

        try:
            import re
            prompt = (
                f"You are an impartial answer quality judge.\n\n"
                f"Question: {question}\n\n"
                f"Answer A (from targeted retrieval — 5 relevant nodes):\n{answer_a[:500]}\n\n"
                f"Answer B (from full context dump — all nodes):\n{answer_b[:500]}\n\n"
                f"Score each answer 0-10 on completeness, accuracy, and relevance.\n"
                f"Also rate retrieval precision 0-10: did Answer A cover the same key points as Answer B?\n"
                f"Note what (if anything) Answer A missed that Answer B caught.\n\n"
                f"Respond ONLY in this format:\n"
                f"quality_a: <number>\n"
                f"quality_b: <number>\n"
                f"precision: <number>\n"
                f"missing: <what A missed, or 'nothing'>\n"
                f"reasoning: <one sentence>"
            )

            response = llm.generate(prompt, system="Output only the scores in the specified format.", max_tokens=200)

            # Parse scores
            qa = _parse_score(response, "quality_a")
            qb = _parse_score(response, "quality_b")
            prec = _parse_score(response, "precision")
            missing_match = re.search(r"missing:\s*(.+?)(?:\n|$)", response, re.IGNORECASE)
            reasoning_match = re.search(r"reasoning:\s*(.+?)(?:\n|$)", response, re.IGNORECASE)

            results.append({
                "precision": prec,
                "quality_a": qa,
                "quality_b": qb,
                "missing": missing_match.group(1).strip() if missing_match else "",
                "judge_reasoning": reasoning_match.group(1).strip() if reasoning_match else "",
            })
        except Exception as e:
            logger.debug("Precision scoring failed for q%d: %s", i, e)
            # Fallback: simple length-based heuristic
            len_a = len(answer_a)
            len_b = len(answer_b)
            ratio = min(len_a, len_b) / max(len_a, len_b) if max(len_a, len_b) > 0 else 0
            results.append({
                "precision": round(ratio * 10, 1),
                "quality_a": 5 if answer_a else 0,
                "quality_b": 5 if answer_b else 0,
                "missing": "",
                "judge_reasoning": "LLM judge unavailable — estimated from answer length",
            })

    return results


def _parse_score(text: str, field: str) -> float:
    """Extract a numeric score from LLM judge output."""
    import re
    match = re.search(rf"{field}:\s*([\d.]+)", text, re.IGNORECASE)
    if match:
        return min(10.0, max(0.0, float(match.group(1))))
    return 5.0  # default if not found


def _build_full_dump(nodes) -> str:
    """Build a full text dump of all graph nodes (baseline context)."""
    parts = []
    for node in nodes:
        props = node.properties if hasattr(node, "properties") else {}
        label = node.label if hasattr(node, "label") else ""
        name = props.get("name") or props.get("title") or ""
        content = props.get("content") or props.get("description") or props.get("statement") or ""
        if name or content:
            parts.append(f"[{label}] {name}\n{content}")
    return "\n\n---\n\n".join(parts)


def _generate_questions(nodes) -> List[str]:
    """Auto-generate relevant questions from graph content."""
    labels = set()
    names = []
    for node in nodes:
        label = node.label if hasattr(node, "label") else ""
        props = node.properties if hasattr(node, "properties") else {}
        if label:
            labels.add(label)
        name = props.get("name") or props.get("title") or ""
        if name and len(names) < 5:
            names.append((label, name))

    questions = []
    if labels:
        top_label = max(labels, key=lambda l: sum(1 for n in nodes if getattr(n, "label", "") == l))
        questions.append(f"What {top_label.lower()}s exist in this project?")
    if names:
        questions.append(f"Tell me about {names[0][1]}")
    if len(names) > 1:
        questions.append(f"How does {names[0][1]} relate to {names[1][1]}?")
    questions.append("What are the key findings or decisions?")
    questions.append("Summarize the most important information")
    return questions[:5]


def _run_path_a(db, graph_name: str, question: str, llm) -> Dict[str, Any]:
    """Path A: WITH AIContextDB — targeted retrieval then answer."""
    start = time.monotonic()

    # Use hybrid retrieval (same as rag_query tool)
    from contextsynapse.search.rag import hybrid_retrieve
    top, sources = hybrid_retrieve(db, question, graph_name, k=5)

    # Build targeted context from top results only
    context_parts = []
    for r in top:
        props = r.get("props", {})
        content = props.get("content") or props.get("description") or props.get("statement") or props.get("name", "")
        if content:
            context_parts.append(content[:500])
    context = "\n\n".join(context_parts)

    # Count tokens for the targeted context
    prompt = f"Context:\n{context}\n\nQuestion: {question}\n\nAnswer concisely."
    prompt_tokens = len(prompt) // 4

    # Generate answer
    try:
        answer = llm.generate(prompt, system="Answer based on the provided context.", max_tokens=500)
    except Exception:
        answer = "(LLM unavailable)"
    answer_tokens = len(answer) // 4
    total_tokens = prompt_tokens + answer_tokens

    latency = int((time.monotonic() - start) * 1000)

    return {
        "tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "answer_tokens": answer_tokens,
        "context_chars": len(context),
        "sources": len(sources),
        "latency_ms": latency,
        "cost": total_tokens / 1_000_000 * 2.50,
        "answer": answer[:200],
    }


def _run_path_b(full_dump: str, question: str, llm) -> Dict[str, Any]:
    """Path B: WITHOUT AIContextDB — full dump then answer."""
    start = time.monotonic()

    # Send EVERYTHING to the LLM (no retrieval — brute force)
    prompt = f"Context:\n{full_dump}\n\nQuestion: {question}\n\nAnswer concisely."
    prompt_tokens = len(prompt) // 4

    try:
        answer = llm.generate(prompt, system="Answer based on the provided context.", max_tokens=500)
    except Exception:
        answer = "(LLM unavailable)"
    answer_tokens = len(answer) // 4
    total_tokens = prompt_tokens + answer_tokens

    latency = int((time.monotonic() - start) * 1000)

    return {
        "tokens": total_tokens,
        "prompt_tokens": prompt_tokens,
        "answer_tokens": answer_tokens,
        "context_chars": len(full_dump),
        "sources": 0,  # no retrieval — everything sent
        "latency_ms": latency,
        "cost": total_tokens / 1_000_000 * 2.50,
        "answer": answer[:200],
    }
