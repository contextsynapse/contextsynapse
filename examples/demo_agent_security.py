#!/usr/bin/env python3
"""
Agent Security Demo
====================
Shows ContextSynapse's security stack for AI agents:
  1. AgentShield — trust scoring + adaptive permissions
  2. PII detection — auto-scan before storage
  3. Agent clearance levels — control what agents can see
  4. Audit trail — every action logged
  5. Context ACL — assembled context respects permissions

No LLM or external services needed.

    pip install -e "."
    python examples/demo_agent_security.py
"""

from __future__ import annotations
import time
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

console = Console()


def section(title):
    console.print()
    console.rule(f"[bold cyan]{title}")
    console.print()


def main():
    console.print(Panel.fit(
        "[bold white]Agent Security Demo[/bold white]\n"
        "[dim]How ContextSynapse secures agent communication and data access[/dim]",
        border_style="red",
    ))

    t0 = time.time()

    # =====================================================================
    section("1. AgentShield -- Trust Scoring")
    # =====================================================================

    from contextsynapse.shield.shield import AgentShield
    shield = AgentShield()

    agents = ["research-agent", "analyst-agent", "rogue-agent"]

    # Simulate agent behavior
    console.print("[bold]Simulating agent actions...[/bold]\n")

    # research-agent: good behavior (reads, successful writes)
    for _ in range(10):
        shield.on_tool_call("research-agent", "search_nodes", "read", success=True)
    shield.on_tool_call("research-agent", "add_knowledge", "write", success=True)
    shield.on_tool_call("research-agent", "add_knowledge", "write", success=True)

    # analyst-agent: mostly good, one bad feedback
    for _ in range(5):
        shield.on_tool_call("analyst-agent", "search_nodes", "read", success=True)
    shield.on_tool_call("analyst-agent", "add_knowledge", "write", success=True)
    shield.on_feedback("analyst-agent", "node-123", "useful")

    # rogue-agent: suspicious pattern — lots of writes, some incorrect
    shield.on_tool_call("rogue-agent", "add_knowledge", "write", success=True)
    shield.on_tool_call("rogue-agent", "add_knowledge", "write", success=True)
    shield.on_feedback("rogue-agent", "node-456", "incorrect")
    shield.on_feedback("rogue-agent", "node-789", "misleading")
    shield.on_invalidation_for_agent("rogue-agent", count=3)

    # Show trust scores
    table = Table(title="Agent Trust Scores", box=box.ROUNDED)
    table.add_column("Agent", style="cyan")
    table.add_column("Trust Score", justify="right")
    table.add_column("Trust Level", style="bold")
    table.add_column("Profile", style="dim")

    for agent_id in agents:
        score = shield.get_trust_score(agent_id)
        level = shield.get_trust_level(agent_id)
        profile = shield.get_profile(agent_id)
        tool_calls = profile.get("tool_calls", 0)
        clean = profile.get("consecutive_clean", 0)

        level_color = {"trusted": "green", "normal": "yellow", "probation": "red", "blocked": "red"}.get(level, "white")
        table.add_row(
            agent_id,
            f"{score:.2f}",
            f"[{level_color}]{level}[/{level_color}]",
            f"{tool_calls} calls, {clean} clean streak",
        )
    console.print(table)

    # =====================================================================
    section("2. Adaptive Permissions")
    # =====================================================================

    console.print("[bold]Permission checks based on trust:[/bold]\n")
    tools_to_check = ["search_nodes", "add_knowledge", "delete_node", "admin_reset"]

    for agent_id in agents:
        console.print(f"  [bold]{agent_id}[/bold] (trust: {shield.get_trust_level(agent_id)})")
        for tool in tools_to_check:
            decision = shield.check_permission(agent_id, tool)
            allowed = decision.allowed
            icon = "[green]ALLOW[/green]" if allowed else "[red]DENY[/red]"
            reason = f" -- {decision.reason}" if hasattr(decision, "reason") and decision.reason else ""
            console.print(f"    {tool:20s} {icon}{reason}")
        console.print()

    # =====================================================================
    section("3. Anomaly Detection")
    # =====================================================================

    console.print("[bold]Behavioral anomaly analysis:[/bold]\n")
    for agent_id in agents:
        result = shield.get_anomaly(agent_id, {"session_duration_minutes": 45})
        console.print(f"  [bold]{agent_id}[/bold]")
        console.print(f"    Anomaly score: {result.score:.2f}")
        is_anomalous = result.score > 0.5 or bool(result.flags)
        console.print(f"    Is anomalous:  {'[red]YES[/red]' if is_anomalous else '[green]NO[/green]'}")
        if result.flags:
            for f in result.flags:
                console.print(f"    Flag: [yellow]{f}[/yellow]")
        console.print()

    # =====================================================================
    section("4. PII Gate -- Scan Before Storage")
    # =====================================================================

    from contextsynapse.security.pii import PIIDetector
    pii = PIIDetector()

    ingestion_samples = [
        ("Research finding: Tesla revenue grew 8% YoY to $25.2B", "research-agent"),
        ("Client John Smith (SSN: 123-45-6789) requested portfolio review", "analyst-agent"),
        ("Contact sales@company.com or call 800-555-0199 for pricing", "rogue-agent"),
        ("Market analysis: S&P 500 up 2.3% this quarter", "research-agent"),
    ]

    table = Table(title="PII Scan Gate", box=box.ROUNDED)
    table.add_column("Agent", style="cyan", width=16)
    table.add_column("Content", style="dim", width=50)
    table.add_column("PII?", justify="center")
    table.add_column("Action", style="bold")

    for content, agent_id in ingestion_samples:
        result = pii.scan(content)
        if result.pii_found:
            pii_types = ", ".join(t for _, t in result.entities)
            table.add_row(
                agent_id,
                content[:48] + "...",
                f"[red]YES ({pii_types})[/red]",
                "[red]BLOCKED[/red]",
            )
        else:
            table.add_row(
                agent_id,
                content[:48] + "...",
                "[green]Clean[/green]",
                "[green]STORED[/green]",
            )
    console.print(table)

    # =====================================================================
    section("5. RBAC -- Role-Based Access Control")
    # =====================================================================

    from contextsynapse.security.rbac import RBACManager, Role, Permission
    rbac = RBACManager()

    console.print("[bold]Role permissions matrix:[/bold]\n")
    # Use actual permissions from the enum
    perms = [Permission.VIEW_PORTFOLIO, Permission.EXECUTE_TRADE,
             Permission.MANAGE_CLIENTS, Permission.MANAGE_USERS]
    table = Table(title="RBAC Matrix", box=box.ROUNDED)
    table.add_column("Role", style="cyan")
    for perm in perms:
        table.add_column(perm.value, justify="center")

    for role in [Role.ADMIN, Role.FUND_MANAGER, Role.RESEARCH_ANALYST, Role.CLIENT_VIEWER, Role.OPERATIONS]:
        row = [role.value]
        for perm in perms:
            has = rbac.check_permission(role, perm)
            row.append("[green]Y[/green]" if has else "[red]-[/red]")
        table.add_row(*row)
    console.print(table)

    # =====================================================================
    section("6. Shield Notifications")
    # =====================================================================

    notifications = shield.get_notifications(limit=10)
    if notifications:
        console.print(f"[bold]Recent shield events ({len(notifications)}):[/bold]\n")
        for n in notifications[-5:]:
            evt = n.get("event_type", "?")
            agent = n.get("agent_id", "?")[:16]
            details = n.get("details", {})
            ts = n.get("timestamp", "")[:19]
            console.print(f"  [{ts}] [yellow]{evt}[/yellow] -- {agent}")
            if "old_level" in details:
                console.print(f"    Trust: {details['old_level']} -> {details['new_level']} (trigger: {details.get('trigger','')})")
    else:
        console.print("  [dim]No shield notifications (rogue agent may not have triggered threshold)[/dim]")

    # =====================================================================
    section("Summary")
    # =====================================================================

    elapsed = time.time() - t0
    console.print(Panel.fit(
        f"[bold green]Security Demo Complete[/bold green]\n\n"
        f"  AgentShield:     Trust scoring across {len(agents)} agents\n"
        f"  Permissions:     Adaptive based on trust level\n"
        f"  Anomaly:         Behavioral analysis per agent\n"
        f"  PII Gate:        {len(ingestion_samples)} scans, blocked sensitive data\n"
        f"  RBAC:            5 roles x 4 permissions checked\n"
        f"  Time:            {elapsed:.1f}s\n\n"
        f"  [bold]Key insight:[/bold] ContextSynapse secures agent comms\n"
        f"  at the data layer -- not the transport layer.\n"
        f"  Whether agents connect via MCP, A2A, REST, or SDK,\n"
        f"  the same trust + PII + RBAC rules apply.",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
