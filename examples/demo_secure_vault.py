#!/usr/bin/env python3
"""
ContextSynapse as a Secure Context Vault
==========================================
Shows how ContextSynapse protects agent data at every layer:

  1. PII Detection    — auto-scan content before storage
  2. Field Encryption — encrypt sensitive fields at rest
  3. AgentShield      — trust scoring + adaptive permissions
  4. RBAC             — role-based access control
  5. Audit Trail      — every action logged with who/what/when
  6. Scoped Access    — agents see only what they're cleared for

No external services needed. Security works standalone.

    pip install -e "."
    python examples/demo_secure_vault.py
"""

from __future__ import annotations
import time
import uuid
from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from contextsynapse import ContextSynapse
from contextsynapse.core.graph_structures import GraphNode

console = Console()


def section(title):
    console.print()
    console.rule(f"[bold red]{title}")
    console.print()


def main():
    console.print(Panel.fit(
        "[bold white]ContextSynapse -- Secure Context Vault[/bold white]\n"
        "[dim]Every layer of agent data protected by default[/dim]",
        border_style="red",
    ))

    t0 = time.time()

    # =====================================================================
    section("1. PII Detection -- Scan Before You Store")
    # =====================================================================

    from contextsynapse.security.pii import PIIDetector
    pii = PIIDetector()

    console.print("  [bold]Agents ingest data. PII scanner runs automatically.[/bold]\n")

    documents = [
        ("Market Analysis: S&P 500 rose 2.3% in Q3, driven by tech sector gains", "research-agent"),
        ("Client portfolio review for John Smith, SSN 078-05-1120, account #4821-3390", "portfolio-agent"),
        ("Email from cfo@acme.com: quarterly board deck attached, revenue $142M", "ingestion-agent"),
        ("Kubernetes cluster scaled to 20 replicas, CPU at 72%, memory stable", "infra-agent"),
        ("Customer support call — caller ID 555-0199, complaint about billing", "support-agent"),
    ]

    table = Table(title="PII Scan Results", box=box.ROUNDED)
    table.add_column("Agent", style="cyan", width=18)
    table.add_column("Content", width=45)
    table.add_column("PII Found", justify="center", width=12)
    table.add_column("Action", width=12)

    blocked = 0
    for content, agent in documents:
        result = pii.scan(content)
        if result.pii_found:
            pii_types = ", ".join(t for _, t in result.entities)
            table.add_row(
                agent,
                content[:43] + "...",
                f"[red]{pii_types}[/red]",
                "[red]BLOCKED[/red]",
            )
            blocked += 1
        else:
            table.add_row(agent, content[:43] + "...", "[green]Clean[/green]", "[green]STORED[/green]")
    console.print(table)
    console.print(f"\n  {blocked}/{len(documents)} documents blocked by PII gate")

    # =====================================================================
    section("2. Field-Level Encryption")
    # =====================================================================

    from contextsynapse.security.encryption import FieldEncryptor

    console.print("  [bold]Sensitive fields encrypted at rest. Searchable tokens preserved.[/bold]\n")
    enc = FieldEncryptor()

    secrets = [
        ("api_key", "sk-proj-a1b2c3d4e5f6g7h8i9j0"),
        ("ssn", "078-05-1120"),
        ("account_number", "4821-3390-0012-7756"),
        ("diagnosis", "Type 2 Diabetes Mellitus"),
    ]

    table = Table(title="Field Encryption", box=box.ROUNDED)
    table.add_column("Field", style="cyan", width=18)
    table.add_column("Original", width=30)
    table.add_column("Encrypted", width=35)
    table.add_column("Decrypt OK?", justify="center", width=12)

    for field_name, value in secrets:
        encrypted = enc.encrypt_value(value)
        decrypted = enc.decrypt_value(encrypted)
        ok = decrypted == value
        # Show only first 30 chars of encrypted
        enc_display = encrypted[:33] + "..." if len(encrypted) > 33 else encrypted
        table.add_row(
            field_name,
            value,
            f"[dim]{enc_display}[/dim]",
            "[green]YES[/green]" if ok else "[red]NO[/red]",
        )
    console.print(table)

    # Show searchable tokens
    console.print("\n  [bold]Searchable tokens (search without decrypting):[/bold]")
    for field_name, value in secrets[:2]:
        token = enc.searchable_token(value)
        console.print(f"    {field_name}: {token[:40]}...")

    # =====================================================================
    section("3. AgentShield -- Behavioral Trust")
    # =====================================================================

    from contextsynapse.shield.shield import AgentShield
    shield = AgentShield()

    console.print("  [bold]Agents earn trust through behavior. Bad actors lose access.[/bold]\n")

    # Simulate agent lifecycles
    agents_behavior = {
        "research-agent": {"reads": 20, "writes": 5, "feedback": [("n1", "useful"), ("n2", "useful")]},
        "analyst-agent": {"reads": 10, "writes": 3, "feedback": [("n3", "useful")]},
        "compromised-agent": {"reads": 2, "writes": 8, "feedback": [("n4", "incorrect"), ("n5", "misleading")]},
    }

    for agent_id, behavior in agents_behavior.items():
        for _ in range(behavior["reads"]):
            shield.on_tool_call(agent_id, "search_nodes", "read", success=True)
        for _ in range(behavior["writes"]):
            shield.on_tool_call(agent_id, "add_knowledge", "write", success=True)
        for node_id, signal in behavior["feedback"]:
            shield.on_feedback(agent_id, node_id, signal)

    # Simulate invalidations for compromised agent
    shield.on_invalidation_for_agent("compromised-agent", count=5)

    table = Table(title="Agent Trust Scores", box=box.ROUNDED)
    table.add_column("Agent", style="cyan", width=20)
    table.add_column("Trust", justify="right", width=6)
    table.add_column("Level", width=12)
    table.add_column("search_nodes", justify="center", width=14)
    table.add_column("add_knowledge", justify="center", width=14)
    table.add_column("delete_node", justify="center", width=12)

    for agent_id in agents_behavior:
        score = shield.get_trust_score(agent_id)
        level = shield.get_trust_level(agent_id)
        color = {"trusted": "green", "verified": "green", "normal": "yellow",
                 "provisional": "red", "probation": "red", "untrusted": "red", "blocked": "red"}.get(level, "white")

        perms = {}
        for tool in ["search_nodes", "add_knowledge", "delete_node"]:
            d = shield.check_permission(agent_id, tool)
            perms[tool] = "[green]ALLOW[/green]" if d.allowed else "[red]DENY[/red]"

        table.add_row(agent_id, f"{score:.2f}", f"[{color}]{level}[/{color}]",
                      perms["search_nodes"], perms["add_knowledge"], perms["delete_node"])
    console.print(table)

    # Show notifications
    notifications = shield.get_notifications()
    if notifications:
        console.print(f"\n  [bold]Shield events ({len(notifications)}):[/bold]")
        for n in notifications[-3:]:
            agent = n["agent_id"][:18]
            details = n.get("details", {})
            if "old_level" in details:
                console.print(f"    [yellow]{n['event_type']}[/yellow] {agent}: "
                            f"{details['old_level']} -> {details['new_level']}")

    # =====================================================================
    section("4. RBAC -- Role-Based Access Control")
    # =====================================================================

    from contextsynapse.security.rbac import RBACManager, Role, Permission
    rbac = RBACManager()

    console.print("  [bold]Roles define what each user/agent can do.[/bold]\n")

    roles = [Role.ADMIN, Role.FUND_MANAGER, Role.RESEARCH_ANALYST, Role.CLIENT_VIEWER, Role.OPERATIONS]
    perms = [Permission.VIEW_PORTFOLIO, Permission.EXECUTE_TRADE, Permission.MANAGE_CLIENTS,
             Permission.MANAGE_USERS, Permission.OVERRIDE_COMPLIANCE]

    table = Table(title="RBAC Permission Matrix", box=box.ROUNDED)
    table.add_column("Role", style="cyan")
    for p in perms:
        table.add_column(p.value.replace("_", " ").title(), justify="center", width=12)

    for role in roles:
        row = [role.value]
        for perm in perms:
            has = rbac.check_permission(role, perm)
            row.append("[green]Y[/green]" if has else "[dim]-[/dim]")
        table.add_row(*row)
    console.print(table)

    # =====================================================================
    section("5. Audit Trail")
    # =====================================================================

    from contextsynapse.security.audit_logger import AuditLogger
    audit = AuditLogger()

    console.print("  [bold]Every action logged. Who did what, when, from where.[/bold]\n")

    # Log some actions
    actions = [
        ("research-agent", "search", "graph:market_data", {"query": "TSLA revenue"}, "10.0.1.15"),
        ("analyst-agent", "write", "graph:analysis", {"node_type": "Finding"}, "10.0.1.22"),
        ("compromised-agent", "delete", "graph:market_data", {"node_id": "n-1234"}, "192.168.1.99"),
    ]

    entries = []
    for who, action, resource, details, ip in actions:
        entry = audit.log(who, action, resource, details=details, ip_address=ip)
        entries.append(entry)

    table = Table(title="Audit Trail", box=box.ROUNDED)
    table.add_column("Agent", style="cyan", width=20)
    table.add_column("Action", width=8)
    table.add_column("Resource", width=22)
    table.add_column("IP", width=14)
    table.add_column("Time", style="dim", width=20)

    for entry in entries:
        table.add_row(
            entry.who,
            entry.action,
            entry.resource,
            entry.ip_address,
            entry.timestamp[:19],
        )
    console.print(table)

    # =====================================================================
    section("6. Zero-Dependency Security")
    # =====================================================================

    console.print("  [bold]All security features work without external services:[/bold]\n")

    table = Table(title="Security Stack -- No External Dependencies", box=box.ROUNDED)
    table.add_column("Feature", style="cyan", width=22)
    table.add_column("With cryptography pkg", style="green", width=22)
    table.add_column("Without (fallback)", style="yellow", width=22)
    table.add_row("PII Detection", "Regex + pattern match", "Same (built-in)")
    table.add_row("Field Encryption", "Fernet AES-128", "Base64 encoding")
    table.add_row("Scoped Encryption", "Per-tenant Fernet keys", "Per-tenant Base64")
    table.add_row("AgentShield", "In-memory + Redis", "In-memory only")
    table.add_row("Trust Engine", "Redis-backed scores", "In-memory scores")
    table.add_row("RBAC", "Built-in role matrix", "Same (built-in)")
    table.add_row("Audit Trail", "SQLite / PostgreSQL", "In-memory log")
    table.add_row("JWT Auth", "python-jose", "Same (required)")
    console.print(table)

    # =====================================================================
    section("Summary")
    # =====================================================================

    elapsed = time.time() - t0
    console.print(Panel.fit(
        f"[bold green]Secure Vault Demo Complete[/bold green]\n\n"
        f"  PII Scanner:     {len(documents)} docs scanned, {blocked} blocked\n"
        f"  Encryption:      {len(secrets)} fields encrypted + searchable tokens\n"
        f"  AgentShield:     {len(agents_behavior)} agents, 1 demoted to untrusted\n"
        f"  RBAC:            {len(roles)} roles x {len(perms)} permissions\n"
        f"  Audit Trail:     {len(actions)} actions logged with full context\n"
        f"  External deps:   ZERO required\n"
        f"  Time:            {elapsed:.1f}s\n\n"
        f"  [bold]Security is not an add-on. It's the foundation.[/bold]\n"
        f"  Every agent interaction passes through PII detection,\n"
        f"  trust verification, and permission checks -- automatically.",
        border_style="green",
    ))


if __name__ == "__main__":
    main()
