"""Field-Level Encryption — platform capability for PII data at rest.

Verticals configure WHICH fields are PII and WHO can decrypt them.
Platform provides encryption, masking, and role-based access.

This complements pii.py (text scanning/detection) with field-level
encryption for structured data in PostgreSQL.

Usage:
    from contextsynapse.security.field_encryption import get_field_encryptor

    enc = get_field_encryptor()

    # Vertical registers its PII fields
    enc.register("pms_clients", {
        "pan":   {"mask": "last4", "decrypt_roles": ["pms_admin", "cio"]},
        "phone": {"mask": "phone", "decrypt_roles": ["pms_admin", "rm"]},
        "email": {"mask": "email", "decrypt_roles": ["pms_admin", "cio"]},
    })

    # Encrypt before storing
    encrypted = enc.encrypt_value("ABCPS1234K")

    # Mask for API response (role-aware)
    safe = enc.process_record(record, "pms_clients", user_roles=["fund_manager"])
    # → {"pan": "XXXXXX234K", "phone": "XXXXXXXX3210", "name": "Rajesh Sharma"}
"""
from __future__ import annotations

import base64
import hashlib
import logging
import os
from typing import Any

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet
    _HAS_FERNET = True
except ImportError:
    _HAS_FERNET = False
    logger.info("[ENCRYPTION] cryptography not installed — using base64 obfuscation")


def _get_key() -> bytes:
    """Get or generate encryption key."""
    key_env = os.environ.get("CONTEXTSYNAPSE_ENCRYPTION_KEY", "")
    if key_env:
        raw = hashlib.sha256(key_env.encode()).digest()
        return base64.urlsafe_b64encode(raw)

    data_dir = os.environ.get("CONTEXTSYNAPSE_STORAGE_DIR", "contextsynapse_data")
    key_file = os.path.join(data_dir, ".encryption_key")
    if os.path.exists(key_file):
        return open(key_file, "rb").read().strip()

    key = Fernet.generate_key() if _HAS_FERNET else base64.urlsafe_b64encode(os.urandom(32))
    os.makedirs(data_dir, exist_ok=True)
    with open(key_file, "wb") as f:
        f.write(key)
    logger.info("[ENCRYPTION] Generated key at %s", key_file)
    return key


# ── Masking functions ────────────────────────────────────────────

def mask_last4(value: str) -> str:
    if len(value) <= 4:
        return "X" * len(value)
    return "X" * (len(value) - 4) + value[-4:]

def mask_email(value: str) -> str:
    if "@" not in value:
        return "X" * len(value)
    local, domain = value.split("@", 1)
    if len(local) <= 2:
        return local[0] + "***@" + domain
    return local[0] + "***" + local[-1] + "@" + domain

def mask_phone(value: str) -> str:
    digits = "".join(c for c in value if c.isdigit())
    if len(digits) <= 4:
        return "X" * len(digits)
    return "X" * (len(digits) - 4) + digits[-4:]

def mask_full(value: str) -> str:
    return "X" * min(len(value), 12)

MASK_FN = {"last4": mask_last4, "email": mask_email, "phone": mask_phone, "full": mask_full}


class FieldEncryptor:
    """Configurable field-level encryption + masking."""

    def __init__(self):
        self._configs: dict[str, dict[str, dict]] = {}
        self._fernet = None
        try:
            key = _get_key()
            if _HAS_FERNET:
                self._fernet = Fernet(key)
        except Exception as e:
            logger.warning("[ENCRYPTION] Init failed: %s", e)

    # ── Config ───────────────────────────────────────────────────

    def register(self, table: str, fields: dict[str, dict]):
        """Register PII field config for a table.

        fields: {"pan": {"mask": "last4", "decrypt_roles": ["admin", "cio"]}}
        """
        self._configs[table] = fields

    def pii_fields(self, table: str) -> list[str]:
        return list(self._configs.get(table, {}).keys())

    # ── Encrypt / Decrypt ────────────────────────────────────────

    def encrypt_value(self, value: str) -> str:
        if not value or not isinstance(value, str) or value.startswith("enc:"):
            return value
        if self._fernet:
            return "enc:" + self._fernet.encrypt(value.encode()).decode()
        return "b64:" + base64.b64encode(value.encode()).decode()

    def decrypt_value(self, value: str) -> str:
        if not value or not isinstance(value, str):
            return value
        if value.startswith("enc:") and self._fernet:
            try:
                return self._fernet.decrypt(value[4:].encode()).decode()
            except Exception:
                return value
        if value.startswith("b64:"):
            try:
                return base64.b64decode(value[4:]).decode()
            except Exception:
                return value
        return value

    def mask_value(self, value: str, mask_type: str = "last4") -> str:
        raw = self.decrypt_value(value) if value.startswith(("enc:", "b64:")) else value
        return MASK_FN.get(mask_type, mask_last4)(raw)

    # ── Record-level ─────────────────────────────────────────────

    def encrypt_record(self, record: dict, table: str) -> dict:
        """Encrypt all PII fields before storage."""
        config = self._configs.get(table)
        if not config:
            return record
        out = dict(record)
        for field in config:
            if field in out and out[field] and not str(out[field]).startswith(("enc:", "b64:")):
                out[field] = self.encrypt_value(str(out[field]))
        return out

    def process_record(self, record: dict, table: str,
                        user_roles: list[str] = None) -> dict:
        """Process a record for API output — decrypt for authorized, mask for others."""
        config = self._configs.get(table)
        if not config:
            return record
        out = dict(record)
        for field, cfg in config.items():
            if field not in out or not out[field]:
                continue
            val = str(out[field])
            allowed = set(cfg.get("decrypt_roles", ["admin"]))
            if user_roles and (set(user_roles) & allowed):
                out[field] = self.decrypt_value(val)
            else:
                out[field] = self.mask_value(val, cfg.get("mask", "last4"))
        return out


# ── Singleton ────────────────────────────────────────────────────

_instance: FieldEncryptor | None = None

def get_field_encryptor() -> FieldEncryptor:
    global _instance
    if _instance is None:
        _instance = FieldEncryptor()
    return _instance
