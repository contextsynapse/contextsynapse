"""Embedding cache — avoids re-computing Ollama embeddings for repeated queries.

Stores query text → embedding vector in LMDB for instant retrieval.
A cache hit saves 3-5 seconds (the Ollama embedding call).

Usage:
    cache = EmbeddingCache("contextcore_data/embedding_cache")
    vec = cache.get("iran ceasefire")  # None on miss
    if vec is None:
        vec = ollama_embed("iran ceasefire")  # 3-5 seconds
        cache.put("iran ceasefire", vec)
    # Next time: cache.get() returns instantly
"""

import hashlib
import json
import logging
import struct
import time
from pathlib import Path
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

try:
    import lmdb
except ImportError:
    lmdb = None


class EmbeddingCache:
    """LMDB-backed embedding vector cache.

    Key: SHA-256 hash of normalized query text (32 bytes)
    Value: packed float32 array (dim * 4 bytes)
    """

    def __init__(self, path: str = "contextcore_data/embedding_cache",
                 map_size: int = 128 * 1024 * 1024):
        self._path = path
        self._env = None
        self._mem: Dict[str, List[float]] = {}  # fallback
        self._hits = 0
        self._misses = 0

        if lmdb is None:
            return

        try:
            Path(path).mkdir(parents=True, exist_ok=True)
            self._env = lmdb.open(path, map_size=map_size)
        except Exception as e:
            logger.debug("[EMBED_CACHE] LMDB open failed: %s", e)

    @staticmethod
    def _key(text: str) -> bytes:
        """Deterministic key from query text."""
        normalized = text.strip().lower()
        return hashlib.sha256(normalized.encode()).digest()

    @staticmethod
    def _pack(vector: List[float]) -> bytes:
        """Pack float list to bytes."""
        return struct.pack(f"{len(vector)}f", *vector)

    @staticmethod
    def _unpack(data: bytes, dim: int = 0) -> List[float]:
        """Unpack bytes to float list."""
        if dim == 0:
            dim = len(data) // 4
        return list(struct.unpack(f"{dim}f", data))

    def get(self, text: str) -> Optional[List[float]]:
        """Get cached embedding. Returns None on miss."""
        key = self._key(text)

        if not self._env:
            vec = self._mem.get(key.hex())
            if vec:
                self._hits += 1
            else:
                self._misses += 1
            return vec

        try:
            with self._env.begin() as txn:
                raw = txn.get(key)
                if raw:
                    self._hits += 1
                    return self._unpack(raw)
                self._misses += 1
                return None
        except Exception:
            self._misses += 1
            return None

    def put(self, text: str, vector: List[float]):
        """Cache an embedding."""
        key = self._key(text)
        packed = self._pack(vector)

        if not self._env:
            self._mem[key.hex()] = vector
            return

        try:
            with self._env.begin(write=True) as txn:
                txn.put(key, packed)
        except Exception as e:
            logger.debug("[EMBED_CACHE] Put failed: %s", e)

    def stats(self) -> Dict:
        """Cache statistics."""
        total = self._hits + self._misses
        hit_rate = (self._hits / total * 100) if total > 0 else 0
        result = {"hits": self._hits, "misses": self._misses, "hit_rate": f"{hit_rate:.0f}%"}
        if self._env:
            try:
                with self._env.begin() as txn:
                    stat = txn.stat()
                    result["entries"] = stat["entries"]
                    result["backend"] = "lmdb"
            except Exception:
                result["backend"] = "lmdb"
        else:
            result["entries"] = len(self._mem)
            result["backend"] = "memory"
        return result


# Module-level singleton
_cache: Optional[EmbeddingCache] = None


def get_embedding_cache() -> EmbeddingCache:
    """Get the global embedding cache."""
    global _cache
    if _cache is None:
        _cache = EmbeddingCache()
    return _cache
