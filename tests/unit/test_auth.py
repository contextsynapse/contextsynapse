"""Tests for JWT creation, verification, and auth helpers."""

import os
import time
import pytest

from contextcore.api.auth import (
    create_jwt,
    verify_jwt,
    create_admin_jwt,
    verify_admin_jwt,
    _get_jwt_secret,
)


TEST_SECRET = "test-secret-key-12345"


class TestJWT:
    def test_create_and_verify(self):
        token = create_jwt({"sub": "user1", "role": "admin"}, secret=TEST_SECRET)
        payload = verify_jwt(token, secret=TEST_SECRET)
        assert payload is not None
        assert payload["sub"] == "user1"
        assert payload["role"] == "admin"

    def test_verify_wrong_secret(self):
        token = create_jwt({"sub": "user1"}, secret=TEST_SECRET)
        result = verify_jwt(token, secret="wrong-secret")
        assert result is None

    def test_verify_expired_token(self):
        token = create_jwt({"sub": "user1"}, secret=TEST_SECRET, expires_in=-1)
        result = verify_jwt(token, secret=TEST_SECRET)
        assert result is None

    def test_verify_malformed_token(self):
        assert verify_jwt("not.a.valid.jwt", secret=TEST_SECRET) is None
        assert verify_jwt("", secret=TEST_SECRET) is None
        assert verify_jwt("abc", secret=TEST_SECRET) is None

    def test_iat_and_exp_present(self):
        token = create_jwt({"sub": "user1"}, secret=TEST_SECRET, expires_in=3600)
        payload = verify_jwt(token, secret=TEST_SECRET)
        assert "iat" in payload
        assert "exp" in payload
        assert payload["exp"] > payload["iat"]

    def test_custom_expiry(self):
        token = create_jwt({"sub": "user1"}, secret=TEST_SECRET, expires_in=10)
        payload = verify_jwt(token, secret=TEST_SECRET)
        assert payload["exp"] - payload["iat"] == 10


class TestAdminJWT:
    def test_create_and_verify_admin(self):
        admin_key = "admin-secret-123"
        token = create_admin_jwt(admin_key)
        assert verify_admin_jwt(token, admin_key) is True

    def test_admin_jwt_wrong_key(self):
        token = create_admin_jwt("real-key")
        assert verify_admin_jwt(token, "wrong-key") is False

    def test_admin_jwt_has_admin_role(self):
        admin_key = "admin-secret-123"
        token = create_admin_jwt(admin_key)
        payload = verify_jwt(token, secret=admin_key)
        assert payload["role"] == "admin"


class TestGetJWTSecret:
    def test_raises_when_no_secret_set(self, monkeypatch):
        monkeypatch.delenv("AICONTEXTDB_JWT_SECRET", raising=False)
        monkeypatch.delenv("AICONTEXTDB_ADMIN_KEY", raising=False)
        import pytest
        with pytest.raises(RuntimeError, match="AICONTEXTDB_JWT_SECRET"):
            _get_jwt_secret()

    def test_uses_jwt_secret_env(self, monkeypatch):
        monkeypatch.setenv("AICONTEXTDB_JWT_SECRET", "my-jwt-secret")
        monkeypatch.delenv("AICONTEXTDB_ADMIN_KEY", raising=False)
        assert _get_jwt_secret() == "my-jwt-secret"

    def test_falls_back_to_admin_key(self, monkeypatch):
        monkeypatch.delenv("AICONTEXTDB_JWT_SECRET", raising=False)
        monkeypatch.setenv("AICONTEXTDB_ADMIN_KEY", "admin-key-fallback")
        assert _get_jwt_secret() == "admin-key-fallback"

    def test_jwt_secret_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("AICONTEXTDB_JWT_SECRET", "jwt-wins")
        monkeypatch.setenv("AICONTEXTDB_ADMIN_KEY", "admin-loses")
        assert _get_jwt_secret() == "jwt-wins"
