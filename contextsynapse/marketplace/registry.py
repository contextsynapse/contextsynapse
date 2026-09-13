"""
Marketplace Registry — Publish, discover, and subscribe to knowledge graphs.

Usage:
    from contextsynapse.marketplace.registry import get_marketplace
    mp = get_marketplace()
    mp.publish("healthcare-kb", graph_name, description="500 medical papers", price_monthly=10)
    listings = mp.search("healthcare")
    mp.subscribe(listing_id, target_namespace)
"""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class MarketplaceRegistry:
    """Manages published and subscribed knowledge graphs."""

    def __init__(self, db_path: str = "contextcore_data/marketplace.db"):
        self._db_path = db_path
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(db_path)
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS listings (
                listing_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                description TEXT DEFAULT '',
                publisher_id TEXT NOT NULL,
                publisher_name TEXT DEFAULT '',
                graph_namespace TEXT NOT NULL,
                category TEXT DEFAULT 'general',
                tags TEXT DEFAULT '[]',
                node_count INTEGER DEFAULT 0,
                edge_count INTEGER DEFAULT 0,
                schema_name TEXT DEFAULT '',
                price_monthly REAL DEFAULT 0.0,
                is_free INTEGER DEFAULT 1,
                status TEXT DEFAULT 'active',
                subscribers INTEGER DEFAULT 0,
                rating REAL DEFAULT 0.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_mp_category ON listings(category);
            CREATE INDEX IF NOT EXISTS idx_mp_status ON listings(status);

            CREATE TABLE IF NOT EXISTS subscriptions (
                subscription_id TEXT PRIMARY KEY,
                listing_id TEXT NOT NULL,
                subscriber_id TEXT NOT NULL,
                target_namespace TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                last_synced TEXT,
                created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_sub_subscriber ON subscriptions(subscriber_id);
        """)
        conn.commit()
        conn.close()

    def publish(
        self,
        name: str,
        graph_namespace: str,
        publisher_id: str,
        publisher_name: str = "",
        description: str = "",
        category: str = "general",
        tags: Optional[List[str]] = None,
        schema_name: str = "",
        price_monthly: float = 0.0,
        node_count: int = 0,
        edge_count: int = 0,
    ) -> Dict[str, Any]:
        """Publish a knowledge graph to the marketplace."""
        listing_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO listings (listing_id, name, description, publisher_id, publisher_name, "
            "graph_namespace, category, tags, node_count, edge_count, schema_name, "
            "price_monthly, is_free, status, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'active', ?, ?)",
            (listing_id, name, description, publisher_id, publisher_name,
             graph_namespace, category, json.dumps(tags or []),
             node_count, edge_count, schema_name,
             price_monthly, int(price_monthly == 0), now, now),
        )
        conn.commit()
        conn.close()
        return {"listing_id": listing_id, "name": name, "status": "published"}

    def search(self, query: str = "", category: str = "", free_only: bool = False) -> List[Dict[str, Any]]:
        """Search marketplace listings."""
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        sql = "SELECT * FROM listings WHERE status = 'active'"
        params = []
        if category:
            sql += " AND category = ?"
            params.append(category)
        if free_only:
            sql += " AND is_free = 1"
        if query:
            sql += " AND (name LIKE ? OR description LIKE ? OR tags LIKE ?)"
            q = f"%{query}%"
            params.extend([q, q, q])
        sql += " ORDER BY subscribers DESC, rating DESC LIMIT 50"
        rows = conn.execute(sql, params).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def get_listing(self, listing_id: str) -> Optional[Dict[str, Any]]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT * FROM listings WHERE listing_id = ?", (listing_id,)).fetchone()
        conn.close()
        return dict(row) if row else None

    def subscribe(self, listing_id: str, subscriber_id: str, target_namespace: str) -> Dict[str, Any]:
        """Subscribe to a listing — imports the graph into subscriber's namespace."""
        sub_id = str(uuid.uuid4())[:12]
        now = datetime.now(timezone.utc).isoformat()
        conn = sqlite3.connect(self._db_path)
        conn.execute(
            "INSERT INTO subscriptions (subscription_id, listing_id, subscriber_id, target_namespace, status, created_at) "
            "VALUES (?, ?, ?, ?, 'active', ?)",
            (sub_id, listing_id, subscriber_id, target_namespace, now),
        )
        conn.execute("UPDATE listings SET subscribers = subscribers + 1 WHERE listing_id = ?", (listing_id,))
        conn.commit()
        conn.close()
        return {"subscription_id": sub_id, "listing_id": listing_id, "status": "subscribed"}

    def my_subscriptions(self, subscriber_id: str) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT s.*, l.name, l.description, l.category, l.node_count "
            "FROM subscriptions s JOIN listings l ON s.listing_id = l.listing_id "
            "WHERE s.subscriber_id = ? AND s.status = 'active'",
            (subscriber_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]

    def my_listings(self, publisher_id: str) -> List[Dict[str, Any]]:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM listings WHERE publisher_id = ? ORDER BY created_at DESC",
            (publisher_id,),
        ).fetchall()
        conn.close()
        return [dict(r) for r in rows]


_marketplace = None

def get_marketplace() -> MarketplaceRegistry:
    global _marketplace
    if _marketplace is None:
        _marketplace = MarketplaceRegistry()
    return _marketplace
