"""
Data Encryption Layer
======================
Field-level encryption for graph node properties at rest.

Uses Fernet (AES-128-CBC) for symmetric encryption with key derivation
from a master secret. Supports searchable encryption via deterministic
hashing for keyword queries on encrypted fields.

For full homomorphic encryption on search, uses HMAC-based searchable
tokens that allow keyword matching without decrypting the full content.

Usage::

    enc = FieldEncryptor(master_key="your-secret-key")

    # Encrypt node properties before storage
    encrypted = enc.encrypt_properties({"name": "John", "email": "john@x.com"}, fields=["email"])
    # {"name": "John", "email": "gAAAAB...", "_encrypted_fields": ["email"]}

    # Decrypt after retrieval
    decrypted = enc.decrypt_properties(encrypted)
    # {"name": "John", "email": "john@x.com"}

    # Searchable token (allows keyword search on encrypted data)
    token = enc.searchable_token("john@x.com")
    # deterministic hash — same input always produces same token
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Try Fernet (cryptography package)
_FERNET_AVAILABLE = False
try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    _FERNET_AVAILABLE = True
except ImportError:
    logger.info("cryptography package not installed — using fallback encryption")


class FieldEncryptor:
    """Encrypt/decrypt individual node properties using Fernet (AES-128-CBC).

    If the `cryptography` package isn't installed, falls back to
    base64 encoding (NOT secure — for development only).
    """

    ENCRYPTED_PREFIX = "ENC:"
    SEARCH_PREFIX = "STOK:"

    def __init__(self, master_key: Optional[str] = None):
        self._master_key = master_key or os.environ.get("CONTEXTSYNAPSE_ENCRYPTION_KEY") or os.environ.get("AICONTEXTDB_ENCRYPTION_KEY", "")
        self._fernet = None
        self._hmac_key = b""

        if self._master_key and _FERNET_AVAILABLE:
            # Derive Fernet key from master key using PBKDF2
            salt = b"contextcore-field-encryption-v1"
            kdf = PBKDF2HMAC(
                algorithm=hashes.SHA256(),
                length=32,
                salt=salt,
                iterations=100_000,
            )
            key = base64.urlsafe_b64encode(kdf.derive(self._master_key.encode()))
            self._fernet = Fernet(key)
            # Derive HMAC key for searchable tokens
            self._hmac_key = hashlib.sha256(
                (self._master_key + ":search-token").encode()
            ).digest()
            logger.info("[ENCRYPTION] Fernet encryption initialized")
        elif self._master_key:
            # Fallback HMAC key without Fernet
            self._hmac_key = hashlib.sha256(
                (self._master_key + ":search-token").encode()
            ).digest()
            logger.warning("[ENCRYPTION] Using base64 fallback (install cryptography for real encryption)")

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt_value(self, value: str) -> str:
        """Encrypt a single string value."""
        if not value or not isinstance(value, str):
            return value
        if self._fernet:
            token = self._fernet.encrypt(value.encode())
            return self.ENCRYPTED_PREFIX + token.decode()
        else:
            # Fallback: base64 (NOT secure — development only)
            encoded = base64.b64encode(value.encode()).decode()
            return self.ENCRYPTED_PREFIX + "B64:" + encoded

    def decrypt_value(self, value: str) -> str:
        """Decrypt a single string value."""
        if not isinstance(value, str) or not value.startswith(self.ENCRYPTED_PREFIX):
            return value

        payload = value[len(self.ENCRYPTED_PREFIX):]

        if payload.startswith("B64:"):
            # Base64 fallback
            return base64.b64decode(payload[4:]).decode()

        if self._fernet:
            try:
                return self._fernet.decrypt(payload.encode()).decode()
            except Exception as e:
                logger.warning("[ENCRYPTION] Decrypt failed: %s", e)
                return "[DECRYPTION FAILED]"

        return value

    def encrypt_properties(self, properties: Dict[str, Any],
                            fields: Optional[List[str]] = None) -> Dict[str, Any]:
        """Encrypt specified fields in a properties dict.

        Args:
            fields: Which fields to encrypt. Default: content, email, phone, ssn.
        """
        if not self._master_key:
            return properties

        target_fields = fields or ["content", "email", "phone", "ssn",
                                    "credit_card", "aadhaar", "address"]
        encrypted = dict(properties)
        encrypted_fields = []

        for field_name in target_fields:
            if field_name in encrypted and isinstance(encrypted[field_name], str):
                encrypted[field_name] = self.encrypt_value(encrypted[field_name])
                encrypted_fields.append(field_name)

                # Generate searchable token for encrypted field
                token = self.searchable_token(str(properties[field_name]))
                encrypted[f"_stok_{field_name}"] = token

        if encrypted_fields:
            encrypted["_encrypted_fields"] = encrypted_fields

        return encrypted

    def decrypt_properties(self, properties: Dict[str, Any]) -> Dict[str, Any]:
        """Decrypt all encrypted fields in a properties dict."""
        encrypted_fields = properties.get("_encrypted_fields", [])
        if not encrypted_fields:
            return properties

        decrypted = dict(properties)
        for field_name in encrypted_fields:
            if field_name in decrypted:
                decrypted[field_name] = self.decrypt_value(decrypted[field_name])

        # Remove encryption metadata from output
        decrypted.pop("_encrypted_fields", None)
        for f in encrypted_fields:
            decrypted.pop(f"_stok_{f}", None)

        return decrypted

    def searchable_token(self, value: str) -> str:
        """Generate a deterministic searchable token for encrypted content.

        This enables keyword search on encrypted fields without decryption:
        - Same plaintext always produces the same token
        - Token cannot be reversed to get the plaintext
        - Allows exact-match search on encrypted fields

        For substring search, tokens are generated for each word.
        """
        if not self._hmac_key:
            return ""
        h = hmac.new(self._hmac_key, value.lower().strip().encode(), hashlib.sha256)
        return self.SEARCH_PREFIX + h.hexdigest()[:16]

    def searchable_tokens_for_text(self, text: str) -> List[str]:
        """Generate searchable tokens for each significant word in text.

        Enables keyword search on encrypted content.
        """
        if not self._hmac_key:
            return []
        words = set(w.lower() for w in text.split() if len(w) > 3)
        return [self.searchable_token(w) for w in words]


# Global singleton
_encryptor: Optional[FieldEncryptor] = None

def get_encryptor() -> FieldEncryptor:
    global _encryptor
    if _encryptor is None:
        _encryptor = FieldEncryptor()
    return _encryptor
