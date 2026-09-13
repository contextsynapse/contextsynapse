"""Tests for ProjectContext — thin SDLC namespace wrapper."""
import pytest


def _make_node(node_id, label, **props):
    from contextcore.core.graph_structures import GraphNode
    return GraphNode(id=node_id, label=label, properties={"version": 1, **props})


class TestProjectContextCreate:
    def test_create_returns_project_context(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc = ProjectContext.create("test-proj", base_path=str(tmp_path))
        assert pc is not None
        assert pc.name == "test-proj"

    def test_create_idempotent(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc1 = ProjectContext.create("idempotent-proj", base_path=str(tmp_path))
        pc2 = ProjectContext.create("idempotent-proj", base_path=str(tmp_path))
        assert pc1.name == pc2.name

    def test_load_returns_project_context(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        ProjectContext.create("load-proj", base_path=str(tmp_path))
        pc = ProjectContext.load("load-proj", base_path=str(tmp_path))
        assert pc.name == "load-proj"


class TestCoverageScore:
    def test_empty_project_zero_coverage(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc = ProjectContext.create("empty-cov", base_path=str(tmp_path))
        score = pc.coverage_score()
        assert score["intent"] == 0.0
        assert score["build"] == 0.0
        assert score["overall"] == 0.0

    def test_coverage_keys_present(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc = ProjectContext.create("keys-proj", base_path=str(tmp_path))
        score = pc.coverage_score()
        assert set(score.keys()) >= {"intent", "design", "build", "verify", "evolution", "overall", "project_type"}

    def test_project_type_greenfield_when_no_evolution(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc = ProjectContext.create("gf-proj", base_path=str(tmp_path))
        score = pc.coverage_score()
        assert score["project_type"] == "greenfield"

    def test_project_type_brownfield_when_evolution_nodes_exist(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        pc = ProjectContext.create("bf-proj", base_path=str(tmp_path))
        pc.db.add_node(GraphNode(
            id="pres-001", label="Preservation",
            properties={"what": "Login API", "version": 1}
        ))
        score = pc.coverage_score()
        assert score["project_type"] == "brownfield"

    def test_coverage_increases_with_nodes(self, tmp_path):
        import uuid
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        uid = uuid.uuid4().hex[:8]
        name = f"cov-incr-{uid}"
        pc = ProjectContext.create(name, base_path=str(tmp_path))
        before = pc.coverage_score()["intent"]
        pc.db.add_node(GraphNode(
            id=f"req-001-{uid}", label="Requirement",
            properties={"content": "Users can log in", "version": 1}
        ))
        after = pc.coverage_score()["intent"]
        assert after > before

    def test_evolution_none_when_no_evolution_nodes(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc = ProjectContext.create("evo-none", base_path=str(tmp_path))
        score = pc.coverage_score()
        assert score["evolution"] is None

    def test_evolution_float_when_evolution_nodes_exist(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        pc = ProjectContext.create("evo-float", base_path=str(tmp_path))
        pc.db.add_node(GraphNode(
            id="pres-001", label="Preservation",
            properties={"what": "Old API", "version": 1}
        ))
        score = pc.coverage_score()
        assert isinstance(score["evolution"], float)


class TestStaleReport:
    def test_no_stale_returns_empty_list(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc = ProjectContext.create("no-stale", base_path=str(tmp_path))
        assert pc.stale_report() == []

    def test_stale_node_appears_in_report(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        pc = ProjectContext.create("stale-proj", base_path=str(tmp_path))
        n = GraphNode(
            id="mod-001", label="CodeModule",
            properties={
                "summary": "Auth module", "version": 2,
                "_stale": True, "_stale_reason": "req-001 updated to v2",
                "_stale_since": "2026-06-23T10:00:00Z",
            }
        )
        pc.db.add_node(n)
        report = pc.stale_report()
        assert len(report) == 1
        assert report[0]["node_id"] == "mod-001"
        assert report[0]["label"] == "CodeModule"
        assert "stale_reason" in report[0]
        assert "stale_since" in report[0]

    def test_non_sdlc_stale_node_excluded(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        pc = ProjectContext.create("non-sdlc", base_path=str(tmp_path))
        n = GraphNode(
            id="misc-001", label="Miscellaneous",
            properties={"version": 1, "_stale": True}
        )
        pc.db.add_node(n)
        report = pc.stale_report()
        assert len(report) == 0


class TestAgentBrief:
    def test_returns_expected_keys(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc = ProjectContext.create("brief-proj", base_path=str(tmp_path))
        brief = pc.agent_brief()
        assert set(brief.keys()) >= {"topic", "phase", "nodes", "stale_count"}

    def test_nodes_is_list(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        pc = ProjectContext.create("brief-nodes", base_path=str(tmp_path))
        brief = pc.agent_brief()
        assert isinstance(brief["nodes"], list)

    def test_topic_filter_returns_relevant_nodes(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        pc = ProjectContext.create("brief-filter", base_path=str(tmp_path))
        pc.db.add_node(GraphNode(
            id="req-auth", label="Requirement",
            properties={"content": "Users can log in with OAuth", "version": 1}
        ))
        pc.db.add_node(GraphNode(
            id="req-billing", label="Requirement",
            properties={"content": "Users can pay with Stripe", "version": 1}
        ))
        brief = pc.agent_brief(topic="oauth")
        node_ids = [n["node_id"] for n in brief["nodes"]]
        assert "req-auth" in node_ids

    def test_phase_filter_returns_layer_nodes(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        pc = ProjectContext.create("brief-phase", base_path=str(tmp_path))
        pc.db.add_node(GraphNode(
            id="req-001", label="Requirement",
            properties={"content": "Login", "version": 1}
        ))
        pc.db.add_node(GraphNode(
            id="mod-001", label="CodeModule",
            properties={"summary": "Auth module", "version": 1}
        ))
        brief = pc.agent_brief(phase="intent")
        node_ids = [n["node_id"] for n in brief["nodes"]]
        assert "req-001" in node_ids
        assert "mod-001" not in node_ids

    def test_stale_count_reflects_stale_nodes(self, tmp_path):
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        pc = ProjectContext.create("brief-stale", base_path=str(tmp_path))
        pc.db.add_node(GraphNode(
            id="mod-001", label="CodeModule",
            properties={"summary": "Auth", "version": 1, "_stale": True}
        ))
        brief = pc.agent_brief()
        assert brief["stale_count"] == 1


class TestSearch:
    def _project_with_nodes(self, tmp_path):
        import uuid
        from contextcore.project.project_context import ProjectContext
        from contextcore.core.graph_structures import GraphNode
        uid = uuid.uuid4().hex[:8]
        pc = ProjectContext.create(f"search-proj-{uid}", base_path=str(tmp_path))
        pc.db.add_node(GraphNode(
            id=f"req:login-{uid}", label="Requirement",
            properties={"content": "Users must be able to log in securely", "version": 1},
        ))
        pc.db.add_node(GraphNode(
            id=f"req:logout-{uid}", label="Requirement",
            properties={"content": "Users must be able to log out", "version": 1},
        ))
        pc.db.add_node(GraphNode(
            id=f"code:auth-{uid}", label="CodeModule",
            properties={"summary": "Authentication service handles login and session", "version": 1},
        ))
        pc.db.add_node(GraphNode(
            id=f"tc:login-{uid}", label="TestCase",
            properties={"what": "Login flow", "how": "POST /auth/login with valid credentials", "version": 1},
        ))
        return pc

    def test_search_returns_list(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("login")
        assert isinstance(results, list)

    def test_search_finds_matching_nodes(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("login")
        assert len(results) > 0

    def test_search_returns_expected_keys(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("login")
        assert len(results) > 0
        r = results[0]
        assert "node_id" in r
        assert "label" in r
        assert "layer" in r
        assert "score" in r
        assert "snippet" in r

    def test_search_layer_field_is_correct(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("login")
        for r in results:
            if r["label"] == "Requirement":
                assert r["layer"] == "intent"
            elif r["label"] == "CodeModule":
                assert r["layer"] == "build"
            elif r["label"] == "TestCase":
                assert r["layer"] == "verify"

    def test_search_layer_filter_restricts_results(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("login", layer="intent")
        labels = {r["label"] for r in results}
        assert labels.issubset({"Requirement", "UserStory", "Goal"})

    def test_search_no_results_for_missing_term(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("xyzzy_nonexistent_term_abc")
        assert results == []

    def test_search_empty_query_returns_empty(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        assert pc.search("") == []

    def test_search_excludes_non_sdlc_nodes(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("login")
        labels = {r["label"] for r in results}
        assert "Project" not in labels

    def test_search_limit_respected(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("log", limit=2)
        assert len(results) <= 2

    def test_search_ordered_by_score_desc(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        results = pc.search("login log")
        scores = [r["score"] for r in results]
        assert scores == sorted(scores, reverse=True)

    def test_search_invalid_layer_raises(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        with pytest.raises(ValueError, match="Unknown SDLC layer"):
            pc.search("login", layer="nosuchlayer")

    def test_search_layer_case_normalised(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        # "Intent", "INTENT", "intent" should all return the same results
        results_lower = pc.search("login", layer="intent")
        results_upper = pc.search("login", layer="INTENT")
        results_mixed = pc.search("login", layer="Intent")
        ids_lower = {r["node_id"] for r in results_lower}
        assert {r["node_id"] for r in results_upper} == ids_lower
        assert {r["node_id"] for r in results_mixed} == ids_lower

    def test_search_duplicate_query_terms_not_inflated(self, tmp_path):
        pc = self._project_with_nodes(tmp_path)
        # Searching "login login" should score same as "login"
        results_once = pc.search("login")
        results_twice = pc.search("login login")
        # Scores should be identical — duplicate terms deduplicated
        ids_once = {r["node_id"]: r["score"] for r in results_once}
        ids_twice = {r["node_id"]: r["score"] for r in results_twice}
        assert ids_once == ids_twice


class TestAgentBriefTruncation:
    def test_long_property_truncated(self, tmp_path):
        import uuid
        from contextcore.core.graph_structures import GraphNode
        from contextcore.project.project_context import ProjectContext, _PROP_MAX_LEN
        uid = uuid.uuid4().hex[:8]
        pc = ProjectContext.create(f"trunc-proj-{uid}", base_path=str(tmp_path))
        long_content = "x" * (_PROP_MAX_LEN + 100)
        pc.db.add_node(GraphNode(
            id=f"req:big-{uid}", label="Requirement",
            properties={"content": long_content, "version": 1},
        ))
        brief = pc.agent_brief()
        nodes = brief["nodes"]
        req_nodes = [n for n in nodes if n["label"] == "Requirement"]
        assert len(req_nodes) == 1
        assert len(req_nodes[0]["properties"]["content"]) <= _PROP_MAX_LEN + 1  # +1 for ellipsis char

    def test_short_property_not_truncated(self, tmp_path):
        import uuid
        from contextcore.core.graph_structures import GraphNode
        from contextcore.project.project_context import ProjectContext
        uid = uuid.uuid4().hex[:8]
        pc = ProjectContext.create(f"short-proj-{uid}", base_path=str(tmp_path))
        short_content = "short text"
        pc.db.add_node(GraphNode(
            id=f"req:short-{uid}", label="Requirement",
            properties={"content": short_content, "version": 1},
        ))
        brief = pc.agent_brief()
        nodes = brief["nodes"]
        req_nodes = [n for n in nodes if n["label"] == "Requirement"]
        assert req_nodes[0]["properties"]["content"] == short_content
