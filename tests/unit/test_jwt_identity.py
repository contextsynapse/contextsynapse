"""Tests for JWT identity extraction for PMS context access."""
import os
import pytest

# Set the secret before any imports that might trigger auth resolution
os.environ["AICONTEXTDB_JWT_SECRET"] = "test-secret-key-for-unit-tests"

from contextcore.security.jwt_identity import extract_identity_from_token, create_pms_jwt
from contextcore.context.acl import JWTIdentity
from contextcore.security.rbac import Role


class TestCreatePmsJwt:
    def test_creates_valid_token(self):
        token = create_pms_jwt(
            sub="fm_1", role="fund_manager", tenant="firm_a",
            portfolio_ids=["p001"], client_ids=["c001"],
        )
        assert isinstance(token, str)
        assert token.count(".") == 2  # JWT has 3 parts

    def test_roundtrip(self):
        token = create_pms_jwt(
            sub="fm_1", role="fund_manager", tenant="firm_a",
            portfolio_ids=["p001", "p002"], client_ids=["c001"],
        )
        identity = extract_identity_from_token(token)
        assert identity is not None
        assert identity.sub == "fm_1"
        assert identity.role == Role.FUND_MANAGER
        assert identity.tenant == "firm_a"
        assert identity.portfolio_ids == ["p001", "p002"]
        assert identity.client_ids == ["c001"]


class TestExtractIdentity:
    def test_invalid_token_returns_none(self):
        assert extract_identity_from_token("bad.token.here") is None

    def test_compliance_role(self):
        token = create_pms_jwt(
            sub="co_1", role="compliance_officer", tenant="firm_a",
        )
        identity = extract_identity_from_token(token)
        assert identity.role == Role.COMPLIANCE_OFFICER

    def test_client_viewer_has_client_id(self):
        token = create_pms_jwt(
            sub="cl_1", role="client_viewer", tenant="firm_a",
            client_id="c001",
        )
        identity = extract_identity_from_token(token)
        assert identity.role == Role.CLIENT_VIEWER
        assert identity.client_id == "c001"
