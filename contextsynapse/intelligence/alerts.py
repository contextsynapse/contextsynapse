"""Alert Delivery — notify external systems when signals fire.

Sends alerts via webhook, email (SMTP), or in-app propagation when
the reactive controller detects important signals.

Usage:
    from contextsynapse.intelligence.alerts import AlertDispatcher, AlertChannel

    dispatcher = AlertDispatcher()
    dispatcher.add_channel(AlertChannel(
        name="ops_webhook",
        channel_type="webhook",
        config={"url": "https://hooks.slack.com/..."},
    ))

    # Called automatically by reactive controller
    dispatcher.dispatch(reaction)
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class AlertChannel:
    """A notification channel for alert delivery."""
    name: str
    channel_type: str           # webhook | email | log
    config: Dict[str, Any] = field(default_factory=dict)
    enabled: bool = True
    min_severity: str = "info"  # info | warning | alert

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "channel_type": self.channel_type,
            "config": {k: v for k, v in self.config.items() if k != "password"},
            "enabled": self.enabled,
            "min_severity": self.min_severity,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "AlertChannel":
        return cls(
            name=data.get("name", ""),
            channel_type=data.get("channel_type", "log"),
            config=data.get("config", {}),
            enabled=data.get("enabled", True),
            min_severity=data.get("min_severity", "info"),
        )


@dataclass
class DeliveryResult:
    channel: str
    status: str = "sent"       # sent | failed | skipped
    error: str = ""

    def to_dict(self):
        return {"channel": self.channel, "status": self.status, "error": self.error}


_SEVERITY_ORDER = {"info": 0, "warning": 1, "alert": 2}


class AlertDispatcher:
    """Dispatches alerts to registered channels."""

    def __init__(self):
        self._channels: List[AlertChannel] = []
        self._delivery_log: List[Dict[str, Any]] = []

    def add_channel(self, channel: AlertChannel):
        self._channels.append(channel)

    def list_channels(self) -> List[Dict[str, Any]]:
        return [c.to_dict() for c in self._channels]

    def dispatch(
        self,
        signal_type: str,
        entity_name: str,
        context_name: str,
        severity: str = "info",
        details: Dict[str, Any] = None,
    ) -> List[DeliveryResult]:
        """Send an alert to all matching channels."""
        results = []
        signal_sev = _SEVERITY_ORDER.get(severity, 0)

        payload = {
            "signal_type": signal_type,
            "entity": entity_name,
            "context": context_name,
            "severity": severity,
            "details": details or {},
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }

        for channel in self._channels:
            if not channel.enabled:
                continue

            min_sev = _SEVERITY_ORDER.get(channel.min_severity, 0)
            if signal_sev < min_sev:
                results.append(DeliveryResult(channel=channel.name, status="skipped"))
                continue

            try:
                if channel.channel_type == "webhook":
                    self._send_webhook(channel, payload)
                elif channel.channel_type == "email":
                    self._send_email(channel, payload)
                elif channel.channel_type == "log":
                    self._send_log(channel, payload)

                results.append(DeliveryResult(channel=channel.name, status="sent"))
            except Exception as exc:
                results.append(DeliveryResult(channel=channel.name, status="failed", error=str(exc)))

        self._delivery_log.append({
            "payload": payload,
            "results": [r.to_dict() for r in results],
            "delivered_at": datetime.now(timezone.utc).isoformat(),
        })

        return results

    def _send_webhook(self, channel: AlertChannel, payload: Dict):
        """POST JSON payload to webhook URL."""
        import urllib.request

        url = channel.config.get("url", "")
        if not url:
            raise ValueError("No webhook URL configured")

        headers = channel.config.get("headers", {})
        data = json.dumps(payload).encode("utf-8")

        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json", **headers},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            logger.info("[ALERT] Webhook %s: %d", channel.name, resp.status)

    def _send_email(self, channel: AlertChannel, payload: Dict):
        """Send email via SMTP."""
        import smtplib
        from email.mime.text import MIMEText

        smtp_host = channel.config.get("smtp_host", "localhost")
        smtp_port = channel.config.get("smtp_port", 587)
        from_addr = channel.config.get("from", "alerts@contextsynapse.local")
        to_addrs = channel.config.get("to", [])
        password = channel.config.get("password", "")

        if not to_addrs:
            raise ValueError("No email recipients configured")

        subject = f"[{payload['severity'].upper()}] {payload['signal_type']}: {payload['entity']}"
        body = (
            f"Signal: {payload['signal_type']}\n"
            f"Entity: {payload['entity']}\n"
            f"Context: {payload['context']}\n"
            f"Severity: {payload['severity']}\n"
            f"Time: {payload['timestamp']}\n"
            f"\nDetails: {json.dumps(payload['details'], indent=2)}"
        )

        msg = MIMEText(body)
        msg["Subject"] = subject
        msg["From"] = from_addr
        msg["To"] = ", ".join(to_addrs) if isinstance(to_addrs, list) else to_addrs

        with smtplib.SMTP(smtp_host, smtp_port) as server:
            if password:
                server.starttls()
                server.login(from_addr, password)
            server.send_message(msg)
            logger.info("[ALERT] Email sent to %s", to_addrs)

    def _send_log(self, channel: AlertChannel, payload: Dict):
        """Log the alert (default fallback)."""
        logger.warning("[ALERT][%s] %s: %s in %s — %s",
                        payload["severity"], payload["signal_type"],
                        payload["entity"], payload["context"],
                        payload.get("details", {}).get("keywords", ""))

    def get_delivery_log(self, limit: int = 50) -> List[Dict[str, Any]]:
        return self._delivery_log[-limit:]
