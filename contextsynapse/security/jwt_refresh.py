"""JWT Refresh Token — short-lived access + long-lived refresh.

Access token:  15 min (default, configurable)
Refresh token: 7 days (default, configurable)

Usage:
    from contextsynapse.security.jwt_refresh import create_token_pair, refresh_access_token

    access, refresh = create_token_pair(user_id="...", claims={...})
    new_access = refresh_access_token(refresh_token)
"""
from __future__ import annotations

import logging
import os
import time
from typing import Any

logger = logging.getLogger(__name__)

ACCESS_EXPIRY_SECONDS = int(os.environ.get("CONTEXTSYNAPSE_JWT_ACCESS_EXPIRY", 900))    # 15 min
REFRESH_EXPIRY_SECONDS = int(os.environ.get("CONTEXTSYNAPSE_JWT_REFRESH_EXPIRY", 604800))  # 7 days


def _get_secret() -> str:
    return os.environ.get("CONTEXTSYNAPSE_JWT_SECRET") or os.environ.get("AICONTEXTDB_JWT_SECRET", "dev-secret")


def create_token_pair(user_id: str, claims: dict = None) -> tuple[str, str]:
    """Create access + refresh token pair."""
    from jose import jwt

    secret = _get_secret()
    now = int(time.time())
    base_claims = {"sub": user_id, **(claims or {})}

    access_token = jwt.encode(
        {**base_claims, "type": "access", "iat": now, "exp": now + ACCESS_EXPIRY_SECONDS},
        secret, algorithm="HS256",
    )
    refresh_token = jwt.encode(
        {"sub": user_id, "type": "refresh", "iat": now, "exp": now + REFRESH_EXPIRY_SECONDS},
        secret, algorithm="HS256",
    )
    return access_token, refresh_token


def refresh_access_token(refresh_token: str) -> str | None:
    """Generate a new access token from a valid refresh token."""
    from jose import jwt, JWTError

    secret = _get_secret()
    try:
        claims = jwt.decode(refresh_token, secret, algorithms=["HS256"])
        if claims.get("type") != "refresh":
            return None

        user_id = claims.get("sub", "")
        now = int(time.time())

        # Issue new access token
        return jwt.encode(
            {"sub": user_id, "type": "access", "iat": now, "exp": now + ACCESS_EXPIRY_SECONDS},
            secret, algorithm="HS256",
        )
    except JWTError:
        return None


def verify_token(token: str) -> dict | None:
    """Verify any token (access or refresh). Returns claims or None."""
    from jose import jwt, JWTError

    try:
        return jwt.decode(token, _get_secret(), algorithms=["HS256"])
    except JWTError:
        return None
