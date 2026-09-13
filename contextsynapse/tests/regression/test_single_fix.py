#!/usr/bin/env python3
"""Test if USE NAMESPACE fix works."""
from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.core.registry import GraphRegistry
from contextsynapse.aiql.engine import AIQLExecutor

print("Testing USE NAMESPACE fix...")

# Create graph registry
gr = GraphRegistry()

# Get graph for namespace
graph = gr.get_graph('test_fix', load_if_missing=True)
if not graph:
    graph = AIContextDB(name='test_fix', storage_backend='csr')

# Create executor
executor = AIQLExecutor(contextcore=graph)
executor.graph_registry = gr
executor.active_namespace = 'test_fix'

# Test 1: Create namespace
print("\n1. Creating namespace...")
result1 = executor.execute("CREATE NAMESPACE test_fix MODE PERSISTENT")
print(f"   Success: {result1.get('success')}")
if not result1.get('success'):
    print(f"   Error: {result1.get('error', 'None')[:200]}")

# Test 2: USE NAMESPACE (this was failing before)
print("\n2. Using namespace...")
result2 = executor.execute("USE NAMESPACE test_fix")
print(f"   Success: {result2.get('success')}")
if not result2.get('success'):
    print(f"   Error: {result2.get('error', 'None')[:200]}")
else:
    print(f"   Message: {result2.get('data', {}).get('message', 'None')[:200]}")

# Test 3: Create node
print("\n3. Creating node...")
result3 = executor.execute("CREATE NODE TestNode { name: 'test', value: 42 }")
print(f"   Success: {result3.get('success')}")
if not result3.get('success'):
    print(f"   Error: {result3.get('error', 'None')[:200]}")

# Test 4: Select node
print("\n4. Selecting node...")
result4 = executor.execute("SELECT * FROM TestNode WHERE name = 'test'")
# result4 could be a list or dict depending on the executor
if isinstance(result4, list):
    print(f"   Success: True")
    print(f"   Found {len(result4)} node(s)")
else:
    print(f"   Success: {result4.get('success')}")
    if not result4.get('success'):
        print(f"   Error: {result4.get('error', 'None')[:200]}")
    else:
        nodes = result4.get('nodes', [])
        print(f"   Found {len(nodes)} node(s)")

print("\n✅ All tests completed!")

