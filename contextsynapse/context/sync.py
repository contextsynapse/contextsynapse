"""
Context Sync
=============
Bidirectional synchronisation between a local AIContextDB instance and a
remote CAS server.

The sync layer enables the hybrid deployment model:
- Local instance for dev / daemon / CLI (fast, offline)
- Remote instance for cloud agents / mobile (public API)
- Sync pushes/pulls context items, graph nodes, and provenance between them.

Usage::

    from contextsynapse.context.sync import ContextSync

    sync = ContextSync(
        remote_url="https://my-server.com/context",
        api_key="<agent_id>:<secret>",
    )

    # Push local session to remote
    sync.push("research-project")

    # Pull remote session to local
    sync.pull("research-project")

    # Bidirectional sync
    sync.sync("research-project")
"""

from __future__ import annotations

import json
import logging
import secrets
import urllib.request
import urllib.error
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SyncResult:
    """Result of a sync operation."""
    direction: str          # push | pull | sync
    session_name: str
    items_pushed: int = 0
    items_pulled: int = 0
    items_skipped: int = 0  # dedup hits
    errors: List[str] = field(default_factory=list)
    success: bool = True


class ContextSync:
    """
    Sync context between local and remote CAS instances.

    The sync protocol:
    1. Compare item hashes to find what's new on each side.
    2. Push local-only items to remote via ``/ingest`` or ``/contribute``.
    3. Pull remote-only items to local hub + graph.
    4. Dedup layer on both sides prevents actual duplicates.
    """

    def __init__(
        self,
        remote_url: str,
        api_key: str,
        timeout: int = 30,
    ):
        """
        Args:
            remote_url: Base URL of the remote CAS API (e.g., ``https://host/context``)
            api_key:    Composite API key (``<agent_id>:<secret>``)
            timeout:    HTTP request timeout in seconds
        """
        self._url = remote_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._agent_id = api_key.split(":")[0] if ":" in api_key else api_key

        # Local CAS references (lazy init)
        self._session_manager = None
        self._dedup = None

    def _init_local(self):
        """Lazy-init local CAS singletons."""
        if self._session_manager is not None:
            return
        from .session import ContextSessionManager
        from .dedup import ContextDedup
        from .vector_integration import SessionVectorStore

        try:
            from ..core.registry import GraphRegistry
            graph_registry = GraphRegistry()
        except Exception:
            graph_registry = None

        self._session_manager = ContextSessionManager(graph_registry=graph_registry)
        self._vector_store = SessionVectorStore()
        self._dedup = ContextDedup(vector_store=self._vector_store)

    # ------------------------------------------------------------------
    # HTTP helpers
    # ------------------------------------------------------------------

    def _request(self, method: str, path: str, data: Any = None) -> Dict[str, Any]:
        """Make an authenticated HTTP request to the remote CAS."""
        url = f"{self._url}{path}"
        headers = {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
        }

        body = json.dumps(data).encode("utf-8") if data else None
        req = urllib.request.Request(url, data=body, headers=headers, method=method)

        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as e:
            error_body = e.read().decode() if e.readable() else str(e)
            raise SyncError(f"{method} {path} -> {e.code}: {error_body}")
        except urllib.error.URLError as e:
            raise SyncError(f"Connection failed: {e.reason}")

    def _get(self, path: str) -> Dict[str, Any]:
        return self._request("GET", path)

    def _post(self, path: str, data: Any) -> Dict[str, Any]:
        return self._request("POST", path, data)

    # ------------------------------------------------------------------
    # Push (local → remote)
    # ------------------------------------------------------------------

    def push(self, session_name: str) -> SyncResult:
        """
        Push local context items to the remote CAS instance.

        Reads the local ContextHub for the session and posts each item
        to the remote ``/sessions/{sid}/ingest`` endpoint.
        The remote's dedup layer prevents actual duplicates.
        """
        self._init_local()
        result = SyncResult(direction="push", session_name=session_name)

        # Resolve local session
        local_session = self._session_manager.get_session_by_name(session_name)
        if not local_session:
            result.errors.append(f"Local session '{session_name}' not found")
            result.success = False
            return result

        # Find or create remote session
        remote_session_id = self._ensure_remote_session(session_name)
        if not remote_session_id:
            result.errors.append("Could not create/find remote session")
            result.success = False
            return result

        # Load local hub
        from .cli import _load_hub
        hub = _load_hub(local_session.session_id)
        items = hub.items()

        if not items:
            logger.info(f"No local items to push for '{session_name}'")
            return result

        for item in items:
            try:
                resp = self._post(f"/sessions/{remote_session_id}/ingest", {
                    "data": item.content,
                    "agent_id": self._agent_id,
                    "data_type": "text",
                    "source": item.source or "sync:push",
                    "metadata": {
                        "label": item.label,
                        "role": item.role.value if hasattr(item.role, "value") else str(item.role),
                        "synced_from": "local",
                        "original_created_at": item.created_at,
                    },
                })
                if resp.get("duplicate"):
                    result.items_skipped += 1
                else:
                    result.items_pushed += 1
            except Exception as e:
                result.errors.append(str(e))

        logger.info(
            f"Push '{session_name}': {result.items_pushed} pushed, "
            f"{result.items_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    # ------------------------------------------------------------------
    # Pull (remote → local)
    # ------------------------------------------------------------------

    def pull(self, session_name: str) -> SyncResult:
        """
        Pull remote context items into the local CAS instance.

        Fetches the remote session's exported context and adds items
        to the local hub, using the local dedup to skip duplicates.
        """
        self._init_local()
        result = SyncResult(direction="pull", session_name=session_name)

        # Find remote session
        remote_session_id = self._find_remote_session(session_name)
        if not remote_session_id:
            result.errors.append(f"Remote session '{session_name}' not found")
            result.success = False
            return result

        # Ensure local session exists
        local_session = self._session_manager.get_session_by_name(session_name)
        if not local_session:
            local_session = self._session_manager.create_session(name=session_name)

        # Fetch remote context
        try:
            remote_data = self._get(f"/sessions/{remote_session_id}/export?format=dict")
        except Exception as e:
            result.errors.append(f"Failed to fetch remote context: {e}")
            result.success = False
            return result

        remote_items = remote_data.get("items", [])
        if not remote_items:
            logger.info(f"No remote items to pull for '{session_name}'")
            return result

        # Load local hub
        from .cli import _load_hub, _save_hub
        hub = _load_hub(local_session.session_id)

        for item_data in remote_items:
            content = item_data.get("content", "")
            if not content.strip():
                continue

            # Dedup check
            dup = self._dedup.check(local_session.session_id, content)
            if dup.is_duplicate:
                result.items_skipped += 1
                continue

            # Add to local hub
            role = item_data.get("role", "user")
            hub.add_text(
                content,
                role=role,
                label=item_data.get("label"),
                source=item_data.get("source", "sync:pull"),
                metadata={
                    **item_data.get("metadata", {}),
                    "synced_from": "remote",
                },
            )

            # Register hash
            import uuid
            node_id = str(uuid.uuid4())
            self._dedup.register(local_session.session_id, node_id, content)

            # Auto-embed
            if self._vector_store and self._vector_store.available:
                self._vector_store.add_text(
                    session_id=local_session.session_id,
                    text=content,
                    node_id=node_id,
                    metadata={"source": "sync:pull"},
                )

            result.items_pulled += 1

        _save_hub(local_session.session_id, hub)

        logger.info(
            f"Pull '{session_name}': {result.items_pulled} pulled, "
            f"{result.items_skipped} skipped, {len(result.errors)} errors"
        )
        return result

    # ------------------------------------------------------------------
    # Bidirectional sync
    # ------------------------------------------------------------------

    def sync(self, session_name: str) -> SyncResult:
        """
        Full bidirectional sync: push local → remote, then pull remote → local.
        """
        push_result = self.push(session_name)
        pull_result = self.pull(session_name)

        return SyncResult(
            direction="sync",
            session_name=session_name,
            items_pushed=push_result.items_pushed,
            items_pulled=pull_result.items_pulled,
            items_skipped=push_result.items_skipped + pull_result.items_skipped,
            errors=push_result.errors + pull_result.errors,
            success=push_result.success and pull_result.success,
        )

    # ------------------------------------------------------------------
    # Remote session helpers
    # ------------------------------------------------------------------

    def _find_remote_session(self, name: str) -> Optional[str]:
        """Find a session on the remote by name."""
        try:
            sessions = self._get("/sessions")
            for s in sessions:
                if s.get("name") == name:
                    return s.get("session_id")
        except Exception as e:
            logger.warning(f"Could not list remote sessions: {e}")
        return None

    def _ensure_remote_session(self, name: str) -> Optional[str]:
        """Find or create a session on the remote."""
        sid = self._find_remote_session(name)
        if sid:
            return sid
        try:
            resp = self._post("/sessions", {"name": name, "agent_id": self._agent_id})
            return resp.get("session_id")
        except Exception as e:
            logger.error(f"Could not create remote session: {e}")
            return None

    def check_connection(self) -> Dict[str, Any]:
        """Test connectivity to the remote CAS."""
        try:
            return self._get("/health")
        except Exception as e:
            return {"status": "error", "message": str(e)}


class SyncError(Exception):
    """Raised when a sync HTTP request fails."""
    pass


# ======================================================================
# Redis Streams sync (optional)
# ======================================================================

_redis_available = False
try:
    import redis.asyncio as aioredis  # type: ignore
    _redis_available = True
except ImportError:
    try:
        import aioredis  # type: ignore  # older package name
        _redis_available = True
    except ImportError:
        pass


class RedisStreamSync:
    """
    Redis Streams-based change propagation for multi-process CAS.

    Each session gets a stream: ``cas:session:{session_id}``.
    Consumer groups allow multiple processes (API servers, daemons, CLIs)
    to each receive every event exactly once.

    Requires ``redis[asyncio]`` (``pip install redis``).
    Falls back gracefully if Redis is unavailable.
    """

    def __init__(
        self,
        redis_url: str = "redis://localhost:6379",
        consumer_group: str = "cas_consumers",
        consumer_name: Optional[str] = None,
    ):
        self._redis_url = redis_url
        self._consumer_group = consumer_group
        self._consumer_name = consumer_name or f"consumer_{secrets.token_hex(4)}"
        self._redis = None
        self._subscriptions: Dict[str, bool] = {}  # session_id -> active

    @property
    def available(self) -> bool:
        return _redis_available

    async def _ensure_connection(self):
        """Lazy-init the Redis connection."""
        if self._redis is not None:
            return
        if not _redis_available:
            raise SyncError("Redis not available (pip install redis)")
        self._redis = aioredis.from_url(self._redis_url, decode_responses=True)

    def _stream_key(self, session_id: str) -> str:
        return f"cas:session:{session_id}"

    async def create_consumer_group(self, session_id: str) -> bool:
        """Create a consumer group for the session stream (idempotent)."""
        await self._ensure_connection()
        key = self._stream_key(session_id)
        try:
            await self._redis.xgroup_create(key, self._consumer_group, id="0", mkstream=True)
            return True
        except Exception as e:
            if "BUSYGROUP" in str(e):
                return True  # group already exists
            logger.warning(f"Failed to create consumer group: {e}")
            return False

    async def publish_change(self, session_id: str, event) -> Optional[str]:
        """
        Publish a ContextEvent to the session's Redis stream.

        Args:
            session_id: Target session.
            event: A ContextEvent instance (must have .to_json() or be a dict).

        Returns:
            Stream message ID, or None on failure.
        """
        await self._ensure_connection()
        key = self._stream_key(session_id)

        if hasattr(event, "to_json"):
            payload = {"event": event.to_json()}
        elif isinstance(event, dict):
            payload = {"event": json.dumps(event)}
        else:
            payload = {"event": str(event)}

        try:
            msg_id = await self._redis.xadd(key, payload)
            return msg_id
        except Exception as e:
            logger.error(f"Redis publish failed: {e}")
            return None

    async def consume_pending(self, session_id: str, count: int = 100) -> List[Dict[str, Any]]:
        """
        Read unacknowledged messages from the consumer group.

        Returns list of ``{"id": stream_id, "event": parsed_event_dict}``.
        """
        await self._ensure_connection()
        key = self._stream_key(session_id)

        await self.create_consumer_group(session_id)

        try:
            messages = await self._redis.xreadgroup(
                self._consumer_group, self._consumer_name,
                {key: ">"}, count=count, block=0,
            )
        except Exception as e:
            logger.error(f"Redis consume failed: {e}")
            return []

        result = []
        for _stream, entries in messages:
            for msg_id, data in entries:
                try:
                    event = json.loads(data.get("event", "{}"))
                except (json.JSONDecodeError, TypeError):
                    event = data
                result.append({"id": msg_id, "event": event})

        return result

    async def acknowledge(self, session_id: str, message_id: str) -> bool:
        """Acknowledge a consumed message."""
        await self._ensure_connection()
        key = self._stream_key(session_id)
        try:
            await self._redis.xack(key, self._consumer_group, message_id)
            return True
        except Exception as e:
            logger.warning(f"Redis ack failed: {e}")
            return False

    async def subscribe(self, session_id: str, callback) -> None:
        """
        Subscribe to a session stream with a callback.

        The callback receives each parsed event dict. Runs until
        ``unsubscribe()`` is called for this session.
        """
        await self._ensure_connection()
        await self.create_consumer_group(session_id)
        self._subscriptions[session_id] = True
        key = self._stream_key(session_id)

        import asyncio
        while self._subscriptions.get(session_id, False):
            try:
                messages = await self._redis.xreadgroup(
                    self._consumer_group, self._consumer_name,
                    {key: ">"}, count=10, block=1000,
                )
                for _stream, entries in messages:
                    for msg_id, data in entries:
                        try:
                            event = json.loads(data.get("event", "{}"))
                        except (json.JSONDecodeError, TypeError):
                            event = data
                        try:
                            await callback(event)
                        except Exception as e:
                            logger.warning(f"Subscriber callback error: {e}")
                        await self._redis.xack(key, self._consumer_group, msg_id)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Redis subscribe loop error: {e}")
                await asyncio.sleep(1)

    def unsubscribe(self, session_id: str) -> None:
        """Stop the subscription loop for a session."""
        self._subscriptions.pop(session_id, None)

    async def trim_stream(self, session_id: str, max_len: int = 10000) -> int:
        """Trim a session stream to max_len entries."""
        await self._ensure_connection()
        key = self._stream_key(session_id)
        try:
            return await self._redis.xtrim(key, maxlen=max_len, approximate=True)
        except Exception as e:
            logger.warning(f"Redis trim failed: {e}")
            return 0

    async def close(self):
        """Close the Redis connection."""
        if self._redis:
            await self._redis.close()
            self._redis = None
        self._subscriptions.clear()


# ======================================================================
# Hybrid sync — Redis when available, HTTP fallback
# ======================================================================

class HybridSync:
    """
    Unified sync layer that uses Redis Streams when available,
    falls back to HTTP push/pull.

    Usage::

        sync = HybridSync(
            redis_url="redis://localhost:6379",
            remote_url="https://my-cas.com/context",
            api_key="agent_id:secret",
        )
        print(sync.mode)  # "redis" | "http" | "local_only"
    """

    def __init__(
        self,
        redis_url: Optional[str] = None,
        remote_url: Optional[str] = None,
        api_key: Optional[str] = None,
        consumer_group: str = "cas_consumers",
    ):
        self._redis_sync: Optional[RedisStreamSync] = None
        self._http_sync: Optional[ContextSync] = None

        if redis_url and _redis_available:
            self._redis_sync = RedisStreamSync(
                redis_url=redis_url,
                consumer_group=consumer_group,
            )

        if remote_url and api_key:
            self._http_sync = ContextSync(
                remote_url=remote_url,
                api_key=api_key,
            )

    @property
    def mode(self) -> str:
        """Current sync mode."""
        if self._redis_sync and self._redis_sync.available:
            return "redis"
        if self._http_sync:
            return "http"
        return "local_only"

    @property
    def redis_sync(self) -> Optional[RedisStreamSync]:
        return self._redis_sync

    @property
    def http_sync(self) -> Optional[ContextSync]:
        return self._http_sync

    async def propagate_change(self, session_id: str, event) -> bool:
        """
        Propagate a change event. Uses Redis if available, otherwise no-op
        (HTTP sync is batch-based, not event-based).
        """
        if self._redis_sync and self._redis_sync.available:
            msg_id = await self._redis_sync.publish_change(session_id, event)
            return msg_id is not None
        return False

    async def consume_changes(self, session_id: str, count: int = 100) -> List[Dict[str, Any]]:
        """Consume pending changes from Redis stream."""
        if self._redis_sync and self._redis_sync.available:
            return await self._redis_sync.consume_pending(session_id, count=count)
        return []

    def push(self, session_name: str) -> SyncResult:
        """Push via HTTP (batch sync)."""
        if self._http_sync:
            return self._http_sync.push(session_name)
        return SyncResult(direction="push", session_name=session_name, success=False,
                          errors=["No HTTP sync configured"])

    def pull(self, session_name: str) -> SyncResult:
        """Pull via HTTP (batch sync)."""
        if self._http_sync:
            return self._http_sync.pull(session_name)
        return SyncResult(direction="pull", session_name=session_name, success=False,
                          errors=["No HTTP sync configured"])

    def sync(self, session_name: str) -> SyncResult:
        """Bidirectional HTTP sync."""
        if self._http_sync:
            return self._http_sync.sync(session_name)
        return SyncResult(direction="sync", session_name=session_name, success=False,
                          errors=["No HTTP sync configured"])

    async def close(self):
        """Clean up connections."""
        if self._redis_sync:
            await self._redis_sync.close()
