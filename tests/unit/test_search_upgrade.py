"""Tests for ProjectContext.search() hybrid search upgrade."""
import pytest


def _seed_project(tmp_path, name="test-search"):
    """Helper: seed a project and return the ProjectContext."""
    from contextcore.demo.scenario import seed_project
    return seed_project(name, base_path=str(tmp_path))


class TestSearchUpgrade:
    def test_search_returns_results_for_matching_query(self, tmp_path):
        pc = _seed_project(tmp_path)
        results = pc.search("rate limiting password")
        assert len(results) > 0

    def test_search_results_have_expected_keys(self, tmp_path):
        pc = _seed_project(tmp_path)
        results = pc.search("jwt token")
        assert len(results) > 0
        r = results[0]
        assert "node_id" in r
        assert "label" in r
        assert "layer" in r
        assert "score" in r
        assert "snippet" in r

    def test_search_layer_filter(self, tmp_path):
        pc = _seed_project(tmp_path)
        results = pc.search("login", layer="intent")
        for r in results:
            assert r["layer"] == "intent"

    def test_search_invalid_layer_raises(self, tmp_path):
        pc = _seed_project(tmp_path)
        with pytest.raises(ValueError, match="Unknown SDLC layer"):
            pc.search("login", layer="nonexistent")

    def test_search_empty_query_returns_empty(self, tmp_path):
        pc = _seed_project(tmp_path)
        results = pc.search("")
        assert results == []

    def test_search_respects_limit(self, tmp_path):
        pc = _seed_project(tmp_path)
        results = pc.search("login token session", limit=2)
        assert len(results) <= 2

    def test_search_only_returns_sdlc_nodes(self, tmp_path):
        from contextcore.project.sdlc_schema import is_sdlc_node_type
        pc = _seed_project(tmp_path)
        results = pc.search("project")
        for r in results:
            assert is_sdlc_node_type(r["label"])
