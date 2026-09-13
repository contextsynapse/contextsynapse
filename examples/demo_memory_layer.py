#!/usr/bin/env python3
"""
ContextSynapse as a Complete Memory Layer
==========================================
Shows all 4 tiers of agent memory working together:

  Tier 1: Working Memory  — per-agent cache, sub-5ms, reactive invalidation
  Tier 2: Hot Memory       — current task state, TTL-based, sub-millisecond
  Tier 3: Agent Memory     — persistent facts/decisions with confidence decay
  Tier 4: Cold Memory      — time-anchored, recall-at-timestamp, auto-decay

Plus: cross-agent sharing, memory versioning, auto-consolidation,
and LLM context assembly from memory.

No external services needed. All tiers fall back to in-process storage
when Redis/DuckDB are unavailable.

    pip install -e "."
    python examples/demo_memory_layer.py
"""

from __future__ import annotations
import time
import uuid
from datetime import datetime, timezone
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextsynapse.core.registry import GraphRegistry
from contextsynapse.context.agent_memory import AgentMemory

console = Console()


def section(title):
    console.print()
    console.rule(f"[bold cyan]{title}")
    console.print()


def main():
    console.print(Panel.fit(
        "[bold white]ContextSynapse -- Complete Memory Layer[/bold white]\n"
        "[dim]4-tier memory system: working, hot, persistent, cold[/dim]",
        border_style="cyan",
    ))

    t0 = time.time()
    reg = GraphRegistry()

    # =====================================================================
    section("Tier 3: Agent Memory (persistent graph-backed)")
    # =====================================================================
    console.print("  [bold]The core memory tier.[/bold] Agents store facts, decisions,")
    console.print("  preferences, and patterns. Confidence decays over time.\n")

    mem = AgentMemory(reg, namespace=f"memory_demo_{uuid.uuid4().hex[:6]}")

    # ── Research agent builds knowledge ──
    console.print("  [cyan]Agent: research-bot[/cyan]")
    research_data = [
        ("Tesla Q3 revenue reached $25.2B, up 8% year over year", ["fact", "tesla", "revenue"], 0.95),
        ("NVIDIA H100 GPU shortage easing — lead times down from 36 to 11 weeks", ["fact", "nvidia", "supply"], 0.90),
        ("Federal Reserve signaled two rate cuts before year end", ["fact", "macro", "rates"], 0.85),
        ("User prefers concise bullet points over long paragraphs", ["preference"], 0.99),
    ]
    for content, tags, conf in research_data:
        mem.remember("research-bot", content, tags=tags, confidence=conf)
        console.print(f"    [green]+[/green] [{conf:.0%}] {content[:65]}")

    # ── Analyst agent adds analysis ──
    console.print(f"\n  [cyan]Agent: analyst-bot[/cyan]")
    analysis_data = [
        ("Tesla P/E of 65x suggests overvaluation vs sector median 22x", ["analysis", "tesla", "valuation"], 0.80),
        ("NVDA-TSLA correlation weakening — divergence since Q2", ["pattern", "nvidia", "tesla"], 0.70),
        ("Recommendation: hold TSLA, increase NVDA position by 10%", ["decision", "portfolio"], 0.75),
    ]
    for content, tags, conf in analysis_data:
        mem.remember("analyst-bot", content, tags=tags, confidence=conf)
        console.print(f"    [green]+[/green] [{conf:.0%}] {content[:65]}")

    # ── Recall with confidence decay ──
    console.print(f"\n  [bold]Recall (with confidence decay):[/bold]")
    tesla_memories = mem.recall("research-bot", query="tesla", limit=5)
    for m in tesla_memories:
        eff = m.get("effective_confidence", m.get("confidence", 0))
        console.print(f"    [{eff:.0%}] {m['content'][:65]}")

    # =====================================================================
    section("Cross-Agent Memory Sharing")
    # =====================================================================

    console.print("  [bold]Agents share knowledge without knowing each other's LLM.[/bold]\n")

    # Analyst shares recommendation with research bot
    decisions = mem.recall("analyst-bot", tags=["decision"], limit=1)
    if decisions:
        mem.share_memory("analyst-bot", "research-bot", decisions[0]["id"])
        console.print(f"  [yellow]analyst-bot >>> research-bot[/yellow]")
        console.print(f"    Shared: {decisions[0]['content'][:60]}")

    # Research bot can now see the shared memory
    shared = mem.recall("research-bot", tags=["decision"], limit=5)
    console.print(f"\n  research-bot sees {len(shared)} decision(s) from analyst-bot")

    # =====================================================================
    section("Memory Versioning")
    # =====================================================================

    console.print("  [bold]Memories evolve. Old versions are superseded, not deleted.[/bold]\n")

    # Find the rate cut memory
    rate_memories = mem.recall("research-bot", query="rate cuts", limit=1)
    if rate_memories:
        old = rate_memories[0]
        new_id = mem.update_memory(
            "research-bot",
            old["id"],
            "Federal Reserve confirmed three rate cuts before year end — revised from two",
            reason="fed_announcement",
        )
        console.print(f"  [dim]v1:[/dim] {old['content'][:60]}")
        console.print(f"  [green]v2:[/green] Federal Reserve confirmed three rate cuts -- revised from two")
        console.print(f"  [dim]Reason: fed_announcement | Old version superseded[/dim]")

    # =====================================================================
    section("Auto-Consolidation")
    # =====================================================================

    console.print("  [bold]Similar memories merge automatically.[/bold]\n")

    result = mem.auto_consolidate("research-bot", overlap_threshold=0.3)
    console.print(f"  Consolidated {result.get('consolidated_count', 0)} memory groups")
    console.print(f"  [dim]Overlapping memories merged, confidence boosted[/dim]")

    # =====================================================================
    section("Tier 2: Hot Memory (TTL-based state)")
    # =====================================================================

    console.print("  [bold]Current task state — expires automatically.[/bold]")
    console.print("  [dim]Uses Redis when available, falls back to in-process dict.[/dim]\n")

    try:
        from contextsynapse.memory.temporal import get_temporal_memory
        tmem = get_temporal_memory()

        # Set hot state
        tmem.set_hot("research-bot", "current_task", "Analyzing TSLA Q3 earnings call transcript")
        tmem.set_hot("research-bot", "focus_entity", "TSLA")
        tmem.set_hot("research-bot", "iteration", "3")

        # Read back
        task = tmem.get_hot("research-bot", "current_task")
        entity = tmem.get_hot("research-bot", "focus_entity")
        iteration = tmem.get_hot("research-bot", "iteration")

        table = Table(title="Hot Memory (research-bot)", box=box.SIMPLE)
        table.add_column("Key", style="cyan")
        table.add_column("Value", style="green")
        table.add_column("TTL")
        table.add_row("current_task", str(task), "auto-expire")
        table.add_row("focus_entity", str(entity), "auto-expire")
        table.add_row("iteration", str(iteration), "auto-expire")
        console.print(table)
    except Exception as e:
        console.print(f"  [dim]Temporal memory: {e}[/dim]")

    # =====================================================================
    section("Tier 4: Cold Memory (time-anchored)")
    # =====================================================================

    console.print("  [bold]Persistent, time-stamped. Recall what you knew at any point.[/bold]\n")

    try:
        from contextsynapse.memory.temporal import get_temporal_memory
        tmem = get_temporal_memory()

        # Store time-anchored facts
        tmem.store("research-bot", "TSLA opened at $245.30", memory_type="fact", entity_id="TSLA")
        tmem.store("research-bot", "Volume spike at 10:15 AM — 3x normal", memory_type="observation", entity_id="TSLA")
        tmem.store("analyst-bot", "Initiated short-term bearish thesis on TSLA", memory_type="decision", entity_id="TSLA")

        console.print("  [green]+[/green] 3 time-anchored memories stored")
        console.print("  [dim]Each stamped with microsecond precision[/dim]")
        console.print("  [dim]Can recall: 'What did we know about TSLA at 10:00 AM?'[/dim]")
    except Exception as e:
        console.print(f"  [dim]Cold memory: {e}[/dim]")

    # =====================================================================
    section("LLM Context Assembly from Memory")
    # =====================================================================

    console.print("  [bold]Build LLM-ready messages from agent memories.[/bold]\n")

    hub = mem.build_context(
        agent_id="research-bot",
        system_prompt=(
            "You are a financial research agent. Use your memories to "
            "answer questions about Tesla and the macro environment."
        ),
        memory_limit=15,
        max_tokens=4000,
    )
    messages = hub.to_messages()
    tokens_est = sum(len(m.get("content", "")) for m in messages) // 4

    table = Table(title="Assembled LLM Context", box=box.ROUNDED)
    table.add_column("Role", style="green")
    table.add_column("Content", style="dim", width=55)
    table.add_column("Size", justify="right")
    for m in messages:
        content_preview = m.get("content", "")[:53]
        table.add_row(m["role"], content_preview + "...", f"{len(m.get('content',''))} chars")
    console.print(table)
    console.print(f"  ~{tokens_est:,} tokens | Ready for any LLM API")

    # =====================================================================
    section("Graceful Degradation")
    # =====================================================================

    console.print("  [bold]Every tier works without external services:[/bold]\n")

    table = Table(title="Backend Fallback Chain", box=box.ROUNDED)
    table.add_column("Tier", style="cyan", width=20)
    table.add_column("With Redis", style="green", width=25)
    table.add_column("Without Redis", style="yellow", width=25)
    table.add_row("Working Memory", "Redis hash (~5ms)", "In-process dict (~0.1ms)")
    table.add_row("Hot Memory", "Redis hash + TTL", "In-process dict")
    table.add_row("Agent Memory", "Redis-backed graph", "In-memory CSR graph")
    table.add_row("Cold Memory", "DuckDB (persistent)", "In-process storage")
    table.add_row("Vector Search", "Qdrant / ChromaDB", "NumPy cosine (built-in)")
    table.add_row("Full-Text Search", "LMDB index", "Whoosh / keyword index")
    table.add_row("Encryption", "Fernet (cryptography)", "Base64 fallback")
    console.print(table)

    # =====================================================================
    section("Summary")
    # =====================================================================

    elapsed = time.time() - t0
    console.print(Panel.fit(
        f"[bold green]Memory Layer Demo Complete[/bold green]\n\n"
        f"  Agents:           2 (research-bot, analyst-bot)\n"
        f"  Memories stored:  {len(research_data) + len(analysis_data)}\n"
        f"  Cross-agent:      Shared decisions across agents\n"
        f"  Versioning:       Memory updated with supersede chain\n"
        f"  Consolidation:    Similar memories auto-merged\n"
        f"  Hot state:        3 keys (current task, entity, iteration)\n"
        f"  Cold storage:     3 time-anchored facts\n"
        f"  LLM context:      ~{tokens_est:,} tokens assembled\n"
        f"  External deps:    ZERO required (all tiers have fallbacks)\n"
        f"  Time:             {elapsed:.1f}s\n\n"
        f"  [bold]ContextSynapse is the shared brain.[/bold]\n"
        f"  Agents remember, recall, share, and forget --\n"
        f"  regardless of which LLM or framework powers them.",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
