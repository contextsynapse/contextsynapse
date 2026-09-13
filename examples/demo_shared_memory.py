#!/usr/bin/env python3
"""
Shared Agent Memory Demo
=========================
Shows how multiple AI agents share a knowledge graph as their
collective memory — remember, recall, cross-agent signals, and
context assembly.

No LLM or external services needed.

    pip install -e "."
    python examples/demo_shared_memory.py
"""

from __future__ import annotations
import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextsynapse import ContextSynapse
from contextsynapse.core.registry import GraphRegistry
from contextsynapse.context.agent_memory import AgentMemory
from contextsynapse.aiql.engine import AIQLExecutor

console = Console()


def section(title):
    console.print()
    console.rule(f"[bold cyan]{title}")
    console.print()


def main():
    console.print(Panel.fit(
        "[bold white]Shared Agent Memory Demo[/bold white]\n"
        "[dim]Multiple agents sharing one knowledge graph as collective memory[/dim]",
        border_style="cyan",
    ))

    t0 = time.time()

    # ── Setup ────────────────────────────────────────────────
    reg = GraphRegistry()
    mem = AgentMemory(reg, namespace="shared_brain")

    # Also set up AIQL for the shared graph
    db = reg.get_graph_for_request("shared_brain")
    ex = AIQLExecutor(contextcore=db, graph_registry=reg)

    # =====================================================================
    section("1. Agent Alice (Research Agent) stores findings")
    # =====================================================================

    alice_memories = [
        ("Tesla Q3 revenue was $25.2B, up 8% YoY", ["fact", "tesla", "revenue"], 0.95),
        ("Tesla FSD v13 rollout delayed to Q1 2027 due to regulatory review", ["fact", "tesla", "fsd"], 0.85),
        ("Analyst consensus: TSLA target price $280, current $245", ["analysis", "tesla", "price"], 0.75),
        ("User prefers bullet-point summaries over paragraphs", ["preference"], 0.99),
        ("NVDA earnings beat estimates by 12% — GPU demand from AI training", ["fact", "nvidia", "earnings"], 0.90),
    ]

    for content, tags, confidence in alice_memories:
        mid = mem.remember("alice", content, tags=tags, confidence=confidence)
        console.print(f"  [green]+[/green] Alice stored: [dim]{content[:60]}[/dim]")

    # =====================================================================
    section("2. Agent Bob (Analyst Agent) stores analysis")
    # =====================================================================

    bob_memories = [
        ("Tesla's P/E ratio of 65x is high vs sector average 22x — overvalued risk", ["analysis", "tesla", "valuation"], 0.80),
        ("Correlation found: TSLA price tracks BTC with 0.72 R-squared", ["pattern", "tesla", "correlation"], 0.70),
        ("Recommendation: reduce TSLA position by 15% given macro headwinds", ["decision", "tesla", "portfolio"], 0.65),
    ]

    for content, tags, confidence in bob_memories:
        mid = mem.remember("bob", content, tags=tags, confidence=confidence)
        console.print(f"  [green]+[/green] Bob stored: [dim]{content[:60]}[/dim]")

    # =====================================================================
    section("3. Cross-Agent Recall")
    # =====================================================================

    # Alice recalls her own Tesla memories
    console.print("[bold]Alice recalls her Tesla research:[/bold]")
    alice_tesla = mem.recall("alice", query="tesla", limit=5)
    for m in alice_tesla:
        conf = m.get("effective_confidence", m.get("confidence", 0))
        console.print(f"  [{conf:.0%}] {m['content'][:70]}")
    console.print()

    # Bob recalls his own analysis
    console.print("[bold]Bob recalls his analysis:[/bold]")
    bob_analysis = mem.recall("bob", tags=["analysis"], limit=5)
    for m in bob_analysis:
        conf = m.get("effective_confidence", m.get("confidence", 0))
        console.print(f"  [{conf:.0%}] {m['content'][:70]}")
    console.print()

    # Bob shares a memory with Alice
    console.print("[bold]Bob shares his recommendation with Alice:[/bold]")
    bob_decisions = mem.recall("bob", tags=["decision"], limit=1)
    if bob_decisions:
        mem.share_memory("bob", "alice", bob_decisions[0]["id"])
        console.print(f"  [yellow]>>>[/yellow] Shared: {bob_decisions[0]['content'][:60]}")

    # =====================================================================
    section("4. Build LLM Context from Agent Memory")
    # =====================================================================

    # Build context for Alice with her memories pre-loaded
    hub = mem.build_context(
        agent_id="alice",
        system_prompt=(
            "You are a research analyst reviewing Tesla. "
            "Use your memories and shared insights to form a view."
        ),
        memory_limit=10,
        max_tokens=4000,
    )
    messages = hub.to_messages()
    tokens_est = sum(len(m.get("content", "")) for m in messages) // 4

    table = Table(title="Alice's LLM Context (with memories)", box=box.ROUNDED)
    table.add_column("Message", style="cyan")
    table.add_column("Role", style="green")
    table.add_column("Size", justify="right")
    for i, m in enumerate(messages):
        label = "System" if i == 0 else f"Memory context {i}"
        table.add_row(label, m.get("role", "?"), f"{len(m.get('content', '')):,} chars")
    console.print(table)
    console.print(f"  ~{tokens_est:,} tokens ready for any LLM API")

    # =====================================================================
    section("5. Memory Lifecycle")
    # =====================================================================

    # Update a memory (versioning)
    console.print("[bold]Memory versioning:[/bold]")
    alice_facts = mem.recall("alice", tags=["fact", "tesla", "fsd"], limit=1)
    if alice_facts:
        old_content = alice_facts[0]["content"]
        new_id = mem.update_memory(
            "alice",
            alice_facts[0]["id"],
            "Tesla FSD v13 rollout confirmed for Q1 2027 after NHTSA approval",
            reason="new_information",
        )
        console.print(f"  [dim]Old:[/dim] {old_content[:60]}")
        console.print(f"  [green]New:[/green] Tesla FSD v13 rollout confirmed for Q1 2027 after NHTSA approval")
        console.print(f"  [dim]Reason: new_information, old version superseded[/dim]")
    console.print()

    # Auto-consolidate similar memories
    console.print("[bold]Auto-consolidation:[/bold]")
    result = mem.auto_consolidate("alice", overlap_threshold=0.3)
    console.print(f"  Consolidated {result.get('consolidated_count', 0)} memory groups")

    # =====================================================================
    section("6. Graph State")
    # =====================================================================

    all_nodes = db.get_all_nodes()
    all_edges = db.get_all_edges()

    label_counts = {}
    for n in all_nodes:
        lbl = getattr(n, "label", "?")
        label_counts[lbl] = label_counts.get(lbl, 0) + 1

    table = Table(title="Shared Memory Graph", box=box.ROUNDED)
    table.add_column("Type", style="cyan")
    table.add_column("Count", justify="right", style="green")
    for lbl, count in sorted(label_counts.items(), key=lambda x: -x[1]):
        table.add_row(lbl, str(count))
    table.add_row("[bold]Total Nodes", f"[bold]{len(all_nodes)}")
    table.add_row("[bold]Total Edges", f"[bold]{len(all_edges)}")
    console.print(table)

    # =====================================================================
    section("Summary")
    # =====================================================================

    elapsed = time.time() - t0
    console.print(Panel.fit(
        f"[bold green]Demo Complete[/bold green]\n\n"
        f"  Agents:         Alice (researcher) + Bob (analyst)\n"
        f"  Memories:       {len(alice_memories) + len(bob_memories)} stored, cross-agent shared\n"
        f"  LLM context:    ~{tokens_est:,} tokens assembled from memory\n"
        f"  Versioning:     Memory updated with supersede chain\n"
        f"  Consolidation:  Similar memories auto-merged\n"
        f"  Time:           {elapsed:.1f}s\n\n"
        f"  [bold]Key insight:[/bold] ContextSynapse is the shared brain.\n"
        f"  Agents remember, recall, and share -- regardless of which\n"
        f"  LLM powers them (Claude, GPT, Llama, Gemini).",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
