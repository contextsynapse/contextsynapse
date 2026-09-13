"""Content filters for the ingestion pipeline.

4-level filter chain: keyword include, keyword exclude, category, semantic.
Runs after cleaning, before chunking. First reject = skip.
All filters optional — no config = no filtering.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


def _word_boundary_match(text: str, keyword: str) -> bool:
    """Check if keyword appears as a whole word/phrase in text.

    Uses word boundaries (\\b) so "AI" matches "AI" but not "said".
    Phrases like "climate change" match the exact phrase, not individual words.
    """
    pattern = r'\b' + re.escape(keyword) + r'\b'
    return bool(re.search(pattern, text, re.IGNORECASE))


@dataclass
class SemanticRule:
    """A single semantic filter rule."""
    query: str
    direction: str = "include"  # include | exclude
    threshold: float = 0.6


@dataclass
class FilterConfig:
    """Filter configuration for a context or source."""
    keywords_include: List[str] = field(default_factory=list)
    keywords_exclude: List[str] = field(default_factory=list)
    categories_include: List[str] = field(default_factory=list)
    # Legacy single query (backward compat)
    semantic_query: str = ""
    semantic_auto_threshold: float = 0.55
    semantic_review_threshold: float = 0.4
    # Multi semantic rules (new)
    semantic_rules: List[SemanticRule] = field(default_factory=list)
    # Keyword include logic: "or" (any match) | "and" (all must match)
    keywords_include_mode: str = "or"


@dataclass
class FilterResult:
    """Result of running the filter chain on one document."""
    status: str = "passed"     # passed | filtered | review
    reason: str = ""           # "" | "keyword_exclude: horoscope" | "semantic: 0.55"
    scores: Dict[str, Any] = field(default_factory=dict)


def _keyword_include(text: str, keywords: List[str], mode: str = "or") -> Optional[FilterResult]:
    """Filter 1: keyword include check.

    mode="or"  — pass if ANY keyword found (default, backward compatible)
    mode="and" — pass only if ALL keywords found
    """
    if not keywords:
        return None  # no filter configured
    if mode == "and":
        missing = [kw for kw in keywords if not _word_boundary_match(text, kw)]
        if missing:
            return FilterResult(
                status="filtered",
                reason=f"keyword_include(AND): missing {missing[:3]}",
            )
        return None  # all found
    # OR mode (default)
    for kw in keywords:
        if _word_boundary_match(text, kw):
            return None  # passed — at least one match
    return FilterResult(
        status="filtered",
        reason=f"keyword_include: none of {keywords[:3]} found",
    )


def _keyword_exclude(text: str, keywords: List[str]) -> Optional[FilterResult]:
    """Filter 2: must NOT contain any exclude keyword."""
    if not keywords:
        return None
    for kw in keywords:
        if _word_boundary_match(text, kw):
            return FilterResult(
                status="filtered",
                reason=f"keyword_exclude: {kw}",
            )
    return None  # passed


def _category_filter(
    title: str, body: str,
    categories: List[str],
    llm_fn: Optional[Callable] = None,
) -> Optional[FilterResult]:
    """Filter 3: classify article category, must be in allowed list."""
    if not categories or not llm_fn:
        return None

    prompt = (
        f"Classify this article into exactly ONE category from this list: "
        f"{', '.join(categories)}.\n\n"
        f"Title: {title}\n\n"
        f"Content: {body[:500]}\n\n"
        f"Reply with just the category name, nothing else."
    )
    try:
        response = llm_fn(prompt).strip().lower()
        # Find best match from allowed categories
        matched = None
        for cat in categories:
            if cat.lower() in response or response in cat.lower():
                matched = cat
                break

        if matched:
            return FilterResult(
                status="passed" if matched else "filtered",
                scores={"category": matched},
            ) if matched else None
        return FilterResult(
            status="filtered",
            reason=f"category: '{response}' not in {categories}",
            scores={"category": response},
        )
    except Exception as e:
        logger.warning("Category filter failed: %s", e)
        return None  # fail open


def _semantic_filter(
    title: str, body: str,
    query: str,
    auto_threshold: float = 0.7,
    review_threshold: float = 0.4,
    embed_fn: Optional[Callable] = None,
) -> Optional[FilterResult]:
    """Filter 4: semantic similarity to reference query."""
    if not query or not embed_fn:
        return None

    try:
        import numpy as np
        article_text = f"{title}. {body[:500]}"
        article_vec = embed_fn(article_text)
        query_vec = embed_fn(query)

        if article_vec is None or query_vec is None:
            return None

        # Cosine similarity
        a = np.array(article_vec)
        q = np.array(query_vec)
        dot = np.dot(a, q)
        norm = np.linalg.norm(a) * np.linalg.norm(q)
        score = float(dot / norm) if norm > 0 else 0.0

        if score >= auto_threshold:
            return FilterResult(status="passed", scores={"semantic": round(score, 3)})
        elif score >= review_threshold:
            return FilterResult(
                status="review",
                reason=f"semantic: {score:.2f} (between review and auto threshold)",
                scores={"semantic": round(score, 3)},
            )
        else:
            return FilterResult(
                status="filtered",
                reason=f"semantic: {score:.2f} (below {review_threshold})",
                scores={"semantic": round(score, 3)},
            )
    except Exception as e:
        logger.warning("Semantic filter failed: %s", e)
        return None  # fail open


def filter_chain(
    title: str,
    body: str,
    config: FilterConfig,
    llm_fn: Optional[Callable] = None,
    embed_fn: Optional[Callable] = None,
    debug_log: Optional[List] = None,
) -> FilterResult:
    """Run filter chain on a document.

    Logic:
      INCLUDE (keyword OR semantic): pass if ANY include matches (either keyword or semantic)
      EXCLUDE (keyword AND semantic): fail if ANY exclude matches
      Category: fail if not in allowed list

    This means: keyword include "Iran" OR semantic include "Iran war" — either passing = pass.

    If debug_log is provided (list), filter decisions are appended for diagnostics.
    """
    text = f"{title} {body}"

    def _debug(step: str, **kwargs):
        if debug_log is not None:
            debug_log.append({"step": step, **kwargs})

    # ── Step 1: Check all EXCLUDE rules first (hard reject) ──────
    # Keyword exclude: any match = reject
    result = _keyword_exclude(text, config.keywords_exclude)
    if result and result.status == "filtered":
        _debug("keyword_exclude", status="filtered", reason=result.reason)
        return result
    elif config.keywords_exclude:
        _debug("keyword_exclude", status="passed", keywords=config.keywords_exclude)

    # Semantic exclude: if similar to ANY exclude query = reject
    all_rules = list(config.semantic_rules)
    if config.semantic_query and not all_rules:
        all_rules.append(SemanticRule(query=config.semantic_query, direction="include",
                                      threshold=config.semantic_review_threshold))

    if all_rules and embed_fn:
        exclude_rules = [r for r in all_rules if r.direction == "exclude"]
        for rule in exclude_rules:
            result = _semantic_filter(title, body, rule.query,
                                      auto_threshold=rule.threshold,
                                      review_threshold=0.0,
                                      embed_fn=embed_fn)
            if result and result.status == "passed":
                return FilterResult(
                    status="filtered",
                    reason=f"semantic_exclude: similar to '{rule.query}' (score: {result.scores.get('semantic', '?')})",
                    scores=result.scores,
                )

    # Category filter
    result = _category_filter(title, body, config.categories_include, llm_fn)
    if result and result.status == "filtered":
        return result

    # ── Step 2: Check INCLUDE rules (OR logic) ───────────────────
    # Pass if ANY include matches: keyword OR semantic
    has_include_rules = bool(config.keywords_include) or bool([r for r in all_rules if r.direction == "include"])

    if not has_include_rules:
        return FilterResult(status="passed")  # no include rules = pass everything

    # Check keyword include
    keyword_passed = False
    if config.keywords_include:
        kw_result = _keyword_include(text, config.keywords_include, mode=config.keywords_include_mode)
        if kw_result is None:  # None means passed
            keyword_passed = True
        matched = [kw for kw in config.keywords_include if _word_boundary_match(text, kw)]
        _debug("keyword_include", mode=config.keywords_include_mode,
               matched=matched, missed=[k for k in config.keywords_include if k not in matched],
               passed=keyword_passed)

    # Check semantic include
    semantic_passed = False
    best_semantic_score = 0.0
    best_semantic_result = None
    if all_rules and embed_fn:
        include_rules = [r for r in all_rules if r.direction == "include"]
        for rule in include_rules:
            result = _semantic_filter(title, body, rule.query,
                                      auto_threshold=rule.threshold,
                                      review_threshold=config.semantic_review_threshold,
                                      embed_fn=embed_fn)
            if result:
                score = result.scores.get("semantic", 0)
                _debug("semantic_include", query=rule.query[:50],
                       score=score, threshold=rule.threshold,
                       status=result.status)
                if score > best_semantic_score:
                    best_semantic_score = score
                    best_semantic_result = result
                if result.status == "passed":
                    semantic_passed = True

    # OR logic: pass if EITHER keyword or semantic matched
    if keyword_passed or semantic_passed:
        return FilterResult(
            status="passed",
            scores={"semantic": best_semantic_score} if best_semantic_score > 0 else {},
        )

    # Check if semantic was close (review zone)
    if best_semantic_result and best_semantic_result.status == "review":
        return best_semantic_result

    # Nothing matched
    reason_parts = []
    if config.keywords_include:
        reason_parts.append(f"keywords {config.keywords_include[:3]} not found")
    if all_rules:
        include_queries = [r.query[:30] for r in all_rules if r.direction == "include"]
        if include_queries:
            reason_parts.append(f"semantic score {best_semantic_score:.2f} below threshold")
    return FilterResult(
        status="filtered",
        reason=f"no include match: {'; '.join(reason_parts)}",
        scores={"semantic": best_semantic_score} if best_semantic_score > 0 else {},
    )


def structural_pre_filter(
    text: str,
    config: FilterConfig,
    schema=None,
) -> Optional[FilterResult]:
    """Cheap structural pre-filter for CSV/code/JSON -- runs before LLM.

    Detects content type from text shape, extracts structural tokens
    (column names, imports, JSON keys), and checks against keyword filters
    and schema node/edge type names. Zero cost -- no LLM, no embeddings.

    Returns FilterResult(filtered) on clear mismatch, None on pass/uncertain.
    """
    if not config.keywords_include and not config.keywords_exclude and not schema:
        return None  # nothing to check

    # Detect content type and extract structural tokens
    lines = text[:2000].split('\n')
    first_line = lines[0].strip() if lines else ""
    tokens = set()

    # CSV: extract column names from first line
    if ',' in first_line and len(first_line.split(',')) >= 3:
        for col in first_line.split(','):
            col = col.strip().strip('"').strip("'").lower()
            if col and len(col) > 1:
                tokens.add(col)

    # JSON: extract top-level keys
    if first_line.startswith('{') or first_line.startswith('['):
        import re
        keys = re.findall(r'"([a-zA-Z_][a-zA-Z0-9_]*)"\s*:', text[:2000])
        tokens.update(k.lower() for k in keys)

    # Code: extract import names and function/class names
    for line in lines[:50]:
        stripped = line.strip()
        if stripped.startswith('import ') or stripped.startswith('from '):
            parts = stripped.replace('import ', ' ').replace('from ', ' ').split()
            tokens.update(p.lower() for p in parts if len(p) > 2 and p.isidentifier())
        if stripped.startswith('def ') or stripped.startswith('class '):
            name = stripped.split('(')[0].split(':')[0].split()[-1] if '(' in stripped or ':' in stripped else ''
            if name and len(name) > 2:
                tokens.add(name.lower())

    if not tokens:
        return None  # not structural content, skip pre-filter

    token_text = ' '.join(tokens)

    # Check keyword exclude against structural tokens
    if config.keywords_exclude:
        for kw in config.keywords_exclude:
            if kw.lower() in token_text:
                return FilterResult(
                    status="filtered",
                    reason=f"structural_pre_filter: excluded keyword '{kw}' found in structure",
                )

    # Check keyword include against structural tokens + schema types
    if config.keywords_include:
        combined_tokens = tokens.copy()
        # Add schema node/edge type names as implicit include signals
        if schema:
            if hasattr(schema, 'node_types') and schema.node_types:
                combined_tokens.update(t.lower() for t in schema.node_types.keys())
            if hasattr(schema, 'edge_types') and schema.edge_types:
                combined_tokens.update(t.lower().replace('_', ' ') for t in schema.edge_types.keys())
        combined_text = ' '.join(combined_tokens)

        mode = config.keywords_include_mode
        if mode == "and":
            missing = [kw for kw in config.keywords_include if kw.lower() not in combined_text]
            if len(missing) == len(config.keywords_include):
                return FilterResult(
                    status="filtered",
                    reason=f"structural_pre_filter(AND): none of {config.keywords_include[:3]} found in structure",
                )
        else:
            found = any(kw.lower() in combined_text for kw in config.keywords_include)
            if not found:
                # Don't hard-reject in OR mode -- raw text filter_chain will check body text too
                return None  # uncertain, let filter_chain decide

    return None  # passed or uncertain


def post_extraction_filter(
    extractions: list,
    config: FilterConfig,
    schema=None,
    embed_fn=None,
) -> Optional[FilterResult]:
    """Post-extraction semantic filter -- runs after LLM extraction, before graph building.

    Uses extracted entity names and fact statements for precise semantic matching.
    Optionally checks schema conformance (% of entities matching schema types).
    """
    if not config.semantic_rules and not schema:
        return None

    # Build structured text from extractions
    parts = []
    schema_matches = 0
    total_entities = 0
    for ext in extractions:
        for ent in getattr(ext, 'entities', []):
            name = ent.get('name', '') if isinstance(ent, dict) else getattr(ent, 'name', '')
            label = ent.get('label', '') if isinstance(ent, dict) else getattr(ent, 'label', '')
            if not name and isinstance(ent, dict):
                name = ent.get('properties', {}).get('name', '')
            if name:
                parts.append(f"{label}: {name}")
                total_entities += 1
                if schema and hasattr(schema, 'node_types') and label in (schema.node_types or {}):
                    schema_matches += 1
        for fact in getattr(ext, 'facts', []):
            stmt = fact.get('statement', '') if isinstance(fact, dict) else getattr(fact, 'statement', '')
            if not stmt and isinstance(fact, dict):
                stmt = fact.get('properties', {}).get('statement', '')
            if stmt:
                parts.append(stmt)

    structured_text = ' '.join(parts)

    # Schema conformance check
    if schema and total_entities > 0:
        conformance = schema_matches / total_entities
        if conformance < 0.1 and total_entities >= 3:
            return FilterResult(
                status="review",
                reason=f"schema_conformance: only {schema_matches}/{total_entities} entities match schema types ({conformance:.0%})",
                scores={"schema_conformance": round(conformance, 2)},
            )

    # Semantic rules against structured text
    if config.semantic_rules and embed_fn and structured_text:
        for rule in config.semantic_rules:
            if rule.direction == "include":
                result = _semantic_filter("", structured_text, rule.query,
                                          auto_threshold=rule.threshold,
                                          review_threshold=config.semantic_review_threshold,
                                          embed_fn=embed_fn)
                if result and result.status == "filtered":
                    return FilterResult(
                        status="filtered",
                        reason=f"post_extraction: entities don't match '{rule.query}' (score: {result.scores.get('semantic', '?')})",
                        scores=result.scores,
                    )

    return None  # passed


