"""Tests for SDLCScanOperator."""
import os
import pytest
from pathlib import Path
from unittest.mock import MagicMock


def _make_repo(tmp_path):
    """Create a minimal fake repo for testing."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    # README
    (tmp_path / "README.md").write_text(
        "# My Project\n\n## Features\n\nUser authentication with JWT tokens.\n\n"
        "## Installation\n\nRun pip install to set up dependencies.\n"
    )
    # Source
    src = tmp_path / "src"
    src.mkdir()
    (src / "__init__.py").write_text("")
    (src / "auth.py").write_text('"""Authentication handler for JWT login."""\nimport jwt\n')
    (src / "db.py").write_text('"""Database connection pool."""\nimport sqlite3\n')
    # Tests
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_auth.py").write_text("def test_login_success():\n    assert True\n\ndef test_login_fail():\n    assert True\n")
    # Docs
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "architecture.md").write_text("# Use JWT for sessions\n\nStateless auth reduces server load.\n")
    # API route
    (src / "routes.py").write_text('@app.post("/auth/login")\ndef login(): pass\n\n@app.get("/users/me")\ndef me(): pass\n')
    return tmp_path


class TestSDLCScanOperator:
    def test_process_creates_nodes(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.ingestion.universal.ingest_content import Chunk
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        repo = _make_repo(tmp_path / "repo")
        db = AIContextDB(name="test-scan", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-scan")
        chunks = [Chunk(content="", index=0, metadata={"repo_path": str(repo)})]

        op = SDLCScanOperator()
        result_chunks = op.process(chunks, graph_ctx)

        assert len(graph_ctx.node_ids) > 0
        assert result_chunks is chunks  # chunks returned unchanged

    def test_creates_requirement_nodes(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.ingestion.universal.ingest_content import Chunk
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        repo = _make_repo(tmp_path / "repo")
        db = AIContextDB(name="test-req", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-req")
        chunks = [Chunk(content="", index=0, metadata={"repo_path": str(repo)})]

        SDLCScanOperator().process(chunks, graph_ctx)

        req_nodes = [nid for nid in graph_ctx.node_ids if nid.startswith("req:")]
        assert len(req_nodes) >= 1

    def test_creates_code_module_nodes(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.ingestion.universal.ingest_content import Chunk
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        repo = _make_repo(tmp_path / "repo")
        db = AIContextDB(name="test-code", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-code")
        chunks = [Chunk(content="", index=0, metadata={"repo_path": str(repo)})]

        SDLCScanOperator().process(chunks, graph_ctx)

        code_nodes = [nid for nid in graph_ctx.node_ids if nid.startswith("code:")]
        assert len(code_nodes) >= 2  # auth.py, db.py, routes.py

    def test_creates_test_case_nodes(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.ingestion.universal.ingest_content import Chunk
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        repo = _make_repo(tmp_path / "repo")
        db = AIContextDB(name="test-tc", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-tc")
        chunks = [Chunk(content="", index=0, metadata={"repo_path": str(repo)})]

        SDLCScanOperator().process(chunks, graph_ctx)

        tc_nodes = [nid for nid in graph_ctx.node_ids if nid.startswith("tc:")]
        assert len(tc_nodes) >= 1

    def test_creates_arch_decision_nodes(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.ingestion.universal.ingest_content import Chunk
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        repo = _make_repo(tmp_path / "repo")
        db = AIContextDB(name="test-arch", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-arch")
        chunks = [Chunk(content="", index=0, metadata={"repo_path": str(repo)})]

        SDLCScanOperator().process(chunks, graph_ctx)

        arch_nodes = [nid for nid in graph_ctx.node_ids if nid.startswith("arch:")]
        assert len(arch_nodes) >= 1

    def test_creates_api_contract_nodes(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.ingestion.universal.ingest_content import Chunk
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        repo = _make_repo(tmp_path / "repo")
        db = AIContextDB(name="test-api", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-api")
        chunks = [Chunk(content="", index=0, metadata={"repo_path": str(repo)})]

        SDLCScanOperator().process(chunks, graph_ctx)

        api_nodes = [nid for nid in graph_ctx.node_ids if nid.startswith("api:")]
        assert len(api_nodes) >= 2  # /auth/login, /users/me

    def test_infers_edges(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.ingestion.universal.ingest_content import Chunk
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        repo = _make_repo(tmp_path / "repo")
        db = AIContextDB(name="test-edges", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-edges")
        chunks = [Chunk(content="", index=0, metadata={"repo_path": str(repo)})]

        SDLCScanOperator().process(chunks, graph_ctx)

        assert graph_ctx.edge_count > 0

    def test_no_repo_path_is_noop(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.ingestion.universal.ingest_content import Chunk
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        db = AIContextDB(name="test-noop", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-noop")
        chunks = [Chunk(content="", index=0, metadata={})]

        SDLCScanOperator().process(chunks, graph_ctx)

        assert len(graph_ctx.node_ids) == 0

    def test_empty_chunks_is_noop(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        from contextcore.core.hybrid_graph_storage import AIContextDB
        from contextcore.ingestion.universal.stage_executor import GraphContext

        db = AIContextDB(name="test-empty", config={"base_path": str(tmp_path)})
        graph_ctx = GraphContext(db=db, namespace="test-empty")

        SDLCScanOperator().process([], graph_ctx)

        assert len(graph_ctx.node_ids) == 0

    def test_operator_name(self):
        from contextcore.ingestion.universal.operators.sdlc_scan import SDLCScanOperator
        assert SDLCScanOperator().name == "sdlc_scan"


class TestScanGithubIssues:
    def test_returns_empty_when_no_git_dir(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_github_issues_full
        result = scan_github_issues_full(tmp_path)
        assert result == []

    def test_returns_empty_when_no_remote(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_github_issues_full
        import subprocess
        subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
        result = scan_github_issues_full(tmp_path)
        assert result == []

    @pytest.mark.skipif(not os.environ.get("GITHUB_TOKEN"), reason="No GITHUB_TOKEN")
    def test_live_fetch_returns_nodes(self, tmp_path):
        """Live test — only runs when GITHUB_TOKEN is set. Uses a known public repo."""
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_github_issues
        import subprocess
        subprocess.run(["git", "clone", "--depth", "1", "https://github.com/tiangolo/fastapi", str(tmp_path / "repo")], capture_output=True, timeout=60)
        result = scan_github_issues(tmp_path / "repo")
        assert len(result) > 0
        for node in result:
            assert node["label"] in ("Requirement", "UserStory", "KnownIssue")
            assert node["id"].startswith("issue:")

    def test_node_ids_are_deterministic(self):
        from contextcore.ingestion.universal.operators.sdlc_scan import _safe_id
        assert _safe_id("Add rate limiting") == "add-rate-limiting"

    def test_bug_label_maps_to_known_issue(self):
        """Unit test the label mapping logic."""
        from contextcore.ingestion.universal.operators.sdlc_scan import _issue_to_node
        node = _issue_to_node({"number": 42, "title": "Login crash", "body": "App crashes on login", "labels": [{"name": "bug"}]})
        assert node["label"] == "KnownIssue"
        assert node["id"] == "issue:42"

    def test_feature_label_maps_to_requirement(self):
        from contextcore.ingestion.universal.operators.sdlc_scan import _issue_to_node
        node = _issue_to_node({"number": 10, "title": "Add dark mode", "body": "Support dark theme", "labels": [{"name": "enhancement"}]})
        assert node["label"] == "Requirement"

    def test_default_label_maps_to_user_story(self):
        from contextcore.ingestion.universal.operators.sdlc_scan import _issue_to_node
        node = _issue_to_node({"number": 5, "title": "Improve docs", "body": "", "labels": []})
        assert node["label"] == "UserStory"


class TestScanGitHistory:
    def test_returns_empty_when_no_git_dir(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_git_history
        nodes, edges = scan_git_history(tmp_path)
        assert nodes == []
        assert edges == []

    def test_returns_change_records_for_high_churn_files(self, tmp_path):
        """Create a git repo with multiple commits to the same file."""
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_git_history
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], capture_output=True)
        # Create file and commit 4 times (above threshold of 3)
        f = repo / "auth.py"
        for i in range(4):
            f.write_text(f"# version {i}\ndef login(): pass\n")
            subprocess.run(["git", "-C", str(repo), "add", "auth.py"], capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", f"update auth v{i}"], capture_output=True)

        nodes, edges = scan_git_history(repo)
        assert len(nodes) >= 1
        assert nodes[0]["label"] == "ChangeRecord"
        assert nodes[0]["properties"]["commit_count"] >= 3

    def test_skips_low_churn_files(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_git_history
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], capture_output=True)
        f = repo / "readme.md"
        f.write_text("# hello")
        subprocess.run(["git", "-C", str(repo), "add", "."], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-m", "init"], capture_output=True)

        nodes, edges = scan_git_history(repo)
        assert nodes == []  # only 1 commit, below threshold

    def test_creates_modifies_edges(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_git_history
        import subprocess
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", str(repo)], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "test@test.com"], capture_output=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], capture_output=True)
        f = repo / "db.py"
        for i in range(4):
            f.write_text(f"# v{i}\ndef connect(): pass\n")
            subprocess.run(["git", "-C", str(repo), "add", "db.py"], capture_output=True)
            subprocess.run(["git", "-C", str(repo), "commit", "-m", f"fix db v{i}"], capture_output=True)

        nodes, edges = scan_git_history(repo)
        assert len(nodes) >= 1
        # Edges reference the ChangeRecord as source
        for edge in edges:
            assert edge["label"] == "MODIFIES"
            assert edge["source"].startswith("churn:")


class TestScanWithLLM:
    def test_returns_nodes_unchanged_when_no_llm(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_with_llm
        from unittest.mock import patch
        nodes = [{"id": "code:foo", "label": "CodeModule", "properties": {"summary": "old", "path": "foo.py", "version": 1}}]
        # Patch get_llm_client to raise
        with patch("contextcore.ingestion.universal.operators.scanners.llm_enrichment.get_llm_client", side_effect=Exception("no LLM")):
            result = scan_with_llm(tmp_path, nodes)
        assert result == nodes  # unchanged

    def test_enriches_summary_when_llm_available(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_with_llm
        from unittest.mock import patch, MagicMock

        # Create a source file
        (tmp_path / "foo.py").write_text('"""Old summary."""\nimport os\ndef main(): pass\n')

        nodes = [{"id": "code:foo", "label": "CodeModule", "properties": {"summary": "Old summary.", "path": "foo.py", "version": 1}}]

        mock_client = MagicMock()
        mock_client.generate.return_value = "This module provides the main entry point. It depends on os. Risk: no error handling."

        with patch("contextcore.ingestion.universal.operators.scanners.llm_enrichment.get_llm_client", return_value=mock_client):
            result = scan_with_llm(tmp_path, nodes)

        assert result[0]["id"] == "code:foo"
        assert "main entry point" in result[0]["properties"]["summary"]

    def test_respects_cap(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_with_llm
        from contextcore.ingestion.universal.operators.scanners.llm_enrichment import _MAX_LLM_FILES
        from unittest.mock import patch, MagicMock

        # Create many nodes
        nodes = []
        for i in range(20):
            (tmp_path / f"mod{i}.py").write_text(f"# module {i}\ndef func{i}(): pass\n")
            nodes.append({"id": f"code:mod{i}", "label": "CodeModule", "properties": {"summary": f"mod {i}", "path": f"mod{i}.py", "version": 1}})

        mock_client = MagicMock()
        mock_client.generate.return_value = "Enriched summary."

        with patch("contextcore.ingestion.universal.operators.scanners.llm_enrichment.get_llm_client", return_value=mock_client):
            scan_with_llm(tmp_path, nodes)

        # LLM should only be called _MAX_LLM_FILES times
        assert mock_client.generate.call_count == _MAX_LLM_FILES

    def test_preserves_node_id_and_label(self, tmp_path):
        from contextcore.ingestion.universal.operators.sdlc_scan import scan_with_llm
        from unittest.mock import patch, MagicMock

        (tmp_path / "bar.py").write_text("def bar(): pass\n")
        nodes = [{"id": "code:bar", "label": "CodeModule", "properties": {"summary": "bar", "path": "bar.py", "version": 1}}]

        mock_client = MagicMock()
        mock_client.generate.return_value = "Bar function."

        with patch("contextcore.ingestion.universal.operators.scanners.llm_enrichment.get_llm_client", return_value=mock_client):
            result = scan_with_llm(tmp_path, nodes)

        assert result[0]["id"] == "code:bar"
        assert result[0]["label"] == "CodeModule"
