"""
Architecture PoC Benchmark
==========================
Compares 3 storage patterns with realistic mixed data:

  Pattern A: Fat Graph (everything in graph nodes) -- current baseline
  Pattern B: Thin Graph + TinyDB (doc store) + Vector
  Pattern C: Thin Graph + DuckDB (columnar) + Vector

Measures: ingest speed, query latency, memory, accuracy, scale.
"""
import gc
import hashlib
import json
import os
import random
import shutil
import statistics
import sys
import tempfile
import time
import tracemalloc
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "poc-key")

import numpy as np
from contextsynapse.storage.csr_graph_storage import CSRGraphStorage

# ---------------------------------------------------------------------------
# Test data generator
# ---------------------------------------------------------------------------

SECTORS = ["IT", "Finance", "Healthcare", "Energy", "Consumer"]
COMPANIES = [f"Company_{i}" for i in range(50)]

def _generate_test_data(n_documents: int, passages_per_doc: int = 10):
    """Generate realistic mixed data: documents, passages, entities, facts, prices."""
    random.seed(42)
    np.random.seed(42)

    documents = []
    passages = []
    entities = []
    facts = []
    edges = []
    prices = []

    # Create company entities
    for i, company in enumerate(COMPANIES):
        entities.append({
            "id": f"ent_{company}",
            "type": "Company",
            "name": company,
            "sector": SECTORS[i % len(SECTORS)],
            "description": f"{company} is a leading company in the {SECTORS[i % len(SECTORS)]} sector. " * 5,
        })

    # Create sector entities
    for sector in SECTORS:
        entities.append({"id": f"ent_{sector}", "type": "Sector", "name": sector})

    # Create documents with passages
    for d in range(n_documents):
        company = COMPANIES[d % len(COMPANIES)]
        doc_id = f"doc_{d}"
        documents.append({
            "id": doc_id,
            "type": "Document",
            "title": f"Analysis Report {d}: {company} Q{(d%4)+1} Results",
            "url": f"https://example.com/reports/{d}",
            "author": f"Analyst {d % 10}",
            "date": (datetime(2026, 1, 1) + timedelta(days=d)).isoformat(),
            "source": "web",
        })

        for p in range(passages_per_doc):
            passage_id = f"passage_{d}_{p}"
            text = (
                f"In Q{(d%4)+1} {company} reported strong performance driven by "
                f"{'digital transformation' if p % 3 == 0 else 'cloud migration' if p % 3 == 1 else 'AI integration'}. "
                f"Revenue grew by {random.randint(5, 25)}% year-over-year to reach "
                f"${random.randint(100, 999)}M. Operating margins expanded by {random.randint(1, 5)} "
                f"basis points. The {SECTORS[d % len(SECTORS)]} sector continues to show "
                f"{'robust' if random.random() > 0.5 else 'moderate'} growth prospects. "
                f"Key risks include regulatory changes and market competition from "
                f"{COMPANIES[(d+1) % len(COMPANIES)]} and {COMPANIES[(d+2) % len(COMPANIES)]}. "
            ) * 2  # ~200 words per passage

            passages.append({
                "id": passage_id,
                "doc_id": doc_id,
                "text": text,
                "position": p,
                "company": company,
            })

            # Entity mention edges
            edges.append({"source": passage_id, "target": f"ent_{company}", "type": "MENTIONS"})

            # Fact extraction (1 per 3 passages)
            if p % 3 == 0:
                fact_id = f"fact_{d}_{p}"
                facts.append({
                    "id": fact_id,
                    "passage_id": passage_id,
                    "statement": f"{company} revenue grew {random.randint(5, 25)}% in Q{(d%4)+1}",
                    "fact_type": "metric",
                    "confidence": round(random.uniform(0.7, 1.0), 2),
                })
                edges.append({"source": passage_id, "target": fact_id, "type": "STATES"})

        # Document -> entity edge
        edges.append({"source": doc_id, "target": f"ent_{company}", "type": "ABOUT"})

    # Company -> sector edges
    for i, company in enumerate(COMPANIES):
        edges.append({"source": f"ent_{company}", "target": f"ent_{SECTORS[i % len(SECTORS)]}", "type": "IN_SECTOR"})
        # Competitor edges
        edges.append({"source": f"ent_{company}", "target": f"ent_{COMPANIES[(i+1) % len(COMPANIES)]}", "type": "COMPETITOR"})

    # Price data (50 companies x 90 days)
    base_date = datetime(2026, 6, 1)
    for company in COMPANIES:
        base_price = random.uniform(100, 5000)
        for day in range(90):
            prices.append({
                "ticker": company,
                "date": (base_date + timedelta(days=day)).strftime("%Y-%m-%d"),
                "close": round(base_price * (1 + random.gauss(0, 0.02)), 2),
                "volume": random.randint(100000, 10000000),
            })

    # Simple embeddings (random but deterministic per passage)
    embeddings = {}
    for p in passages:
        h = int(hashlib.md5(p["id"].encode()).hexdigest()[:8], 16)
        rng = np.random.RandomState(h)
        vec = rng.randn(128).astype(np.float32)
        vec /= np.linalg.norm(vec)
        embeddings[p["id"]] = vec

    return {
        "documents": documents,
        "passages": passages,
        "entities": entities,
        "facts": facts,
        "edges": edges,
        "prices": prices,
        "embeddings": embeddings,
    }


# ---------------------------------------------------------------------------
# Pattern A: Fat Graph (everything in graph nodes)
# ---------------------------------------------------------------------------

class PatternA_FatGraph:
    """Current architecture: everything stored as graph nodes."""
    name = "A: Fat Graph"

    def __init__(self, tmpdir: str):
        self.graph = CSRGraphStorage()
        self.vectors = {}  # id -> numpy array
        self.texts = {}    # id -> text (for vector search result)

    def ingest(self, data: dict):
        # Entities
        for e in data["entities"]:
            self.graph.add_node(e["id"], e["type"], e)

        # Documents (full metadata in graph)
        for d in data["documents"]:
            self.graph.add_node(d["id"], d["type"], d)

        # Passages (full text in graph node!)
        for p in data["passages"]:
            self.graph.add_node(p["id"], "Passage", {
                "text": p["text"], "doc_id": p["doc_id"],
                "position": p["position"], "company": p["company"],
            })
            self.texts[p["id"]] = p["text"]

        # Facts (in graph)
        for f in data["facts"]:
            self.graph.add_node(f["id"], "Fact", f)

        # Prices (as graph nodes!)
        for pr in data["prices"]:
            pid = f"price_{pr['ticker']}_{pr['date']}"
            self.graph.add_node(pid, "Price", pr)

        # Edges
        for e in data["edges"]:
            self.graph.add_edge(e["source"], e["target"], e["type"], {})

        # Vectors
        self.vectors = data["embeddings"]

    def query_rag(self, query_vec: np.ndarray, top_k: int = 5) -> List[dict]:
        """Simulate RAG: vector search -> fetch content -> expand graph."""
        # Vector search
        scores = {}
        for pid, vec in self.vectors.items():
            scores[pid] = float(np.dot(query_vec, vec))
        top = sorted(scores, key=scores.get, reverse=True)[:top_k]

        # Fetch content (from graph node properties)
        results = []
        for pid in top:
            node = self.graph.get_node(pid)
            if node:
                text = node.properties.get("text", "")
                # Expand: get related entities
                neighbors = self.graph.get_neighbors(pid)
                entities = [n[0] for n in neighbors]
                results.append({"id": pid, "text": text, "score": scores[pid], "entities": entities})
        return results

    def query_neighbors(self, entity_id: str) -> List[str]:
        """Pure graph traversal."""
        return [n[0] for n in self.graph.get_neighbors(entity_id)]

    def query_prices(self, ticker: str, days: int = 30) -> List[dict]:
        """Get prices — must scan all graph nodes."""
        results = []
        # No index on ticker+date, must scan all Price nodes
        price_nodes = self.graph.get_nodes_by_type("Price")
        for nid in price_nodes:
            node = self.graph.get_node(nid)
            if node and node.properties.get("ticker") == ticker:
                results.append(node.properties)
        results.sort(key=lambda x: x.get("date", ""), reverse=True)
        return results[:days]

    def get_stats(self) -> dict:
        return {"nodes": self.graph.get_node_count(), "edges": self.graph.get_edge_count()}


# ---------------------------------------------------------------------------
# Pattern B: Thin Graph + TinyDB + Vector
# ---------------------------------------------------------------------------

class PatternB_ThinGraph_DocStore:
    """Thin graph for relationships. TinyDB for content. Vector for search."""
    name = "B: Thin Graph + TinyDB"

    def __init__(self, tmpdir: str):
        self.graph = CSRGraphStorage()
        self.vectors = {}
        self.texts = {}

        from tinydb import TinyDB, Query
        self.doc_db = TinyDB(os.path.join(tmpdir, "documents.json"))
        self.passage_db = TinyDB(os.path.join(tmpdir, "passages.json"))
        self.fact_db = TinyDB(os.path.join(tmpdir, "facts.json"))
        self.price_db = TinyDB(os.path.join(tmpdir, "prices.json"))
        self.Q = Query()

    def ingest(self, data: dict):
        # Entities (in graph — they have relationships)
        for e in data["entities"]:
            self.graph.add_node(e["id"], e["type"], {"name": e["name"], "sector": e.get("sector", "")})

        # Documents metadata -> TinyDB
        for d in data["documents"]:
            self.doc_db.insert(d)
            # Thin graph node
            self.graph.add_node(d["id"], "Document", {"title": d["title"], "_ref": f"tinydb:documents:{d['id']}"})

        # Passages -> TinyDB (content) + vectors
        passage_batch = []
        for p in data["passages"]:
            passage_batch.append({"id": p["id"], "doc_id": p["doc_id"], "text": p["text"],
                                  "position": p["position"], "company": p["company"]})
            self.texts[p["id"]] = p["text"]
            # Thin graph node (no text!)
            self.graph.add_node(p["id"], "Passage", {
                "doc_id": p["doc_id"], "position": p["position"],
                "_ref": f"tinydb:passages:{p['id']}",
            })
        # Batch insert to TinyDB
        self.passage_db.insert_multiple(passage_batch)

        # Facts -> TinyDB
        self.fact_db.insert_multiple(data["facts"])
        for f in data["facts"]:
            self.graph.add_node(f["id"], "Fact", {
                "fact_type": f["fact_type"], "_ref": f"tinydb:facts:{f['id']}",
            })

        # Prices -> TinyDB (no graph nodes for prices!)
        self.price_db.insert_multiple(data["prices"])

        # Edges (same as Pattern A)
        for e in data["edges"]:
            self.graph.add_edge(e["source"], e["target"], e["type"], {})

        self.vectors = data["embeddings"]

    def query_rag(self, query_vec: np.ndarray, top_k: int = 5) -> List[dict]:
        # Vector search
        scores = {}
        for pid, vec in self.vectors.items():
            scores[pid] = float(np.dot(query_vec, vec))
        top = sorted(scores, key=scores.get, reverse=True)[:top_k]

        # Fetch content from TinyDB (not graph)
        results = []
        for pid in top:
            docs = self.passage_db.search(self.Q.id == pid)
            text = docs[0]["text"] if docs else ""
            neighbors = self.graph.get_neighbors(pid)
            entities = [n[0] for n in neighbors]
            results.append({"id": pid, "text": text, "score": scores[pid], "entities": entities})
        return results

    def query_neighbors(self, entity_id: str) -> List[str]:
        return [n[0] for n in self.graph.get_neighbors(entity_id)]

    def query_prices(self, ticker: str, days: int = 30) -> List[dict]:
        results = self.price_db.search(self.Q.ticker == ticker)
        results.sort(key=lambda x: x.get("date", ""), reverse=True)
        return results[:days]

    def get_stats(self) -> dict:
        return {"nodes": self.graph.get_node_count(), "edges": self.graph.get_edge_count(),
                "passages_in_db": len(self.passage_db), "prices_in_db": len(self.price_db)}


# ---------------------------------------------------------------------------
# Pattern C: Thin Graph + DuckDB + Vector
# ---------------------------------------------------------------------------

class PatternC_ThinGraph_Columnar:
    """Thin graph for relationships. DuckDB for all structured data. Vector for search."""
    name = "C: Thin Graph + DuckDB"

    def __init__(self, tmpdir: str):
        self.graph = CSRGraphStorage()
        self.vectors = {}
        self.texts = {}

        import duckdb
        self.db = duckdb.connect(os.path.join(tmpdir, "data.duckdb"))
        self.db.execute("""
            CREATE TABLE documents (id TEXT PRIMARY KEY, type TEXT, title TEXT,
                url TEXT, author TEXT, date TEXT, source TEXT);
            CREATE TABLE passages (id TEXT PRIMARY KEY, doc_id TEXT, text TEXT,
                position INTEGER, company TEXT);
            CREATE TABLE facts (id TEXT PRIMARY KEY, passage_id TEXT, statement TEXT,
                fact_type TEXT, confidence REAL);
            CREATE TABLE prices (ticker TEXT, date TEXT, close REAL, volume BIGINT,
                PRIMARY KEY (ticker, date));
            CREATE INDEX idx_prices_ticker ON prices(ticker);
            CREATE INDEX idx_passages_doc ON passages(doc_id);
        """)

    def ingest(self, data: dict):
        # Entities (in graph)
        for e in data["entities"]:
            self.graph.add_node(e["id"], e["type"], {"name": e["name"], "sector": e.get("sector", "")})

        # Documents -> DuckDB
        if data["documents"]:
            self.db.executemany(
                "INSERT INTO documents VALUES (?, ?, ?, ?, ?, ?, ?)",
                [(d["id"], d["type"], d["title"], d["url"], d["author"], d["date"], d["source"])
                 for d in data["documents"]]
            )
        for d in data["documents"]:
            self.graph.add_node(d["id"], "Document", {"title": d["title"], "_ref": f"duckdb:documents:{d['id']}"})

        # Passages -> DuckDB
        if data["passages"]:
            self.db.executemany(
                "INSERT INTO passages VALUES (?, ?, ?, ?, ?)",
                [(p["id"], p["doc_id"], p["text"], p["position"], p["company"])
                 for p in data["passages"]]
            )
            for p in data["passages"]:
                self.texts[p["id"]] = p["text"]
                self.graph.add_node(p["id"], "Passage", {
                    "doc_id": p["doc_id"], "position": p["position"],
                    "_ref": f"duckdb:passages:{p['id']}",
                })

        # Facts -> DuckDB
        if data["facts"]:
            self.db.executemany(
                "INSERT INTO facts VALUES (?, ?, ?, ?, ?)",
                [(f["id"], f["passage_id"], f["statement"], f["fact_type"], f["confidence"])
                 for f in data["facts"]]
            )
            for f in data["facts"]:
                self.graph.add_node(f["id"], "Fact", {"fact_type": f["fact_type"], "_ref": f"duckdb:facts:{f['id']}"})

        # Prices -> DuckDB (no graph nodes!)
        if data["prices"]:
            self.db.executemany(
                "INSERT INTO prices VALUES (?, ?, ?, ?)",
                [(p["ticker"], p["date"], p["close"], p["volume"]) for p in data["prices"]]
            )

        # Edges
        for e in data["edges"]:
            self.graph.add_edge(e["source"], e["target"], e["type"], {})

        self.vectors = data["embeddings"]

    def query_rag(self, query_vec: np.ndarray, top_k: int = 5) -> List[dict]:
        scores = {}
        for pid, vec in self.vectors.items():
            scores[pid] = float(np.dot(query_vec, vec))
        top = sorted(scores, key=scores.get, reverse=True)[:top_k]

        results = []
        for pid in top:
            row = self.db.execute("SELECT text FROM passages WHERE id = ?", [pid]).fetchone()
            text = row[0] if row else ""
            neighbors = self.graph.get_neighbors(pid)
            entities = [n[0] for n in neighbors]
            results.append({"id": pid, "text": text, "score": scores[pid], "entities": entities})
        return results

    def query_neighbors(self, entity_id: str) -> List[str]:
        return [n[0] for n in self.graph.get_neighbors(entity_id)]

    def query_prices(self, ticker: str, days: int = 30) -> List[dict]:
        rows = self.db.execute(
            "SELECT ticker, date, close, volume FROM prices WHERE ticker = ? ORDER BY date DESC LIMIT ?",
            [ticker, days]
        ).fetchall()
        return [{"ticker": r[0], "date": r[1], "close": r[2], "volume": r[3]} for r in rows]

    def get_stats(self) -> dict:
        p_count = self.db.execute("SELECT COUNT(*) FROM passages").fetchone()[0]
        pr_count = self.db.execute("SELECT COUNT(*) FROM prices").fetchone()[0]
        return {"nodes": self.graph.get_node_count(), "edges": self.graph.get_edge_count(),
                "passages_in_db": p_count, "prices_in_db": pr_count}


# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def run_benchmark(pattern_class, data: dict, tmpdir: str, scale_label: str) -> dict:
    """Run all benchmarks for one pattern at one scale."""
    gc.collect()

    # -- Ingest --
    tracemalloc.start()
    snap1 = tracemalloc.take_snapshot()
    t0 = time.perf_counter()
    pattern = pattern_class(tmpdir)
    pattern.ingest(data)
    ingest_time = time.perf_counter() - t0
    snap2 = tracemalloc.take_snapshot()
    tracemalloc.stop()
    mem_diff = sum(s.size_diff for s in snap2.compare_to(snap1, "lineno") if s.size_diff > 0)
    mem_mb = mem_diff / (1024 * 1024)

    stats = pattern.get_stats()

    # -- RAG query (10 queries, average) --
    np.random.seed(99)
    rag_times = []
    rag_result_counts = []
    for _ in range(10):
        q_vec = np.random.randn(128).astype(np.float32)
        q_vec /= np.linalg.norm(q_vec)
        t0 = time.perf_counter()
        results = pattern.query_rag(q_vec, top_k=5)
        rag_times.append(time.perf_counter() - t0)
        rag_result_counts.append(len(results))

    # -- Neighbor query (50 queries) --
    neighbor_times = []
    neighbor_counts = []
    test_entities = [f"ent_{c}" for c in COMPANIES[:50]]
    for eid in test_entities:
        t0 = time.perf_counter()
        neighbors = pattern.query_neighbors(eid)
        neighbor_times.append(time.perf_counter() - t0)
        neighbor_counts.append(len(neighbors))

    # -- Price query (10 queries) --
    price_times = []
    price_counts = []
    for company in COMPANIES[:10]:
        t0 = time.perf_counter()
        prices = pattern.query_prices(company, days=30)
        price_times.append(time.perf_counter() - t0)
        price_counts.append(len(prices))

    # -- Accuracy: verify RAG returns text --
    np.random.seed(42)
    q_vec = np.random.randn(128).astype(np.float32)
    q_vec /= np.linalg.norm(q_vec)
    accuracy_results = pattern.query_rag(q_vec, top_k=5)
    has_text = all(len(r.get("text", "")) > 50 for r in accuracy_results)
    has_entities = all(len(r.get("entities", [])) > 0 for r in accuracy_results)

    return {
        "pattern": pattern.name,
        "scale": scale_label,
        "ingest_sec": round(ingest_time, 3),
        "memory_mb": round(mem_mb, 1),
        "graph_nodes": stats.get("nodes", 0),
        "graph_edges": stats.get("edges", 0),
        "rag_avg_ms": round(statistics.mean(rag_times) * 1000, 2),
        "rag_p99_ms": round(sorted(rag_times)[int(len(rag_times) * 0.9)] * 1000, 2),
        "neighbor_avg_us": round(statistics.mean(neighbor_times) * 1_000_000, 1),
        "neighbor_avg_count": round(statistics.mean(neighbor_counts), 1),
        "price_avg_ms": round(statistics.mean(price_times) * 1000, 2),
        "price_avg_count": round(statistics.mean(price_counts), 1),
        "accuracy_has_text": has_text,
        "accuracy_has_entities": has_entities,
    }


def main():
    print("=" * 80)
    print("ContextCore Architecture PoC Benchmark")
    print("=" * 80)
    print()
    print("Patterns:")
    print("  A: Fat Graph       -- everything in graph nodes (current)")
    print("  B: Thin Graph      -- graph + TinyDB (document store) + vector")
    print("  C: Thin Graph      -- graph + DuckDB (columnar) + vector")
    print()

    scales = [
        ("Small (100 docs)",   100),
        ("Medium (1K docs)",   1000),
        ("Large (5K docs)",    5000),
    ]

    all_results = []

    for label, n_docs in scales:
        print(f"\n{'=' * 80}")
        print(f"Scale: {label} ({n_docs} docs x 10 passages = {n_docs * 10:,} passages + {n_docs * 10 // 3:,} facts + 4,500 prices)")
        print(f"{'=' * 80}")

        data = _generate_test_data(n_docs, passages_per_doc=10)
        print(f"  Generated: {len(data['passages']):,} passages, {len(data['entities'])} entities, "
              f"{len(data['facts']):,} facts, {len(data['prices']):,} prices, {len(data['edges']):,} edges")

        patterns = [PatternA_FatGraph, PatternB_ThinGraph_DocStore, PatternC_ThinGraph_Columnar]

        for pcls in patterns:
            with tempfile.TemporaryDirectory() as tmpdir:
                r = run_benchmark(pcls, data, tmpdir, label)
                all_results.append(r)
                print(f"\n  {r['pattern']}")
                print(f"    Ingest:      {r['ingest_sec']:.3f}s")
                print(f"    Memory:      {r['memory_mb']:.1f} MB")
                print(f"    Graph nodes: {r['graph_nodes']:,}")
                print(f"    RAG query:   {r['rag_avg_ms']:.2f}ms avg, {r['rag_p99_ms']:.2f}ms p99")
                print(f"    Neighbors:   {r['neighbor_avg_us']:.1f}us avg ({r['neighbor_avg_count']:.0f} results)")
                print(f"    Prices:      {r['price_avg_ms']:.2f}ms avg ({r['price_avg_count']:.0f} results)")
                print(f"    Accuracy:    text={'PASS' if r['accuracy_has_text'] else 'FAIL'} "
                      f"entities={'PASS' if r['accuracy_has_entities'] else 'FAIL'}")

    # -- Summary tables --
    print(f"\n\n{'=' * 80}")
    print("COMPARISON SUMMARY")
    print(f"{'=' * 80}")

    # Group by scale
    for label, _ in scales:
        print(f"\n--- {label} ---")
        scale_results = [r for r in all_results if r["scale"] == label]
        print(f"{'Pattern':<28} | {'Ingest':>8} | {'Memory':>8} | {'Nodes':>8} | {'RAG':>8} | {'Neighbor':>10} | {'Prices':>8} | {'Accuracy':>8}")
        print(f"{'-'*28}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*8}-+-{'-'*10}-+-{'-'*8}-+-{'-'*8}")
        for r in scale_results:
            acc = "PASS" if r["accuracy_has_text"] and r["accuracy_has_entities"] else "FAIL"
            print(f"{r['pattern']:<28} | {r['ingest_sec']:>6.3f}s | {r['memory_mb']:>6.1f}MB | {r['graph_nodes']:>8,} | {r['rag_avg_ms']:>6.2f}ms | {r['neighbor_avg_us']:>8.1f}us | {r['price_avg_ms']:>6.2f}ms | {acc:>8}")

    # -- Winner analysis --
    print(f"\n\n{'=' * 80}")
    print("WINNER BY METRIC (at largest scale)")
    print(f"{'=' * 80}")
    large = [r for r in all_results if "Large" in r["scale"]]
    if large:
        metrics = [
            ("Fastest ingest", "ingest_sec", False),
            ("Lowest memory", "memory_mb", False),
            ("Fewest graph nodes", "graph_nodes", False),
            ("Fastest RAG query", "rag_avg_ms", False),
            ("Fastest neighbor query", "neighbor_avg_us", False),
            ("Fastest price query", "price_avg_ms", False),
        ]
        for label, key, higher_better in metrics:
            if higher_better:
                winner = max(large, key=lambda r: r[key])
            else:
                winner = min(large, key=lambda r: r[key])
            print(f"  {label:<25} -> {winner['pattern']:<28} ({winner[key]})")

    # Save results
    snapshot_dir = Path(__file__).parent / "snapshots"
    snapshot_dir.mkdir(exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
    out_file = snapshot_dir / f"poc_architecture_{ts}.json"
    with open(out_file, "w") as f:
        json.dump(all_results, f, indent=2)
    print(f"\nResults saved: {out_file}")


if __name__ == "__main__":
    main()
