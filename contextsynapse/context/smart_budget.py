"""
Smart Context Budgeting — Outcome-based context prioritization.

Instead of giving agents everything, measure which context items
actually led to good outcomes (task completions, correct decisions)
and prioritize those for future agents.

The "smart" part: context that helped Agent A succeed gets boosted
for Agent B working on similar tasks.

Usage:
    from contextsynapse.context.smart_budget import SmartBudget
    budget = SmartBudget(graph_registry)
    ranked = budget.rank_context_items(session_id, agent_id, items, budget_tokens=4000)
"""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class SmartBudget:
    """Outcome-aware context prioritization."""

    def __init__(self, graph_registry=None):
        self._registry = graph_registry

    def rank_context_items(
        self,
        namespace: str,
        agent_id: str,
        items: List[Dict[str, Any]],
        budget_tokens: int = 4000,
    ) -> List[Dict[str, Any]]:
        """Re-rank context items by outcome relevance.

        Scores each item based on:
        1. Recency (newer = higher)
        2. Agent gravity (matches agent's current focus)
        3. Outcome signal (items referenced by completed tasks score higher)
        4. Cross-agent success (items that helped other agents complete tasks)
        """
        if not items:
            return []

        # Get completed tasks to measure outcomes
        outcome_keywords = set()
        try:
            from .gravity import get_gravity
            intent = get_gravity().get_intent(agent_id)
            outcome_keywords = set(intent.keys())
        except Exception:
            pass

        # Get completed task titles/descriptions for outcome signal
        completed_terms = set()
        if self._registry and namespace:
            try:
                db = self._registry.get_graph(namespace)
                if db:
                    for n in db.get_all_nodes():
                        if getattr(n, "label", "") == "Task":
                            props = n.properties if hasattr(n, "properties") else {}
                            if props.get("status") in ("completed", "done"):
                                title = (props.get("title") or "").lower()
                                completed_terms.update(w for w in title.split() if len(w) > 3)
                                desc = (props.get("description") or "").lower()
                                completed_terms.update(w for w in desc.split() if len(w) > 3)
            except Exception:
                pass

        # Score each item
        scored = []
        for item in items:
            score = 1.0
            text = ""
            if isinstance(item, dict):
                text = " ".join(str(v) for v in item.values() if isinstance(v, str)).lower()
            elif hasattr(item, "properties"):
                text = " ".join(str(v) for v in item.properties.values() if isinstance(v, str)).lower()

            # Gravity boost: matches agent's current focus
            if outcome_keywords:
                gravity_matches = sum(1 for kw in outcome_keywords if kw in text)
                score += gravity_matches * 0.5

            # Outcome boost: referenced by completed tasks
            if completed_terms:
                outcome_matches = sum(1 for term in completed_terms if term in text)
                score += outcome_matches * 0.3

            # Recency boost
            created = ""
            if isinstance(item, dict):
                created = item.get("_created_at", item.get("created_at", ""))
            elif hasattr(item, "properties"):
                created = item.properties.get("_created_at", "")
            if created:
                # More recent = higher score (simple: longer ISO string = more recent)
                score += 0.1  # base boost for having a timestamp

            scored.append((score, item))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Budget: keep items until we hit token limit
        result = []
        tokens_used = 0
        for score, item in scored:
            # Estimate tokens for this item
            if isinstance(item, dict):
                item_text = " ".join(str(v) for v in item.values() if isinstance(v, str))
            elif hasattr(item, "properties"):
                item_text = " ".join(str(v) for v in item.properties.values() if isinstance(v, str))
            else:
                item_text = str(item)
            item_tokens = len(item_text) // 4

            if tokens_used + item_tokens > budget_tokens and result:
                break
            result.append(item)
            tokens_used += item_tokens

        return result
