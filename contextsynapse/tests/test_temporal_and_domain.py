"""
Comprehensive tests for temporal queries and domain-based filtering.
"""

import sys
import os
from pathlib import Path
from datetime import datetime, timedelta
import time

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine.executor import AIQLExecutor


class TemporalAndDomainTester:
    """Test temporal queries and domain-based filtering."""
    
    def __init__(self):
        self.namespace = "test_temporal_domain"
        
    def setup_graph(self, enable_temporal=True):
        """Setup graph with temporal enabled."""
        config = {
            'temporal': {'enabled': enable_temporal},
            'buffer': {'enabled': False}
        }
        graph = AIContextDB(name=self.namespace, storage_backend='csr', config=config)
        executor = AIQLExecutor(contextcore=graph)
        executor.active_namespace = self.namespace
        
        # Create namespace
        executor.execute(f'CREATE NAMESPACE {self.namespace}')
        executor.execute(f'USE NAMESPACE {self.namespace}')
        
        return graph, executor
    
    def test_temporal_queries(self):
        """Test temporal queries with AT TIMESTAMP."""
        print("\n" + "="*80)
        print("TEST: Temporal Queries (AT TIMESTAMP)")
        print("="*80)
        
        graph, executor = self.setup_graph(enable_temporal=True)
        
        if not graph.temporal_enabled:
            print("  [SKIP] Time travel not enabled")
            return None
        
        # Create a node
        print("\n[SETUP] Creating node...")
        executor.execute('CREATE NODE Person {name: "Alice", age: 30, domain: "finance"}')
        
        # Get timestamp after creation
        time.sleep(0.1)
        timestamp1 = datetime.now()
        
        # Update the node
        print("\n[UPDATE] Updating node...")
        executor.execute('UPDATE NODE Person SET {age: 31} WHERE name = "Alice"')
        
        time.sleep(0.1)
        timestamp2 = datetime.now()
        
        # Query current state
        print("\n[TEST] Querying current state...")
        result = executor.execute('SELECT * FROM Person WHERE name = "Alice"')
        if isinstance(result, list) and len(result) > 0:
            age = result[0].get('age', 'N/A')
            print(f"  Current age: {age}")
            if age == 31:
                print("  [OK] Current state query works")
            else:
                print(f"  [FAIL] Expected age 31, got {age}")
        
        # Query at first timestamp (before update)
        print(f"\n[TEST] Querying at timestamp {timestamp1.isoformat()}...")
        # Note: This requires AT TIMESTAMP syntax in the query
        # For now, test temporal storage directly
        if graph.temporal_storage:
            # Get all versions for Alice node
            # Find Alice node ID first
            all_nodes = graph.get_all_nodes()
            alice_node = None
            for node in all_nodes:
                if node.properties.get('name') == 'Alice':
                    alice_node = node
                    break
            
            if alice_node:
                versions = graph.temporal_storage.get_all_versions(alice_node.id)
                print(f"  Found {len(versions)} versions for Alice")
                if len(versions) >= 2:
                    print("  [OK] Version creation working")
                    # Check first version (should have age 30)
                    first_version = versions[0]
                    if first_version.properties.get('age') == 30:
                        print("  [OK] First version has correct age (30)")
                    else:
                        print(f"  [FAIL] First version age: {first_version.properties.get('age')}")
                    
                    # Check last version (should have age 31)
                    last_version = versions[-1]
                    if last_version.properties.get('age') == 31:
                        print("  [OK] Last version has correct age (31)")
                    else:
                        print(f"  [FAIL] Last version age: {last_version.properties.get('age')}")
                    return True
                else:
                    print(f"  [FAIL] Expected at least 2 versions, got {len(versions)}")
                    return False
            else:
                print("  [FAIL] Could not find Alice node")
                return False
        else:
            print("  [FAIL] Temporal storage not available")
            return False
    
    def test_domain_filtering(self):
        """Test domain-based filtering."""
        print("\n" + "="*80)
        print("TEST: Domain-Based Filtering")
        print("="*80)
        
        graph, executor = self.setup_graph(enable_temporal=False)
        
        # Create nodes with different domains
        print("\n[SETUP] Creating nodes with different domains...")
        queries = [
            'CREATE NODE Person {name: "Alice", age: 30, domain: "finance"}',
            'CREATE NODE Person {name: "Bob", age: 25, domain: "finance"}',
            'CREATE NODE Person {name: "Charlie", age: 35, domain: "sales"}',
            'CREATE NODE Person {name: "Diana", age: 28, domain: "sales"}',
        ]
        
        for query in queries:
            result = executor.execute(query)
            if not isinstance(result, dict) or not result.get('success', True):
                print(f"  [WARN] Failed to create node: {query}")
        
        # Test domain filtering
        print("\n[TEST] Filtering by domain = 'finance'...")
        query = "SELECT * FROM Person WHERE domain = 'finance'"
        result = executor.execute(query)
        
        if isinstance(result, list):
            print(f"  Found {len(result)} nodes in finance domain")
            names = [r.get('name') for r in result if r.get('name')]
            print(f"  Names: {names}")
            if len(result) == 2 and 'Alice' in names and 'Bob' in names:
                print("  [OK] Domain filtering works correctly")
                return True
            else:
                print(f"  [FAIL] Expected 2 nodes (Alice, Bob), got {len(result)}")
                return False
        else:
            print(f"  [FAIL] Query returned unexpected format: {type(result)}")
            return False
    
    def test_domain_filtering_sales(self):
        """Test domain filtering for sales domain."""
        print("\n[TEST] Filtering by domain = 'sales'...")
        graph, executor = self.setup_graph(enable_temporal=False)
        
        # Recreate nodes (fresh namespace)
        queries = [
            'CREATE NODE Person {name: "Alice", age: 30, domain: "finance"}',
            'CREATE NODE Person {name: "Bob", age: 25, domain: "finance"}',
            'CREATE NODE Person {name: "Charlie", age: 35, domain: "sales"}',
            'CREATE NODE Person {name: "Diana", age: 28, domain: "sales"}',
        ]
        
        for query in queries:
            executor.execute(query)
        
        query = "SELECT * FROM Person WHERE domain = 'sales'"
        result = executor.execute(query)
        
        if isinstance(result, list):
            print(f"  Found {len(result)} nodes in sales domain")
            names = [r.get('name') for r in result if r.get('name')]
            print(f"  Names: {names}")
            if len(result) == 2 and 'Charlie' in names and 'Diana' in names:
                print("  [OK] Sales domain filtering works correctly")
                return True
            else:
                print(f"  [FAIL] Expected 2 nodes (Charlie, Diana), got {len(result)}")
                return False
        else:
            print(f"  [FAIL] Query returned unexpected format")
            return False
    
    def run_all_tests(self):
        """Run all tests."""
        print("\n" + "="*80)
        print("TEMPORAL AND DOMAIN FILTERING TEST SUITE")
        print("="*80)
        
        results = {}
        
        # Test temporal queries
        results['temporal'] = self.test_temporal_queries()
        
        # Test domain filtering
        results['domain_finance'] = self.test_domain_filtering()
        results['domain_sales'] = self.test_domain_filtering_sales()
        
        # Summary
        print("\n" + "="*80)
        print("TEST SUMMARY")
        print("="*80)
        for test_name, result in results.items():
            status = "[PASS]" if result is True else ("[SKIP]" if result is None else "[FAIL]")
            print(f"  {test_name}: {status}")
        
        return results


if __name__ == "__main__":
    tester = TemporalAndDomainTester()
    tester.run_all_tests()
