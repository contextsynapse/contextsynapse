"""Scoped Encryption — transparent per-scope encryption for context data.

Market data is plaintext (public). Portfolio, client, and frozen data are
encrypted with Fernet (AES-128-CBC). Client data uses per-client derived keys.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
from typing import Any, Dict

from contextsynapse.security.sanitize import parse_context_path

logger = logging.getLogger(__name__)

# Scopes that require encryption
_ENCRYPTED_SCOPES = {"portfolio", "client", "frozen"}

try:
    from cryptography.fernet import Fernet
    _FERNET_AVAILABLE = True
except ImportError:
    _FERNET_AVAILABLE = False
    logger.warning("cryptography package not installed — scoped encryption disabled")


def derive_client_key(tenant_key: bytes, client_id: str) -> bytes:
    """Derive a per-client Fernet key from the tenant master key.

    Args:
        tenant_key: Raw bytes of the tenant master key.
        client_id: Unique client identifier used as PBKDF2 salt.

    Returns:
        URL-safe base64-encoded 32-byte key suitable for Fernet.
    """
    derived = hashlib.pbkdf2_hmac("sha256", tenant_key, client_id.encode(), 100_000)
    return base64.urlsafe_b64encode(derived[:32])


class ScopedEncryptor:
    """Transparent encryption based on context scope.

    - ``market`` scope: plaintext JSON (no encryption — public data)
    - ``portfolio`` scope: Fernet-encrypted with tenant key
    - ``client`` scope: Fernet-encrypted with per-client derived key
    - ``frozen`` scope: Fernet-encrypted with tenant key

    Usage::

        enc = ScopedEncryptor()
        raw = enc.encrypt("portfolio:p001:holdings", {"holdings": [...]})
        data = enc.decrypt("portfolio:p001:holdings", raw)
    """

    def __init__(self, tenant_key: str = None):
        self._tenant_key = (
            tenant_key or os.environ.get("CONTEXTSYNAPSE_TENANT_KEY") or os.environ.get("AICONTEXTDB_TENANT_KEY", "")
        ).encode()

        if self._tenant_key and _FERNET_AVAILABLE:
            # Derive a Fernet-compatible 32-byte key from the tenant key string
            dk = hashlib.pbkdf2_hmac(
                "sha256", self._tenant_key, b"contextcore_scope", 100_000
            )
            self._tenant_fernet_key = base64.urlsafe_b64encode(dk[:32])
        else:
            self._tenant_fernet_key = None

    def _fernet_for(self, context_path: str) -> "Fernet":
        """Return the correct Fernet instance for the given context path.

        Client paths use a per-client derived key; all other encrypted scopes
        use the shared tenant key.
        """
        scope, parts = parse_context_path(context_path)

        if scope == "client" and parts:
            client_id = parts[0]
            key = derive_client_key(self._tenant_key, client_id)
        else:
            key = self._tenant_fernet_key

        return Fernet(key)

    def encrypt(self, context_path: str, data: Dict[str, Any]) -> bytes:
        """Encrypt context data based on scope.

        Market data is returned as plain JSON bytes. All other recognised
        scopes (portfolio, client, frozen) are Fernet-encrypted.

        Args:
            context_path: Scoped path like ``"portfolio:p001:holdings"``.
            data: JSON-serialisable dict to store.

        Returns:
            Encrypted bytes (Fernet token) or plain JSON bytes for market data.
        """
        scope, _ = parse_context_path(context_path)
        serialized = json.dumps(data, default=str).encode("utf-8")

        if scope not in _ENCRYPTED_SCOPES or not _FERNET_AVAILABLE:
            return serialized

        f = self._fernet_for(context_path)
        return f.encrypt(serialized)

    def decrypt(self, context_path: str, raw: bytes) -> Dict[str, Any]:
        """Decrypt context data based on scope.

        Market data is decoded directly from JSON. Encrypted scopes are
        decrypted with the appropriate Fernet key before JSON parsing.

        Args:
            context_path: Scoped path matching the one used during encryption.
            raw: Bytes returned by :meth:`encrypt`.

        Returns:
            Original data dict.
        """
        scope, _ = parse_context_path(context_path)

        if scope not in _ENCRYPTED_SCOPES or not _FERNET_AVAILABLE:
            return json.loads(raw)

        f = self._fernet_for(context_path)
        decrypted = f.decrypt(raw)
        return json.loads(decrypted)
