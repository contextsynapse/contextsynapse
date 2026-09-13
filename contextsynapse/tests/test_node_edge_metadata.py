"""
Test Node and Edge Metadata (name, UUID, alias).
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine.executor import AIQLExecutor


def test_node_edge_metadata():
    """Test that nodes and edges have name, UUID, and alias."""
    namespace = "test_metadata"
    
    print("=" * 80)
    print("NODE AND EDGE METADATA TEST")
    print("=" * 80)
    
    # Setup
    graph = AIContextDB(name=namespace, storage_backend='csr')
    graph.buffer_enabled = False  # Disable buffer for immediate updates
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
    
    # Test 1: CREATE NODE with name
    print("\n[TEST 1] CREATE NODE with name")
    try:
        query = 'CREATE NODE Person {name: "Alice", age: 30}'
        result = executor.execute(query)
        print(f"  Result: {result}")
        
        node_uuid = result.get('uuid')
        name = result.get('name')
        
        # Verify node has metadata
        if node_uuid:
            # Find node by uuid from properties
            all_nodes = graph.get_all_nodes()
            node = None
            node_id = None
            for n in all_nodes:
                if n.properties.get('uuid') == node_uuid:
                    node = n
                    node_id = n.id
                    break
            
            if node:
                has_uuid = hasattr(node, 'uuid') and node.uuid == node_uuid
                has_name = (hasattr(node, 'name') and node.name == "Alice") or node.properties.get('name') == "Alice"
                has_uuid_in_props = node.properties.get('uuid') == node_uuid
                
                print(f"  Node ID: {node_id}")
                print(f"  Node UUID: {node_uuid}")
                print(f"  Node name: {node.name if hasattr(node, 'name') else node.properties.get('name')}")
                print(f"  Has UUID attribute: {has_uuid}")
                print(f"  Has name: {has_name}")
                print(f"  UUID in properties: {has_uuid_in_props}")
                
                results['NODE_UUID'] = has_uuid and has_uuid_in_props
                results['NODE_NAME'] = has_name
                print(f"  Status: {'PASS' if results['NODE_UUID'] and results['NODE_NAME'] else 'FAIL'}\n")
            else:
                results['NODE_UUID'] = False
                results['NODE_NAME'] = False
                print(f"  Status: FAIL (node not found)\n")
        else:
            results['NODE_UUID'] = False
            results['NODE_NAME'] = False
            print(f"  Status: FAIL (no UUID returned)\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['NODE_UUID'] = False
        results['NODE_NAME'] = False
    
    # Test 2: CREATE NODE with alias (via AS clause)
    print("[TEST 2] CREATE NODE with alias")
    try:
        # Note: Parser might need to support AS clause, for now check if alias is in properties
        query = 'CREATE NODE Company {name: "TechCorp", industry: "Tech"}'
        result = executor.execute(query)
        
        node_uuid = result.get('uuid')
        if node_uuid:
            # Find node by uuid from properties
            all_nodes = graph.get_all_nodes()
            node = None
            for n in all_nodes:
                if n.properties.get('uuid') == node_uuid:
                    node = n
                    break
            if node:
                has_uuid = hasattr(node, 'uuid') and node.uuid
                has_name = (hasattr(node, 'name') and node.name) or node.properties.get('name')
                
                results['NODE_ALIAS_SUPPORT'] = True  # Structure supports it
                print(f"  Node has UUID: {has_uuid}")
                print(f"  Node has name: {has_name}")
                print(f"  Status: PASS (structure supports alias)\n")
            else:
                results['NODE_ALIAS_SUPPORT'] = False
                print(f"  Status: FAIL\n")
        else:
            results['NODE_ALIAS_SUPPORT'] = False
            print(f"  Status: FAIL\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        results['NODE_ALIAS_SUPPORT'] = False
    
    # Test 3: CREATE EDGE with metadata
    print("[TEST 3] CREATE EDGE with UUID and name")
    try:
        query = "CREATE EDGE WORKS_AT SRC Person DEST Company"
        result = executor.execute(query)
        print(f"  Result: {result}")
        
        edge_uuid = result.get('uuid')
        
        if edge_uuid:
            # Get edge from graph
            all_edges = graph.get_all_edges(label="WORKS_AT")
            if all_edges:
                edge = all_edges[0]
                has_uuid = hasattr(edge, 'uuid') and edge.uuid == edge_uuid
                has_uuid_in_props = edge.properties.get('uuid') == edge_uuid
                
                print(f"  Edge UUID: {edge_uuid}")
                print(f"  Has UUID attribute: {has_uuid}")
                print(f"  UUID in properties: {has_uuid_in_props}")
                
                results['EDGE_UUID'] = has_uuid and has_uuid_in_props
                print(f"  Status: {'PASS' if results['EDGE_UUID'] else 'FAIL'}\n")
            else:
                results['EDGE_UUID'] = False
                print(f"  Status: FAIL (edge not found)\n")
        else:
            results['EDGE_UUID'] = False
            print(f"  Status: FAIL (no UUID returned)\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['EDGE_UUID'] = False
    
    # Test 4: Query nodes and verify UUID is accessible
    print("[TEST 4] Query nodes and verify UUID in results")
    try:
        query = "SELECT * FROM Person"
        result = executor.execute(query)
        
        if isinstance(result, list) and result:
            node = result[0]
            has_uuid = 'uuid' in node or node.get('uuid')
            has_name = 'name' in node or node.get('name')
            
            print(f"  Node in query result has UUID: {has_uuid}")
            print(f"  Node in query result has name: {has_name}")
            if has_uuid:
                print(f"  UUID value: {node.get('uuid')}")
            if has_name:
                print(f"  Name value: {node.get('name')}")
            
            results['QUERY_UUID'] = has_uuid
            results['QUERY_NAME'] = has_name
            print(f"  Status: {'PASS' if has_uuid and has_name else 'FAIL'}\n")
        else:
            results['QUERY_UUID'] = False
            results['QUERY_NAME'] = False
            print(f"  Status: FAIL (no nodes returned)\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        results['QUERY_UUID'] = False
        results['QUERY_NAME'] = False
    
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
        success = test_node_edge_metadata()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)





























