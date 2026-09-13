"""Duplicate detection for ingestion pipeline.

Provides exact and fuzzy matching against existing graph nodes
to prevent duplicate data from being ingested.
"""

from typing import Any, Dict, List, Optional, Set


def _keywords(text: str) -> Set[str]:
    """Extract lowercase keyword tokens from text, stripping punctuation."""
    if not text:
        return set()
    # Split on whitespace, strip non-alpha chars, lowercase, drop empties
    return {
        w.strip(".,;:!?()[]{}\"'").lower()
        for w in text.split()
        if w.strip(".,;:!?()[]{}\"'")
    }


def _keyword_overlap(a: str, b: str) -> float:
    """Jaccard similarity between keyword sets of two strings."""
    ka = _keywords(a)
    kb = _keywords(b)
    if not ka or not kb:
        return 0.0
    intersection = ka & kb
    union = ka | kb
    return len(intersection) / len(union)


def dedup_check(
    node: Dict[str, Any],
    db: Any,
    fuzzy_threshold: float = 0.8,
) -> Optional[Dict[str, Any]]:
    """Check whether *node* duplicates an existing node in *db*.

    Parameters
    ----------
    node : dict
        Candidate node with ``label`` and ``properties.name``.
    db : object
        Database instance exposing ``db.csr_adapter.get_all_nodes()``.
    fuzzy_threshold : float
        Minimum keyword-overlap ratio to consider a fuzzy match.

    Returns
    -------
    dict or None
        ``{"match_type": "exact"|"fuzzy", "existing_id": str}`` if a
        duplicate is found, otherwise ``None``.
    """
    candidate_label = node.get("label", "")
    candidate_name = (node.get("properties") or {}).get("name", "")
    if not candidate_name:
        return None

    # Get all nodes — handle both CSR and Redis adapter APIs
    try:
        existing_nodes = db.get_all_nodes()
    except Exception:
        try:
            existing_nodes = db.csr_adapter.get_all_nodes()
        except Exception:
            return None

    best_fuzzy: Optional[Dict[str, Any]] = None
    best_score: float = 0.0

    for existing in existing_nodes:
        ex_label = getattr(existing, "node_type", None) or getattr(existing, "label", "")
        if ex_label.lower() != candidate_label.lower():
            continue

        ex_name = (getattr(existing, "properties", None) or {}).get("name", "")
        if not ex_name:
            continue

        # Exact match: case-insensitive name comparison
        if candidate_name.strip().lower() == ex_name.strip().lower():
            return {"match_type": "exact", "existing_id": existing.id}

        # Fuzzy match: keyword overlap
        score = _keyword_overlap(candidate_name, ex_name)
        if score >= fuzzy_threshold and score > best_score:
            best_score = score
            best_fuzzy = {"match_type": "fuzzy", "existing_id": existing.id}

    return best_fuzzy


def dedup_check_batch(
    nodes: List[Dict[str, Any]],
    db: Any,
    fuzzy_threshold: float = 0.8,
) -> List[Dict[str, Any]]:
    """Run :func:`dedup_check` on each node, adding ``_dedup_match`` key.

    Returns the same list (mutated in-place) with ``_dedup_match`` set to the
    match dict or ``None``.
    """
    for node in nodes:
        node["_dedup_match"] = dedup_check(node, db, fuzzy_threshold=fuzzy_threshold)
    return nodes
