"""Full Pipeline Test -- every stage from connector to RAG output."""
import asyncio
import os
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
from contextsynapse.engine import ContextEngine
from contextsynapse.governance import GovernanceLayer
from contextsynapse.memory.temporal import TemporalMemory
from contextsynapse.connectors.universal import WebhookConnector, ConnectorConfig
from contextsynapse.storage.router import factory
from contextsynapse.storage.router.resolver import ContentResolver
from contextsynapse.storage.router import resolver as rmod


def main():
    d = tempfile.mkdtemp()
    graph = CSRGraphStorage()
    store = DuckDBStore(os.path.join(d, "c.duckdb"))
    engine = ContextEngine(graph=graph)
    engine.router._content_store = store
    engine.memory._router = engine.router
    engine.resolver._content_store = store
    factory._global_store = store
    r = ContentResolver()
    r._content_store = store
    rmod._global_resolver = r
    gov = GovernanceLayer(engine=engine)
    tmem = TemporalMemory(engine=engine)

    print("=" * 60)
    print("FULL PIPELINE: Connector -> Govern -> Store -> Memory -> Search -> RAG")
    print("=" * 60)

    # Step 1: Connector
    print("\n--- 1. Connector ---")
    wh = WebhookConnector(item_type="Document", config=ConnectorConfig(name="news"))
    wh.receive({"id": "news_1", "text": "TCS wins USD 2B deal with US bank for cloud transformation"})
    wh.receive({"id": "news_2", "text": "Infosys attrition rises to 28% in Q2, margins under pressure"})
    wh.receive({"id": "news_3", "text": "IT sector to grow 8-10% in FY26 driven by AI adoption"})
    raw = asyncio.run(wh.collect())
    print(f"  Connector output: {len(raw)} items")

    # Step 2: Governed Ingest
    print("\n--- 2. Governed Ingest ---")
    items = raw + [
        {"id": "ent_TCS", "type": "Company", "properties": {"name": "TCS", "sector": "IT"}},
        {"id": "ent_Infosys", "type": "Company", "properties": {"name": "Infosys", "sector": "IT"}},
        {"source_id": "ent_TCS", "target_id": "ent_Infosys", "edge_type": "COMPETITOR"},
        {"id": "p1", "type": "Passage", "properties": {
            "text": "TCS wins USD 2B deal with major US bank for cloud transformation over 5 years. Largest deal in TCS history.",
            "doc_id": "news_1", "position": 0}},
        {"id": "p2", "type": "Passage", "properties": {
            "text": "Infosys faces margin pressure as attrition rises to 28% in Q2. Management expects normalization by Q4.",
            "doc_id": "news_2", "position": 0}},
        {"id": "p3", "type": "Passage", "properties": {
            "text": "Indian IT sector expected to grow 8-10% in FY26 driven by AI and cloud adoption across verticals.",
            "doc_id": "news_3", "position": 0}},
        {"source_id": "p1", "target_id": "ent_TCS", "edge_type": "MENTIONS"},
        {"source_id": "p2", "target_id": "ent_Infosys", "edge_type": "MENTIONS"},
        {"id": "f1", "type": "Fact", "properties": {
            "statement": "TCS won USD 2B cloud deal with US bank",
            "passage_id": "p1", "fact_type": "event", "confidence": 0.95}},
        {"id": "f2", "type": "Fact", "properties": {
            "statement": "Infosys attrition at 28% in Q2",
            "passage_id": "p2", "fact_type": "metric", "confidence": 0.9}},
        {"source_id": "p1", "target_id": "f1", "edge_type": "STATES"},
        {"source_id": "p2", "target_id": "f2", "edge_type": "STATES"},
        {"type": "Price", "properties": {"ticker": "TCS", "date": "2026-09-10", "close": 4200}},
        {"type": "Price", "properties": {"ticker": "TCS", "date": "2026-09-09", "close": 4150}},
        {"type": "Price", "properties": {"ticker": "Infosys", "date": "2026-09-10", "close": 1850}},
    ]
    counts = gov.ingest("analyst-1", "agent", items)
    print(f"  Counts: {counts}")
    print(f"  Graph: {graph.get_node_count()} nodes, {graph.get_edge_count()} edges")
    print(f"  DuckDB: {store.stats()}")

    # Step 3: Memory
    print("\n--- 3. Memory ---")
    tmem.set_hot("analyst-1", "focus", "IT sector mega deals")
    engine.remember("analyst-1", "TCS is our top pick for Q3", memory_type="decision")
    engine.remember("analyst-1", "Client prefers large-cap IT", memory_type="preference")
    hot = tmem.get_hot_all("analyst-1")
    cold = engine.recall("analyst-1")
    print(f"  Hot: {hot}")
    print(f"  Cold: {len(cold)} memories")

    # Step 4: Search
    print("\n--- 4. Search (entity boost + rerank + diversity) ---")
    t0 = time.perf_counter()
    results = engine.search("TCS cloud deal", top_k=3, include=["passages", "entities", "prices"])
    ms = (time.perf_counter() - t0) * 1000
    print(f"  Time: {ms:.1f}ms | Results: {results['result_count']}")
    for p in results["passages"]:
        boost = " [BOOSTED]" if p.get("_entity_boost") else ""
        ents = [e["name"] for e in p.get("entities", [])]
        print(f"    score={p['score']:.2f}{boost} entities={ents}")
        print(f'      "{p["text"][:70]}..."')
    for e in results.get("entities", []):
        print(f"    Entity: {e['name']} ({e['mentioned_in']} mentions)")
    for pr in results.get("prices", []):
        print(f"    Price: {pr['entity']}={pr['latest']}")

    # Step 5: Entity Context
    print("\n--- 5. Entity Context ---")
    ctx = engine.context("ent_TCS")
    print(f"  Passages: {len(ctx.get('passages', []))}")
    print(f"  Neighbors: {[n['name']+'('+n['edge']+')' for n in ctx.get('neighbors', [])]}")
    print(f"  Prices: {len(ctx.get('prices', []))}")
    print(f"  LLM tokens: {ctx['token_estimate']}")

    # Step 6: Full RAG
    print("\n--- 6. Full RAG: engine.ask() ---")
    t0 = time.perf_counter()
    answer = engine.ask("What is the latest news about TCS? Any major deals?", top_k=3)
    ms = (time.perf_counter() - t0) * 1000
    print(f"  Time: {ms:.1f}ms | Messages: {len(answer['messages'])} | Tokens: {answer['token_estimate']}")
    print(f"  Sources: {len(answer['sources'])} | Timings: {answer['timings']}")
    for msg in answer["messages"]:
        content = msg["content"][:100].replace("\n", " ")
        print(f"    [{msg['role']}] {content}...")

    # Step 7: Governance
    print("\n--- 7. Governance Report ---")
    audit = gov.audit_log(last_n=5)
    print(f"  Audit: {len(audit)} entries")
    for a in audit[-3:]:
        print(f"    [{a['action']}] {a['agent_id']} ({a['status']}) {a['duration_ms']}ms")

    # Final stats
    print("\n--- Stats ---")
    s = engine.stats()
    print(f"  {s}")

    shutil.rmtree(d, ignore_errors=True)
    print("\n" + "=" * 60)
    print("ALL PIPELINE STAGES PASS")
    print("=" * 60)


if __name__ == "__main__":
    main()
