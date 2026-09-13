"""
Claude + Codex Bridge via AIContextDB
======================================
Connects Claude (via MCP) and OpenAI Codex (via function calling)
to the SAME shared graph. Both agents read and write to one brain.

Architecture:
    ┌──────────┐     MCP (stdio)      ┌─────────────────┐
    │  Claude   │◄──────────────────►  │                 │
    │  (Code)   │                      │  AIContextDB    │
    └──────────┘                      │  Shared Graph   │
                                      │  (namespace:    │
    ┌──────────┐   OpenAI Tools API   │   "shared")     │
    │  Codex   │◄──────────────────►  │                 │
    │  (OpenAI) │                      └─────────────────┘
    └──────────┘

Setup:
    1. Configure Claude's MCP to point at the AIContextDB MCP server
    2. Run this script to start a Codex agent loop on the same namespace
    3. Both agents share knowledge through the graph

Requirements:
    pip install openai
    export OPENAI_API_KEY=sk-...
"""

from __future__ import annotations

import json
import os
import sys

# Ensure contextsynapse is importable
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from contextsynapse.adapters.openai import configure, create_openai_tools, dispatch_tool_call


# =====================================================================
# 1. Claude MCP Configuration (paste into claude_desktop_config.json
#    or use `claude mcp add`)
# =====================================================================

CLAUDE_MCP_CONFIG = {
    "mcpServers": {
        "contextsynapse": {
            "command": "python",
            "args": ["-m", "contextsynapse.mcp.server", "--namespace", "shared"],
            "cwd": os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
            "env": {
                "PYTHONPATH": os.path.abspath(os.path.join(os.path.dirname(__file__), "..")),
                "AICONTEXTDB_AGENT_NAME": "claude",
            },
        }
    }
}


# =====================================================================
# 2. Codex agent loop — uses OpenAI Chat Completions with tools
# =====================================================================

def run_codex_agent(
    task: str,
    namespace: str = "shared",
    model: str = "o4-mini",
    max_turns: int = 10,
) -> str:
    """Run Codex as a tool-using agent connected to AIContextDB.

    Args:
        task: The task/prompt for Codex to execute.
        namespace: Graph namespace (must match Claude's MCP namespace).
        model: OpenAI model to use.
        max_turns: Max tool-calling rounds before stopping.

    Returns:
        Codex's final text response.
    """
    try:
        from openai import OpenAI
    except ImportError:
        return "Error: pip install openai"

    # Point the adapter at the same namespace Claude uses (skip if already configured)
    from contextsynapse.adapters.openai.tools import _conn as existing_conn
    if existing_conn is None or existing_conn.namespace != namespace:
        configure(namespace=namespace, agent_name="codex")

    client = OpenAI()
    tools = create_openai_tools()

    messages = [
        {
            "role": "system",
            "content": (
                "You are Codex, a coding agent. You have access to a shared knowledge "
                "graph (AIContextDB) that you share with Claude. Use the graph tools to:\n"
                "- Read what Claude has written (search_nodes, query_graph, get_context)\n"
                "- Write your own findings (add_knowledge, log_action)\n"
                "- Link related knowledge (add_relationship)\n"
                "- Check graph state (graph_summary)\n\n"
                "Everything you write is visible to Claude and vice versa."
            ),
        },
        {"role": "user", "content": task},
    ]

    for turn in range(max_turns):
        response = client.chat.completions.create(
            model=model,
            messages=messages,
            tools=tools,
        )

        choice = response.choices[0]

        # If the model is done (no tool calls), return its response
        if choice.finish_reason == "stop" or not choice.message.tool_calls:
            return choice.message.content or "(no response)"

        # Process tool calls
        messages.append(choice.message)
        for tool_call in choice.message.tool_calls:
            result = dispatch_tool_call(
                tool_call.function.name,
                tool_call.function.arguments,
            )
            messages.append({
                "role": "tool",
                "tool_call_id": tool_call.id,
                "content": result,
            })
            print(f"  [codex] {tool_call.function.name} -> {result[:120]}...")

    return messages[-1].get("content", "(max turns reached)")


# =====================================================================
# 3. Demo: seed knowledge from one agent, query from the other
# =====================================================================

def demo_shared_brain():
    """Demonstrate cross-agent collaboration through the shared graph.

    This seeds some knowledge (simulating what Claude might write via MCP),
    then runs Codex to discover and build on it.
    """
    from contextsynapse.adapters._base import AIContextDBConnection

    namespace = "shared"

    # ── Simulate Claude writing knowledge via MCP ───────────────────
    print("=== Simulating Claude (via MCP) writing to shared graph ===\n")
    conn = AIContextDBConnection(namespace=namespace)

    # Claude discovers a bug
    conn.query('CREATE NODE BugReport {title: "Auth token expires silently", severity: "high", file: "auth/token.py", agent: "claude"}')
    print("  Claude created: BugReport — Auth token expires silently")

    # Claude makes a decision
    conn.query('CREATE NODE Decision {text: "Use refresh tokens with sliding window", rationale: "Prevents silent expiry", agent: "claude"}')
    print("  Claude created: Decision — Use refresh tokens with sliding window")

    # Claude logs an action
    conn.query('CREATE NODE AgentAction {action: "code_review", description: "Reviewed auth module, found silent token expiry", agent: "claude"}')
    print("  Claude created: AgentAction — code_review\n")

    # Share the SAME connection so Codex sees Claude's nodes (in-process demo).
    # In production, both agents share via disk persistence or the API server.
    configure(namespace=namespace, agent_name="codex", connection=conn)

    # ── Now run Codex to discover and extend ────────────────────────
    print("=== Running Codex to discover Claude's findings ===\n")

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("  [skip] Set OPENAI_API_KEY to run the Codex agent loop.")
        print("  Codex would see Claude's BugReport, Decision, and AgentAction nodes")
        print("  and could add its own findings/fixes to the same graph.\n")

        # Show what's in the graph via the Codex adapter
        from contextsynapse.adapters.openai.tools import _graph_summary, _search_nodes
        print("  Current graph state:")
        print("  " + _graph_summary().replace("\n", "\n  "))
        print()
        print("  Codex sees Claude's BugReports:")
        print("  " + _search_nodes(label="BugReport").replace("\n", "\n  "))
        print("  Codex sees Claude's Decisions:")
        print("  " + _search_nodes(label="Decision").replace("\n", "\n  "))
        return

    result = run_codex_agent(
        task=(
            "Check the shared graph for any bug reports or decisions that Claude has made. "
            "Summarize what you find, then add your own finding about the auth module: "
            "the token refresh endpoint also needs rate limiting. Log your action."
        ),
        namespace=namespace,
    )
    print(f"\nCodex response:\n{result}")


# =====================================================================
# 4. Setup helper — prints config for both agents
# =====================================================================

def print_setup_instructions():
    """Print setup instructions for connecting Claude and Codex."""
    project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))

    print("""
================================================================
        Claude + Codex Bridge via AIContextDB
================================================================

Both agents connect to the SAME graph namespace ("shared").
Claude writes via MCP, Codex writes via OpenAI function calling.
All knowledge is visible to both.

--- Step 1: Configure Claude (MCP) --------------------------------

Option A -- Claude Code CLI:
""")
    print(f'  claude mcp add contextsynapse -- python -m contextsynapse.mcp.server --namespace shared')

    print(f"""
Option B — claude_desktop_config.json / .claude.json:

{json.dumps(CLAUDE_MCP_CONFIG, indent=2)}

--- Step 2: Configure Codex ----------------------------------------

  export OPENAI_API_KEY=sk-...

  # In your Python code:
  from contextsynapse.adapters.openai import configure, create_openai_tools, dispatch_tool_call

  configure(namespace="shared", agent_name="codex")
  tools = create_openai_tools()
  # Pass tools to openai.chat.completions.create(tools=tools, ...)
  # Dispatch responses with dispatch_tool_call(name, arguments)

--- Step 3: Verify shared access -----------------------------------

  # From Claude (MCP tool):
  graph_summary()

  # From Codex (this script):
  python examples/claude_codex_bridge.py demo

--- How it works ---------------------------------------------------

  Claude --MCP--> AIContextDB (namespace: "shared") <--API--- Codex
                        |
                  Shared Graph
                  +-- Nodes (BugReport, Decision, Finding, ...)
                  +-- Edges (FIXES, DEPENDS_ON, RELATED_TO, ...)
                  +-- Agent Actions (provenance: who did what)

  Both agents see the same nodes and edges.
  Both agents can query, create, and link knowledge.
  Provenance tracking shows which agent wrote what.
""")


# =====================================================================
# CLI
# =====================================================================

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "demo":
        demo_shared_brain()
    elif len(sys.argv) > 1 and sys.argv[1] == "run":
        task = " ".join(sys.argv[2:]) or "Check the shared graph and summarize what's there."
        result = run_codex_agent(task)
        print(result)
    else:
        print_setup_instructions()
