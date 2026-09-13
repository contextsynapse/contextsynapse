"""
ContextSynapse Demo
====================
A self-contained walkthrough of ContextSynapse's core features:
  1. Create a knowledge graph with AIQL
  2. Query and filter nodes
  3. Build LLM-ready context

No external services needed — runs entirely in-process.

Prerequisites:
    pip install -e "."

Run:
    python examples/demo.py
"""

import uuid
from contextsynapse import ContextSynapse
from contextsynapse.core.graph_structures import GraphNode, GraphEdge
from contextsynapse.aiql.engine import AIQLExecutor


def main():
    print("=" * 60)
    print("  ContextSynapse Demo")
    print("=" * 60)

    # ── 1. Initialize ────────────────────────────────────────
    db = ContextSynapse()
    ex = AIQLExecutor(contextcore=db)

    # Use a unique graph name to avoid collisions with existing data
    graph_name = f"demo_{uuid.uuid4().hex[:8]}"
    ex.execute(f"CREATE GRAPH {graph_name}")
    ex.execute(f"USE GRAPH {graph_name}")

    # ── 2. Create nodes with AIQL ────────────────────────────
    print("\n1. Creating nodes with AIQL...")

    people = [
        ("Alice Chen", "Engineering Lead"),
        ("Bob Martinez", "Product Manager"),
        ("Carol Okafor", "Data Scientist"),
    ]
    for name, role in people:
        r = ex.execute(f'CREATE NODE Person {{name: "{name}", role: "{role}"}}')
        if r.get("success"):
            print(f"   + Person: {name} ({role})")

    projects = [
        ("Atlas", "active", "Graph-based knowledge engine"),
        ("Beacon", "planning", "Real-time monitoring dashboard"),
    ]
    for name, status, desc in projects:
        r = ex.execute(f'CREATE NODE Project {{name: "{name}", status: "{status}", description: "{desc}"}}')
        if r.get("success"):
            print(f"   + Project: {name} [{status}]")

    # ── 3. Create edges ──────────────────────────────────────
    print("\n2. Creating relationships...")

    edge_defs = [
        ("WORKS_ON", "Person", "Alice Chen", "Project", "Atlas"),
        ("WORKS_ON", "Person", "Carol Okafor", "Project", "Atlas"),
        ("WORKS_ON", "Person", "Bob Martinez", "Project", "Beacon"),
    ]
    for rel, src_label, src_name, tgt_label, tgt_name in edge_defs:
        r = ex.execute(
            f'CREATE EDGE {rel} FROM {src_label} WHERE name = "{src_name}" '
            f'TO {tgt_label} WHERE name = "{tgt_name}"'
        )
        if r.get("success"):
            print(f"   + {src_name} --[{rel}]--> {tgt_name}")
        else:
            print(f"   ! Edge failed: {r.get('error', 'unknown')}")

    # ── 4. Query with AIQL ───────────────────────────────────
    print("\n3. Querying with AIQL...")

    print("\n   SELECT * FROM Person:")
    result = ex.execute("SELECT * FROM Person")
    for node in result.get("nodes", []):
        p = node.get("properties", node) if isinstance(node, dict) else (getattr(node, "properties", {}) or {})
        name = p.get("name", "?")
        role = p.get("role", "?")
        if name != "?":
            print(f"     {name:20s}  {role}")

    print("\n   MATCH active projects:")
    result = ex.execute('MATCH NODE Project WHERE status = "active"')
    for node in result.get("nodes", []):
        p = node.get("properties", node) if isinstance(node, dict) else (getattr(node, "properties", {}) or {})
        print(f"     {p.get('name', '?'):12s}  {p.get('description', '?')}")

    # ── 5. Direct graph API ──────────────────────────────────
    print("\n4. Direct graph API (no AIQL)...")

    # You can also use the graph API directly
    tech = GraphNode(
        id=f"tech_{uuid.uuid4().hex[:8]}",
        label="Technology",
        properties={"name": "Python", "category": "language", "version": "3.12"},
    )
    db.add_node(tech)
    print(f"   Added node via API: {tech.label} — {tech.properties['name']}")

    all_nodes = db.get_all_nodes()
    all_edges = db.get_all_edges()
    print(f"   Total graph: {len(all_nodes)} nodes, {len(all_edges)} edges")

    # ── 6. Build LLM context ────────────────────────────────
    print("\n5. Building LLM-ready context...")
    try:
        from contextsynapse.context.hub import ContextHub
        hub = ContextHub(system_prompt="You are a technical project analyst for Acme Corp.")
        hub.add_nodes(all_nodes)
        messages = hub.to_messages()
        print(f"   Generated {len(messages)} messages for LLM API")
        if messages:
            print(f"   System: \"{messages[0]['content'][:60]}...\"")
        total_chars = sum(len(m.get("content", "")) for m in messages)
        print(f"   Context size: {total_chars:,} characters")
        print("\n   Ready to pass to OpenAI/Anthropic/any LLM API!")
    except Exception as e:
        print(f"   (ContextHub: {e})")

    # ── 7. Show available graphs ─────────────────────────────
    print("\n6. Graph management...")
    result = ex.execute("SHOW GRAPHS")
    graphs = result.get("graphs", [])
    if graphs:
        for g in graphs:
            print(f"     {g}")
    else:
        print(f"     Active graph: {graph_name}")

    # ── Summary ──────────────────────────────────────────────
    print("\n" + "=" * 60)
    print("  Demo complete!")
    print(f"  Graph '{graph_name}': {len(all_nodes)} nodes, {len(all_edges)} edges")
    print()
    print("  Next steps:")
    print("    - Start the API:  uvicorn contextsynapse.api.api:app --port 8000")
    print("    - Start the UI:   cd frontend && npm start")
    print("    - Use as MCP:     python -m contextsynapse.mcp")
    print("=" * 60)


if __name__ == "__main__":
    main()
