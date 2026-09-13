"""Single Pipeline model — identity, source, trigger, filters, extraction, steps, models, state."""
from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_DEFAULT_STEPS = {
    "fetch": True, "clean": True, "chunk": True, "filter": True,
    "extract": True, "graph_build": True, "embed": True,
    "index": True, "notify": True,
}


# ---------------------------------------------------------------------------
# Pipeline — the one model
# ---------------------------------------------------------------------------

@dataclass
class Pipeline:
    """A configurable ingestion pipeline (identity + source + trigger + filters + extraction + state)."""

    # Identity
    id: str = ""
    name: str = ""
    description: str = ""

    # Source
    source_type: str = "web_crawl"
    source_url: str = ""
    max_pages: int = 5

    # Trigger
    trigger_type: str = "one_time"            # one_time | scheduled | webhook
    interval_minutes: float = 60             # supports fractional minutes for sub-minute intervals

    # Filters (runtime)
    include_keywords: List[str] = field(default_factory=list)
    exclude_keywords: List[str] = field(default_factory=list)
    semantic_filter: str = ""
    semantic_threshold: float = 0.6

    # Extraction
    schema_id: str = ""
    schema_mode: str = "schema_plus"          # strict | schema_plus

    # Steps — the processing chain, editable
    steps: Dict[str, bool] = field(default_factory=lambda: dict(_DEFAULT_STEPS))

    # Models
    llm_model: str = ""
    embedding_model: str = ""

    # Target
    target_context_id: str = ""

    # State
    status: str = "draft"                     # draft | active | paused | error | completed
    created_at: str = ""
    last_run_at: str = ""
    next_run_at: str = ""
    total_runs: int = 0
    total_documents: int = 0
    last_error: str = ""

    def __post_init__(self):
        if not self.id:
            self.id = str(uuid.uuid4())
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        for step, default in _DEFAULT_STEPS.items():
            if step not in self.steps:
                self.steps[step] = default

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": self.description,
            "source_type": self.source_type,
            "source_url": self.source_url,
            "max_pages": self.max_pages,
            "trigger_type": self.trigger_type,
            "interval_minutes": self.interval_minutes,
            "include_keywords": self.include_keywords,
            "exclude_keywords": self.exclude_keywords,
            "semantic_filter": self.semantic_filter,
            "semantic_threshold": self.semantic_threshold,
            "schema_id": self.schema_id,
            "schema_mode": self.schema_mode,
            "steps": self.steps,
            "llm_model": self.llm_model,
            "embedding_model": self.embedding_model,
            "target_context_id": self.target_context_id,
            "status": self.status,
            "created_at": self.created_at,
            "last_run_at": self.last_run_at,
            "next_run_at": self.next_run_at,
            "total_runs": self.total_runs,
            "total_documents": self.total_documents,
            "last_error": self.last_error,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> Pipeline:
        return cls(**{k: d[k] for k in d if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# PipelineStore — persists Pipelines
# ---------------------------------------------------------------------------

class PipelineStore:
    """Persist Pipelines in Redis or in-memory fallback."""

    def __init__(self, redis_url: Optional[str] = None):
        self._redis = None
        self._memory: Dict[str, Dict] = {}
        if redis_url:
            try:
                import redis as _redis
                self._redis = _redis.from_url(redis_url)
                self._redis.ping()
            except Exception:
                self._redis = None

    def _key(self, pipeline_id: str) -> str:
        return f"pipeline:{pipeline_id}"

    def save(self, pipeline: Pipeline) -> None:
        data = json.dumps(pipeline.to_dict())
        if self._redis:
            self._redis.set(self._key(pipeline.id), data)
            self._redis.sadd("pipeline:ids", pipeline.id)
        else:
            self._memory[pipeline.id] = pipeline.to_dict()

    def get(self, pipeline_id: str) -> Optional[Pipeline]:
        if self._redis:
            raw = self._redis.get(self._key(pipeline_id))
            if raw:
                return Pipeline.from_dict(json.loads(raw))
            return None
        d = self._memory.get(pipeline_id)
        return Pipeline.from_dict(d) if d else None

    def list_all(self) -> List[Pipeline]:
        if self._redis:
            ids = self._redis.smembers("pipeline:ids")
            return [p for pid in ids if (p := self.get(pid.decode() if isinstance(pid, bytes) else pid))]
        return [Pipeline.from_dict(d) for d in self._memory.values()]

    def delete(self, pipeline_id: str) -> bool:
        if self._redis:
            self._redis.delete(self._key(pipeline_id))
            self._redis.srem("pipeline:ids", pipeline_id)
        else:
            self._memory.pop(pipeline_id, None)
        return True

    def save_run(self, pipeline_id: str, run_data: Dict) -> None:
        if self._redis:
            key = f"pipeline:{pipeline_id}:runs"
            self._redis.lpush(key, json.dumps(run_data))
            self._redis.ltrim(key, 0, 99)

    def get_runs(self, pipeline_id: str, limit: int = 20) -> List[Dict]:
        if self._redis:
            raws = self._redis.lrange(f"pipeline:{pipeline_id}:runs", 0, limit - 1)
            return [json.loads(r) for r in raws]
        return []
