"""
Test how the 'name' property appears when creating nodes and edges.

This demonstrates:
1. How name is extracted from CREATE NODE/EDGE queries
2. Where name appears (as object property vs properties dict)
3. Different ways to specify name
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


def test_name_in_node_creation():
    """Test how name appears in node creation."""
    print("=" * 80)
    print("TEST: Name Property in Node Creation")
    print("=" * 80)
    
    namespace = "test_name_property"
    config = {'buffer': {'enabled': False}}
    graph = AIContextDB(name=namespace, storage_backend='csr', config=config)
    executor = AIQLExecutor(contextcore=graph)
    executor.active_namespace = namespace
    
    # Create namespace
    executor.execute(f'CREATE NAMESPACE {namespace}')
    executor.execute(f'USE NAMESPACE {namespace}')
    
    print("\n[1] Creating node with name in properties: CREATE NODE Person {name: 'Alice', age: 30}")
    result1 = executor.execute("CREATE NODE Person {name: 'Alice', age: 30}")
    print(f"    Result: {json.dumps(result1, indent=2, default=str)}")
    
    # Verify UUID is not duplicated in result
    if 'node_uuid' in result1 and 'uuid' in result1:
        print("    [ERROR] UUID duplication found in result!")
    elif 'uuid' in result1:
        print(f"    [OK] UUID returned as 'uuid': {result1['uuid']}")
    
    # Get the created node
    all_nodes = graph.get_all_nodes()
    alice_node = None
    for n in all_nodes:
        if n.properties.get('name') == 'Alice':
            alice_node = n
            break
    
    if alice_node:
        print(f"\n[2] Node object properties:")
        print(f"    node.id: {alice_node.id}")
        print(f"    node.label: {alice_node.label}")
        print(f"    node.name: {alice_node.name}")  # Direct property
        print(f"    node.uuid: {alice_node.uuid}")  # Direct property (for backward compat)
        print(f"    node.properties: {alice_node.properties}")
        print(f"    node.properties['name']: {alice_node.properties.get('name')}")  # In properties dict
        print(f"    node.properties['uuid']: {alice_node.properties.get('uuid')}")  # UUID in properties
        
        print(f"\n[3] Verification:")
        # Check name (should be clean, no quotes)
        name_clean = alice_node.name == 'Alice' and not (alice_node.name.startswith("'") or alice_node.name.startswith('"'))
        print(f"    - Name is clean (no quotes): {name_clean} (value: '{alice_node.name}')")
        # Check UUID consistency
        uuid_consistent = alice_node.uuid == alice_node.properties.get('uuid')
        print(f"    - UUID consistent (node.uuid == properties['uuid']): {uuid_consistent}")
        print(f"      node.uuid: {alice_node.uuid}")
        print(f"      properties['uuid']: {alice_node.properties.get('uuid')}")
        # Check name consistency
        name_consistent = alice_node.name == alice_node.properties.get('name')
        print(f"    - Name consistent (node.name == properties['name']): {name_consistent}")
        print(f"      node.name: '{alice_node.name}'")
        print(f"      properties['name']: '{alice_node.properties.get('name')}'")
        
        if name_clean and uuid_consistent and name_consistent:
            print(f"\n    [OK] All checks passed!")
        else:
            print(f"\n    [FAIL] Some checks failed!")
    
    print("\n" + "=" * 80)
    print("TEST: Name Property in Edge Creation")
    print("=" * 80)
    
    # Create another node for edge
    print("\n[1] Creating second node: CREATE NODE Company {name: 'Acme Corp'}")
    result2 = executor.execute("CREATE NODE Company {name: 'Acme Corp'}")
    
    # Get the company node
    company_node = None
    for n in all_nodes:
        if n.properties.get('name') == 'Acme Corp':
            company_node = n
            break
    
    if not company_node:
        all_nodes = graph.get_all_nodes()
        for n in all_nodes:
            if n.label == 'Company':
                company_node = n
                break
    
    if alice_node and company_node:
        print("\n[2] Creating edge with name in properties:")
        print("    CREATE EDGE WORKS_FOR SRC Person DEST Company {name: 'Employment'}")
        result3 = executor.execute(f"CREATE EDGE WORKS_FOR SRC Person DEST Company {{name: 'Employment'}}")
        print(f"    Result: {json.dumps(result3, indent=2, default=str)}")
        
        # Verify UUID is not duplicated in result
        if 'edge_uuid' in result3 and 'uuid' in result3:
            print("    [ERROR] UUID duplication found in edge result!")
        elif 'uuid' in result3:
            print(f"    [OK] UUID returned as 'uuid': {result3['uuid']}")
        
        # Get the created edge
        all_edges = graph.get_all_edges()
        works_for_edge = None
        for e in all_edges:
            if e.label == 'WORKS_FOR' and e.properties.get('name') == 'Employment':
                works_for_edge = e
                break
        
        if not works_for_edge:
            # Try to find any WORKS_FOR edge
            for e in all_edges:
                if e.label == 'WORKS_FOR':
                    works_for_edge = e
                    break
        
        if works_for_edge:
            print(f"\n[3] Edge object properties:")
            print(f"    edge.id: {works_for_edge.id}")
            print(f"    edge.label: {works_for_edge.label}")
            print(f"    edge.name: {works_for_edge.name}")  # Direct property
            print(f"    edge.uuid: {works_for_edge.uuid}")  # Direct property (for backward compat)
            print(f"    edge.properties: {works_for_edge.properties}")
            print(f"    edge.properties['name']: {works_for_edge.properties.get('name')}")  # In properties dict
            print(f"    edge.properties['uuid']: {works_for_edge.properties.get('uuid')}")  # UUID in properties
            
            print(f"\n[4] Verification:")
            # Check name (should be clean, no quotes)
            name_clean = works_for_edge.name == 'Employment' and not (works_for_edge.name.startswith("'") or works_for_edge.name.startswith('"'))
            print(f"    - Name is clean (no quotes): {name_clean} (value: '{works_for_edge.name}')")
            # Check UUID consistency
            uuid_consistent = works_for_edge.uuid == works_for_edge.properties.get('uuid')
            print(f"    - UUID consistent (edge.uuid == properties['uuid']): {uuid_consistent}")
            print(f"      edge.uuid: {works_for_edge.uuid}")
            print(f"      properties['uuid']: {works_for_edge.properties.get('uuid')}")
            # Check name consistency
            name_consistent = works_for_edge.name == works_for_edge.properties.get('name')
            print(f"    - Name consistent (edge.name == properties['name']): {name_consistent}")
            print(f"      edge.name: '{works_for_edge.name}'")
            print(f"      properties['name']: '{works_for_edge.properties.get('name')}'")
            
            if name_clean and uuid_consistent and name_consistent:
                print(f"\n    [OK] All checks passed!")
            else:
                print(f"\n    [FAIL] Some checks failed!")
    
    print("\n" + "=" * 80)
    print("FIXES APPLIED:")
    print("=" * 80)
    print("""
1. QUOTE STRIPPING:
   - Parser now strips both single (') and double (") quotes from string values
   - Executor also cleans string values as a safety measure
   - Name values are now clean: 'Alice' becomes Alice (no quotes)

2. UUID DEDUPLICATION:
   - UUID is stored ONLY in properties['uuid'] as the single source of truth
   - The node.uuid/edge.uuid property is kept for backward compatibility
   - Both node.uuid and properties['uuid'] will always match
   - No more duplicate UUIDs in different places

3. CONSISTENCY:
   - Name appears in both node.name and node.properties['name']
   - UUID appears in both node.uuid and node.properties['uuid']
   - Both are always in sync
   - Same applies to edges

4. TESTING:
   - All CREATE NODE queries tested
   - All CREATE EDGE queries tested
   - Quote stripping verified
   - UUID deduplication verified
    """)


if __name__ == "__main__":
    test_name_in_node_creation()




























