"""
Team Workflow: Claude + Codex collaborating on a project
=========================================================
Demonstrates how two AI agents work together through a shared
project knowledge graph — claiming tasks, making decisions,
handing off work, and building on each other's contributions.

Architecture:
    Claude (MCP) ----+                     +---- Codex (OpenAI)
                     |                     |
                     v                     v
               +---------------------------------+
               |   Project Knowledge Graph       |
               |   (namespace: "my-project")     |
               |                                 |
               |   Tasks:  claim / complete / handoff
               |   Decisions: record / query     |
               |   Code:   track files           |
               |   Actions: provenance trail     |
               +---------------------------------+

Usage:
    python examples/team_workflow.py           # full demo
    python examples/team_workflow.py setup     # print setup instructions
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from contextsynapse.adapters._base import AIContextDBConnection
from contextsynapse.adapters.openai import configure, create_openai_tools, dispatch_tool_call, init_project
from contextsynapse.project.graph import ProjectGraph
from contextsynapse.project.team_tools import init_team, dispatch_team_tool
from contextsynapse.context.agents import AgentRegistry


def demo_team_workflow():
    """Simulate a full team workflow between Claude and Codex."""

    PROJECT = "auth-service-v2"
    print(f"=== Team Workflow Demo: {PROJECT} ===\n")

    # ------------------------------------------------------------------
    # 1. Register both agents
    # ------------------------------------------------------------------
    print("--- Step 1: Register agents ---")
    registry = AgentRegistry()

    claude_agent, claude_key = registry.register(
        name="claude", platform="desktop",
        capabilities=["read", "write"],
        metadata={"adapter": "mcp", "role": "architect"},
    )
    codex_agent, codex_key = registry.register(
        name="codex", platform="app",
        capabilities=["read", "write"],
        metadata={"adapter": "openai", "role": "implementer"},
    )
    print(f"  Claude registered: {claude_agent.agent_id[:12]} (key: {claude_key[:12]}...)")
    print(f"  Codex  registered: {codex_agent.agent_id[:12]} (key: {codex_key[:12]}...)")
    print(f"  Auth check: Claude={registry.authenticate(claude_agent.agent_id, claude_key)}, "
          f"Codex={registry.authenticate(codex_agent.agent_id, codex_key)}")
    print()

    # ------------------------------------------------------------------
    # 2. Create shared project graph
    # ------------------------------------------------------------------
    print("--- Step 2: Create project knowledge graph ---")
    conn = AIContextDBConnection(namespace=PROJECT)
    pg = ProjectGraph(PROJECT, connection=conn, agent_registry=registry)

    # Seed with project docs
    pg.add_document(
        title="Auth Service V2 Spec",
        content="Migrate from session tokens to JWT + refresh tokens. "
                "Add rate limiting, RBAC, and audit logging.",
        doc_type="spec",
        author="product-team",
    )
    print("  Added: Auth Service V2 Spec")

    # ------------------------------------------------------------------
    # 3. Claude creates tasks (acting as architect)
    # ------------------------------------------------------------------
    print("\n--- Step 3: Claude creates task backlog ---")
    init_team(PROJECT, claude_agent.agent_id, "claude", project=pg)

    t1 = pg.add_task("Design JWT token schema", priority="high", tags=["auth", "design"])
    t2 = pg.add_task("Implement token generation endpoint", priority="high",
                     tags=["auth", "backend"], depends_on=[t1])
    t3 = pg.add_task("Implement refresh token rotation", priority="high",
                     tags=["auth", "security"])
    t4 = pg.add_task("Add rate limiting middleware", priority="medium",
                     tags=["security", "middleware"])
    t5 = pg.add_task("Write integration tests for auth flow", priority="medium",
                     tags=["testing"], depends_on=[t2, t3])
    t6 = pg.add_task("Add RBAC role checks to endpoints", priority="medium",
                     tags=["auth", "rbac"])

    tasks = {t1: "JWT schema", t2: "Token endpoint", t3: "Refresh rotation",
             t4: "Rate limiting", t5: "Integration tests", t6: "RBAC"}
    for tid, name in tasks.items():
        print(f"  Task: {name} (id: {tid[:8]})")
    print()

    # ------------------------------------------------------------------
    # 4. Claude claims the design task and completes it
    # ------------------------------------------------------------------
    print("--- Step 4: Claude works on design ---")
    result = pg.claim_task(t1, claude_agent.agent_id)
    print(f"  {result}")

    # Claude makes a decision
    d1 = pg.add_decision(
        title="Use asymmetric JWT (RS256) instead of HS256",
        rationale="Allows token verification without sharing the signing key. "
                  "Public key can be distributed to microservices.",
        agent_id=claude_agent.agent_id,
        impacts=[t1, t2],
    )
    print(f"  Decision: RS256 over HS256 (id: {d1[:8]})")

    # Complete the design task
    result = pg.complete_task(t1, claude_agent.agent_id,
                             summary="Designed JWT schema with RS256, 15-min access tokens, "
                                     "7-day refresh tokens with rotation.")
    print(f"  {result}")
    print()

    # ------------------------------------------------------------------
    # 5. Claude hands off implementation to Codex
    # ------------------------------------------------------------------
    print("--- Step 5: Claude hands off to Codex ---")
    result = pg.handoff_task(t2, from_agent=claude_agent.agent_id,
                            to_agent=codex_agent.agent_id,
                            notes="JWT schema is designed. Use RS256 per decision. "
                                  "Generate key pair on startup, store in env vars.")
    print(f"  {result}")

    result = pg.claim_task(t3, codex_agent.agent_id)
    print(f"  {result}")
    print()

    # ------------------------------------------------------------------
    # 6. Codex works via OpenAI tools
    # ------------------------------------------------------------------
    print("--- Step 6: Codex works on implementation ---")

    # Switch to Codex identity
    configure(namespace=PROJECT, agent_name="codex", auto_register=False)
    import contextsynapse.adapters.openai.tools as _tools
    _tools._agent_id = codex_agent.agent_id
    _tools._api_key = codex_key
    _tools._conn = conn
    init_team(PROJECT, codex_agent.agent_id, "codex", project=pg)

    # Codex checks its tasks
    my_result = dispatch_team_tool("my_tasks", "{}")
    print(f"  {my_result}")
    print()

    # Codex sees Claude's decision
    decisions = conn.get_nodes(label="Decision")
    for d in decisions:
        p = d.properties if hasattr(d, "properties") else d
        print(f"  Codex reads decision: {p.get('title')} (rationale: {p.get('rationale', '')[:60]}...)")
    print()

    # Codex completes the token endpoint
    result = pg.complete_task(t2, codex_agent.agent_id,
                             summary="Implemented /auth/token endpoint with RS256 JWT. "
                                     "Key pair loaded from AICONTEXTDB_JWT_PRIVATE_KEY env var.",
                             files_changed=["auth/token.py", "auth/keys.py", "config.py"])
    print(f"  {result}")

    # Codex makes its own decision
    d2 = pg.add_decision(
        title="Use sliding window for refresh token rotation",
        rationale="Prevents token theft replay — each refresh invalidates the previous token. "
                  "Grace period of 30s for concurrent requests.",
        agent_id=codex_agent.agent_id,
        impacts=[t3],
    )
    print(f"  Decision: Sliding window refresh (id: {d2[:8]})")

    # Complete refresh rotation
    result = pg.complete_task(t3, codex_agent.agent_id,
                             summary="Implemented refresh token rotation with 30s grace period. "
                                     "Old tokens invalidated after new token issued.",
                             files_changed=["auth/refresh.py", "auth/token_store.py"])
    print(f"  {result}")
    print()

    # ------------------------------------------------------------------
    # 7. Check project status
    # ------------------------------------------------------------------
    print("--- Step 7: Project status ---")
    status = pg.get_project_status()
    print(f"  Project: {status['project']}")
    print(f"  Tasks: {status['tasks']['total']} total")
    for s, c in status["tasks"]["by_status"].items():
        print(f"    {s}: {c}")
    print(f"  Decisions: {status['decisions']}")
    if status["agents_working"]:
        print("  Agents working:")
        for aid, titles in status["agents_working"].items():
            a = registry.get(aid)
            name = a.name if a else aid[:8]
            print(f"    {name}: {', '.join(titles)}")
    print()

    # ------------------------------------------------------------------
    # 8. Build agent context (what each agent sees)
    # ------------------------------------------------------------------
    print("--- Step 8: Agent-specific context ---")
    context = pg.build_agent_context(claude_agent.agent_id)
    print("  Claude's context (first 500 chars):")
    for line in context[:500].split("\n"):
        print(f"    {line}")
    print()

    context = pg.build_agent_context(codex_agent.agent_id)
    print("  Codex's context (first 500 chars):")
    for line in context[:500].split("\n"):
        print(f"    {line}")
    print()

    # ------------------------------------------------------------------
    # 9. Provenance trail
    # ------------------------------------------------------------------
    print("--- Step 9: Provenance trail ---")
    provenance = registry.get_provenance(PROJECT)
    if provenance:
        for p in provenance[-8:]:
            agent = registry.get(p["agent_id"])
            name = agent.name if agent else p["agent_id"][:8]
            meta = p["metadata"]
            print(f"  [{p['operation']}] {p['target_type']} by {name} "
                  f"({meta.get('label', meta.get('action', ''))}) at {p['timestamp'][:19]}")
    else:
        print("  (provenance recorded in graph node properties)")
    print()

    # ------------------------------------------------------------------
    # 10. Show remaining work
    # ------------------------------------------------------------------
    print("--- Step 10: Remaining work ---")
    open_tasks = pg.get_open_tasks()
    if open_tasks:
        for t in open_tasks:
            print(f"  [{t['priority'].upper():8s}] {t['title']} (status: {t['status']}, assigned: {t['assigned_to']})")
    else:
        print("  All tasks completed!")
    print()

    print("=== Demo Complete ===")
    print(f"Both agents collaborated on '{PROJECT}' through the shared knowledge graph.")
    print(f"Claude designed ({status['decisions']} decisions), Codex implemented.")
    print(f"All work tracked with agent provenance and task dependencies.")


def print_setup():
    """Print setup instructions for team collaboration."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    print(f"""
================================================================
     Claude + Codex Team Collaboration via AIContextDB
================================================================

Both agents share a PROJECT knowledge graph with:
  - Tasks (claim, complete, handoff between agents)
  - Decisions (recorded with rationale and impact tracking)
  - Documents (specs, design docs)
  - Code files (tracked source files)
  - Provenance (who did what, when, and why)

--- Configure Claude Code (MCP) ------------------------------------

  claude mcp add contextsynapse -- python -m contextsynapse.mcp.server --namespace my-project

  Then in Claude Code:
    > init_project("my-project")    # initialize project graph
    > add_task("Fix auth bug", priority="high", tags="auth,security")
    > claim_task("<task-id>")       # start working
    > complete_task("<task-id>", summary="Fixed the bug", files_changed="auth.py")
    > handoff_task("<task-id>", to_agent="<codex-agent-id>", notes="Need tests")

--- Configure Codex (OpenAI) --------------------------------------

  from contextsynapse.adapters.openai import configure, create_openai_tools, dispatch_tool_call, init_project

  creds = configure(namespace="my-project", agent_name="codex")
  init_project("my-project")

  tools = create_openai_tools()   # includes team tools
  # Pass to openai.chat.completions.create(tools=tools, ...)
  # Dispatch: dispatch_tool_call(name, arguments)

--- Team workflow --------------------------------------------------

  1. Claude creates tasks:     add_task("Implement feature X")
  2. Claude claims a task:     claim_task(task_id)
  3. Claude works + decides:   add_decision("Use approach Y", "Because Z")
  4. Claude hands off:         handoff_task(task_id, codex_id, "Need tests")
  5. Codex sees the handoff:   my_tasks()
  6. Codex reads decisions:    query_graph("SELECT * FROM Decision")
  7. Codex implements + logs:  complete_task(task_id, "Wrote tests")
  8. Both check status:        project_status()
  9. Context for each agent:   get_agent_context()

--- Available team tools (both agents get these) -------------------

  init_project     - Create/load a project knowledge graph
  add_task         - Create a task in the backlog
  claim_task       - Claim an open task to work on
  complete_task    - Mark task done with summary
  handoff_task     - Pass task to another agent with notes
  my_tasks         - See your assigned tasks
  list_tasks       - List all tasks (filterable)
  project_status   - Project overview
  add_decision     - Record a technical decision
  get_agent_context - Personalized context for your agent
""")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "setup":
        print_setup()
    else:
        demo_team_workflow()
