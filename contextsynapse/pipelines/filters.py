"""Pipeline content filters — junk removal, dedup, keyword, semantic."""
from __future__ import annotations

import hashlib
import logging
import re
from typing import List, Optional, Set

logger = logging.getLogger(__name__)

_JUNK_PATTERNS = [
    re.compile(r"^(Home|About|Contact|Subscribe|Sign [Uu]p|Log [Ii]n)(\s*\|)", re.IGNORECASE),
    re.compile(r"(Follow us|Share this|Read more|Click here|View all)", re.IGNORECASE),
    re.compile(r"(Copyright|All [Rr]ights [Rr]eserved|Terms of [Ss]ervice|Privacy [Pp]olicy)", re.IGNORECASE),
    re.compile(r"(Cookie|Accept cookies|Manage preferences)", re.IGNORECASE),
    re.compile(r"^\s*(Share|Tweet|Pin|Email|Print)\s*$", re.IGNORECASE),
    re.compile(r"(Advertisement|Sponsored|Promoted)", re.IGNORECASE),
]


def remove_junk_lines(text: str) -> str:
    """Remove navigation, boilerplate, and junk lines from text."""
    lines = text.split("\n")
    cleaned = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        if len(stripped) < 10:
            continue
        if any(p.search(stripped) for p in _JUNK_PATTERNS):
            continue
        if stripped.count("|") >= 3 and len(stripped) < 200:
            continue
        cleaned.append(line)
    return "\n".join(cleaned)


def passes_minimum_content(text: str, min_chars: int = 100) -> bool:
    """Check if text has enough content after cleaning."""
    clean = remove_junk_lines(text)
    return len(clean.strip()) >= min_chars


def keyword_filter(text: str, include: List[str], exclude: List[str]) -> bool:
    """Check text against keyword include/exclude lists.
    Returns True if text passes. Exclude checked first (hard reject).
    If include is non-empty, must match at least one.
    """
    lower = text.lower()
    for kw in exclude:
        if kw.lower() in lower:
            return False
    if include:
        return any(kw.lower() in lower for kw in include)
    return True


def semantic_filter(text: str, description_vector: List[float],
                    threshold: float = 0.6) -> bool:
    """Check semantic similarity against filter description vector."""
    try:
        from ..models.embedding_service import get_embedding_service
        svc = get_embedding_service()
        text_vector = svc.embed_text(text[:2000])
        if not text_vector or not description_vector:
            return True
        dot = sum(a * b for a, b in zip(text_vector, description_vector))
        norm_a = sum(a * a for a in text_vector) ** 0.5
        norm_b = sum(b * b for b in description_vector) ** 0.5
        if norm_a == 0 or norm_b == 0:
            return True
        return (dot / (norm_a * norm_b)) >= threshold
    except Exception:
        return True


def content_filter(
    text: str,
    keyword_exclude: List[str] = None,
    keyword_include: List[str] = None,
    semantic_rules: List[dict] = None,
    semantic_mode: str = "or",
) -> dict:
    """Unified content filter: exclude blocks, include (keyword OR semantic) permits.

    Args:
        text: Content to filter
        keyword_exclude: Hard reject keywords
        keyword_include: Soft include keywords (OR with semantic)
        semantic_rules: List of {"description": "...", "threshold": 0.6}
        semantic_mode: "or" (any rule matches) or "and" (all must match)

    Returns:
        {"passed": bool, "reason": str, "scores": {}}
    """
    keyword_exclude = keyword_exclude or []
    keyword_include = keyword_include or []
    semantic_rules = semantic_rules or []

    text_lower = text.lower()

    # Step 1: Keyword EXCLUDE — hard reject
    for kw in keyword_exclude:
        if kw.lower() in text_lower:
            return {"passed": False, "reason": f"excluded: {kw}", "scores": {}}

    # Step 2: Keyword INCLUDE — soft pass (OR with semantic)
    if keyword_include:
        for kw in keyword_include:
            if kw.lower() in text_lower:
                return {"passed": True, "reason": f"keyword: {kw}", "scores": {}}

    # Step 3: Semantic rules
    if semantic_rules:
        try:
            from ..models.embedding_service import get_embedding_service
            svc = get_embedding_service()
            text_vec = svc.embed_text(text[:2000])

            def _cosine(a: List[float], b: List[float]) -> float:
                dot = sum(x * y for x, y in zip(a, b))
                norm_a = sum(x * x for x in a) ** 0.5
                norm_b = sum(x * x for x in b) ** 0.5
                if norm_a == 0 or norm_b == 0:
                    return 0.0
                return dot / (norm_a * norm_b)

            scores: dict = {}
            rule_results: List[bool] = []
            best_passed_reason = ""
            for rule in semantic_rules:
                desc = rule.get("description", "")
                threshold = float(rule.get("threshold", 0.6))
                rule_vec = svc.embed_text(desc)
                if text_vec and rule_vec:
                    score = _cosine(text_vec, rule_vec)
                    scores[desc[:40]] = round(score, 3)
                    passed = score >= threshold
                    rule_results.append(passed)
                    if passed and not best_passed_reason:
                        best_passed_reason = f"semantic: {desc}"
                else:
                    rule_results.append(False)

            if semantic_mode == "and":
                if rule_results and all(rule_results):
                    return {"passed": True, "reason": "semantic: all rules matched", "scores": scores}
                else:
                    return {"passed": False, "reason": "semantic: not all rules matched", "scores": scores}
            else:  # "or"
                if any(rule_results):
                    return {"passed": True, "reason": best_passed_reason or "semantic: matched", "scores": scores}
                else:
                    return {"passed": False, "reason": "semantic: no rules matched", "scores": scores}
        except Exception:
            pass  # fail open, continue to no-match logic

    # Step 4: No include filters defined at all → pass everything
    if not keyword_include and not semantic_rules:
        return {"passed": True, "reason": "no filters", "scores": {}}

    # Step 5: keyword_include was provided but nothing matched, no semantic rules
    return {"passed": False, "reason": "no match", "scores": {}}


class SemanticDedup:
    """Track seen content by hash to detect duplicates."""

    def __init__(self, redis_client=None, pipeline_id: str = ""):
        self._redis = redis_client
        self._pipeline_id = pipeline_id
        self._local_hashes: Set[str] = set()

    def _hash(self, text: str) -> str:
        return hashlib.sha256(text.strip().lower()[:2000].encode()).hexdigest()[:24]

    def is_duplicate(self, text: str) -> bool:
        h = self._hash(text)
        if self._redis and self._pipeline_id:
            key = f"pipeline:{self._pipeline_id}:seen_hashes"
            if self._redis.sismember(key, h):
                return True
            self._redis.sadd(key, h)
            return False
        if h in self._local_hashes:
            return True
        self._local_hashes.add(h)
        return False
