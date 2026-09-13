"""
Context Scoping
================
Token budget enforcement and priority ranking for context items.

When a ContextHub has more content than fits in a model's context window,
the scoper selects the most valuable items within a token budget.

Usage::

    from contextsynapse.context.scoping import ContextScoper, ScopingConfig

    scoper = ContextScoper(ScopingConfig(max_tokens=4000, strategy="combined"))
    selected = scoper.select_within_budget(hub.items(), max_tokens=4000)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .hub import ContextItem, ContextRole

logger = logging.getLogger(__name__)

# Optional tiktoken for accurate token counts
_tiktoken_available = False
_tiktoken_encoding = None
try:
    import tiktoken
    _tiktoken_encoding = tiktoken.get_encoding("cl100k_base")
    _tiktoken_available = True
except Exception:
    pass


# Default role weights — higher = more important to keep
DEFAULT_ROLE_WEIGHTS: Dict[str, float] = {
    "system": 10.0,
    "instruction": 9.0,
    "decision": 8.0,
    "feedback": 7.5,       # human corrections are high-value
    "synthesis": 7.0,
    "human": 6.5,           # explicit human-provided context
    "retrieved": 6.0,
    "document": 5.5,        # full source documents
    "background": 5.0,
    "tool": 4.5,            # tool call results
    "interaction": 4.0,     # multi-turn dialogue transcripts
    "example": 4.0,
    "generated": 3.0,
    "user": 2.0,
    "assistant": 1.0,
}


@dataclass
class ScopingConfig:
    """Configuration for token budget enforcement."""
    max_tokens: int = 8000
    strategy: str = "combined"        # recency | relevance | role_priority | combined
    role_weights: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_ROLE_WEIGHTS))
    recency_decay: float = 0.95       # multiplier per position from newest
    reserve_system: bool = True       # always keep system messages
    reserve_ratio: float = 0.1        # fraction of budget reserved for system messages
    # Combined strategy weights
    role_weight_factor: float = 0.3
    recency_factor: float = 0.3
    relevance_factor: float = 0.4
    confidence_factor: float = 0.0    # opt-in: weight of confidence in combined scoring
    # Custom per-label or per-tag boosts
    custom_weights: Dict[str, float] = field(default_factory=dict)


@dataclass
class ScoredItem:
    """A context item annotated with its priority score and token estimate."""
    item: Any  # ContextItem
    index: int            # original position in the list
    score: float = 0.0
    token_estimate: int = 0
    breakdown: Dict = None  # {"role": 0.27, "recency": 0.25, "relevance": 0.38, "confidence": 0.10}
    reason: str = ""        # human-readable: "relevance (0.38) + role (0.27)"

    def __post_init__(self):
        if self.breakdown is None:
            self.breakdown = {}


class ContextScoper:
    """
    Score and select context items within a token budget.

    Strategies:
    - **recency**: Newer items (later in list) score higher.
    - **role_priority**: Items with important roles (system, instruction) score higher.
    - **relevance**: External relevance scores (e.g., from vector similarity).
    - **combined**: Weighted mix of all three.
    """

    def __init__(self, config: Optional[ScopingConfig] = None):
        self.config = config or ScopingConfig()

    def estimate_tokens(self, text: str) -> int:
        """Estimate token count for a string."""
        if _tiktoken_available and _tiktoken_encoding is not None:
            return len(_tiktoken_encoding.encode(text))
        return max(1, int(len(text) / 4.0))

    @staticmethod
    def _compute_query_relevance(query: str, items: List[Any]) -> Dict[int, float]:
        """Compute keyword-overlap relevance between a query and each item.

        Returns a dict of {item_index: score} where score is in [0, 1].
        Uses normalized keyword intersection — lightweight, no embeddings needed.
        """
        _STOP_WORDS = frozenset({
            "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
            "have", "has", "had", "do", "does", "did", "will", "would", "could",
            "should", "may", "might", "shall", "can", "need", "dare", "ought",
            "this", "that", "these", "those", "i", "you", "he", "she", "it",
            "we", "they", "what", "which", "who", "whom", "how", "where", "when",
            "why", "all", "each", "every", "both", "few", "more", "most", "other",
            "some", "such", "no", "not", "only", "own", "same", "so", "than",
            "too", "very", "just", "about", "above", "after", "again", "also",
            "and", "any", "as", "at", "but", "by", "for", "from", "get", "show",
            "find", "tell", "give", "in", "into", "of", "off", "on", "or", "out",
            "to", "up", "with",
        })
        import re as _re
        query_terms = {
            w for w in _re.split(r"\W+", query.lower()) if len(w) > 1 and w not in _STOP_WORDS
        }
        if not query_terms:
            return {}

        scores: Dict[int, float] = {}
        for i, item in enumerate(items):
            text = item.content.lower() if hasattr(item, "content") else str(item).lower()
            # Count how many query terms appear in the item
            hits = sum(1 for t in query_terms if t in text)
            scores[i] = hits / len(query_terms)  # [0.0, 1.0]
        return scores

    def score_items(
        self,
        items: List[Any],
        query: Optional[str] = None,
        relevance_scores: Optional[Dict[int, float]] = None,
    ) -> List[ScoredItem]:
        """
        Score each item according to the configured strategy.

        Args:
            items: List of ContextItem objects.
            query: Optional query string for computing relevance scores
                automatically when *relevance_scores* is not provided.
            relevance_scores: Optional dict mapping item index → relevance score [0, 1].
                When omitted and *query* is given, keyword-overlap relevance is
                computed automatically.

        Returns:
            List of ScoredItem with scores and token estimates.
        """
        n = len(items)
        if n == 0:
            return []

        scored: List[ScoredItem] = []
        strategy = self.config.strategy

        # Auto-compute relevance when a query is given but no external scores
        if not relevance_scores and query:
            relevance_scores = self._compute_query_relevance(query, items)
        relevance_scores = relevance_scores or {}

        for i, item in enumerate(items):
            tokens = self.estimate_tokens(item.content)
            role_val = item.role.value if hasattr(item.role, "value") else str(item.role)

            # --- Temporal filtering (C3) ---
            item_status = getattr(item, "state", "active")
            if item_status in ("superseded", "expired"):
                scored.append(ScoredItem(item=item, index=i, score=0.0, token_estimate=tokens,
                                         breakdown={"excluded": 1.0}, reason=f"excluded ({item_status})"))
                continue
            # Check valid_to in metadata
            valid_to = (getattr(item, "metadata", {}) or {}).get("valid_to")
            if valid_to and isinstance(valid_to, str):
                try:
                    from datetime import datetime as _dt, timezone as _tz
                    if valid_to < _dt.now(_tz.utc).isoformat():
                        scored.append(ScoredItem(item=item, index=i, score=0.0, token_estimate=tokens,
                                                 breakdown={"excluded": 1.0}, reason="excluded (expired)"))
                        continue
                except Exception:
                    pass

            # --- Recency score ---
            # Use _created_at timestamp if available, otherwise position-based
            recency = self.config.recency_decay ** (n - 1 - i)
            created_at = getattr(item, "created_at", "") or (item.metadata.get("_created_at", "") if hasattr(item, "metadata") else "")
            if created_at:
                try:
                    from datetime import datetime, timezone
                    import math
                    ts = datetime.fromisoformat(created_at.replace("Z", "+00:00"))
                    age_h = max(0.01, (datetime.now(timezone.utc) - ts).total_seconds() / 3600)
                    # Newer = higher recency (half-life 72 hours)
                    recency = math.exp(-0.693 * age_h / 72)
                except Exception:
                    pass

            # --- Role priority score ---
            role_score = self.config.role_weights.get(role_val, 1.0) / 10.0  # normalise to [0, 1]

            # --- Relevance score ---
            relevance = relevance_scores.get(i, 0.3)  # default low when no query match

            # --- Custom boosts ---
            boost = 0.0
            if item.label and item.label in self.config.custom_weights:
                boost = max(boost, self.config.custom_weights[item.label])
            for tag in getattr(item, "tags", []):
                if tag in self.config.custom_weights:
                    boost = max(boost, self.config.custom_weights[tag])

            # --- Confidence score ---
            conf_score = getattr(item, "confidence", 1.0)
            stale_penalty = 1.0
            if item_status == "stale":
                stale_penalty = 0.5

            # --- Compute final score + breakdown (C6) ---
            role_component = self.config.role_weight_factor * role_score
            recency_component = self.config.recency_factor * recency
            relevance_component = self.config.relevance_factor * relevance
            confidence_component = self.config.confidence_factor * conf_score

            if strategy == "recency":
                score = recency * stale_penalty
            elif strategy == "role_priority":
                score = role_score * stale_penalty
            elif strategy == "relevance":
                score = relevance * stale_penalty
            else:  # combined
                score = (role_component + recency_component + relevance_component + confidence_component) * stale_penalty

            score += boost

            breakdown = {
                "role": round(role_component, 4),
                "recency": round(recency_component, 4),
                "relevance": round(relevance_component, 4),
                "confidence": round(confidence_component, 4),
            }
            sorted_factors = sorted(breakdown.items(), key=lambda x: x[1], reverse=True)
            top_factors = [f"{k} ({v:.2f})" for k, v in sorted_factors[:2] if v > 0]
            reason = " + ".join(top_factors) if top_factors else "default score"

            scored.append(ScoredItem(
                item=item,
                index=i,
                score=score,
                token_estimate=tokens,
                breakdown=breakdown,
                reason=reason,
            ))

        return scored

    def select_within_budget(
        self,
        items: List[Any],
        max_tokens: Optional[int] = None,
        query: Optional[str] = None,
        relevance_scores: Optional[Dict[int, float]] = None,
    ) -> List[Any]:
        """
        Select items that fit within the token budget, prioritised by score.

        Algorithm:
        1. Reserve system messages if configured.
        2. Score remaining items.
        3. Greedily add highest-scored items until budget is exhausted.
        4. Return items in their original order.

        Args:
            items: Full list of ContextItem objects.
            max_tokens: Override budget (defaults to config.max_tokens).
            query: Optional query for relevance scoring.
            relevance_scores: Optional external relevance scores.

        Returns:
            Subset of items within budget, in original order.
        """
        budget = max_tokens or self.config.max_tokens

        if not items:
            return []

        # --- Step 1: Reserve system messages ---
        system_items: List[Any] = []
        other_items: List[Any] = []
        system_indices: set = set()

        if self.config.reserve_system:
            system_budget = int(budget * self.config.reserve_ratio)
            system_tokens = 0
            for i, item in enumerate(items):
                role_val = item.role.value if hasattr(item.role, "value") else str(item.role)
                if role_val == "system":
                    t = self.estimate_tokens(item.content)
                    if system_tokens + t <= system_budget or not system_items:
                        system_items.append(item)
                        system_indices.add(i)
                        system_tokens += t
                    else:
                        other_items.append(item)
                else:
                    other_items.append(item)
            remaining_budget = budget - system_tokens
        else:
            other_items = list(items)
            remaining_budget = budget

        # --- Step 2: Score remaining items ---
        # Build relevance map with adjusted indices for other_items
        adj_relevance: Dict[int, float] = {}
        if relevance_scores:
            other_idx = 0
            for orig_idx in range(len(items)):
                if orig_idx not in system_indices:
                    if orig_idx in relevance_scores:
                        adj_relevance[other_idx] = relevance_scores[orig_idx]
                    other_idx += 1

        scored = self.score_items(other_items, query=query, relevance_scores=adj_relevance)

        # --- Step 3: Greedy selection by score ---
        scored.sort(key=lambda s: s.score, reverse=True)

        selected_indices: set = set()
        used_tokens = 0
        for si in scored:
            if used_tokens + si.token_estimate <= remaining_budget:
                selected_indices.add(si.index)
                used_tokens += si.token_estimate

        # --- Step 4: Return in original order ---
        selected_other = [other_items[i] for i in sorted(selected_indices)]

        # Merge system + selected, preserving original interleaving
        result: List[Any] = []
        sys_iter = iter(system_items)
        other_iter = iter(selected_other)
        next_sys = next(sys_iter, None)
        next_other = next(other_iter, None)

        for item in items:
            if item is next_sys:
                result.append(item)
                next_sys = next(sys_iter, None)
            elif item is next_other:
                result.append(item)
                next_other = next(other_iter, None)

        return result
