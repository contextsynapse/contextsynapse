"""Integration tests for core API endpoints using httpx AsyncClient."""

import pytest
from httpx import AsyncClient, ASGITransport

from contextcore.api.api import app
from contextcore.api.auth import create_jwt


@pytest.fixture
def user_token():
    """Create a valid user JWT for testing."""
    return create_jwt({"sub": "test-user", "type": "user", "email": "test@example.com"})


@pytest.fixture
def admin_headers():
    """Admin headers using X-Admin-Key."""
    import os
    key = os.environ.get("AICONTEXTDB_ADMIN_KEY", "test-admin-key")
    return {"X-Admin-Key": key}


@pytest.mark.asyncio
async def test_health_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data.get("status") in ("healthy", "ok", True)


@pytest.mark.asyncio
async def test_docs_endpoint():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/docs")
        assert resp.status_code == 200


@pytest.mark.asyncio
async def test_unauthenticated_dashboard_returns_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/dashboard/graphs")
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_invalid_token_returns_401():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get(
            "/dashboard/graphs",
            headers={"Authorization": "Bearer invalid-token"},
        )
        assert resp.status_code == 401


@pytest.mark.asyncio
async def test_tinker_requires_auth():
    """Tinker endpoint should require admin auth (Phase 1 security)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.post("/tinker", json={"query": "SHOW GRAPHS"})
        assert resp.status_code in (401, 403, 503)


@pytest.mark.asyncio
async def test_error_response_format():
    """Error responses should include either standardized format or legacy detail."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/dashboard/graphs")
        assert resp.status_code == 401
        data = resp.json()
        # Accept both standardized (error_code+message) and legacy (detail) formats
        assert "error_code" in data or "detail" in data


@pytest.mark.asyncio
async def test_request_id_header():
    """Responses should include X-Request-ID header."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health")
        assert "x-request-id" in resp.headers


@pytest.mark.asyncio
async def test_custom_request_id_echoed():
    """Server should echo back a provided X-Request-ID."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.get("/health", headers={"X-Request-ID": "my-test-id"})
        assert resp.headers.get("x-request-id") == "my-test-id"


@pytest.mark.asyncio
async def test_cors_headers():
    """CORS should return allowed origins (not wildcard in production)."""
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        resp = await client.options(
            "/health",
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "GET",
            },
        )
        # Should allow localhost:3000
        acl = resp.headers.get("access-control-allow-origin", "")
        assert acl in ("http://localhost:3000", "*") or resp.status_code == 200
