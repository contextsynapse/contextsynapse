"""Retrieval and query benchmarks — measures query execution, search, and context building."""
import os
import sys
import time
import statistics
from typing import Dict, Any

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "bench-key")

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.core.graph_structures import GraphNode, GraphEdge
from contextsynapse.context.hub import ContextHub


def _build_test_graph(db: AIContextDB, node_count: int):
    """Build a test graph with documents, entities, and relationships."""
    import random
    random.seed(42)

    categories = ["tech", "finance", "health", "science", "politics"]
    entity_types = ["Person", "Company", "Product", "Location", "Event"]

    # Add document nodes
    for i in range(node_count // 2):
        db.add_node(GraphNode(
            id=f"doc_{i}",
            label="Document",
            properties={
                "title": f"Document {i}: Analysis of {categories[i % 5]}",
                "content": f"This is the content of document {i}. " * 20,
                "category": categories[i % 5],
                "source": f"source_{i % 3}",
                "created_at": f"2026-0{(i % 9) + 1}-01",
            },
        ))

    # Add entity nodes
    for i in range(node_count // 2):
        db.add_node(GraphNode(
            id=f"entity_{i}",
            label=entity_types[i % 5],
            properties={
                "name": f"Entity_{i}",
                "type": entity_types[i % 5],
                "importance": random.random(),
                "category": categories[i % 5],
            },
        ))

    # Add edges
    for i in range(node_count):
        src_type = "doc" if random.random() > 0.5 else "entity"
        tgt_type = "entity" if src_type == "doc" else "doc"
        src = f"{src_type}_{random.randint(0, node_count // 2 - 1)}"
        tgt = f"{tgt_type}_{random.randint(0, node_count // 2 - 1)}"
        edge_label = random.choice(["MENTIONS", "RELATED_TO", "CONTAINS", "DERIVED_FROM"])
        db.add_edge(GraphEdge(id=f"re_{i}", source=src, target=tgt, label=edge_label, properties={"weight": random.random()}))


def bench_get_all_with_label_filter(db: AIContextDB) -> Dict[str, Any]:
    """Benchmark label-filtered retrieval."""
    labels = ["Document", "Person", "Company", "Product", "Location", "Event"]
    times = []
    counts = {}
    for label in labels:
        t0 = time.perf_counter()
        nodes = db.get_all_nodes(label=label)
        times.append(time.perf_counter() - t0)
        counts[label] = len(nodes)

    return {
        "operation": "label_filter",
        "queries": len(labels),
        "counts": counts,
        "avg_ms": statistics.mean(times) * 1000,
        "total_sec": sum(times),
    }


def bench_multi_hop_traversal(db: AIContextDB, node_count: int) -> Dict[str, Any]:
    """Benchmark multi-hop graph traversal."""
    import random
    random.seed(789)
    start_nodes = [f"doc_{random.randint(0, node_count // 2 - 1)}" for _ in range(100)]

    results_by_depth = {}
    for depth in [1, 2, 3]:
        times = []
        total_reached = 0
        for nid in start_nodes:
            t0 = time.perf_counter()
            reached = db.traverse(nid, edge_label=None, max_depth=depth, direction="OUTGOING")
            times.append(time.perf_counter() - t0)
            total_reached += len(reached) if reached else 0

        results_by_depth[depth] = {
            "depth": depth,
            "queries": len(start_nodes),
            "avg_reached": total_reached / len(start_nodes),
            "avg_ms": statistics.mean(times) * 1000,
            "p99_ms": sorted(times)[int(len(times) * 0.99)] * 1000,
            "total_sec": sum(times),
        }

    return {
        "operation": "multi_hop_traversal",
        "by_depth": results_by_depth,
    }


def bench_context_hub_build(db: AIContextDB) -> Dict[str, Any]:
    """Benchmark ContextHub building from graph data."""
    times = []
    token_counts = []

    for _ in range(10):
        t0 = time.perf_counter()
        hub = ContextHub(system_prompt="You are an analyst.", max_tokens=8000)
        nodes = db.get_all_nodes(label="Document")[:50]
        hub.add_nodes(nodes)
        hub.add_graph_summary(db)
        messages = hub.to_messages()
        elapsed = time.perf_counter() - t0
        times.append(elapsed)
        token_counts.append(hub.estimate_tokens())

    return {
        "operation": "context_hub_build",
        "iterations": 10,
        "avg_ms": statistics.mean(times) * 1000,
        "p99_ms": max(times) * 1000,
        "avg_tokens": statistics.mean(token_counts),
        "avg_messages": len(messages),
    }


def bench_search_by_property(db: AIContextDB) -> Dict[str, Any]:
    """Benchmark property-based search across different selectivities."""
    categories = ["tech", "finance", "health", "science", "politics"]
    times = []
    results_counts = []

    for cat in categories:
        t0 = time.perf_counter()
        found = db.get_nodes_by_property("category", cat)
        times.append(time.perf_counter() - t0)
        results_counts.append(len(found))

    return {
        "operation": "property_search",
        "queries": len(categories),
        "avg_results": statistics.mean(results_counts),
        "avg_ms": statistics.mean(times) * 1000,
        "total_sec": sum(times),
    }


def run_all_benchmarks():
    """Run the full retrieval benchmark suite."""
    print("=" * 70)
    print("AIContextDB Retrieval Benchmark Suite")
    print("=" * 70)

    results = {}
    sizes = [1000, 10000, 50000]

    for size in sizes:
        print(f"\n{'─' * 70}")
        print(f"Graph Size: {size:,} nodes")
        print(f"{'─' * 70}")

        db = AIContextDB(name=f"ret_bench_{size}")
        _build_test_graph(db, size)

        # Label filter
        r = bench_get_all_with_label_filter(db)
        results[f"label_filter_{size}"] = r
        print(f"  Label filter:      avg={r['avg_ms']:.3f}ms | counts={r['counts']}")

        # Multi-hop traversal
        r = bench_multi_hop_traversal(db, size)
        results[f"traversal_{size}"] = r
        for depth, d in r["by_depth"].items():
            print(f"  Traverse depth={depth}:  avg={d['avg_ms']:.3f}ms | p99={d['p99_ms']:.3f}ms | avg_reached={d['avg_reached']:.1f}")

        # Property search
        r = bench_search_by_property(db)
        results[f"prop_search_{size}"] = r
        print(f"  Property search:   avg={r['avg_ms']:.3f}ms | avg_results={r['avg_results']:.0f}")

        # Context hub
        r = bench_context_hub_build(db)
        results[f"context_hub_{size}"] = r
        print(f"  Context hub build: avg={r['avg_ms']:.3f}ms | avg_tokens={r['avg_tokens']:.0f}")

    # Summary table
    print(f"\n{'=' * 70}")
    print("RETRIEVAL SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Size':>10} | {'Label Flt':>10} | {'Hop-1':>10} | {'Hop-2':>10} | {'Hop-3':>10} | {'Prop Search':>12}")
    print(f"{'─' * 10}-+-{'─' * 10}-+-{'─' * 10}-+-{'─' * 10}-+-{'─' * 10}-+-{'─' * 12}")
    for size in sizes:
        lf = results.get(f"label_filter_{size}", {}).get("avg_ms", 0)
        t1 = results.get(f"traversal_{size}", {}).get("by_depth", {}).get(1, {}).get("avg_ms", 0)
        t2 = results.get(f"traversal_{size}", {}).get("by_depth", {}).get(2, {}).get("avg_ms", 0)
        t3 = results.get(f"traversal_{size}", {}).get("by_depth", {}).get(3, {}).get("avg_ms", 0)
        ps = results.get(f"prop_search_{size}", {}).get("avg_ms", 0)
        print(f"{size:>10,} | {lf:>8.3f}ms | {t1:>8.3f}ms | {t2:>8.3f}ms | {t3:>8.3f}ms | {ps:>10.3f}ms")

    return results


if __name__ == "__main__":
    run_all_benchmarks()
