"""Webhook Receiver — push-based source ingestion (zero latency).

External services push events TO us instead of us polling them.
Each webhook has a unique endpoint URL and routes content to a context.

Usage:
    # In API router:
    receiver = WebhookReceiver(collector)
    receiver.register("tcs_news", context_name="TCS", secret="abc123")

    # External service POSTs to:
    # POST /intelligence/webhooks/tcs_news
    # Body: {"title": "...", "content": "...", "url": "...", "source": "..."}
    result = receiver.receive("tcs_news", payload)
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class WebhookConfig:
    """Configuration for a registered webhook endpoint."""
    webhook_id: str
    context_name: str
    secret: str = ""
    strategy: str = "news_article"
    enabled: bool = True
    created_at: str = ""
    receive_count: int = 0
    last_received: str = ""

    def __post_init__(self):
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()
        if not self.secret:
            self.secret = secrets.token_hex(16)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "webhook_id": self.webhook_id,
            "context_name": self.context_name,
            "strategy": self.strategy,
            "enabled": self.enabled,
            "created_at": self.created_at,
            "receive_count": self.receive_count,
            "last_received": self.last_received,
        }


@dataclass
class WebhookResult:
    status: str = "accepted"
    ingested: bool = False
    deduped: bool = False
    error: str = ""

    def to_dict(self):
        return {"status": self.status, "ingested": self.ingested,
                "deduped": self.deduped, "error": self.error}


class WebhookReceiver:
    """Manages webhook endpoints for push-based ingestion."""

    def __init__(self, db=None):
        self._db = db
        self._webhooks: Dict[str, WebhookConfig] = {}

    def register(self, webhook_id: str, context_name: str,
                 secret: str = "", strategy: str = "news_article") -> WebhookConfig:
        """Register a new webhook endpoint."""
        config = WebhookConfig(
            webhook_id=webhook_id,
            context_name=context_name,
            secret=secret,
            strategy=strategy,
        )
        self._webhooks[webhook_id] = config
        logger.info("[WEBHOOK] Registered: %s -> %s", webhook_id, context_name)
        return config

    def list_webhooks(self) -> List[Dict[str, Any]]:
        return [w.to_dict() for w in self._webhooks.values()]

    def get_webhook(self, webhook_id: str) -> Optional[WebhookConfig]:
        return self._webhooks.get(webhook_id)

    def verify_signature(self, webhook_id: str, payload: bytes, signature: str) -> bool:
        """Verify HMAC-SHA256 signature for a webhook payload."""
        config = self._webhooks.get(webhook_id)
        if not config or not config.secret:
            return True  # no secret = no verification
        expected = hmac.new(config.secret.encode(), payload, hashlib.sha256).hexdigest()
        return hmac.compare_digest(expected, signature)

    def receive(self, webhook_id: str, payload: Dict[str, Any]) -> WebhookResult:
        """Process an incoming webhook payload.

        Expected payload:
        {
            "title": "Article title",
            "content": "Full text content",  # or "text" or "body"
            "url": "https://source.com/article",  # optional
            "source": "Reuters",  # optional
        }
        """
        config = self._webhooks.get(webhook_id)
        if not config:
            return WebhookResult(status="error", error=f"Unknown webhook: {webhook_id}")

        if not config.enabled:
            return WebhookResult(status="error", error="Webhook disabled")

        # Extract content from payload
        content = payload.get("content") or payload.get("text") or payload.get("body") or ""
        title = payload.get("title") or payload.get("headline") or ""
        url = payload.get("url") or payload.get("link") or ""
        source = payload.get("source") or ""

        if not content and not url:
            return WebhookResult(status="error", error="No content or URL in payload")

        # Update stats
        config.receive_count += 1
        config.last_received = datetime.now(timezone.utc).isoformat()

        # Ingest
        try:
            if url and not content:
                from ..ingestion.smart_ingest import ingest_url
                result = ingest_url(
                    url, self._db,
                    strategy=config.strategy,
                    context_purpose=config.context_name,
                )
            else:
                from ..ingestion.smart_ingest import ingest_text
                result = ingest_text(
                    content, self._db,
                    title=title,
                    source_url=url,
                    strategy=config.strategy,
                    context_purpose=config.context_name,
                )

            if hasattr(result, 'errors') and result.errors:
                if any("Duplicate" in str(e) for e in result.errors):
                    return WebhookResult(status="accepted", deduped=True)
                return WebhookResult(status="error", error=str(result.errors))

            logger.info("[WEBHOOK] %s: ingested '%s'", webhook_id, title[:50])
            return WebhookResult(status="accepted", ingested=True)

        except Exception as exc:
            logger.error("[WEBHOOK] %s: ingest failed: %s", webhook_id, exc)
            return WebhookResult(status="error", error=str(exc))
