"""Pipeline Rate Limiter — prevent source overload.

Enforces per-source and per-pipeline request limits to avoid
getting blocked by target sites or overwhelming the system.

Usage:
    from contextsynapse.intelligence.rate_limiter import PipelineRateLimiter

    limiter = PipelineRateLimiter()
    if limiter.allow("reuters.com", max_per_minute=10):
        fetch(url)
    else:
        skip("rate limited")
"""
from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)


@dataclass
class RateLimitConfig:
    """Rate limit configuration for a source domain."""
    max_per_minute: int = 10
    max_per_hour: int = 100
    cooldown_seconds: float = 2.0  # minimum gap between requests


class PipelineRateLimiter:
    """Token-bucket style rate limiter per source domain."""

    def __init__(self, default_rpm: int = 10, default_rph: int = 100,
                 default_cooldown: float = 2.0):
        self._default = RateLimitConfig(
            max_per_minute=default_rpm,
            max_per_hour=default_rph,
            cooldown_seconds=default_cooldown,
        )
        self._custom: Dict[str, RateLimitConfig] = {}
        self._requests: Dict[str, List[float]] = defaultdict(list)  # domain -> [timestamps]
        self._blocked_count: Dict[str, int] = defaultdict(int)

    def set_limit(self, domain: str, max_per_minute: int = 10,
                  max_per_hour: int = 100, cooldown: float = 2.0):
        """Set custom rate limit for a specific domain."""
        self._custom[domain] = RateLimitConfig(
            max_per_minute=max_per_minute,
            max_per_hour=max_per_hour,
            cooldown_seconds=cooldown,
        )

    def _get_domain(self, source: str) -> str:
        """Extract domain from URL or source identifier."""
        try:
            from urllib.parse import urlparse
            parsed = urlparse(source)
            return parsed.netloc or source
        except Exception:
            return source

    def _get_config(self, domain: str) -> RateLimitConfig:
        return self._custom.get(domain, self._default)

    def allow(self, source: str, max_per_minute: int = 0) -> bool:
        """Check if a request to this source is allowed.

        Returns True if within rate limits, False if throttled.
        """
        domain = self._get_domain(source)
        config = self._get_config(domain)
        now = time.time()

        # Override with explicit limit if provided
        rpm = max_per_minute or config.max_per_minute
        rph = config.max_per_hour

        # Clean old entries
        timestamps = self._requests[domain]
        timestamps[:] = [t for t in timestamps if now - t < 3600]  # keep last hour

        # Check cooldown
        if timestamps and (now - timestamps[-1]) < config.cooldown_seconds:
            self._blocked_count[domain] += 1
            return False

        # Check per-minute limit
        recent_minute = sum(1 for t in timestamps if now - t < 60)
        if recent_minute >= rpm:
            self._blocked_count[domain] += 1
            return False

        # Check per-hour limit
        if len(timestamps) >= rph:
            self._blocked_count[domain] += 1
            return False

        timestamps.append(now)
        return True

    def wait_if_needed(self, source: str) -> float:
        """Wait until a request is allowed. Returns seconds waited."""
        domain = self._get_domain(source)
        config = self._get_config(domain)

        if self.allow(source):
            return 0.0

        # Wait for cooldown
        wait = config.cooldown_seconds
        time.sleep(wait)
        return wait

    def get_stats(self) -> Dict[str, Dict]:
        """Get rate limiting stats per domain."""
        now = time.time()
        stats = {}
        for domain, timestamps in self._requests.items():
            recent = [t for t in timestamps if now - t < 60]
            stats[domain] = {
                "requests_last_minute": len(recent),
                "requests_last_hour": len(timestamps),
                "blocked_count": self._blocked_count.get(domain, 0),
                "config": self._get_config(domain).__dict__,
            }
        return stats
