"""Tests for Federated Context — cross-namespace search with sensitivity enforcement."""

import pytest
from contextcore.core.federation import FederationRegistry, FederatedSearchEngine
from contextcore.core.registry import GraphRegistry
from contextcore.core.graph_structures import GraphNode


@pytest.fixture
def setup():
    """Create two namespaces with test data."""
    reg = GraphRegistry()

    # Team A's graph — mix of public and confidential
    g_a = reg.create_graph("team_a")
    g_a.add_node(GraphNode(id="a1", label="Fact", properties={
        "name": "Public finding", "content": "AI regulation is increasing globally",
        "sensitivity": "public",
    }), write_through=True)
    g_a.add_node(GraphNode(id="a2", label="Fact", properties={
        "name": "Confidential report", "content": "Internal revenue projections show 40% growth",
        "sensitivity": "confidential",
    }), write_through=True)
    g_a.add_node(GraphNode(id="a3", label="Entity", properties={
        "name": "EU", "content": "European Union",
        "sensitivity": "public",
    }), write_through=True)
    g_a.add_node(GraphNode(id="a4", label="Decision", properties={
        "name": "Secret strategy", "content": "Pivot to enterprise market",
        "sensitivity": "restricted",
    }), write_through=True)

    # Team B's graph
    g_b = reg.create_graph("team_b")
    g_b.add_node(GraphNode(id="b1", label="Fact", properties={
        "name": "Team B finding", "content": "Solar energy adoption accelerating",
    }), write_through=True)

    fed = FederationRegistry()
    return reg, fed


class TestFederationRegistry:

    def test_create_grant(self, setup):
        reg, fed = setup
        grant = fed.create_grant("team_a", "team_b", created_by="admin")
        assert grant.source_namespace == "team_a"
        assert grant.target_namespace == "team_b"
        assert grant.active is True

    def test_duplicate_grant_returns_existing(self, setup):
        reg, fed = setup
        g1 = fed.create_grant("team_a", "team_b")
        g2 = fed.create_grant("team_a", "team_b")
        assert g1.grant_id == g2.grant_id

    def test_self_federation_rejected(self, setup):
        reg, fed = setup
        with pytest.raises(ValueError):
            fed.create_grant("team_a", "team_a")

    def test_revoke_grant(self, setup):
        reg, fed = setup
        grant = fed.create_grant("team_a", "team_b")
        assert fed.revoke_grant(grant.grant_id) is True
        grants = fed.get_grants_for("team_b")
        assert len(grants) == 0

    def test_get_grants_for_target(self, setup):
        reg, fed = setup
        fed.create_grant("team_a", "team_b")
        grants = fed.get_grants_for("team_b")
        assert len(grants) == 1
        assert grants[0].source_namespace == "team_a"

    def test_get_exposures_from_source(self, setup):
        reg, fed = setup
        fed.create_grant("team_a", "team_b")
        exposures = fed.get_exposures_from("team_a")
        assert len(exposures) == 1

    def test_grant_with_label_whitelist(self, setup):
        reg, fed = setup
        grant = fed.create_grant("team_a", "team_b", node_labels=["Fact"])
        assert grant.node_labels == ["Fact"]

    def test_grant_serializable(self, setup):
        reg, fed = setup
        grant = fed.create_grant("team_a", "team_b")
        d = grant.to_dict()
        assert d["source_namespace"] == "team_a"
        assert d["active"] is True


class TestFederatedSearch:

    def test_local_only_search(self, setup):
        reg, fed = setup
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("solar energy", "team_b", include_federated=False)
        assert result.total >= 1
        assert len(result.federated_results) == 0

    def test_federated_search_includes_public(self, setup):
        reg, fed = setup
        fed.create_grant("team_a", "team_b")
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("AI regulation", "team_b", include_federated=True)
        # Should find team_a's public "AI regulation" fact
        fed_names = [r["name"] for r in result.federated_results]
        assert "Public finding" in fed_names

    def test_federated_search_blocks_confidential(self, setup):
        """CRITICAL SECURITY: confidential nodes must NEVER cross federation."""
        reg, fed = setup
        fed.create_grant("team_a", "team_b")
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("revenue", "team_b", include_federated=True)
        # "Confidential report" about revenue should NOT appear
        all_names = [r["name"] for r in result.federated_results]
        assert "Confidential report" not in all_names

    def test_federated_search_blocks_restricted(self, setup):
        """CRITICAL SECURITY: restricted nodes must NEVER cross federation."""
        reg, fed = setup
        fed.create_grant("team_a", "team_b")
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("strategy enterprise", "team_b", include_federated=True)
        all_names = [r["name"] for r in result.federated_results]
        assert "Secret strategy" not in all_names

    def test_federated_results_tagged(self, setup):
        reg, fed = setup
        fed.create_grant("team_a", "team_b")
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("regulation", "team_b", include_federated=True)
        for r in result.federated_results:
            assert r.get("_federated") is True
            assert r.get("_source_namespace") == "team_a"

    def test_label_whitelist_enforced(self, setup):
        reg, fed = setup
        # Only share Fact nodes, not Entity
        fed.create_grant("team_a", "team_b", node_labels=["Fact"])
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("EU European", "team_b", include_federated=True)
        # Entity "EU" should NOT appear because whitelist only allows Fact
        fed_labels = [r["label"] for r in result.federated_results]
        assert "Entity" not in fed_labels

    def test_no_grants_no_federated(self, setup):
        reg, fed = setup
        # No grant created
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("anything", "team_b", include_federated=True)
        assert len(result.federated_results) == 0

    def test_result_serializable(self, setup):
        reg, fed = setup
        fed.create_grant("team_a", "team_b")
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("AI", "team_b")
        d = result.to_dict()
        assert "local_results" in d
        assert "federated_results" in d
        assert "total" in d

    def test_search_with_label_filter(self, setup):
        reg, fed = setup
        fed.create_grant("team_a", "team_b")
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("", "team_b", label="Entity", include_federated=True)
        # Only Entity nodes from team_a (that are public)
        for r in result.federated_results:
            assert r["label"] == "Entity"

    def test_multiple_sources(self, setup):
        reg, fed = setup
        # Add team_c
        g_c = reg.create_graph("team_c")
        g_c.add_node(GraphNode(id="c1", label="Fact", properties={
            "name": "Team C data", "content": "Climate change research",
            "sensitivity": "public",
        }), write_through=True)
        fed.create_grant("team_a", "team_b")
        fed.create_grant("team_c", "team_b")
        engine = FederatedSearchEngine(reg, fed)
        result = engine.search("", "team_b", include_federated=True)
        assert len(result.sources) >= 2  # team_b + at least one federated
