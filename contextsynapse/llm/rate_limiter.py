"""
LLM Rate Limiter — Per-provider concurrency control.

Prevents overwhelming LLM providers with too many simultaneous requests.
Uses threading.Semaphore for sync code (stage executor runs in threads).

Usage:
    from contextsynapse.llm.rate_limiter import get_limiter
    limiter = get_limiter("groq")
    with limiter:
        result = llm.generate(...)
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Dict

logger = logging.getLogger(__name__)

DEFAULT_CONCURRENCY = int(os.environ.get("CONTEXTSYNAPSE_LLM_CONCURRENCY") or os.environ.get("AICONTEXTDB_LLM_CONCURRENCY", "10"))


class LLMRateLimiter:
    """Per-provider semaphore for LLM request concurrency control."""

    def __init__(self, max_concurrent: int = DEFAULT_CONCURRENCY):
        self._max = max_concurrent
        self._semaphores: Dict[str, threading.Semaphore] = {}
        self._lock = threading.Lock()
        self._stats: Dict[str, Dict] = {}  # provider → {active, total, rejected}

    def _get_semaphore(self, provider: str) -> threading.Semaphore:
        if provider not in self._semaphores:
            with self._lock:
                if provider not in self._semaphores:
                    self._semaphores[provider] = threading.Semaphore(self._max)
                    self._stats[provider] = {"active": 0, "total": 0, "rejected": 0}
        return self._semaphores[provider]

    def acquire(self, provider: str, timeout: float = 120.0) -> bool:
        """Acquire a slot for the provider. Returns False if timeout."""
        sem = self._get_semaphore(provider)
        acquired = sem.acquire(timeout=timeout)
        if acquired:
            with self._lock:
                self._stats[provider]["active"] += 1
                self._stats[provider]["total"] += 1
        else:
            with self._lock:
                self._stats[provider]["rejected"] += 1
            logger.warning("[LLM_LIMIT] %s: request rejected (timeout=%.0fs, max=%d)",
                           provider, timeout, self._max)
        return acquired

    def release(self, provider: str):
        """Release a slot for the provider."""
        sem = self._get_semaphore(provider)
        sem.release()
        with self._lock:
            self._stats[provider]["active"] = max(0, self._stats[provider]["active"] - 1)

    def get_stats(self) -> Dict[str, Dict]:
        """Get concurrency stats per provider."""
        with self._lock:
            return {p: dict(s) for p, s in self._stats.items()}

    class _Context:
        """Context manager for acquire/release."""
        def __init__(self, limiter: 'LLMRateLimiter', provider: str):
            self._limiter = limiter
            self._provider = provider

        def __enter__(self):
            if not self._limiter.acquire(self._provider):
                raise TimeoutError(f"LLM rate limit timeout for {self._provider}")
            return self

        def __exit__(self, *args):
            self._limiter.release(self._provider)

    def __call__(self, provider: str) -> '_Context':
        """Use as context manager: with limiter('groq'): ..."""
        return self._Context(self, provider)


class RedisLLMRateLimiter(LLMRateLimiter):
    """Redis-backed rate limiter — shared RPM tracking across workers.

    Extends in-process semaphore with Redis sorted-set RPM counter.
    """

    def __init__(self, redis_url: str, max_concurrent: int = DEFAULT_CONCURRENCY,
                 rpm: int = int(os.environ.get("CONTEXTSYNAPSE_LLM_RPM") or os.environ.get("AICONTEXTDB_LLM_RPM", "100"))):
        super().__init__(max_concurrent)
        import redis
        self._r = redis.from_url(redis_url, decode_responses=True)
        self._rpm = rpm

    def acquire(self, provider: str, timeout: float = 120.0) -> bool:
        import time, uuid
        # Check Redis RPM first
        try:
            now = time.time()
            rpm_key = f"llm:rpm:{provider}"
            pipe = self._r.pipeline()
            pipe.zremrangebyscore(rpm_key, 0, now - 60)
            pipe.zcard(rpm_key)
            _, count = pipe.execute()
            if count >= self._rpm:
                with self._lock:
                    self._stats.setdefault(provider, {"active": 0, "total": 0, "rejected": 0})
                    self._stats[provider]["rejected"] += 1
                logger.debug("[LLM_LIMIT] %s: RPM limit (%d/%d)", provider, count, self._rpm)
                return False
            # Record this call
            self._r.zadd(rpm_key, {str(uuid.uuid4())[:8]: now})
            self._r.expire(rpm_key, 120)
        except Exception:
            pass  # fail open — fall through to semaphore

        return super().acquire(provider, timeout)

    def get_stats(self) -> Dict[str, Dict]:
        import time
        stats = super().get_stats()
        for provider in stats:
            try:
                now = time.time()
                rpm_key = f"llm:rpm:{provider}"
                self._r.zremrangebyscore(rpm_key, 0, now - 60)
                stats[provider]["rpm_used"] = self._r.zcard(rpm_key)
                stats[provider]["rpm_limit"] = self._rpm
            except Exception:
                pass
        return stats


# Global singleton
_limiter: LLMRateLimiter | None = None
_limiter_lock = threading.Lock()


def get_limiter() -> LLMRateLimiter:
    global _limiter
    if _limiter is None:
        with _limiter_lock:
            if _limiter is None:
                url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
                if url:
                    try:
                        import redis
                        r = redis.from_url(url, decode_responses=True)
                        r.ping()
                        _limiter = RedisLLMRateLimiter(url)
                        logger.info("[LLM_LIMIT] Redis rate limiter (max=%d, rpm=%d)",
                                    _limiter._max, _limiter._rpm)
                        return _limiter
                    except Exception:
                        pass
                _limiter = LLMRateLimiter()
                logger.info("[LLM_LIMIT] In-process rate limiter (max=%d per provider)",
                            _limiter._max)
    return _limiter
