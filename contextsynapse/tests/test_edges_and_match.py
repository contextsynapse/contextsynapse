"""
Test Edge Operations and MATCH queries.
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine.executor import AIQLExecutor


def test_edges_and_match():
    """Test edge operations and MATCH queries."""
    namespace = "test_edges_match"
    
    print("=" * 80)
    print("EDGE OPERATIONS AND MATCH QUERIES TEST")
    print("=" * 80)
    
    # Setup
    graph = AIContextDB(name=namespace, storage_backend='csr')
    executor = AIQLExecutor(contextcore=graph)
    executor.active_namespace = namespace
    
    results = {}
    
    # Create namespace
    print("\n[SETUP] Creating namespace...")
    query = f"CREATE NAMESPACE {namespace};"
    result = executor.execute(query)
    print(f"[OK] Namespace created")
    
    query = f"USE NAMESPACE {namespace};"
    result = executor.execute(query)
    print(f"[OK] Using namespace")
    
    # Create nodes
    print("\n[STEP 1] Creating nodes...")
    queries = [
        "CREATE NODE Person {name: \"Alice\", age: 30, city: \"New York\"}",
        "CREATE NODE Person {name: \"Bob\", age: 25, city: \"Boston\"}",
        "CREATE NODE Company {name: \"Tech Corp\", industry: \"Technology\"}",
        "CREATE NODE Company {name: \"Finance Inc\", industry: \"Finance\"}",
    ]
    
    for q in queries:
        result = executor.execute(q)
        print(f"  Created: {result.get('node_type', 'unknown')} - {result.get('success', False)}")
    
    # Test 1: CREATE EDGE
    print("\n[TEST 1] CREATE EDGE")
    try:
        # Use SRC/DEST syntax: CREATE EDGE edge_type SRC source DEST target
        query = "CREATE EDGE WORKS_AT SRC Person DEST Company"
        result = executor.execute(query)
        print(f"  Result: {result}")
        success = result.get('success', False) if isinstance(result, dict) else False
        results['CREATE_EDGE'] = success
        print(f"  Status: {'PASS' if success else 'FAIL'}\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['CREATE_EDGE'] = False
    
    # Test 2: Query edges via get_all_edges
    print("[TEST 2] Query edges via get_all_edges")
    try:
        # Get all edges
        all_edges = graph.get_all_edges(label="WORKS_AT")
        print(f"  Found {len(all_edges)} WORKS_AT edges")
        if all_edges:
            print(f"  First edge: {all_edges[0].source} -> {all_edges[0].target}")
        results['QUERY_EDGES'] = len(all_edges) > 0
        print(f"  Status: {'PASS' if results['QUERY_EDGES'] else 'FAIL'}\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['QUERY_EDGES'] = False
    
    # Test 3: UPDATE EDGE
    print("[TEST 3] UPDATE EDGE")
    try:
        query = "UPDATE EDGE WORKS_AT SET {since: 2020, role: \"Engineer\"}"
        result = executor.execute(query)
        print(f"  Result: {result}")
        # Check if edge_type is extracted correctly
        if not result.get('success', False):
            # Try to extract edge_type from AST
            print(f"  Error details: {result.get('error', 'Unknown error')}")
        success = result.get('success', False) if isinstance(result, dict) else False
        results['UPDATE_EDGE'] = success
        print(f"  Status: {'PASS' if success else 'FAIL'}\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['UPDATE_EDGE'] = False
    
    # Test 4: DELETE EDGE
    print("[TEST 4] DELETE EDGE")
    try:
        # First create another edge to delete
        query = "CREATE EDGE KNOWS SRC Person DEST Person"
        result = executor.execute(query)
        print(f"  Created KNOWS edge: {result.get('success', False)}")
        
        # Delete it
        query = "DELETE EDGE KNOWS"
        result = executor.execute(query)
        print(f"  Result: {result}")
        success = result.get('success', False) if isinstance(result, dict) else False
        deleted_count = result.get('deleted_count', 0) if isinstance(result, dict) else 0
        results['DELETE_EDGE'] = success and deleted_count > 0
        print(f"  Status: {'PASS' if results['DELETE_EDGE'] else 'FAIL'}\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['DELETE_EDGE'] = False
    
    # Test 5: MATCH nodes
    print("[TEST 5] MATCH nodes")
    try:
        # MATCH NODE Person (simple syntax)
        query = "MATCH NODE Person"
        result = executor.execute(query)
        print(f"  Result: {result}")
        if isinstance(result, list):
            nodes = result
        elif isinstance(result, dict) and 'select' in result:
            nodes = result['select']
        else:
            nodes = []
        success = len(nodes) > 0
        results['MATCH_NODES'] = success
        print(f"  Matched {len(nodes)} Person nodes")
        print(f"  Status: {'PASS' if success else 'FAIL'}\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['MATCH_NODES'] = False
    
    # Test 6: MATCH with WHERE
    print("[TEST 6] MATCH with WHERE clause")
    try:
        # Try MATCH NODE Person WHERE ...
        query = "MATCH NODE Person WHERE age > 25"
        result = executor.execute(query)
        print(f"  Result: {result}")
        if isinstance(result, list):
            nodes = result
        elif isinstance(result, dict) and 'select' in result:
            nodes = result['select']
        else:
            nodes = []
        success = len(nodes) > 0
        results['MATCH_WHERE'] = success
        print(f"  Matched {len(nodes)} Person nodes with age > 25")
        print(f"  Status: {'PASS' if success else 'FAIL'}\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['MATCH_WHERE'] = False
    
    # Summary
    print("=" * 80)
    print("TEST SUMMARY")
    print("=" * 80)
    
    passed = sum(1 for v in results.values() if v)
    total = len(results)
    
    for test_name, result in results.items():
        status = "[PASS]" if result else "[FAIL]"
        print(f"{status} {test_name}")
    
    print("\n" + "=" * 80)
    print(f"RESULTS: {passed}/{total} tests passed ({passed*100//total if total > 0 else 0}%)")
    print("=" * 80)
    
    return passed == total


if __name__ == "__main__":
    try:
        success = test_edges_and_match()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

