"""
Graph Statistics Collector
===========================
Collects graph statistics for the AIQL query optimizer.
Used by the cost estimator to choose between full scan, label index, etc.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, Set

logger = logging.getLogger(__name__)


@dataclass
class GraphStatistics:
    """Statistics about a graph namespace for query optimization."""
    total_nodes: int = 0
    total_edges: int = 0
    node_count_by_label: Dict[str, int] = field(default_factory=dict)
    edge_count_by_type: Dict[str, int] = field(default_factory=dict)
    label_index_available: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "total_nodes": self.total_nodes,
            "total_edges": self.total_edges,
            "node_count_by_label": self.node_count_by_label,
            "edge_count_by_type": self.edge_count_by_type,
            "label_index_available": self.label_index_available,
        }


@dataclass
class CostEstimate:
    """Cost estimate for a query execution plan."""
    scan_type: str       # "full_scan" | "label_index" | "property_filter"
    estimated_rows: int
    estimated_cost: float
    explanation: str

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_type": self.scan_type,
            "estimated_rows": self.estimated_rows,
            "estimated_cost": round(self.estimated_cost, 4),
            "explanation": self.explanation,
        }


@dataclass
class QueryPlan:
    """A planned query execution strategy."""
    scan_type: str
    label_filter: Optional[str] = None
    property_filters: Dict[str, Any] = field(default_factory=dict)
    cost: Optional[CostEstimate] = None
    use_vector: bool = False
    use_bm25: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return {
            "scan_type": self.scan_type,
            "label_filter": self.label_filter,
            "property_filters": self.property_filters,
            "cost": self.cost.to_dict() if self.cost else None,
            "use_vector": self.use_vector,
            "use_bm25": self.use_bm25,
        }


class GraphStatisticsCollector:
    """Collects statistics from a graph for query optimization."""

    def __init__(self, graph_storage=None):
        self._graph = graph_storage

    def collect(self, graph=None) -> GraphStatistics:
        """Collect statistics from the graph.

        Uses get_statistics() if available (fast path via label index),
        otherwise falls back to scanning.
        """
        db = graph or self._graph
        if not db:
            return GraphStatistics()

        # Fast path: use built-in statistics
        if hasattr(db, "get_statistics"):
            try:
                raw = db.get_statistics()
                return GraphStatistics(
                    total_nodes=raw.get("total_nodes", 0),
                    total_edges=raw.get("total_edges", 0),
                    node_count_by_label=raw.get("node_count_by_label", {}),
                    edge_count_by_type=raw.get("edge_count_by_type", {}),
                    label_index_available=raw.get("label_index_available", False),
                )
            except Exception as e:
                logger.debug("get_statistics() failed, falling back to scan: %s", e)

        # Slow path: count manually
        try:
            all_nodes = db.get_all_nodes()
            label_counts: Dict[str, int] = {}
            for n in all_nodes:
                lbl = getattr(n, "label", "")
                label_counts[lbl] = label_counts.get(lbl, 0) + 1

            all_edges = db.get_all_edges()
            edge_counts: Dict[str, int] = {}
            for e in all_edges:
                et = getattr(e, "label", "")
                edge_counts[et] = edge_counts.get(et, 0) + 1

            return GraphStatistics(
                total_nodes=len(all_nodes),
                total_edges=len(all_edges),
                node_count_by_label=label_counts,
                edge_count_by_type=edge_counts,
                label_index_available=False,
            )
        except Exception:
            return GraphStatistics()


class CostEstimator:
    """Estimates query cost based on graph statistics."""

    PER_NODE_COST = 0.001   # cost units per node scanned
    INDEX_OVERHEAD = 0.01   # fixed overhead for index lookup

    def estimate_full_scan(self, stats: GraphStatistics) -> CostEstimate:
        """Cost of scanning all nodes."""
        cost = stats.total_nodes * self.PER_NODE_COST
        return CostEstimate(
            scan_type="full_scan",
            estimated_rows=stats.total_nodes,
            estimated_cost=cost,
            explanation=f"Full scan of {stats.total_nodes} nodes",
        )

    def estimate_label_scan(self, stats: GraphStatistics, label: str) -> CostEstimate:
        """Cost of scanning nodes by label index."""
        count = stats.node_count_by_label.get(label, stats.total_nodes)
        cost = count * self.PER_NODE_COST + self.INDEX_OVERHEAD
        return CostEstimate(
            scan_type="label_index",
            estimated_rows=count,
            estimated_cost=cost,
            explanation=f"Label index scan: {count} '{label}' nodes (of {stats.total_nodes} total)",
        )

    def estimate_property_filter(self, stats: GraphStatistics, label: str, prop: str) -> CostEstimate:
        """Cost of label scan + property filter."""
        label_count = stats.node_count_by_label.get(label, stats.total_nodes)
        # Assume property filter reduces by ~50% (heuristic)
        estimated = max(1, label_count // 2)
        cost = label_count * self.PER_NODE_COST + self.INDEX_OVERHEAD * 2
        return CostEstimate(
            scan_type="property_filter",
            estimated_rows=estimated,
            estimated_cost=cost,
            explanation=f"Label index '{label}' ({label_count} nodes) + property filter on '{prop}'",
        )


class QueryPlanSelector:
    """Selects the optimal query execution plan based on cost estimates."""

    def __init__(self):
        self._estimator = CostEstimator()

    def select_plan(
        self,
        stats: GraphStatistics,
        label: Optional[str] = None,
        where_props: Optional[Dict[str, Any]] = None,
    ) -> QueryPlan:
        """Choose the cheapest execution plan.

        Decision tree:
        1. If label provided AND label index available → label_index scan
        2. If label provided but no index → full_scan with label filter
        3. If no label → full_scan
        """
        where_props = where_props or {}

        if not label:
            cost = self._estimator.estimate_full_scan(stats)
            return QueryPlan(scan_type="full_scan", cost=cost)

        if stats.label_index_available:
            if where_props:
                prop = next(iter(where_props))
                cost = self._estimator.estimate_property_filter(stats, label, prop)
                return QueryPlan(
                    scan_type="label_index",
                    label_filter=label,
                    property_filters=where_props,
                    cost=cost,
                )
            else:
                cost = self._estimator.estimate_label_scan(stats, label)
                return QueryPlan(
                    scan_type="label_index",
                    label_filter=label,
                    cost=cost,
                )
        else:
            # No index — full scan with in-memory label filter
            cost = self._estimator.estimate_full_scan(stats)
            cost.explanation += f" (filtered to label '{label}' in memory)"
            return QueryPlan(
                scan_type="full_scan",
                label_filter=label,
                cost=cost,
            )


@dataclass
class ExplainResult:
    """Result of EXPLAIN query — shows plan without executing."""
    query: str
    plan: QueryPlan
    statistics: GraphStatistics

    def to_dict(self) -> Dict[str, Any]:
        return {
            "query": self.query,
            "plan": self.plan.to_dict(),
            "statistics": self.statistics.to_dict(),
        }
