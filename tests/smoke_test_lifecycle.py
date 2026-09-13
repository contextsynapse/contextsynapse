"""
Smoke test: graph/context create → delete → re-create → restart → node persistence.
Run with: python tests/smoke_test_lifecycle.py
"""
import tempfile, os, shutil, sys

tmpdir = tempfile.mkdtemp()
storage = os.path.join(tmpdir, "contextcore_data")
os.makedirs(storage)
db_path = os.path.join(storage, "context.db")

from contextcore.core.registry import GraphRegistry
from contextcore.core.hybrid_graph_storage import GraphNode, GraphEdge
from contextcore.context.context_manager import ContextManager

reg = GraphRegistry(storage_dir=storage)
cm = ContextManager(db_path=db_path, graph_registry=reg)
errors = []

# TEST 1: Create context -> graph exists
print("--- TEST 1: Create context ---")
ctx = cm.create_context("My Project", context_type="knowledge_base")
print(f"  context_id: {ctx.context_id}")
print(f"  graph_namespace: {ctx.graph_namespace}")

g = reg.get_graph(ctx.graph_namespace, load_if_missing=False)
if g is None:
    errors.append("TEST 1: Graph not in registry after context creation")
    print("  FAIL: graph not in registry")
else:
    print(f"  graph in registry: YES ({len(g.node_index)} nodes)")

gnames = [x["name"] for x in reg.list_graphs()]
if ctx.graph_namespace not in gnames:
    errors.append(f"TEST 1: namespace '{ctx.graph_namespace}' not in list_graphs {gnames}")
    print(f"  FAIL: not in list_graphs")
else:
    print("  graph in list_graphs: YES")

root = g.get_node(ctx.context_id) if g else None
if root is None:
    errors.append("TEST 1: Root Context node missing")
    print("  FAIL: root node missing")
else:
    print(f"  root node: label={root.label}")

# TEST 2: Delete context -> graph gone
print("\n--- TEST 2: Delete context ---")
ns1 = ctx.graph_namespace
cid1 = ctx.context_id
cm.delete_context(cid1)

if reg.get_graph(ns1, load_if_missing=False) is not None:
    errors.append("TEST 2: Graph still in registry")
    print("  FAIL: graph still in registry")
else:
    print("  graph in registry: NO (correct)")

if ns1 in [x["name"] for x in reg.list_graphs()]:
    errors.append("TEST 2: Graph still in list_graphs")
    print("  FAIL: still in list_graphs")
else:
    print("  graph in list_graphs: NO (correct)")

row = cm._conn.execute("SELECT * FROM contexts WHERE context_id = ?", (cid1,)).fetchone()
if row:
    errors.append(f"TEST 2: Context row still in DB (status={row['status']})")
    print(f"  FAIL: row in DB")
else:
    print("  context row: DELETED (correct)")

# TEST 3: Re-create same name -> no UNIQUE error
print("\n--- TEST 3: Re-create same name ---")
try:
    ctx2 = cm.create_context("My Project", context_type="knowledge_base")
    print(f"  SUCCESS: namespace={ctx2.graph_namespace}")
except Exception as e:
    errors.append(f"TEST 3: {e}")
    print(f"  FAIL: {e}")

# TEST 4: Create graph directly -> no auto-context
print("\n--- TEST 4: Create standalone graph ---")
reg.create_graph("standalone_graph")
reg.save_graph("standalone_graph", create_checkpoint=False)
row4 = cm._conn.execute(
    "SELECT * FROM contexts WHERE graph_namespace = ?", ("standalone_graph",)
).fetchone()
if row4:
    errors.append("TEST 4: standalone graph auto-created a context")
    print("  FAIL: auto-context created")
else:
    print("  no auto-context: correct")

# TEST 5: Delete graph -> context cascade
print("\n--- TEST 5: Delete graph cascades to context ---")
ctx5 = cm.create_context("Cascade Test")
ns5 = ctx5.graph_namespace
cid5 = ctx5.context_id
reg.delete_graph(ns5, delete_files=True)
row5 = cm._conn.execute("SELECT * FROM contexts WHERE context_id = ?", (cid5,)).fetchone()
if row5:
    errors.append(f"TEST 5: Context not cascade-deleted (status={row5['status']})")
    print(f"  FAIL: context still in DB")
else:
    print("  context cascade-deleted: correct")

# TEST 6: Simulate restart
print("\n--- TEST 6: Restart - no ghosts ---")
if ctx2 and ctx2.graph_namespace in reg.graphs:
    reg.save_graph(ctx2.graph_namespace, create_checkpoint=False)
if "standalone_graph" in reg.graphs:
    reg.save_graph("standalone_graph", create_checkpoint=False)

reg2 = GraphRegistry(storage_dir=storage)
gnames2 = [x["name"] for x in reg2.list_graphs()]
print(f"  graphs after restart: {gnames2}")

# ns1 (my_project) was re-created in TEST 3, so it SHOULD be present
if ns1 not in gnames2:
    errors.append(f"TEST 6: Re-created graph '{ns1}' missing after restart")
    print(f"  FAIL: re-created graph {ns1} missing")
else:
    print(f"  re-created graph '{ns1}' persisted: correct")
if ns5 in gnames2:
    errors.append(f"TEST 6: Ghost graph '{ns5}' reappeared")
    print(f"  FAIL: ghost {ns5}")
if "standalone_graph" not in gnames2:
    errors.append("TEST 6: standalone_graph missing after restart")
    print("  FAIL: standalone_graph missing")
else:
    print("  standalone_graph persisted: correct")

# TEST 7: Node persistence across restart
print("\n--- TEST 7: Node persistence ---")
g7 = reg2.get_graph("standalone_graph")
g7.add_node(GraphNode(id="p1", label="Person", properties={"name": "Alice"}), write_through=True)
g7.add_node(GraphNode(id="p2", label="Person", properties={"name": "Bob"}), write_through=True)
g7.add_edge(GraphEdge(id="e1", source="p1", target="p2", label="KNOWS", properties={}))
reg2.save_graph("standalone_graph", create_checkpoint=False)

reg3 = GraphRegistry(storage_dir=storage)
g7r = reg3.get_graph("standalone_graph")
if g7r is None:
    errors.append("TEST 7: Graph missing after restart")
    print("  FAIL: graph missing")
else:
    n1 = g7r.get_node("p1")
    n2 = g7r.get_node("p2")
    if n1 and n1.properties.get("name") == "Alice":
        print("  Alice loaded: correct")
    else:
        errors.append("TEST 7: Alice missing or wrong")
        print(f"  FAIL: Alice={n1}")
    if n2 and n2.properties.get("name") == "Bob":
        print("  Bob loaded: correct")
    else:
        errors.append("TEST 7: Bob missing or wrong")
        print(f"  FAIL: Bob={n2}")

    meta = [x for x in reg3.list_graphs() if x["name"] == "standalone_graph"][0]
    print(f"  metadata num_nodes: {meta['num_nodes']}")
    if meta["num_nodes"] < 2:
        errors.append(f"TEST 7: metadata shows {meta['num_nodes']} nodes, expected >= 2")

# SUMMARY
print("\n" + "=" * 50)
if errors:
    print(f"FAILED: {len(errors)} error(s)")
    for e in errors:
        print(f"  - {e}")
    sys.exit(1)
else:
    print("ALL 7 TESTS PASSED")

shutil.rmtree(tmpdir, ignore_errors=True)
