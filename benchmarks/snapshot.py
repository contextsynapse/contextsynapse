"""Benchmark snapshot system — saves timestamped results for comparison.

Usage:
    python benchmarks/snapshot.py              # Run benchmarks + save snapshot
    python benchmarks/snapshot.py --compare    # Compare latest with previous
    python benchmarks/snapshot.py --list       # List all snapshots
"""
import json
import os
import sys
import time
import random
import tracemalloc
import platform
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "bench-key")

SNAPSHOT_DIR = Path(__file__).parent / "snapshots"


def _run_storage_benchmarks():
    """Run core storage benchmarks and return results dict."""
    import warnings
    warnings.filterwarnings("ignore")
    from contextsynapse.storage.csr_graph_storage import CSRGraphStorage

    results = {}
    for size in [1000, 10000, 50000]:
        s = CSRGraphStorage()

        # Node insertion
        t0 = time.perf_counter()
        for i in range(size):
            s.add_node(f"n_{i}", "B", {"name": f"N{i}", "cat": f"c_{i%10}"})
        t_nodes = time.perf_counter() - t0

        # Edge insertion
        random.seed(42)
        ec = 0
        t0 = time.perf_counter()
        for i in range(size * 3):
            a, b = f"n_{random.randint(0, size-1)}", f"n_{random.randint(0, size-1)}"
            if a != b:
                s.add_edge(a, b, "R", {})
                ec += 1
        t_edges = time.perf_counter() - t0

        # Node lookup
        random.seed(123)
        lookups = min(10000, size)
        t0 = time.perf_counter()
        for i in range(lookups):
            s.get_node(f"n_{random.randint(0, size-1)}")
        t_lookup = time.perf_counter() - t0

        # Neighbor traversal
        random.seed(456)
        trav_count = min(5000, size)
        total_n = 0
        t0 = time.perf_counter()
        for i in range(trav_count):
            nb = s.get_neighbors(f"n_{random.randint(0, size-1)}")
            total_n += len(nb)
        t_trav = time.perf_counter() - t0

        # Property filter
        t0 = time.perf_counter()
        found = 0
        for c in range(10):
            r = s.get_nodes_by_property("cat", f"c_{c}")
            found += len(r)
        t_prop = time.perf_counter() - t0

        results[f"{size}"] = {
            "node_insert_ops_sec": round(size / t_nodes),
            "edge_insert_ops_sec": round(ec / t_edges),
            "node_lookup_ops_sec": round(lookups / t_lookup),
            "neighbor_trav_ops_sec": round(trav_count / t_trav),
            "prop_filter_sec": round(t_prop, 6),
            "prop_filter_found": found,
            "edge_count": ec,
        }

    # Memory benchmarks
    for size in [10000, 50000]:
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()
        s = CSRGraphStorage()
        for i in range(size):
            s.add_node(f"m_{i}", "M", {"n": f"N{i}", "v": i})
        random.seed(42)
        for i in range(size * 2):
            a, b = f"m_{random.randint(0, size-1)}", f"m_{random.randint(0, size-1)}"
            if a != b:
                s.add_edge(a, b, "L", {})
        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()
        diff = sum(x.size_diff for x in snap2.compare_to(snap1, "lineno") if x.size_diff > 0)
        results[f"{size}"]["memory_mb"] = round(diff / (1024 * 1024), 1)
        results[f"{size}"]["bytes_per_node"] = diff // size

    return results


def _run_accuracy_benchmarks():
    """Run accuracy benchmarks and return results dict."""
    import warnings
    warnings.filterwarnings("ignore")
    from contextsynapse.storage.csr_graph_storage import CSRGraphStorage

    results = {}

    # Data integrity
    s = CSRGraphStorage()
    for i in range(5000):
        s.add_node(f"a_{i}", "T", {"name": f"N{i}", "val": i * 3.14, "flag": i % 2 == 0})
    missing = mismatch = 0
    for i in range(5000):
        n = s.get_node(f"a_{i}")
        if n is None:
            missing += 1
            continue
        if n.properties.get("val") != i * 3.14:
            mismatch += 1
        if n.properties.get("flag") != (i % 2 == 0):
            mismatch += 1
    results["data_integrity"] = {
        "nodes": 5000,
        "missing": missing,
        "mismatches": mismatch,
        "score": round(1.0 - (missing + mismatch) / 10000, 4),
    }

    # Dedup
    s2 = CSRGraphStorage()
    for i in range(50):
        s2.add_node("same", "T", {"v": i})
    n = s2.get_node("same")
    count = sum(1 for nid in s2.nodes if nid == "same")
    results["dedup"] = {
        "insertions": 50,
        "final_count": count,
        "pass": count == 1,
    }

    # Edge integrity
    random.seed(42)
    s3 = CSRGraphStorage()
    for i in range(2000):
        s3.add_node(f"e_{i}", "N", {"idx": i})
    expected = []
    for i in range(4000):
        a, b = f"e_{random.randint(0, 1999)}", f"e_{random.randint(0, 1999)}"
        if a != b:
            s3.add_edge(a, b, "R", {})
            expected.append((a, b))
    found = miss = 0
    checked = set()
    for src, tgt in expected[:500]:
        if src in checked:
            continue
        checked.add(src)
        nb = s3.get_neighbors(src)
        nb_ids = {t for t, _ in nb}
        exp_tgts = {t for s, t in expected if s == src}
        found += len(nb_ids & exp_tgts)
        miss += len(exp_tgts - nb_ids)
    results["edge_integrity"] = {
        "edges_created": len(expected),
        "edges_found": found,
        "edges_missing": miss,
        "score": round(found / (found + miss) if found + miss > 0 else 1.0, 4),
    }

    return results


def create_snapshot():
    """Run all benchmarks and save a timestamped snapshot."""
    now = datetime.now(timezone.utc)
    timestamp = now.strftime("%Y-%m-%d_%H-%M-%S")

    print(f"Running benchmarks at {now.isoformat()}")
    print("=" * 60)

    print("\n[1/2] Storage benchmarks...")
    storage = _run_storage_benchmarks()

    print("[2/2] Accuracy benchmarks...")
    accuracy = _run_accuracy_benchmarks()

    snapshot = {
        "timestamp": now.isoformat(),
        "date": now.strftime("%Y-%m-%d %H:%M:%S UTC"),
        "platform": {
            "python": platform.python_version(),
            "os": platform.system(),
            "machine": platform.machine(),
            "processor": platform.processor() or "unknown",
        },
        "storage": storage,
        "accuracy": accuracy,
    }

    # Save snapshot
    SNAPSHOT_DIR.mkdir(parents=True, exist_ok=True)
    snapshot_file = SNAPSHOT_DIR / f"bench_{timestamp}.json"
    with open(snapshot_file, "w") as f:
        json.dump(snapshot, f, indent=2)

    # Print results
    print(f"\nSTORAGE PERFORMANCE")
    print(f"{'Size':>8} | {'Node Ins':>12} | {'Edge Ins':>12} | {'Lookup':>12} | {'Traverse':>12} | {'Memory':>8}")
    print(f"{'-'*8}-+-{'-'*12}-+-{'-'*12}-+-{'-'*12}-+-{'-'*12}-+-{'-'*8}")
    for size_key in ["1000", "10000", "50000"]:
        d = storage[size_key]
        mem = f"{d.get('memory_mb', '?')} MB" if 'memory_mb' in d else "—"
        print(f"{size_key:>8} | {d['node_insert_ops_sec']:>10,}/s | {d['edge_insert_ops_sec']:>10,}/s | {d['node_lookup_ops_sec']:>10,}/s | {d['neighbor_trav_ops_sec']:>10,}/s | {mem:>8}")

    print(f"\nACCURACY")
    for test, d in accuracy.items():
        score = d.get("score", "PASS" if d.get("pass") else "FAIL")
        print(f"  {test}: {score}")

    print(f"\nSnapshot saved: {snapshot_file}")
    return snapshot_file


def list_snapshots():
    """List all saved snapshots."""
    if not SNAPSHOT_DIR.exists():
        print("No snapshots found.")
        return []

    files = sorted(SNAPSHOT_DIR.glob("bench_*.json"))
    if not files:
        print("No snapshots found.")
        return []

    print(f"{'#':>3} | {'Date':>22} | {'Python':>8} | {'File'}")
    print(f"{'-'*3}-+-{'-'*22}-+-{'-'*8}-+-{'-'*40}")
    for i, f in enumerate(files, 1):
        with open(f) as fh:
            data = json.load(fh)
        date = data.get("date", "?")
        py = data.get("platform", {}).get("python", "?")
        print(f"{i:>3} | {date:>22} | {py:>8} | {f.name}")

    return files


def compare_snapshots(file1=None, file2=None):
    """Compare two snapshots. Defaults to latest vs previous."""
    files = sorted(SNAPSHOT_DIR.glob("bench_*.json")) if SNAPSHOT_DIR.exists() else []
    if len(files) < 2:
        print("Need at least 2 snapshots to compare. Run benchmarks first.")
        return

    f1 = Path(file1) if file1 else files[-2]  # previous
    f2 = Path(file2) if file2 else files[-1]   # latest

    with open(f1) as fh:
        old = json.load(fh)
    with open(f2) as fh:
        new = json.load(fh)

    print(f"Comparing:")
    print(f"  OLD: {old['date']} ({f1.name})")
    print(f"  NEW: {new['date']} ({f2.name})")
    print()

    # Storage comparison
    print("STORAGE PERFORMANCE DELTA")
    print(f"{'Size':>8} | {'Metric':>16} | {'Old':>12} | {'New':>12} | {'Delta':>8}")
    print(f"{'-'*8}-+-{'-'*16}-+-{'-'*12}-+-{'-'*12}-+-{'-'*8}")

    metrics = [
        ("node_insert_ops_sec", "Node Insert/s"),
        ("edge_insert_ops_sec", "Edge Insert/s"),
        ("node_lookup_ops_sec", "Lookup/s"),
        ("neighbor_trav_ops_sec", "Traverse/s"),
    ]

    for size_key in ["1000", "10000", "50000"]:
        old_d = old.get("storage", {}).get(size_key, {})
        new_d = new.get("storage", {}).get(size_key, {})
        for key, label in metrics:
            o = old_d.get(key, 0)
            n = new_d.get(key, 0)
            if o > 0:
                delta_pct = ((n - o) / o) * 100
                sign = "+" if delta_pct >= 0 else ""
                color = sign  # positive = good for throughput
                print(f"{size_key:>8} | {label:>16} | {o:>10,} | {n:>10,} | {sign}{delta_pct:.1f}%")
            else:
                print(f"{size_key:>8} | {label:>16} | {'—':>12} | {n:>10,} | {'new':>8}")

    # Memory comparison
    print(f"\nMEMORY DELTA")
    for size_key in ["10000", "50000"]:
        old_mem = old.get("storage", {}).get(size_key, {}).get("bytes_per_node", 0)
        new_mem = new.get("storage", {}).get(size_key, {}).get("bytes_per_node", 0)
        if old_mem > 0:
            delta = new_mem - old_mem
            sign = "+" if delta >= 0 else ""
            print(f"  {size_key} nodes: {old_mem} -> {new_mem} bytes/node ({sign}{delta})")

    # Accuracy comparison
    print(f"\nACCURACY DELTA")
    for test in ["data_integrity", "edge_integrity"]:
        old_score = old.get("accuracy", {}).get(test, {}).get("score", 0)
        new_score = new.get("accuracy", {}).get(test, {}).get("score", 0)
        status = "SAME" if old_score == new_score else ("IMPROVED" if new_score > old_score else "REGRESSED")
        print(f"  {test}: {old_score} -> {new_score} [{status}]")


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--list" in args:
        list_snapshots()
    elif "--compare" in args:
        compare_snapshots()
    else:
        create_snapshot()
