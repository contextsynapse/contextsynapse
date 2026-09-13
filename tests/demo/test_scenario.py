"""Tests for demo scenario seed data."""
import pytest


class TestAuthScenario:
    def test_scenario_has_nodes(self):
        from contextcore.demo.scenario import AUTH_SCENARIO
        assert len(AUTH_SCENARIO["nodes"]) >= 8

    def test_scenario_has_edges(self):
        from contextcore.demo.scenario import AUTH_SCENARIO
        assert len(AUTH_SCENARIO["edges"]) >= 2

    def test_all_node_types_are_sdlc(self):
        from contextcore.demo.scenario import AUTH_SCENARIO
        from contextcore.project.sdlc_schema import is_sdlc_node_type
        for node in AUTH_SCENARIO["nodes"]:
            assert is_sdlc_node_type(node["label"]), f"Not SDLC: {node['label']}"

    def test_all_edge_types_are_sdlc(self):
        from contextcore.demo.scenario import AUTH_SCENARIO
        from contextcore.project.sdlc_schema import SDLC_EDGE_TYPES
        for edge in AUTH_SCENARIO["edges"]:
            assert edge["label"] in SDLC_EDGE_TYPES, f"Not SDLC edge: {edge['label']}"


class TestSeedProject:
    def test_seed_returns_project_context(self, tmp_path):
        from contextcore.demo.scenario import seed_project
        from contextcore.project.project_context import ProjectContext
        pc = seed_project("test-seed", base_path=str(tmp_path))
        assert isinstance(pc, ProjectContext)

    def test_seed_creates_requirement_nodes(self, tmp_path):
        from contextcore.demo.scenario import seed_project
        pc = seed_project("test-seed-req", base_path=str(tmp_path))
        nodes = pc.db.get_all_nodes()
        req_nodes = [n for n in nodes if n.label == "Requirement"]
        assert len(req_nodes) == 3

    def test_seed_creates_arch_decisions(self, tmp_path):
        from contextcore.demo.scenario import seed_project
        pc = seed_project("test-seed-arch", base_path=str(tmp_path))
        nodes = pc.db.get_all_nodes()
        arch_nodes = [n for n in nodes if n.label == "ArchDecision"]
        assert len(arch_nodes) == 2

    def test_seed_creates_code_modules(self, tmp_path):
        from contextcore.demo.scenario import seed_project
        pc = seed_project("test-seed-code", base_path=str(tmp_path))
        nodes = pc.db.get_all_nodes()
        code_nodes = [n for n in nodes if n.label == "CodeModule"]
        assert len(code_nodes) == 1

    def test_seed_creates_test_cases(self, tmp_path):
        from contextcore.demo.scenario import seed_project
        pc = seed_project("test-seed-tc", base_path=str(tmp_path))
        nodes = pc.db.get_all_nodes()
        tc_nodes = [n for n in nodes if n.label == "TestCase"]
        assert len(tc_nodes) == 2

    def test_seed_coverage_is_37_percent(self, tmp_path):
        from contextcore.demo.scenario import seed_project
        pc = seed_project("test-seed-cov", base_path=str(tmp_path))
        score = pc.coverage_score()
        assert abs(score["overall"] - 0.37) < 0.01

    def test_seed_coverage_build_is_20_percent(self, tmp_path):
        from contextcore.demo.scenario import seed_project
        pc = seed_project("test-seed-build", base_path=str(tmp_path))
        score = pc.coverage_score()
        assert abs(score["intent"] - 0.60) < 0.01
        assert abs(score["design"] - 0.40) < 0.01
        assert abs(score["build"] - 0.20) < 0.01
        assert abs(score["verify"] - 0.40) < 0.01


class TestSeedProjectIndexing:
    def test_seed_tracks_all_node_ids_in_graph_ctx(self, tmp_path):
        """seed_project must go through ingest_sdlc_nodes so node_ids are tracked."""
        from contextcore.demo.scenario import seed_project, AUTH_SCENARIO
        # Patch ingest_sdlc_nodes to capture the GraphContext it returns
        import contextcore.demo.scenario as scenario_mod
        from unittest.mock import patch, MagicMock
        import contextcore.ingestion.sdlc_ingest as sdlc_ingest_mod

        captured = []
        original = sdlc_ingest_mod.ingest_sdlc_nodes

        def capturing(*args, **kwargs):
            ctx = original(*args, **kwargs)
            captured.append(ctx)
            return ctx

        with patch.object(scenario_mod, "ingest_sdlc_nodes", capturing):
            seed_project("test-index-tracking", base_path=str(tmp_path))

        assert len(captured) == 1
        ctx = captured[0]
        assert len(ctx.node_ids) == len(AUTH_SCENARIO["nodes"])

    def test_seed_node_ids_match_scenario_ids(self, tmp_path):
        """ingest_sdlc_nodes must preserve explicit node IDs like req:login."""
        from contextcore.demo.scenario import seed_project, AUTH_SCENARIO
        pc = seed_project("test-ids-match", base_path=str(tmp_path))

        expected_ids = {n["id"] for n in AUTH_SCENARIO["nodes"]}
        all_nodes = pc.db.get_all_nodes()
        # Filter out system-generated nodes (project, ctx_intelligence)
        actual_ids = {n.id for n in all_nodes
                      if not n.id.startswith("project:") and not n.id.startswith("ctx_intelligence")}

        assert expected_ids == actual_ids
