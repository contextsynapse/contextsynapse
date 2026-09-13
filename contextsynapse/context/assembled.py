"""AssembledContext — the single security gate for context composition.

Every context delivery to an FM, agent, or LLM flows through AssembledContext.
It resolves atomic contexts from purpose templates, enforces ACL per-atomic,
logs the assembly, and can freeze a snapshot for SEBI trade audit.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional

from .acl import JWTIdentity, ContextPermission, check_context_access
from .frozen import FrozenContext, freeze_context

logger = logging.getLogger(__name__)


class ContextPurpose(str, Enum):
    PRE_TRADE = "pre_trade"
    MORNING_BRIEF = "morning_brief"
    COMPLIANCE_AUDIT = "compliance_audit"
    REBALANCE = "rebalance"
    CLIENT_REPORT = "client_report"
    STOCK_ANALYSIS = "stock_analysis"
    RISK_REVIEW = "risk_review"
    AD_HOC = "ad_hoc"


# Purpose templates define which atomic contexts each purpose auto-includes.
# Placeholders: {stock}, {portfolio_id}, {client_id} are resolved at assembly time.
PURPOSE_TEMPLATES: Dict[ContextPurpose, Dict[str, Any]] = {
    ContextPurpose.PRE_TRADE: {
        "atomics": [
            "market:{stock}", "market:{stock}_price",
            "market:regulatory_legal", "market:negative_signals",
            "portfolio:{portfolio_id}:holdings",
        ],
        "budget": "standard",
    },
    ContextPurpose.MORNING_BRIEF: {
        "atomics": [
            "market:india_economy", "market:nifty_50",
        ],
        "budget": "deep",
    },
    ContextPurpose.COMPLIANCE_AUDIT: {
        "atomics": [
            "portfolio:{portfolio_id}:holdings",
            "portfolio:{portfolio_id}:trades",
            "market:regulatory_legal",
        ],
        "budget": "deep",
    },
    ContextPurpose.REBALANCE: {
        "atomics": [
            "portfolio:{portfolio_id}:holdings",
        ],
        "budget": "standard",
    },
    ContextPurpose.CLIENT_REPORT: {
        "atomics": [
            "portfolio:{portfolio_id}:holdings",
            "portfolio:{portfolio_id}:nav",
            "client:{client_id}:mandate",
        ],
        "budget": "standard",
    },
    ContextPurpose.STOCK_ANALYSIS: {
        "atomics": [
            "market:{stock}", "market:{stock}_price",
            "market:india_economy", "market:negative_signals",
        ],
        "budget": "deep",
    },
    ContextPurpose.RISK_REVIEW: {
        "atomics": [
            "portfolio:{portfolio_id}:holdings",
            "market:negative_signals", "market:vix",
        ],
        "budget": "standard",
    },
    ContextPurpose.AD_HOC: {
        "atomics": [],
        "budget": "standard",
    },
}


def resolve_atomics(
    purpose: ContextPurpose,
    subject: str = "",
    portfolio_id: str = "",
    client_id: str = "",
) -> List[str]:
    """Expand a purpose template into concrete atomic context paths."""
    template = PURPOSE_TEMPLATES[purpose]
    result = []
    for pattern in template["atomics"]:
        path = pattern.format(
            stock=subject, portfolio_id=portfolio_id, client_id=client_id,
        )
        # Skip paths with unresolved placeholders
        if "{" in path:
            continue
        result.append(path)
    return result


def _make_id(purpose: ContextPurpose, subject: str) -> str:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    suffix = f"_{subject}" if subject else ""
    return f"ac_{ts}_{purpose.value}{suffix}"


@dataclass
class AssembledContext:
    """Composed context with ACL enforcement and audit trail."""

    id: str
    purpose: ContextPurpose
    assembled_by: JWTIdentity
    assembled_at: str

    atomics_granted: List[str] = field(default_factory=list)
    atomics_denied: List[str] = field(default_factory=list)

    budget: str = "standard"
    frozen: bool = False

    @classmethod
    def create(
        cls,
        purpose: ContextPurpose,
        user: JWTIdentity,
        subject: str = "",
        portfolio_id: str = "",
        client_id: str = "",
    ) -> "AssembledContext":
        """Create an assembled context, enforcing ACL on each atomic."""
        atomics = resolve_atomics(purpose, subject, portfolio_id, client_id)
        template = PURPOSE_TEMPLATES[purpose]

        granted = []
        denied = []
        for path in atomics:
            if check_context_access(user, path, ContextPermission.READ):
                granted.append(path)
            else:
                denied.append(path)

        return cls(
            id=_make_id(purpose, subject),
            purpose=purpose,
            assembled_by=user,
            assembled_at=datetime.now(timezone.utc).isoformat(),
            atomics_granted=granted,
            atomics_denied=denied,
            budget=template.get("budget", "standard"),
        )

    def include(self, *context_paths: str) -> "AssembledContext":
        """Add atomic contexts (subject to ACL check)."""
        for path in context_paths:
            if path in self.atomics_granted:
                continue
            if check_context_access(self.assembled_by, path, ContextPermission.READ):
                self.atomics_granted.append(path)
            else:
                self.atomics_denied.append(path)
        return self

    def exclude(self, *context_paths: str) -> "AssembledContext":
        """Remove atomic contexts from this assembly."""
        for path in context_paths:
            if path in self.atomics_granted:
                self.atomics_granted.remove(path)
        return self

    def to_dict(self) -> Dict[str, Any]:
        """Serialize assembly metadata."""
        return {
            "id": self.id,
            "purpose": self.purpose.value,
            "assembled_by": self.assembled_by.sub,
            "assembled_at": self.assembled_at,
            "atomics_granted": self.atomics_granted,
            "atomics_denied": self.atomics_denied,
            "budget": self.budget,
            "frozen": self.frozen,
        }

    def to_snapshot(self, trade_id: str) -> FrozenContext:
        """Freeze this assembled context for a trade decision."""
        self.frozen = True
        return freeze_context(
            trade_id=trade_id,
            assembled_id=self.id,
            atomics=self.atomics_granted,
            content=self.to_dict(),
            frozen_by=self.assembled_by.sub,
        )
