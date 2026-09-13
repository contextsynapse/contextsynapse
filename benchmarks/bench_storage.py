"""Storage layer benchmarks — measures write/read throughput and memory."""
import os
import sys
import time
import statistics
import tracemalloc
import uuid
from typing import Dict, List, Any

# Ensure the project root is on sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "bench-key")

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.core.graph_structures import GraphNode, GraphEdge


def bench_node_insertion(db: AIContextDB, count: int) -> Dict[str, Any]:
    """Benchmark node insertion throughput."""
    times = []
    for i in range(count):
        t0 = time.perf_counter()
        node = GraphNode(
            id=f"node_{i}",
            label="BenchNode",
            properties={
                "name": f"Node {i}",
                "value": i * 1.5,
                "category": f"cat_{i % 10}",
                "tags": ["bench", f"group_{i % 5}"],
            },
        )
        db.add_node(node)
        times.append(time.perf_counter() - t0)

    return {
        "operation": "node_insertion",
        "count": count,
        "total_sec": sum(times),
        "avg_ms": statistics.mean(times) * 1000,
        "p50_ms": statistics.median(times) * 1000,
        "p99_ms": sorted(times)[int(count * 0.99)] * 1000 if count > 100 else max(times) * 1000,
        "throughput_ops_sec": count / sum(times) if sum(times) > 0 else 0,
    }


def bench_edge_insertion(db: AIContextDB, node_count: int) -> Dict[str, Any]:
    """Benchmark edge insertion throughput."""
    import random
    random.seed(42)

    # Ensure nodes exist
    existing = db.get_all_nodes()
    existing_ids = {n.id if hasattr(n, 'id') else n.get('id', '') for n in existing}
    for i in range(node_count):
        nid = f"node_{i}"
        if nid not in existing_ids:
            db.add_node(GraphNode(id=nid, label="BenchNode", properties={"name": f"Node {i}"}))

    edge_count = node_count * 3  # avg degree 3
    times = []
    for i in range(edge_count):
        src = f"node_{random.randint(0, node_count - 1)}"
        tgt = f"node_{random.randint(0, node_count - 1)}"
        if src == tgt:
            tgt = f"node_{(int(tgt.split('_')[1]) + 1) % node_count}"
        t0 = time.perf_counter()
        edge = GraphEdge(
            id=f"e_{i}",
            source=src,
            target=tgt,
            label=random.choice(["RELATED", "DEPENDS_ON", "MENTIONS", "CONTAINS"]),
            properties={"weight": random.random(), "created": "2026-01-01"},
        )
        db.add_edge(edge)
        times.append(time.perf_counter() - t0)

    return {
        "operation": "edge_insertion",
        "count": edge_count,
        "total_sec": sum(times),
        "avg_ms": statistics.mean(times) * 1000,
        "p50_ms": statistics.median(times) * 1000,
        "p99_ms": sorted(times)[int(edge_count * 0.99)] * 1000 if edge_count > 100 else max(times) * 1000,
        "throughput_ops_sec": edge_count / sum(times) if sum(times) > 0 else 0,
    }


def bench_node_lookup(db: AIContextDB, count: int) -> Dict[str, Any]:
    """Benchmark random node lookups."""
    import random
    random.seed(123)
    ids = [f"node_{random.randint(0, count - 1)}" for _ in range(min(count, 10000))]

    times = []
    found = 0
    for nid in ids:
        t0 = time.perf_counter()
        result = db.get_node(nid)
        times.append(time.perf_counter() - t0)
        if result is not None:
            found += 1

    return {
        "operation": "node_lookup",
        "count": len(ids),
        "found": found,
        "total_sec": sum(times),
        "avg_us": statistics.mean(times) * 1_000_000,
        "p50_us": statistics.median(times) * 1_000_000,
        "p99_us": sorted(times)[int(len(times) * 0.99)] * 1_000_000 if len(times) > 100 else max(times) * 1_000_000,
        "throughput_ops_sec": len(ids) / sum(times) if sum(times) > 0 else 0,
    }


def bench_neighbor_traversal(db: AIContextDB, count: int) -> Dict[str, Any]:
    """Benchmark neighbor lookups."""
    import random
    random.seed(456)
    ids = [f"node_{random.randint(0, count - 1)}" for _ in range(min(count, 5000))]

    times = []
    total_neighbors = 0
    for nid in ids:
        t0 = time.perf_counter()
        neighbors = db.get_neighbors(nid)
        times.append(time.perf_counter() - t0)
        total_neighbors += len(neighbors)

    return {
        "operation": "neighbor_traversal",
        "count": len(ids),
        "total_neighbors_found": total_neighbors,
        "avg_neighbors": total_neighbors / len(ids) if ids else 0,
        "total_sec": sum(times),
        "avg_us": statistics.mean(times) * 1_000_000,
        "p50_us": statistics.median(times) * 1_000_000,
        "throughput_ops_sec": len(ids) / sum(times) if sum(times) > 0 else 0,
    }


def bench_get_all_nodes(db: AIContextDB) -> Dict[str, Any]:
    """Benchmark full graph scan."""
    t0 = time.perf_counter()
    all_nodes = db.get_all_nodes()
    elapsed = time.perf_counter() - t0
    return {
        "operation": "get_all_nodes",
        "count": len(all_nodes),
        "elapsed_sec": elapsed,
        "throughput_nodes_sec": len(all_nodes) / elapsed if elapsed > 0 else 0,
    }


def bench_property_filter(db: AIContextDB, count: int) -> Dict[str, Any]:
    """Benchmark property-based node filtering."""
    times = []
    total_found = 0
    for i in range(10):
        cat = f"cat_{i}"
        t0 = time.perf_counter()
        results = db.get_nodes_by_property("category", cat)
        times.append(time.perf_counter() - t0)
        total_found += len(results)

    return {
        "operation": "property_filter",
        "queries": 10,
        "total_found": total_found,
        "total_sec": sum(times),
        "avg_ms": statistics.mean(times) * 1000,
        "p50_ms": statistics.median(times) * 1000,
    }


def bench_memory_usage(node_count: int) -> Dict[str, Any]:
    """Measure memory usage for a graph of given size."""
    tracemalloc.start()
    db = AIContextDB(name=f"mem_bench_{node_count}")

    snap_before = tracemalloc.take_snapshot()

    for i in range(node_count):
        db.add_node(GraphNode(
            id=f"m_{i}",
            label="MemNode",
            properties={"name": f"Node {i}", "value": i, "desc": f"Description for node {i} " * 5},
        ))

    # Add edges (avg degree 2)
    import random
    random.seed(42)
    for i in range(node_count * 2):
        src = f"m_{random.randint(0, node_count - 1)}"
        tgt = f"m_{random.randint(0, node_count - 1)}"
        if src != tgt:
            db.add_edge(GraphEdge(id=f"me_{i}", source=src, target=tgt, label="LINK", properties={}))

    snap_after = tracemalloc.take_snapshot()
    tracemalloc.stop()

    stats = snap_after.compare_to(snap_before, 'lineno')
    total_bytes = sum(s.size_diff for s in stats if s.size_diff > 0)

    return {
        "operation": "memory_usage",
        "node_count": node_count,
        "edge_count": node_count * 2,
        "memory_mb": total_bytes / (1024 * 1024),
        "bytes_per_node": total_bytes / node_count if node_count > 0 else 0,
    }


def bench_save_load(db: AIContextDB, temp_dir: str) -> Dict[str, Any]:
    """Benchmark save and load performance."""
    import os
    save_path = os.path.join(temp_dir, "bench_graph.json")

    t0 = time.perf_counter()
    db.save(save_path)
    save_time = time.perf_counter() - t0

    file_size = os.path.getsize(save_path) if os.path.exists(save_path) else 0

    db2 = AIContextDB(name="bench_load")
    t0 = time.perf_counter()
    db2.load(save_path)
    load_time = time.perf_counter() - t0

    return {
        "operation": "save_load",
        "save_sec": save_time,
        "load_sec": load_time,
        "file_size_mb": file_size / (1024 * 1024),
    }


def run_all_benchmarks():
    """Run the full storage benchmark suite and print results."""
    print("=" * 70)
    print("AIContextDB Storage Benchmark Suite")
    print("=" * 70)

    results = {}
    sizes = [1000, 10000, 50000]

    for size in sizes:
        print(f"\n{'─' * 70}")
        print(f"Graph Size: {size:,} nodes")
        print(f"{'─' * 70}")

        db = AIContextDB(name=f"bench_{size}")

        # Node insertion
        r = bench_node_insertion(db, size)
        results[f"insert_nodes_{size}"] = r
        print(f"  Node insertion:    {r['throughput_ops_sec']:>10,.0f} ops/sec | avg={r['avg_ms']:.3f}ms | p99={r['p99_ms']:.3f}ms")

        # Edge insertion
        r = bench_edge_insertion(db, size)
        results[f"insert_edges_{size}"] = r
        print(f"  Edge insertion:    {r['throughput_ops_sec']:>10,.0f} ops/sec | avg={r['avg_ms']:.3f}ms | p99={r['p99_ms']:.3f}ms")

        # Node lookup
        r = bench_node_lookup(db, size)
        results[f"lookup_nodes_{size}"] = r
        print(f"  Node lookup:       {r['throughput_ops_sec']:>10,.0f} ops/sec | avg={r['avg_us']:.1f}μs | p99={r['p99_us']:.1f}μs")

        # Neighbor traversal
        r = bench_neighbor_traversal(db, size)
        results[f"traverse_{size}"] = r
        print(f"  Neighbor traverse: {r['throughput_ops_sec']:>10,.0f} ops/sec | avg={r['avg_us']:.1f}μs | avg_neighbors={r['avg_neighbors']:.1f}")

        # Full scan
        r = bench_get_all_nodes(db)
        results[f"scan_{size}"] = r
        print(f"  Full scan:         {r['throughput_nodes_sec']:>10,.0f} nodes/sec | {r['elapsed_sec']:.3f}s total")

        # Property filter
        r = bench_property_filter(db, size)
        results[f"prop_filter_{size}"] = r
        print(f"  Property filter:   avg={r['avg_ms']:.3f}ms | found={r['total_found']:,}")

        # Memory
        r = bench_memory_usage(size)
        results[f"memory_{size}"] = r
        print(f"  Memory:            {r['memory_mb']:.1f} MB | {r['bytes_per_node']:.0f} bytes/node")

        # Save/Load
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            r = bench_save_load(db, td)
            results[f"saveload_{size}"] = r
            print(f"  Save:              {r['save_sec']:.3f}s | Load: {r['load_sec']:.3f}s | File: {r['file_size_mb']:.1f} MB")

    # Summary
    print(f"\n{'=' * 70}")
    print("SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Size':>10} | {'Insert N/s':>12} | {'Insert E/s':>12} | {'Lookup/s':>12} | {'Memory MB':>10}")
    print(f"{'─' * 10}-+-{'─' * 12}-+-{'─' * 12}-+-{'─' * 12}-+-{'─' * 10}")
    for size in sizes:
        n_ops = results.get(f"insert_nodes_{size}", {}).get("throughput_ops_sec", 0)
        e_ops = results.get(f"insert_edges_{size}", {}).get("throughput_ops_sec", 0)
        l_ops = results.get(f"lookup_nodes_{size}", {}).get("throughput_ops_sec", 0)
        mem = results.get(f"memory_{size}", {}).get("memory_mb", 0)
        print(f"{size:>10,} | {n_ops:>12,.0f} | {e_ops:>12,.0f} | {l_ops:>12,.0f} | {mem:>10.1f}")

    return results


if __name__ == "__main__":
    run_all_benchmarks()
