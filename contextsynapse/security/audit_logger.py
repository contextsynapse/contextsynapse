"""AuditLogger — append-only compliance audit trail.

Every action is logged with WHO, WHAT, WHEN, WHY. No update or delete
operations exist — the log is immutable by design (SEBI requirement).

Usage:
    logger = AuditLogger()
    logger.log(who="user1", action="trade_executed", resource="portfolio/pf_001",
               details={"trade_id": "trd_001"}, skill_chain="/pre-trade-check")
    entries = logger.get_entries(resource="portfolio/pf_001")
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class AuditEntry:
    """A single audit log entry — immutable after creation."""
    who: str                              # user_id
    action: str                           # trade_proposed, compliance_checked, etc.
    resource: str                         # portfolio/pf_001, trade/trd_001
    role: str = ""                        # fund_manager, compliance_officer
    details: Dict[str, Any] = field(default_factory=dict)
    skill_chain: str = ""                 # /pre-trade-check → /compliance-check
    ip_address: str = ""
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "who": self.who,
            "action": self.action,
            "resource": self.resource,
            "role": self.role,
            "details": self.details,
            "skill_chain": self.skill_chain,
            "ip_address": self.ip_address,
            "timestamp": self.timestamp,
        }


class AuditLogger:
    """Append-only audit log. No delete/update/clear methods exist."""

    def __init__(self):
        self._entries: List[AuditEntry] = []

    @property
    def total_entries(self) -> int:
        return len(self._entries)

    def log(
        self,
        who: str,
        action: str,
        resource: str,
        role: str = "",
        details: Optional[Dict[str, Any]] = None,
        skill_chain: str = "",
        ip_address: str = "",
    ) -> AuditEntry:
        """Append an entry to the audit log. Returns the created entry."""
        entry = AuditEntry(
            who=who,
            action=action,
            resource=resource,
            role=role,
            details=details or {},
            skill_chain=skill_chain,
            ip_address=ip_address,
        )
        self._entries.append(entry)
        return entry

    def get_entries(
        self,
        resource: Optional[str] = None,
        action: Optional[str] = None,
        who: Optional[str] = None,
        limit: int = 100,
    ) -> List[AuditEntry]:
        """Get entries, most recent first. Filterable by resource/action/who."""
        filtered = self._entries
        if resource:
            filtered = [e for e in filtered if e.resource == resource]
        if action:
            filtered = [e for e in filtered if e.action == action]
        if who:
            filtered = [e for e in filtered if e.who == who]
        # Most recent first
        return list(reversed(filtered))[:limit]
