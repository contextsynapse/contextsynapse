"""
A2A Discovery Registry
=======================
Cache and query remote A2A agent cards.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from contextsynapse.core.db import IS_POSTGRES, PH, connect, dict_cursor, run_ddl, row_to_dict

from .client import A2AClient

logger = logging.getLogger(__name__)


class A2ADiscoveryRegistry:
    """Registry for discovered remote A2A agents."""

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS a2a_remotes (
        url TEXT PRIMARY KEY,
        name TEXT,
        card_json TEXT,
        status TEXT DEFAULT 'active',
        last_fetched TEXT,
        created_at TEXT NOT NULL
    );
    """

    def __init__(self, db_path: str = "contextcore_data/context.db"):
        self._db_path = Path(db_path)
        if not IS_POSTGRES:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = connect(str(self._db_path))
        run_ddl(self._conn, self._SCHEMA)

    def _exec(self, sql: str, params: tuple = ()):
        cur = dict_cursor(self._conn)
        cur.execute(sql, params)
        return cur

    def _commit(self):
        self._conn.commit()

    def register_remote(self, url: str, auth_token: str = "") -> Dict[str, Any]:
        """Discover and register a remote A2A agent."""
        client = A2AClient(url, auth_token=auth_token)
        card = client.discover()
        now = datetime.now(timezone.utc).isoformat()
        if IS_POSTGRES:
            self._exec(
                f"INSERT INTO a2a_remotes (url, name, card_json, status, last_fetched, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, 'active', {PH}, {PH}) "
                f"ON CONFLICT (url) DO UPDATE SET name = EXCLUDED.name, card_json = EXCLUDED.card_json, "
                f"status = 'active', last_fetched = EXCLUDED.last_fetched",
                (url, card.get("name", url), json.dumps(card), now, now),
            )
        else:
            self._exec(
                f"INSERT OR REPLACE INTO a2a_remotes (url, name, card_json, status, last_fetched, created_at) "
                f"VALUES ({PH}, {PH}, {PH}, 'active', {PH}, COALESCE((SELECT created_at FROM a2a_remotes WHERE url = {PH}), {PH}))",
                (url, card.get("name", url), json.dumps(card), now, url, now),
            )
        self._commit()
        return card

    def list_remotes(self) -> List[Dict[str, Any]]:
        """List all registered remote agents."""
        rows = self._exec(
            "SELECT * FROM a2a_remotes WHERE status = 'active' ORDER BY name"
        ).fetchall()
        result = []
        for r in [row_to_dict(r) for r in rows]:
            entry = {
                "url": r["url"],
                "name": r["name"],
                "status": r["status"],
                "last_fetched": r["last_fetched"],
            }
            try:
                entry["card"] = json.loads(r["card_json"])
            except Exception:
                entry["card"] = {}
            result.append(entry)
        return result

    def get_card(self, url: str) -> Optional[Dict[str, Any]]:
        """Get a cached agent card by URL."""
        row = row_to_dict(self._exec(
            f"SELECT card_json FROM a2a_remotes WHERE url = {PH}", (url,)
        ).fetchone())
        if row:
            return json.loads(row["card_json"])
        return None

    def find_by_skill(self, skill_tag: str) -> List[Dict[str, Any]]:
        """Find remote agents that have a skill matching the given tag."""
        remotes = self.list_remotes()
        matches = []
        tag_lower = skill_tag.lower()
        for remote in remotes:
            card = remote.get("card", {})
            for skill in card.get("skills", []):
                tags = [t.lower() for t in skill.get("tags", [])]
                name = skill.get("name", "").lower()
                if tag_lower in tags or tag_lower in name:
                    matches.append(remote)
                    break
        return matches

    def refresh(self, url: str, auth_token: str = "") -> Optional[Dict[str, Any]]:
        """Re-fetch and update a remote agent's card."""
        try:
            return self.register_remote(url, auth_token)
        except Exception as e:
            logger.warning("Failed to refresh remote %s: %s", url, e)
            return None

    def remove(self, url: str):
        """Remove a remote agent."""
        self._exec(f"DELETE FROM a2a_remotes WHERE url = {PH}", (url,))
        self._commit()
