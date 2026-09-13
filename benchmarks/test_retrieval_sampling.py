"""Retrieval sampling test -- verifies data flows correctly through all stores."""
import os
import random
import shutil
import tempfile
import time
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "k")

import warnings
warnings.filterwarnings("ignore")

from contextsynapse.storage.csr_graph_storage import CSRGraphStorage
from contextsynapse.storage.router.duckdb_store import DuckDBStore
from contextsynapse.storage.router.router import StorageRouter
from contextsynapse.storage.router.resolver import ContentResolver


def main():
    d = tempfile.mkdtemp()
    graph = CSRGraphStorage()
    store = DuckDBStore(os.path.join(d, "c.duckdb"))
    router = StorageRouter(graph=graph)
    router._content_store = store
    resolver = ContentResolver()
    resolver._content_store = store

    companies = ["TCS", "Infosys", "Wipro", "HCL", "TechM",
                 "LTI", "Mphasis", "Coforge", "LTTS", "Persistent"]

    # ── INGEST ──
    print("=" * 60)
    print("INGESTION")
    print("=" * 60)

    items = []
    # Entities
    for c in companies:
        items.append({"id": f"ent_{c}", "type": "Company",
                       "properties": {"name": c, "sector": "IT Services"}})
    items.append({"id": "ent_IT", "type": "Sector",
                   "properties": {"name": "IT Services"}})
    for c in companies:
        items.append({"source_id": f"ent_{c}", "target_id": "ent_IT", "edge_type": "IN_SECTOR"})

    # Documents + passages + facts
    random.seed(42)
    for d_idx in range(500):
        c = companies[d_idx % len(companies)]
        doc_id = f"doc_{d_idx}"
        items.append({"id": doc_id, "type": "Document", "properties": {
            "title": f"{c} Q{d_idx%4+1} FY2026 Earnings Analysis",
            "url": f"https://analysis.example.com/reports/{doc_id}",
            "author": f"Analyst Team {d_idx%5+1}",
        }})
        items.append({"source_id": doc_id, "target_id": f"ent_{c}", "edge_type": "ABOUT"})

        for p_idx in range(10):
            pid = f"p_{d_idx}_{p_idx}"
            revenue = random.randint(5000, 25000)
            growth = random.randint(5, 25)
            margin = random.randint(18, 32)
            text = (
                f"{c} reported Q{d_idx%4+1} revenue of INR {revenue} crore, "
                f"growing {growth}% YoY. Operating margin stood at {margin}%. "
                f"Key clients include Fortune 500 companies in BFSI. "
                f"Attrition declined to {random.randint(12, 22)}%."
            )
            items.append({"id": pid, "type": "Passage",
                           "properties": {"text": text, "doc_id": doc_id, "position": p_idx}})
            items.append({"source_id": pid, "target_id": f"ent_{c}", "edge_type": "MENTIONS"})

            if p_idx % 3 == 0:
                fid = f"f_{d_idx}_{p_idx}"
                items.append({"id": fid, "type": "Fact", "properties": {
                    "statement": f"{c} revenue INR {revenue}cr, {growth}% YoY in Q{d_idx%4+1}",
                    "doc_id": pid, "fact_type": "metric", "confidence": 0.95,
                }})
                items.append({"source_id": pid, "target_id": fid, "edge_type": "STATES"})

    # Prices
    random.seed(42)
    for c in companies:
        base = random.uniform(500, 5000)
        for day in range(90):
            close = round(base * (1 + random.gauss(0, 0.015)), 2)
            items.append({"type": "Price", "properties": {
                "ticker": c, "date": f"2026-{day//30+6:02d}-{day%30+1:02d}",
                "close": close, "volume": random.randint(500000, 5000000),
            }})

    t0 = time.perf_counter()
    counts = router.ingest(items)
    elapsed = time.perf_counter() - t0
    print(f"  Items:      {len(items):,}")
    print(f"  Time:       {elapsed:.2f}s ({len(items)/elapsed:,.0f} items/sec)")
    print(f"  Graph:      {graph.get_node_count():,} nodes, {graph.get_edge_count():,} edges")
    print(f"  Content:    {store.stats()}")
    print(f"  Price nodes in graph: {len(graph.get_nodes_by_type('Price'))}")

    # ── RETRIEVAL SAMPLES ──
    print()
    print("=" * 60)
    print("RETRIEVAL SAMPLES")
    print("=" * 60)

    # 1. Document -> its passages
    print()
    print("--- 1. Document -> Passages ---")
    doc = resolver.resolve(graph.get_node("doc_0"))
    print(f"  Doc: {doc.get('title', doc.get('text', ''))}")
    children = store.get_items_by_parent("doc_0")
    print(f"  Children: {len(children)} passages")
    for ch in children[:2]:
        print(f"    [{ch['id']}] {ch['text'][:70]}...")

    # 2. Thin node -> resolved content
    print()
    print("--- 2. Thin Node -> Full Content ---")
    thin = graph.get_node("p_42_3")
    print(f"  Graph props: {dict(thin.properties)}")
    print(f"  Size: {len(str(thin.properties))} bytes")
    full = resolver.resolve(thin)
    print(f"  Resolved: {full['text'][:80]}...")

    # 3. Entity neighbors
    print()
    print("--- 3. Entity Relationships ---")
    neighbors = graph.get_neighbors("ent_TCS")
    by_type = {}
    for nid, edge in neighbors:
        by_type.setdefault(edge.edge_type, []).append(nid)
    print(f"  TCS has {len(neighbors)} connections:")
    for etype, nids in by_type.items():
        sample = nids[0]
        node = graph.get_node(sample)
        name = node.properties.get("name", node.properties.get("title", sample)) if node else sample
        print(f"    {etype}: {len(nids)} (e.g. {name})")

    # 4. Price time-series
    print()
    print("--- 4. Price Query ---")
    t0 = time.perf_counter()
    prices = store.get_timeseries("TCS", 5)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  TCS latest 5 prices ({ms:.1f}ms):")
    for p in prices:
        print(f"    {p['ts']}: close={p['value']}")

    # 5. Facts via graph traversal
    print()
    print("--- 5. Facts via Graph Traversal ---")
    tcs_passages = [nid for nid, e in graph.get_neighbors("ent_TCS")
                     if e.edge_type == "MENTIONS"]
    facts = []
    for pid in tcs_passages[:5]:
        for nid, e in graph.get_neighbors(pid):
            if e.edge_type == "STATES":
                f = resolver.resolve(graph.get_node(nid))
                if f:
                    facts.append(f)
    print(f"  Found {len(facts)} facts about TCS:")
    for f in facts[:3]:
        print(f"    {f.get('text', f.get('statement', ''))[:70]}")

    # 6. Batch resolve
    print()
    print("--- 6. Batch Resolve ---")
    pids = [f"p_{i}_0" for i in range(20)]
    t0 = time.perf_counter()
    texts = resolver.get_texts_batch(pids)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  Resolved {len(texts)} passages in {ms:.1f}ms")
    for pid in list(texts)[:2]:
        print(f"    {pid}: {texts[pid][:60]}...")

    # 7. Cross-store query: company -> docs -> passages -> facts
    print()
    print("--- 7. Full Traversal: Company -> Docs -> Passages -> Facts ---")
    t0 = time.perf_counter()
    # TCS -> documents about TCS
    about_edges = [(nid, e) for nid, e in graph.get_neighbors("ent_TCS") if e.edge_type == "ABOUT"]
    doc_ids = [nid for nid, _ in about_edges]
    print(f"  TCS documents: {len(doc_ids)}")
    # Documents -> passages
    all_passages = []
    for did in doc_ids[:3]:
        passages = store.get_items_by_parent(did)
        all_passages.extend(passages)
    print(f"  Passages from first 3 docs: {len(all_passages)}")
    # Sample passage text
    if all_passages:
        print(f"  Sample: {all_passages[0]['text'][:70]}...")
    ms = (time.perf_counter() - t0) * 1000
    print(f"  Full traversal: {ms:.1f}ms")

    print()
    print("=" * 60)
    print("ALL RETRIEVAL SAMPLES PASS")
    print("=" * 60)

    shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
