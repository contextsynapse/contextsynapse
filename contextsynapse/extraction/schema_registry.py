"""
Schema Registry — Predefined + custom extraction schemas.

Manages YAML schemas that control what entities/relationships the LLM
extracts during ingestion. Users can use predefined industry schemas
or create custom ones.

Usage:
    from contextsynapse.extraction.schema_registry import get_schema_registry
    reg = get_schema_registry()
    schemas = reg.list_schemas()
    schema = reg.get_schema("healthcare")
    reg.save_custom("my_schema", yaml_content)
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

logger = logging.getLogger(__name__)

# Predefined schemas ship with the package
_BUILTIN_DIR = Path(__file__).resolve().parent.parent / "config" / "schemas"
_CUSTOM_DB = "contextcore_data/schemas.db"

# Industry presets mapped to schema files
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


class SchemaRegistry:
    """Manages predefined and custom extraction schemas."""

    def __init__(self, builtin_dir: str = None, db_path: str = None):
        self._builtin_dir = Path(builtin_dir) if builtin_dir else _BUILTIN_DIR
        self._db_path = db_path or _CUSTOM_DB
        self._init_db()

    def _init_db(self):
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self._db_path)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS custom_schemas (
                name TEXT PRIMARY KEY,
                yaml_content TEXT NOT NULL,
                description TEXT DEFAULT '',
                industry TEXT DEFAULT '',
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        conn.commit()
        conn.close()

    def list_schemas(self) -> List[Dict[str, Any]]:
        """List all available schemas (builtin + custom)."""
        schemas = []

        # Builtin schemas
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

        # Custom schemas
        try:
            conn = sqlite3.connect(self._db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM custom_schemas ORDER BY name").fetchall()
            for r in rows:
                try:
                    raw = yaml.safe_load(r["yaml_content"])
                    node_types = list(raw.get("node_types", {}).keys())
                    edge_types = list(raw.get("edge_types", {}).keys())
                    schemas.append({
                        "name": r["name"],
                        "source": "custom",
                        "description": r["description"],
                        "industry": r["industry"],
                        "tags": json.loads(r["tags"]),
                        "node_types": node_types,
                        "edge_types": edge_types,
                        "node_count": len(node_types),
                        "edge_count": len(edge_types),
                        "created_at": r["created_at"],
                    })
                except Exception:
                    pass
            conn.close()
        except Exception:
            pass

        return schemas

    def get_schema(self, name: str) -> Optional[str]:
        """Get schema YAML content by name. Checks builtin then custom."""
        # Builtin
        if self._builtin_dir.exists():
            for f in self._builtin_dir.glob("*.yaml"):
                try:
                    raw = yaml.safe_load(f.read_text(encoding="utf-8"))
                    if raw.get("name") == name or f.stem == name:
                        return f.read_text(encoding="utf-8")
                except Exception:
                    pass

        # Custom
        try:
            conn = sqlite3.connect(self._db_path)
            row = conn.execute("SELECT yaml_content FROM custom_schemas WHERE name = ?", (name,)).fetchone()
            conn.close()
            if row:
                return row[0]
        except Exception:
            pass

        # Industry mapping
        mapped = INDUSTRY_SCHEMAS.get(name)
        if mapped and mapped != name:
            return self.get_schema(mapped)

        return None

    def get_schema_object(self, name: str):
        """Get a parsed ExtractionSchema object."""
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
        """Save a custom schema."""
        # Validate YAML
        raw = yaml.safe_load(yaml_content)
        if not isinstance(raw, dict) or "node_types" not in raw:
            raise ValueError("Schema must be a YAML mapping with 'node_types' key")

        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT OR REPLACE INTO custom_schemas (name, yaml_content, description, industry, tags, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, COALESCE((SELECT created_at FROM custom_schemas WHERE name = ?), ?), ?)",
            (name, yaml_content, description, industry, json.dumps(tags or []), name, now, now),
        )
        conn.commit()
        conn.close()

        node_types = list(raw.get("node_types", {}).keys())
        return {"name": name, "node_types": node_types, "status": "saved"}

    def delete_custom(self, name: str) -> bool:
        """Delete a custom schema."""
        conn = sqlite3.connect(self._db_path)
        c = conn.execute("DELETE FROM custom_schemas WHERE name = ?", (name,))
        conn.commit()
        conn.close()
        return c.rowcount > 0

    def get_for_industry(self, industry: str) -> Optional[str]:
        """Get the best schema for an industry tag."""
        mapped = INDUSTRY_SCHEMAS.get(industry.lower())
        if mapped:
            return self.get_schema(mapped)
        return self.get_schema("knowledge_base")  # default fallback


# Singleton — Redis if available, SQLite fallback
_registry = None

def get_schema_registry():
    """Get schema registry — Redis if available, SQLite fallback."""
    global _registry
    if _registry is not None:
        return _registry

    url = os.environ.get("CONTEXTSYNAPSE_REDIS_URL") or os.environ.get("AICONTEXTDB_REDIS_URL")
    if url:
        try:
            from .redis_schema_registry import RedisSchemaRegistry
            reg = RedisSchemaRegistry(redis_url=url)
            reg._r.ping()
            logger.info("Schema registry: Redis")
            _registry = reg
            return _registry
        except Exception as e:
            logger.warning("Redis schema registry unavailable (%s), using SQLite", e)

    _registry = SchemaRegistry()
    logger.info("Schema registry: SQLite")
    return _registry
