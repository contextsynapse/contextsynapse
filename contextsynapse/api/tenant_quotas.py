"""
Tenant Quotas — per-tenant resource limits and usage tracking for SaaS.

Tracks and enforces:
  - Graphs per tenant (default: 50)
  - Nodes per tenant (default: 100,000)
  - Ingestion jobs per hour (default: 20)
  - LLM calls per hour (default: 200)
  - Storage MB per tenant (default: 500)
  - API requests per minute (default: 60)

Usage:
    from contextsynapse.api.tenant_quotas import get_quota_manager

    quotas = get_quota_manager()
    quotas.check("tenant_123", "graphs")       # raises 429 if over limit
    quotas.record("tenant_123", "llm_calls")   # increment usage counter
    usage = quotas.get_usage("tenant_123")      # get current usage
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any, Dict, Optional

from fastapi import HTTPException

logger = logging.getLogger(__name__)

# Default limits (can be overridden per tenant in tenant.config)
DEFAULT_QUOTAS = {
    "graphs": 50,              # max graphs per tenant
    "nodes": 100_000,          # max total nodes across all graphs
    "ingestion_per_hour": 20,  # max ingestion jobs per hour
    "llm_calls_per_hour": 200, # max LLM API calls per hour
    "storage_mb": 500,         # max disk storage in MB
    "api_rpm": 60,             # max API requests per minute
}


class TenantQuotaManager:
    """Redis-backed quota tracking and enforcement."""

    KEY_PREFIX = "quota:"

    def __init__(self, redis_url: str = None):
        import redis
        self._url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
        self._r = redis.from_url(self._url, decode_responses=True)

    def _quota_key(self, tenant_id: str, resource: str) -> str:
        return f"{self.KEY_PREFIX}{tenant_id}:{resource}"

    def get_limits(self, tenant_id: str) -> Dict[str, int]:
        """Get effective limits for a tenant (custom overrides + defaults)."""
        # Check for custom limits in Redis
        custom_key = f"{self.KEY_PREFIX}{tenant_id}:limits"
        try:
            custom = self._r.hgetall(custom_key)
            if custom:
                limits = dict(DEFAULT_QUOTAS)
                for k, v in custom.items():
                    try:
                        limits[k] = int(v)
                    except ValueError:
                        pass
                return limits
        except Exception:
            pass
        return dict(DEFAULT_QUOTAS)

    def set_limits(self, tenant_id: str, limits: Dict[str, int]):
        """Set custom limits for a tenant (admin operation)."""
        custom_key = f"{self.KEY_PREFIX}{tenant_id}:limits"
        self._r.hset(custom_key, mapping={k: str(v) for k, v in limits.items()})

    def check(self, tenant_id: str, resource: str, count: int = 1) -> bool:
        """Check if tenant can use more of a resource. Raises 429 if over limit."""
        limits = self.get_limits(tenant_id)
        limit = limits.get(resource)
        if limit is None:
            return True  # no limit defined for this resource

        current = self.get_current(tenant_id, resource)
        if current + count > limit:
            raise HTTPException(
                status_code=429,
                detail=f"Quota exceeded: {resource} ({current}/{limit}). Upgrade your plan for higher limits.",
            )
        return True

    def get_current(self, tenant_id: str, resource: str) -> int:
        """Get current usage count for a resource."""
        key = self._quota_key(tenant_id, resource)
        try:
            # Time-windowed resources (per hour / per minute)
            if resource.endswith("_per_hour"):
                now = time.time()
                self._r.zremrangebyscore(key, 0, now - 3600)
                return self._r.zcard(key)
            elif resource == "api_rpm":
                now = time.time()
                self._r.zremrangebyscore(key, 0, now - 60)
                return self._r.zcard(key)
            else:
                # Absolute counters (graphs, nodes, storage)
                val = self._r.get(key)
                return int(val) if val else 0
        except Exception:
            return 0

    def record(self, tenant_id: str, resource: str, count: int = 1):
        """Record resource usage."""
        key = self._quota_key(tenant_id, resource)
        try:
            if resource.endswith("_per_hour") or resource == "api_rpm":
                # Time-windowed: add to sorted set with timestamp
                import uuid
                now = time.time()
                pipe = self._r.pipeline()
                for _ in range(count):
                    pipe.zadd(key, {str(uuid.uuid4())[:8]: now})
                ttl = 3600 if resource.endswith("_per_hour") else 120
                pipe.expire(key, ttl)
                pipe.execute()
            else:
                # Absolute: increment counter
                self._r.incrby(key, count)
        except Exception as e:
            logger.debug("[QUOTA] Failed to record %s for %s: %s", resource, tenant_id, e)

    def set_absolute(self, tenant_id: str, resource: str, value: int):
        """Set an absolute usage value (e.g., after recounting nodes)."""
        key = self._quota_key(tenant_id, resource)
        try:
            self._r.set(key, str(value))
        except Exception:
            pass

    def get_usage(self, tenant_id: str) -> Dict[str, Any]:
        """Get full usage report for a tenant."""
        limits = self.get_limits(tenant_id)
        usage = {}
        for resource, limit in limits.items():
            current = self.get_current(tenant_id, resource)
            usage[resource] = {
                "current": current,
                "limit": limit,
                "percent": round(current / limit * 100, 1) if limit > 0 else 0,
                "remaining": max(0, limit - current),
            }
        return usage

    def reset(self, tenant_id: str, resource: str = None):
        """Reset usage counters (admin operation)."""
        if resource:
            key = self._quota_key(tenant_id, resource)
            self._r.delete(key)
        else:
            # Reset all
            pattern = f"{self.KEY_PREFIX}{tenant_id}:*"
            cursor = 0
            while True:
                cursor, keys = self._r.scan(cursor, match=pattern, count=100)
                keys = [k for k in keys if not k.endswith(":limits")]  # keep custom limits
                if keys:
                    self._r.delete(*keys)
                if cursor == 0:
                    break


class InProcessQuotaManager:
    """Fallback quota manager when Redis is unavailable (single-worker only)."""

    def __init__(self):
        self._counters: Dict[str, int] = {}

    def get_limits(self, tenant_id: str) -> Dict[str, int]:
        return dict(DEFAULT_QUOTAS)

    def set_limits(self, tenant_id: str, limits: Dict[str, int]):
        pass

    def check(self, tenant_id: str, resource: str, count: int = 1) -> bool:
        return True  # no enforcement without Redis

    def get_current(self, tenant_id: str, resource: str) -> int:
        return self._counters.get(f"{tenant_id}:{resource}", 0)

    def record(self, tenant_id: str, resource: str, count: int = 1):
        key = f"{tenant_id}:{resource}"
        self._counters[key] = self._counters.get(key, 0) + count

    def set_absolute(self, tenant_id: str, resource: str, value: int):
        self._counters[f"{tenant_id}:{resource}"] = value

    def get_usage(self, tenant_id: str) -> Dict[str, Any]:
        limits = self.get_limits(tenant_id)
        return {r: {"current": 0, "limit": l, "percent": 0, "remaining": l} for r, l in limits.items()}

    def reset(self, tenant_id: str, resource: str = None):
        pass


# Singleton
_manager = None


def get_quota_manager():
    """Get or create quota manager (Redis if available, in-process fallback)."""
    global _manager
    if _manager is not None:
        return _manager

    url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if url:
        try:
            mgr = TenantQuotaManager(url)
            mgr._r.ping()
            logger.info("Tenant quotas: Redis")
            _manager = mgr
            return _manager
        except Exception:
            pass

    _manager = InProcessQuotaManager()
    logger.info("Tenant quotas: in-process (no enforcement)")
    return _manager
