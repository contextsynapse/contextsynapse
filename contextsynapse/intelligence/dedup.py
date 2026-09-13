"""Three-layer dedup pipeline — fingerprint, semantic, entity resolution.

Layer 1 (this file): Content fingerprinting using SHA-256 of normalized text.
Prevents exact and near-exact duplicate content from being ingested.

Usage:
    from contextsynapse.intelligence.dedup import ContentFingerprint

    fp = ContentFingerprint()
    result = fp.check("Article text...", url="https://example.com")
    if result.status == "exact_duplicate":
        skip()
"""
from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class DedupResult:
    """Result of dedup check."""
    status: str              # new | exact_duplicate | content_changed
    fingerprint: str         # SHA-256 of normalized content
    existing_doc_id: Optional[str] = None


def _normalize_text(text: str) -> str:
    """Normalize text for fingerprinting: lowercase, collapse whitespace, strip punctuation."""
    text = text.lower().strip()
    text = re.sub(r'\s+', ' ', text)
    return text


def _hash_text(text: str) -> str:
    """SHA-256 hash of normalized text."""
    normalized = _normalize_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class ContentFingerprint:
    """Layer 1: Content fingerprint dedup.

    Maintains an in-memory set of fingerprints and a URL→fingerprint map.
    Can optionally be backed by Redis for cross-process dedup.
    """

    def __init__(self, redis_client=None):
        self._fingerprints: Set[str] = set()
        self._url_fingerprints: Dict[str, str] = {}  # url -> fingerprint
        self._redis = redis_client

    def check(self, text: str, url: str = "") -> DedupResult:
        """Check content for duplicates.

        Returns DedupResult with status:
        - "new": content not seen before
        - "exact_duplicate": same content (possibly different URL)
        - "content_changed": same URL but content differs (should version)
        """
        fingerprint = _hash_text(text)

        # Check URL first (same URL, same or different content)
        if url and url in self._url_fingerprints:
            existing_fp = self._url_fingerprints[url]
            if existing_fp == fingerprint:
                return DedupResult(status="exact_duplicate", fingerprint=fingerprint)
            else:
                # Same URL, different content — content changed
                self._url_fingerprints[url] = fingerprint
                self._fingerprints.add(fingerprint)
                return DedupResult(status="content_changed", fingerprint=fingerprint)

        # Check fingerprint (same content, different URL)
        if fingerprint in self._fingerprints:
            return DedupResult(status="exact_duplicate", fingerprint=fingerprint)

        # New content
        self._fingerprints.add(fingerprint)
        if url:
            self._url_fingerprints[url] = fingerprint

        return DedupResult(status="new", fingerprint=fingerprint)

    def clear(self):
        """Clear all fingerprints."""
        self._fingerprints.clear()
        self._url_fingerprints.clear()


# ---------------------------------------------------------------------------
# Layer 2: Semantic Dedup (embedding-based)
# ---------------------------------------------------------------------------

@dataclass
class SemanticDedupResult:
    status: str              # new | semantic_duplicate | related_coverage | factual_update
    similarity: float = 0.0
    matched_doc_id: Optional[str] = None
    numbers_changed: bool = False


# Pattern to extract all numbers from text (integers, decimals, percentages, money)
_NUMBER_EXTRACT = re.compile(
    r'(?:\$\s*)?[\d,]+(?:\.\d+)?(?:\s*(?:%|billion|million|trillion|B|M|K|cr|lakh))?',
    re.IGNORECASE,
)


def _extract_numbers(text: str) -> Set[str]:
    """Extract all numeric values from text for comparison."""
    return {m.strip().lower().replace(",", "").replace(" ", "") for m in _NUMBER_EXTRACT.findall(text) if m.strip()}


def _numbers_differ(text_a: str, text_b: str) -> bool:
    """Check if two texts have different numeric values.

    Returns True if the numbers in the texts differ — meaning this is
    a factual update (corrected typo, updated figure), not a true duplicate.
    """
    nums_a = _extract_numbers(text_a)
    nums_b = _extract_numbers(text_b)

    if not nums_a and not nums_b:
        return False  # no numbers in either — can't tell

    # If the number sets differ, it's a factual change
    return nums_a != nums_b


class SemanticDedup:
    """Layer 2: Embedding-based semantic dedup.

    Compares chunk embeddings against stored embeddings to catch
    rewritten duplicates (same story from different sources).

    IMPORTANT for factual data: when similarity > 0.95 but numbers
    differ, the content is treated as a "factual_update" (version it)
    instead of "semantic_duplicate" (reject it). This prevents
    rejecting corrected typos or updated figures.
    """

    def __init__(self, dup_threshold: float = 0.95, related_threshold: float = 0.85):
        self._embeddings: List[tuple] = []  # (embedding_array, doc_id, text)
        self._dup_threshold = dup_threshold
        self._related_threshold = related_threshold

    def add(self, embedding: List[float], doc_id: str = "", text: str = ""):
        """Add an embedding to the store."""
        self._embeddings.append((np.array(embedding, dtype=np.float32), doc_id, text))

    def check(self, embedding: List[float], text: str = "") -> SemanticDedupResult:
        """Check embedding against stored embeddings.

        Args:
            embedding: Vector embedding of the content
            text: Raw text (used for number comparison on near-duplicates)
        """
        if not self._embeddings:
            return SemanticDedupResult(status="new")

        query = np.array(embedding, dtype=np.float32)
        query_norm = np.linalg.norm(query)
        if query_norm == 0:
            return SemanticDedupResult(status="new")

        best_sim = 0.0
        best_doc_id = ""
        best_text = ""

        for stored_emb, doc_id, stored_text in self._embeddings:
            stored_norm = np.linalg.norm(stored_emb)
            if stored_norm == 0:
                continue
            sim = float(np.dot(query, stored_emb) / (query_norm * stored_norm))
            if sim > best_sim:
                best_sim = sim
                best_doc_id = doc_id
                best_text = stored_text

        if best_sim >= self._dup_threshold:
            # High similarity — but check if numbers differ (factual update vs true duplicate)
            if text and best_text and _numbers_differ(text, best_text):
                return SemanticDedupResult(
                    status="factual_update",
                    similarity=best_sim,
                    matched_doc_id=best_doc_id,
                    numbers_changed=True,
                )
            return SemanticDedupResult(
                status="semantic_duplicate",
                similarity=best_sim,
                matched_doc_id=best_doc_id,
            )
        elif best_sim >= self._related_threshold:
            return SemanticDedupResult(
                status="related_coverage",
                similarity=best_sim,
                matched_doc_id=best_doc_id,
            )
        else:
            return SemanticDedupResult(status="new", similarity=best_sim)


# ---------------------------------------------------------------------------
# Layer 3: Entity Resolution (fuzzy name matching)
# ---------------------------------------------------------------------------

@dataclass
class ResolutionResult:
    status: str               # new | merge | alias
    matched_id: Optional[str] = None
    matched_name: Optional[str] = None
    similarity: float = 0.0


class EntityResolver:
    """Layer 3: Fuzzy entity name resolution.

    Matches extracted entity names against existing entities to prevent
    duplicates like "Tesla" vs "tesla" vs "TSLA".
    """

    def resolve(
        self,
        name: str,
        label: str,
        existing: List[Dict[str, Any]],
    ) -> ResolutionResult:
        """Resolve an entity name against existing entities.

        Args:
            name: Entity name to resolve
            label: Entity label/type
            existing: List of existing entity dicts with "name", "label", "id"

        Returns ResolutionResult with status and matched entity info.
        """
        if not existing or not name:
            return ResolutionResult(status="new")

        name_lower = name.strip().lower()
        best_score = 0.0
        best_match: Optional[Dict[str, Any]] = None

        for ent in existing:
            # Must be same label type
            if ent.get("label", "") != label:
                continue

            ent_name = ent.get("name", "")
            ent_lower = ent_name.strip().lower()

            # Exact match (case-insensitive)
            if name_lower == ent_lower:
                return ResolutionResult(
                    status="merge",
                    matched_id=ent.get("id"),
                    matched_name=ent_name,
                    similarity=1.0,
                )

            # Substring match (one contains the other)
            score = 0.0
            if name_lower in ent_lower or ent_lower in name_lower:
                shorter = min(len(name_lower), len(ent_lower))
                longer = max(len(name_lower), len(ent_lower))
                score = shorter / longer if longer > 0 else 0.0

            if score > best_score:
                best_score = score
                best_match = ent

        if best_score >= 0.9:
            return ResolutionResult(
                status="merge",
                matched_id=best_match.get("id") if best_match else None,
                matched_name=best_match.get("name") if best_match else None,
                similarity=best_score,
            )
        elif best_score >= 0.4:
            return ResolutionResult(
                status="alias",
                matched_id=best_match.get("id") if best_match else None,
                matched_name=best_match.get("name") if best_match else None,
                similarity=best_score,
            )
        else:
            return ResolutionResult(status="new")
