"""
Redis-backed Schema Registry — custom schemas stored in Redis instead of SQLite.

Builtin schemas still read from disk (config/schemas/*.yaml).
Custom schemas stored as Redis hashes: schema:custom:{name} → fields.

Same public API as SchemaRegistry — drop-in replacement.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "config" / "schemas"

INDUSTRY_SCHEMAS = {
    "technology": "sdlc",
    "healthcare": "healthcare",
    "finance": "finance",
    "legal": "legal",
    "education": "education",
    "retail": "retail",
    "research": "knowledge_base",
    "general": "knowledge_base",
}


class RedisSchemaRegistry:
    """Manages predefined and custom extraction schemas with Redis backend."""

    KEY_PREFIX = "schema:custom:"
    NAMES_KEY = "schema:custom:names"

    def __init__(self, redis_url: str = None, builtin_dir: str = None):
        import redis
        self._redis_url = redis_url or os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL", "redis://localhost:6379/0")
        self._r = redis.from_url(self._redis_url, decode_responses=True)
        self._builtin_dir = Path(builtin_dir) if builtin_dir else _BUILTIN_DIR

        # Migrate from SQLite if Redis is empty
        self._migrate_from_sqlite()

    def list_schemas(self) -> List[Dict[str, Any]]:
        schemas = []

        # Builtin schemas (from disk)
        if self._builtin_dir.exists():
            for f in sorted(self._builtin_dir.glob("*.yaml")):
                try:
                    raw = yaml.safe_load(f.read_text(encoding="utf-8"))
                    name = raw.get("name", f.stem)
                    node_types = list(raw.get("node_types", {}).keys())
                    edge_types = list(raw.get("edge_types", {}).keys())
                    schemas.append({
                        "name": name,
                        "source": "builtin",
                        "file": f.name,
                        "node_types": node_types,
                        "edge_types": edge_types,
                        "node_count": len(node_types),
                        "edge_count": len(edge_types),
                        "description": raw.get("description", ""),
                    })
                except Exception as e:
                    logger.debug("Failed to load schema %s: %s", f.name, e)

        # Custom schemas (from Redis)
        names = self._r.smembers(self.NAMES_KEY)
        if names:
            pipe = self._r.pipeline()
            for name in sorted(names):
                pipe.hgetall(f"{self.KEY_PREFIX}{name}")
            results = pipe.execute()

            for meta in results:
                if not meta:
                    continue
                try:
                    raw = yaml.safe_load(meta.get("yaml_content", ""))
                    node_types = list(raw.get("node_types", {}).keys()) if raw else []
                    edge_types = list(raw.get("edge_types", {}).keys()) if raw else []
                    schemas.append({
                        "name": meta["name"],
                        "source": "custom",
                        "description": meta.get("description", ""),
                        "industry": meta.get("industry", ""),
                        "tags": json.loads(meta.get("tags", "[]")),
                        "node_types": node_types,
                        "edge_types": edge_types,
                        "node_count": len(node_types),
                        "edge_count": len(edge_types),
                        "created_at": meta.get("created_at", ""),
                    })
                except Exception:
                    pass

        return schemas

    def get_schema(self, name: str) -> Optional[str]:
        # Builtin first
        if self._builtin_dir.exists():
            for f in self._builtin_dir.glob("*.yaml"):
                try:
                    raw = yaml.safe_load(f.read_text(encoding="utf-8"))
                    if raw.get("name") == name or f.stem == name:
                        return f.read_text(encoding="utf-8")
                except Exception:
                    pass

        # Custom from Redis
        content = self._r.hget(f"{self.KEY_PREFIX}{name}", "yaml_content")
        if content:
            return content

        # Industry mapping
        mapped = INDUSTRY_SCHEMAS.get(name)
        if mapped and mapped != name:
            return self.get_schema(mapped)

        return None

    def get_schema_object(self, name: str):
        yaml_content = self.get_schema(name)
        if not yaml_content:
            return None
        from .schema_loader import load_schema_from_yaml_string
        return load_schema_from_yaml_string(yaml_content, name=name)

    def save_custom(
        self,
        name: str,
        yaml_content: str,
        description: str = "",
        industry: str = "",
        tags: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        raw = yaml.safe_load(yaml_content)
        if not isinstance(raw, dict) or "node_types" not in raw:
            raise ValueError("Schema must be a YAML mapping with 'node_types' key")

        now = datetime.now(timezone.utc).isoformat()

        # Preserve original created_at
        existing = self._r.hget(f"{self.KEY_PREFIX}{name}", "created_at")
        created_at = existing or now

        pipe = self._r.pipeline()
        pipe.hset(f"{self.KEY_PREFIX}{name}", mapping={
            "name": name,
            "yaml_content": yaml_content,
            "description": description,
            "industry": industry,
            "tags": json.dumps(tags or []),
            "created_at": created_at,
            "updated_at": now,
        })
        pipe.sadd(self.NAMES_KEY, name)
        pipe.execute()

        node_types = list(raw.get("node_types", {}).keys())
        return {"name": name, "node_types": node_types, "status": "saved"}

    def delete_custom(self, name: str) -> bool:
        pipe = self._r.pipeline()
        pipe.delete(f"{self.KEY_PREFIX}{name}")
        pipe.srem(self.NAMES_KEY, name)
        results = pipe.execute()
        return results[0] > 0  # True if the hash was deleted

    def get_for_industry(self, industry: str) -> Optional[str]:
        mapped = INDUSTRY_SCHEMAS.get(industry.lower())
        if mapped:
            return self.get_schema(mapped)
        return self.get_schema("knowledge_base")

    # ------------------------------------------------------------------
    # Migration from SQLite
    # ------------------------------------------------------------------

    def _migrate_from_sqlite(self):
        if self._r.scard(self.NAMES_KEY) > 0:
            return

        db_path = "contextcore_data/schemas.db"
        if not os.path.exists(db_path):
            return

        try:
            import sqlite3
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM custom_schemas").fetchall()
            conn.close()

            if not rows:
                return

            pipe = self._r.pipeline()
            for r in rows:
                pipe.hset(f"{self.KEY_PREFIX}{r['name']}", mapping={
                    "name": r["name"],
                    "yaml_content": r["yaml_content"],
                    "description": r["description"] or "",
                    "industry": r["industry"] or "",
                    "tags": r["tags"] or "[]",
                    "created_at": r["created_at"],
                    "updated_at": r["updated_at"],
                })
                pipe.sadd(self.NAMES_KEY, r["name"])
            pipe.execute()
            logger.info("[REDIS-SCHEMA] Migrated %d custom schemas from SQLite to Redis", len(rows))
        except Exception as e:
            logger.warning("[REDIS-SCHEMA] Migration from SQLite failed: %s", e)
