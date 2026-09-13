"""
Context Relevance Gravity™
============================
Dynamically re-ranks context items based on what the agent is actually doing.

Instead of static context delivery, the system observes agent tool calls and
queries, learns the agent's current intent, and surfaces the most relevant
context items for their next interaction.

Patent-worthy: No existing system adapts context delivery based on real-time
agent execution behavior.

Usage:
    gravity = ContextGravity()

    # Track agent behavior
    gravity.observe(agent_id, "search_nodes", {"query": "authentication"})
    gravity.observe(agent_id, "claim_task", {"task_id": "auth-task-123"})

    # Get gravity-adjusted scores for context items
    scores = gravity.score_items(agent_id, items)
    # Items about authentication will score higher
"""

from __future__ import annotations

import logging
import re
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class ContextGravity:
    """Tracks agent behavior and computes relevance gravity for context items.

    The "gravity" metaphor: frequently-accessed topics pull related context
    items closer (higher score), while untouched topics drift away (lower score).
    """

    def __init__(self, decay_seconds: int = 300):
        """
        Args:
            decay_seconds: How quickly gravity decays (default 5 minutes).
                          Recent actions have more gravity than old ones.
        """
        self._decay = decay_seconds
        # agent_id -> list of (timestamp, keywords, weight)
        self._observations: Dict[str, List[Tuple[float, List[str], float]]] = defaultdict(list)
        # agent_id -> inferred intent keywords (accumulated)
        self._intent_cache: Dict[str, Dict[str, float]] = defaultdict(lambda: defaultdict(float))
        # agent_id -> set of keywords from remembered memories (persistent boost)
        self._memory_keywords: Dict[str, set] = defaultdict(set)

    def observe(self, agent_id: str, tool_name: str, args: Dict[str, Any] = None):
        """Record an agent action and extract intent signals.

        Called by the tool dispatch system after each tool call.
        """
        args = args or {}
        keywords = []
        weight = 1.0

        # Extract keywords from different tool patterns
        if tool_name in ("search_nodes", "ask"):
            query = args.get("query") or args.get("question", "")
            keywords = self._extract_keywords(query)
            weight = 2.0  # searches are strong intent signals

        elif tool_name == "claim_task":
            # Task title/description carries intent
            task_id = str(args.get("task_id", ""))
            keywords = [task_id[:12]]  # will match via ID
            weight = 3.0  # claiming a task is strongest signal

        elif tool_name in ("ws_write_file", "ws_read_file"):
            filepath = args.get("filepath", "")
            # Extract meaningful parts from file path
            parts = filepath.replace("\\", "/").split("/")
            keywords = [p for p in parts if p and not p.startswith(".")]
            weight = 1.5

        elif tool_name == "add_knowledge":
            label = args.get("label", "")
            props = args.get("properties", "")
            content = args.get("content", "")
            keywords = self._extract_keywords(f"{label} {props} {content}")
            weight = 1.0

        elif tool_name == "remember":
            # Memory storage is a strong intent signal — agent explicitly chose to remember this
            content = args.get("content", "")
            keywords = self._extract_keywords(content)
            weight = 3.0  # memories are high-intent signals
            # Cache memory keywords for persistent boosting in score_items
            self._memory_keywords[agent_id].update(keywords)

        elif tool_name == "recall":
            query = args.get("query", "")
            keywords = self._extract_keywords(query)
            weight = 2.5  # recalling = actively searching memory

        elif tool_name == "task_context":
            task_id = args.get("task_id", "")
            keywords = [task_id[:12]]
            weight = 2.0

        elif tool_name == "query_graph":
            query = args.get("query", "")
            keywords = self._extract_keywords(query)
            weight = 1.5

        if keywords:
            now = time.time()
            self._observations[agent_id].append((now, keywords, weight))

            # Update intent cache
            for kw in keywords:
                self._intent_cache[agent_id][kw] += weight

            # Prune old observations (older than 2x decay)
            cutoff = now - (self._decay * 2)
            self._observations[agent_id] = [
                (ts, kws, w) for ts, kws, w in self._observations[agent_id]
                if ts > cutoff
            ]

    def get_intent(self, agent_id: str) -> Dict[str, float]:
        """Get the current inferred intent for an agent.

        Returns a dict of keyword → weight, decayed by time.
        """
        now = time.time()
        intent: Dict[str, float] = defaultdict(float)

        for ts, keywords, weight in self._observations.get(agent_id, []):
            # Exponential decay based on age
            age = now - ts
            decay_factor = max(0.1, 1.0 - (age / self._decay))
            for kw in keywords:
                intent[kw] += weight * decay_factor

        return dict(intent)

    def score_items(
        self,
        agent_id: str,
        items: List[Dict[str, Any]],
        base_scores: Optional[List[float]] = None,
    ) -> List[Tuple[Dict, float]]:
        """Score context items by relevance to the agent's current intent.

        Args:
            agent_id: The agent to score for.
            items: List of context item dicts.
            base_scores: Optional existing scores to boost (not replace).

        Returns:
            List of (item, gravity_score) sorted by score descending.
        """
        intent = self.get_intent(agent_id)
        if not intent:
            # No observations yet — return items with base scores
            if base_scores:
                return sorted(zip(items, base_scores), key=lambda x: x[1], reverse=True)
            return [(item, 0.5) for item in items]

        # Normalize intent weights
        max_weight = max(intent.values()) if intent else 1.0

        scored = []
        for i, item in enumerate(items):
            # Build searchable text from item
            text = " ".join(str(v) for v in item.values()).lower()

            # Compute gravity score based on keyword overlap
            gravity = 0.0
            for keyword, weight in intent.items():
                if keyword.lower() in text:
                    gravity += weight / max_weight

            # Memory boost: if agent has memories matching this item, boost score
            memory_boost = 0.0
            if agent_id in self._memory_keywords:
                for mkw in self._memory_keywords[agent_id]:
                    if mkw in text:
                        memory_boost += 0.3  # significant boost per matching memory keyword
                memory_boost = min(memory_boost, 1.0)  # cap at 1.0

            # Combine with base score if available
            base = base_scores[i] if base_scores and i < len(base_scores) else 0.5
            combined = (base * 0.3) + (gravity * 0.5) + (memory_boost * 0.2)

            scored.append((item, round(combined, 3)))

        scored.sort(key=lambda x: x[1], reverse=True)
        return scored

    def clear(self, agent_id: str):
        """Reset gravity for an agent (new task/session)."""
        self._observations.pop(agent_id, None)
        self._intent_cache.pop(agent_id, None)

    @staticmethod
    def _extract_keywords(text: str) -> List[str]:
        """Extract meaningful keywords from text."""
        # Remove common stop words and extract meaningful terms
        stop = {"the", "a", "an", "is", "are", "was", "were", "be", "been",
                "to", "of", "in", "for", "on", "with", "at", "by", "from",
                "what", "how", "why", "when", "where", "which", "who",
                "this", "that", "these", "those", "it", "its", "my",
                "and", "or", "but", "not", "no", "all", "any", "some",
                "has", "have", "had", "do", "does", "did", "will", "would",
                "can", "could", "should", "shall", "may", "might"}
        words = re.findall(r'[a-zA-Z_]{3,}', text.lower())
        return [w for w in words if w not in stop][:10]  # max 10 keywords


# Global singleton
_gravity = ContextGravity()


def get_gravity() -> ContextGravity:
    """Get the global ContextGravity instance."""
    return _gravity
