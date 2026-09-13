"""Quality Amplifier — post-extraction relevance scoring.

Runs after extraction, before graph building. Scores every entity/fact/edge
for relevance against the context's purpose using embedding similarity.
Low-relevance items get demoted (confidence reduced) not removed.

Usage:
    from contextsynapse.ingestion.amplifier import amplify_extractions

    result = amplify_extractions(
        extractions=extractions,           # List[ChunkExtraction]
        context_purpose="Tesla news",      # what this context is about
        embed_fn=embed_fn,                 # Callable[[str], List[float]]
    )
    # result.extractions has scored items
    # result.stats has summary metrics
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

# Noise patterns — common web/UI cruft that should never be entities
_NOISE_PATTERNS = [
    re.compile(r"^(Subscribe|Click|Share|Download|Sign Up|Log In|Read More|View All)", re.IGNORECASE),
    re.compile(r"^(Latest|Breaking|Trending|Top|Popular|Most Read)\s+(News|Stories|Videos|Articles)", re.IGNORECASE),
    re.compile(r"^(Edition|Results|Match|Live|Update)\b", re.IGNORECASE),
    re.compile(r"^(Cookie|Privacy|Terms|Disclaimer|Copyright)", re.IGNORECASE),
    re.compile(r"^(Next|Previous|Back|Home|Menu|Navigation|Sidebar|Footer|Header)", re.IGNORECASE),
    re.compile(r"^(Advertisement|Sponsored|Promoted|Ad)\b", re.IGNORECASE),
]

# Generic single-word names that are too vague to be useful entities
_GENERIC_WORDS = {
    "analysis", "processing", "management", "security", "system", "service",
    "data", "information", "technology", "development", "solution", "platform",
    "project", "report", "update", "review", "summary", "overview", "results",
    "it", "the", "this", "that", "here", "there",
}

_MIN_MEANINGFUL_LENGTH = 3  # Names shorter than this are too generic


def _detect_noise(name: str) -> List[str]:
    """Detect noise patterns in an entity/fact name. Returns list of flags."""
    flags = []
    if not name:
        flags.append("empty_name")
        return flags

    # Check noise patterns
    for pattern in _NOISE_PATTERNS:
        if pattern.search(name):
            flags.append("web_cruft")
            break

    # Check generic single words
    if name.lower().strip() in _GENERIC_WORDS:
        flags.append("generic_word")

    # Too short
    if len(name.strip()) < _MIN_MEANINGFUL_LENGTH:
        flags.append("too_short")

    return flags


@dataclass
class AmplifierResult:
    """Result of running the quality amplifier on extractions."""
    extractions: List[Any] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    """Cosine similarity between two vectors. Returns 0.0 on error."""
    try:
        va, vb = np.array(a), np.array(b)
        denom = np.linalg.norm(va) * np.linalg.norm(vb)
        if denom == 0:
            return 0.0
        return float(np.dot(va, vb) / denom)
    except Exception:
        return 0.0


def _get_text_for_item(item: Dict[str, Any]) -> str:
    """Extract meaningful text from an entity or fact dict for embedding."""
    props = item.get("properties", {})
    parts = []
    name = props.get("name", "")
    if name:
        parts.append(name)
    desc = props.get("description", "")
    if desc:
        parts.append(desc)
    stmt = props.get("statement", "")
    if stmt:
        parts.append(stmt)
    evidence = props.get("evidence", "")
    if evidence:
        parts.append(evidence)
    return " ".join(parts) if parts else item.get("label", "")


def _score_items(
    items: List[Dict[str, Any]],
    purpose_embedding: List[float],
    embed_fn: Callable[[str], List[float]],
    demotion_threshold: float = 0.3,
    boost_threshold: float = 0.6,
) -> int:
    """Score items in-place, return count of demoted items.

    Scoring rules:
    - relevance >= boost_threshold: keep original confidence (or boost slightly)
    - relevance < demotion_threshold: multiply confidence by 0.3
    - between: scale confidence linearly
    """
    demoted = 0
    for item in items:
        props = item.setdefault("properties", {})
        name = props.get("name", "")

        # Phase 1: Noise pattern detection
        noise_flags = _detect_noise(name)
        if noise_flags:
            props["_noise_flags"] = noise_flags
            props["confidence"] = round(props.get("confidence", 0.5) * 0.2, 3)
            props["_amplifier_relevance"] = 0.05
            demoted += 1
            continue

        # Phase 2: Embedding-based relevance scoring
        text = _get_text_for_item(item)
        if not text.strip():
            props["_amplifier_relevance"] = 0.0
            props["confidence"] = props.get("confidence", 0.5) * 0.2
            demoted += 1
            continue

        try:
            item_embedding = embed_fn(text)
            relevance = _cosine_similarity(item_embedding, purpose_embedding)
        except Exception:
            relevance = 0.5  # neutral on embed failure

        # Clamp to [0, 1]
        relevance = max(0.0, min(1.0, relevance))
        item.setdefault("properties", {})["_amplifier_relevance"] = round(relevance, 3)

        original_confidence = item["properties"].get("confidence", 0.5)

        if relevance >= boost_threshold:
            # High relevance: keep or slightly boost
            item["properties"]["confidence"] = min(1.0, original_confidence * 1.05)
        elif relevance < demotion_threshold:
            # Low relevance: demote hard
            item["properties"]["confidence"] = round(original_confidence * 0.3, 3)
            demoted += 1
        else:
            # Middle band: linear scale between 0.3x and 1.0x
            scale = 0.3 + 0.7 * ((relevance - demotion_threshold) / (boost_threshold - demotion_threshold))
            item["properties"]["confidence"] = round(original_confidence * scale, 3)
            if scale < 0.7:
                demoted += 1

    return demoted


def build_context_purpose(
    context_name: str = "",
    context_description: str = "",
    tags: Optional[List[str]] = None,
) -> str:
    """Build a purpose string from context metadata for relevance scoring.

    Combines name, description, and tags into a single string that captures
    what this context is about.
    """
    parts = []
    if context_description:
        parts.append(context_description)
    if context_name and context_name.lower() not in ("default", "research", "general", "untitled"):
        parts.append(context_name)
    if tags:
        parts.append("Topics: " + ", ".join(tags))
    return " — ".join(parts) if parts else ""


def amplify_extractions(
    extractions: List,
    context_purpose: str = "",
    embed_fn: Optional[Callable[[str], List[float]]] = None,
    demotion_threshold: float = 0.3,
    boost_threshold: float = 0.6,
) -> AmplifierResult:
    """Score extractions for relevance against context purpose.

    Parameters
    ----------
    extractions : List[ChunkExtraction]
        Output from the extraction stage.
    context_purpose : str
        What this context is about (e.g., "Tesla financial news").
        If empty, amplifier is a no-op.
    embed_fn : callable, optional
        Function that takes a string and returns an embedding vector.
        If None, amplifier is a no-op pass-through.
    demotion_threshold : float
        Below this cosine similarity, items get heavily demoted (default 0.3).
    boost_threshold : float
        Above this cosine similarity, items keep/gain confidence (default 0.6).

    Returns
    -------
    AmplifierResult
        Scored extractions (in-place) and summary stats.
    """
    total_entities = sum(len(e.entities) for e in extractions)
    total_facts = sum(len(e.facts) for e in extractions)

    if not embed_fn or not context_purpose or not extractions:
        return AmplifierResult(
            extractions=extractions,
            stats={
                "total_entities": total_entities,
                "total_facts": total_facts,
                "demoted_count": 0,
                "avg_relevance": 0.0,
                "amplifier_ran": False,
            },
        )

    # Embed the context purpose once
    try:
        purpose_embedding = embed_fn(context_purpose)
    except Exception as exc:
        logger.warning("Amplifier: failed to embed context purpose: %s", exc)
        return AmplifierResult(
            extractions=extractions,
            stats={
                "total_entities": total_entities,
                "total_facts": total_facts,
                "demoted_count": 0,
                "avg_relevance": 0.0,
                "amplifier_ran": False,
            },
        )

    demoted_count = 0
    all_relevances = []

    for ext in extractions:
        demoted_count += _score_items(ext.entities, purpose_embedding, embed_fn,
                                       demotion_threshold, boost_threshold)
        demoted_count += _score_items(ext.facts, purpose_embedding, embed_fn,
                                       demotion_threshold, boost_threshold)

        # Collect relevance values for stats
        for item in ext.entities + ext.facts:
            rel = item.get("properties", {}).get("_amplifier_relevance")
            if rel is not None:
                all_relevances.append(rel)

    avg_relevance = round(sum(all_relevances) / len(all_relevances), 3) if all_relevances else 0.0

    logger.info(
        "[AMPLIFIER] Scored %d entities + %d facts (avg_relevance=%.2f, demoted=%d)",
        total_entities, total_facts, avg_relevance, demoted_count,
    )

    return AmplifierResult(
        extractions=extractions,
        stats={
            "total_entities": total_entities,
            "total_facts": total_facts,
            "demoted_count": demoted_count,
            "avg_relevance": avg_relevance,
            "amplifier_ran": True,
        },
    )
