"""
ContextSynapse Quickstart
==========================
Create a graph, add data, and query it — all in Python.

Prerequisites:
    pip install -e "."

Run:
    python examples/quickstart.py
"""

from contextsynapse import ContextSynapse
from contextsynapse.aiql.engine import AIQLExecutor

# 1. Initialize database and executor
db = ContextSynapse()
ex = AIQLExecutor(contextcore=db)

# 2. Create and select a graph
ex.execute("CREATE GRAPH company")
ex.execute("USE GRAPH company")

# 3. Add nodes
ex.execute('CREATE NODE Person {name: "Alice", role: "Engineer", age: 30}')
ex.execute('CREATE NODE Person {name: "Bob", role: "Manager", age: 42}')
ex.execute('CREATE NODE Project {name: "QGraph", status: "active"}')

# 4. Add relationships
ex.execute('CREATE EDGE WORKS_ON FROM Person WHERE name = "Alice" TO Project WHERE name = "QGraph"')
ex.execute('CREATE EDGE MANAGES FROM Person WHERE name = "Bob" TO Project WHERE name = "QGraph"')

# 5. Query data
print("=== All People ===")
result = ex.execute("SELECT * FROM Person")
for node in result.get("nodes", []):
    p = node.get("properties", node) if isinstance(node, dict) else (getattr(node, "properties", {}) or {})
    print(f"  {p.get('name', '?')} — {p.get('role', '?')}")

print("\n=== Match by property ===")
result = ex.execute('MATCH NODE Person WHERE role = "Engineer"')
for node in result.get("nodes", []):
    p = node.get("properties", node) if isinstance(node, dict) else (getattr(node, "properties", {}) or {})
    print(f"  Found: {p.get('name', '?')}")

print("\n=== Show graphs ===")
result = ex.execute("SHOW GRAPHS")
for g in result.get("graphs", []):
    print(f"  {g}")

print("\nDone!")
