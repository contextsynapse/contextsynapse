#!/usr/bin/env python3
"""
Session Graph Demo
==================
Demonstrates the unified session graph where knowledge ingestion and
agent conversations build a single queryable graph.

Two agents collaborate on a project. Their interactions are automatically
graphified — questions, decisions, actions, and topics become graph nodes
linked to knowledge entities via REFERENCES edges.

Run:
    python examples/session_graph_demo.py
"""

import asyncio
import sys
import os
import shutil
import textwrap

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from contextsynapse.core.registry import GraphRegistry
from contextsynapse.core.graph_structures import GraphNode, GraphEdge
from contextsynapse.context.session import ContextSession
from contextsynapse.context.session_graph import SessionGraphBuilder
from contextsynapse.context.hub import ContextHub

# ── Pretty printing helpers ──────────────────────────────────────────

CYAN    = "\033[36m"
GREEN   = "\033[32m"
YELLOW  = "\033[33m"
MAGENTA = "\033[35m"
RED     = "\033[31m"
DIM     = "\033[2m"
BOLD    = "\033[1m"
RESET   = "\033[0m"

def header(text):
    w = 64
    print(f"\n{CYAN}{'=' * w}")
    print(f"  {text}")
    print(f"{'=' * w}{RESET}\n")

def step(n, text):
    print(f"{GREEN}{BOLD}Step {n}:{RESET} {text}")

def agent_msg(agent, role, msg):
    color = YELLOW if "researcher" in agent.lower() else MAGENTA
    arrow = ">>" if role == "user" else "<<"
    print(f"  {color}[{agent}]{RESET} {DIM}{arrow}{RESET} {msg[:100]}{'...' if len(msg) > 100 else ''}")

def show_result(result):
    status = "skipped" if result.skipped else "graphified"
    signals = len(result.signal_node_ids)
    refs = len(result.reference_edge_ids)
    parts = [f"{DIM}({status}"]
    if signals:
        parts.append(f"{signals} signals")
    if refs:
        parts.append(f"{refs} refs")
    parts_str = ", ".join(parts) + f"){RESET}"
    print(f"    {parts_str}")

def show_graph_stats(stats):
    print(f"\n  {BOLD}Graph Stats:{RESET}")
    print(f"    Nodes: {stats['total_nodes']}  |  Edges: {stats['total_edges']}")
    for label, count in sorted(stats["node_counts"].items()):
        bar = "|" * min(count, 30)
        print(f"    {label:18s} {count:3d}  {DIM}{bar}{RESET}")
    print()
    for label, count in sorted(stats["edge_counts"].items()):
        bar = "|" * min(count, 30)
        print(f"    {label:18s} {count:3d}  {DIM}{bar}{RESET}")

def show_context(hub):
    msgs = hub.to_messages()
    tokens = hub.estimate_tokens()
    print(f"\n  {BOLD}Context ({len(msgs)} messages, ~{tokens} tokens):{RESET}")
    for m in msgs:
        role = m["role"]
        content = m["content"]
        lines = content.split("\n")
        first = lines[0][:90]
        if role == "system":
            print(f"    {RED}[system]{RESET} {first}")
        elif role == "user":
            print(f"    {YELLOW}[user]{RESET}   {first}")
        elif role == "assistant":
            print(f"    {MAGENTA}[asst]{RESET}   {first}")
        else:
            print(f"    {DIM}[{role}]{RESET}   {first}")
        for line in lines[1:3]:
            print(f"              {DIM}{line[:85]}{RESET}")
        if len(lines) > 3:
            print(f"              {DIM}... ({len(lines) - 3} more lines){RESET}")


# ── Main demo ────────────────────────────────────────────────────────

async def main():
    header("AIContextDB — Unified Session Graph Demo")

    # Clean up any previous demo data
    demo_dir = "demo_session_data"
    if os.path.exists(demo_dir):
        shutil.rmtree(demo_dir)

    # ── Step 1: Create session with schema ───────────────────────────
    step(1, "Create session with unified graph schema")

    registry = GraphRegistry(storage_dir=demo_dir)
    session = ContextSession(
        session_id="demo-001",
        name="project-alpha",
        created_at="2026-03-16T10:00:00Z",
        updated_at="2026-03-16T10:00:00Z",
        owner_agent_id="researcher",
        graph_namespace="ctx_project_alpha",
    )

    builder = SessionGraphBuilder(session, registry)
    await builder.initialize()

    schema = builder.schema
    print(f"  Schema loaded: {len(schema.node_types)} node types, {len(schema.edge_types)} edge types")
    print(f"  Knowledge layer: Document, TextChunk, Entity, Relationship")
    print(f"  Conversation layer: Turn, Topic, Decision, Question, Action, AgentPresence")

    # ── Step 2: Ingest knowledge (simulate document extraction) ──────
    step(2, "Ingest knowledge layer (simulating document extraction)")

    db = builder.db

    # Simulate entities extracted from a document about system architecture
    entities = [
        ("PostgreSQL", "Database", "Open-source relational database, ACID-compliant"),
        ("Redis", "Cache", "In-memory key-value store for caching and message brokering"),
        ("Auth Service", "Microservice", "Handles user authentication, JWT tokens, session management"),
        ("API Gateway", "Microservice", "Routes requests, rate limiting, load balancing"),
        ("Kubernetes", "Platform", "Container orchestration for deploying and scaling services"),
        ("React", "Framework", "Frontend JavaScript library for building user interfaces"),
        ("GraphQL", "Protocol", "Query language for APIs, alternative to REST"),
    ]

    for name, etype, desc in entities:
        db.add_node(GraphNode(
            id=f"entity_{name.lower().replace(' ', '_')}",
            label="Entity",
            properties={
                "entity_name": name,
                "entity_type": etype,
                "description": desc,
            },
        ))
    print(f"  Ingested {len(entities)} entities into knowledge layer")

    # Add some relationships between entities
    rels = [
        ("entity_auth_service", "entity_postgresql", "USES", "Auth Service stores sessions in PostgreSQL"),
        ("entity_api_gateway", "entity_auth_service", "ROUTES_TO", "Gateway forwards auth requests"),
        ("entity_api_gateway", "entity_redis", "USES", "Gateway uses Redis for rate limiting"),
    ]
    for src, tgt, label, desc in rels:
        db.add_edge(GraphEdge(
            id=f"rel_{src}_{tgt}",
            source=src,
            target=tgt,
            label=label,
            properties={"description": desc},
        ))
    print(f"  Added {len(rels)} knowledge relationships")

    # ── Step 3: Agent conversation (researcher + architect) ──────────
    step(3, "Multi-agent conversation (auto-graphified)")
    print()

    conversation = [
        ("Researcher", "user",
         "I've been looking at our Auth Service architecture. The current PostgreSQL setup "
         "handles about 10K sessions but we're expecting 100K after launch. Should we add "
         "Redis as a caching layer for session tokens?"),

        ("Architect", "assistant",
         "Good question. The Auth Service currently writes sessions directly to PostgreSQL, "
         "which won't scale to 100K concurrent sessions. We decided to use Redis as a "
         "session cache in front of PostgreSQL — reads hit Redis first, writes go to both."),

        ("Researcher", "user",
         "Makes sense. What about the API Gateway? It already uses Redis for rate limiting. "
         "Can we share the same Redis cluster or do we need separate instances?"),

        ("Architect", "assistant",
         "We should use separate Redis instances. The rate limiting data in API Gateway has "
         "different TTLs and eviction policies than session tokens. Sharing would risk "
         "cache pollution. I need to set up the Redis cluster topology for the new instance."),

        ("Researcher", "user",
         "Agreed. Should we also consider switching from REST to GraphQL for the Auth Service API?"),

        ("Architect", "assistant",
         "Let's not change the API protocol right now. The Auth Service REST endpoints are "
         "stable and well-tested. GraphQL would add complexity we don't need for simple "
         "auth flows. Let's keep REST and focus on the caching layer. TODO: document the "
         "Redis cluster config and update the Kubernetes deployment manifests."),

        ("Researcher", "user", "ok sounds good"),

        ("Researcher", "user",
         "One more thing — we need to update the React frontend to handle the new "
         "session token refresh flow since Redis-cached sessions might expire differently."),
    ]

    results = []
    for agent, role, content in conversation:
        agent_id = agent.lower()
        agent_msg(agent, role, content)
        r = await builder.graphify_interaction(
            role=role,
            content=content,
            agent_id=agent_id,
            conversation_id="conv-001",
        )
        show_result(r)
        results.append(r)
    print()

    # ── Step 4: Show graph stats ─────────────────────────────────────
    step(4, "Session graph statistics")
    show_graph_stats(builder.get_graph_stats())

    # ── Step 5: Query the conversation layer ─────────────────────────
    step(5, "Query conversation layer")

    decisions = builder.get_decisions()
    print(f"\n  {BOLD}Decisions ({len(decisions)}):{RESET}")
    for d in decisions:
        print(f"    {YELLOW}*{RESET} {d.get('summary', '')[:90]}")

    topics = builder.get_topics()
    print(f"\n  {BOLD}Topics ({len(topics)}):{RESET}")
    for t in topics[:5]:
        print(f"    {CYAN}#{RESET} {t.get('name', '')} (mentioned {t.get('mention_count', 1)}x)")

    # ── Step 6: Cross-layer traversal ────────────────────────────────
    step(6, "Cross-layer traversal: conversation -> knowledge")

    all_edges = db.get_all_edges()
    ref_edges = [e for e in all_edges if e.label == "REFERENCES"]
    print(f"\n  {BOLD}REFERENCES edges ({len(ref_edges)}):{RESET}")
    for e in ref_edges:
        turn = db.get_node(e.source)
        entity = db.get_node(e.target)
        if turn and entity:
            turn_content = turn.properties.get("content", "")[:50]
            entity_name = entity.properties.get("entity_name", "?")
            agent = turn.properties.get("agent_id", "?")
            print(f"    {DIM}[{agent}]{RESET} \"{turn_content}...\" {CYAN}->{RESET} {BOLD}{entity_name}{RESET}")

    # ── Step 7: Build graph-aware context ────────────────────────────
    step(7, "Build graph-aware context for LLM call")

    hub = builder.build_context(
        agent_id="researcher",
        system_prompt="You are a senior software architect helping plan a system migration.",
        max_tokens=4000,
        recent_turns=10,
    )
    show_context(hub)

    # ── Step 8: Compare with ContextHub.from_session_graph ───────────
    step(8, "Alternative: ContextHub.from_session_graph() (standalone)")

    hub2 = ContextHub.from_session_graph(
        db,
        system_prompt="Summarize the architectural decisions made in this session.",
        max_tokens=2000,
        recent_turns=5,
    )
    print(f"  Built context: {len(hub2.items())} items, ~{hub2.estimate_tokens()} tokens")

    # ── Step 9: Multi-agent view ─────────────────────────────────────
    step(9, "Multi-agent graph structure")

    all_nodes = db.get_all_nodes()
    agents = [n for n in all_nodes if n.label == "AgentPresence"]
    print(f"\n  {BOLD}Agents in session ({len(agents)}):{RESET}")
    for a in agents:
        agent_id = a.properties.get("agent_id", "?")
        # Count their turns
        turns = [n for n in all_nodes if n.label == "Turn" and n.properties.get("agent_id") == agent_id]
        print(f"    {MAGENTA}{agent_id}{RESET}: {len(turns)} turns")

    # Show CONCURRENT_WITH edges
    concurrent = [e for e in all_edges if e.label == "CONCURRENT_WITH"]
    if concurrent:
        print(f"\n  {BOLD}Concurrent turn pairs: {len(concurrent)}{RESET}")

    # Show NEXT_TURN chains
    next_turns = [e for e in all_edges if e.label == "NEXT_TURN"]
    print(f"  {BOLD}NEXT_TURN chain edges: {len(next_turns)}{RESET}")

    # ── Summary ──────────────────────────────────────────────────────
    header("Demo Complete")
    stats = builder.get_graph_stats()
    total_signals = sum(len(r.signal_node_ids) for r in results)
    total_refs = sum(len(r.reference_edge_ids) for r in results)
    print(f"  {GREEN}Session graph built from {len(conversation)} messages{RESET}")
    print(f"  {GREEN}{stats['total_nodes']} nodes, {stats['total_edges']} edges{RESET}")
    print(f"  {GREEN}{total_signals} signals extracted (decisions, questions, actions, topics){RESET}")
    print(f"  {GREEN}{total_refs} cross-layer REFERENCES to knowledge entities{RESET}")
    print(f"  {GREEN}{len(decisions)} decisions, {len(topics)} topics tracked{RESET}")
    print()
    print(f"  The unified graph lets agents traverse from conversation to knowledge:")
    print(f"    {DIM}\"What did we decide about Redis?\" -> Decision node -> REFERENCES -> Redis entity{RESET}")
    print(f"    {DIM}\"What entities were discussed?\" -> Turn nodes -> REFERENCES -> all entities{RESET}")
    print()

    # Cleanup
    shutil.rmtree(demo_dir, ignore_errors=True)


if __name__ == "__main__":
    asyncio.run(main())
