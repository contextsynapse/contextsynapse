#!/usr/bin/env python3
"""
Graph RAG Demo
===============
Shows ContextSynapse's retrieval-augmented generation pipeline:
  1. Build a knowledge graph from documents
  2. Keyword search (no LLM needed)
  3. Topic clustering (no LLM needed)
  4. Hybrid retrieve (vector + BM25 + keyword fusion)
  5. Context assembly for LLM consumption

The core pipeline works without an LLM.
Vector search and answer generation need an LLM key (optional).

    pip install -e "."
    python examples/demo_graph_rag.py
"""

from __future__ import annotations
import time
import uuid
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextsynapse import ContextSynapse
from contextsynapse.core.graph_structures import GraphNode, GraphEdge
from contextsynapse.aiql.engine import AIQLExecutor

console = Console()


def section(title):
    console.print()
    console.rule(f"[bold cyan]{title}")
    console.print()


def main():
    console.print(Panel.fit(
        "[bold white]Graph RAG Demo[/bold white]\n"
        "[dim]Retrieval-Augmented Generation with a knowledge graph backend[/dim]",
        border_style="cyan",
    ))

    t0 = time.time()
    db = ContextSynapse()
    ex = AIQLExecutor(contextcore=db)
    gname = f"rag_demo_{uuid.uuid4().hex[:6]}"
    ex.execute(f"CREATE GRAPH {gname}")
    ex.execute(f"USE GRAPH {gname}")

    # =====================================================================
    section("1. Ingest Knowledge")
    # =====================================================================

    # Simulate ingesting documents as graph nodes
    documents = [
        ("Document", "API Authentication Guide",
         "All API requests require a JWT token in the Authorization header. "
         "Tokens are issued via POST /auth/login with username and password. "
         "Tokens expire after 24 hours. Refresh tokens last 30 days. "
         "Admin endpoints require the x-admin-key header in addition to JWT."),

        ("Document", "Database Migration Runbook",
         "Step 1: Run pg_dump on the source database. "
         "Step 2: Transfer the dump to the target server via scp. "
         "Step 3: Run pg_restore with --no-owner flag. "
         "Step 4: Run ALTER OWNER on all tables to the new service account. "
         "Step 5: Verify row counts match between source and target."),

        ("Fact", "PostgreSQL Connection Pool",
         "The API uses pgbouncer with max 100 connections in transaction mode. "
         "Each FastAPI worker gets 10 connections from the pool. "
         "Under 500 RPS load the pool saturates at 95% utilization."),

        ("Fact", "Redis Cache Strategy",
         "Cache TTL is 5 minutes for search results and 30 minutes for graph snapshots. "
         "Cache invalidation happens on write via pub/sub channel context:invalidate. "
         "Cache hit rate is typically 78% in production."),

        ("Passage", "Incident Post-Mortem INC-087",
         "Root cause: connection pool exhaustion during traffic spike. "
         "The pgbouncer pool was configured with max_connections=20 which was "
         "insufficient for 500 RPS sustained load. Resolution: increased pool "
         "to 100 connections, added circuit breaker, and deployed connection "
         "recycling with 300s max lifetime."),

        ("Passage", "Kubernetes Scaling Policy",
         "HPA configured with CPU target 70% and memory target 80%. "
         "Min replicas: 3, max replicas: 20. Scale-up cooldown: 60 seconds, "
         "scale-down cooldown: 300 seconds. Custom metric: request_queue_depth "
         "with target value 10 triggers emergency scale-up."),

        ("Fact", "Embedding Model Configuration",
         "Using all-MiniLM-L6-v2 for embeddings with 384 dimensions. "
         "Batch size 64 for indexing, single query for search. "
         "Average embedding latency: 12ms local, 150ms via API."),

        ("Fact", "Search Ranking Algorithm",
         "Uses Reciprocal Rank Fusion combining three signals: "
         "vector similarity (cosine), BM25 full-text score, and keyword match. "
         "RRF formula: score = sum(1/(60+rank)) across all signals. "
         "Temporal recency boost with 72-hour half-life applied post-fusion."),

        ("Document", "Onboarding Checklist",
         "New engineer setup: 1) Clone all repos from GitHub org. "
         "2) Install Python 3.12 and Node 20. 3) Run docker compose up for "
         "Redis and PostgreSQL. 4) Copy .env.example to .env and set API keys. "
         "5) Run pytest to verify setup. 6) Request Vault access from SRE team."),

        ("Decision", "ADR-015 Replace Whoosh with Tantivy",
         "Whoosh has been unmaintained since 2023. Tantivy-py provides "
         "10x faster indexing, proper concurrent read/write access, and "
         "BM25 scoring out of the box. Migration path: dual-write for "
         "2 weeks, then cut over. Risk: tantivy-py wheels not available "
         "for all platforms."),
    ]

    console.print("  Ingesting documents into knowledge graph...")
    node_ids = {}
    for label, name, content in documents:
        nid = f"{label.lower()}_{uuid.uuid4().hex[:8]}"
        node = GraphNode(id=nid, label=label, properties={"name": name, "content": content})
        db.add_node(node)
        node_ids[name] = nid
    console.print(f"  [green]+[/green] {len(documents)} documents ingested")

    # Add relationships
    edge_defs = [
        ("PostgreSQL Connection Pool", "Incident Post-Mortem INC-087", "RELATED_TO"),
        ("Redis Cache Strategy", "Search Ranking Algorithm", "RELATED_TO"),
        ("API Authentication Guide", "Onboarding Checklist", "REFERENCED_BY"),
        ("ADR-015 Replace Whoosh with Tantivy", "Search Ranking Algorithm", "AFFECTS"),
    ]
    for src_name, tgt_name, rel in edge_defs:
        if src_name in node_ids and tgt_name in node_ids:
            eid = f"e_{uuid.uuid4().hex[:8]}"
            db.add_edge(GraphEdge(id=eid, source=node_ids[src_name], target=node_ids[tgt_name], label=rel, properties={}))
    console.print(f"  [green]+[/green] {len(edge_defs)} relationships created")

    # =====================================================================
    section("2. Keyword Search (no LLM needed)")
    # =====================================================================

    from contextsynapse.search.rag import plain_search, _build_keyword_index

    # Build keyword index
    _build_keyword_index(db, gname)

    queries = [
        "connection pool pgbouncer",
        "authentication JWT token",
        "kubernetes scaling HPA",
        "search ranking BM25 vector",
    ]

    for q in queries:
        console.print(f"  [bold]Query:[/bold] \"{q}\"")
        results = plain_search(db, q, k=3)
        if results:
            for r in results[:2]:
                score = r.get("score", 0)
                name = r.get("name", "?")
                label = r.get("label", "?")
                console.print(f"    [{label}] {name} (score: {score:.2f})")
        else:
            console.print(f"    [dim](no results)[/dim]")
        console.print()

    # =====================================================================
    section("3. Topic Clustering (no LLM needed)")
    # =====================================================================

    from contextsynapse.search.rag import topic_scan

    topics = topic_scan(db, graph_name=gname, max_topics=8, samples_per_topic=3)
    topic_list = topics.get("topics", [])
    total = topics.get("total_content_nodes", 0)
    scan_ms = topics.get("scan_ms", 0)

    console.print(f"  [bold]Found {len(topic_list)} topics across {total} content nodes ({scan_ms}ms):[/bold]\n")
    for t in topic_list[:6]:
        name = t.get("name", "?")
        count = t.get("count", 0)
        samples = t.get("samples", [])
        sample_str = ", ".join(s[:30] for s in samples[:2])
        console.print(f"  [cyan]{name}[/cyan] ({count} nodes)")
        if sample_str:
            console.print(f"    [dim]e.g. {sample_str}[/dim]")

    # =====================================================================
    section("4. Hybrid Retrieval Pipeline")
    # =====================================================================

    from contextsynapse.search.rag import hybrid_retrieve

    rag_question = "What caused the connection pool incident and how was it fixed?"
    console.print(f"  [bold]Question:[/bold] {rag_question}\n")

    try:
        top_results, sources = hybrid_retrieve(
            db, rag_question,
            graph_name=gname,
            k=5,
            skip_vector=True,  # Skip vector search (needs embedding model)
        )

        if top_results:
            table = Table(title="Retrieved Context (BM25 + Keyword Fusion)", box=box.ROUNDED)
            table.add_column("#", justify="right", style="dim", width=3)
            table.add_column("Type", style="cyan", width=10)
            table.add_column("Name", width=35)
            table.add_column("Score", justify="right", width=8)

            for i, r in enumerate(top_results[:5], 1):
                table.add_row(
                    str(i),
                    r.get("label", "?"),
                    (r.get("name") or r.get("props", {}).get("name", "?"))[:35],
                    f"{r.get('score', 0):.3f}",
                )
            console.print(table)
        else:
            console.print("  [dim]No results from hybrid retrieval[/dim]")
    except Exception as e:
        console.print(f"  [dim]Hybrid retrieval: {e}[/dim]")

    # =====================================================================
    section("5. Context Assembly for LLM")
    # =====================================================================

    from contextsynapse.context.hub import ContextHub

    hub = ContextHub(
        system_prompt=(
            "You are a senior SRE investigating a production incident. "
            "Use the retrieved context from the knowledge graph to answer "
            "the engineer's question accurately. Cite specific facts."
        )
    )

    # Add all nodes as context
    all_nodes = db.get_all_nodes()
    hub.add_nodes(all_nodes)
    messages = hub.to_messages()
    tokens_est = sum(len(m.get("content", "")) for m in messages) // 4

    console.print(f"  [bold]Assembled context:[/bold]")
    console.print(f"    Messages:  {len(messages)}")
    console.print(f"    Tokens:    ~{tokens_est:,}")
    console.print(f"    Documents: {len(all_nodes)} nodes included")
    console.print()
    console.print("  [bold]Ready for:[/bold]")
    console.print("    response = openai.chat.completions.create(")
    console.print("        model='gpt-4o',")
    console.print("        messages=hub.to_messages()")
    console.print("    )")
    console.print()
    console.print("  Or with Anthropic:")
    console.print("    response = anthropic.messages.create(")
    console.print("        model='claude-sonnet-4-20250514',")
    console.print("        system=messages[0]['content'],")
    console.print("        messages=messages[1:]")
    console.print("    )")

    # =====================================================================
    section("Summary")
    # =====================================================================

    elapsed = time.time() - t0
    console.print(Panel.fit(
        f"[bold green]Graph RAG Demo Complete[/bold green]\n\n"
        f"  Documents:    {len(documents)} ingested as graph nodes\n"
        f"  Edges:        {len(edge_defs)} relationships connecting knowledge\n"
        f"  Search:       Keyword + BM25 (no LLM needed)\n"
        f"  Topics:       {len(topic_list)} auto-clustered\n"
        f"  RAG context:  ~{tokens_est:,} tokens assembled\n"
        f"  Time:         {elapsed:.1f}s\n\n"
        f"  [bold]Why Graph RAG > Vector RAG:[/bold]\n"
        f"  Regular RAG: retrieve similar chunks, hope for the best\n"
        f"  Graph RAG:   retrieve + follow relationships + expand context\n"
        f"  The graph connects incidents to root causes to runbooks --\n"
        f"  relationships that pure vector similarity would miss.",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
