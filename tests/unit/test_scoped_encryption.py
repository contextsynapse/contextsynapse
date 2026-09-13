"""Tests for per-scope context encryption."""
import json
import os
import pytest
from contextcore.security.scoped_encryption import ScopedEncryptor, derive_client_key


@pytest.fixture
def encryptor():
    os.environ["AICONTEXTDB_TENANT_KEY"] = "test-tenant-key-for-unit-tests-32ch"
    enc = ScopedEncryptor()
    yield enc
    os.environ.pop("AICONTEXTDB_TENANT_KEY", None)


class TestScopedEncryptor:
    def test_market_data_not_encrypted(self, encryptor):
        data = {"price": 3500, "symbol": "TCS"}
        raw = encryptor.encrypt("market:tcs", data)
        # Market data is stored as plain JSON (no encryption)
        result = json.loads(raw)
        assert result == data

    def test_portfolio_data_encrypted(self, encryptor):
        data = {"holdings": [{"symbol": "TCS", "qty": 100}]}
        raw = encryptor.encrypt("portfolio:p001:holdings", data)
        # Encrypted data should NOT be valid JSON of the original
        assert raw != json.dumps(data).encode()

    def test_portfolio_roundtrip(self, encryptor):
        data = {"holdings": [{"symbol": "TCS", "qty": 100}]}
        raw = encryptor.encrypt("portfolio:p001:holdings", data)
        decrypted = encryptor.decrypt("portfolio:p001:holdings", raw)
        assert decrypted == data

    def test_client_data_roundtrip(self, encryptor):
        data = {"name": "John Doe", "pan": "ABCDE1234F"}
        raw = encryptor.encrypt("client:c001:profile", data)
        decrypted = encryptor.decrypt("client:c001:profile", raw)
        assert decrypted == data

    def test_frozen_data_roundtrip(self, encryptor):
        data = {"trade_id": "T-001", "content": {"price": 100}}
        raw = encryptor.encrypt("frozen:T-001", data)
        decrypted = encryptor.decrypt("frozen:T-001", raw)
        assert decrypted == data

    def test_different_clients_different_keys(self, encryptor):
        data = {"name": "test"}
        raw1 = encryptor.encrypt("client:c001:profile", data)
        raw2 = encryptor.encrypt("client:c002:profile", data)
        # Same data, different client keys → different ciphertext
        assert raw1 != raw2


class TestDeriveClientKey:
    def test_deterministic(self):
        k1 = derive_client_key(b"master", "c001")
        k2 = derive_client_key(b"master", "c001")
        assert k1 == k2

    def test_different_clients_different_keys(self):
        k1 = derive_client_key(b"master", "c001")
        k2 = derive_client_key(b"master", "c002")
        assert k1 != k2
