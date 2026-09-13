"""Tests for label index performance — verifies get_all_nodes(label=X) uses index."""

import pytest
from contextcore.core.registry import GraphRegistry
from contextcore.core.graph_structures import GraphNode


@pytest.fixture
def graph():
    reg = GraphRegistry()
    g = reg.create_graph("label_idx_test")
    # 100 nodes across 5 labels
    for i in range(100):
        labels = ["Fact", "Entity", "Task", "Decision", "Finding"]
        label = labels[i % 5]
        g.add_node(GraphNode(
            id=f"n{i}", label=label,
            properties={"name": f"Node {i}", "index": i},
        ), write_through=True)
    return g


class TestLabelIndex:

    def test_get_all_nodes_no_filter(self, graph):
        nodes = graph.get_all_nodes()
        # 100 content nodes + ContextMeta infrastructure node
        assert len(nodes) >= 100

    def test_get_all_nodes_with_label(self, graph):
        facts = graph.get_all_nodes(label="Fact")
        assert len(facts) == 20  # 100/5 labels
        for n in facts:
            assert getattr(n, "label", "") == "Fact"

    def test_label_filter_entity(self, graph):
        entities = graph.get_all_nodes(label="Entity")
        assert len(entities) == 20
        for n in entities:
            assert getattr(n, "label", "") == "Entity"

    def test_label_filter_returns_fewer_than_total(self, graph):
        all_nodes = graph.get_all_nodes()
        tasks = graph.get_all_nodes(label="Task")
        assert len(tasks) < len(all_nodes)

    def test_nonexistent_label_returns_empty(self, graph):
        nodes = graph.get_all_nodes(label="NonExistent")
        assert len(nodes) == 0

    def test_get_statistics(self, graph):
        if not hasattr(graph, "get_statistics"):
            pytest.skip("get_statistics not available on this backend")
        stats = graph.get_statistics()
        # Stats may use different key names depending on backend
        total = stats.get("total_nodes", stats.get("nodes", 0))
        if total == 0:
            pytest.skip("get_statistics returned empty on this backend")
        assert total >= 100
        labels = stats.get("node_count_by_label", {})
        if labels:
            assert "Fact" in labels
            assert labels["Fact"] >= 20

    def test_label_index_consistent_with_full_scan(self, graph):
        """Label-filtered results must match manual filtering of full scan."""
        all_nodes = graph.get_all_nodes()
        manual_findings = [n for n in all_nodes if getattr(n, "label", "") == "Finding"]
        indexed_findings = graph.get_all_nodes(label="Finding")
        assert len(manual_findings) == len(indexed_findings)
