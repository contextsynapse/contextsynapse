"""
Detailed RAG Query Benchmark
=============================
Breaks down the full RAG pipeline into individual steps and measures each:

  Step 1: Vector search (find relevant passages)
  Step 2: Content resolve (fetch text from DuckDB)
  Step 3: Graph expand - outgoing (passage -> entities, facts)
  Step 4: Graph expand - incoming (entity <- other passages, docs)
  Step 5: Entity context (prices, details from DuckDB)
  Step 6: Context assembly (build LLM-ready output)

Shows actual data at each step for transparency.
"""
import hashlib
import os
import random
import shutil
import statistics
import sys
import tempfile
import time

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("AICONTEXTDB_ADMIN_KEY", "k")

import warnings
warnings.filterwarnings("ignore")

from contextsynapse.storage.csr_graph_storage import CSRGraphStorage
from contextsynapse.storage.router.duckdb_store import DuckDBStore
from contextsynapse.storage.router.router import StorageRouter
from contextsynapse.storage.router.resolver import ContentResolver


def build_test_data(n_docs=500, ppd=10):
    """Build realistic IT sector dataset."""
    random.seed(42)
    np.random.seed(42)
    companies = ["TCS", "Infosys", "Wipro", "HCL", "TechM",
                 "LTI", "Mphasis", "Coforge", "LTTS", "Persistent"]
    items = []

    # Entities
    for c in companies:
        items.append({"id": f"ent_{c}", "type": "Company",
                       "properties": {"name": c, "sector": "IT"}})
    items.append({"id": "ent_IT", "type": "Sector", "properties": {"name": "IT Services"}})
    for i, c in enumerate(companies):
        items.append({"source_id": f"ent_{c}", "target_id": "ent_IT", "edge_type": "IN_SECTOR"})
        items.append({"source_id": f"ent_{c}", "target_id": f"ent_{companies[(i+1)%len(companies)]}", "edge_type": "COMPETITOR"})

    # Documents + passages + facts
    embeddings = {}
    for d in range(n_docs):
        c = companies[d % len(companies)]
        did = f"doc_{d}"
        items.append({"id": did, "type": "Document",
                       "properties": {"title": f"{c} Q{d%4+1} FY26 Report",
                                      "author": f"Analyst_{d%5}", "date": f"2026-0{d%9+1}-01"}})
        items.append({"source_id": did, "target_id": f"ent_{c}", "edge_type": "ABOUT"})

        for p in range(ppd):
            pid = f"p_{d}_{p}"
            rev = random.randint(5000, 25000)
            growth = random.randint(5, 25)
            margin = random.randint(18, 32)
            topic = ["digital transformation", "cloud migration", "AI services",
                     "cost optimization", "talent acquisition"][p % 5]
            text = (
                f"{c} reported Q{d%4+1} revenue of INR {rev} crore, growing {growth}% YoY. "
                f"Operating margin at {margin}%, driven by {topic}. "
                f"Management expects continued momentum in BFSI and manufacturing verticals. "
                f"Deal pipeline remains strong with large deal TCV at USD {random.randint(2,8)}B. "
                f"Attrition at {random.randint(12,22)}%, down from prior quarter."
            )
            items.append({"id": pid, "type": "Passage",
                           "properties": {"text": text, "doc_id": did, "position": p}})
            items.append({"source_id": pid, "target_id": f"ent_{c}", "edge_type": "MENTIONS"})

            # Embedding
            h = int(hashlib.md5(pid.encode()).hexdigest()[:8], 16)
            vec = np.random.RandomState(h).randn(128).astype(np.float32)
            vec /= np.linalg.norm(vec)
            embeddings[pid] = vec

            if p % 3 == 0:
                fid = f"f_{d}_{p}"
                items.append({"id": fid, "type": "Fact", "properties": {
                    "statement": f"{c} revenue INR {rev}cr, {growth}% YoY growth Q{d%4+1}",
                    "doc_id": pid, "fact_type": "metric", "confidence": 0.95}})
                items.append({"source_id": pid, "target_id": fid, "edge_type": "STATES"})

    # Prices
    for c in companies:
        base = random.uniform(500, 5000)
        for day in range(90):
            items.append({"type": "Price", "properties": {
                "ticker": c, "date": f"2026-{day//30+6:02d}-{day%30+1:02d}",
                "close": round(base * (1 + random.gauss(0, 0.015)), 2),
                "volume": random.randint(500000, 5000000)}})

    return items, embeddings, companies


def run_detailed_rag(graph, store, resolver, embeddings, companies, query_text, show_data=False):
    """Run one RAG query with per-step timing."""
    timings = {}

    # ── Step 1: Vector Search ──
    np.random.seed(hash(query_text) % 2**31)
    q_vec = np.random.randn(128).astype(np.float32)
    q_vec /= np.linalg.norm(q_vec)

    t0 = time.perf_counter()
    scores = {pid: float(np.dot(q_vec, vec)) for pid, vec in embeddings.items()}
    top_ids = sorted(scores, key=scores.get, reverse=True)[:5]
    timings["1_vector_search"] = (time.perf_counter() - t0) * 1000

    # ── Step 2: Content Resolve ──
    t0 = time.perf_counter()
    texts = resolver.get_texts_batch(top_ids)
    timings["2_content_resolve"] = (time.perf_counter() - t0) * 1000

    # ── Step 3: Graph Expand - Outgoing ──
    t0 = time.perf_counter()
    passage_entities = {}  # pid -> [entity_ids]
    passage_facts = {}     # pid -> [fact dicts]
    for pid in top_ids:
        entities = []
        facts = []
        for nid, edge in graph.get_neighbors(pid):
            if edge.edge_type == "MENTIONS":
                entities.append(nid)
            elif edge.edge_type == "STATES":
                fact = resolver.resolve(graph.get_node(nid))
                if fact:
                    facts.append(fact)
        passage_entities[pid] = entities
        passage_facts[pid] = facts
    timings["3_graph_outgoing"] = (time.perf_counter() - t0) * 1000

    # ── Step 4: Graph Expand - Incoming (entity context) ──
    t0 = time.perf_counter()
    unique_entities = set()
    for ents in passage_entities.values():
        unique_entities.update(ents)

    entity_context = {}
    for eid in unique_entities:
        ctx = {"id": eid}
        node = graph.get_node(eid)
        if node:
            ctx["name"] = node.properties.get("name", eid)
            ctx["type"] = node.node_type

        # Incoming edges: who else mentions this entity?
        incoming = graph.get_incoming_neighbors(eid)
        ctx["mentioned_in_passages"] = sum(1 for _, e in incoming if e.edge_type == "MENTIONS")
        ctx["docs_about"] = sum(1 for _, e in incoming if e.edge_type == "ABOUT")

        # Outgoing edges: competitors, sector
        outgoing = graph.get_neighbors(eid)
        ctx["competitors"] = [nid for nid, e in outgoing if e.edge_type == "COMPETITOR"]
        ctx["sector"] = [nid for nid, e in outgoing if e.edge_type == "IN_SECTOR"]

        entity_context[eid] = ctx
    timings["4_graph_incoming"] = (time.perf_counter() - t0) * 1000

    # ── Step 5: Entity Data (prices from DuckDB) ──
    t0 = time.perf_counter()
    entity_prices = {}
    for eid in unique_entities:
        node = graph.get_node(eid)
        if node and node.node_type == "Company":
            ticker = node.properties.get("name", "")
            prices = store.get_timeseries(ticker, 5)
            if prices:
                entity_prices[eid] = prices
    timings["5_entity_data"] = (time.perf_counter() - t0) * 1000

    # ── Step 6: Context Assembly ──
    t0 = time.perf_counter()
    context_parts = []
    # Passages with scores
    for pid in top_ids:
        context_parts.append({
            "type": "passage",
            "id": pid,
            "text": texts.get(pid, ""),
            "score": round(scores[pid], 4),
            "entities": passage_entities.get(pid, []),
            "facts": [f.get("text", f.get("statement", "")) for f in passage_facts.get(pid, [])],
        })
    # Entity summaries
    for eid, ctx in entity_context.items():
        context_parts.append({
            "type": "entity",
            "id": eid,
            "name": ctx.get("name", eid),
            "mentioned_in": ctx["mentioned_in_passages"],
            "docs_about": ctx["docs_about"],
            "competitors": ctx.get("competitors", []),
            "latest_price": entity_prices.get(eid, [{}])[0].get("value") if eid in entity_prices else None,
        })
    timings["6_context_assembly"] = (time.perf_counter() - t0) * 1000

    timings["_total"] = sum(timings.values())

    if show_data:
        print(f"\n  Query: \"{query_text}\"")
        print(f"\n  Step 1 - Vector Search ({timings['1_vector_search']:.2f}ms)")
        print(f"    Top 5 passages:")
        for pid in top_ids:
            print(f"      {pid} (score={scores[pid]:.4f})")

        print(f"\n  Step 2 - Content Resolve ({timings['2_content_resolve']:.2f}ms)")
        for pid in top_ids[:2]:
            print(f"      {pid}: \"{texts.get(pid, '')[:70]}...\"")

        print(f"\n  Step 3 - Graph Outgoing ({timings['3_graph_outgoing']:.2f}ms)")
        for pid in top_ids[:2]:
            ents = passage_entities.get(pid, [])
            facts = passage_facts.get(pid, [])
            print(f"      {pid} -> {len(ents)} entities, {len(facts)} facts")
            for f in facts[:1]:
                print(f"        Fact: \"{f.get('text', f.get('statement', ''))[:60]}\"")

        print(f"\n  Step 4 - Graph Incoming ({timings['4_graph_incoming']:.2f}ms)")
        for eid, ctx in list(entity_context.items())[:2]:
            print(f"      {ctx.get('name', eid)}: {ctx['mentioned_in_passages']} mentions, "
                  f"{ctx['docs_about']} docs, "
                  f"competitors={[graph.get_node(c).properties.get('name', c) if graph.get_node(c) else c for c in ctx.get('competitors', [])[:2]]}")

        print(f"\n  Step 5 - Entity Data ({timings['5_entity_data']:.2f}ms)")
        for eid, prices in list(entity_prices.items())[:2]:
            name = entity_context[eid].get("name", eid)
            latest = prices[0] if prices else {}
            print(f"      {name}: latest close={latest.get('value', '?')} on {latest.get('ts', '?')}")

        print(f"\n  Step 6 - Context Assembly ({timings['6_context_assembly']:.2f}ms)")
        print(f"      {len(context_parts)} items assembled for LLM")

    return timings


def main():
    tmpdir = tempfile.mkdtemp()
    graph = CSRGraphStorage()
    store = DuckDBStore(os.path.join(tmpdir, "c.duckdb"))
    router = StorageRouter(graph=graph)
    router._content_store = store
    resolver = ContentResolver()
    resolver._content_store = store

    for scale_label, n_docs in [("1K passages", 100), ("5K passages", 500), ("10K passages", 1000)]:
        items, embeddings, companies = build_test_data(n_docs)

        # Ingest
        t0 = time.perf_counter()
        router.ingest(items)
        ingest_time = time.perf_counter() - t0

        print("=" * 70)
        print(f"  RAG DETAILED BREAKDOWN -- {scale_label}")
        print(f"  ({n_docs} docs, {n_docs*10:,} passages, {len(embeddings):,} vectors)")
        print(f"  Ingested in {ingest_time:.2f}s")
        print("=" * 70)

        # Show one query with full data
        run_detailed_rag(graph, store, resolver, embeddings, companies,
                         "TCS revenue growth digital transformation", show_data=True)

        # Run 20 queries for timing averages
        queries = [
            "revenue growth quarterly results",
            "operating margin improvement",
            "cloud migration strategy",
            "AI services adoption",
            "deal pipeline large deals",
            "attrition talent retention",
            "BFSI vertical performance",
            "manufacturing sector outlook",
            "digital transformation initiative",
            "cost optimization efficiency",
            "TCS earnings analysis",
            "Infosys quarterly performance",
            "Wipro cloud strategy",
            "HCL technology outlook",
            "IT sector competition",
            "margin expansion drivers",
            "talent acquisition strategy",
            "client concentration risk",
            "currency impact hedging",
            "subcontracting cost management",
        ]

        all_timings = []
        for q in queries:
            t = run_detailed_rag(graph, store, resolver, embeddings, companies, q)
            all_timings.append(t)

        # Averages
        print(f"\n  {'=' * 50}")
        print(f"  TIMING BREAKDOWN (avg of {len(queries)} queries)")
        print(f"  {'=' * 50}")
        steps = ["1_vector_search", "2_content_resolve", "3_graph_outgoing",
                 "4_graph_incoming", "5_entity_data", "6_context_assembly", "_total"]
        for step in steps:
            vals = [t[step] for t in all_timings]
            avg = statistics.mean(vals)
            p50 = statistics.median(vals)
            p99 = sorted(vals)[min(int(len(vals) * 0.99), len(vals) - 1)]
            bar = "#" * max(1, int(avg / 0.5))
            label = step.replace("_", " ").title()
            if step == "_total":
                print(f"  {'─' * 50}")
            print(f"  {label:<25} avg={avg:>7.2f}ms  p50={p50:>7.2f}ms  p99={p99:>7.2f}ms  {bar}")

        # Percentage breakdown
        total_avg = statistics.mean([t["_total"] for t in all_timings])
        print(f"\n  Breakdown by % of total ({total_avg:.1f}ms):")
        for step in steps[:-1]:
            avg = statistics.mean([t[step] for t in all_timings])
            pct = (avg / total_avg) * 100
            bar = "=" * max(1, int(pct / 2))
            label = step.split("_", 1)[1].replace("_", " ").title()
            print(f"    {label:<22} {pct:>5.1f}%  {bar}")

        # Reset for next scale
        graph = CSRGraphStorage()
        store = DuckDBStore(os.path.join(tmpdir, f"c_{n_docs}.duckdb"))
        router = StorageRouter(graph=graph)
        router._content_store = store
        resolver._content_store = store

    shutil.rmtree(tmpdir, ignore_errors=True)


if __name__ == "__main__":
    main()
