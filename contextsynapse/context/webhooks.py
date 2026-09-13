"""
Webhook Registry & Dispatcher
==============================
Register outbound webhook URLs to receive HTTP POST notifications
when context events occur in sessions.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import sqlite3
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class Webhook:
    webhook_id: str
    tenant_id: str
    url: str
    event_types: List[str]
    secret: str
    created_at: float
    status: str = "active"

    def to_dict(self) -> dict:
        return {
            "webhook_id": self.webhook_id,
            "url": self.url,
            "event_types": self.event_types,
            "status": self.status,
            "created_at": self.created_at,
        }


class WebhookRegistry:
    """SQLite-backed webhook registration."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path("contextcore_data") / "context.db")
        self._db_path = db_path
        self._init_db()

    def _init_db(self):
        Path(self._db_path).parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS webhooks (
                    webhook_id TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL,
                    url TEXT NOT NULL,
                    event_types TEXT NOT NULL DEFAULT '[]',
                    secret TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'active',
                    created_at REAL NOT NULL
                )
            """)

    def register(
        self,
        tenant_id: str,
        url: str,
        event_types: Optional[List[str]] = None,
    ) -> Webhook:
        webhook = Webhook(
            webhook_id=str(uuid.uuid4()),
            tenant_id=tenant_id,
            url=url,
            event_types=event_types or ["context_added", "agent_joined"],
            secret=uuid.uuid4().hex,
            created_at=time.time(),
        )
        with sqlite3.connect(self._db_path) as conn:
            conn.execute(
                """INSERT INTO webhooks
                   (webhook_id, tenant_id, url, event_types, secret, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    webhook.webhook_id,
                    webhook.tenant_id,
                    webhook.url,
                    json.dumps(webhook.event_types),
                    webhook.secret,
                    webhook.status,
                    webhook.created_at,
                ),
            )
        return webhook

    def list_webhooks(self, tenant_id: str) -> List[Webhook]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM webhooks WHERE tenant_id = ? ORDER BY created_at DESC",
                (tenant_id,),
            ).fetchall()
        return [
            Webhook(
                webhook_id=r["webhook_id"],
                tenant_id=r["tenant_id"],
                url=r["url"],
                event_types=json.loads(r["event_types"]),
                secret=r["secret"],
                created_at=r["created_at"],
                status=r["status"],
            )
            for r in rows
        ]

    def delete(self, webhook_id: str) -> bool:
        with sqlite3.connect(self._db_path) as conn:
            c = conn.execute("DELETE FROM webhooks WHERE webhook_id = ?", (webhook_id,))
            return c.rowcount > 0

    def get(self, webhook_id: str) -> Optional[Webhook]:
        with sqlite3.connect(self._db_path) as conn:
            conn.row_factory = sqlite3.Row
            r = conn.execute(
                "SELECT * FROM webhooks WHERE webhook_id = ?", (webhook_id,)
            ).fetchone()
        if not r:
            return None
        return Webhook(
            webhook_id=r["webhook_id"],
            tenant_id=r["tenant_id"],
            url=r["url"],
            event_types=json.loads(r["event_types"]),
            secret=r["secret"],
            created_at=r["created_at"],
            status=r["status"],
        )

    @staticmethod
    def sign_payload(payload: dict, secret: str) -> str:
        """HMAC-SHA256 signature for webhook payload."""
        body = json.dumps(payload, sort_keys=True).encode()
        return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()

    def dispatch(self, tenant_id: str, event: dict):
        """Fire webhooks matching event type (best-effort, non-blocking)."""
        hooks = self.list_webhooks(tenant_id)
        event_type = event.get("event_type", "")

        for hook in hooks:
            if hook.status != "active":
                continue
            if event_type and hook.event_types and event_type not in hook.event_types:
                continue

            try:
                self._deliver(hook, event)
            except Exception as e:
                logger.warning("Webhook dispatch failed for %s: %s", hook.webhook_id, e)

    def _deliver(self, hook: Webhook, event: dict, max_retries: int = 3):
        """Deliver a webhook with retry + exponential backoff."""
        import urllib.request

        sig = self.sign_payload(event, hook.secret)
        body = json.dumps(event, default=str).encode("utf-8")

        for attempt in range(max_retries):
            try:
                req = urllib.request.Request(
                    hook.url,
                    data=body,
                    headers={
                        "Content-Type": "application/json",
                        "X-Webhook-Signature": sig,
                        "X-Webhook-Event": event.get("event_type", ""),
                        "X-Webhook-ID": hook.webhook_id,
                    },
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=10) as resp:
                    if resp.status < 300:
                        logger.info("Webhook delivered: %s → %s (attempt %d)",
                                     event.get("event_type"), hook.url, attempt + 1)
                        return
            except Exception as e:
                if attempt == max_retries - 1:
                    logger.error("Webhook delivery failed after %d attempts: %s → %s: %s",
                                  max_retries, event.get("event_type"), hook.url, e)
                else:
                    time.sleep(2 ** attempt)  # 1s, 2s, 4s backoff
