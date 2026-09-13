"""LMDB-backed keyword and BM25 index for microsecond search.

Replaces the in-memory keyword index (60s rebuild cycle) and Whoosh BM25
with persistent, memory-mapped indexes that survive restarts and update
incrementally.

Usage:
    idx = LMDBIndex("contextcore_data/lmdb_index/my_graph")
    idx.index_node(node_id, label, {"name": "Netanyahu", "statement": "..."})
    results = idx.search("netanyahu", limit=10)
"""

import json
import logging
import math
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)

try:
    import lmdb
except ImportError:
    lmdb = None
    logger.warning("lmdb not installed — falling back to in-memory index")


# Fields to index and their weights
_FIELD_WEIGHTS = {"name": 2.0, "title": 2.0, "content": 1.0, "statement": 1.5, "description": 1.0}

# Labels whose nodes are meaningful search results (used in hybrid_search neighbour expansion)
_CONTENT_LABELS: frozenset = frozenset({
    "Person", "Organization", "Location", "Event",
    "Fact", "Passage", "Finding", "Insight",
})

# Labels to exclude from indexing
_NOISE_LABELS = frozenset({
    # Agent mechanics — not knowledge
    "AgentThought", "AgentAction", "AgentMessage", "AgentPresence",
    "Task", "Action",
    # Conversation structure — search entities/topics/signals, not raw turns
    "Turn", "Session",
    # System/experiment infrastructure
    "ExperimentRun", "ExperimentScore", "PipelineRun",
    "VectorIndex", "BM25Index", "ContextRef",
    "Context", "Project", "ContextIntelligence",
    "CodeBase", "SystemStore", "UserStore", "WebStore",
    "GeneratedStore", "MemoryStore", "ArtifactStore", "ToolStore",
    "KnowledgeBase",
})

# Stop words — skip these terms
_STOP_WORDS = frozenset({
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "shall",
    "should", "may", "might", "can", "could", "of", "in", "to", "for",
    "with", "on", "at", "by", "from", "as", "into", "through", "during",
    "and", "or", "but", "not", "no", "if", "then", "than", "that", "this",
    "it", "its", "he", "she", "his", "her", "we", "they", "them", "our",
    "said", "also", "one", "two", "who", "what", "when", "where",
    "how", "all", "each", "every", "both", "few", "more", "most", "other",
    "some", "such", "only", "own", "same", "so", "very", "just", "about",
    "https", "http", "www", "com", "org", "html", "htm",
})


# Pre-compiled tokenization patterns (module-level for zero per-call overhead)
# Splits on whitespace and common punctuation boundaries
_PUNCT_RE = re.compile(r"[\s\-_/.,;:!?()\[\]{}\"|]+")
# Inserts a split at camelCase boundaries:
#   lower→UPPER: "graphNode" → "graph Node"
#   UPPER sequence → "UIContext" → "UI Context"
_CAMEL_RE = re.compile(r"(?<=[a-z])(?=[A-Z])|(?<=[A-Z])(?=[A-Z][a-z])")

_VOWELS = frozenset("aeiou")


def _stem(word: str) -> str:
    """Minimal suffix-stripping stemmer (English). Zero dependencies.

    Handles the highest-value, lowest-error suffix rules only:
      -ies → -y  : "queries"    → "query"
      -ing        : "searching"  → "search", "processing" → "process"
      -ed         : "indexed"    → "index",  "processed"  → "process"
      -s          : "nodes"      → "node",   "graphs"     → "graph"
                    (blocked when stem ends in "ss": "class", "process")

    Deliberately omits doubled-consonant undoing to avoid "process" → "proces"
    false-positives. "running" → "runn" is an acceptable imprecision for a
    knowledge-graph workload where uninflected root queries are rare.
    """
    n = len(word)
    if n <= 3:
        return word
    if word.endswith("ies") and n > 4:
        return word[:-3] + "y"
    if word.endswith("ing") and n > 5:
        return word[:-3]
    if word.endswith("ed") and n > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and n > 3:
        return word[:-1]
    return word


def _tokenize(text: str, max_token_len: int = 100) -> List[str]:
    """Tokenize text into stemmed lowercase terms, skipping stop words.

    Pipeline:
      1. Split camelCase on original text ("GraphNode" → "Graph Node").
      2. Lowercase and split on whitespace + punctuation.
      3. Filter by length and stop words.
      4. Stem each token (-ing/-ed/-s/-ies).
      5. Re-filter stemmed form (stem may produce a stop word or be too short).
    """
    # Step 1: camelCase split on original casing, then lowercase
    text = _CAMEL_RE.sub(" ", text).lower()
    result = []
    for t in _PUNCT_RE.split(text):
        if not (2 <= len(t) <= max_token_len) or t in _STOP_WORDS:
            continue
        stemmed = _stem(t)
        if 2 <= len(stemmed) <= max_token_len and stemmed not in _STOP_WORDS:
            result.append(stemmed)
    return result


def _bigrams(tokens: List[str]) -> List[str]:
    """Generate consecutive token pair strings from a token list.

    Pairs are joined with '__' (double underscore) — a separator that
    cannot appear in real tokens since _PUNCT_RE splits on underscores.
    Stored in the same 'terms' LMDB database as unigrams; their low df
    (rare occurrence) gives them high IDF, boosting phrase precision.
    """
    return [f"{tokens[i]}__{tokens[i + 1]}" for i in range(len(tokens) - 1)]


def _char_trigrams(token: str) -> List[str]:
    """Generate character trigrams from a token string.

    Used for typo-tolerant fuzzy search: tokens with ≥50% shared
    trigrams are considered likely matches for the same term despite
    spelling errors.

    Examples:
        "graph"  → ["gra", "rap", "aph"]
        "ab"     → ["ab"]   (too short, return as-is)
        "abc"    → ["abc"]
    """
    if len(token) < 3:
        return [token]
    return [token[i:i + 3] for i in range(len(token) - 2)]


def _compute_node_terms(
    label: str,
    props: Dict[str, Any],
) -> Tuple[Dict[str, float], List[str], bytes]:
    """Compute BM25 terms, term list, and node metadata bytes for one node.

    Returns (terms, term_list, node_data):
      - terms: {token: weight} — accumulated field-weighted token weights,
               including bigram phrase tokens (joined with '__')
      - term_list: list of token strings (same keys as terms, includes bigrams)
      - node_data: JSON-encoded node metadata bytes ready for LMDB storage
                   Returns ({}, [], b"") for noise labels so callers can skip.

    Called by both index_node (in-memory path) and index_batch (LMDB path)
    to guarantee identical term computation in both paths.
    """
    if label in _NOISE_LABELS:
        return {}, [], b""

    # Auto-resolve thin nodes with _ref pointers
    if props.get("_ref", "").startswith("duckdb:"):
        try:
            from contextsynapse.storage.router import get_content_resolver
            node_id = props.get("id", "")
            resolved_text = get_content_resolver().get_text(node_id) if node_id else ""
            if resolved_text:
                props = {**props, "content": resolved_text}
        except Exception:
            pass

    name = props.get("name", "")
    snippet = (
        props.get("statement") or props.get("content") or props.get("text")
        or props.get("description") or name
    )[:200]
    created_at = props.get("_created_at", "")
    updated_at = props.get("_updated_at", "")

    terms: Dict[str, float] = {}
    for field, weight in _FIELD_WEIGHTS.items():
        val = str(props.get(field, ""))
        if not val:
            continue
        field_tokens = _tokenize(val)
        for term in field_tokens:
            terms[term] = terms.get(term, 0) + weight
        # Phrase signals: consecutive token pairs with boosted weight
        for bigram in _bigrams(field_tokens):
            terms[bigram] = terms.get(bigram, 0) + weight * 1.5

    # term_count counts only unigrams — bigrams must not inflate BM25 dl
    unigram_count = sum(1 for t in terms if "__" not in t)
    term_list = list(terms.keys())

    node_data = json.dumps({
        "label": label,
        "name": name[:120],
        "snippet": snippet,
        "_created_at": created_at,
        "_updated_at": updated_at,
        "term_count": unigram_count,   # BM25 dl: unigrams only
    }).encode()

    return terms, term_list, node_data


# Reserved key in the nodes DB for corpus-level BM25 statistics.
# Tilde prefix sorts after all hex UUID characters — won't collide.
_CORPUS_STATS_KEY = b"~corpus_stats"


class LMDBIndex:
    """Persistent keyword + BM25 index backed by LMDB.

    Two LMDB databases in one environment:
    - ``terms``: term → JSON list of (node_id, weight) pairs
    - ``nodes``: node_id → JSON {label, name, snippet}
    """

    def __init__(self, path: str, map_size: int = 256 * 1024 * 1024):
        """Open or create the index at ``path``.

        Parameters
        ----------
        path : str
            Directory for the LMDB environment.
        map_size : int
            Max DB size in bytes (default 256MB, auto-grows on write).
        """
        import threading as _threading_local
        self._path = path
        self._map_size = map_size
        self._env = None
        self._local = _threading_local.local()  # per-thread state (e.g. grow_depth counter)

        if lmdb is None:
            logger.debug("[LMDB] lmdb not installed — using in-memory fallback")
            self._mem_terms: Dict[str, List[Tuple[str, float]]] = {}
            self._mem_nodes: Dict[str, Dict] = {}
            self._mem_node_terms: Dict[str, List[str]] = {}  # reverse index: node_id → [terms]
            self._mem_corpus_stats: Dict[str, int] = {"total_term_count": 0, "doc_count": 0}
            return

        Path(path).mkdir(parents=True, exist_ok=True)
        try:
            self._env = lmdb.open(path, map_size=map_size, max_dbs=7)
            # Pre-open named databases (must be done in a write txn)
            with self._env.begin(write=True) as txn:
                self._terms_db_handle = self._env.open_db(b"terms", txn=txn, create=True)
                self._nodes_db_handle = self._env.open_db(b"nodes", txn=txn, create=True)
                self._passages_db_handle = self._env.open_db(b"passages", txn=txn, create=True)  # node_id → full text
                self._edges_db_handle = self._env.open_db(b"edges", txn=txn, create=True)  # node_id → [{target, label, name}]
                self._neighbors_db_handle = self._env.open_db(b"neighbors", txn=txn, create=True)  # node_id → [similar_ids] (semantic)
                self._node_terms_db_handle = self._env.open_db(b"node_terms", txn=txn, create=True)  # reverse: node_id → [terms]
                self._trigrams_db_handle = self._env.open_db(b"trigrams", txn=txn, create=True)
        except Exception as e:
            logger.warning("[LMDB] Failed to open %s: %s — using in-memory", path, e)
            self._env = None
            self._trigrams_db_handle = None
            self._mem_terms = {}
            self._mem_nodes = {}
            self._mem_node_terms = {}
            self._mem_corpus_stats = {"total_term_count": 0, "doc_count": 0}

    def _terms_db(self, txn=None):
        return self._terms_db_handle

    def _nodes_db(self, txn=None):
        return self._nodes_db_handle

    def _node_terms_db(self, txn=None):
        return self._node_terms_db_handle

    def _trigrams_db(self, txn=None):
        return self._trigrams_db_handle

    # ── Write ──────────────────────────────────────────────────

    def index_node(self, node_id: str, label: str, props: Dict[str, Any]):
        """Index a single node. Call after node creation or update."""
        terms, term_list, node_data = _compute_node_terms(label, props)
        if not node_data:          # noise label — _compute_node_terms returned empty
            return

        if not self._env:
            # In-memory fallback
            old = self._mem_nodes.get(node_id)
            if old:
                # Re-index: subtract old contribution from corpus stats
                self._mem_corpus_stats["total_term_count"] -= old.get("term_count", 0)
                self._mem_corpus_stats["doc_count"] = max(0, self._mem_corpus_stats["doc_count"] - 1)
                # Prune stale terms: remove node from posting lists it no longer belongs to
                old_terms = set(self._mem_node_terms.get(node_id, []))
                stale_terms = old_terms - set(terms.keys())
                for stale_term in stale_terms:
                    if stale_term in self._mem_terms:
                        self._mem_terms[stale_term] = [
                            (nid, w) for nid, w in self._mem_terms[stale_term] if nid != node_id
                        ]
                        if not self._mem_terms[stale_term]:
                            del self._mem_terms[stale_term]
            self._mem_nodes[node_id] = json.loads(node_data)
            self._mem_node_terms[node_id] = term_list  # reverse index
            self._mem_corpus_stats["total_term_count"] += sum(1 for t in term_list if "__" not in t)
            self._mem_corpus_stats["doc_count"] += 1
            for term, weight in terms.items():
                if term not in self._mem_terms:
                    self._mem_terms[term] = []
                # Use dict for O(1) lookup/update, then convert back to list
                postings_dict = {nid: w for nid, w in self._mem_terms[term]}
                postings_dict[node_id] = weight
                self._mem_terms[term] = list(postings_dict.items())
            return

        # LMDB write — delegate to shared helper, handle map-full here
        try:
            with self._env.begin(write=True) as txn:
                self._write_node_to_txn(txn, node_id, node_data, terms, term_list)
        except lmdb.MapFullError:
            # Auto-grow and retry (bounded to prevent infinite recursion).
            # _local.grow_depth is per-thread so concurrent writers don't
            # interfere with each other's retry count.
            depth = getattr(self._local, 'grow_depth', 0)
            if depth >= 4:
                logger.warning("[LMDB] map full after %d grow attempts, giving up", depth)
                return
            self._local.grow_depth = depth + 1
            self._map_size *= 2
            self._env.set_mapsize(self._map_size)
            self.index_node(node_id, label, props)
            self._local.grow_depth = max(0, getattr(self._local, 'grow_depth', 1) - 1)

    def _write_node_to_txn(
        self,
        txn: Any,
        node_id: str,
        node_data: bytes,
        terms: Dict[str, float],
        term_list: List[str],
    ) -> None:
        """Write one node's index data into an open LMDB write transaction.

        Shared by index_node (single-node txn) and index_batch (batch txn)
        so that corpus stats, stale-term pruning, and posting-list updates
        are identical in both paths.
        """
        terms_db = self._terms_db()
        nodes_db = self._nodes_db()
        node_terms_db = self._node_terms_db()

        # Update corpus stats (subtract old term count first on re-index)
        stats_raw = txn.get(_CORPUS_STATS_KEY, db=nodes_db)
        corpus = json.loads(stats_raw) if stats_raw else {"total_term_count": 0, "doc_count": 0}
        old_node_raw = txn.get(node_id.encode(), db=nodes_db)
        if old_node_raw:
            try:
                old_node = json.loads(old_node_raw)
                corpus["total_term_count"] -= old_node.get("term_count", 0)
                corpus["doc_count"] = max(0, corpus["doc_count"] - 1)
            except Exception:
                pass
        corpus["total_term_count"] += sum(1 for t in term_list if "__" not in t)
        corpus["doc_count"] += 1
        txn.put(_CORPUS_STATS_KEY, json.dumps(corpus).encode(), db=nodes_db)

        # Store node metadata (includes term_count for BM25 dl)
        txn.put(node_id.encode(), node_data, db=nodes_db)

        # Prune stale posting-list entries for terms that no longer apply
        old_term_list_raw = txn.get(node_id.encode(), db=node_terms_db)
        if old_term_list_raw:
            try:
                old_terms = set(json.loads(old_term_list_raw))
                stale_terms = old_terms - set(term_list)
                for stale_term in stale_terms:
                    stale_key = stale_term.encode()
                    existing = txn.get(stale_key, db=terms_db)
                    if existing:
                        postings = [[nid, w] for nid, w in json.loads(existing) if nid != node_id]
                        if postings:
                            txn.put(stale_key, json.dumps(postings).encode(), db=terms_db)
                        else:
                            txn.delete(stale_key, db=terms_db)
            except Exception:
                pass  # stale-term pruning must never block indexing

        # Store reverse index: node_id → [terms] for O(K) delete
        txn.put(node_id.encode(), json.dumps(term_list).encode(), db=node_terms_db)

        # Update inverted index for each term — O(1) update via dict
        for term, weight in terms.items():
            key = term.encode()
            existing = txn.get(key, db=terms_db)
            if existing:
                postings_dict = {nid: w for nid, w in json.loads(existing)}
                postings_dict[node_id] = weight
                postings = [[nid, w] for nid, w in postings_dict.items()]
            else:
                postings = [[node_id, weight]]
            txn.put(key, json.dumps(postings).encode(), db=terms_db)

        # Trigram index: character 3-grams of each unigram token → node_id membership
        # Used by fuzzy_candidates() for typo-tolerant search.
        trigrams_db = self._trigrams_db()
        if trigrams_db is not None:
            written_trigrams: Set[str] = set()
            for term in term_list:
                if "__" in term:
                    continue   # skip bigrams — trigrams index unigrams only
                for tg in _char_trigrams(term):
                    if tg in written_trigrams:
                        continue
                    written_trigrams.add(tg)
                    tg_key = tg.encode()
                    existing_raw = txn.get(tg_key, db=trigrams_db)
                    node_set: List[str] = json.loads(existing_raw) if existing_raw else []
                    if node_id not in node_set:
                        node_set.append(node_id)
                        txn.put(tg_key, json.dumps(node_set).encode(), db=trigrams_db)

    def index_batch(self, nodes: list):
        """Index multiple nodes in a single LMDB transaction (N× faster than N calls).

        Pre-computes all node data outside the transaction to minimise lock
        hold time, then writes everything in one atomic commit.  Falls back
        to per-node indexing on MapFullError (map grow + retry).
        """
        if not self._env:
            # In-memory path has no transaction overhead — delegate normally
            for n in nodes:
                node_id = n.get("id", "")
                label = n.get("label", "")
                props = n.get("properties", {})
                if node_id and label:
                    self.index_node(node_id, label, props)
            return

        # Pre-compute outside the transaction to minimise write-lock hold time
        batch: List[Tuple[str, bytes, Dict[str, float], List[str]]] = []
        for n in nodes:
            node_id = n.get("id", "")
            label = n.get("label", "")
            props = n.get("properties", {})
            if not node_id or not label:
                continue
            terms, term_list, node_data = _compute_node_terms(label, props)
            if not node_data:   # noise label
                continue
            batch.append((node_id, node_data, terms, term_list))

        if not batch:
            return

        try:
            with self._env.begin(write=True) as txn:
                for node_id, node_data, terms, term_list in batch:
                    self._write_node_to_txn(txn, node_id, node_data, terms, term_list)
        except lmdb.MapFullError:
            # Grow map and fall back to per-node on map-full (rare)
            self._map_size *= 2
            self._env.set_mapsize(self._map_size)
            for n in nodes:
                node_id = n.get("id", "")
                label = n.get("label", "")
                props = n.get("properties", {})
                if node_id and label:
                    self.index_node(node_id, label, props)

    # ── Read ───────────────────────────────────────────────────

    def search(self, query: str, limit: int = 10, label_filter: str = "") -> List[Dict]:
        """Search the index. Returns list of {node_id, label, name, snippet, score}."""
        query_terms = list(dict.fromkeys(_tokenize(query)))  # deduplicate, preserve order
        if not query_terms:
            return []

        scores: Dict[str, float] = {}

        if not self._env:
            # In-memory fallback
            for term in query_terms:
                for node_id, weight in self._mem_terms.get(term, []):
                    scores[node_id] = scores.get(node_id, 0) + weight
            results = []
            for nid, score in sorted(scores.items(), key=lambda x: -x[1])[:limit * 2]:
                node = self._mem_nodes.get(nid, {})
                if label_filter and node.get("label") != label_filter:
                    continue
                results.append({
                    "node_id": nid, "label": node.get("label", ""),
                    "name": node.get("name", ""), "snippet": node.get("snippet", ""),
                    "score": round(score, 3),
                })
                if len(results) >= limit:
                    break
            return results

        # LMDB read — no locks on readers
        try:
            with self._env.begin() as txn:
                terms_db = self._terms_db()
                nodes_db = self._nodes_db()

                # Aggregate term scores
                for term in query_terms:
                    raw = txn.get(term.encode(), db=terms_db)
                    if raw:
                        for nid, weight in json.loads(raw):
                            scores[nid] = scores.get(nid, 0) + weight

                # Resolve top-k nodes (deduplicated by node_id)
                results = []
                seen_ids: Set[str] = set()
                for nid, score in sorted(scores.items(), key=lambda x: -x[1])[:limit * 3]:
                    if nid in seen_ids:
                        continue
                    node_raw = txn.get(nid.encode(), db=nodes_db)
                    if not node_raw:
                        continue
                    node = json.loads(node_raw)
                    if label_filter and node.get("label") != label_filter:
                        continue
                    seen_ids.add(nid)
                    name = node.get("name", "")
                    results.append({
                        "node_id": nid, "label": node.get("label", ""),
                        "name": name, "snippet": node.get("snippet", ""),
                        "score": round(score, 3),
                    })
                    if len(results) >= limit:
                        break

            return results
        except Exception as e:
            logger.debug("[LMDB] Search failed: %s", e)
            return []

    def search_bm25(self, query: str, limit: int = 10, label_filter: str = "",
                    k1: float = 1.5, b: float = 0.75) -> List[Dict]:
        """BM25-scored search. Uses term postings + doc frequency for ranking.

        Parameters
        ----------
        k1 : float
            Term frequency saturation (default 1.5).
        b : float
            Length normalization (default 0.75).
        """
        query_terms = list(dict.fromkeys(_tokenize(query)))  # deduplicate, preserve order
        if not query_terms:
            return []

        if not self._env:
            return self.search(query, limit, label_filter)  # fallback

        # Extend query with bigrams so phrase matches score higher
        query_bigrams = _bigrams(query_terms)
        all_query_terms = list(dict.fromkeys(query_terms + query_bigrams))

        try:
            with self._env.begin() as txn:
                terms_db = self._terms_db()
                nodes_db = self._nodes_db()

                # Read real corpus stats for accurate avg_dl
                # total_docs excludes the reserved ~corpus_stats key
                stats_raw = txn.get(_CORPUS_STATS_KEY, db=nodes_db)
                if stats_raw:
                    corpus = json.loads(stats_raw)
                    total_docs = max(corpus.get("doc_count", 1), 1)
                    total_terms = corpus.get("total_term_count", total_docs * 5)
                    avg_dl = max(total_terms / total_docs, 1.0)  # guard against 0 → ZeroDivisionError
                else:
                    total_docs = max(txn.stat(nodes_db)["entries"] - 1, 1)  # -1 for corpus_stats key
                    avg_dl = 5.0  # fallback when stats not yet built

                # Collect postings and compute BM25 with correct per-term saturation.
                # BM25(d,Q) = Σ_t IDF_t * (tf_t*(k1+1)) / (tf_t + k1*(1-b+b*dl/avgdl))
                # Saturation must be applied per-term before summing, not to the aggregate.
                #
                # node_cache stores the full node dict (or None if not found) so that
                # the result-building pass below never needs a second LMDB read.
                bm25_scores: Dict[str, float] = {}
                node_cache: Dict[str, Optional[Dict]] = {}
                for term in all_query_terms:
                    raw = txn.get(term.encode(), db=terms_db)
                    if not raw:
                        continue
                    postings = json.loads(raw)
                    df = len(postings)
                    # Clamp to 0: IDF goes negative when df > total_docs (stale corpus stats).
                    idf = max(0.0, math.log((total_docs - df + 0.5) / (df + 0.5) + 1.0))

                    for nid, tf in postings:
                        if nid not in node_cache:
                            node_raw = txn.get(nid.encode(), db=nodes_db)
                            try:
                                node_cache[nid] = json.loads(node_raw) if node_raw else None
                            except Exception:
                                node_cache[nid] = None
                        node = node_cache[nid]
                        if node is None:
                            continue  # stale posting — node deleted but not yet pruned
                        dl = node.get("term_count", avg_dl)
                        # Per-term BM25 contribution with length normalization
                        tf_norm = (tf * (k1 + 1)) / (tf + k1 * (1 - b + b * dl / avg_dl))
                        bm25_scores[nid] = bm25_scores.get(nid, 0.0) + idf * tf_norm

                # Resolve top-k — reuse node_cache to avoid second LMDB reads
                results = []
                seen_ids: Set[str] = set()
                for nid, score in sorted(bm25_scores.items(), key=lambda x: -x[1])[:limit * 3]:
                    if nid in seen_ids:
                        continue
                    node = node_cache.get(nid)
                    if node is None:
                        continue
                    if label_filter and node.get("label") != label_filter:
                        continue
                    seen_ids.add(nid)
                    results.append({
                        "node_id": nid, "label": node.get("label", ""),
                        "name": node.get("name", ""), "snippet": node.get("snippet", ""),
                        "score": round(score, 4),
                    })
                    if len(results) >= limit:
                        break

                return results
        except Exception as e:
            logger.debug("[LMDB] BM25 search failed: %s", e)
            return self.search(query, limit, label_filter)

    def get_node(self, node_id: str) -> Optional[Dict]:
        """Get node metadata by ID."""
        if not self._env:
            return self._mem_nodes.get(node_id)
        try:
            with self._env.begin() as txn:
                raw = txn.get(node_id.encode(), db=self._nodes_db())
                return json.loads(raw) if raw else None
        except Exception:
            return None

    # ── Passage storage ─────────────────────────────────────

    def store_passage(self, node_id: str, full_text: str):
        """Store full passage text for a node."""
        if not self._env:
            return
        try:
            with self._env.begin(write=True) as txn:
                # Truncate string (not bytes) to avoid splitting multi-byte UTF-8 sequences
                stored = full_text[:5000].encode()
                txn.put(node_id.encode(), stored, db=self._passages_db_handle)
        except Exception:
            pass

    def get_passage(self, node_id: str) -> Optional[str]:
        """Get full passage text for a node."""
        if not self._env:
            return None
        try:
            with self._env.begin() as txn:
                raw = txn.get(node_id.encode(), db=self._passages_db_handle)
                return raw.decode() if raw else None
        except Exception:
            return None

    # ── Edge/graph storage ─────────────────────────────────

    def store_edges(self, node_id: str, edges: List[Dict]):
        """Store edges for a node: [{target_id, label, target_name, target_type}]."""
        if not self._env:
            return
        try:
            with self._env.begin(write=True) as txn:
                txn.put(node_id.encode(), json.dumps(edges).encode(), db=self._edges_db_handle)
        except Exception:
            pass

    def get_edges(self, node_id: str) -> List[Dict]:
        """Get stored edges for a node."""
        if not self._env:
            return []
        try:
            with self._env.begin() as txn:
                raw = txn.get(node_id.encode(), db=self._edges_db_handle)
                return json.loads(raw) if raw else []
        except Exception:
            return []

    # ── Semantic neighbor storage ──────────────────────────

    def store_neighbors(self, node_id: str, neighbor_ids: List[str]):
        """Store pre-computed semantic neighbors for a node."""
        if not self._env:
            return
        try:
            with self._env.begin(write=True) as txn:
                txn.put(node_id.encode(), json.dumps(neighbor_ids).encode(), db=self._neighbors_db_handle)
        except Exception:
            pass

    def get_neighbors(self, node_id: str) -> List[str]:
        """Get semantic neighbors for a node."""
        if not self._env:
            return []
        try:
            with self._env.begin() as txn:
                raw = txn.get(node_id.encode(), db=self._neighbors_db_handle)
                return json.loads(raw) if raw else []
        except Exception:
            return []

    # ── Hybrid search (all signals) ────────────────────────

    def hybrid_search(self, query: str, limit: int = 10, label_filter: str = "") -> List[Dict]:
        """Full hybrid search: BM25 + graph expansion + passages.

        Returns rich results with full text, connected entities, and relevance scores.
        All from LMDB — no network calls, microseconds per signal.
        """
        # Signal 1: BM25 keyword relevance + quality + relevance filter
        bm25_results = self.search_bm25(query, limit=limit * 3, label_filter=label_filter)

        if not bm25_results:
            return []

        # Filter: require meaningful query term overlap (not just one common word like "US").
        # Use the same stemmed tokens as BM25 so "searching" matches a node indexed with "search".
        query_stems = set(_tokenize(query))
        meaningful_stems = {t for t in query_stems if len(t) > 3}
        min_matches = max(1, len(meaningful_stems) // 2)
        filtered = []
        for r in bm25_results:
            text_stems = set(_tokenize(r.get("name", "") + " " + r.get("snippet", "")))
            meaningful_matching = sum(1 for t in meaningful_stems if t in text_stems)
            if meaningful_matching >= min_matches:
                r["_relevance"] = sum(1 for t in query_stems if t in text_stems)
                filtered.append(r)
        # Sort filtered by relevance then BM25 score
        filtered.sort(key=lambda r: (r.get("_relevance", 0), r.get("score", 0)), reverse=True)
        bm25_results = filtered or bm25_results[:limit]

        # Signal 2: Graph expansion — find connected entities for top results
        expanded_entities: Dict[str, Dict] = {}  # name → {label, node_id}
        for r in bm25_results[:5]:
            edges = self.get_edges(r["node_id"])
            for edge in edges[:10]:
                ename = edge.get("target_name", "")
                if ename and ename not in expanded_entities:
                    expanded_entities[ename] = {
                        "node_id": edge.get("target_id", ""),
                        "label": edge.get("target_type", ""),
                        "name": ename,
                        "via": edge.get("label", ""),
                    }

        # Signal 3: Enrich with full passage text
        for r in bm25_results:
            passage = self.get_passage(r["node_id"])
            if passage:
                r["full_text"] = passage
            # Also enrich snippet if it's short
            if not r.get("snippet") or len(r.get("snippet", "")) < 30:
                r["snippet"] = (passage or r.get("name", ""))[:200]

        # Signal 4: Semantic neighbors — find similar nodes (filter out noise)
        neighbor_results = []
        existing_names = {r.get("name", "").lower() for r in bm25_results}
        for r in bm25_results[:3]:
            neighbors = self.get_neighbors(r["node_id"])
            for nid in neighbors[:3]:
                node = self.get_node(nid)
                if not node:
                    continue
                # Filter: only content nodes (not Document/Link/system noise)
                if node.get("label", "") not in _CONTENT_LABELS:
                    continue
                # Dedup
                if node.get("name", "").lower() in existing_names:
                    continue
                existing_names.add(node.get("name", "").lower())
                # Semantic neighbors are pre-validated by Qdrant — no keyword filter needed
                neighbor_results.append({
                    "node_id": nid, "label": node.get("label", ""),
                    "name": node.get("name", ""), "snippet": node.get("snippet", ""),
                        "score": 0.1, "via": "semantic_neighbor",
                    })

        # Merge: BM25 results + expanded entities + semantic neighbors
        final = []
        seen_names: Set[str] = set()

        # Primary: BM25 results (highest relevance)
        for r in bm25_results:
            name_key = r.get("name", "").strip().lower()[:60]
            if name_key not in seen_names:
                seen_names.add(name_key)
                final.append(r)

        # Secondary: Connected entities (graph expansion) — show actual name + relationship
        for ename, edata in list(expanded_entities.items())[:5]:
            name_key = ename.strip().lower()[:60]
            if name_key not in seen_names and len(ename) > 3:
                seen_names.add(name_key)
                # Get full node data for richer display
                enode = self.get_node(edata["node_id"])
                esnippet = ""
                if enode:
                    esnippet = enode.get("snippet", "")
                passage = self.get_passage(edata["node_id"])
                display = passage[:200] if passage else esnippet or ename
                final.append({
                    "node_id": edata["node_id"], "label": edata["label"],
                    "name": ename, "snippet": display,
                    "full_text": passage if passage else None,
                    "score": 0.3, "via": f"connected ({edata['via']})",
                })

        # Tertiary: Semantic neighbors
        for r in neighbor_results:
            name_key = r.get("name", "").strip().lower()[:60]
            if name_key not in seen_names:
                seen_names.add(name_key)
                final.append(r)

        # Quaternary: fuzzy candidates (typo tolerance) — add any not already seen
        fuzzy = self.fuzzy_candidates(query, limit=limit)
        for r in fuzzy:
            name_key = r.get("name", "").strip().lower()[:60]
            if name_key not in seen_names:
                seen_names.add(name_key)
                final.append({
                    "node_id": r["node_id"], "label": r["label"],
                    "name": r["name"], "snippet": r["snippet"],
                    "score": r["similarity"] * 0.5,  # lower than BM25 results
                    "via": "fuzzy_match",
                })

        return final[:limit]

    def get_idf(self, terms: List[str]) -> Dict[str, float]:
        """Get IDF scores for terms directly from the index. O(K) not O(N).

        Uses the same Robertson-Sparck Jones formula as search_bm25:
          IDF = max(0, log((N - df + 0.5) / (df + 0.5) + 1))
        Terms not in the index are treated as df=0 (maximum IDF).
        """
        result = {}
        if not self._env:
            total = max(len(self._mem_nodes), 1)
            for t in terms:
                df = len(self._mem_terms.get(t, []))
                result[t] = max(0.0, math.log((total - df + 0.5) / (df + 0.5) + 1.0))
            return result
        try:
            with self._env.begin() as txn:
                stats_raw = txn.get(_CORPUS_STATS_KEY, db=self._nodes_db())
                if stats_raw:
                    total = max(json.loads(stats_raw).get("doc_count", 1), 1)
                else:
                    total = max(txn.stat(self._nodes_db())["entries"] - 1, 1)
                terms_db = self._terms_db()
                for t in terms:
                    raw = txn.get(t.encode(), db=terms_db)
                    df = len(json.loads(raw)) if raw else 0
                    result[t] = max(0.0, math.log((total - df + 0.5) / (df + 0.5) + 1.0))
        except Exception:
            for t in terms:
                result[t] = 2.0
        return result

    def fuzzy_candidates(
        self,
        query: str,
        limit: int = 10,
        min_similarity: float = 0.5,
    ) -> List[Dict]:
        """Return nodes whose indexed tokens share ≥ min_similarity trigrams with query tokens.

        Similarity per token pair = shared_trigrams / query_token_trigrams.
        A node is included if any of its tokens reaches min_similarity against
        any query token. Results are ordered by descending max similarity.

        Used to augment BM25 results with typo-tolerant candidates.
        """
        query_terms = list(dict.fromkeys(_tokenize(query)))
        if not query_terms or not self._env:
            return []

        # Build query trigram sets per token
        query_tg_sets: Dict[str, Set[str]] = {
            t: set(_char_trigrams(t)) for t in query_terms
        }

        try:
            with self._env.begin() as txn:
                trigrams_db = self._trigrams_db()
                if trigrams_db is None:
                    return []
                nodes_db = self._nodes_db()

                # Count how many query trigrams each candidate node matches
                candidate_scores: Dict[str, float] = {}  # node_id → best similarity
                for qt, qt_tgs in query_tg_sets.items():
                    qt_count = len(qt_tgs)
                    if qt_count == 0:
                        continue
                    matches_per_node: Dict[str, int] = {}
                    for tg in qt_tgs:
                        raw = txn.get(tg.encode(), db=trigrams_db)
                        if raw:
                            for nid in json.loads(raw):
                                matches_per_node[nid] = matches_per_node.get(nid, 0) + 1
                    for nid, match_count in matches_per_node.items():
                        sim = match_count / qt_count
                        if sim >= min_similarity:
                            candidate_scores[nid] = max(candidate_scores.get(nid, 0.0), sim)

                # Resolve node metadata for top candidates
                results = []
                for nid, sim in sorted(candidate_scores.items(), key=lambda x: -x[1])[:limit * 2]:
                    node_raw = txn.get(nid.encode(), db=nodes_db)
                    if not node_raw:
                        continue
                    node = json.loads(node_raw)
                    results.append({
                        "node_id": nid,
                        "label": node.get("label", ""),
                        "name": node.get("name", ""),
                        "snippet": node.get("snippet", ""),
                        "similarity": round(sim, 3),
                    })
                    if len(results) >= limit:
                        break
                return results
        except Exception as e:
            logger.debug("[LMDB] fuzzy_candidates failed: %s", e)
            return []

    def delete_node(self, node_id: str) -> bool:
        """Remove a node and all its term postings from the index.

        O(K) where K = number of terms in the node, using the reverse index
        (node_terms db) to find only the affected posting lists.
        Previously O(total_terms) full scan — catastrophic under cascade invalidation.

        Returns True if the node was present and removed, False if not found.
        """
        if not self._env:
            # In-memory fallback
            if node_id not in self._mem_nodes:
                return False
            old = self._mem_nodes.pop(node_id)
            self._mem_corpus_stats["total_term_count"] -= old.get("term_count", 0)
            self._mem_corpus_stats["doc_count"] = max(0, self._mem_corpus_stats["doc_count"] - 1)
            node_term_list = self._mem_node_terms.pop(node_id, [])
            for term in node_term_list:
                if term in self._mem_terms:
                    self._mem_terms[term] = [
                        (nid, w) for nid, w in self._mem_terms[term] if nid != node_id
                    ]
                    if not self._mem_terms[term]:
                        del self._mem_terms[term]
            return True

        try:
            with self._env.begin(write=True) as txn:
                nodes_db = self._nodes_db()
                terms_db = self._terms_db()
                node_terms_db = self._node_terms_db()

                # Check node exists and read metadata (need term_count for corpus stats)
                node_raw = txn.get(node_id.encode(), db=nodes_db)
                if node_raw is None:
                    return False

                # Decrement corpus stats
                try:
                    node_meta = json.loads(node_raw)
                    stats_raw = txn.get(_CORPUS_STATS_KEY, db=nodes_db)
                    corpus = json.loads(stats_raw) if stats_raw else {"total_term_count": 0, "doc_count": 0}
                    corpus["total_term_count"] -= node_meta.get("term_count", 0)
                    corpus["doc_count"] = max(0, corpus["doc_count"] - 1)
                    txn.put(_CORPUS_STATS_KEY, json.dumps(corpus).encode(), db=nodes_db)
                except Exception:
                    pass

                # Look up this node's term list from the reverse index — O(1)
                node_terms_raw = txn.get(node_id.encode(), db=node_terms_db)
                if node_terms_raw:
                    term_list = json.loads(node_terms_raw)
                else:
                    # No reverse index entry (node was indexed before this feature).
                    # Fall back to full scan for this node only.
                    term_list = None

                # Remove from nodes and reverse-index DBs
                txn.delete(node_id.encode(), db=nodes_db)
                txn.delete(node_id.encode(), db=node_terms_db)

                if term_list is not None:
                    # Fast path: only touch the K terms this node contributed to
                    for term in term_list:
                        key = term.encode()
                        raw = txn.get(key, db=terms_db)
                        if not raw:
                            continue
                        try:
                            postings = json.loads(raw)
                            new_postings = [p for p in postings if p[0] != node_id]
                            if new_postings:
                                txn.put(key, json.dumps(new_postings).encode(), db=terms_db)
                            else:
                                txn.delete(key, db=terms_db)
                        except Exception:
                            pass

                    # Remove node from trigram postings
                    trigrams_db = self._trigrams_db()
                    if trigrams_db is not None:
                        removed_tgs: Set[str] = set()
                        for term in term_list:
                            if "__" in term:
                                continue
                            for tg in _char_trigrams(term):
                                if tg in removed_tgs:
                                    continue
                                removed_tgs.add(tg)
                                tg_key = tg.encode()
                                existing_raw = txn.get(tg_key, db=trigrams_db)
                                if existing_raw:
                                    node_set = [n for n in json.loads(existing_raw) if n != node_id]
                                    if node_set:
                                        txn.put(tg_key, json.dumps(node_set).encode(), db=trigrams_db)
                                    else:
                                        txn.delete(tg_key, db=trigrams_db)
                else:
                    # Legacy fallback: full scan (only for pre-reverse-index nodes)
                    logger.debug("[LMDB] delete_node %s: no reverse index, doing full terms scan", node_id)
                    cursor = txn.cursor(db=terms_db)
                    to_update: List[Tuple[bytes, bytes]] = []
                    to_delete_keys: List[bytes] = []
                    if cursor.first():
                        while True:
                            key = cursor.key()
                            raw = cursor.value()
                            try:
                                postings = json.loads(raw)
                                new_postings = [p for p in postings if p[0] != node_id]
                                if len(new_postings) != len(postings):
                                    if new_postings:
                                        to_update.append((key, json.dumps(new_postings).encode()))
                                    else:
                                        to_delete_keys.append(key)
                            except Exception:
                                pass
                            if not cursor.next():
                                break
                    for key, val in to_update:
                        txn.put(key, val, db=terms_db)
                    for key in to_delete_keys:
                        txn.delete(key, db=terms_db)

            return True
        except Exception as e:
            logger.warning("[LMDB] delete_node(%s) failed: %s", node_id, e)
            return False

    def stats(self) -> Dict:
        """Return index statistics."""
        if not self._env:
            return {"nodes": len(self._mem_nodes), "terms": len(self._mem_terms), "backend": "memory"}
        try:
            with self._env.begin() as txn:
                terms_stat = txn.stat(self._terms_db())
                nodes_stat = txn.stat(self._nodes_db())
                raw_node_entries = nodes_stat["entries"]
                # Subtract the reserved ~corpus_stats key so the count reflects real nodes
                node_count = max(0, raw_node_entries - 1) if raw_node_entries > 0 else 0
                # Read corpus stats for avg_dl if available
                stats_raw = txn.get(_CORPUS_STATS_KEY, db=self._nodes_db())
                corpus = json.loads(stats_raw) if stats_raw else {}
                return {
                    "nodes": node_count,
                    "terms": terms_stat["entries"],
                    "backend": "lmdb",
                    "path": self._path,
                    "map_size_mb": self._map_size // (1024 * 1024),
                    "avg_dl": round(corpus.get("total_term_count", 0) / max(corpus.get("doc_count", 1), 1), 1),
                    "doc_count": corpus.get("doc_count", 0),
                }
        except Exception:
            return {"backend": "lmdb", "error": "stats unavailable"}

    def close(self):
        """Close the LMDB environment."""
        if self._env:
            self._env.close()
            self._env = None


# ── Module-level cache of LMDBIndex instances ─────────────────

import threading as _threading
_lmdb_indexes: Dict[str, LMDBIndex] = {}
_lmdb_index_lock = _threading.Lock()


def get_lmdb_index(graph_name: str) -> LMDBIndex:
    """Get or create an LMDBIndex for a graph. Thread-safe."""
    # Fast path — no lock needed for reads once entry exists
    idx = _lmdb_indexes.get(graph_name)
    if idx is not None:
        return idx
    # Slow path — serialize creation to avoid TOCTOU race
    with _lmdb_index_lock:
        idx = _lmdb_indexes.get(graph_name)
        if idx is None:
            path = f"contextcore_data/lmdb_index/{graph_name}"
            idx = LMDBIndex(path)
            _lmdb_indexes[graph_name] = idx
    return idx


def build_lmdb_index(db, graph_name: str) -> LMDBIndex:
    """Build LMDB index from a graph (full rebuild). Returns the index."""
    idx = get_lmdb_index(graph_name)
    start = time.time()

    try:
        all_nodes = db.get_all_nodes()
    except Exception:
        all_nodes = []

    batch = []
    for node in all_nodes:
        if isinstance(node, dict):
            batch.append(node)
        else:
            batch.append({
                "id": str(getattr(node, "id", "")),
                "label": getattr(node, "label", getattr(node, "node_type", "")),
                "properties": getattr(node, "properties", {}) or {},
            })

    if batch:
        idx.index_batch(batch)

    elapsed = (time.time() - start) * 1000
    stats = idx.stats()
    logger.info("[LMDB] Built index for '%s': %d nodes, %d terms in %.0fms",
                graph_name, stats.get("nodes", 0), stats.get("terms", 0), elapsed)
    return idx


def lmdb_delete_node(namespace: str, node_id: str) -> None:
    """Best-effort LMDB cleanup for a deleted graph node. Never raises."""
    try:
        if namespace:
            get_lmdb_index(namespace).delete_node(str(node_id))
    except Exception:
        pass


def lmdb_reindex_node(namespace: str, node_id: str, label: str, props: dict) -> None:
    """Best-effort LMDB re-index after a node create or update. Never raises."""
    try:
        if namespace:
            get_lmdb_index(namespace).index_node(str(node_id), label, dict(props))
    except Exception:
        pass


def remove_lmdb_index(graph_name: str) -> None:
    """Close and delete the LMDB index for a dropped graph. Never raises.

    Evicts the singleton, closes the LMDB environment, and deletes the
    directory on disk so a fresh graph reusing the same name starts clean.
    """
    try:
        import shutil
        from pathlib import Path

        with _lmdb_index_lock:
            idx = _lmdb_indexes.pop(graph_name, None)
            if idx is not None:
                try:
                    idx.close()
                except Exception:
                    pass

        path = f"contextcore_data/lmdb_index/{graph_name}"
        if Path(path).exists():
            shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass
