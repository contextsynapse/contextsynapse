"""
Multi-Worker Support
=====================
Redis-backed bridges for in-memory singletons that need to share state
across uvicorn workers.

When running with `--workers N` (N > 1), each worker is a separate process.
In-memory singletons (EventBus, Propagator, Gravity) don't share state.
This module provides Redis-backed alternatives.

Usage:
    # In start_server.py or app startup:
    from contextsynapse.multiworker import enable_multiworker

    if workers > 1:
        enable_multiworker()  # patches singletons to use Redis

Architecture:
    - EventBus -> Redis Pub/Sub (events published to Redis channel, all workers subscribe)
    - Propagator -> Redis Hash (pending updates stored in Redis, read by any worker)
    - Gravity -> Redis Hash (intent scores stored per-agent in Redis)
    - Policy cache -> TTL-based invalidation (re-read from SQLite every 60s)
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_redis_client = None
_enabled = False


def _get_redis():
    """Get or create Redis client."""
    global _redis_client
    if _redis_client is not None:
        return _redis_client

    url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
    try:
        import redis
        _redis_client = redis.Redis.from_url(url, decode_responses=True)
        _redis_client.ping()
        return _redis_client
    except Exception as e:
        logger.warning("Redis not available for multi-worker: %s", e)
        return None


# ── Redis-backed EventBus Bridge ────────────────────────────────────

class RedisEventBridge:
    """Bridges the in-process EventBus to Redis Pub/Sub.

    - On emit: publishes to Redis channel + local bus
    - On subscribe: listens to Redis channel in background thread
    """

    CHANNEL = "contextcore:events"

    def __init__(self, local_bus):
        self._local = local_bus
        self._original_emit = local_bus.emit
        self._listener_thread = None
        self._running = False

    def patch(self):
        """Replace local bus emit with Redis-forwarding version."""
        self._local.emit = self._emit_with_redis
        self._start_listener()
        logger.info("[MULTIWORKER] EventBus patched with Redis Pub/Sub")

    def _emit_with_redis(self, event_type: str, data: Dict[str, Any] | None = None):
        """Emit locally AND publish to Redis."""
        # Local emit (for this worker's subscribers)
        self._original_emit(event_type, data)

        # Publish to Redis (for other workers)
        r = _get_redis()
        if r:
            try:
                payload = json.dumps({
                    "type": event_type,
                    "data": data or {},
                    "timestamp": time.time(),
                    "worker_pid": os.getpid(),
                })
                r.publish(self.CHANNEL, payload)
            except Exception as e:
                logger.debug("Redis publish failed: %s", e)

    def _start_listener(self):
        """Start background thread to receive events from other workers."""
        if self._listener_thread and self._listener_thread.is_alive():
            return

        self._running = True
        self._listener_thread = threading.Thread(
            target=self._listen_loop, daemon=True, name="redis-event-listener",
        )
        self._listener_thread.start()

    def _listen_loop(self):
        """Listen for events from Redis and dispatch to local bus."""
        r = _get_redis()
        if not r:
            return

        my_pid = os.getpid()
        try:
            pubsub = r.pubsub()
            pubsub.subscribe(self.CHANNEL)
            for message in pubsub.listen():
                if not self._running:
                    break
                if message["type"] != "message":
                    continue
                try:
                    event = json.loads(message["data"])
                    # Skip events from this worker (already dispatched locally)
                    if event.get("worker_pid") == my_pid:
                        continue
                    # Dispatch to local subscribers
                    self._original_emit(event.get("type", ""), event.get("data", {}))
                except Exception:
                    pass
        except Exception as e:
            logger.debug("Redis event listener stopped: %s", e)


# ── Redis-backed Propagator ──────────────────────────────────────

class RedisPropagatorBridge:
    """Stores pending context updates in Redis instead of in-memory dict."""

    KEY_PREFIX = "contextcore:propagation:"

    def __init__(self, propagator):
        self._propagator = propagator
        self._original_propagate = propagator.propagate
        self._original_get_pending = propagator.get_pending

    def patch(self):
        """Replace in-memory propagation with Redis-backed."""
        self._propagator.propagate = self._propagate_redis
        self._propagator.get_pending = self._get_pending_redis
        logger.info("[MULTIWORKER] ContextPropagator patched with Redis")

    def _propagate_redis(self, source_agent, event_type, content, namespace,
                         target_agents=None, priority="normal"):
        """Store update in Redis."""
        r = _get_redis()
        if not r:
            return self._original_propagate(source_agent, event_type, content,
                                            namespace, target_agents, priority)

        update = json.dumps({
            "source_agent": source_agent,
            "event_type": event_type,
            "content": content,
            "namespace": namespace,
            "priority": priority,
            "timestamp": time.time(),
            "delivered_to": [],
        })

        key = f"{self.KEY_PREFIX}{namespace}"
        r.rpush(key, update)
        r.expire(key, self._propagator._ttl)
        logger.info("Propagated %s from %s (Redis)", event_type, source_agent)

    def _get_pending_redis(self, agent_id, namespace, max_updates=5):
        """Get pending updates from Redis."""
        r = _get_redis()
        if not r:
            return self._original_get_pending(agent_id, namespace, max_updates)

        key = f"{self.KEY_PREFIX}{namespace}"
        items = r.lrange(key, 0, -1)
        now = time.time()
        results = []

        for raw in items:
            try:
                update = json.loads(raw)
                # Skip expired
                if now - update.get("timestamp", 0) > self._propagator._ttl:
                    continue
                # Skip self
                if update.get("source_agent") == agent_id:
                    continue
                # Skip already delivered to this agent
                if agent_id in update.get("delivered_to", []):
                    continue

                prefix = "IMPORTANT" if update.get("priority") == "critical" else "Update"
                results.append(
                    f"{prefix}: {update['source_agent']} -- {update['event_type']}: {update['content']}"
                )

                # Mark as delivered
                update.setdefault("delivered_to", []).append(agent_id)
                # Update in Redis (replace the item)
                # For simplicity, we use a separate delivered tracking key
                delivered_key = f"{self.KEY_PREFIX}delivered:{namespace}:{agent_id}"
                r.sadd(delivered_key, raw)
                r.expire(delivered_key, self._propagator._ttl)

            except Exception:
                continue

            if len(results) >= max_updates:
                break

        return results


# ── Redis-backed Gravity ──────────────────────────────────────────

class RedisGravityBridge:
    """Stores agent intent observations in Redis instead of in-memory dict."""

    KEY_PREFIX = "contextcore:gravity:"

    def __init__(self, gravity):
        self._gravity = gravity
        self._original_observe = gravity.observe
        self._original_get_intent = gravity.get_intent

    def patch(self):
        """Replace in-memory gravity with Redis-backed."""
        self._gravity.observe = self._observe_redis
        self._gravity.get_intent = self._get_intent_redis
        logger.info("[MULTIWORKER] ContextGravity patched with Redis")

    def _observe_redis(self, agent_id, tool_name, args):
        """Store observation in Redis."""
        # Still run local observation (for this worker's cache)
        self._original_observe(agent_id, tool_name, args)

        # Also store in Redis for other workers
        r = _get_redis()
        if not r:
            return

        try:
            key = f"{self.KEY_PREFIX}{agent_id}"
            observation = json.dumps({
                "tool": tool_name,
                "args": {k: str(v)[:100] for k, v in (args or {}).items()},
                "timestamp": time.time(),
            })
            r.rpush(key, observation)
            r.ltrim(key, -100, -1)  # keep last 100 observations
            r.expire(key, 600)  # 10 min TTL
        except Exception:
            pass

    def _get_intent_redis(self, agent_id):
        """Get intent from Redis (merges with local)."""
        # Get local intent
        local_intent = self._original_get_intent(agent_id)

        # Merge with Redis observations from other workers
        r = _get_redis()
        if not r:
            return local_intent

        try:
            key = f"{self.KEY_PREFIX}{agent_id}"
            items = r.lrange(key, 0, -1)
            now = time.time()

            for raw in items:
                try:
                    obs = json.loads(raw)
                    if now - obs.get("timestamp", 0) > 300:  # 5 min decay
                        continue
                    # Extract keywords from tool args
                    for v in obs.get("args", {}).values():
                        for word in str(v).lower().split():
                            if len(word) > 2:
                                local_intent[word] = local_intent.get(word, 0) + 0.5
                except Exception:
                    continue
        except Exception:
            pass

        return local_intent


# ── Policy Cache TTL ──────────────────────────────────────────────

def _patch_policy_cache():
    """Add TTL to ToolPolicy's in-memory cache."""
    try:
        from contextsynapse.gateway.policy import get_policy
        policy = get_policy()

        # Store original _load_policy
        original_load = policy._load_policy
        cache_times: Dict[str, float] = {}
        TTL = 60  # Re-read from DB every 60 seconds

        def _ttl_load_policy(namespace):
            now = time.time()
            if namespace in cache_times and (now - cache_times[namespace]) < TTL:
                if namespace in policy._cache:
                    return policy._cache[namespace]
            # Cache miss or expired
            policy._cache.pop(namespace, None)
            result = original_load(namespace)
            cache_times[namespace] = now
            return result

        policy._load_policy = _ttl_load_policy
        logger.info("[MULTIWORKER] ToolPolicy cache TTL set to %ds", TTL)
    except Exception as e:
        logger.debug("Could not patch policy cache: %s", e)


# ── Main Enable Function ──────────────────────────────────────────

def enable_multiworker():
    """Enable multi-worker support by patching singletons with Redis bridges.

    Call this ONCE during app startup when workers > 1.
    Safe to call even when Redis is not available (falls back to local).
    """
    global _enabled
    if _enabled:
        return

    r = _get_redis()
    if not r:
        logger.warning(
            "[MULTIWORKER] Redis not available. Multi-worker mode will have "
            "limited cross-worker sync. Set AICONTEXTDB_REDIS_URL to enable."
        )
        _enabled = True
        return

    # 1. Patch EventBus
    try:
        from contextsynapse.api.events import event_bus
        bridge = RedisEventBridge(event_bus)
        bridge.patch()
    except Exception as e:
        logger.debug("Could not patch EventBus: %s", e)

    # 2. Patch Propagator
    try:
        from contextsynapse.context.propagation import get_propagator
        bridge = RedisPropagatorBridge(get_propagator())
        bridge.patch()
    except Exception as e:
        logger.debug("Could not patch Propagator: %s", e)

    # 3. Patch Gravity
    try:
        from contextsynapse.context.gravity import get_gravity
        bridge = RedisGravityBridge(get_gravity())
        bridge.patch()
    except Exception as e:
        logger.debug("Could not patch Gravity: %s", e)

    # 4. Patch Policy cache
    _patch_policy_cache()

    _enabled = True
    logger.info("[MULTIWORKER] All Redis bridges enabled (PID: %d)", os.getpid())
