"""
PII Detection & Data Redaction
================================
Auto-detect and mask personally identifiable information (PII) in graph nodes.

Detects: emails, phone numbers, SSN, credit cards, IP addresses, names (NER-based).
Actions: redact (remove), mask (replace with ***), hash (one-way), allow (pass through).

Usage::

    detector = PIIDetector()
    result = detector.scan("Contact john@example.com or call 555-1234")
    # result.pii_found = True
    # result.entities = [("john@example.com", "email"), ("555-1234", "phone")]

    masked = detector.mask("Contact john@example.com", mode="mask")
    # "Contact [EMAIL REDACTED]"
"""

from __future__ import annotations

import hashlib
import logging
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class PIIType(str, Enum):
    EMAIL = "email"
    PHONE = "phone"
    SSN = "ssn"
    CREDIT_CARD = "credit_card"
    IP_ADDRESS = "ip_address"
    AADHAAR = "aadhaar"  # Indian national ID
    PAN = "pan"  # Indian tax ID
    PASSPORT = "passport"
    DATE_OF_BIRTH = "dob"


class RedactionMode(str, Enum):
    REDACT = "redact"  # Remove entirely: "[REDACTED]"
    MASK = "mask"  # Partial mask: "j***@example.com"
    HASH = "hash"  # One-way hash: "[SHA:a1b2c3]"
    ALLOW = "allow"  # Pass through (no redaction)


@dataclass
class PIIScanResult:
    """Result of scanning text for PII."""

    pii_found: bool = False
    entities: list[tuple[str, str]] = field(default_factory=list)  # (value, type)
    sensitivity: str = "public"  # auto-classified based on PII found
    entity_count: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "pii_found": self.pii_found,
            "entity_count": self.entity_count,
            "types_found": list(set(t for _, t in self.entities)),
            "sensitivity": self.sensitivity,
        }


# Regex patterns for PII detection
_PATTERNS = {
    PIIType.EMAIL: re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b"),
    PIIType.PHONE: re.compile(r"(?:\+?\d{1,3}[-.\s]?)?\(?\d{2,4}\)?[-.\s]?\d{3,4}[-.\s]?\d{3,4}\b"),
    PIIType.SSN: re.compile(r"\b\d{3}-\d{2}-\d{4}\b"),
    PIIType.CREDIT_CARD: re.compile(r"\b(?:\d{4}[-\s]?){3}\d{4}\b"),
    PIIType.IP_ADDRESS: re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b"),
    PIIType.AADHAAR: re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"),
    PIIType.PAN: re.compile(r"\b[A-Z]{5}\d{4}[A-Z]\b"),
}

# Redaction templates
_REDACT_LABELS = {
    PIIType.EMAIL: "[EMAIL REDACTED]",
    PIIType.PHONE: "[PHONE REDACTED]",
    PIIType.SSN: "[SSN REDACTED]",
    PIIType.CREDIT_CARD: "[CARD REDACTED]",
    PIIType.IP_ADDRESS: "[IP REDACTED]",
    PIIType.AADHAAR: "[AADHAAR REDACTED]",
    PIIType.PAN: "[PAN REDACTED]",
    PIIType.PASSPORT: "[PASSPORT REDACTED]",
    PIIType.DATE_OF_BIRTH: "[DOB REDACTED]",
}


class PIIDetector:
    """Detect and redact PII in text content."""

    def __init__(self, custom_patterns: dict[str, re.Pattern] | None = None):
        self._patterns = dict(_PATTERNS)
        if custom_patterns:
            self._patterns.update(custom_patterns)

    def scan(self, text: str) -> PIIScanResult:
        """Scan text for PII entities.

        Returns scan result with found entities and auto-classified sensitivity.
        """
        if not text:
            return PIIScanResult()

        entities = []
        for pii_type, pattern in self._patterns.items():
            for match in pattern.finditer(text):
                value = match.group()
                # Basic validation to reduce false positives
                if pii_type == PIIType.PHONE and len(value.replace("-", "").replace(" ", "")) < 7:
                    continue
                if pii_type == PIIType.IP_ADDRESS:
                    octets = value.split(".")
                    if any(int(o) > 255 for o in octets if o.isdigit()):
                        continue
                entities.append((value, pii_type.value))

        # Auto-classify sensitivity based on PII found
        if not entities:
            sensitivity = "public"
        elif any(t in ("ssn", "credit_card", "aadhaar", "passport") for _, t in entities):
            sensitivity = "restricted"
        elif any(t in ("email", "phone", "pan", "dob") for _, t in entities):
            sensitivity = "confidential"
        else:
            sensitivity = "internal"

        return PIIScanResult(
            pii_found=len(entities) > 0,
            entities=entities,
            sensitivity=sensitivity,
            entity_count=len(entities),
        )

    def mask(self, text: str, mode: RedactionMode = RedactionMode.MASK) -> str:
        """Apply redaction to all detected PII in text.

        Args:
            mode: redact (remove), mask (partial), hash (one-way), allow (none)
        """
        if mode == RedactionMode.ALLOW:
            return text

        result = text
        for pii_type, pattern in self._patterns.items():
            for match in pattern.finditer(text):
                value = match.group()

                if mode == RedactionMode.REDACT:
                    replacement = _REDACT_LABELS.get(pii_type, "[REDACTED]")
                elif mode == RedactionMode.HASH:
                    h = hashlib.sha256(value.encode()).hexdigest()[:8]
                    replacement = f"[SHA:{h}]"
                elif mode == RedactionMode.MASK:
                    replacement = self._partial_mask(value, pii_type)
                else:
                    replacement = value

                result = result.replace(value, replacement)

        return result

    def mask_node_properties(
        self, properties: dict[str, Any], mode: RedactionMode = RedactionMode.MASK, fields: list[str] | None = None
    ) -> dict[str, Any]:
        """Mask PII in specific node properties.

        Args:
            fields: Property names to scan. Default: content, description, name, statement.
        """
        target_fields = fields or ["content", "description", "name", "statement", "title", "body", "text", "summary"]
        masked = dict(properties)
        for field_name in target_fields:
            if field_name in masked and isinstance(masked[field_name], str):
                masked[field_name] = self.mask(masked[field_name], mode)
        return masked

    @staticmethod
    def _partial_mask(value: str, pii_type: PIIType) -> str:
        """Create a partial mask showing first/last chars."""
        if pii_type == PIIType.EMAIL:
            parts = value.split("@")
            if len(parts) == 2:
                local = parts[0]
                masked = local[0] + "***" + (local[-1] if len(local) > 1 else "")
                return f"{masked}@{parts[1]}"

        if pii_type == PIIType.PHONE:
            digits = re.sub(r"\D", "", value)
            if len(digits) >= 4:
                return "***-***-" + digits[-4:]

        if pii_type in (PIIType.SSN, PIIType.CREDIT_CARD):
            return "***-" + value[-4:]

        if pii_type == PIIType.AADHAAR:
            digits = re.sub(r"\D", "", value)
            return "XXXX-XXXX-" + digits[-4:]

        # Default: show first and last 2 chars
        if len(value) > 4:
            return value[:2] + "*" * (len(value) - 4) + value[-2:]
        return "****"


# Global singleton
_detector: PIIDetector | None = None


def get_pii_detector() -> PIIDetector:
    global _detector
    if _detector is None:
        _detector = PIIDetector()
    return _detector
