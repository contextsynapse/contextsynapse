"""GovernanceLayer -- wraps ContextEngine with governance controls.

Every operation goes through:
  1. Access check (can this agent/user do this?)
  2. PII scan (does the content contain sensitive data?)
  3. Execute (delegate to engine)
  4. Audit log (record what happened)
  5. Lineage track (record data flow)
"""
from __future__ import annotations

import hashlib
import logging
import re
import threading
import time
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Set

logger = logging.getLogger(__name__)

_global_governance: Optional["GovernanceLayer"] = None
_gov_lock = threading.Lock()


# Built-in PII pattern library (select which to enable via PIIConfig)
_PII_PATTERN_LIBRARY = {
    # Universal
    "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
    "phone": re.compile(r'\b(?:\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}\b'),
    "ip_address": re.compile(r'\b\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}\b'),
    # US
    "ssn": re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
    "credit_card": re.compile(r'\b(?:\d{4}[-\s]?){3}\d{4}\b'),
    # India
    "pan": re.compile(r'\b[A-Z]{5}\d{4}[A-Z]\b'),
    "aadhaar": re.compile(r'\b\d{4}\s?\d{4}\s?\d{4}\b'),
    # Healthcare
    "medical_record": re.compile(r'\bMRN[-:\s]?\d{6,10}\b', re.IGNORECASE),
    "npi": re.compile(r'\b\d{10}\b'),  # US National Provider Identifier
    # Financial
    "iban": re.compile(r'\b[A-Z]{2}\d{2}[A-Z0-9]{4}\d{7}([A-Z0-9]?){0,16}\b'),
    "swift": re.compile(r'\b[A-Z]{6}[A-Z0-9]{2}([A-Z0-9]{3})?\b'),
    "account_number": re.compile(r'\b\d{9,18}\b'),
}

# Preset profiles
_PII_PRESETS = {
    "off": [],
    "minimal": ["email", "phone"],
    "standard": ["email", "phone", "ssn", "credit_card", "pan", "aadhaar"],
    "finance": ["email", "phone", "ssn", "credit_card", "pan", "aadhaar", "iban", "swift", "account_number"],
    "healthcare": ["email", "phone", "ssn", "medical_record", "npi"],
    "strict": list(_PII_PATTERN_LIBRARY.keys()),
}


class PIIConfig:
    """Configurable PII detection.

    Usage:
        # Use a preset
        config = PIIConfig(preset="finance")

        # Custom selection
        config = PIIConfig(enabled=["email", "pan", "aadhaar"])

        # Add custom patterns
        config = PIIConfig(preset="standard")
        config.add_pattern("employee_id", r'EMP-\\d{6}')

        # Disable entirely
        config = PIIConfig(preset="off")
    """

    def __init__(self, preset: str = "standard", enabled: List[str] = None):
        if enabled is not None:
            self._enabled = set(enabled)
        elif preset in _PII_PRESETS:
            self._enabled = set(_PII_PRESETS[preset])
        else:
            self._enabled = set(_PII_PRESETS["standard"])

        self._custom_patterns: Dict[str, re.Pattern] = {}
        self.mask_char = "*"
        self.on_detect = "log"  # "log", "mask", "block", "redact"

    @property
    def patterns(self) -> Dict[str, re.Pattern]:
        """Active patterns = library patterns (filtered) + custom patterns."""
        active = {k: v for k, v in _PII_PATTERN_LIBRARY.items() if k in self._enabled}
        active.update(self._custom_patterns)
        return active

    def add_pattern(self, name: str, regex: str, flags: int = 0):
        """Add a custom PII pattern."""
        self._custom_patterns[name] = re.compile(regex, flags)
        self._enabled.add(name)

    def remove_pattern(self, name: str):
        """Disable a pattern."""
        self._enabled.discard(name)
        self._custom_patterns.pop(name, None)

    def enable(self, *names: str):
        """Enable additional patterns."""
        for n in names:
            self._enabled.add(n)

    def disable(self, *names: str):
        """Disable patterns."""
        for n in names:
            self._enabled.discard(n)

    @property
    def enabled_list(self) -> List[str]:
        return sorted(self._enabled)

    @staticmethod
    def available_patterns() -> List[str]:
        """List all available pattern names."""
        return sorted(_PII_PATTERN_LIBRARY.keys())

    @staticmethod
    def available_presets() -> Dict[str, List[str]]:
        """List all presets and their patterns."""
        return {k: sorted(v) for k, v in _PII_PRESETS.items()}


class AccessPolicy:
    """Defines what an agent/role can access."""
    def __init__(self):
        self.rules: Dict[str, Dict[str, Set[str]]] = defaultdict(lambda: defaultdict(set))
        # Default: all roles can read, only admin/owner can write
        self.add_rule("*", "read", {"admin", "owner", "analyst", "viewer", "agent"})
        self.add_rule("*", "write", {"admin", "owner"})
        self.add_rule("*", "ingest", {"admin", "owner", "agent"})
        self.add_rule("memory", "write", {"admin", "owner", "agent"})
        self.add_rule("memory", "read", {"admin", "owner", "agent", "analyst"})

    def add_rule(self, resource: str, action: str, roles: Set[str]):
        self.rules[resource][action] = roles

    def check(self, role: str, resource: str, action: str) -> bool:
        # Check specific resource first, then wildcard
        for r in [resource, "*"]:
            if r in self.rules and action in self.rules[r]:
                return role in self.rules[r][action]
        return False


class AuditEntry:
    """Single audit log entry."""
    __slots__ = ("timestamp", "agent_id", "action", "resource", "resource_id",
                 "status", "details", "duration_ms")

    def __init__(self, agent_id, action, resource, resource_id="",
                 status="success", details="", duration_ms=0):
        self.timestamp = datetime.now(timezone.utc).isoformat()
        self.agent_id = agent_id
        self.action = action
        self.resource = resource
        self.resource_id = resource_id
        self.status = status
        self.details = details
        self.duration_ms = duration_ms

    def to_dict(self):
        return {
            "timestamp": self.timestamp,
            "agent_id": self.agent_id,
            "action": self.action,
            "resource": self.resource,
            "resource_id": self.resource_id,
            "status": self.status,
            "details": self.details,
            "duration_ms": self.duration_ms,
        }


class LineageRecord:
    """Tracks data flow: source -> operation -> destination."""
    __slots__ = ("source", "operation", "destination", "agent_id", "timestamp")

    def __init__(self, source, operation, destination, agent_id="system"):
        self.source = source
        self.operation = operation
        self.destination = destination
        self.agent_id = agent_id
        self.timestamp = datetime.now(timezone.utc).isoformat()

    def to_dict(self):
        return {
            "source": self.source,
            "operation": self.operation,
            "destination": self.destination,
            "agent_id": self.agent_id,
            "timestamp": self.timestamp,
        }


class GovernanceLayer:
    """Unified governance: access control + audit + PII + lineage + retention.

    Wraps ContextEngine operations with governance controls.

    Usage:
        gov = get_governance(engine)

        # Governed ingest (checks access, scans PII, logs audit, tracks lineage)
        gov.ingest(agent_id="agent-1", role="agent", items=[...])

        # Governed search
        results = gov.search(agent_id="analyst-1", role="analyst", query="TCS")

        # Governed memory
        gov.remember(agent_id="agent-1", role="agent", content="User prefers X")

        # Audit trail
        logs = gov.audit_log(last_n=50)

        # Lineage
        lineage = gov.get_lineage(item_id="passage_42")

        # PII report
        report = gov.pii_scan_report()
    """

    def __init__(self, engine=None, pii: str = "standard"):
        """
        Args:
            engine: ContextEngine instance.
            pii: PII preset name or "off" to disable.
                 Presets: "off", "minimal", "standard", "finance", "healthcare", "strict"
        """
        self._engine = engine
        self._policy = AccessPolicy()
        self._pii_config = PIIConfig(preset=pii)
        self._audit: List[AuditEntry] = []
        self._lineage: List[LineageRecord] = []
        self._pii_detections: Dict[str, List[str]] = {}
        self._retention_policies: Dict[str, int] = {}
        self._max_audit = 10000
        self._max_audit = 10000

    def set_engine(self, engine):
        self._engine = engine

    @property
    def engine(self):
        if self._engine is None:
            from contextsynapse.engine import get_engine
            self._engine = get_engine()
        return self._engine

    # ── Access Control ──

    def check_access(self, role: str, resource: str, action: str) -> bool:
        """Check if a role can perform an action on a resource."""
        return self._policy.check(role, resource, action)

    def add_access_rule(self, resource: str, action: str, roles: Set[str]):
        """Add a custom access rule."""
        self._policy.add_rule(resource, action, roles)

    def _require_access(self, agent_id: str, role: str, resource: str, action: str):
        if not self.check_access(role, resource, action):
            self._log_audit(agent_id, action, resource, status="denied",
                            details=f"Role '{role}' denied for {action} on {resource}")
            raise PermissionError(
                f"Access denied: role '{role}' cannot '{action}' on '{resource}'"
            )

    # ── PII Detection ──

    @property
    def pii_config(self) -> PIIConfig:
        """Access PII configuration."""
        return self._pii_config

    def configure_pii(self, preset: str = None, enable: List[str] = None,
                       disable: List[str] = None, action: str = None):
        """Configure PII detection at runtime.

        Args:
            preset: Switch to a preset ("off", "minimal", "standard", "finance", "healthcare", "strict")
            enable: Enable specific patterns (e.g. ["iban", "swift"])
            disable: Disable specific patterns (e.g. ["ip_address"])
            action: What to do on detection ("log", "mask", "block", "redact")
        """
        if preset:
            self._pii_config = PIIConfig(preset=preset)
        if enable:
            self._pii_config.enable(*enable)
        if disable:
            self._pii_config.disable(*disable)
        if action:
            self._pii_config.on_detect = action
        logger.info("PII config updated: enabled=%s, action=%s",
                     self._pii_config.enabled_list, self._pii_config.on_detect)

    def scan_pii(self, text: str) -> List[str]:
        """Scan text for PII using configured patterns."""
        if not text or not isinstance(text, str):
            return []
        detected = []
        for pii_type, pattern in self._pii_config.patterns.items():
            if pattern.search(text):
                detected.append(pii_type)
        return detected

    def mask_pii(self, text: str) -> str:
        """Replace detected PII with type labels."""
        if not text:
            return text
        masked = text
        for pii_type, pattern in self._pii_config.patterns.items():
            label = f"[{pii_type.upper()}]"
            masked = pattern.sub(label, masked)
        return masked

    def _scan_items_pii(self, items: List[Dict]) -> int:
        """Scan a batch of items for PII. Returns count of items with PII."""
        count = 0
        for item in items:
            props = item.get("properties", item)
            for key in ("text", "content", "statement", "description"):
                text = props.get(key, "")
                if text:
                    pii = self.scan_pii(text)
                    if pii:
                        item_id = item.get("id", "unknown")
                        self._pii_detections[item_id] = pii
                        count += 1
        return count

    # ── Audit ──

    def _log_audit(self, agent_id: str, action: str, resource: str,
                   resource_id: str = "", status: str = "success",
                   details: str = "", duration_ms: float = 0):
        entry = AuditEntry(agent_id, action, resource, resource_id,
                           status, details, round(duration_ms, 2))
        self._audit.append(entry)
        if len(self._audit) > self._max_audit:
            self._audit = self._audit[-self._max_audit:]

    def audit_log(self, last_n: int = 50, agent_id: str = None,
                  action: str = None) -> List[Dict]:
        """Get audit log entries."""
        entries = self._audit
        if agent_id:
            entries = [e for e in entries if e.agent_id == agent_id]
        if action:
            entries = [e for e in entries if e.action == action]
        return [e.to_dict() for e in entries[-last_n:]]

    # ── Lineage ──

    def _track_lineage(self, source: str, operation: str, destination: str,
                       agent_id: str = "system"):
        self._lineage.append(LineageRecord(source, operation, destination, agent_id))

    def get_lineage(self, item_id: str) -> List[Dict]:
        """Get data lineage for an item (where it came from, what derived from it)."""
        return [l.to_dict() for l in self._lineage
                if l.source == item_id or l.destination == item_id]

    # ── Retention ──

    def set_retention(self, content_type: str, ttl_hours: int):
        """Set retention policy for a content type."""
        self._retention_policies[content_type] = ttl_hours
        logger.info("Retention: %s = %d hours", content_type, ttl_hours)

    def enforce_retention(self) -> int:
        """Delete expired content based on retention policies. Returns count deleted."""
        deleted = 0
        now = datetime.now(timezone.utc)
        store = self.engine.store
        for content_type, ttl_hours in self._retention_policies.items():
            cutoff = (now - timedelta(hours=ttl_hours)).isoformat()
            try:
                result = store.query(
                    "DELETE FROM content WHERE type = ? AND created_at < ? RETURNING id",
                    [content_type, cutoff]
                )
                deleted += len(result)
            except Exception as e:
                logger.warning("Retention enforcement failed for %s: %s", content_type, e)
        if deleted:
            logger.info("Retention: deleted %d expired items", deleted)
        return deleted

    # ── Governed Operations ──

    def ingest(self, agent_id: str, role: str, items: List[Dict],
               namespace: str = None, mask_pii: bool = False) -> Dict[str, Any]:
        """Governed ingest: access check -> PII scan -> ingest -> audit -> lineage."""
        self._require_access(agent_id, role, "content", "ingest")

        # PII scan
        pii_count = self._scan_items_pii(items)
        if mask_pii and pii_count > 0:
            for item in items:
                props = item.get("properties", item)
                for key in ("text", "content", "statement"):
                    if key in props and isinstance(props[key], str):
                        props[key] = self.mask_pii(props[key])

        # Ingest
        t0 = time.perf_counter()
        counts = self.engine.ingest(items, namespace=namespace)
        elapsed = (time.perf_counter() - t0) * 1000

        # Audit
        self._log_audit(agent_id, "ingest", "content",
                        details=f"{len(items)} items, PII={pii_count}",
                        duration_ms=elapsed)

        # Lineage
        for item in items:
            if item.get("id"):
                self._track_lineage(
                    source=item.get("properties", {}).get("url", item.get("properties", {}).get("doc_id", "external")),
                    operation="ingest",
                    destination=item["id"],
                    agent_id=agent_id,
                )

        counts["pii_detected"] = pii_count
        return counts

    def search(self, agent_id: str, role: str, query: str, **kwargs) -> Dict[str, Any]:
        """Governed search: access check -> search -> audit."""
        self._require_access(agent_id, role, "content", "read")

        t0 = time.perf_counter()
        results = self.engine.search(query, **kwargs)
        elapsed = (time.perf_counter() - t0) * 1000

        self._log_audit(agent_id, "search", "content",
                        details=f"query='{query[:50]}', results={results.get('result_count', 0)}",
                        duration_ms=elapsed)
        return results

    def remember(self, agent_id: str, role: str, content: str, **kwargs) -> str:
        """Governed memory write."""
        self._require_access(agent_id, role, "memory", "write")

        # PII check on memory content
        pii = self.scan_pii(content)
        if pii:
            self._pii_detections[f"memory_{agent_id}"] = pii
            logger.warning("PII detected in memory from %s: %s", agent_id, pii)

        mem_id = self.engine.remember(agent_id, content, **kwargs)
        self._log_audit(agent_id, "remember", "memory", resource_id=mem_id)
        return mem_id

    def recall(self, agent_id: str, role: str, **kwargs) -> List[Dict]:
        """Governed memory read."""
        self._require_access(agent_id, role, "memory", "read")
        results = self.engine.recall(agent_id=agent_id, **kwargs)
        self._log_audit(agent_id, "recall", "memory",
                        details=f"{len(results)} memories")
        return results

    def get(self, agent_id: str, role: str, item_id: str) -> Optional[Dict]:
        """Governed item read."""
        self._require_access(agent_id, role, "content", "read")
        result = self.engine.get(item_id)
        self._log_audit(agent_id, "get", "content", resource_id=item_id)
        return result

    # ── Reports ──

    def pii_report(self) -> Dict[str, Any]:
        """Report of all PII detections."""
        return {
            "total_items_with_pii": len(self._pii_detections),
            "by_type": dict(defaultdict(int, {
                pii_type: sum(1 for types in self._pii_detections.values() if pii_type in types)
                for pii_type in set(t for types in self._pii_detections.values() for t in types)
            })),
            "recent": dict(list(self._pii_detections.items())[-10:]),
        }

    def governance_status(self) -> Dict[str, Any]:
        """Full governance status report."""
        return {
            "audit_entries": len(self._audit),
            "lineage_records": len(self._lineage),
            "pii_detections": len(self._pii_detections),
            "retention_policies": self._retention_policies,
            "access_rules": {
                resource: {action: list(roles) for action, roles in actions.items()}
                for resource, actions in self._policy.rules.items()
            },
        }


def get_governance(engine=None) -> GovernanceLayer:
    """Get or create the global governance layer."""
    global _global_governance
    if _global_governance is None:
        with _gov_lock:
            if _global_governance is None:
                _global_governance = GovernanceLayer(engine=engine)
    if engine and _global_governance._engine is None:
        _global_governance.set_engine(engine)
    return _global_governance
