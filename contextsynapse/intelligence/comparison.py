"""
Multi-Model Comparison Engine
==============================
Run the same extraction task with multiple LLMs in parallel and compare:
- Speed (time to complete)
- Quality (entities extracted, relationships found)
- Cost (estimated token usage)

Usage:
    from contextsynapse.comparison import compare_models

    results = compare_models(
        text="Alice works at Acme Corp as CTO...",
        models=["groq:gpt-oss-120b", "openai:gpt-4o-mini", "anthropic:claude-sonnet-4-20250514"],
        schema=extraction_schema,
    )
"""

from __future__ import annotations

import json
import logging
import time
import threading
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Approximate cost per 1K tokens (input/output) — rough estimates
_TOKEN_COSTS = {
    "gpt-4o": (0.005, 0.015),
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4.1-nano": (0.0001, 0.0004),
    "claude-sonnet-4-20250514": (0.003, 0.015),
    "claude-haiku-4-5-20251001": (0.001, 0.005),
    "gpt-oss-120b": (0.0, 0.0),  # Groq free tier
    "gpt-oss-20b": (0.0, 0.0),
    "llama-v3p1-70b-instruct": (0.0009, 0.0009),
    "mistral-large-latest": (0.002, 0.006),
    "gemini-2.0-flash": (0.0, 0.0),
}


def _estimate_cost(model: str, input_tokens: int, output_tokens: int) -> float:
    """Estimate cost in USD for a model call."""
    for model_key, (in_cost, out_cost) in _TOKEN_COSTS.items():
        if model_key in model:
            return (input_tokens / 1000 * in_cost) + (output_tokens / 1000 * out_cost)
    return 0.0


def _run_extraction(
    provider: str,
    model: str,
    text: str,
    prompt: str,
    result_holder: Dict,
):
    """Run extraction with one model and store results."""
    try:
        from contextsynapse.llm.client import get_llm_client

        start = time.time()
        llm = get_llm_client(provider=provider, model=model)
        full_prompt = f"{prompt}\n\nText:\n{text[:3000]}"

        raw = llm.generate(prompt=full_prompt, max_tokens=4000)
        elapsed = time.time() - start

        # Parse JSON response
        import re
        json_match = re.search(r'```(?:json)?\s*([\s\S]*?)```', raw or "")
        text_to_parse = json_match.group(1).strip() if json_match else (raw or "").strip()

        try:
            data = json.loads(text_to_parse)
        except json.JSONDecodeError:
            data = {"entities": [], "relationships": [], "facts": []}

        entities = data.get("entities", [])
        relationships = data.get("relationships", [])
        facts = data.get("facts", [])

        # Estimate tokens (rough: 4 chars per token)
        input_tokens = len(full_prompt) // 4
        output_tokens = len(raw or "") // 4
        cost = _estimate_cost(model, input_tokens, output_tokens)

        result_holder["status"] = "completed"
        result_holder["time_seconds"] = round(elapsed, 2)
        result_holder["entities"] = len(entities)
        result_holder["relationships"] = len(relationships)
        result_holder["facts"] = len(facts)
        result_holder["entity_types"] = list(set(e.get("type", "?") for e in entities))
        result_holder["relationship_types"] = list(set(r.get("type", r.get("relation", "?")) for r in relationships))
        result_holder["input_tokens"] = input_tokens
        result_holder["output_tokens"] = output_tokens
        result_holder["estimated_cost_usd"] = round(cost, 6)
        result_holder["raw_response_length"] = len(raw or "")
        result_holder["sample_entities"] = [
            {"name": e.get("name", "?"), "type": e.get("type", "?")}
            for e in entities[:5]
        ]

    except Exception as e:
        result_holder["status"] = "failed"
        result_holder["error"] = str(e)
        result_holder["time_seconds"] = 0


def compare_models(
    text: str,
    models: List[str],
    schema=None,
    schema_prompt: str = None,
) -> Dict[str, Any]:
    """Run extraction with multiple models in parallel and compare results.

    Args:
        text: The text to extract from.
        models: List of "provider:model" strings (e.g., ["groq:gpt-oss-120b", "openai:gpt-4o-mini"]).
        schema: Optional ExtractionSchema object.
        schema_prompt: Optional pre-built schema prompt string.

    Returns:
        {"models": [...], "comparison": {...}, "best": {...}}
    """
    # Build the extraction prompt
    if schema_prompt:
        prompt = schema_prompt
    elif schema:
        from contextsynapse.extraction.schema_loader import schema_to_prompt
        prompt = schema_to_prompt(schema)
    else:
        prompt = (
            "Extract entities, relationships, and facts from the text below.\n"
            "Return JSON: {\"entities\": [{\"name\": \"...\", \"type\": \"...\"}], "
            "\"relationships\": [{\"source\": \"...\", \"target\": \"...\", \"type\": \"...\"}], "
            "\"facts\": [{\"statement\": \"...\", \"type\": \"Fact\"}]}\n"
            "Return ONLY valid JSON."
        )

    # Parse model specs and run in parallel
    results = {}
    threads = []

    for model_spec in models:
        parts = model_spec.split(":", 1)
        provider = parts[0] if len(parts) > 1 else "openai"
        model = parts[1] if len(parts) > 1 else parts[0]

        result = {
            "provider": provider,
            "model": model,
            "model_spec": model_spec,
            "status": "running",
        }
        results[model_spec] = result

        t = threading.Thread(
            target=_run_extraction,
            args=(provider, model, text, prompt, result),
            name=f"compare-{model_spec}",
        )
        threads.append(t)
        t.start()

    # Wait for all to complete (max 60s)
    for t in threads:
        t.join(timeout=60)

    # Build comparison
    completed = [r for r in results.values() if r["status"] == "completed"]

    comparison = {}
    if completed:
        comparison["fastest"] = min(completed, key=lambda r: r["time_seconds"])["model_spec"]
        comparison["most_entities"] = max(completed, key=lambda r: r["entities"])["model_spec"]
        comparison["most_relationships"] = max(completed, key=lambda r: r["relationships"])["model_spec"]
        comparison["cheapest"] = min(completed, key=lambda r: r["estimated_cost_usd"])["model_spec"]

        # Quality score: entities + relationships + facts (higher = more thorough)
        for r in completed:
            r["quality_score"] = r["entities"] + r["relationships"] + r["facts"]
        comparison["best_quality"] = max(completed, key=lambda r: r["quality_score"])["model_spec"]

        # Value score: quality / (time + 1) — best balance of quality and speed
        for r in completed:
            r["value_score"] = round(r["quality_score"] / (r["time_seconds"] + 1), 2)
        comparison["best_value"] = max(completed, key=lambda r: r["value_score"])["model_spec"]

    return {
        "models": list(results.values()),
        "comparison": comparison,
        "text_length": len(text),
        "models_tested": len(models),
        "models_completed": len(completed),
    }
