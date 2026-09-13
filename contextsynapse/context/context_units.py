"""
Context Units — Self-Discoverable Intelligence Layer
=====================================================
A ContextUnit (CU) is a synthesized intelligence object that bundles related
facts, entities, and events into a single addressable knowledge packet.

CUs answer questions, expose what they know, and hint at what to look for next.

Key functions:
- cluster_facts_for_cus()  — group facts by shared keywords into candidate clusters
- build_context_unit()     — build a CU dict from a topic + facts + entities
- match_intent()           — find CUs relevant to a natural-language query
- format_cu_for_agent()    — render a CU as human-readable text
"""

from __future__ import annotations

import logging
import re
import uuid
from collections import Counter, defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Stop words (excluded from keyword matching) ─────────────────────────────

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "shall", "can", "need", "dare", "ought",
    "used", "to", "of", "in", "on", "at", "by", "for", "with", "about",
    "against", "between", "into", "through", "during", "before", "after",
    "above", "below", "from", "up", "down", "out", "off", "over", "under",
    "again", "then", "once", "that", "this", "these", "those", "what",
    "which", "who", "whom", "when", "where", "why", "how", "and", "but",
    "or", "nor", "not", "so", "yet", "both", "either", "neither", "if",
    "as", "it", "its", "he", "she", "they", "we", "i", "you", "me", "him",
    "her", "us", "them", "my", "your", "his", "our", "their", "there",
    "says", "said", "say", "s", "whats", "happening", "happening",
})


def _tokenize(text: str) -> list[str]:
    """Lowercase, split on non-word chars, strip stop words."""
    tokens = re.findall(r"[a-z]+", text.lower())
    return [t for t in tokens if t not in _STOP_WORDS and len(t) > 2]


def _extract_features(text: str) -> list[str]:
    """Extract unigrams + bigrams + capitalized entities for richer TF-IDF."""
    # Unigrams (lowercase, no stop words)
    words = re.findall(r"[a-z]+", text.lower())
    unigrams = [w for w in words if w not in _STOP_WORDS and len(w) > 2]

    # Bigrams — capture phrases like "nuclear_talks", "climate_summit"
    bigrams = [f"{unigrams[i]}_{unigrams[i+1]}" for i in range(len(unigrams) - 1)]

    # Capitalized words from original text — proper nouns (Iran, India, OpenAI, etc.)
    entities = re.findall(r"\b[A-Z][a-z]{2,}\b", text)
    entity_features = [f"ENT_{e.lower()}" for e in entities]

    return unigrams + bigrams + entity_features


# ── Clustering ───────────────────────────────────────────────────────────────


def cluster_facts_for_cus(
    facts: List[Dict[str, Any]],
    min_cluster_size: int = 3,
    max_clusters: int = 15,
    db=None,
) -> List[Dict[str, Any]]:
    """
    Group facts into clusters using graph-aware entity co-occurrence.

    Strategy (best → fallback):
    1. **Graph-aware** (if db provided): facts sharing entities (via MENTIONS edges)
       cluster together. A fact about "Iran" and "Nuclear talks" clusters with
       another fact about "Iran" and "IAEA" because they share the Iran entity.
    2. **Embedding-based**: cached Qdrant vectors + k-means.
    3. **TF-IDF**: unigram+bigram+entity features + k-means.

    Returns a list of cluster dicts:
        {"topic": str, "facts": [fact_dict, ...]}
    """
    if not facts:
        return []

    import math
    n = len(facts)
    if n < 30:
        k = max(2, int(math.sqrt(n)))
    else:
        k = max(3, min(max_clusters, max(n // 15, int(math.sqrt(n)))))

    # Strategy 1: Graph-aware clustering via entity co-occurrence
    if db is not None:
        clusters = _cluster_with_graph(facts, db, k, min_cluster_size)
        if clusters:
            logger.info("[CU] Graph-aware clustering: %d facts → %d clusters", n, len(clusters))
            return clusters

    # Strategy 2: Embedding-based clustering
    texts = [
        fact.get("properties", {}).get("statement", "")
        or fact.get("properties", {}).get("name", "")
        for fact in facts
    ]
    clusters = _cluster_with_embeddings(facts, texts, k, min_cluster_size)
    if clusters:
        logger.info("[CU] Embedding-based clustering: %d facts → %d clusters", n, len(clusters))
        return clusters

    # Strategy 3: TF-IDF fallback
    clusters = _cluster_with_tfidf(facts, texts, k, min_cluster_size)
    logger.info("[CU] TF-IDF clustering: %d facts → %d clusters", n, len(clusters))
    return clusters


def _cluster_with_graph(
    facts: List[Dict], db, k: int, min_size: int,
) -> List[Dict[str, Any]]:
    """
    Cluster facts using graph structure — entity co-occurrence.

    For each fact, find which entities it connects to via MENTIONS edges
    (or reverse: entities that MENTION this fact). Facts sharing entities
    naturally cluster together.

    Builds a fact × entity binary matrix, then runs k-means on it.
    """
    try:
        import numpy as np
    except ImportError:
        return []

    # Build fact_id → index mapping
    fact_ids = [f.get("id", "") for f in facts]
    fact_idx = {fid: i for i, fid in enumerate(fact_ids) if fid}

    if not fact_idx:
        return []

    # Build fact→entity connections via 2-hop: Fact ←STATES← Passage →MENTIONS→ Entity
    # Step 1: Scan edges to build passage→facts and passage→entities maps
    entity_set: Dict[str, int] = {}  # entity_id → column index
    fact_entities: Dict[int, List[int]] = defaultdict(list)  # fact_row → [entity_cols]
    entity_names: Dict[int, str] = {}

    passage_to_facts: Dict[str, List[int]] = defaultdict(list)  # passage_id → [fact rows]
    passage_to_entities: Dict[str, List[str]] = defaultdict(list)  # passage_id → [entity_ids]

    try:
        all_edges = db.get_all_edges()
    except Exception:
        return []

    for edge in all_edges:
        src = getattr(edge, 'source', '')
        tgt = getattr(edge, 'target', '')
        label = getattr(edge, 'label', '') or getattr(edge, 'edge_type', '')

        # Passage →STATES→ Fact (or Fact →DERIVED_FROM→ Passage)
        if label == "STATES" and tgt in fact_idx:
            passage_to_facts[src].append(fact_idx[tgt])
        elif label == "DERIVED_FROM" and src in fact_idx:
            passage_to_facts[tgt].append(fact_idx[src])

        # Passage →MENTIONS→ Entity
        if label == "MENTIONS":
            passage_to_entities[src].append(tgt)

        # Direct Fact →MENTIONS→ Entity (if any exist)
        if label == "MENTIONS" and src in fact_idx:
            eid = tgt
            if eid not in entity_set:
                entity_set[eid] = len(entity_set)
            fact_entities[fact_idx[src]].append(entity_set[eid])

    # Step 2: Bridge — facts inherit entities from their source passage
    for passage_id, fact_rows in passage_to_facts.items():
        entity_ids = passage_to_entities.get(passage_id, [])
        for eid in entity_ids:
            if eid not in entity_set:
                entity_set[eid] = len(entity_set)
            col = entity_set[eid]
            for row in fact_rows:
                fact_entities[row].append(col)

    # Step 3: Resolve entity names
    # Build a complete ID→name map from all entity nodes upfront.
    # This handles merged/deduplicated entities where edge targets point to
    # pre-merge IDs that have empty properties (ghost nodes).
    _all_entity_names: Dict[str, str] = {}
    for _label in ("Person", "Organization", "Location", "Event"):
        try:
            for _n in db.get_all_nodes(label=_label):
                _nid = _n.id if hasattr(_n, 'id') else ""
                _props = _n.properties if hasattr(_n, 'properties') else {}
                _name = _props.get("name") or _props.get("title") or ""
                _name = _name.split("\n")[0].strip()[:60]
                if _nid and _name:
                    _all_entity_names[_nid] = _name
        except Exception:
            pass

    for eid, col in entity_set.items():
        # Try the pre-built map first (fast, handles merged entities)
        name = _all_entity_names.get(eid, "")
        if not name:
            # Fallback: direct node lookup (handles non-standard labels)
            try:
                node = db.get_node(eid)
                if node:
                    props = node.properties if hasattr(node, 'properties') else {}
                    name = props.get("name") or props.get("title") or ""
                    name = name.split("\n")[0].strip()[:60]
            except Exception:
                pass
        entity_names[col] = name if name else eid[:12]

    if not entity_set or not fact_entities:
        logger.debug("[CU] No graph edges found for facts — skipping graph clustering")
        return []

    # Build binary fact × entity matrix
    n_facts = len(facts)
    n_entities = len(entity_set)
    mat = np.zeros((n_facts, n_entities), dtype=np.float32)

    for row, cols in fact_entities.items():
        for col in cols:
            mat[row, col] = 1.0

    # Weight by IDF — entities mentioned by many facts are less discriminative
    import math as _math
    for col in range(n_entities):
        doc_freq = np.sum(mat[:, col] > 0)
        if doc_freq > 0:
            mat[:, col] *= _math.log(n_facts / doc_freq + 1)

    # Normalize rows
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    mat = mat / norms

    # Filter out facts with zero entity connections (no graph signal)
    connected = np.where(np.any(mat > 0, axis=1))[0]
    if len(connected) < max(k, n_facts // 3):
        logger.debug("[CU] Only %d/%d facts have graph connections — insufficient", len(connected), n_facts)
        return []

    labels = _kmeans(mat, k, max_iter=25)

    # Build clusters with entity-derived topics
    clusters = _build_clusters_from_labels(facts, list(range(n_facts)), labels, min_size)

    # Enrich topic names with top entity names from each cluster
    for cluster in clusters:
        entity_counts: Counter = Counter()
        for fact in cluster["facts"]:
            fid = fact.get("id", "")
            if fid in fact_idx:
                for col in fact_entities.get(fact_idx[fid], []):
                    name = entity_names.get(col, "")
                    if name and name != "same-source":
                        entity_counts[name] += 1
        if entity_counts:
            # Filter out entity-ID-like names (ent_xxxx) — they're unresolved
            real_names = [(n, c) for n, c in entity_counts.most_common(10)
                          if not n.startswith("ent_") and len(n) > 2]
            if real_names:
                top_entities = [name for name, _ in real_names[:3]]
                cluster["topic"] = " / ".join(top_entities)
                cluster["key_entities"] = [name for name, _ in real_names[:5]]
            else:
                # All entity names unresolved — fall back to keyword topic
                cluster["topic"] = _derive_topic(cluster["facts"])
        # Final safety: if topic still contains ent_ IDs, override with keywords
        if "ent_" in cluster.get("topic", ""):
            cluster["topic"] = _derive_topic(cluster["facts"])

    # Final cleanup: replace any remaining ent_ ID topics with keyword-derived topics
    for cluster in clusters:
        if "ent_" in cluster.get("topic", ""):
            cluster["topic"] = _derive_topic(cluster["facts"])

    return clusters


def _cluster_with_embeddings(
    facts: List[Dict], texts: List[str], k: int, min_size: int
) -> List[Dict[str, Any]]:
    """Cluster facts using cached embedding vectors + k-means."""
    try:
        from ..search.embedding_cache import get_embedding_cache
        cache = get_embedding_cache()
    except Exception:
        return []

    # Retrieve cached embeddings
    vectors = []
    indices = []  # which facts have embeddings
    for i, text in enumerate(texts):
        if not text.strip():
            continue
        vec = cache.get(text)
        if vec is not None:
            vectors.append(vec)
            indices.append(i)

    # Need at least 50% coverage and enough for k-means
    if len(vectors) < max(k, len(facts) // 2):
        return []

    try:
        import numpy as np
        mat = np.array(vectors, dtype=np.float32)
        # Normalize for cosine distance
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms

        labels = _kmeans(mat, k, max_iter=20)

        return _build_clusters_from_labels(facts, indices, labels, min_size)
    except Exception as e:
        logger.debug("[CU] Embedding clustering failed: %s", e)
        return []


def _cluster_with_tfidf(
    facts: List[Dict], texts: List[str], k: int, min_size: int, _depth: int = 0,
) -> List[Dict[str, Any]]:
    """Cluster facts using TF-IDF vectors + k-means. Pure Python, no sklearn."""
    # Build TF-IDF matrix with unigrams + bigrams + entity features
    doc_tokens = [_extract_features(t) for t in texts]
    # Vocabulary: tokens that appear in 2+ docs but <40% of docs
    doc_freq: Dict[str, int] = Counter()
    for tokens in doc_tokens:
        for t in set(tokens):
            doc_freq[t] += 1

    n_docs = len(doc_tokens)
    # Include all terms appearing in 2+ docs. IDF naturally down-weights common ones.
    # Only exclude terms in >80% of docs (truly universal noise like "report", "news").
    max_df = max(int(n_docs * 0.8), 3)
    vocab = {t: idx for idx, (t, df) in enumerate(
        (t, df) for t, df in doc_freq.items() if 2 <= df <= max_df
    )}

    if len(vocab) < 3:
        # Not enough distinctive terms — return single cluster
        return [{"topic": _derive_topic(facts), "facts": facts}]

    import math as _math

    # Build sparse TF-IDF vectors as dicts
    idf = {t: _math.log(n_docs / df) for t, df in doc_freq.items() if t in vocab}
    vectors = []
    indices = []
    for i, tokens in enumerate(doc_tokens):
        if not tokens:
            continue
        tf = Counter(tokens)
        vec = {}
        for t, count in tf.items():
            if t in vocab:
                vec[vocab[t]] = (count / len(tokens)) * idf.get(t, 1.0)
        if vec:
            vectors.append(vec)
            indices.append(i)

    if len(vectors) < k:
        return [{"topic": _derive_topic(facts), "facts": facts}]

    # Convert sparse to dense for k-means
    try:
        import numpy as np
        dim = len(vocab)
        mat = np.zeros((len(vectors), dim), dtype=np.float32)
        for row, vec in enumerate(vectors):
            for col, val in vec.items():
                mat[row, col] = val
        # Normalize
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms

        labels = _kmeans(mat, k, max_iter=20)
        return _build_clusters_from_labels(facts, indices, labels, min_size, _depth=_depth)
    except ImportError:
        # No numpy — pure Python fallback with simple keyword grouping
        return _cluster_keyword_fallback(facts, min_size)


def _kmeans(mat, k: int, max_iter: int = 20) -> list:
    """Lightweight k-means on a numpy matrix. Returns list of cluster labels."""
    import numpy as np
    n = mat.shape[0]
    k = min(k, n)

    # K-means++ initialization
    rng = np.random.RandomState(42)
    centroids = [mat[rng.randint(n)]]
    for _ in range(k - 1):
        dists = np.array([min(np.dot(mat[i] - c, mat[i] - c) for c in centroids) for i in range(n)])
        probs = dists / (dists.sum() + 1e-12)
        idx = rng.choice(n, p=probs)
        centroids.append(mat[idx])
    centroids = np.array(centroids)

    labels = np.zeros(n, dtype=int)
    for _ in range(max_iter):
        # Assign
        # Cosine similarity = dot product on normalized vectors
        sims = mat @ centroids.T  # (n, k)
        new_labels = np.argmax(sims, axis=1)
        if np.array_equal(new_labels, labels):
            break
        labels = new_labels
        # Update centroids
        for j in range(k):
            members = mat[labels == j]
            if len(members) > 0:
                c = members.mean(axis=0)
                norm = np.linalg.norm(c)
                centroids[j] = c / norm if norm > 0 else c

    return labels.tolist()


def _build_clusters_from_labels(
    facts: List[Dict], indices: List[int], labels: list, min_size: int,
    _depth: int = 0,
) -> List[Dict[str, Any]]:
    """Group facts by cluster labels and derive topics.
    Recursively splits oversized clusters (>40% of total)."""
    groups: Dict[int, List[Dict]] = defaultdict(list)
    for idx, label in zip(indices, labels):
        groups[label].append(facts[idx])

    # Also assign un-indexed facts to nearest cluster by keyword overlap
    indexed_set = set(indices)
    for i, fact in enumerate(facts):
        if i not in indexed_set:
            if groups:
                best = max(groups, key=lambda g: len(groups[g]))
                groups[best].append(fact)

    total = len(facts)
    max_cluster_pct = 0.4  # No single cluster should be >40% of total

    clusters = []
    for label, cluster_facts in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(cluster_facts) < min_size:
            continue

        # Recursively split oversized clusters (max 2 levels deep)
        if len(cluster_facts) > total * max_cluster_pct and _depth < 2 and len(cluster_facts) >= 6:
            sub_texts = [
                f.get("properties", {}).get("statement", "")
                or f.get("properties", {}).get("name", "")
                for f in cluster_facts
            ]
            import math
            sub_k = max(2, min(5, len(cluster_facts) // 10))
            sub_clusters = _cluster_with_tfidf(cluster_facts, sub_texts, sub_k, min_size, _depth=_depth + 1)
            if len(sub_clusters) > 1:
                clusters.extend(sub_clusters)
                continue

        topic = _derive_topic(cluster_facts)
        clusters.append({"topic": topic, "facts": cluster_facts})

    # If min_size filtered everything, return top clusters with relaxed threshold
    if not clusters and groups:
        for label, cluster_facts in sorted(groups.items(), key=lambda x: -len(x[1])):
            if len(cluster_facts) >= 2:
                topic = _derive_topic(cluster_facts)
                clusters.append({"topic": topic, "facts": cluster_facts})
            if len(clusters) >= 3:
                break

    return clusters


def _derive_topic(facts: List[Dict]) -> str:
    """Derive a human-readable topic label from the most frequent keywords."""
    all_text = " ".join(
        f.get("properties", {}).get("statement", "") for f in facts
    )
    freq = Counter(_tokenize(all_text))
    # Filter out very common words within this cluster (>60% of facts)
    n = len(facts)
    top = [(kw, c) for kw, c in freq.most_common(10) if c < n * 0.6]
    if not top:
        top = freq.most_common(3)
    top_kws = [kw for kw, _ in top[:3]]
    return " ".join(w.capitalize() for w in top_kws) or "General"


def _cluster_keyword_fallback(facts: List[Dict], min_size: int) -> List[Dict[str, Any]]:
    """Simple keyword-based grouping when numpy is unavailable. Uses TF-IDF scores
    to pick the top distinctive keyword per fact, then groups by that keyword."""
    doc_tokens = [_tokenize(
        f.get("properties", {}).get("statement", "") or f.get("properties", {}).get("name", "")
    ) for f in facts]

    doc_freq: Dict[str, int] = Counter()
    for tokens in doc_tokens:
        for t in set(tokens):
            doc_freq[t] += 1

    import math as _math
    n_docs = len(facts)
    idf = {t: _math.log(n_docs / df) for t, df in doc_freq.items() if df >= 2}

    groups: Dict[str, List[Dict]] = defaultdict(list)
    for i, tokens in enumerate(doc_tokens):
        if not tokens:
            groups["general"].append(facts[i])
            continue
        tf = Counter(tokens)
        # Pick the keyword with highest TF-IDF score as this fact's "topic"
        best_kw = max(
            (t for t in tokens if t in idf),
            key=lambda t: (tf[t] / len(tokens)) * idf[t],
            default="general",
        )
        groups[best_kw].append(facts[i])

    # Merge tiny groups into "other"
    clusters = []
    other: List[Dict] = []
    for kw, group_facts in sorted(groups.items(), key=lambda x: -len(x[1])):
        if len(group_facts) >= min_size:
            clusters.append({"topic": kw.capitalize(), "facts": group_facts})
        else:
            other.extend(group_facts)
    if other and len(other) >= min_size:
        clusters.append({"topic": _derive_topic(other), "facts": other})

    return clusters or [{"topic": _derive_topic(facts), "facts": facts}]


# ── CU Builder ───────────────────────────────────────────────────────────────


def build_context_unit(
    topic: str,
    facts: List[Dict[str, Any]],
    entities: Optional[List[Dict[str, Any]]] = None,
    events: Optional[List[Dict[str, Any]]] = None,
    use_llm: bool = False,
    llm_client: Any = None,
    cu_config: Any = None,
) -> Dict[str, Any]:
    """
    Build a ContextUnit dict from a topic, facts, entities, and events.

    Parameters
    ----------
    topic       : Human-readable label for this CU's subject matter.
    facts       : List of Fact node dicts (with 'id' and 'properties').
    entities    : List of Person/Organization/Location node dicts.
    events      : List of Event node dicts.
    use_llm     : If True and llm_client is provided, synthesize claim via LLM.
    llm_client  : Optional LLM client (must expose .complete(prompt) → str).
    cu_config   : Optional ContextUnitConfig from schema (domain-aware CU rules).

    Returns
    -------
    A ContextUnit dict with keys:
        id, label, properties (topic, claim, confidence, questions_answered,
                               next_clues, created_at), evidence_ids, actor_ids
    """
    entities = entities or []
    events = events or []

    # Extract raw text
    statements = [
        f.get("properties", {}).get("statement", "")
        for f in facts
        if f.get("properties", {}).get("statement")
    ]
    actor_names = [
        e.get("properties", {}).get("name", "")
        for e in entities
        if e.get("properties", {}).get("name")
    ]
    event_names = [
        ev.get("properties", {}).get("name", "")
        for ev in events
        if ev.get("properties", {}).get("name")
    ]

    evidence_ids = [f.get("id") for f in facts if f.get("id")]
    actor_ids = [e.get("id") for e in entities if e.get("id")]

    # Build properties via LLM or heuristic
    if use_llm and llm_client is not None:
        props = _build_cu_with_llm(topic, statements, actor_names, event_names, llm_client)
    else:
        props = _build_cu_heuristic(topic, statements, actor_names, event_names)

    # Sanitize topic — replace unresolved entity IDs with keyword-derived topic
    if "ent_" in topic:
        all_text = " ".join(statements[:20])
        freq = Counter(_tokenize(all_text))
        top_kws = [kw for kw, _ in freq.most_common(3)]
        topic = " ".join(w.capitalize() for w in top_kws) or topic

    props["topic"] = topic

    # Apply domain-specific overrides from schema's context_unit config
    # (runs after topic is set so claim_template can reference it)
    if cu_config is not None:
        props = _apply_cu_config(props, facts, cu_config)

    cu_id = f"cu_{uuid.uuid4().hex[:12]}"
    return {
        "id": cu_id,
        "label": "ContextUnit",
        "properties": props,
        "evidence_ids": evidence_ids,
        "actor_ids": actor_ids,
    }


def _build_cu_with_llm(
    topic: str,
    statements: List[str],
    actor_names: List[str],
    event_names: List[str],
    llm_client: Any,
) -> Dict[str, Any]:
    """Synthesize CU properties using an LLM client."""
    facts_text = "\n".join(f"- {s}" for s in statements[:10])
    actors_text = ", ".join(actor_names) if actor_names else "unknown"
    prompt = (
        f"You are a news analyst. Given the following facts about '{topic}', "
        f"write:\n1. A single-sentence claim summarising the situation.\n"
        f"2. Two questions this cluster of facts answers.\n"
        f"3. One next clue to follow up.\n\n"
        f"Facts:\n{facts_text}\n\nActors: {actors_text}\n\n"
        f"Respond as JSON with keys: claim, questions_answered (list), next_clues (list)."
    )
    try:
        raw = llm_client.complete(prompt)
        import json as _json
        data = _json.loads(raw)
        confidence = min(0.95, 0.5 + 0.05 * len(statements))
        return {
            "claim": data.get("claim", statements[0] if statements else topic),
            "confidence": confidence,
            "questions_answered": data.get("questions_answered", []),
            "next_clues": data.get("next_clues", []),
        }
    except Exception as exc:
        logger.warning("LLM synthesis failed, falling back to heuristic: %s", exc)
        return _build_cu_heuristic(topic, statements, actor_names, event_names)


def _build_cu_heuristic(
    topic: str,
    statements: List[str],
    actor_names: List[str],
    event_names: List[str],
) -> Dict[str, Any]:
    """
    Build CU properties without an LLM.

    - claim          : first 1-2 fact statements joined
    - confidence     : 0.3 + 0.1 * num_facts, capped at 0.95
    - questions_answered : derived from topic and actor names
    - next_clues     : derived from actor and event names
    """
    # Claim: concatenate first two statements
    if statements:
        claim_parts = [s.rstrip(".") for s in statements[:2]]
        claim = "; ".join(claim_parts) + "."
    else:
        claim = f"Situation update on {topic}."

    confidence = min(0.95, 0.3 + 0.1 * len(statements))

    # Questions answered
    questions: list[str] = [f"What is the current situation regarding {topic}?"]
    for name in actor_names[:2]:
        questions.append(f"What is {name}'s role in {topic}?")

    # Next clues
    next_clues: list[str] = []
    for name in actor_names[:2]:
        next_clues.append(f"Follow up on {name}'s statements.")
    for name in event_names[:2]:
        next_clues.append(f"Track developments in {name}.")
    if not next_clues:
        next_clues.append(f"Search for latest updates on {topic}.")

    return {
        "claim": claim,
        "confidence": confidence,
        "questions_answered": questions,
        "next_clues": next_clues,
    }


def _apply_cu_config(
    props: Dict[str, Any],
    facts: List[Dict[str, Any]],
    cu_config: Any,
) -> Dict[str, Any]:
    """Apply domain-specific ContextUnitConfig overrides to CU properties.

    - confidence_weights: replaces flat 0.1-per-fact with evidence-type-weighted scoring
    - claim_template: domain-specific claim phrasing (if non-empty)
    - domain_questions: prepends domain seed questions to questions_answered
    """
    # 1. Domain-aware confidence scoring
    weights = getattr(cu_config, "confidence_weights", {})
    if weights:
        total_weight = 0.0
        matched = 0
        for fact in facts:
            fprops = fact.get("properties", {})
            # Check fact's evidence_level, evidence_type, type, or label
            ev_type = (
                fprops.get("evidence_level")
                or fprops.get("evidence_type")
                or fprops.get("type", "")
            ).lower().replace(" ", "_")
            w = weights.get(ev_type, 0.0)
            if w > 0:
                total_weight += w
                matched += 1
            else:
                # Unclassified evidence gets a small default weight
                total_weight += 0.05
        # Base 0.2 + accumulated evidence weight, capped at 0.95
        props["confidence"] = min(0.95, 0.2 + total_weight)
        if matched > 0:
            props["evidence_quality"] = f"{matched}/{len(facts)} typed"

    # 2. Claim template
    template = getattr(cu_config, "claim_template", "")
    if template and props.get("claim"):
        try:
            props["claim"] = template.format(
                topic=props.get("topic", ""),
                claim=props["claim"],
                evidence_type=props.get("evidence_quality", "mixed"),
                fact_count=len(facts),
                exposure="",
            )
        except (KeyError, IndexError):
            pass  # template has placeholders we can't fill — keep original claim

    # 3. Domain questions — prepend schema-defined questions, deduplicate
    domain_qs = getattr(cu_config, "domain_questions", [])
    if domain_qs:
        existing = set(props.get("questions_answered", []))
        new_qs = [q for q in domain_qs if q not in existing]
        props["questions_answered"] = new_qs + props.get("questions_answered", [])

    return props


# ── Intent Matching ──────────────────────────────────────────────────────────


def match_intent(
    query: str,
    cus: List[Dict[str, Any]],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    """
    Find CUs whose topic or questions_answered overlap with a query.

    Scoring:
    - Each query token matched in CU.topic contributes 2 points.
    - Each query token matched in any questions_answered string contributes 1 point.

    Returns CUs with score > 0, ranked descending, up to `limit`.
    """
    query_tokens = set(_tokenize(query))
    if not query_tokens:
        return []

    scored: list[tuple[int, dict]] = []
    for cu in cus:
        props = cu.get("properties", {})
        score = 0

        topic_tokens = set(_tokenize(props.get("topic", "")))
        score += 2 * len(query_tokens & topic_tokens)

        for q in props.get("questions_answered", []):
            q_tokens = set(_tokenize(q))
            score += len(query_tokens & q_tokens)

        if score > 0:
            scored.append((score, cu))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [cu for _, cu in scored[:limit]]


# ── Formatter ────────────────────────────────────────────────────────────────


def format_cu_for_agent(
    cu: Dict[str, Any],
    zoom: str = "full",
    cu_config: Any = None,
) -> str:
    """Render a ContextUnit at a specific zoom level.

    Zoom levels (like Google Maps tile resolution):
      headline  — ~20 tokens: topic + short claim. For instant/fast tiers.
      summary   — ~80 tokens: claim + evidence count + top questions. For standard tier.
      full      — ~200 tokens: everything including evidence IDs. For deep tier.

    If cu_config has custom zoom_levels templates, those are used instead.
    """
    props = cu.get("properties", {})
    topic = props.get("topic", "Unknown")
    claim = props.get("claim", "")
    confidence = props.get("confidence", 0.0)
    questions = props.get("questions_answered", [])
    clues = props.get("next_clues", [])
    evidence = cu.get("evidence_ids", [])

    # Try schema-defined zoom template first
    if cu_config is not None:
        templates = getattr(cu_config, "zoom_levels", {})
        template = templates.get(zoom, "")
        if template:
            try:
                claim_short = claim.split(";")[0].split(".")[0].strip()
                return template.format(
                    topic=topic,
                    confidence=confidence,
                    claim=claim,
                    claim_short=claim_short[:80],
                    questions="; ".join(questions[:3]),
                    questions_short="; ".join(q[:40] for q in questions[:2]),
                    clues="; ".join(clues[:2]),
                    evidence_ids=", ".join(evidence[:5]),
                    evidence_count=len(evidence),
                )
            except (KeyError, IndexError, ValueError):
                pass  # fall through to default formatting

    # Default zoom formatting
    if zoom == "headline":
        claim_short = claim.split(";")[0].split(".")[0].strip()
        return f"[{topic}] {claim_short[:80]} ({confidence:.0%})"

    if zoom == "summary":
        lines = [
            f"[{topic}] ({confidence:.0%})",
            f"  {claim[:150]}",
        ]
        if evidence:
            lines.append(f"  Evidence: {len(evidence)} sources")
        if questions:
            lines.append(f"  Answers: {'; '.join(q[:40] for q in questions[:2])}")
        return "\n".join(lines)

    # full (default)
    lines = [
        f"[ContextUnit: {topic}]",
        f"Claim: {claim}",
        f"Confidence: {confidence:.0%}",
    ]
    if questions:
        lines.append("Answers:")
        for q in questions:
            lines.append(f"  - {q}")
    if clues:
        lines.append("Next clues:")
        for c in clues:
            lines.append(f"  - {c}")
    if evidence:
        lines.append(f"Evidence: {', '.join(evidence)}")

    return "\n".join(lines)
