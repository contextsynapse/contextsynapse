"""AgentProfile — behavioral fingerprint per agent."""
from __future__ import annotations
import json, logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)

_DEFAULT_PROFILE = {
    "total_reads": 0, "total_writes": 0, "total_tool_calls": 0,
    "total_feedback_given": 0, "total_derivations": 0,
    "feedback_useful": 0, "feedback_misleading": 0, "feedback_incorrect": 0, "feedback_outdated": 0,
    "invalidations_caused": 0, "read_write_ratio": 0.5, "feedback_quality": 0.5,
    "tool_distribution": "{}", "hour_distribution": "{}",
    "sessions_count": 0, "consecutive_clean": 0,
}

class AgentProfile:
    def __init__(self, redis_client=None):
        self._redis = redis_client
        self._memory: Dict[str, Dict[str, Any]] = {}
        self._prefix = "contextcore:shield:profile"

    def get(self, agent_id: str) -> Dict[str, Any]:
        if self._redis:
            try:
                key = f"{self._prefix}:{agent_id}"
                raw = self._redis.hgetall(key)
                if raw:
                    data = dict(_DEFAULT_PROFILE)
                    for k, v in raw.items():
                        k = k.decode() if isinstance(k, bytes) else k
                        v = v.decode() if isinstance(v, bytes) else v
                        if k in data and isinstance(data[k], (int, float)):
                            data[k] = type(data[k])(v)
                        else:
                            data[k] = v
                    return data
            except Exception:
                pass
        return dict(self._memory.get(agent_id, _DEFAULT_PROFILE))

    def record_event(self, agent_id: str, event_type: str):
        d = self._ensure(agent_id)
        if event_type == "read":
            d["total_reads"] += 1
        elif event_type in ("write", "derive"):
            d["total_writes"] += 1
            if event_type == "derive":
                d["total_derivations"] += 1
        total = d["total_reads"] + d["total_writes"]
        d["read_write_ratio"] = round(d["total_reads"] / total, 3) if total > 0 else 0.5
        self._save(agent_id, d)

    def record_tool_call(self, agent_id: str, tool_name: str):
        d = self._ensure(agent_id)
        d["total_tool_calls"] += 1
        dist = json.loads(d["tool_distribution"])
        dist[tool_name] = dist.get(tool_name, 0) + 1
        d["tool_distribution"] = json.dumps(dist)
        hour = str(datetime.now(timezone.utc).hour)
        hours = json.loads(d["hour_distribution"])
        hours[hour] = hours.get(hour, 0) + 1
        d["hour_distribution"] = json.dumps(hours)
        self._save(agent_id, d)

    def record_feedback(self, agent_id: str, signal: str):
        d = self._ensure(agent_id)
        d["total_feedback_given"] += 1
        key = f"feedback_{signal}"
        if key in d:
            d[key] += 1
        good = d["feedback_useful"]
        bad = d["feedback_misleading"] + d["feedback_incorrect"] + d["feedback_outdated"]
        total = good + bad
        d["feedback_quality"] = round(good / total, 3) if total > 0 else 0.5
        if signal in ("misleading", "incorrect"):
            d["consecutive_clean"] = 0
        self._save(agent_id, d)

    def record_invalidation(self, agent_id: str, count: int = 1):
        d = self._ensure(agent_id)
        d["invalidations_caused"] += count
        d["consecutive_clean"] = 0
        self._save(agent_id, d)

    def increment_clean(self, agent_id: str):
        d = self._ensure(agent_id)
        d["consecutive_clean"] += 1
        self._save(agent_id, d)

    def _ensure(self, agent_id: str) -> Dict[str, Any]:
        if agent_id not in self._memory:
            self._memory[agent_id] = dict(_DEFAULT_PROFILE)
        return self._memory[agent_id]

    def _save(self, agent_id: str, data: Dict):
        self._memory[agent_id] = data
        if self._redis:
            try:
                self._redis.hset(f"{self._prefix}:{agent_id}", mapping={k: str(v) for k, v in data.items()})
            except Exception:
                pass
