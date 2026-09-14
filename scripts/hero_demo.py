"""Hero Demo — ContextSynapse in action with a Customer Support scenario.

Shows how context transforms customer support from robotic to empathetic.

Usage:
    python scripts/hero_demo.py          # interactive
    python scripts/hero_demo.py --auto   # auto-run
"""
from __future__ import annotations

import argparse
import sys
import os
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

B = "\033[1m"
R = "\033[0m"
D = "\033[2m"
BLUE = "\033[94m"
GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
CYAN = "\033[96m"


def step(num, title, subtitle=""):
    print(f"\n{B}{'─' * 60}{R}")
    print(f"  {B}{BLUE}{num}. {title}{R}")
    if subtitle:
        print(f"  {D}{subtitle}{R}")
    print(f"{B}{'─' * 60}{R}\n")


def wait(auto):
    if not auto:
        input(f"\n  {D}Press Enter...{R}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto", action="store_true")
    args = parser.parse_args()

    print(f"""
{B}
    ContextSynapse — Hero Demo
    Customer Support Without & With Context
{R}""")

    # Seed demo data
    print(f"  {D}Loading demo data...{R}")
    from scripts.seed_demo import main as seed
    seed()

    from contextsynapse.core.hybrid_graph_storage import ContextSynapse
    db = ContextSynapse()

    # ── 1. The Problem ─────────────────────────────────────────
    step(1, "THE PROBLEM — Support Without Context",
         "Agent gets a call. Knows nothing. Customer repeats everything.")

    print(f"""  {RED}Without ContextSynapse:{R}

  Agent: "Thank you for calling. Can I have your account number?"
  Alice: "It's alice@example.com"
  Agent: "What plan are you on?"
  Alice: "Pro. I've told this to 3 agents already."
  Agent: "What seems to be the issue?"
  Alice: "Video buffering. AGAIN. And you double-charged me last week."
  Agent: "Let me look into that... can you hold?"
  Alice: *frustrated*

  {D}The agent has NO context. Alice repeats herself.
  The agent doesn't know about the billing issue.
  The agent doesn't know Alice is a high-value customer about to churn.{R}
""")

    wait(args.auto)

    # ── 2. The Context Graph ───────────────────────────────────
    step(2, "THE CONTEXT GRAPH — Everything Connected",
         "One graph connects customers, tickets, products, knowledge.")

    print(f"  {GREEN}Graph structure:{R}\n")

    nodes = list(db.node_index.values())
    edges = list(db.edge_index.values())
    by_label = {}
    for n in nodes:
        label = getattr(n, 'label', '?')
        by_label.setdefault(label, []).append(n)

    for label, items in sorted(by_label.items()):
        print(f"    {B}{label}{R} ({len(items)})")
        for n in items[:3]:
            name = n.properties.get('name', n.properties.get('title', n.properties.get('subject', n.id)))
            print(f"      {n.id}: {name}")
    print(f"\n    {B}Edges:{R} {len(edges)} relationships")

    edge_types = {}
    for e in edges:
        edge_types.setdefault(getattr(e, 'label', '?'), []).append(e)
    for etype, items in sorted(edge_types.items()):
        print(f"      {etype}: {len(items)}")

    wait(args.auto)

    # ── 3. Context Assembly — What the Agent Sees ──────────────
    step(3, "CONTEXT ASSEMBLY — Agent Opens Alice's Ticket",
         "One API call assembles everything the agent needs.")

    alice = db.node_index.get("cust_alice")
    ticket = db.node_index.get("ticket_001")
    billing = db.node_index.get("ticket_002")
    kb = db.node_index.get("kb_buffering")

    print(f"  {GREEN}With ContextSynapse — agent sees instantly:{R}\n")
    print(f"    {B}Customer:{R}  {alice.properties.get('name')} — {alice.properties.get('plan').replace('prod_', '')} subscriber")
    print(f"    {B}Tenure:{R}   {alice.properties.get('tenure_months')} months | LTV: Rs {alice.properties.get('lifetime_value'):,}")
    print(f"    {B}Sentiment:{R} {RED}{alice.properties.get('sentiment').upper()}{R}")
    print(f"    {B}Pattern:{R}  {YELLOW}Repeat contact — 2 open tickets{R}")
    print()
    print(f"    {B}Current Ticket:{R}")
    print(f"      {ticket.properties.get('subject')}")
    print(f"      Priority: {RED}{ticket.properties.get('priority').upper()}{R} | SLA: {ticket.properties.get('sla_hours')}h")
    print()
    print(f"    {B}Related Ticket:{R}")
    print(f"      {billing.properties.get('subject')}")
    print(f"      Status: {RED}{billing.properties.get('status').upper()}{R} — unresolved billing issue!")
    print()
    print(f"    {B}Suggested KB Article:{R}")
    print(f"      {kb.properties.get('title')} (92% match, {kb.properties.get('helpful_votes')} upvotes)")
    print(f"      {D}{kb.properties.get('content')[:80]}...{R}")

    wait(args.auto)

    # ── 4. The Difference ──────────────────────────────────────
    step(4, "THE DIFFERENCE — Same Call, With Context",
         "Agent has full context BEFORE picking up the phone.")

    print(f"""  {GREEN}With ContextSynapse:{R}

  Agent: "Hi Alice, I see you're calling about buffering on your Samsung TV.
          I also noticed we double-charged you last week — that shouldn't
          have happened. Let me fix both right now."

  Alice: "Oh wow, you already know about the billing issue?"

  Agent: "Yes, I can see your full history. For the buffering, our KB says
          to try clearing the app cache and checking your CDN region.
          For the billing, I'm processing your refund now AND adding
          a free month for the trouble."

  Alice: "That's... actually amazing. Thank you."

  {B}Result:{R}
    {GREEN}Resolution time: 4 minutes (vs 25 minutes without context){R}
    {GREEN}Customer sentiment: frustrated -> satisfied{R}
    {GREEN}Churn risk: HIGH -> LOW{R}
    {GREEN}NPS impact: +2 (Alice tells a friend){R}
""")

    wait(args.auto)

    # ── 5. Access Control — Different Roles, Different Views ───
    step(5, "ACCESS CONTROL — PII Masking by Role",
         "Same customer record, different views based on role.")

    from contextsynapse.security.field_encryption import get_field_encryptor
    enc = get_field_encryptor()

    record = {"name": "Alice Chen", "email": "alice@example.com", "phone": "+91-98765-11111"}

    admin_view = enc.process_record(record, "customers", user_roles=["support_admin"])
    agent_view = enc.process_record(record, "customers", user_roles=["agent"])

    print(f"    {B}Admin sees:{R}    name={admin_view['name']}  email={admin_view['email']}  phone={admin_view['phone']}")
    print(f"    {B}Agent sees:{R}    name={agent_view['name']}  email={agent_view['email']}  phone={YELLOW}{agent_view['phone']}{R}")
    print()
    print(f"    {D}PII encryption + role-based masking. Configured per vertical, enforced by platform.{R}")

    wait(args.auto)

    # ── 6. Workflow — Escalation ───────────────────────────────
    step(6, "WORKFLOWS — Ticket Escalation",
         "Agent escalates billing ticket. Auto-checks run. Senior agent approves.")

    from contextsynapse.workflow.engine import WorkflowEngine
    engine = WorkflowEngine()

    print(f"    Agent escalates ticket_002 (double charge)...")
    task = engine.initiate(
        workflow_type="ticket_escalation",
        initiated_by="agent_mike",
        title="Escalate: Alice Chen double-charged (Rs 499)",
        payload={"ticket_id": "ticket_002", "customer": "Alice Chen", "reason": "repeat billing failure"},
    )
    status = task.get("status", "?")
    print(f"    Status: {YELLOW}{status}{R}")

    print(f"\n    Small refund (Rs 499 < Rs 5000 threshold)...")
    refund = engine.initiate(
        workflow_type="refund_request",
        initiated_by="agent_mike",
        title="Refund Rs 499 for Alice Chen",
        payload={"amount": 499, "estimated_value": 499},
    )
    print(f"    Status: {GREEN}{refund.get('status', '?')}{R} (auto-approved — below Rs 5,000)")

    wait(args.auto)

    # ── 7. Graph Traversal — Pattern Detection ─────────────────
    step(7, "GRAPH INTELLIGENCE — Pattern Detection",
         "Traverse the graph to find patterns no single ticket shows.")

    print(f"    {B}Query:{R} Find customers with multiple open tickets + negative sentiment\n")

    at_risk = []
    for n in nodes:
        if getattr(n, 'label', '') != 'Customer':
            continue
        sentiment = n.properties.get('sentiment', '')
        issues = n.properties.get('recent_issues', [])
        if sentiment in ('frustrated', 'at_risk') and len(issues) > 0:
            at_risk.append(n)

    for c in at_risk:
        print(f"    {RED}AT RISK:{R} {B}{c.properties.get('name')}{R}")
        print(f"      Sentiment: {c.properties.get('sentiment')} | LTV: Rs {c.properties.get('lifetime_value'):,}")
        print(f"      Issues: {', '.join(c.properties.get('recent_issues', []))}")
        print(f"      Plan: {c.properties.get('plan').replace('prod_', '')} ({c.properties.get('tenure_months')} months)")
        print()

    print(f"    {D}Graph traversal finds patterns that ticket systems miss.{R}")
    print(f"    {D}An AI agent can proactively reach out BEFORE the customer churns.{R}")

    wait(args.auto)

    # ── 8. The Platform ────────────────────────────────────────
    step(8, "THE PLATFORM — What You Just Saw",
         "Everything above is built on ContextSynapse — the context layer.")

    print(f"""
    {B}ContextSynapse provides:{R}

    {GREEN}1. Graph Database{R}        Nodes + edges + properties (customers, tickets, products)
    {GREEN}2. Context Assembly{R}      One call assembles related data with access control
    {GREEN}3. RBAC Framework{R}        Verticals register their own roles + permissions
    {GREEN}4. PII Encryption{R}        Field-level encryption, role-based masking
    {GREEN}5. Workflow Engine{R}        Approval flows with auto-checks + escalation
    {GREEN}6. Audit Middleware{R}       Every action logged for compliance
    {GREEN}7. Skills Framework{R}       Markdown-defined agent capabilities
    {GREEN}8. LLM Integration{R}        10+ providers auto-detected
    {GREEN}9. MCP Server{R}            Expose as tools for Claude, Copilot, any AI agent
    {GREEN}10. App Factory{R}           Build any vertical — support, healthcare, finance, HR

    {B}The customer support app above was built with ~100 lines of config.{R}
    {B}The platform did the rest.{R}

    {CYAN}pip install contextsynapse{R}
    {CYAN}https://github.com/contextsynapse/contextsynapse{R}
""")


if __name__ == "__main__":
    main()
