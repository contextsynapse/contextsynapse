"""
Runtime Prompt Injection Queue
================================
Allows humans to inject prompts into running agent pipelines.
Agents check this queue before each LLM turn and incorporate
any pending directives into their next context window.

Usage:
    queue = get_injection_queue()

    # Human injects a directive
    queue.inject("session-123", "codex", "Focus on payments instead of auth", "directive", "high")

    # Agent checks before each turn
    injections = queue.get_pending("session-123", "codex")
    # → [{"message": "Focus on payments...", "type": "directive", "priority": "high", ...}]
"""

from __future__ import annotations

import json
import logging
import os
import time
from collections import defaultdict
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class InjectionQueue:
    """Per-session, per-agent queue for runtime prompt injections.

    Supports two backends:
        - Redis (multi-worker safe, preferred)
        - In-memory (single-worker fallback)
    """

    REDIS_PREFIX = "contextcore:inject:"
    TTL_SECONDS = 600  # 10 minutes

    def __init__(self):
        self._redis = None
        self._local: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._try_redis()

    def _try_redis(self):
        url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "")
        if not url:
            return
        try:
            import redis
            self._redis = redis.Redis.from_url(url, decode_responses=True)
            self._redis.ping()
        except Exception:
            self._redis = None

    def inject(
        self,
        session_id: str,
        target_agent: str,
        message: str,
        injection_type: str = "directive",
        priority: str = "normal",
        injected_by: str = "human",
    ) -> Dict[str, Any]:
        """Queue a prompt injection for an agent.

        Args:
            session_id: The running session/boundary ID.
            target_agent: Agent name or "all" for broadcast.
            message: The directive/constraint/answer text.
            injection_type: "directive", "constraint", "answer", "priority", "stop".
            priority: "high", "normal", "low".
            injected_by: Who injected (default "human").

        Returns:
            Dict with injection metadata.
        """
        entry = {
            "message": message,
            "type": injection_type,
            "priority": priority,
            "target_agent": target_agent,
            "injected_by": injected_by,
            "timestamp": time.time(),
            "consumed": False,
        }

        key = f"{session_id}:{target_agent}"

        if self._redis:
            rkey = f"{self.REDIS_PREFIX}{key}"
            self._redis.rpush(rkey, json.dumps(entry))
            self._redis.expire(rkey, self.TTL_SECONDS)
            # Also push to broadcast key if target is specific agent
            if target_agent != "all":
                bkey = f"{self.REDIS_PREFIX}{session_id}:all"
                self._redis.rpush(bkey, json.dumps(entry))
                self._redis.expire(bkey, self.TTL_SECONDS)
        else:
            self._local[key].append(entry)
            if target_agent != "all":
                self._local[f"{session_id}:all"].append(entry)

        logger.info(
            "Injection queued: session=%s agent=%s type=%s priority=%s msg=%s",
            session_id, target_agent, injection_type, priority, message[:60],
        )
        return entry

    def get_pending(self, session_id: str, agent_name: str) -> List[Dict[str, Any]]:
        """Get and clear pending injections for an agent.

        Called by the agent loop before each LLM turn.
        Returns all pending injections (agent-specific + broadcast).
        Marks them as consumed.
        """
        results = []

        # Agent-specific key
        agent_key = f"{session_id}:{agent_name}"
        # Broadcast key
        all_key = f"{session_id}:all"

        if self._redis:
            for rkey_suffix in (agent_key, all_key):
                rkey = f"{self.REDIS_PREFIX}{rkey_suffix}"
                items = self._redis.lrange(rkey, 0, -1)
                self._redis.delete(rkey)
                now = time.time()
                for raw in items:
                    try:
                        entry = json.loads(raw)
                        if now - entry.get("timestamp", 0) > self.TTL_SECONDS:
                            continue
                        if entry.get("consumed"):
                            continue
                        # Avoid duplicates (broadcast + specific)
                        if not any(r["message"] == entry["message"] and r["timestamp"] == entry["timestamp"] for r in results):
                            results.append(entry)
                    except Exception:
                        continue
        else:
            now = time.time()
            for local_key in (agent_key, all_key):
                items = self._local.pop(local_key, [])
                for entry in items:
                    if now - entry.get("timestamp", 0) > self.TTL_SECONDS:
                        continue
                    if entry.get("consumed"):
                        continue
                    if not any(r["message"] == entry["message"] and r["timestamp"] == entry["timestamp"] for r in results):
                        results.append(entry)

        # Sort: high priority first, then by timestamp
        priority_order = {"high": 0, "normal": 1, "low": 2}
        results.sort(key=lambda x: (priority_order.get(x.get("priority", "normal"), 1), x.get("timestamp", 0)))

        return results

    def has_pending(self, session_id: str, agent_name: str) -> bool:
        """Check if there are pending injections without consuming them."""
        agent_key = f"{session_id}:{agent_name}"
        all_key = f"{session_id}:all"

        if self._redis:
            for rkey_suffix in (agent_key, all_key):
                rkey = f"{self.REDIS_PREFIX}{rkey_suffix}"
                if self._redis.llen(rkey) > 0:
                    return True
            return False
        else:
            return bool(self._local.get(agent_key)) or bool(self._local.get(all_key))

    def format_for_agent(self, injections: List[Dict[str, Any]]) -> str:
        """Format injections as text to prepend to agent's context.

        Returns a formatted block that gets injected before the agent's next turn.
        """
        if not injections:
            return ""

        lines = ["\n=== HUMAN DIRECTIVE (Runtime Injection) ==="]
        for inj in injections:
            prio = inj.get("priority", "normal")
            itype = inj.get("type", "directive")
            msg = inj.get("message", "")

            if prio == "high":
                prefix = "IMPORTANT"
            else:
                prefix = "Note"

            if itype == "constraint":
                lines.append(f"[CONSTRAINT — {prefix}] {msg}")
                lines.append("You MUST follow this constraint for all remaining actions.")
            elif itype == "stop":
                lines.append(f"[STOP DIRECTIVE] Finish your current task and stop. Do not start new work.")
                lines.append(f"Reason: {msg}")
            elif itype == "answer":
                lines.append(f"[HUMAN ANSWER] {msg}")
            elif itype == "priority":
                lines.append(f"[PRIORITY CHANGE — {prefix}] {msg}")
            else:
                lines.append(f"[DIRECTIVE — {prefix}] {msg}")

        lines.append("=== END HUMAN DIRECTIVE ===\n")
        return "\n".join(lines)

    def get_history(self, session_id: str, limit: int = 20) -> List[Dict[str, Any]]:
        """Get recent injection history for a session (for display)."""
        # For Redis, we'd need a separate history key; for now return empty
        # since consumed items are deleted. Could be enhanced with audit log.
        return []


# Global singleton
_queue = None


def get_injection_queue() -> InjectionQueue:
    global _queue
    if _queue is None:
        _queue = InjectionQueue()
    return _queue
