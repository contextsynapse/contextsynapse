"""Seed Demo — Customer Support Platform built on ContextSynapse.

Creates a realistic customer support scenario showing how the context engine
connects customers, tickets, agents, products, and knowledge — so agents
(human or AI) always have the right context.

Usage:
    python scripts/seed_demo.py
"""
from __future__ import annotations

import logging
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("DATABASE_URL", "postgresql://contextsynapse:contextsynapse@localhost:5432/contextsynapse")

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("seed")


def main():
    logger.info("")
    logger.info("  ContextSynapse Demo — Customer Support Platform")
    logger.info("  ================================================")
    logger.info("")

    # ── 1. Register vertical roles ───────────────────────────────
    logger.info("  [1/5] Registering roles...")
    from contextsynapse.security.rbac_framework import get_role_registry

    reg = get_role_registry()
    reg.register_vertical("support", {
        "support_admin": {
            "permissions": [
                "view_all_tickets", "manage_tickets", "view_customers", "manage_customers",
                "manage_users", "view_audit", "manage_knowledge", "view_analytics",
                "escalate", "override_sla",
            ],
            "label": "Support Admin",
            "description": "Full access — manage agents, customers, SLA, knowledge base",
            "global_access": True,
        },
        "senior_agent": {
            "permissions": [
                "view_all_tickets", "manage_tickets", "view_customers",
                "manage_knowledge", "escalate", "view_analytics",
            ],
            "label": "Senior Agent",
            "description": "Handle any ticket, manage knowledge base, escalate",
            "global_access": True,
        },
        "agent": {
            "permissions": [
                "view_assigned_tickets", "manage_tickets", "view_customers",
                "view_knowledge", "escalate",
            ],
            "label": "Support Agent",
            "description": "Handle assigned tickets, view customer context",
            "global_access": False,
        },
        "customer": {
            "permissions": ["view_own_tickets", "create_ticket"],
            "label": "Customer",
            "description": "View own tickets only",
            "global_access": False,
        },
    })

    reg.register_access_groups({
        "support_team": ["support_admin", "senior_agent", "agent"],
        "management": ["support_admin"],
    })

    logger.info("    4 roles registered (admin, senior_agent, agent, customer)")

    # ── 2. Register PII fields ───────────────────────────────────
    logger.info("  [2/5] Configuring PII encryption...")
    from contextsynapse.security.field_encryption import get_field_encryptor

    enc = get_field_encryptor()
    enc.register("customers", {
        "email": {"mask": "email", "decrypt_roles": ["support_admin", "senior_agent"]},
        "phone": {"mask": "phone", "decrypt_roles": ["support_admin"]},
    })

    logger.info("    2 PII fields configured (email, phone)")

    # ── 3. Register workflows ────────────────────────────────────
    logger.info("  [3/5] Registering workflows...")
    from contextsynapse.workflow.registry import get_workflow_registry

    get_workflow_registry().register_many({
        "ticket_escalation": {
            "label": "Ticket Escalation",
            "auto_checks": ["sla_check"],
            "approvers": ["senior_agent", "support_admin"],
            "auto_approve_below": 0,
            "timeout_hours": 4,
            "vertical": "support",
        },
        "refund_request": {
            "label": "Refund Request",
            "auto_checks": ["order_verification"],
            "approvers": ["support_admin"],
            "auto_approve_below": 5000,  # auto-approve refunds under Rs 5000
            "timeout_hours": 24,
            "vertical": "support",
        },
        "account_deletion": {
            "label": "Account Deletion (GDPR)",
            "auto_checks": ["active_subscriptions"],
            "approvers": ["support_admin"],
            "auto_approve_below": 0,
            "timeout_hours": 72,
            "vertical": "support",
        },
    })

    logger.info("    3 workflows registered (escalation, refund, account deletion)")

    # ── 4. Build the knowledge graph ─────────────────────────────
    logger.info("  [4/5] Building context graph...")

    try:
        from contextsynapse.core.hybrid_graph_storage import ContextSynapse
        from contextsynapse.core.graph_structures import GraphNode, GraphEdge

        db = ContextSynapse()

        # Products
        products = [
            {"id": "prod_streaming", "name": "StreamMax Pro", "category": "Streaming", "price": 499, "tier": "premium"},
            {"id": "prod_basic", "name": "StreamMax Basic", "category": "Streaming", "price": 199, "tier": "basic"},
            {"id": "prod_family", "name": "StreamMax Family", "category": "Streaming", "price": 799, "tier": "family"},
        ]
        for p in products:
            db.add_node(GraphNode(id=p["id"], label="Product", properties=p))

        # Customers (with context — purchase history, preferences, sentiment)
        customers = [
            {
                "id": "cust_alice", "name": "Alice Chen", "email": "alice@example.com",
                "phone": "+91-98765-11111", "plan": "prod_streaming", "tenure_months": 18,
                "lifetime_value": 8982, "sentiment": "frustrated",
                "recent_issues": ["buffering on smart TV", "payment failed twice"],
                "_visibility": "scoped", "_context_type": "atomic",
            },
            {
                "id": "cust_bob", "name": "Bob Patel", "email": "bob@example.com",
                "phone": "+91-98765-22222", "plan": "prod_family", "tenure_months": 36,
                "lifetime_value": 28764, "sentiment": "loyal",
                "recent_issues": [],
                "_visibility": "scoped", "_context_type": "atomic",
            },
            {
                "id": "cust_carol", "name": "Carol D'Souza", "email": "carol@example.com",
                "phone": "+91-98765-33333", "plan": "prod_basic", "tenure_months": 2,
                "lifetime_value": 398, "sentiment": "at_risk",
                "recent_issues": ["can't find content", "app crashes on Android"],
                "_visibility": "scoped", "_context_type": "atomic",
            },
        ]
        for c in customers:
            db.add_node(GraphNode(id=c["id"], label="Customer", properties=c))
            db.add_edge(GraphEdge(
                id=f"sub_{c['id']}", source=c["id"], target=c["plan"],
                label="SUBSCRIBED_TO", properties={"since_months": c["tenure_months"]},
            ))

        # Support tickets (with full context chain)
        tickets = [
            {
                "id": "ticket_001", "subject": "Video buffering on Samsung TV",
                "status": "open", "priority": "high", "category": "streaming",
                "customer_id": "cust_alice", "assigned_to": "agent_mike",
                "sla_hours": 4, "created_hours_ago": 2,
                "messages": [
                    {"from": "customer", "text": "Videos keep buffering every 30 seconds on my Samsung TV. WiFi is fine, Netflix works perfectly."},
                    {"from": "agent", "text": "I can see your account is on our Pro plan. Let me check the CDN logs for your region."},
                ],
                "_context_type": "atomic", "_visibility": "scoped",
            },
            {
                "id": "ticket_002", "subject": "Charged twice for monthly subscription",
                "status": "escalated", "priority": "critical", "category": "billing",
                "customer_id": "cust_alice", "assigned_to": "agent_sarah",
                "sla_hours": 2, "created_hours_ago": 5,
                "messages": [
                    {"from": "customer", "text": "I was charged Rs 499 twice this month. This is the second time this has happened!"},
                    {"from": "agent", "text": "I can confirm the duplicate charge. Escalating to billing team for immediate refund."},
                ],
                "_context_type": "atomic", "_visibility": "scoped",
            },
            {
                "id": "ticket_003", "subject": "App crashes on Android 14",
                "status": "open", "priority": "medium", "category": "app",
                "customer_id": "cust_carol", "assigned_to": "agent_mike",
                "sla_hours": 8, "created_hours_ago": 1,
                "messages": [
                    {"from": "customer", "text": "App crashes immediately after opening on my Pixel 8. Android 14. Reinstalled twice."},
                ],
                "_context_type": "atomic", "_visibility": "scoped",
            },
        ]
        for t in tickets:
            db.add_node(GraphNode(id=t["id"], label="Ticket", properties=t))
            db.add_edge(GraphEdge(
                id=f"raised_{t['id']}", source=t["customer_id"], target=t["id"],
                label="RAISED", properties={"priority": t["priority"]},
            ))

        # Knowledge base articles (connected to products + categories)
        articles = [
            {
                "id": "kb_buffering", "title": "Fix Video Buffering Issues",
                "category": "streaming", "content": "Steps: 1) Check internet speed (min 25Mbps for 4K), 2) Clear app cache, 3) Check CDN region, 4) Try wired connection",
                "helpful_votes": 342, "product": "prod_streaming",
                "_visibility": "public",
            },
            {
                "id": "kb_billing", "title": "Duplicate Charge Resolution Process",
                "category": "billing", "content": "1) Verify in payment gateway, 2) Issue refund within 48h, 3) Send confirmation email, 4) Add credit for inconvenience",
                "helpful_votes": 128, "product": "prod_streaming",
                "internal_note": "Always offer 1 month free credit for duplicate charges",
                "_visibility": "scoped", "_access_groups": ["support_team"],
            },
            {
                "id": "kb_android_crash", "title": "Android 14 Compatibility Fix",
                "category": "app", "content": "Known issue with Android 14 + Pixel devices. Workaround: clear data (not just cache). Fix in v4.2.1 (releasing next week).",
                "helpful_votes": 89, "product": "prod_basic",
                "_visibility": "public",
            },
        ]
        for a in articles:
            db.add_node(GraphNode(id=a["id"], label="KnowledgeArticle", properties=a))
            if a.get("product"):
                db.add_edge(GraphEdge(
                    id=f"about_{a['id']}", source=a["id"], target=a["product"],
                    label="ABOUT", properties={},
                ))

        # Connect tickets to relevant KB articles
        db.add_edge(GraphEdge(id="rel_t1_kb1", source="ticket_001", target="kb_buffering",
                              label="RELEVANT_ARTICLE", properties={"score": 0.92}))
        db.add_edge(GraphEdge(id="rel_t2_kb2", source="ticket_002", target="kb_billing",
                              label="RELEVANT_ARTICLE", properties={"score": 0.95}))
        db.add_edge(GraphEdge(id="rel_t3_kb3", source="ticket_003", target="kb_android_crash",
                              label="RELEVANT_ARTICLE", properties={"score": 0.88}))

        # Connect customer Alice's tickets (shows pattern — repeat issues)
        db.add_edge(GraphEdge(id="pattern_alice", source="ticket_001", target="ticket_002",
                              label="SAME_CUSTOMER", properties={"pattern": "repeat_contact", "frustration": "high"}))

        node_count = len(db.node_index)
        edge_count = len(db.edge_index)
        logger.info(f"    Graph built: {node_count} nodes, {edge_count} edges")
        logger.info("    3 customers, 3 tickets, 3 products, 3 KB articles")
        logger.info("    Connected: tickets ↔ customers ↔ products ↔ knowledge")

    except Exception as e:
        logger.warning(f"    Graph build skipped: {e}")

    # ── 5. Summary ───────────────────────────────────────────────
    logger.info("  [5/5] Demo ready!")
    logger.info("")
    logger.info("  The Context Story:")
    logger.info("  ──────────────────")
    logger.info("  Alice calls about video buffering (ticket_001).")
    logger.info("  The agent sees her FULL context in one view:")
    logger.info("")
    logger.info("    Customer:  Alice Chen — Premium subscriber, 18 months, Rs 8,982 LTV")
    logger.info("    Sentiment: FRUSTRATED (2 recent issues)")
    logger.info("    Ticket:    Video buffering on Samsung TV (HIGH priority, 2h old)")
    logger.info("    History:   Also has an ESCALATED billing ticket (double-charged)")
    logger.info("    Pattern:   Repeat contact — frustration building")
    logger.info("    KB Match:  'Fix Video Buffering Issues' (92% relevant, 342 upvotes)")
    logger.info("    Product:   StreamMax Pro (Rs 499/mo)")
    logger.info("")
    logger.info("  Without context: agent asks 'what plan are you on?' (customer repeats)")
    logger.info("  With context:    agent says 'I see you're on Pro and had billing issues")
    logger.info("                   too — let me fix both and add a free month.'")
    logger.info("")
    logger.info("  That's the difference. Context changes everything.")
    logger.info("")
    logger.info("  Run the hero demo:  python scripts/hero_demo.py")
    logger.info("")


if __name__ == "__main__":
    main()
