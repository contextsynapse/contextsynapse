"""Tests for spec-driven development: SpecParser, CodeContext traceability, MCP tools."""
import json
import pytest
from unittest.mock import MagicMock, patch

from contextcore.project.spec_parser import parse_spec
from contextcore.project.code_context import CodeContext


# ── Task 1: SpecParser ───────────────────────────────────────────────────────

class TestSpecParser:
    def test_empty_returns_empty_list(self):
        assert parse_spec("") == []

    def test_whitespace_only_returns_empty_list(self):
        assert parse_spec("   \n\n  ") == []

    def test_requirement_heading(self):
        text = "## REQ: User can log in\n\nThe system shall authenticate via email/password."
        nodes = parse_spec(text)
        assert len(nodes) == 1
        n = nodes[0]
        assert n["label"] == "Requirement"
        assert n["name"] == "User can log in"
        assert "authenticate" in n["description"]
        assert n["node_id"].startswith("req_")
        assert len(n["node_id"]) == 12  # "req_" + 8 hex chars

    def test_feature_heading(self):
        text = "## FEAT: Dashboard\n\nAggregate view of all metrics."
        nodes = parse_spec(text)
        assert nodes[0]["label"] == "Feature"
        assert nodes[0]["node_id"].startswith("feat_")
        assert len(nodes[0]["node_id"]) == 13  # "feat_" + 8 hex chars

    def test_constraint_heading(self):
        text = "## CON: Response time < 200ms"
        nodes = parse_spec(text)
        assert nodes[0]["label"] == "Constraint"
        assert nodes[0]["node_id"].startswith("con_")
        assert len(nodes[0]["node_id"]) == 12  # "con_" + 8 hex chars

    def test_priority_from_must_keyword(self):
        text = "## REQ: MUST support SSO\n\nThe system must support SSO."
        nodes = parse_spec(text)
        assert nodes[0]["priority"] == "must"

    def test_priority_from_should_keyword(self):
        text = "## REQ: SHOULD cache results\n\nThe system should cache API results."
        nodes = parse_spec(text)
        assert nodes[0]["priority"] == "should"

    def test_multiple_nodes(self):
        text = (
            "## REQ: Login\n\nMust allow login.\n\n"
            "## FEAT: Profile Page\n\n"
            "## CON: Max 100ms latency"
        )
        nodes = parse_spec(text)
        assert len(nodes) == 3
        labels = [n["label"] for n in nodes]
        assert "Requirement" in labels
        assert "Feature" in labels
        assert "Constraint" in labels

    def test_unknown_prefix_skipped(self):
        text = "## Introduction\n\nThis is preamble.\n\n## REQ: Auth"
        nodes = parse_spec(text)
        assert len(nodes) == 1
        assert nodes[0]["label"] == "Requirement"

    def test_node_ids_are_unique(self):
        text = "## REQ: A\n\n## REQ: B\n\n## REQ: C"
        nodes = parse_spec(text)
        ids = [n["node_id"] for n in nodes]
        assert len(ids) == len(set(ids))

    def test_tags_field_present(self):
        nodes = parse_spec("## REQ: Auth")
        assert "tags" in nodes[0]
        assert isinstance(nodes[0]["tags"], list)

    def test_depth_1_heading_recognised(self):
        text = "# REQ: Top level requirement"
        nodes = parse_spec(text)
        assert len(nodes) == 1
        assert nodes[0]["label"] == "Requirement"

    def test_depth_4_heading_recognised(self):
        text = "#### FEAT: Deep feature"
        nodes = parse_spec(text)
        assert len(nodes) == 1
        assert nodes[0]["label"] == "Feature"


# ── Task 2: CodeContext.ingest_spec + traceability ────────────────────────────

def _make_cc(project_name="test-project"):
    """Build a CodeContext backed by a real in-process AIContextDB."""
    import uuid
    from contextcore.project.code_context import CodeContext
    from contextcore.core.hybrid_graph_storage import AIContextDB
    from contextcore.adapters._base import AIContextDBConnection

    unique_name = f"{project_name}_{uuid.uuid4().hex[:6]}"
    db = AIContextDB(name=unique_name)
    conn = AIContextDBConnection(contextcore=db, namespace=unique_name)
    cc = CodeContext.__new__(CodeContext)
    cc.name = unique_name
    cc.conn = conn
    cc.ns = None
    cc._spec_node_id = None
    cc._thread_id = None
    return cc


class TestCodeContextSpec:
    def test_ingest_spec_returns_node_ids(self):
        cc = _make_cc()
        text = "## REQ: Login\n\nMust support email login.\n\n## FEAT: Dashboard"
        ids = cc.ingest_spec(text)
        assert len(ids) == 2
        assert ids[0].startswith("req_")
        assert ids[1].startswith("feat_")

    def test_ingest_spec_nodes_stored_in_graph(self):
        cc = _make_cc()
        cc.ingest_spec("## REQ: Auth\n\nMust authenticate.")
        nodes = cc.conn.get_nodes(label="Requirement")
        assert len(nodes) == 1
        assert "Auth" in nodes[0].properties.get("name", "")

    def test_ingest_spec_project_tag_on_nodes(self):
        cc = _make_cc()
        cc.ingest_spec("## REQ: Feature X")
        nodes = cc.conn.get_nodes(label="Requirement")
        assert nodes[0].properties.get("project") == cc.name

    def test_ingest_spec_empty_returns_empty(self):
        cc = _make_cc()
        ids = cc.ingest_spec("")
        assert ids == []

    def test_ingest_spec_constraint_node(self):
        cc = _make_cc()
        ids = cc.ingest_spec("## CON: Max 100ms latency")
        assert len(ids) == 1
        assert ids[0].startswith("con_")
        nodes = cc.conn.get_nodes(label="Constraint")
        assert len(nodes) == 1

    def test_get_requirements_returns_only_requirements(self):
        cc = _make_cc()
        cc.ingest_spec(
            "## REQ: Auth\n\n## FEAT: Dashboard\n\n## CON: Fast"
        )
        reqs = cc.get_requirements()
        assert len(reqs) == 1
        assert reqs[0]["label"] == "Requirement"

    def test_get_requirements_scoped_to_project(self):
        """Two CodeContexts with different project names don't share requirements."""
        cc1 = _make_cc("proj_a")
        cc2 = _make_cc("proj_b")
        cc1.ingest_spec("## REQ: A")
        cc2.ingest_spec("## REQ: B")
        assert len(cc1.get_requirements()) == 1
        assert len(cc2.get_requirements()) == 1
        assert cc1.get_requirements()[0]["properties"]["name"] == "A"
        assert cc2.get_requirements()[0]["properties"]["name"] == "B"

    def test_link_artifact_creates_edge(self):
        cc = _make_cc()
        ids = cc.ingest_spec("## REQ: Auth")
        req_id = ids[0]
        cc.link_artifact(req_id, "task_abc", "IMPLEMENTED_BY")
        neighbors = cc.conn.contextcore.get_neighbors(
            req_id, edge_label="IMPLEMENTED_BY", direction="OUTGOING"
        )
        assert len(neighbors) >= 1


# ── Task 3: check_coverage ───────────────────────────────────────────────────

class TestCoverageAnalysis:
    def test_no_requirements_returns_100pct(self):
        cc = _make_cc()
        result = cc.check_coverage()
        assert result["coverage_pct"] == 100.0
        assert result["total"] == 0

    def test_fully_covered_requirement(self):
        cc = _make_cc()
        ids = cc.ingest_spec("## REQ: Auth")
        req_id = ids[0]
        cc.link_artifact(req_id, "task_1", "IMPLEMENTED_BY")
        cc.link_artifact(req_id, "tc_1", "VERIFIED_BY")
        result = cc.check_coverage()
        assert req_id in result["covered"]
        assert result["coverage_pct"] == 100.0

    def test_partial_coverage_implemented_only(self):
        cc = _make_cc()
        ids = cc.ingest_spec("## REQ: Auth")
        req_id = ids[0]
        cc.link_artifact(req_id, "task_1", "IMPLEMENTED_BY")
        result = cc.check_coverage()
        assert req_id in result["partial"]
        assert result["coverage_pct"] == 50.0

    def test_partial_coverage_verified_only(self):
        cc = _make_cc()
        ids = cc.ingest_spec("## REQ: Auth")
        req_id = ids[0]
        cc.link_artifact(req_id, "tc_1", "VERIFIED_BY")
        result = cc.check_coverage()
        assert req_id in result["partial"]

    def test_uncovered_requirement(self):
        cc = _make_cc()
        ids = cc.ingest_spec("## REQ: Auth")
        req_id = ids[0]
        result = cc.check_coverage()
        assert req_id in result["uncovered"]
        assert result["coverage_pct"] == 0.0

    def test_mixed_coverage(self):
        cc = _make_cc()
        ids = cc.ingest_spec(
            "## REQ: Auth\n\n## REQ: CRUD\n\n## REQ: Export"
        )
        r1, r2, r3 = ids[0], ids[1], ids[2]
        cc.link_artifact(r1, "t1", "IMPLEMENTED_BY")
        cc.link_artifact(r1, "tc1", "VERIFIED_BY")   # covered
        cc.link_artifact(r2, "t2", "IMPLEMENTED_BY")  # partial
        # r3 untouched — uncovered
        result = cc.check_coverage()
        assert r1 in result["covered"]
        assert r2 in result["partial"]
        assert r3 in result["uncovered"]
        # (1 + 0.5) / 3 * 100 = 50.0
        assert abs(result["coverage_pct"] - 50.0) < 0.01

    def test_coverage_pct_formula(self):
        cc = _make_cc()
        ids = cc.ingest_spec(
            "## REQ: A\n\n## REQ: B\n\n## REQ: C\n\n## REQ: D"
        )
        r1, r2, r3, r4 = ids
        for r in [r1, r2]:
            cc.link_artifact(r, f"t_{r}", "IMPLEMENTED_BY")
            cc.link_artifact(r, f"tc_{r}", "VERIFIED_BY")
        cc.link_artifact(r3, "t3", "IMPLEMENTED_BY")
        # r4 uncovered
        result = cc.check_coverage()
        # (2 + 0.5) / 4 * 100 = 62.5
        assert abs(result["coverage_pct"] - 62.5) < 0.01


# ── Task 4: MCP tools ────────────────────────────────────────────────────────

class TestMCPSpecTools:
    """Test that MCP spec tools delegate to CodeContext and serialise correctly."""

    def _mock_cc(self):
        cc = MagicMock()
        cc.ingest_spec.return_value = ["req_abc", "feat_xyz"]
        cc.get_requirements.return_value = [
            {"node_id": "req_1", "label": "Requirement",
             "properties": {"name": "Auth", "priority": "must"}}
        ]
        cc.check_coverage.return_value = {
            "covered": ["req_1"], "partial": [], "uncovered": [],
            "coverage_pct": 100.0, "total": 1,
        }
        cc.link_artifact.return_value = None
        return cc

    def test_ingest_spec_calls_cc_ingest(self):
        import contextcore.mcp.server as server
        cc = self._mock_cc()
        with patch.object(server, "_load_code_context", return_value=cc):
            from contextcore.mcp.server import create_mcp_server
            mcp = create_mcp_server()
            tool_fn = next(
                t.fn for t in mcp._tool_manager.list_tools()
                if t.name == "ingest_spec"
            )
            result = tool_fn("my-project", "## REQ: Auth")
        cc.ingest_spec.assert_called_once_with("## REQ: Auth")
        data = json.loads(result)
        assert "req_abc" in data["created"]

    def test_get_requirements_returns_json(self):
        import contextcore.mcp.server as server
        cc = self._mock_cc()
        with patch.object(server, "_load_code_context", return_value=cc):
            from contextcore.mcp.server import create_mcp_server
            mcp = create_mcp_server()
            tool_fn = next(
                t.fn for t in mcp._tool_manager.list_tools()
                if t.name == "get_requirements"
            )
            result = tool_fn("my-project")
        data = json.loads(result)
        assert isinstance(data, list)
        assert data[0]["node_id"] == "req_1"

    def test_check_coverage_returns_json(self):
        import contextcore.mcp.server as server
        cc = self._mock_cc()
        with patch.object(server, "_load_code_context", return_value=cc):
            from contextcore.mcp.server import create_mcp_server
            mcp = create_mcp_server()
            tool_fn = next(
                t.fn for t in mcp._tool_manager.list_tools()
                if t.name == "check_coverage"
            )
            result = tool_fn("my-project")
        data = json.loads(result)
        assert data["coverage_pct"] == 100.0

    def test_link_artifact_calls_cc_link(self):
        import contextcore.mcp.server as server
        cc = self._mock_cc()
        with patch.object(server, "_load_code_context", return_value=cc):
            from contextcore.mcp.server import create_mcp_server
            mcp = create_mcp_server()
            tool_fn = next(
                t.fn for t in mcp._tool_manager.list_tools()
                if t.name == "link_artifact"
            )
            tool_fn("my-project", "req_1", "task_99", "IMPLEMENTED_BY")
        cc.link_artifact.assert_called_once_with("req_1", "task_99", "IMPLEMENTED_BY")

    def test_link_artifact_invalid_label_returns_error(self):
        import contextcore.mcp.server as server
        cc = self._mock_cc()
        with patch.object(server, "_load_code_context", return_value=cc):
            from contextcore.mcp.server import create_mcp_server
            mcp = create_mcp_server()
            tool_fn = next(
                t.fn for t in mcp._tool_manager.list_tools()
                if t.name == "link_artifact"
            )
            result = tool_fn("my-project", "req_1", "task_99", "INVALID_EDGE")
        data = json.loads(result)
        assert "error" in data
        assert "INVALID_EDGE" in data["error"]

    def test_valid_trace_labels_constant(self):
        from contextcore.mcp.server import _VALID_TRACE_LABELS
        assert isinstance(_VALID_TRACE_LABELS, frozenset)
        assert "IMPLEMENTED_BY" in _VALID_TRACE_LABELS
        assert "VERIFIED_BY" in _VALID_TRACE_LABELS
        assert "COVERS" in _VALID_TRACE_LABELS
