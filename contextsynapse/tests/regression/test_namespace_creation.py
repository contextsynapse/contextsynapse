#!/usr/bin/env python3
"""Test namespace creation."""
from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine import AIQLExecutor

graph = AIContextDB('regression_test', storage_backend='csr')
executor = AIQLExecutor(contextcore=graph)

# Try different namespace creation syntaxes
queries = [
    "CREATE NAMESPACE regression_test",
    "CREATE NAMESPACE regression_test MODE PERSISTENT",
    "CREATE NAMESPACE IF NOT EXISTS regression_test",
]

for query in queries:
    print(f"\nTrying: {query}")
    result = executor.execute(query)
    print(f"Success: {result.get('success')}")
    print(f"Error: {result.get('error', 'None')}")
    if result.get('success'):
        break

# Now try USE NAMESPACE
print("\nTrying USE NAMESPACE regression_test")
result = executor.execute("USE NAMESPACE regression_test")
print(f"Success: {result.get('success')}")
print(f"Error: {result.get('error', 'None')}")

