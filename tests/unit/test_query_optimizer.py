"""Tests for AIQL Query Optimizer — cost-based plan selection."""

import pytest
from contextcore.aiql.compiler.statistics import (
    GraphStatistics, CostEstimator, QueryPlanSelector, ExplainResult,
    GraphStatisticsCollector,
)


@pytest.fixture
def stats():
    """Graph with 10K nodes across 3 labels."""
    return GraphStatistics(
        total_nodes=10000,
        total_edges=25000,
        node_count_by_label={"Fact": 5000, "Entity": 3000, "Task": 2000},
        edge_count_by_type={"RELATED": 15000, "DEPENDS_ON": 10000},
        label_index_available=True,
    )


@pytest.fixture
def stats_no_index():
    return GraphStatistics(
        total_nodes=10000,
        total_edges=25000,
        node_count_by_label={"Fact": 5000, "Entity": 3000, "Task": 2000},
        label_index_available=False,
    )


class TestCostEstimator:
    def test_full_scan_cost(self, stats):
        est = CostEstimator()
        cost = est.estimate_full_scan(stats)
        assert cost.scan_type == "full_scan"
        assert cost.estimated_rows == 10000
        assert cost.estimated_cost == 10.0  # 10000 * 0.001

    def test_label_scan_cheaper_than_full(self, stats):
        est = CostEstimator()
        full = est.estimate_full_scan(stats)
        label = est.estimate_label_scan(stats, "Task")
        assert label.estimated_cost < full.estimated_cost
        assert label.estimated_rows == 2000

    def test_label_scan_cost(self, stats):
        est = CostEstimator()
        cost = est.estimate_label_scan(stats, "Fact")
        assert cost.scan_type == "label_index"
        assert cost.estimated_rows == 5000
        assert cost.estimated_cost < 10.0  # cheaper than full scan

    def test_property_filter_cost(self, stats):
        est = CostEstimator()
        cost = est.estimate_property_filter(stats, "Fact", "name")
        assert cost.scan_type == "property_filter"
        assert cost.estimated_rows == 2500  # 5000 / 2 heuristic
        assert "property filter" in cost.explanation

    def test_unknown_label_falls_back(self, stats):
        est = CostEstimator()
        cost = est.estimate_label_scan(stats, "Unknown")
        # Unknown label defaults to total_nodes
        assert cost.estimated_rows == 10000


class TestQueryPlanSelector:
    def test_no_label_full_scan(self, stats):
        sel = QueryPlanSelector()
        plan = sel.select_plan(stats)
        assert plan.scan_type == "full_scan"
        assert plan.label_filter is None

    def test_with_label_uses_index(self, stats):
        sel = QueryPlanSelector()
        plan = sel.select_plan(stats, label="Fact")
        assert plan.scan_type == "label_index"
        assert plan.label_filter == "Fact"
        assert plan.cost.estimated_rows == 5000

    def test_with_label_no_index_full_scan(self, stats_no_index):
        sel = QueryPlanSelector()
        plan = sel.select_plan(stats_no_index, label="Fact")
        assert plan.scan_type == "full_scan"
        assert plan.label_filter == "Fact"

    def test_with_where_uses_property_filter(self, stats):
        sel = QueryPlanSelector()
        plan = sel.select_plan(stats, label="Task", where_props={"status": "open"})
        assert plan.scan_type == "label_index"
        assert plan.property_filters == {"status": "open"}
        assert "property filter" in plan.cost.explanation

    def test_plan_serializable(self, stats):
        sel = QueryPlanSelector()
        plan = sel.select_plan(stats, label="Entity")
        d = plan.to_dict()
        assert d["scan_type"] == "label_index"
        assert d["cost"]["estimated_rows"] == 3000


class TestExplainResult:
    def test_explain_to_dict(self, stats):
        sel = QueryPlanSelector()
        plan = sel.select_plan(stats, label="Fact")
        explain = ExplainResult(query="SELECT * FROM Fact", plan=plan, statistics=stats)
        d = explain.to_dict()
        assert d["query"] == "SELECT * FROM Fact"
        assert d["plan"]["scan_type"] == "label_index"
        assert d["statistics"]["total_nodes"] == 10000


class TestStatisticsCollector:
    def test_collect_empty(self):
        collector = GraphStatisticsCollector()
        stats = collector.collect()
        assert stats.total_nodes == 0

    def test_collect_from_graph_with_statistics(self):
        """Test with a mock graph that has get_statistics()."""
        class MockGraph:
            def get_statistics(self):
                return {
                    "total_nodes": 500,
                    "total_edges": 1200,
                    "node_count_by_label": {"Fact": 300, "Entity": 200},
                    "edge_count_by_type": {"RELATED": 1200},
                    "label_index_available": True,
                }
        collector = GraphStatisticsCollector(MockGraph())
        stats = collector.collect()
        assert stats.total_nodes == 500
        assert stats.node_count_by_label["Fact"] == 300
        assert stats.label_index_available is True
