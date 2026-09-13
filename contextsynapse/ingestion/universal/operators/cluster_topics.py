"""ClusterTopicsOperator -- cluster chunks into topic groups."""
from __future__ import annotations

import logging
import re
from collections import Counter, defaultdict
from typing import Any, Dict, List, TYPE_CHECKING

from .base import StageOperator

if TYPE_CHECKING:
    from ..ingest_content import Chunk
    from ..stage_executor import GraphContext

logger = logging.getLogger(__name__)

_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "shall", "must", "need",
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us",
    "my", "your", "his", "its", "our", "their", "this", "that", "these",
    "those", "what", "which", "who", "whom", "whose", "when", "where",
    "why", "how", "all", "each", "every", "both", "few", "more", "most",
    "other", "some", "such", "no", "nor", "not", "only", "own", "same",
    "so", "than", "too", "very", "just", "don", "now", "also", "but",
    "and", "or", "if", "then", "because", "as", "until", "while", "of",
    "at", "by", "for", "with", "about", "against", "between", "through",
    "during", "before", "after", "above", "below", "to", "from", "up",
    "down", "in", "out", "on", "off", "over", "under", "again", "further",
    "once", "here", "there", "when", "where", "why", "how", "any",
    "been", "get", "got", "like", "going", "think", "know", "want", "look",
    "use", "try", "yeah", "ok", "okay", "yes", "no", "well", "right",
    "thing", "things", "way", "much", "many", "one", "two", "really",
    "make", "made", "let", "take", "go", "see", "come", "still",
})


def _tokenize(text: str) -> List[str]:
    """Extract lowercase words, filtering stop words and short tokens."""
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    return [w for w in words if w not in _STOP_WORDS]


def _keyword_cluster(texts: List[str], max_topics: int) -> List[List[int]]:
    """Simple keyword-frequency clustering (no numpy required).

    Groups texts that share dominant keywords into clusters.
    Returns list of clusters, each cluster is a list of text indices.
    """
    # Build per-text keyword sets
    text_keywords: List[set] = []
    global_freq: Counter = Counter()
    for text in texts:
        tokens = _tokenize(text)
        kw_set = set(tokens)
        text_keywords.append(kw_set)
        global_freq.update(kw_set)

    # Pick top keywords as cluster seeds (exclude very common words)
    n_texts = len(texts)
    # A keyword is a good topic seed if it appears in multiple texts but not all
    seed_candidates = [
        (word, count) for word, count in global_freq.most_common(50)
        if 2 <= count <= n_texts * 0.8
    ]

    if not seed_candidates:
        # Fallback: use most frequent keywords
        seed_candidates = global_freq.most_common(max_topics)

    # Take top seeds as topic anchors
    seeds = [word for word, _ in seed_candidates[:max_topics]]

    # Assign each text to its best-matching seed
    clusters: Dict[int, List[int]] = defaultdict(list)
    assigned = set()

    for idx, kw_set in enumerate(text_keywords):
        best_seed = -1
        best_overlap = 0
        for si, seed in enumerate(seeds):
            if seed in kw_set:
                # Count total overlap with this seed's concept
                overlap = len(kw_set & {seed})
                if overlap > best_overlap:
                    best_overlap = overlap
                    best_seed = si
        if best_seed >= 0:
            clusters[best_seed].append(idx)
            assigned.add(idx)

    # Assign unassigned texts to nearest cluster or create misc cluster
    unassigned = [i for i in range(n_texts) if i not in assigned]
    if unassigned:
        if clusters:
            # Put in largest cluster
            largest = max(clusters, key=lambda k: len(clusters[k]))
            clusters[largest].extend(unassigned)
        else:
            clusters[0] = list(range(n_texts))

    return list(clusters.values())


def _derive_topic_name(texts: List[str], chunk_indices: List[int], chunks=None) -> str:
    """Derive a readable topic name from the cluster's content.

    Strategy:
    1. If chunks have conversation titles → use the most common title
    2. If user questions exist → use the first user question as topic
    3. Fallback → top 3 keywords joined as a phrase (capitalized)
    """
    # Try conversation titles first
    if chunks:
        titles = []
        user_questions = []
        for ci in chunk_indices:
            if ci < len(chunks):
                meta = chunks[ci].metadata if hasattr(chunks[ci], 'metadata') else {}
                title = meta.get("conversation_title", meta.get("title", ""))
                if title and title != "New chat" and len(title) > 3:
                    titles.append(title)
                role = meta.get("role", "")
                if role == "user" and ci < len(texts):
                    q = texts[ci].strip()[:100]
                    if len(q) > 10:
                        user_questions.append(q)

        # Use most common conversation title
        if titles:
            title_freq = Counter(titles)
            best_title = title_freq.most_common(1)[0][0]
            return best_title[:80]

        # Use first user question
        if user_questions:
            return user_questions[0][:80]

    # Fallback: use first meaningful text from the cluster as topic name
    for ci in chunk_indices:
        if ci < len(texts):
            text = texts[ci].strip()
            # Skip very short or generic text
            if len(text) > 15:
                # Use first sentence as topic name
                first_sentence = text.split('.')[0].split('?')[0].split('\n')[0].strip()
                if len(first_sentence) > 10:
                    return first_sentence[:80]

    # Last resort: top keywords
    combined = " ".join(texts[i] for i in chunk_indices if i < len(texts))
    tokens = _tokenize(combined)
    freq = Counter(tokens)
    top = [w.title() for w, _ in freq.most_common(3) if w.lower() not in ("none", "general", "user", "session", "agent", "start")]
    if top:
        return ", ".join(top)
    return "Miscellaneous"


class ClusterTopicsOperator(StageOperator):
    """Cluster chunks into topics and create Topic nodes + edges."""

    name = "cluster_topics"

    def __init__(self, method: str = "llm_with_fallback", max_topics: int = 7):
        self.method = method
        self.max_topics = max_topics

    def process(self, chunks: List["Chunk"], graph_ctx: "GraphContext") -> List["Chunk"]:
        if len(chunks) < 3:
            return chunks

        texts = [c.content for c in chunks]
        clusters = self._cluster(texts)

        if not clusters:
            return chunks

        # Build topic_id lookup: chunk_index -> topic_node_id
        chunk_topic_map: Dict[int, str] = {}

        for cluster_indices in clusters:
            if not cluster_indices:
                continue

            topic_name = _derive_topic_name(texts, cluster_indices, chunks=chunks)
            # Build a meaningful description from user questions in this cluster
            user_snippets = []
            for ci in cluster_indices:
                if ci < len(chunks):
                    meta = chunks[ci].metadata if hasattr(chunks[ci], 'metadata') else {}
                    if meta.get("role") == "user":
                        snippet = texts[ci].strip().split('\n')[0][:120]
                        if len(snippet) > 10 and snippet not in user_snippets:
                            user_snippets.append(snippet)
            if user_snippets:
                desc = "; ".join(user_snippets[:3])
            else:
                desc = f"Conversation topic with {len(cluster_indices)} messages"
            topic_id = graph_ctx.add_node("Topic", {
                "name": topic_name,
                "description": desc,
                "chunk_count": len(cluster_indices),
            })

            for ci in cluster_indices:
                if ci < len(chunks):
                    chunk_topic_map[ci] = topic_id
                    turn_node_id = chunks[ci].metadata.get("turn_node_id", "")
                    if turn_node_id:
                        graph_ctx.add_edge(turn_node_id, topic_id, "RAISES")

                    # Link topic to entities mentioned in this chunk
                    for eid in chunks[ci].metadata.get("entity_node_ids", []):
                        graph_ctx.add_edge(topic_id, eid, "INVOLVES")

        # Store topic IDs on chunks
        for idx, chunk in enumerate(chunks):
            tid = chunk_topic_map.get(idx)
            if tid:
                chunk.metadata.setdefault("topic_ids", []).append(tid)

        return chunks

    def _cluster(self, texts: List[str]) -> List[List[int]]:
        """Run clustering. Strategy: embedding → TF-IDF → keyword fallback.

        Embedding clustering uses actual semantic vectors (from Ollama/OpenAI)
        for high-quality grouping. Falls back to TF-IDF then keywords.
        """
        # 1. Try embedding-based clustering (best quality — semantic similarity)
        if self.method in ("llm_with_fallback", "embedding", "tfidf"):
            try:
                result = self._embedding_cluster(texts)
                if result:
                    logger.info("[TOPICS] Embedding-based clustering: %d texts → %d clusters", len(texts), len(result))
                    return result
            except Exception as e:
                logger.debug("[TOPICS] Embedding clustering failed: %s", e)

        # 2. Try TF-IDF (keyword vectors + k-means)
        if self.method in ("llm_with_fallback", "tfidf"):
            try:
                return self._tfidf_cluster(texts)
            except ImportError:
                pass

        # 3. Simple keyword frequency fallback
        return _keyword_cluster(texts, self.max_topics)

    def _embedding_cluster(self, texts: List[str]) -> List[List[int]]:
        """Cluster using actual embedding vectors — semantic similarity, not keywords.

        Uses the Ollama/OpenAI embedding service to embed each turn's content,
        then k-means on the resulting vectors. Produces much better topic grouping
        than keyword overlap because semantically similar turns cluster together
        even when they use different words.
        """
        import numpy as np
        import math

        # Try embedding service (Ollama, OpenAI, etc.)
        vectors = []
        try:
            from ....models.embedding_service import embedding_service
            result = embedding_service.embed_batch([t[:500] for t in texts])
            if result.get("success") and result.get("embeddings"):
                vectors = result["embeddings"]
                logger.info("[TOPICS] Embedding-based clustering: %d vectors (dim=%d)",
                            len(vectors), len(vectors[0]) if vectors else 0)
        except Exception as e:
            logger.debug("[TOPICS] Embedding service unavailable: %s", e)

        # Fallback: try embedding cache (already computed vectors)
        if not vectors:
            try:
                from ....search.embedding_cache import get_embedding_cache
                cache = get_embedding_cache()
                cached_vecs = []
                cached_indices = []
                for i, text in enumerate(texts):
                    vec = cache.get(text[:500])
                    if vec is not None:
                        cached_vecs.append(vec)
                        cached_indices.append(i)
                if len(cached_vecs) >= max(3, len(texts) * 0.6):
                    vectors = cached_vecs
                    # Map back to original indices later
            except Exception:
                pass

        if not vectors or len(vectors) < 3:
            return None

        mat = np.array(vectors, dtype=np.float32)

        # Normalize for cosine similarity
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms

        # K-means on embedding vectors
        n = mat.shape[0]
        k = min(self.max_topics, max(2, int(math.sqrt(n))))
        labels = self._kmeans(mat, k)

        # Group by label
        clusters: Dict[int, List[int]] = defaultdict(list)
        for idx, label_val in enumerate(labels):
            clusters[label_val].append(idx)

        result = [v for v in clusters.values() if len(v) >= 1]
        return result if result else None

    def _tfidf_cluster(self, texts: List[str]) -> List[List[int]]:
        """TF-IDF vectorization + k-means clustering (requires numpy)."""
        import numpy as np

        n = len(texts)
        # Build vocabulary
        doc_tokens = [_tokenize(t) for t in texts]
        vocab: Dict[str, int] = {}
        df: Counter = Counter()

        for tokens in doc_tokens:
            unique = set(tokens)
            for w in unique:
                if w not in vocab:
                    vocab[w] = len(vocab)
                df[w] += 1

        if not vocab:
            return _keyword_cluster(texts, self.max_topics)

        # Build TF-IDF matrix
        import math
        v = len(vocab)
        mat = np.zeros((n, v), dtype=np.float32)
        for i, tokens in enumerate(doc_tokens):
            tf: Counter = Counter(tokens)
            for word, count in tf.items():
                j = vocab[word]
                idf = math.log(n / (1 + df[word]))
                mat[i, j] = count * idf

        # L2 normalize
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms

        # K-means
        k = min(self.max_topics, max(2, int(math.sqrt(n))))
        labels = self._kmeans(mat, k)

        # Group by label
        clusters: Dict[int, List[int]] = defaultdict(list)
        for idx, label in enumerate(labels):
            clusters[label].append(idx)

        return [v for v in clusters.values() if len(v) >= 1]

    @staticmethod
    def _kmeans(mat, k: int, max_iter: int = 20) -> List[int]:
        """Lightweight k-means on a numpy matrix."""
        import numpy as np

        n = mat.shape[0]
        k = min(k, n)

        rng = np.random.RandomState(42)
        centroids = [mat[rng.randint(n)]]
        for _ in range(k - 1):
            dists = np.array([
                min(np.dot(mat[i] - c, mat[i] - c) for c in centroids)
                for i in range(n)
            ])
            probs = dists / (dists.sum() + 1e-12)
            idx = rng.choice(n, p=probs)
            centroids.append(mat[idx])
        centroids = np.array(centroids)

        labels = np.zeros(n, dtype=int)
        for _ in range(max_iter):
            sims = mat @ centroids.T
            new_labels = np.argmax(sims, axis=1)
            if np.array_equal(new_labels, labels):
                break
            labels = new_labels
            for j in range(k):
                members = mat[labels == j]
                if len(members) > 0:
                    c = members.mean(axis=0)
                    norm = np.linalg.norm(c)
                    centroids[j] = c / norm if norm > 0 else c

        return labels.tolist()
