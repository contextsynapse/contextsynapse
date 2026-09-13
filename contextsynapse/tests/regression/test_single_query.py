#!/usr/bin/env python3
"""Test a single query to diagnose issues."""
from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine import AIQLExecutor

query = """USE NAMESPACE regression_test;
CREATE NODE TestNode { name: "regression_test", value: 42, active: true }"""

graph = AIContextDB('regression_test', storage_backend='csr')
executor = AIQLExecutor(contextcore=graph)
executor.active_namespace = 'regression_test'

print(f"Query: {query}")
print("Executing...")
result = executor.execute(query)
print(f"Success: {result.get('success')}")
print(f"Error: {result.get('error', 'None')}")
print(f"Result keys: {list(result.keys())}")
print(f"Full result: {result}")

