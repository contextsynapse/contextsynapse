"""Tests for /dashboard/projects/{name}/spec/* endpoints."""
import uuid
import pytest
from unittest.mock import MagicMock, patch
from fastapi.testclient import TestClient
from fastapi import FastAPI

from contextcore.api.projects_router import create_projects_router


def _make_client():
    app = FastAPI()
    app.include_router(create_projects_router())
    return TestClient(app)


def _mock_cc(ingest_ids=None, requirements=None, coverage=None):
    cc = MagicMock()
    cc.ns.get_metadata.return_value = "## REQ: Auth\n\nMust support login."
    cc.ingest_spec.return_value = ingest_ids if ingest_ids is not None else ["req_abc12345"]
    cc.get_requirements.return_value = requirements if requirements is not None else [
        {
            "node_id": "req_abc12345",
            "label": "Requirement",
            "properties": {"name": "Auth", "priority": "must", "description": "Must support login.", "project": "test-proj"},
        }
    ]
    cc.check_coverage.return_value = coverage or {
        "covered": [],
        "partial": [],
        "uncovered": ["req_abc12345"],
        "coverage_pct": 0.0,
        "total": 1,
    }
    return cc


class TestIngestSpec:
    def test_returns_ingested_ids(self):
        client = _make_client()
        cc = _mock_cc()
        with patch("contextcore.api.projects_router.CodeContext") as MockCC:
            MockCC.load.return_value = cc
            r = client.post("/dashboard/projects/test-proj/spec/ingest")
        assert r.status_code == 200
        data = r.json()
        assert "ingested" in data
        assert data["count"] == 1
        assert "req_abc12345" in data["ingested"]

    def test_calls_ingest_spec_with_project_spec(self):
        client = _make_client()
        cc = _mock_cc()
        with patch("contextcore.api.projects_router.CodeContext") as MockCC:
            MockCC.load.return_value = cc
            client.post("/dashboard/projects/test-proj/spec/ingest")
        cc.ingest_spec.assert_called_once_with("## REQ: Auth\n\nMust support login.")

    def test_empty_spec_returns_zero(self):
        client = _make_client()
        cc = _mock_cc(ingest_ids=[])
        cc.ns.get_metadata.return_value = ""
        with patch("contextcore.api.projects_router.CodeContext") as MockCC:
            MockCC.load.return_value = cc
            r = client.post("/dashboard/projects/test-proj/spec/ingest")
        assert r.status_code == 200
        assert r.json()["count"] == 0


class TestGetRequirements:
    def test_returns_nodes_list(self):
        client = _make_client()
        cc = _mock_cc()
        with patch("contextcore.api.projects_router.CodeContext") as MockCC:
            MockCC.load.return_value = cc
            r = client.get("/dashboard/projects/test-proj/spec/requirements")
        assert r.status_code == 200
        data = r.json()
        assert "nodes" in data
        assert data["total"] == 1
        assert data["nodes"][0]["node_id"] == "req_abc12345"

    def test_empty_returns_zero(self):
        client = _make_client()
        cc = _mock_cc(requirements=[])
        with patch("contextcore.api.projects_router.CodeContext") as MockCC:
            MockCC.load.return_value = cc
            r = client.get("/dashboard/projects/test-proj/spec/requirements")
        assert r.status_code == 200
        assert r.json()["total"] == 0


class TestGetCoverage:
    def test_returns_coverage_dict(self):
        client = _make_client()
        cc = _mock_cc()
        with patch("contextcore.api.projects_router.CodeContext") as MockCC:
            MockCC.load.return_value = cc
            r = client.get("/dashboard/projects/test-proj/spec/coverage")
        assert r.status_code == 200
        data = r.json()
        assert "coverage_pct" in data
        assert "covered" in data
        assert "uncovered" in data
        assert data["total"] == 1

    def test_coverage_pct_is_float(self):
        client = _make_client()
        cc = _mock_cc(coverage={"covered": ["req_1"], "partial": [], "uncovered": [], "coverage_pct": 100.0, "total": 1})
        with patch("contextcore.api.projects_router.CodeContext") as MockCC:
            MockCC.load.return_value = cc
            r = client.get("/dashboard/projects/test-proj/spec/coverage")
        assert r.json()["coverage_pct"] == 100.0
