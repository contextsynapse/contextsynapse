"""
Security Middleware
====================
Configurable security layer that wraps tool dispatch.
Applied at Context level (on ingest) and Runtime level (on access).

All features are OPTIONAL and configurable per global/context/session level.

Usage::

    # Global config
    middleware = SecurityMiddleware(SecurityConfig(
        pii_detection=True,
        auto_tagging=True,
        encryption=False,
        audit_trail=True,
    ))

    # Wrap tool dispatch
    result = middleware.wrap_dispatch("search_nodes", ctx, params, tool_fn)

    # On ingest — secure node before storage
    secured_props = middleware.secure_on_ingest(node_properties, context_config)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class SecurityConfig:
    """Security configuration — can be set globally, per-context, or per-session."""
    enabled: bool = True
    pii_detection: bool = True
    auto_tagging: bool = True
    encryption: bool = False           # opt-in (needs AICONTEXTDB_ENCRYPTION_KEY)
    audit_trail: bool = True
    redaction_mode: str = "mask"       # redact | mask | hash | allow
    encrypt_fields: List[str] = field(default_factory=lambda: [
        "email", "phone", "ssn", "credit_card", "aadhaar", "address",
    ])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "enabled": self.enabled,
            "pii_detection": self.pii_detection,
            "auto_tagging": self.auto_tagging,
            "encryption": self.encryption,
            "audit_trail": self.audit_trail,
            "redaction_mode": self.redaction_mode,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "SecurityConfig":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})

    @classmethod
    def disabled(cls) -> "SecurityConfig":
        """All security features off."""
        return cls(enabled=False, pii_detection=False, auto_tagging=False,
                   encryption=False, audit_trail=False, redaction_mode="allow")


@dataclass
class AgentClearance:
    """Per-agent security clearance within a session."""
    agent_id: str
    clearance_level: str = "public"    # public | internal | confidential | restricted
    pii_access: List[str] = field(default_factory=list)  # PII types this agent can see
    can_write_encrypted: bool = False

    # Sensitivity hierarchy
    _LEVELS = {"public": 0, "internal": 1, "confidential": 2, "restricted": 3}

    def can_access(self, node_sensitivity: str) -> bool:
        """Check if this agent can access a node at the given sensitivity."""
        agent_level = self._LEVELS.get(self.clearance_level, 0)
        node_level = self._LEVELS.get(node_sensitivity, 0)
        return agent_level >= node_level


# Read tools — filter results after dispatch
_READ_TOOLS = frozenset({
    "search_nodes", "search", "ask", "rag_query", "rag_graph",
    "graph_summary", "briefing", "get_context", "get_agent_context",
    "list_tasks", "my_tasks", "task_context", "recall",
    "query_graph", "reason",
})

# Write tools — secure content before writing
_WRITE_TOOLS = frozenset({
    "add_knowledge", "add_task", "add_decision", "add_relationship",
    "log_action", "remember", "complete_task",
})


class SecurityMiddleware:
    """Configurable security layer for tool dispatch.

    Features (all optional):
    - PII detection + redaction on read results
    - Auto-tagging on write operations
    - Field encryption on sensitive writes
    - Audit trail on every operation
    - Agent clearance filtering
    """

    def __init__(self, config: Optional[SecurityConfig] = None):
        self._global_config = config or SecurityConfig()
        # Per-context and per-session overrides
        self._context_configs: Dict[str, SecurityConfig] = {}
        self._session_configs: Dict[str, SecurityConfig] = {}
        self._agent_clearances: Dict[str, AgentClearance] = {}

        # Lazy-init security services
        self._pii = None
        self._tagger = None
        self._encryptor = None
        self._audit = None

    # ── Configuration ─────────────────────────────

    def set_context_config(self, context_id: str, config: SecurityConfig):
        """Override security config for a specific context."""
        self._context_configs[context_id] = config

    def set_session_config(self, session_id: str, config: SecurityConfig):
        """Override security config for a specific session."""
        self._session_configs[session_id] = config

    def set_agent_clearance(self, agent_id: str, clearance: AgentClearance):
        """Set clearance level for an agent."""
        self._agent_clearances[agent_id] = clearance

    def get_effective_config(self, context_id: str = "",
                              session_id: str = "") -> SecurityConfig:
        """Get the effective config (session > context > global)."""
        if session_id and session_id in self._session_configs:
            return self._session_configs[session_id]
        if context_id and context_id in self._context_configs:
            return self._context_configs[context_id]
        return self._global_config

    # ── Lazy service init ─────────────────────────

    def _get_pii(self):
        if self._pii is None:
            from .pii import PIIDetector
            self._pii = PIIDetector()
        return self._pii

    def _get_tagger(self):
        if self._tagger is None:
            from .auto_tagger import AutoTagger
            self._tagger = AutoTagger()
        return self._tagger

    def _get_encryptor(self):
        if self._encryptor is None:
            from .encryption import FieldEncryptor
            self._encryptor = FieldEncryptor()
        return self._encryptor

    def _get_audit(self):
        if self._audit is None:
            from .audit_trail import get_audit_trail
            self._audit = get_audit_trail()
        return self._audit

    # ── Ingest-time security (Context level) ──────

    def secure_on_ingest(self, properties: Dict[str, Any],
                          context_id: str = "") -> Dict[str, Any]:
        """Apply security on ingest — before node is stored in Context.

        Called by the ingestion pipeline when creating nodes.
        """
        config = self.get_effective_config(context_id=context_id)
        if not config.enabled:
            return properties

        secured = dict(properties)

        # 1. Auto-tag (classify sensitivity)
        if config.auto_tagging:
            secured = self._get_tagger().tag_node(secured)

        # 2. PII detection + flag
        if config.pii_detection:
            text_fields = ["content", "description", "name", "title", "statement"]
            combined = " ".join(str(secured.get(f, "")) for f in text_fields)
            scan = self._get_pii().scan(combined)
            if scan.pii_found:
                secured["pii_detected"] = True
                secured["_pii_types"] = [t for _, t in scan.entities]

        # 3. Encrypt sensitive fields
        if config.encryption:
            secured = self._get_encryptor().encrypt_properties(
                secured, fields=config.encrypt_fields,
            )

        # 4. Audit
        if config.audit_trail:
            self._get_audit().log(
                actor="system:ingest",
                action="node_ingest",
                operation_type="write",
                resource_type="node",
                namespace=context_id,
                details={
                    "sensitivity": secured.get("sensitivity", "public"),
                    "pii_detected": secured.get("pii_detected", False),
                    "encrypted": bool(secured.get("_encrypted_fields")),
                },
            )

        return secured

    # ── Access-time security (Runtime level) ──────

    def wrap_dispatch(
        self,
        tool_name: str,
        ctx: Any,  # ToolContext
        params: Dict[str, Any],
        dispatch_fn: Callable,
        context_id: str = "",
        session_id: str = "",
    ) -> str:
        """Wrap a tool dispatch with security checks.

        Pre-dispatch: audit + clearance check
        Post-dispatch: filter + redact results
        """
        config = self.get_effective_config(context_id, session_id)
        if not config.enabled:
            return dispatch_fn(tool_name, ctx, params)

        agent_id = getattr(ctx, "agent_id", "") or ""
        agent_name = getattr(ctx, "agent_name", "") or ""
        start = time.time()

        # ── Pre-dispatch: audit the request ──
        if config.audit_trail:
            self._get_audit().log(
                actor=f"agent:{agent_id}" if agent_id else "user:unknown",
                action=tool_name,
                operation_type="write" if tool_name in _WRITE_TOOLS else "read",
                namespace=session_id or context_id,
                details={"params": {k: str(v)[:100] for k, v in params.items()}},
                actor_type="agent" if agent_id else "user",
            )

        # ── Dispatch the tool ──
        result = dispatch_fn(tool_name, ctx, params)

        # ── Post-dispatch: secure the result ──

        # For WRITE tools: auto-tag + encrypt the content being written
        if tool_name in _WRITE_TOOLS and config.auto_tagging:
            content = params.get("content", "")
            if content and config.pii_detection:
                scan = self._get_pii().scan(content)
                if scan.pii_found:
                    logger.info("[SECURITY] PII detected in %s write by %s",
                                tool_name, agent_name)

        # For READ tools: redact PII from results based on agent clearance
        if tool_name in _READ_TOOLS and config.pii_detection:
            clearance = self._agent_clearances.get(agent_id)
            if clearance and config.redaction_mode != "allow":
                from .pii import RedactionMode
                mode = RedactionMode(config.redaction_mode)
                result = self._get_pii().mask(result, mode=mode)

        # ── Post-dispatch: audit the result ──
        if config.audit_trail:
            elapsed = int((time.time() - start) * 1000)
            self._get_audit().log(
                actor=f"agent:{agent_id}" if agent_id else "user:unknown",
                action=f"{tool_name}:result",
                operation_type="read",
                namespace=session_id or context_id,
                details={
                    "duration_ms": elapsed,
                    "result_length": len(result) if result else 0,
                },
                actor_type="agent" if agent_id else "user",
            )

        return result

    # ── Utility ───────────────────────────────────

    def filter_nodes_by_clearance(
        self,
        nodes: List[Dict[str, Any]],
        agent_id: str,
    ) -> List[Dict[str, Any]]:
        """Filter a list of nodes based on agent's clearance level.

        Used by search tools to remove nodes the agent can't see.
        """
        clearance = self._agent_clearances.get(agent_id)
        if not clearance:
            # No clearance set — default to public only
            return [n for n in nodes
                    if n.get("sensitivity", "public") == "public"]

        return [n for n in nodes if clearance.can_access(n.get("sensitivity", "public"))]


# ── Global singleton ──────────────────────────

_middleware: Optional[SecurityMiddleware] = None


def get_security_middleware(config: Optional[SecurityConfig] = None) -> SecurityMiddleware:
    """Get or create the global security middleware."""
    global _middleware
    if _middleware is None:
        _middleware = SecurityMiddleware(config)
    return _middleware


def configure_security(config: SecurityConfig):
    """Reconfigure the global security middleware."""
    global _middleware
    _middleware = SecurityMiddleware(config)
