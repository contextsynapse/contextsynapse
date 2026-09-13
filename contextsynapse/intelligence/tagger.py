# contextcore/intelligence/tagger.py
"""Intelligence Tagger — universal content enrichment with 5 tag dimensions.

Runs after extraction to tag content with sentiment, geography, domain,
category, and impact. Uses LLM when available, falls back to keyword heuristics.

Tags become properties on extracted entities/facts, and are also returned
as structured IntelligenceTag objects for graph node/edge creation.

Usage:
    from contextsynapse.intelligence.tagger import tag_extractions

    result = tag_extractions(
        extractions=extractions,
        chunk_texts=["Tesla stock plunged..."],
        llm_fn=llm_fn,
        taxonomy={"domains": ["tech", "energy"], "categories": ["earnings"]},
    )
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

from .geo_reference import GeoEntry, GeoLookup
from .heuristics import detect_sentiment, detect_impact, detect_geo_mentions

logger = logging.getLogger(__name__)

# Module-level singleton
_geo_lookup: Optional[GeoLookup] = None


def _get_geo_lookup() -> GeoLookup:
    global _geo_lookup
    if _geo_lookup is None:
        _geo_lookup = GeoLookup()
    return _geo_lookup


@dataclass
class IntelligenceTag:
    """A single intelligence tag on a chunk."""
    dimension: str       # sentiment | geography | domain | category | impact
    value: str           # the tag value (e.g., "positive", "United States", "technology")
    confidence: float    # 0.0-1.0
    evidence: str        # the text that triggered this tag
    chunk_index: int     # which chunk this tag applies to


@dataclass
class TaggingResult:
    """Result of tagging extractions."""
    tags: List[IntelligenceTag] = field(default_factory=list)
    geo_entries: List[GeoEntry] = field(default_factory=list)
    stats: Dict[str, Any] = field(default_factory=dict)


def _build_tagging_prompt(text: str, taxonomy: Optional[Dict] = None) -> str:
    """Build the LLM prompt for intelligence tagging."""
    domain_list = ""
    category_list = ""
    if taxonomy:
        if taxonomy.get("domains"):
            domain_list = f"\nUse these domain values when applicable: {', '.join(taxonomy['domains'])}"
        if taxonomy.get("categories"):
            category_list = f"\nUse these category values when applicable: {', '.join(taxonomy['categories'])}"

    return f"""Analyze the following text and return ONLY a JSON object with these fields:

{{
  "sentiment": "positive" | "negative" | "neutral" | "mixed",
  "sentiment_confidence": 0.0 to 1.0,
  "sentiment_evidence": "the key phrase indicating sentiment",
  "geographies": [
    {{"name": "place name", "level": "continent|region|country|state|city"}}
  ],
  "domains": ["domain1", "domain2"],
  "categories": ["category1"],
  "impact": "high" | "medium" | "low",
  "impact_reason": "why this impact level"
}}
{domain_list}{category_list}

Rules:
- sentiment must be exactly one of: positive, negative, neutral, mixed
- geographies: list ALL places mentioned, with the most specific level
- domains: broad topic areas (technology, energy, healthcare, finance, etc.)
- categories: specific event type (earnings, product_launch, regulation, etc.)
- impact: based on scope and significance of the content

Text:
---
{text[:3000]}
---

Return ONLY valid JSON, no other text."""


def _parse_llm_response(response: str) -> Optional[Dict]:
    """Parse LLM JSON response, stripping code fences if present."""
    try:
        text = response.strip()
        if text.startswith("```"):
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[:-3]
            text = text.strip()
        return json.loads(text)
    except (json.JSONDecodeError, Exception):
        return None


def _tag_chunk_llm(
    text: str,
    chunk_index: int,
    llm_fn: Callable,
    taxonomy: Optional[Dict],
    geo: GeoLookup,
) -> tuple:
    """Tag a single chunk using LLM. Returns (tags, geo_entries) or None on failure."""
    prompt = _build_tagging_prompt(text, taxonomy)
    try:
        response = llm_fn(prompt)
        data = _parse_llm_response(response)
        if not data:
            return None
    except Exception as exc:
        logger.warning("[TAGGER] LLM call failed for chunk %d: %s", chunk_index, exc)
        return None

    tags = []
    geo_entries = []

    # Sentiment
    sentiment = data.get("sentiment", "neutral")
    if sentiment in ("positive", "negative", "neutral", "mixed"):
        tags.append(IntelligenceTag(
            dimension="sentiment",
            value=sentiment,
            confidence=float(data.get("sentiment_confidence", 0.7)),
            evidence=data.get("sentiment_evidence", ""),
            chunk_index=chunk_index,
        ))

    # Geography
    for geo_item in data.get("geographies", []):
        name = geo_item.get("name", "")
        entry = geo.lookup(name)
        if entry and entry not in geo_entries:
            geo_entries.append(entry)
            tags.append(IntelligenceTag(
                dimension="geography",
                value=entry.name,
                confidence=0.9,
                evidence=name,
                chunk_index=chunk_index,
            ))

    # Domains
    for domain in data.get("domains", []):
        if domain:
            tags.append(IntelligenceTag(
                dimension="domain",
                value=domain.lower(),
                confidence=0.8,
                evidence="",
                chunk_index=chunk_index,
            ))

    # Categories
    for cat in data.get("categories", []):
        if cat:
            tags.append(IntelligenceTag(
                dimension="category",
                value=cat.lower(),
                confidence=0.8,
                evidence="",
                chunk_index=chunk_index,
            ))

    # Impact
    impact = data.get("impact", "low")
    if impact in ("high", "medium", "low"):
        tags.append(IntelligenceTag(
            dimension="impact",
            value=impact,
            confidence=0.8,
            evidence=data.get("impact_reason", ""),
            chunk_index=chunk_index,
        ))

    return tags, geo_entries


def _tag_chunk_heuristic(
    text: str,
    chunk_index: int,
    geo: GeoLookup,
) -> tuple:
    """Tag a single chunk using keyword heuristics. Returns (tags, geo_entries)."""
    tags = []
    geo_entries = []

    # Sentiment
    sent = detect_sentiment(text)
    tags.append(IntelligenceTag(
        dimension="sentiment",
        value=sent.sentiment,
        confidence=sent.confidence,
        evidence=sent.evidence,
        chunk_index=chunk_index,
    ))

    # Impact
    imp = detect_impact(text)
    tags.append(IntelligenceTag(
        dimension="impact",
        value=imp.impact,
        confidence=0.6,
        evidence=imp.impact_reason,
        chunk_index=chunk_index,
    ))

    # Geography
    mentions = detect_geo_mentions(text, geo)
    for entry in mentions:
        if entry not in geo_entries:
            geo_entries.append(entry)
            tags.append(IntelligenceTag(
                dimension="geography",
                value=entry.name,
                confidence=0.7,
                evidence=entry.name,
                chunk_index=chunk_index,
            ))

    return tags, geo_entries


def tag_extractions(
    extractions: List,
    chunk_texts: List[str],
    llm_fn: Optional[Callable] = None,
    taxonomy: Optional[Dict] = None,
) -> TaggingResult:
    """Tag extractions with intelligence dimensions.

    Parameters
    ----------
    extractions : List[ChunkExtraction]
        Output from the extraction stage.
    chunk_texts : List[str]
        Raw text of each chunk (parallel to extractions).
    llm_fn : callable, optional
        LLM function for structured tagging. Falls back to heuristics if None.
    taxonomy : dict, optional
        Custom taxonomy with "domains" and "categories" lists.

    Returns
    -------
    TaggingResult with tags, geo_entries, and stats.
    """
    if not extractions or not chunk_texts:
        return TaggingResult(stats={
            "total_chunks": 0,
            "sentiment_count": 0,
            "geo_count": 0,
            "domain_count": 0,
            "category_count": 0,
            "impact_count": 0,
            "method": "none",
        })

    geo = _get_geo_lookup()
    all_tags = []
    all_geo_entries = []

    for i, text in enumerate(chunk_texts):
        chunk_index = i

        # Try LLM first, fall back to heuristics
        if llm_fn:
            result = _tag_chunk_llm(text, chunk_index, llm_fn, taxonomy, geo)
            if result:
                tags, geos = result
                all_tags.extend(tags)
                all_geo_entries.extend(g for g in geos if g not in all_geo_entries)
                continue
            # LLM failed — fall through to heuristics

        # Heuristic fallback
        tags, geos = _tag_chunk_heuristic(text, chunk_index, geo)
        all_tags.extend(tags)
        all_geo_entries.extend(g for g in geos if g not in all_geo_entries)

    # Build stats
    dim_counts = {}
    for tag in all_tags:
        key = f"{tag.dimension}_count"
        dim_counts[key] = dim_counts.get(key, 0) + 1

    method = "llm" if llm_fn else "heuristic"

    stats = {
        "total_chunks": len(chunk_texts),
        "sentiment_count": dim_counts.get("sentiment_count", 0),
        "geo_count": dim_counts.get("geography_count", 0),
        "domain_count": dim_counts.get("domain_count", 0),
        "category_count": dim_counts.get("category_count", 0),
        "impact_count": dim_counts.get("impact_count", 0),
        "method": method,
    }

    logger.info("[TAGGER] Tagged %d chunks (%s): %s", len(chunk_texts), method, stats)

    return TaggingResult(tags=all_tags, geo_entries=all_geo_entries, stats=stats)
