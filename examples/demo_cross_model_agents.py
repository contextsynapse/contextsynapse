#!/usr/bin/env python3
"""
Cross-Model Agent Collaboration Demo
======================================
Shows how agents powered by DIFFERENT LLMs collaborate through
ContextSynapse as their shared brain.

  Agent "Claude" (Anthropic) → researches and stores findings
  Agent "GPT" (OpenAI)       → reads findings and adds analysis

Both agents share the same knowledge graph. ContextSynapse doesn't
care which LLM powers each agent — it's the neutral data layer.

REQUIREMENTS:
  - At least one LLM API key: OPENAI_API_KEY, ANTHROPIC_API_KEY, or GROQ_API_KEY
  - pip install -e ".[llm]"

Without API keys, the demo simulates the LLM calls to show the architecture.

    python examples/demo_cross_model_agents.py
"""

from __future__ import annotations
import os
import time
import uuid
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextsynapse import ContextSynapse
from contextsynapse.core.registry import GraphRegistry
from contextsynapse.context.agent_memory import AgentMemory
from contextsynapse.context.hub import ContextHub

console = Console()


def section(title):
    console.print()
    console.rule(f"[bold cyan]{title}")
    console.print()


def _get_llm(provider):
    """Try to get an LLM client, return None if unavailable."""
    try:
        from contextsynapse.llm import get_llm_client
        return get_llm_client(provider=provider)
    except Exception:
        return None


def main():
    console.print(Panel.fit(
        "[bold white]Cross-Model Agent Collaboration[/bold white]\n"
        "[dim]Claude + GPT sharing a knowledge graph via ContextSynapse[/dim]",
        border_style="magenta",
    ))

    t0 = time.time()

    # ── Setup shared brain ───────────────────────────────────
    reg = GraphRegistry()
    mem = AgentMemory(reg, namespace="cross_model_demo")

    # Detect available LLMs
    llm_anthropic = _get_llm("anthropic")
    llm_openai = _get_llm("openai")
    llm_groq = _get_llm("groq")

    has_any_llm = any([llm_anthropic, llm_openai, llm_groq])
    simulated = not has_any_llm

    if simulated:
        console.print("  [yellow]No LLM API keys found -- running in simulation mode[/yellow]")
        console.print("  [dim]Set ANTHROPIC_API_KEY, OPENAI_API_KEY, or GROQ_API_KEY for live mode[/dim]")
    else:
        providers = []
        if llm_anthropic: providers.append(f"Anthropic ({llm_anthropic.model})")
        if llm_openai: providers.append(f"OpenAI ({llm_openai.model})")
        if llm_groq: providers.append(f"Groq ({llm_groq.model})")
        console.print(f"  [green]LLM providers available:[/green] {', '.join(providers)}")

    # =====================================================================
    section("1. Agent 'Claude' researches a topic")
    # =====================================================================

    console.print("  [bold]Agent Claude[/bold] (powered by Anthropic or Groq)")
    console.print("  Task: Research the impact of AI on software development\n")

    # Claude's research findings (simulated or real)
    if simulated or not (llm_anthropic or llm_groq):
        claude_findings = [
            "GitHub Copilot increases developer productivity by 26-55% according to multiple studies",
            "AI code generation has shifted the bottleneck from writing code to reviewing and testing code",
            "Security vulnerabilities in AI-generated code are 40% more common than human-written code",
            "Companies using AI-assisted development ship features 2x faster but need stronger code review processes",
        ]
        console.print("  [dim](Simulated -- no Anthropic/Groq key)[/dim]")
    else:
        llm = llm_groq or llm_anthropic  # Prefer Groq (faster, free)
        console.print(f"  [green]Using {llm.model} for research...[/green]")
        try:
            response = llm.generate(
                "List 4 key facts about the impact of AI on software development. "
                "Be specific with numbers and sources where possible. "
                "Return each fact as a separate line.",
                system="You are a technology researcher. Be concise and factual.",
                max_tokens=300,
            )
            claude_findings = [line.strip().lstrip("0123456789.-) ") for line in response.strip().split("\n") if line.strip() and len(line.strip()) > 20][:4]
        except Exception as e:
            console.print(f"  [red]LLM error: {e}[/red]")
            claude_findings = ["AI is transforming software development (LLM call failed)"]

    for finding in claude_findings:
        mem.remember("agent-claude", finding, tags=["research", "ai-impact"], confidence=0.90)
        console.print(f"  [green]+[/green] Stored: {finding[:70]}")

    # =====================================================================
    section("2. Agent 'GPT' reads Claude's findings and adds analysis")
    # =====================================================================

    console.print("  [bold]Agent GPT[/bold] (powered by OpenAI or Groq)")
    console.print("  Task: Analyze Claude's findings and add recommendations\n")

    # GPT reads what Claude found
    shared_context = mem.recall("agent-claude", tags=["research"], limit=10)
    console.print(f"  [yellow]<<<[/yellow] Read {len(shared_context)} findings from Agent Claude\n")

    if simulated or not (llm_openai or llm_groq):
        gpt_analysis = [
            "Teams should invest in AI-focused code review training -- the 40% vulnerability increase is a real risk",
            "The 2x shipping speed benefit compounds over time but only with proper testing infrastructure",
            "Recommendation: adopt AI coding tools gradually, starting with non-critical internal tools",
        ]
        console.print("  [dim](Simulated -- no OpenAI/Groq key)[/dim]")
    else:
        llm = llm_openai or llm_groq
        console.print(f"  [green]Using {llm.model} for analysis...[/green]")
        # Build context from Claude's findings
        context_text = "\n".join(f"- {m['content']}" for m in shared_context)
        try:
            response = llm.generate(
                f"Based on these research findings:\n{context_text}\n\n"
                "Provide 3 actionable recommendations for engineering teams. "
                "Be specific and reference the findings above.",
                system="You are a technology strategy advisor. Be concise.",
                max_tokens=300,
            )
            gpt_analysis = [line.strip().lstrip("0123456789.-) ") for line in response.strip().split("\n") if line.strip() and len(line.strip()) > 20][:3]
        except Exception as e:
            console.print(f"  [red]LLM error: {e}[/red]")
            gpt_analysis = ["Analysis pending (LLM call failed)"]

    for analysis in gpt_analysis:
        mem.remember("agent-gpt", analysis, tags=["analysis", "recommendation"], confidence=0.85)
        console.print(f"  [green]+[/green] Stored: {analysis[:70]}")

    # =====================================================================
    section("3. Combined Knowledge -- Both Agents' Work")
    # =====================================================================

    all_claude = mem.recall("agent-claude", limit=10)
    all_gpt = mem.recall("agent-gpt", limit=10)

    table = Table(title="Shared Knowledge Graph", box=box.ROUNDED)
    table.add_column("Agent", style="cyan", width=14)
    table.add_column("Type", style="green", width=14)
    table.add_column("Content", width=60)
    table.add_column("Conf", justify="right", width=5)

    for m in all_claude:
        tags = ", ".join(m.get("tags", []))
        conf = m.get("effective_confidence", m.get("confidence", 0))
        table.add_row("Claude", tags, m["content"][:58], f"{conf:.0%}")
    for m in all_gpt:
        tags = ", ".join(m.get("tags", []))
        conf = m.get("effective_confidence", m.get("confidence", 0))
        table.add_row("GPT", tags, m["content"][:58], f"{conf:.0%}")
    console.print(table)

    # =====================================================================
    section("4. Assemble Combined Context for Any LLM")
    # =====================================================================

    hub = mem.build_context(
        agent_id="agent-claude",
        system_prompt=(
            "You are a senior technology advisor preparing a briefing. "
            "You have access to research findings and strategic analysis "
            "from your team of AI agents. Synthesize their work."
        ),
        memory_limit=20,
    )

    # Also add GPT's memories
    for m in all_gpt:
        hub.add_text(m["content"], role="background", label=f"Agent GPT: {', '.join(m.get('tags', []))}")

    messages = hub.to_messages()
    tokens_est = sum(len(m.get("content", "")) for m in messages) // 4

    console.print(f"  [bold]Combined context from both agents:[/bold]")
    console.print(f"    Messages:  {len(messages)}")
    console.print(f"    Tokens:    ~{tokens_est:,}")
    console.print(f"    Sources:   Agent Claude ({len(all_claude)} findings) + Agent GPT ({len(all_gpt)} analyses)")
    console.print()
    console.print("  This context can now be sent to [bold]any[/bold] LLM:")
    console.print("    - Claude for synthesis")
    console.print("    - GPT for executive summary")
    console.print("    - Llama for open-source deployment")
    console.print("    - Gemini for multimodal integration")

    # =====================================================================
    section("Summary")
    # =====================================================================

    elapsed = time.time() - t0
    mode = "SIMULATED" if simulated else "LIVE"
    console.print(Panel.fit(
        f"[bold green]Cross-Model Demo Complete ({mode})[/bold green]\n\n"
        f"  Agent Claude:    {len(claude_findings)} research findings stored\n"
        f"  Agent GPT:       {len(gpt_analysis)} analyses added\n"
        f"  Shared memory:   {len(all_claude) + len(all_gpt)} total memories\n"
        f"  Combined tokens: ~{tokens_est:,}\n"
        f"  Time:            {elapsed:.1f}s\n\n"
        f"  [bold]The point:[/bold]\n"
        f"  ContextSynapse is the shared brain between agents.\n"
        f"  Claude researches, GPT analyzes, Llama summarizes --\n"
        f"  they all read/write to the same knowledge graph.\n"
        f"  No vendor lock-in. Any LLM can participate.",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
