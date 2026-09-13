"""
Comprehensive test for all CREATE NODE and CREATE EDGE queries.

Tests:
1. CREATE NODE with various property formats
2. CREATE EDGE with various formats
3. Quote stripping
4. UUID handling
5. Name property handling
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine.executor import AIQLExecutor
import json


def test_all_create_queries():
    """Test all CREATE NODE and CREATE EDGE query variations."""
    print("=" * 80)
    print("COMPREHENSIVE CREATE QUERIES TEST")
    print("=" * 80)
    
    namespace = "test_all_create"
    config = {'buffer': {'enabled': False}}
    graph = AIContextDB(name=namespace, storage_backend='csr', config=config)
    executor = AIQLExecutor(contextcore=graph)
    executor.active_namespace = namespace
    
    # Create namespace
    executor.execute(f'CREATE NAMESPACE {namespace}')
    executor.execute(f'USE NAMESPACE {namespace}')
    
    results = {}
    
    # Test 1: CREATE NODE with single quotes
    print("\n[TEST 1] CREATE NODE with single quotes")
    print("  Query: CREATE NODE Person {name: 'Bob', age: 25}")
    result1 = executor.execute("CREATE NODE Person {name: 'Bob', age: 25}")
    results['node_single_quotes'] = result1
    if result1.get('success'):
        name = result1.get('name', '')
        props_name = result1.get('properties', {}).get('name', '')
        has_quotes = name.startswith("'") or name.startswith('"')
        print(f"  [{'PASS' if not has_quotes else 'FAIL'}] Name clean: '{name}' (quotes: {has_quotes})")
        print(f"  [{'PASS' if 'uuid' in result1 else 'FAIL'}] UUID field: {'uuid' if 'uuid' in result1 else 'node_uuid'}")
    else:
        print(f"  [FAIL] Query failed: {result1.get('error')}")
    
    # Test 2: CREATE NODE with double quotes
    print("\n[TEST 2] CREATE NODE with double quotes")
    print('  Query: CREATE NODE Person {name: "Charlie", age: 30}')
    result2 = executor.execute('CREATE NODE Person {name: "Charlie", age: 30}')
    results['node_double_quotes'] = result2
    if result2.get('success'):
        name = result2.get('name', '')
        has_quotes = name.startswith("'") or name.startswith('"')
        print(f"  [{'PASS' if not has_quotes else 'FAIL'}] Name clean: '{name}' (quotes: {has_quotes})")
    else:
        print(f"  [FAIL] Query failed: {result2.get('error')}")
    
    # Test 3: CREATE NODE without name
    print("\n[TEST 3] CREATE NODE without name property")
    print("  Query: CREATE NODE Product {price: 100, stock: 50}")
    result3 = executor.execute("CREATE NODE Product {price: 100, stock: 50}")
    results['node_no_name'] = result3
    if result3.get('success'):
        has_uuid = 'uuid' in result3 and result3.get('properties', {}).get('uuid')
        print(f"  [{'PASS' if has_uuid else 'FAIL'}] UUID present: {has_uuid}")
    else:
        print(f"  [FAIL] Query failed: {result3.get('error')}")
    
    # Test 4: CREATE EDGE with SRC/DEST syntax
    print("\n[TEST 4] CREATE EDGE with SRC/DEST syntax")
    all_nodes = graph.get_all_nodes()
    person_nodes = [n for n in all_nodes if n.label == 'Person']
    product_nodes = [n for n in all_nodes if n.label == 'Product']
    
    if person_nodes and product_nodes:
        print("  Query: CREATE EDGE OWNS SRC Person DEST Product {name: 'Ownership'}")
        result4 = executor.execute("CREATE EDGE OWNS SRC Person DEST Product {name: 'Ownership'}")
        results['edge_src_dest'] = result4
        if result4.get('success'):
            has_uuid = 'uuid' in result4
            print(f"  [{'PASS' if has_uuid else 'FAIL'}] UUID field: {'uuid' if has_uuid else 'edge_uuid'}")
            # Check edge in graph
            all_edges = graph.get_all_edges()
            owns_edges = [e for e in all_edges if e.label == 'OWNS']
            if owns_edges:
                edge = owns_edges[0]
                name_clean = edge.name == 'Ownership' and not (edge.name.startswith("'") or edge.name.startswith('"'))
                uuid_consistent = edge.uuid == edge.properties.get('uuid')
                print(f"  [{'PASS' if name_clean else 'FAIL'}] Edge name clean: '{edge.name}'")
                print(f"  [{'PASS' if uuid_consistent else 'FAIL'}] Edge UUID consistent: {uuid_consistent}")
        else:
            print(f"  [FAIL] Query failed: {result4.get('error')}")
    
    # Test 5: CREATE EDGE with FROM/TO syntax (if supported)
    print("\n[TEST 5] CREATE EDGE with node IDs")
    if person_nodes and product_nodes:
        person_id = person_nodes[0].id
        product_id = product_nodes[0].id
        print(f"  Query: CREATE EDGE PURCHASED FROM {person_id} TO {product_id} {{name: 'Purchase'}}")
        result5 = executor.execute(f"CREATE EDGE PURCHASED FROM {person_id} TO {product_id} {{name: 'Purchase'}}")
        results['edge_from_to_ids'] = result5
        if result5.get('success'):
            has_uuid = 'uuid' in result5
            print(f"  [{'PASS' if has_uuid else 'FAIL'}] UUID field: {'uuid' if has_uuid else 'edge_uuid'}")
        else:
            print(f"  [FAIL] Query failed: {result5.get('error')}")
    
    # Test 6: Verify UUID consistency in all nodes
    print("\n[TEST 6] Verify UUID consistency in all created nodes")
    all_nodes = graph.get_all_nodes()
    uuid_issues = []
    for node in all_nodes:
        if node.uuid != node.properties.get('uuid'):
            uuid_issues.append(f"Node {node.id}: uuid={node.uuid}, properties['uuid']={node.properties.get('uuid')}")
    
    if uuid_issues:
        print(f"  [FAIL] Found {len(uuid_issues)} UUID inconsistencies:")
        for issue in uuid_issues:
            print(f"    - {issue}")
    else:
        print(f"  [PASS] All {len(all_nodes)} nodes have consistent UUIDs")
    
    # Test 7: Verify UUID consistency in all edges
    print("\n[TEST 7] Verify UUID consistency in all created edges")
    all_edges = graph.get_all_edges()
    uuid_issues = []
    for edge in all_edges:
        if edge.uuid != edge.properties.get('uuid'):
            uuid_issues.append(f"Edge {edge.id}: uuid={edge.uuid}, properties['uuid']={edge.properties.get('uuid')}")
    
    if uuid_issues:
        print(f"  [FAIL] Found {len(uuid_issues)} UUID inconsistencies:")
        for issue in uuid_issues:
            print(f"    - {issue}")
    else:
        print(f"  [PASS] All {len(all_edges)} edges have consistent UUIDs")
    
    # Test 8: Verify name values are clean (no quotes)
    print("\n[TEST 8] Verify name values are clean (no quotes)")
    name_issues = []
    for node in all_nodes:
        if node.name:
            if node.name.startswith("'") or node.name.startswith('"'):
                name_issues.append(f"Node {node.id}: name has quotes: '{node.name}'")
    
    for edge in all_edges:
        if edge.name:
            if edge.name.startswith("'") or edge.name.startswith('"'):
                name_issues.append(f"Edge {edge.id}: name has quotes: '{edge.name}'")
    
    if name_issues:
        print(f"  [FAIL] Found {len(name_issues)} name quote issues:")
        for issue in name_issues:
            print(f"    - {issue}")
    else:
        total_with_names = sum(1 for n in all_nodes if n.name) + sum(1 for e in all_edges if e.name)
        print(f"  [PASS] All {total_with_names} entities with names have clean values (no quotes)")
    
    # Summary
    print("\n" + "=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    passed = sum(1 for r in results.values() if r.get('success'))
    total = len(results)
    print(f"Queries executed: {total}")
    print(f"Successful: {passed}")
    print(f"Failed: {total - passed}")
    
    return all(results.get('success', False) for results in [results])


if __name__ == "__main__":
    success = test_all_create_queries()
    sys.exit(0 if success else 1)




























