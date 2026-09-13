"""Event Bus — pluggable pub/sub for intelligence modules."""
from __future__ import annotations

import logging
import os
import uuid
from abc import ABC, abstractmethod
from collections import defaultdict, deque
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


class EventBus(ABC):
    """Abstract event bus interface."""

    @abstractmethod
    async def publish(self, event_type: str, payload: dict) -> None: ...

    @abstractmethod
    def subscribe(self, event_type: str, callback: Callable) -> str: ...

    @abstractmethod
    def unsubscribe(self, subscription_id: str) -> None: ...


class LocalEventBus(EventBus):
    """In-process event bus with per-callback error isolation."""

    def __init__(self, dead_letter_max: int = 1000, max_retries: int = 2):
        self._subscribers: Dict[str, Dict[str, Callable]] = defaultdict(dict)
        self._dead_letters: deque = deque(maxlen=dead_letter_max)
        self._max_retries = max_retries

    @property
    def dead_letters(self) -> List[dict]:
        return list(self._dead_letters)

    def subscribe(self, event_type: str, callback: Callable) -> str:
        sub_id = str(uuid.uuid4())
        self._subscribers[event_type][sub_id] = callback
        return sub_id

    def unsubscribe(self, subscription_id: str) -> None:
        for event_type in list(self._subscribers):
            self._subscribers[event_type].pop(subscription_id, None)

    async def publish(self, event_type: str, payload: dict) -> None:
        for sub_id, callback in list(self._subscribers.get(event_type, {}).items()):
            retries = 0
            while retries <= self._max_retries:
                try:
                    result = callback(payload)
                    # Support async callbacks
                    if hasattr(result, "__await__"):
                        await result
                    break
                except Exception as e:
                    retries += 1
                    if retries > self._max_retries:
                        logger.warning("[EVENT_BUS] Callback %s failed after %d retries: %s",
                                       sub_id[:8], self._max_retries, e)
                        self._dead_letters.append({
                            "event_type": event_type,
                            "payload": payload,
                            "error": str(e),
                            "subscription_id": sub_id,
                        })


_bus: Optional[EventBus] = None


def get_event_bus() -> EventBus:
    """Factory — returns singleton event bus based on config."""
    global _bus
    if _bus is None:
        bus_type = os.getenv("CONTEXTSYNAPSE_EVENT_BUS") or os.getenv("AICONTEXTDB_EVENT_BUS", "local")
        if bus_type == "local":
            _bus = LocalEventBus()
        else:
            # Future: kafka, redis
            logger.warning("[EVENT_BUS] Unknown bus type '%s', falling back to local", bus_type)
            _bus = LocalEventBus()
    return _bus


def reset_event_bus():
    """Reset singleton (for testing)."""
    global _bus
    _bus = None
