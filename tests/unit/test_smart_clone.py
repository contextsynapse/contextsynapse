"""Tests for smart clone — preserves intelligence, filters noise."""

import pytest
from contextcore.core.registry import GraphRegistry
from contextcore.core.replication import GraphReplicator
from contextcore.core.graph_structures import GraphNode, GraphEdge


@pytest.fixture
def source_graph():
    reg = GraphRegistry()
    g = reg.create_graph("clone_source")

    # Original data — always cloned
    g.add_node(GraphNode(id="f1", label="Fact", properties={"content": "Alwar 92.90% pass rate"}), write_through=True)
    g.add_node(GraphNode(id="f2", label="Fact", properties={"content": "PMK candidates for 2026"}), write_through=True)
    g.add_node(GraphNode(id="e1", label="Entity", properties={"name": "Rajasthan Board"}), write_through=True)
    g.add_node(GraphNode(id="d1", label="Document", properties={"name": "TOI News"}), write_through=True)

    # Intelligence — should be carried forward
    g.add_node(GraphNode(id="dec1", label="Decision", properties={"title": "Focus on exam results", "rationale": "Most data available"}), write_through=True)
    g.add_node(GraphNode(id="ins1", label="Insight", properties={"content": "Rajasthan leads in exam coverage"}), write_through=True)
    g.add_node(GraphNode(id="fin1", label="Finding", properties={"content": "Alwar district had highest pass rate at 92.90%"}), write_through=True)
    g.add_node(GraphNode(id="task1", label="Task", properties={"title": "Analyze PMK", "status": "completed"}), write_through=True)
    g.add_node(GraphNode(id="mem1", label="Memory", properties={"content": "Exam data in Fact nodes"}), write_through=True)

    # Noise — should be filtered
    g.add_node(GraphNode(id="at1", label="AgentThought", properties={"content": "thinking about search"}), write_through=True)
    g.add_node(GraphNode(id="aa1", label="AgentAction", properties={"action": "search_nodes"}), write_through=True)
    g.add_node(GraphNode(id="am1", label="AgentMessage", properties={"content": "hello from agent"}), write_through=True)
    g.add_node(GraphNode(id="er1", label="ExperimentRun", properties={"run_id": "test"}), write_through=True)

    # Garbage Finding — should be filtered
    g.add_node(GraphNode(id="fin_bad", label="Finding", properties={"content": "Analysis: Found 20 node(s) via aiql: [Fact] stuff"}), write_through=True)

    # Archived task — should be filtered
    g.add_node(GraphNode(id="task_old", label="Task", properties={"title": "Old task", "status": "archived"}), write_through=True)

    # Edges
    g.add_edge(GraphEdge(id="edge1", source="f1", target="e1", label="MENTIONS"))
    g.add_edge(GraphEdge(id="edge2", source="dec1", target="f1", label="BASED_ON"))
    g.add_edge(GraphEdge(id="edge3", source="at1", target="aa1", label="LED_TO"))  # noise edge

    return reg


class TestSmartClone:

    def test_clone_preserves_original_data(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        result = rep.clone_graph("clone_source", "clone_target_1")
        target = source_graph.get_graph("clone_target_1")

        labels = [getattr(n, "label", "") for n in target.get_all_nodes()]
        assert "Fact" in labels
        assert "Entity" in labels
        assert "Document" in labels

    def test_clone_carries_decisions(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        rep.clone_graph("clone_source", "clone_target_2")
        target = source_graph.get_graph("clone_target_2")

        decisions = [n for n in target.get_all_nodes() if getattr(n, "label", "") == "Decision"]
        assert len(decisions) == 1
        assert decisions[0].properties.get("title") == "Focus on exam results"

    def test_clone_carries_insights(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        rep.clone_graph("clone_source", "clone_target_3")
        target = source_graph.get_graph("clone_target_3")

        insights = [n for n in target.get_all_nodes() if getattr(n, "label", "") == "Insight"]
        assert len(insights) == 1

    def test_clone_carries_good_findings(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        rep.clone_graph("clone_source", "clone_target_4")
        target = source_graph.get_graph("clone_target_4")

        findings = [n for n in target.get_all_nodes() if getattr(n, "label", "") == "Finding"]
        assert len(findings) == 1  # good finding only, not the garbage one
        assert "92.90%" in findings[0].properties.get("content", "")

    def test_clone_carries_completed_tasks(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        rep.clone_graph("clone_source", "clone_target_5")
        target = source_graph.get_graph("clone_target_5")

        tasks = [n for n in target.get_all_nodes() if getattr(n, "label", "") == "Task"]
        assert len(tasks) == 1
        assert tasks[0].properties.get("status") == "completed"

    def test_clone_filters_noise(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        result = rep.clone_graph("clone_source", "clone_target_6")
        target = source_graph.get_graph("clone_target_6")

        labels = [getattr(n, "label", "") for n in target.get_all_nodes()]
        assert "AgentThought" not in labels
        assert "AgentAction" not in labels
        assert "AgentMessage" not in labels
        assert "ExperimentRun" not in labels
        assert result["skipped_noise"] >= 4

    def test_clone_filters_garbage_findings(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        rep.clone_graph("clone_source", "clone_target_7")
        target = source_graph.get_graph("clone_target_7")

        findings = [n for n in target.get_all_nodes() if getattr(n, "label", "") == "Finding"]
        for f in findings:
            assert "Found 20 node(s) via aiql" not in f.properties.get("content", "")

    def test_clone_filters_archived_tasks(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        rep.clone_graph("clone_source", "clone_target_8")
        target = source_graph.get_graph("clone_target_8")

        tasks = [n for n in target.get_all_nodes() if getattr(n, "label", "") == "Task"]
        for t in tasks:
            assert t.properties.get("status") != "archived"

    def test_clone_edges_follow_nodes(self, source_graph):
        """Edges pointing to filtered noise nodes should not be cloned."""
        rep = GraphReplicator(graph_registry=source_graph)
        rep.clone_graph("clone_source", "clone_target_9")
        target = source_graph.get_graph("clone_target_9")

        edges = target.get_all_edges()
        edge_labels = [getattr(e, "label", "") for e in edges]
        assert "MENTIONS" in edge_labels  # f1 → e1 (both cloned)
        assert "BASED_ON" in edge_labels  # dec1 → f1 (both cloned)
        assert "LED_TO" not in edge_labels  # at1 → aa1 (both noise, skipped)

    def test_clone_reports_intelligence_count(self, source_graph):
        rep = GraphReplicator(graph_registry=source_graph)
        result = rep.clone_graph("clone_source", "clone_target_10")
        assert result["carried_intelligence"] >= 4  # Decision + Insight + Finding + Task + Memory

    def test_full_clone_skips_nothing(self, source_graph):
        """smart_clone=False should copy everything."""
        rep = GraphReplicator(graph_registry=source_graph)
        result = rep.clone_graph("clone_source", "clone_target_11", smart_clone=False)
        target = source_graph.get_graph("clone_target_11")

        labels = [getattr(n, "label", "") for n in target.get_all_nodes()]
        assert "AgentThought" in labels  # noise preserved
        assert "AgentAction" in labels
        assert result.get("skipped_noise", 0) == 0
