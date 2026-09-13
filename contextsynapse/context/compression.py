"""
Compression Tiers
=================
Graceful content degradation when context exceeds budget.

Instead of simply dropping low-priority items, compression tiers degrade
content quality in stages:

- **Tier 1 (FULL)**: Complete content, no changes.
- **Tier 2 (SUMMARY)**: Auto-generated or extractive summary (cached).
- **Tier 3 (METADATA)**: One-line descriptor with label + token count + confidence.
- **Tier 4 (DROPPED)**: Excluded entirely from export.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from .hub import ContextItem


class CompressionTier:
    """Compression tier constants."""
    FULL = 1
    SUMMARY = 2
    METADATA = 3
    DROPPED = 4


@dataclass
class CompressionConfig:
    """Configuration for compression tier thresholds.

    Thresholds determine which tier an item falls into based on its score:
    - score >= tier1_threshold → FULL
    - score >= tier2_threshold → SUMMARY
    - score >= tier3_threshold → METADATA
    - score < tier3_threshold  → DROPPED

    When ``use_percentile=True`` (default), thresholds are interpreted as
    percentile positions in the score distribution rather than absolute values.
    """
    tier1_threshold: float = 0.7
    tier2_threshold: float = 0.4
    tier3_threshold: float = 0.1
    use_percentile: bool = True
    summarizer: Optional[Callable[[str], str]] = None
    max_summary_tokens: int = 100
    extractive_chars: int = 300

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tier1_threshold": self.tier1_threshold,
            "tier2_threshold": self.tier2_threshold,
            "tier3_threshold": self.tier3_threshold,
            "use_percentile": self.use_percentile,
            "max_summary_tokens": self.max_summary_tokens,
            "extractive_chars": self.extractive_chars,
        }


def assign_compression_tiers(
    scored_items: List[Any],
    config: CompressionConfig,
) -> Dict[int, int]:
    """Assign a compression tier to each scored item.

    Args:
        scored_items: List of ScoredItem (from ContextScoper.score_items).
        config: Compression configuration with thresholds.

    Returns:
        Dict mapping scored item index → CompressionTier value.
    """
    if not scored_items:
        return {}

    scores = [si.score for si in scored_items]

    if config.use_percentile:
        sorted_scores = sorted(scores)
        n = len(sorted_scores)
        t1 = sorted_scores[min(int(n * config.tier1_threshold), n - 1)]
        t2 = sorted_scores[min(int(n * config.tier2_threshold), n - 1)]
        t3 = sorted_scores[min(int(n * config.tier3_threshold), n - 1)]
    else:
        t1, t2, t3 = config.tier1_threshold, config.tier2_threshold, config.tier3_threshold

    result: Dict[int, int] = {}
    for si in scored_items:
        if si.score >= t1:
            result[si.index] = CompressionTier.FULL
        elif si.score >= t2:
            result[si.index] = CompressionTier.SUMMARY
        elif si.score >= t3:
            result[si.index] = CompressionTier.METADATA
        else:
            result[si.index] = CompressionTier.DROPPED
    return result


def generate_summary(content: str, config: CompressionConfig) -> str:
    """Generate a summary using the configured summarizer or extractive fallback."""
    if config.summarizer is not None:
        try:
            return config.summarizer(content)
        except Exception:
            pass
    # Extractive fallback: first N chars, break at word boundary
    if len(content) <= config.extractive_chars:
        return content
    truncated = content[:config.extractive_chars]
    # Try to break at last space for cleaner output
    last_space = truncated.rfind(" ")
    if last_space > config.extractive_chars // 2:
        truncated = truncated[:last_space]
    return truncated + "..."


def compress_item(item: Any, tier: int, config: CompressionConfig) -> str:
    """Return the content string for an item at the given compression tier.

    Args:
        item: A ContextItem.
        tier: CompressionTier value.
        config: Compression configuration.

    Returns:
        The rendered content for this tier.
    """
    if tier == CompressionTier.FULL:
        return item.content
    elif tier == CompressionTier.SUMMARY:
        cached = item.metadata.get("_summary")
        if cached:
            return cached
        summary = generate_summary(item.content, config)
        item.metadata["_summary"] = summary
        return summary
    elif tier == CompressionTier.METADATA:
        label = item.label or getattr(item.role, "value", "item")
        token_est = max(1, int(len(item.content) / 4.0))
        conf = getattr(item, "confidence", 1.0)
        return f"[{label}] ~{token_est} tokens, confidence: {conf:.2f}"
    return ""  # DROPPED
