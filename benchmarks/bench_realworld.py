"""
Real-World End-to-End Benchmark
================================
Ingests realistic mixed data through the StorageRouter and measures:
- Ingestion throughput (docs/sec, passages/sec, prices/sec)
- RAG query latency (vector search + content resolve + graph expand)
- Price query latency (SQL range query)
- Relationship query latency (graph traversal)
- Memory usage
- Content integrity

Runs at 3 scales: 1K, 10K, 50K passages.
"""
import gc
import hashlib
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "k")

import warnings
warnings.filterwarnings("ignore")

from contextsynapse.storage.csr_graph_storage import CSRGraphStorage
from contextsynapse.storage.router.router import StorageRouter
from contextsynapse.storage.router.resolver import ContentResolver
from contextsynapse.storage.router.duckdb_store import DuckDBStore


SECTORS = ["Technology", "Finance", "Healthcare", "Energy", "Consumer"]
COMPANIES = [f"Company_{i}" for i in range(100)]


def generate_data(n_docs, passages_per_doc=10):
    """Generate realistic mixed data."""
    random.seed(42)
    np.random.seed(42)
    items = []

    # Company entities
    for i, c in enumerate(COMPANIES):
        items.append({"id": f"ent_{c}", "type": "Company",
                       "properties": {"name": c, "sector": SECTORS[i % 5]}})
    # Sector entities
    for s in SECTORS:
        items.append({"id": f"ent_{s}", "type": "Sector", "properties": {"name": s}})
    # Sector edges
    for i, c in enumerate(COMPANIES):
        items.append({"source_id": f"ent_{c}", "target_id": f"ent_{SECTORS[i % 5]}", "edge_type": "IN_SECTOR"})
        items.append({"source_id": f"ent_{c}", "target_id": f"ent_{COMPANIES[(i+1) % len(COMPANIES)]}", "edge_type": "COMPETITOR"})

    # Documents + passages + facts + edges
    for d in range(n_docs):
        company = COMPANIES[d % len(COMPANIES)]
        doc_id = f"doc_{d}"
        items.append({"id": doc_id, "type": "Document",
                       "properties": {"title": f"Report {d}: {company} Q{(d%4)+1}",
                                      "url": f"https://example.com/{d}",
                                      "author": f"Analyst_{d%10}",
                                      "date": (datetime(2026, 1, 1) + timedelta(days=d)).isoformat(),
                                      "source": "web"}})
        items.append({"source_id": doc_id, "target_id": f"ent_{company}", "edge_type": "ABOUT"})

        for p in range(passages_per_doc):
            pid = f"p_{d}_{p}"
            text = (
                f"In Q{(d%4)+1} {company} reported {'strong' if random.random() > 0.3 else 'moderate'} "
                f"performance. Revenue grew {random.randint(5, 25)}% to ${random.randint(100, 999)}M. "
                f"The {SECTORS[d % 5]} sector shows {'robust' if random.random() > 0.5 else 'cautious'} outlook. "
                f"Competition from {COMPANIES[(d+1) % len(COMPANIES)]} intensifies. "
                f"{'Digital transformation' if p % 3 == 0 else 'Cloud migration' if p % 3 == 1 else 'AI adoption'} "
                f"remains a key growth driver. Operating margin was {random.randint(15, 30)}%. "
            ) * 3  # ~180 words
            items.append({"id": pid, "type": "Passage",
                           "properties": {"text": text, "doc_id": doc_id, "position": p, "company": company}})
            items.append({"source_id": pid, "target_id": f"ent_{company}", "edge_type": "MENTIONS"})

            if p % 3 == 0:
                fid = f"f_{d}_{p}"
                items.append({"id": fid, "type": "Fact",
                               "properties": {"statement": f"{company} revenue grew {random.randint(5, 25)}%",
                                               "passage_id": pid, "fact_type": "metric",
                                               "confidence": round(random.uniform(0.7, 1.0), 2)}})
                items.append({"source_id": pid, "target_id": fid, "edge_type": "STATES"})

    # Prices (100 companies x 90 days)
    base_date = datetime(2026, 6, 1)
    for c in COMPANIES:
        bp = random.uniform(100, 5000)
        for day in range(90):
            items.append({"type": "Price",
                           "properties": {"ticker": c, "date": (base_date + timedelta(days=day)).strftime("%Y-%m-%d"),
                                          "close": round(bp * (1 + random.gauss(0, 0.02)), 2),
                                          "volume": random.randint(100000, 10000000)}})

    # Embeddings (stored separately for vector search simulation)
    embeddings = {}
    for item in items:
        if item.get("type") == "Passage":
            h = int(hashlib.md5(item["id"].encode()).hexdigest()[:8], 16)
            rng = np.random.RandomState(h)
            vec = rng.randn(128).astype(np.float32)
            vec /= np.linalg.norm(vec)
            embeddings[item["id"]] = vec

    return items, embeddings


def run_benchmark(scale_label, n_docs, passages_per_doc=10):
    """Run full benchmark at one scale."""
    total_passages = n_docs * passages_per_doc
    total_prices = len(COMPANIES) * 90

    print(f"\n{'=' * 70}")
    print(f"  {scale_label}: {n_docs} docs, {total_passages:,} passages, {total_prices:,} prices")
    print(f"{'=' * 70}")

    items, embeddings = generate_data(n_docs, passages_per_doc)
    print(f"  Generated {len(items):,} items, {len(embeddings):,} vectors")

    tmpdir = tempfile.mkdtemp()
    try:
        gc.collect()
        tracemalloc.start()
        snap1 = tracemalloc.take_snapshot()

        # Setup
        graph = CSRGraphStorage()
        store = DuckDBStore(os.path.join(tmpdir, "content.duckdb"))
        router = StorageRouter(graph=graph)
        router._content_store = store
        resolver = ContentResolver()
        resolver._content_store = store

        # ── INGEST ──
        t0 = time.perf_counter()
        counts = router.ingest(items)
        ingest_time = time.perf_counter() - t0

        snap2 = tracemalloc.take_snapshot()
        tracemalloc.stop()
        mem_diff = sum(s.size_diff for s in snap2.compare_to(snap1, "lineno") if s.size_diff > 0)
        mem_mb = mem_diff / (1024 * 1024)

        stats = store.stats()
        print(f"\n  INGEST ({ingest_time:.2f}s)")
        print(f"    Throughput:  {len(items)/ingest_time:,.0f} items/sec")
        print(f"    Graph:       {graph.get_node_count():,} nodes, {graph.get_edge_count():,} edges")
        print(f"    DuckDB:      {stats}")
        print(f"    Memory:      {mem_mb:.1f} MB")
        print(f"    Price nodes: {len(graph.get_nodes_by_type('Price'))} (should be 0)")

        # ── RAG QUERY ──
        # Simulate: vector search -> resolve text -> expand graph
        np.random.seed(99)
        rag_times = []
        for _ in range(20):
            q_vec = np.random.randn(128).astype(np.float32)
            q_vec /= np.linalg.norm(q_vec)

            t0 = time.perf_counter()

            # Step 1: Vector search (brute force for benchmark)
            scores = {pid: float(np.dot(q_vec, vec)) for pid, vec in embeddings.items()}
            top_ids = sorted(scores, key=scores.get, reverse=True)[:5]

            # Step 2: Resolve content from DuckDB (batch)
            texts = resolver.get_texts_batch(top_ids)

            # Step 3: Graph expansion
            for pid in top_ids:
                neighbors = graph.get_neighbors(pid)

            rag_times.append(time.perf_counter() - t0)

        print(f"\n  RAG QUERY (20 queries)")
        print(f"    Avg:  {statistics.mean(rag_times)*1000:.2f}ms")
        print(f"    P50:  {statistics.median(rag_times)*1000:.2f}ms")
        print(f"    P99:  {sorted(rag_times)[int(len(rag_times)*0.99)]*1000:.2f}ms")

        # ── PRICE QUERY ──
        price_times = []
        for c in COMPANIES[:20]:
            t0 = time.perf_counter()
            prices = store.get_prices(c, days=30)
            price_times.append(time.perf_counter() - t0)

        print(f"\n  PRICE QUERY (20 queries, 30 days each)")
        print(f"    Avg:  {statistics.mean(price_times)*1000:.2f}ms")
        print(f"    Results: {len(prices)} rows per query")

        # ── NEIGHBOR QUERY ──
        neighbor_times = []
        for c in COMPANIES[:50]:
            t0 = time.perf_counter()
            nb = graph.get_neighbors(f"ent_{c}")
            neighbor_times.append(time.perf_counter() - t0)

        print(f"\n  NEIGHBOR QUERY (50 entities)")
        print(f"    Avg:  {statistics.mean(neighbor_times)*1000000:.1f}us")
        print(f"    Results: {len(nb)} neighbors per entity")

        # ── CONTENT INTEGRITY ──
        random.seed(42)
        passage_items = [i for i in items if i.get("type") == "Passage"]
        sample = random.sample(passage_items, min(100, len(passage_items)))
        match = 0
        for item in sample:
            original_text = item["properties"]["text"]
            resolved = resolver.get_text(item["id"])
            if resolved == original_text:
                match += 1

        print(f"\n  CONTENT INTEGRITY")
        print(f"    Checked: {len(sample)} passages")
        print(f"    Match:   {match}/{len(sample)} ({match/len(sample):.0%})")

        return {
            "scale": scale_label,
            "docs": n_docs,
            "passages": total_passages,
            "prices": total_prices,
            "ingest_sec": round(ingest_time, 2),
            "ingest_items_sec": round(len(items) / ingest_time),
            "graph_nodes": graph.get_node_count(),
            "graph_edges": graph.get_edge_count(),
            "memory_mb": round(mem_mb, 1),
            "rag_avg_ms": round(statistics.mean(rag_times) * 1000, 2),
            "rag_p50_ms": round(statistics.median(rag_times) * 1000, 2),
            "price_avg_ms": round(statistics.mean(price_times) * 1000, 2),
            "neighbor_avg_us": round(statistics.mean(neighbor_times) * 1000000, 1),
            "integrity": f"{match}/{len(sample)}",
        }

    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def main():
    print("=" * 70)
    print("  ContextCore Real-World Benchmark")
    print("  Storage: Thin Graph (CSR) + DuckDB (content) + Vector (in-mem)")
    print("=" * 70)

    results = []
    scales = [
        ("Small",  100,  10),
        ("Medium", 1000, 10),
        ("Large",  5000, 10),
    ]

    for label, n_docs, ppd in scales:
        r = run_benchmark(label, n_docs, ppd)
        results.append(r)

    # Summary
    print(f"\n\n{'=' * 70}")
    print("  SUMMARY")
    print(f"{'=' * 70}")
    print(f"{'Scale':<10} | {'Passages':>10} | {'Ingest':>10} | {'Items/s':>10} | {'RAG':>10} | {'Prices':>10} | {'Memory':>8} | {'Nodes':>8} | {'Integrity':>10}")
    print(f"{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*10}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}")
    for r in results:
        print(f"{r['scale']:<10} | {r['passages']:>10,} | {r['ingest_sec']:>8.2f}s | {r['ingest_items_sec']:>10,} | {r['rag_avg_ms']:>8.2f}ms | {r['price_avg_ms']:>8.2f}ms | {r['memory_mb']:>6.1f}MB | {r['graph_nodes']:>8,} | {r['integrity']:>10}")

    # Save
    import json
    snapshot_dir = Path(__file__).parent / "snapshots"
    snapshot_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    out = snapshot_dir / f"realworld_{ts}.json"
    with open(out, "w") as f:
        json.dump(results, f, indent=2)
    print(f"\nSaved: {out}")


if __name__ == "__main__":
    main()
