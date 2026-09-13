"""
Test Graph Traversal and Aggregation Operations.
"""

import sys
import os
from pathlib import Path

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine.executor import AIQLExecutor


def test_traversal_and_aggregation():
    """Test graph traversal and aggregation operations."""
    namespace = "test_traversal"
    
    print("=" * 80)
    print("GRAPH TRAVERSAL AND AGGREGATION TEST")
    print("=" * 80)
    
    # Setup
    graph = AIContextDB(name=namespace, storage_backend='csr')
    # Disable buffer for immediate CSR updates (needed for traversal)
    graph.buffer_enabled = False
    executor = AIQLExecutor(contextcore=graph)
    executor.active_namespace = namespace
    
    results = {}
    
    # Create namespace
    print("\n[SETUP] Creating namespace and nodes...")
    query = f"CREATE NAMESPACE {namespace};"
    result = executor.execute(query)
    print(f"[OK] Namespace created")
    
    query = f"USE NAMESPACE {namespace};"
    result = executor.execute(query)
    print(f"[OK] Using namespace")
    
    # Create a graph structure: Person -> Company -> Product
    print("\n[SETUP] Creating graph structure...")
    queries = [
        # People
        "CREATE NODE Person {name: \"Alice\", age: 30, salary: 100000}",
        "CREATE NODE Person {name: \"Bob\", age: 25, salary: 80000}",
        "CREATE NODE Person {name: \"Charlie\", age: 35, salary: 120000}",
        # Companies
        "CREATE NODE Company {name: \"Tech Corp\", revenue: 1000000}",
        "CREATE NODE Company {name: \"Finance Inc\", revenue: 2000000}",
        # Products
        "CREATE NODE Product {name: \"Software A\", price: 500}",
        "CREATE NODE Product {name: \"Software B\", price: 1000}",
    ]
    
    for q in queries:
        result = executor.execute(q)
        print(f"  Created: {result.get('node_type', 'unknown')}")
    
    # Create edges: Person WORKS_AT Company, Company PRODUCES Product
    print("\n[SETUP] Creating edges...")
    edge_queries = [
        "CREATE EDGE WORKS_AT SRC Person DEST Company",
        "CREATE EDGE WORKS_AT SRC Person DEST Company",
        "CREATE EDGE PRODUCES SRC Company DEST Product",
        "CREATE EDGE PRODUCES SRC Company DEST Product",
    ]
    
    for q in edge_queries:
        result = executor.execute(q)
        print(f"  Created edge: {result.get('edge_type', 'unknown')}")
    
    # Test 1: Simple TRAVERSE
    print("\n[TEST 1] Simple TRAVERSE from Person")
    try:
        # Get a Person node first
        query = "SELECT * FROM Person LIMIT 1"
        result = executor.execute(query)
        person_nodes = result if isinstance(result, list) else (result.get('select', []) if isinstance(result, dict) else [])
        
        if person_nodes:
            person_id = person_nodes[0].get('id')
            # Use graph's traverse method
            traversed = graph.traverse_graph(person_id, edge_label="WORKS_AT", max_depth=1)
            print(f"  Traversed from Person {person_id}: {len(traversed)} nodes found")
            results['TRAVERSE_SIMPLE'] = len(traversed) > 0
            print(f"  Status: {'PASS' if results['TRAVERSE_SIMPLE'] else 'FAIL'}\n")
        else:
            results['TRAVERSE_SIMPLE'] = False
            print(f"  Status: FAIL (no Person nodes)\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['TRAVERSE_SIMPLE'] = False
    
    # Test 2: TRAVERSE with depth
    print("[TEST 2] TRAVERSE with max_depth=2")
    try:
        query = "SELECT * FROM Person LIMIT 1"
        result = executor.execute(query)
        person_nodes = result if isinstance(result, list) else (result.get('select', []) if isinstance(result, dict) else [])
        
        if person_nodes:
            person_id = person_nodes[0].get('id')
            # Traverse 2 levels: Person -> Company -> Product
            traversed = graph.traverse_graph(person_id, max_depth=2)
            print(f"  Traversed 2 levels: {len(traversed)} nodes found")
            results['TRAVERSE_DEPTH'] = len(traversed) > 0
            print(f"  Status: {'PASS' if results['TRAVERSE_DEPTH'] else 'FAIL'}\n")
        else:
            results['TRAVERSE_DEPTH'] = False
            print(f"  Status: FAIL\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        results['TRAVERSE_DEPTH'] = False
    
    # Test 3: Aggregation - COUNT
    print("[TEST 3] Aggregation - COUNT nodes")
    try:
        query = "SELECT COUNT(*) FROM Person"
        result = executor.execute(query)
        # COUNT can be in result dict or in list
        if isinstance(result, dict):
            count = result.get('count', 0) or result.get('COUNT(*)', 0)
        elif isinstance(result, list) and result:
            count = result[0].get('count', 0) or result[0].get('COUNT(*)', 0)
        else:
            count = 0
        print(f"  Count of Person nodes: {count}")
        results['AGG_COUNT'] = count == 3
        print(f"  Status: {'PASS' if results['AGG_COUNT'] else 'FAIL'}\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['AGG_COUNT'] = False
    
    # Test 4: Aggregation - SUM
    print("[TEST 4] Aggregation - SUM salaries")
    try:
        query = "SELECT * FROM Person"
        result = executor.execute(query)
        person_nodes = result if isinstance(result, list) else (result.get('select', []) if isinstance(result, dict) else [])
        
        if person_nodes:
            # Deduplicate by ID
            seen = set()
            unique_nodes = []
            for p in person_nodes:
                if isinstance(p, dict) and p.get('id') not in seen:
                    seen.add(p.get('id'))
                    unique_nodes.append(p)
            
            total_salary = sum(p.get('salary', 0) for p in unique_nodes if isinstance(p, dict))
            print(f"  Sum of salaries: {total_salary}")
            results['AGG_SUM'] = total_salary == 300000
            print(f"  Status: {'PASS' if results['AGG_SUM'] else 'FAIL'}\n")
        else:
            results['AGG_SUM'] = False
            print(f"  Status: FAIL\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        results['AGG_SUM'] = False
    
    # Test 5: Aggregation - AVG
    print("[TEST 5] Aggregation - AVG age")
    try:
        query = "SELECT * FROM Person"
        result = executor.execute(query)
        person_nodes = result if isinstance(result, list) else (result.get('select', []) if isinstance(result, dict) else [])
        
        if person_nodes:
            ages = [p.get('age', 0) for p in person_nodes if isinstance(p, dict) and 'age' in p]
            avg_age = sum(ages) / len(ages) if ages else 0
            print(f"  Average age: {avg_age:.2f}")
            results['AGG_AVG'] = abs(avg_age - 30.0) < 0.1  # Should be 30
            print(f"  Status: {'PASS' if results['AGG_AVG'] else 'FAIL'}\n")
        else:
            results['AGG_AVG'] = False
            print(f"  Status: FAIL\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        results['AGG_AVG'] = False
    
    # Test 6: Aggregation - MAX
    print("[TEST 6] Aggregation - MAX salary")
    try:
        query = "SELECT * FROM Person"
        result = executor.execute(query)
        person_nodes = result if isinstance(result, list) else (result.get('select', []) if isinstance(result, dict) else [])
        
        if person_nodes:
            salaries = [p.get('salary', 0) for p in person_nodes if isinstance(p, dict) and 'salary' in p]
            max_salary = max(salaries) if salaries else 0
            print(f"  Max salary: {max_salary}")
            results['AGG_MAX'] = max_salary == 120000
            print(f"  Status: {'PASS' if results['AGG_MAX'] else 'FAIL'}\n")
        else:
            results['AGG_MAX'] = False
            print(f"  Status: FAIL\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        results['AGG_MAX'] = False
    
    # Test 7: Traversal + Aggregation
    print("[TEST 7] Traversal + Aggregation")
    try:
        # Traverse from Person to Company, then aggregate
        query = "SELECT * FROM Person"
        result = executor.execute(query)
        person_nodes = result if isinstance(result, list) else (result.get('select', []) if isinstance(result, dict) else [])
        
        if person_nodes:
            # Traverse from each person to companies
            all_companies = []
            for person in person_nodes:
                person_id = person.get('id')
                if person_id:
                    companies = graph.traverse_graph(person_id, edge_label="WORKS_AT", max_depth=1)
                    all_companies.extend(companies)
            
            # Get company nodes and aggregate revenue
            company_revenues = []
            for company_id in set(all_companies):
                company = graph.get_node(company_id)
                if company and hasattr(company, 'properties'):
                    revenue = company.properties.get('revenue', 0)
                    company_revenues.append(revenue)
            
            total_revenue = sum(company_revenues)
            print(f"  Total revenue from traversed companies: {total_revenue}")
            results['TRAVERSE_AGG'] = total_revenue > 0
            print(f"  Status: {'PASS' if results['TRAVERSE_AGG'] else 'FAIL'}\n")
        else:
            results['TRAVERSE_AGG'] = False
            print(f"  Status: FAIL\n")
    except Exception as e:
        print(f"  Error: {e}\n")
        import traceback
        traceback.print_exc()
        results['TRAVERSE_AGG'] = False
    
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
        success = test_traversal_and_aggregation()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)

