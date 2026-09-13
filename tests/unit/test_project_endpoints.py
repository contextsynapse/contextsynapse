"""Tests for the SDLC Projects REST endpoints."""
import pytest
from fastapi.testclient import TestClient
from fastapi import FastAPI


@pytest.fixture
def client(tmp_path):
    from contextcore.api.projects_router import create_projects_router
    app = FastAPI()
    app.include_router(create_projects_router(base_path=str(tmp_path)))
    return TestClient(app)


class TestCreateProject:
    def test_create_project_returns_201(self, client):
        resp = client.post("/dashboard/projects", json={"name": "test-proj"})
        assert resp.status_code == 201

    def test_create_project_returns_name(self, client):
        resp = client.post("/dashboard/projects", json={"name": "my-app"})
        assert resp.json()["name"] == "my-app"

    def test_create_project_missing_name_returns_422(self, client):
        resp = client.post("/dashboard/projects", json={})
        assert resp.status_code == 422

    def test_create_idempotent(self, client):
        client.post("/dashboard/projects", json={"name": "idempotent"})
        resp = client.post("/dashboard/projects", json={"name": "idempotent"})
        assert resp.status_code == 201


class TestListProjects:
    def test_list_returns_200(self, client):
        resp = client.get("/dashboard/projects")
        assert resp.status_code == 200

    def test_list_returns_list(self, client):
        assert isinstance(client.get("/dashboard/projects").json(), list)

    def test_created_project_appears_in_list(self, client):
        client.post("/dashboard/projects", json={"name": "listed-proj"})
        names = [p["name"] for p in client.get("/dashboard/projects").json()]
        assert "listed-proj" in names


class TestGetProject:
    def test_get_existing_project_returns_200(self, client):
        client.post("/dashboard/projects", json={"name": "get-proj"})
        assert client.get("/dashboard/projects/get-proj").status_code == 200

    def test_get_project_includes_coverage(self, client):
        client.post("/dashboard/projects", json={"name": "cov-proj"})
        data = client.get("/dashboard/projects/cov-proj").json()
        assert "coverage" in data

    def test_get_missing_project_returns_404(self, client):
        assert client.get("/dashboard/projects/does-not-exist").status_code == 404


class TestCoverageEndpoint:
    def test_coverage_returns_200(self, client):
        client.post("/dashboard/projects", json={"name": "cov-ep"})
        assert client.get("/dashboard/projects/cov-ep/coverage").status_code == 200

    def test_coverage_has_expected_keys(self, client):
        client.post("/dashboard/projects", json={"name": "cov-keys"})
        data = client.get("/dashboard/projects/cov-keys/coverage").json()
        assert "intent" in data
        assert "overall" in data
        assert "project_type" in data


class TestStaleEndpoint:
    def test_stale_returns_200(self, client):
        client.post("/dashboard/projects", json={"name": "stale-ep"})
        assert client.get("/dashboard/projects/stale-ep/stale").status_code == 200

    def test_stale_returns_list(self, client):
        client.post("/dashboard/projects", json={"name": "stale-list"})
        assert isinstance(client.get("/dashboard/projects/stale-list/stale").json(), list)


class TestBriefEndpoint:
    def test_brief_returns_200(self, client):
        client.post("/dashboard/projects", json={"name": "brief-ep"})
        assert client.get("/dashboard/projects/brief-ep/brief").status_code == 200

    def test_brief_has_expected_keys(self, client):
        client.post("/dashboard/projects", json={"name": "brief-keys"})
        data = client.get("/dashboard/projects/brief-keys/brief").json()
        assert "nodes" in data
        assert "stale_count" in data

    def test_brief_topic_param_accepted(self, client):
        client.post("/dashboard/projects", json={"name": "brief-topic"})
        assert client.get("/dashboard/projects/brief-topic/brief?topic=auth").status_code == 200

    def test_brief_phase_param_accepted(self, client):
        client.post("/dashboard/projects", json={"name": "brief-phase"})
        assert client.get("/dashboard/projects/brief-phase/brief?phase=build").status_code == 200


class TestTimelineEndpoint:
    def test_timeline_returns_200(self, client):
        client.post("/dashboard/projects", json={"name": "tl-ep"})
        assert client.get("/dashboard/projects/tl-ep/timeline").status_code == 200

    def test_timeline_returns_list(self, client):
        client.post("/dashboard/projects", json={"name": "tl-list"})
        assert isinstance(client.get("/dashboard/projects/tl-list/timeline").json(), list)
