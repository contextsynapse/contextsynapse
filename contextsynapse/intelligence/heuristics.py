# contextcore/intelligence/heuristics.py
"""Keyword-based intelligence heuristics — LLM fallback.

Provides sentiment, impact, and geography detection using pattern matching.
Used when no LLM is available or as a fast pre-filter before LLM calls.

Usage:
    from contextsynapse.intelligence.heuristics import detect_sentiment, detect_impact, detect_geo_mentions

    sent = detect_sentiment("Revenue surged 25%")  # SentimentResult(sentiment="positive", ...)
    imp = detect_impact("CEO fired after fraud")    # ImpactResult(impact="high", ...)
    geos = detect_geo_mentions(text, geo_lookup)    # [GeoEntry, ...]
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional

from .geo_reference import GeoEntry, GeoLookup


@dataclass
class SentimentResult:
    sentiment: str       # positive | negative | neutral | mixed
    confidence: float    # 0.0-1.0
    evidence: str        # the key phrase(s) that triggered


@dataclass
class ImpactResult:
    impact: str          # high | medium | low
    impact_reason: str


# ── Sentiment patterns ──

_POSITIVE_PATTERNS = re.compile(
    r'\b(?:surged?|gained?|grew|growth|rose|soared?|rallied|beat(?:ing|s)?\s+expectations?|'
    r'upgrade[ds]?|bullish|strong(?:er)?|record\s+high|outperform|boom(?:ing)?|'
    r'profit(?:able|ability)?|expanded?|breakthrough|approved?|success|optimis[mt])'
    r'\b', re.IGNORECASE
)

_NEGATIVE_PATTERNS = re.compile(
    r'\b(?:fell|dropped?|declined?|plunged?|crashed?|miss(?:ed|ing)?|'
    r'downgrade[ds]?|bearish|weak(?:er|ened)?|warning|layoff[s]?|cut(?:s|ting)?|'
    r'loss(?:es)?|bankruptcy|fraud|scandal|investigation|lawsuit|recalled?|'
    r'failed?|slump|recession|default(?:ed)?|pessimis[mt]|shutdown|crisis)'
    r'\b', re.IGNORECASE
)

# ── Impact patterns ──

_HIGH_IMPACT_PATTERNS = re.compile(
    r'\b(?:CEO|president|chairman|fired|resign|bankrupt|fraud|halt|crash|'
    r'billion|trillion|emergency|war|sanction|default|crisis|recession|pandemic|'
    r'acquisition|merger|IPO|indictment|arrest|collapse)\b', re.IGNORECASE
)

_FINANCIAL_NUMBERS = re.compile(
    r'(?:\$[\d,.]+\s*(?:billion|million|trillion)|[\d,.]+%|\d{1,3}(?:,\d{3})+)'
)

_NAMED_ENTITIES = re.compile(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)+\b')

# Short ISO codes that are also common English words — skip these in geo detection
_GEO_SKIP_ALIASES = {"it", "in", "no", "is", "at", "be", "am", "me", "us", "or", "an",
                      "as", "to", "do", "go", "so", "my", "by", "if", "of", "on",
                      "co", "ca", "ma", "pa", "oh", "ga"}


def detect_sentiment(text: str) -> SentimentResult:
    """Detect sentiment using keyword patterns.

    Returns SentimentResult with polarity, confidence, and evidence.
    """
    if not text.strip():
        return SentimentResult(sentiment="neutral", confidence=0.0, evidence="")

    pos_matches = _POSITIVE_PATTERNS.findall(text)
    neg_matches = _NEGATIVE_PATTERNS.findall(text)

    pos_count = len(pos_matches)
    neg_count = len(neg_matches)
    total = pos_count + neg_count

    if total == 0:
        return SentimentResult(sentiment="neutral", confidence=0.3, evidence="")

    if pos_count > 0 and neg_count > 0:
        ratio = pos_count / total
        if 0.3 < ratio < 0.7:
            evidence = f"positive: {', '.join(pos_matches[:2])}; negative: {', '.join(neg_matches[:2])}"
            return SentimentResult(sentiment="mixed", confidence=0.6, evidence=evidence)

    if pos_count > neg_count:
        confidence = min(0.9, 0.5 + pos_count * 0.1)
        return SentimentResult(
            sentiment="positive",
            confidence=confidence,
            evidence=", ".join(pos_matches[:3]),
        )

    if neg_count > pos_count:
        confidence = min(0.9, 0.5 + neg_count * 0.1)
        return SentimentResult(
            sentiment="negative",
            confidence=confidence,
            evidence=", ".join(neg_matches[:3]),
        )

    return SentimentResult(sentiment="neutral", confidence=0.3, evidence="")


def detect_impact(text: str) -> ImpactResult:
    """Detect impact level using keyword patterns and content signals."""
    if not text.strip():
        return ImpactResult(impact="low", impact_reason="empty content")

    high_matches = _HIGH_IMPACT_PATTERNS.findall(text)
    has_numbers = bool(_FINANCIAL_NUMBERS.search(text))
    named_count = len(_NAMED_ENTITIES.findall(text))

    score = 0
    reasons = []

    if high_matches:
        score += len(high_matches) * 2
        reasons.append(f"high-impact terms: {', '.join(high_matches[:3])}")

    if has_numbers:
        score += 1
        reasons.append("contains financial figures")

    if named_count >= 3:
        score += 1
        reasons.append(f"{named_count} named entities")

    if score >= 4:
        return ImpactResult(impact="high", impact_reason="; ".join(reasons))
    elif score >= 1:
        return ImpactResult(impact="medium", impact_reason="; ".join(reasons))
    else:
        return ImpactResult(impact="low", impact_reason="routine content")


def detect_geo_mentions(text: str, geo: GeoLookup) -> List[GeoEntry]:
    """Detect geographic mentions in text by matching against GeoLookup.

    Uses word-boundary matching to avoid false positives. Skips short
    aliases that are common English words (IT, IN, NO, etc.).
    """
    if not text.strip():
        return []

    found: List[GeoEntry] = []
    seen_names: set = set()

    # Check all geo entries against the text
    for level in ["city", "state", "country", "region", "continent"]:
        for entry in geo.get_by_level(level):
            if entry.name.lower() in seen_names:
                continue

            # Skip very short names (< 3 chars) that could be false positives
            if len(entry.name) < 3:
                continue

            # Word-boundary match
            pattern = r'\b' + re.escape(entry.name) + r'\b'
            if re.search(pattern, text, re.IGNORECASE):
                found.append(entry)
                seen_names.add(entry.name.lower())

    return found
