#!/usr/bin/env python3
"""
ContextSynapse -- Real-World Demo
==================================
Simulates a startup's knowledge base: team, codebase, incidents,
decisions -- and shows how ContextSynapse connects everything for
AI agents to consume.

Features demonstrated:
  1. AIQL graph construction (people, repos, incidents, decisions)
  2. AIQL queries -- SELECT, MATCH, filtering
  3. PII detection on ingested content
  4. LLM context assembly -- ready for any AI model
  5. Direct graph API for low-level access

No external services needed. Runs entirely in-process.

    pip install -e "."
    python examples/real_demo.py
"""

from __future__ import annotations

import time
import uuid

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.tree import Tree
from rich import box

from contextsynapse import ContextSynapse
from contextsynapse.aiql.engine import AIQLExecutor

console = Console()


def _props(node):
    if isinstance(node, dict):
        return node.get("properties", node)
    return getattr(node, "properties", {}) or {}


def section(title):
    console.print()
    console.rule(f"[bold cyan]{title}")
    console.print()


def main():
    console.print(Panel.fit(
        "[bold white]ContextSynapse -- Real-World Demo[/bold white]\n"
        "[dim]A startup's knowledge graph: team, code, incidents, decisions[/dim]",
        border_style="cyan",
    ))

    t0 = time.time()
    db = ContextSynapse()
    ex = AIQLExecutor(contextcore=db)
    gname = f"startup_{uuid.uuid4().hex[:6]}"
    ex.execute(f"CREATE GRAPH {gname}")
    ex.execute(f"USE GRAPH {gname}")

    # =====================================================================
    section("1. Build the Knowledge Graph (AIQL)")
    # =====================================================================

    console.print("  Creating nodes with AIQL...")

    # -- Team --
    team_data = [
        ("Alice Chen",       "CTO",             "engineering", "distributed-systems,rust,python"),
        ("Bob Martinez",     "Lead Backend",     "engineering", "python,postgres,redis"),
        ("Carol Okafor",     "ML Engineer",      "ml",          "pytorch,transformers,rag"),
        ("Dave Kim",         "Frontend Lead",    "frontend",    "react,typescript,webgl"),
        ("Eve Johansson",    "SRE",              "infra",       "kubernetes,terraform,prometheus"),
    ]
    for name, role, team, skills in team_data:
        ex.execute(f'CREATE NODE Person {{name: "{name}", role: "{role}", team: "{team}", expertise: "{skills}"}}')
    console.print(f"    [green]+[/green] {len(team_data)} team members")

    # -- Repositories --
    repo_data = [
        ("synapse-api",       "Python",     "active",     "Core REST API with FastAPI and PostgreSQL"),
        ("synapse-dashboard", "TypeScript", "active",     "React dashboard with graph visualizer"),
        ("synapse-ml",        "Python",     "active",     "ML pipeline for embeddings NER and RAG"),
        ("synapse-infra",     "HCL",        "active",     "Terraform and K8s manifests"),
        ("old-monolith",      "Java",       "deprecated", "Legacy v1 monolith being migrated off"),
    ]
    for name, lang, status, desc in repo_data:
        ex.execute(f'CREATE NODE Repository {{name: "{name}", language: "{lang}", status: "{status}", description: "{desc}"}}')
    console.print(f"    [green]+[/green] {len(repo_data)} repositories")

    # -- Incidents --
    inc_data = [
        ("INC-087", "P1", "resolved",      "API latency spike p99 over 2s for 45 minutes",
         "Connection pool exhaustion under load with max_connections 20 too low for 500 RPS"),
        ("INC-092", "P2", "resolved",      "ML pipeline OOM crash from oversized embedding batch",
         "Batch size 10K with 1536-dim embeddings exceeded 16GB pod memory limit"),
        ("INC-101", "P2", "investigating", "Dashboard blank page for Chrome 128 users",
         "Chrome 128 deprecated document.domain breaking iframe auth flow"),
    ]
    for name, sev, status, title, root_cause in inc_data:
        ex.execute(f'CREATE NODE Incident {{name: "{name}", severity: "{sev}", status: "{status}", title: "{title}", root_cause: "{root_cause}"}}')
    console.print(f"    [green]+[/green] {len(inc_data)} incidents")

    # -- Architecture Decisions --
    adr_data = [
        ("ADR-001", "accepted", "Adopt CSR graph for in-memory storage giving O(1) neighbor lookup"),
        ("ADR-007", "accepted", "Use AIQL instead of Cypher or SPARQL for unified query language"),
        ("ADR-012", "accepted", "Migrate from SQLite to PostgreSQL for multi-worker auth"),
        ("ADR-015", "proposed", "Evaluate replacing Whoosh with Tantivy for 10x faster indexing"),
    ]
    for name, status, title in adr_data:
        ex.execute(f'CREATE NODE Decision {{name: "{name}", status: "{status}", title: "{title}"}}')
    console.print(f"    [green]+[/green] {len(adr_data)} architecture decisions")

    # -- Edges --
    edge_defs = [
        ("WORKS_ON",   "Person", "Alice Chen",   "Repository", "synapse-api"),
        ("WORKS_ON",   "Person", "Bob Martinez",  "Repository", "synapse-api"),
        ("WORKS_ON",   "Person", "Carol Okafor",  "Repository", "synapse-ml"),
        ("WORKS_ON",   "Person", "Dave Kim",      "Repository", "synapse-dashboard"),
        ("WORKS_ON",   "Person", "Eve Johansson", "Repository", "synapse-infra"),
        ("RESPONDED_TO", "Person", "Bob Martinez",  "Incident", "INC-087"),
        ("RESPONDED_TO", "Person", "Eve Johansson", "Incident", "INC-087"),
        ("RESPONDED_TO", "Person", "Carol Okafor",  "Incident", "INC-092"),
        ("RESPONDED_TO", "Person", "Dave Kim",      "Incident", "INC-101"),
        ("AUTHORED",   "Person", "Alice Chen",    "Decision",   "ADR-001"),
        ("AUTHORED",   "Person", "Alice Chen",    "Decision",   "ADR-007"),
        ("AUTHORED",   "Person", "Bob Martinez",  "Decision",   "ADR-012"),
    ]
    created_edges = 0
    for rel, src_lbl, src_name, tgt_lbl, tgt_name in edge_defs:
        r = ex.execute(
            f'CREATE EDGE {rel} FROM {src_lbl} WHERE name = "{src_name}" '
            f'TO {tgt_lbl} WHERE name = "{tgt_name}"'
        )
        if r.get("success"):
            created_edges += 1
    console.print(f"    [green]+[/green] {created_edges} relationships")

    build_ms = (time.time() - t0) * 1000

    # -- Summary table --
    all_nodes = db.get_all_nodes()
    all_edges = db.get_all_edges()
    label_counts = {}
    for n in all_nodes:
        lbl = getattr(n, "label", "?")
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

    table = Table(title="Knowledge Graph", box=box.ROUNDED)
    table.add_column("Type", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for lbl in ["Person", "Repository", "Incident", "Decision"]:
        if lbl in label_counts:
            table.add_row(lbl, str(label_counts[lbl]))
    table.add_row("[bold]Total Nodes", f"[bold]{len(all_nodes)}")
    table.add_row("[bold]Total Edges", f"[bold]{len(all_edges)}")
    console.print(table)
    console.print(f"  [dim]Built in {build_ms:.0f}ms[/dim]")

    # =====================================================================
    section("2. AIQL Queries")
    # =====================================================================

    # Query: All people
    console.print("[bold]Team:[/bold]")
    result = ex.execute("SELECT * FROM Person")
    for node in result.get("nodes", []):
        p = _props(node)
        name = p.get("name", "?")
        role = p.get("role", "?")
        team = p.get("team", "?")
        console.print(f"  {name:20s}  {role:18s}  [dim]({team})[/dim]")
    console.print()

    # Query: P1 incidents
    console.print("[bold]P1 Incidents:[/bold]")
    result = ex.execute('MATCH NODE Incident WHERE severity = "P1"')
    nodes = result.get("nodes", [])
    if nodes:
        for node in nodes:
            p = _props(node)
            console.print(f"  [red]*[/red] {p.get('name', '?')} -- {p.get('title', '?')}")
            console.print(f"    Root cause: [dim]{(p.get('root_cause') or '?')[:90]}[/dim]")
    else:
        # Fallback: filter manually from SELECT
        result = ex.execute("SELECT * FROM Incident")
        for node in result.get("nodes", []):
            p = _props(node)
            if p.get("severity") == "P1":
                console.print(f"  [red]*[/red] {p.get('name', '?')} -- {p.get('title', '?')}")
                console.print(f"    Root cause: [dim]{(p.get('root_cause') or '?')[:90]}[/dim]")
    console.print()

    # Query: Active repos
    console.print("[bold]Active Repositories:[/bold]")
    result = ex.execute("SELECT * FROM Repository")
    for node in result.get("nodes", []):
        p = _props(node)
        if p.get("status") == "active":
            console.print(f"  [green]*[/green] {(p.get('name') or '?'):22s}  {(p.get('language') or '?'):12s}  {(p.get('description') or '')[:50]}")
    console.print()

    # Query: Open decisions
    console.print("[bold]Open Architecture Decisions:[/bold]")
    result = ex.execute("SELECT * FROM Decision")
    for node in result.get("nodes", []):
        p = _props(node)
        if p.get("status") == "proposed":
            console.print(f"  [yellow]*[/yellow] {p.get('name', '?')} -- {p.get('title', '?')}")

    # =====================================================================
    section("3. PII Detection")
    # =====================================================================

    from contextsynapse.security.pii import PIIDetector
    pii = PIIDetector()

    test_texts = [
        "Ping Bob at bob.martinez@acme.com for the API keys",
        "Call support at 555-0199 or email help@acme.io -- ref customer SSN 078-05-1120",
        "Deploy to us-east-1 with terraform apply -- no PII here",
    ]

    for text in test_texts:
        result = pii.scan(text)
        if result.pii_found:
            console.print(f"  [red]!! PII FOUND[/red] ({result.entity_count} entities, sensitivity: {result.sensitivity})")
            console.print(f"    Text: [dim]{text}[/dim]")
            for value, pii_type in result.entities:
                console.print(f"    -> [red]{pii_type}[/red]: {value}")
        else:
            console.print(f"  [green]OK Clean[/green]: [dim]{text[:60]}[/dim]")
    console.print()

    # =====================================================================
    section("4. LLM Context Assembly")
    # =====================================================================

    from contextsynapse.context.hub import ContextHub

    hub = ContextHub(
        system_prompt=(
            "You are an on-call SRE investigating a P1 incident at a startup. "
            "Use the knowledge graph context to understand the incident, "
            "identify who to page, and suggest remediation."
        )
    )
    hub.add_nodes(all_nodes)
    messages = hub.to_messages()
    tokens_est = sum(len(m.get("content", "")) for m in messages) // 4

    table = Table(title="Assembled LLM Context", box=box.ROUNDED)
    table.add_column("Message", style="cyan")
    table.add_column("Role", style="green")
    table.add_column("Size", justify="right")
    for i, m in enumerate(messages):
        size = len(m.get("content", ""))
        table.add_row(
            "System Prompt" if i == 0 else f"Context {i}",
            m.get("role", "?"),
            f"{size:,} chars",
        )
    console.print(table)
    console.print(f"  Estimated tokens: ~{tokens_est:,}")
    console.print(f"  Ready for: OpenAI, Anthropic, Groq, Ollama, or any LLM")
    console.print()

    # Preview
    if len(messages) > 1:
        snippet = messages[1]["content"][:400].replace("[", "(").replace("]", ")")
        console.print(Panel(
            snippet + "...",
            title="Context Preview (first 400 chars)",
            border_style="dim",
        ))

    # =====================================================================
    section("5. Graph Structure")
    # =====================================================================

    edge_types = {}
    for e in all_edges:
        lbl = getattr(e, "label", "?")
        edge_types[lbl] = edge_types.get(lbl, 0) + 1

    tree = Tree("[bold]Relationship Types")
    for lbl, count in sorted(edge_types.items(), key=lambda x: -x[1]):
        tree.add(f"[cyan]{lbl}[/cyan] ({count})")
    console.print(tree)

    # =====================================================================
    section("Done")
    # =====================================================================

    elapsed = time.time() - t0
    console.print(Panel.fit(
        f"[bold green]Demo Complete[/bold green]\n\n"
        f"  Graph:        {len(all_nodes)} nodes, {len(all_edges)} edges\n"
        f"  Node types:   {', '.join(sorted(label_counts.keys()))}\n"
        f"  LLM context:  ~{tokens_est:,} tokens assembled\n"
        f"  PII scanner:  3 scans, real-time detection\n"
        f"  Time:         {elapsed:.1f}s\n\n"
        f"  [dim]Start the full stack:[/dim]\n"
        f"  uvicorn contextsynapse.api.api:app --port 8000\n"
        f"  cd frontend && npm start",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
