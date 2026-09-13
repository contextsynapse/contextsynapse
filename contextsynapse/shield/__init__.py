"""AgentShield — continuous behavioral auth + adaptive trust."""
from __future__ import annotations

_shield = None

def get_agent_shield():
    global _shield
    if _shield is None:
        redis_client = None
        try:
            import os, redis
            url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
            if url:
                redis_client = redis.from_url(url)
        except Exception:
            pass
        from .shield import AgentShield
        _shield = AgentShield(redis_client=redis_client)
    return _shield

__all__ = ["get_agent_shield"]
