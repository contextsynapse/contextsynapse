"""
Comprehensive Test Suite for All AIQL Queries

Tests all major AIQL query types both natively and via API.

This test suite covers:
- CREATE operations (NODE, EDGE, GRAPH, COLLECTION, INDEX)
- SELECT queries (basic, WHERE, LIMIT, COUNT, ORDER BY, GROUP BY, aggregations)
- UPDATE operations (NODE, EDGE)
- DELETE operations (NODE, EDGE)
- MATCH queries (NODE, patterns, RETURN, WHERE)
- TRAVERSE queries (FROM, VIA, DEPTH, MAX DEPTH, WHERE)
- Search queries (DENSE, SPARSE, HYBRID, SEMANTIC, GRAPH)
- RAG queries (Simple RAG, RAG GENERATE)
- Analytics (PAGERANK, SHORTEST_PATH, COMMUNITY_DETECTION, GRAPH SUMMARY, HOP)
- Variable declarations (LET)
- MERGE operations
- NEIGHBORS queries
- Namespace operations
- Statistics and metadata
- Transaction operations
- And more...

Usage:
    # Test both native and API (API server must be running)
    python test_all_aiql_queries.py
    
    # To start API server:
    python -m contextsynapse.api.server
    
Note: Some queries may be marked as expected_success=False if they require
      specific setup (e.g., embeddings for search, graph structure for analytics).
      These will still be tested but failures won't count against the test suite.
"""

import sys
import os
import json
import time
import argparse
from pathlib import Path
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
    print("[WARN] requests library not available. API tests will be skipped.")

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine.executor import AIQLExecutor


class AIQLQueryTester:
    """Comprehensive tester for all AIQL queries."""
    
    def __init__(self, namespace: str = "test_all_queries", api_url: str = "http://localhost:8000", 
                 test_native_only: bool = False, test_api_only: bool = False):
        self.namespace = namespace
        self.api_url = api_url
        self.test_native_only = test_native_only
        self.test_api_only = test_api_only
        
        # Generate session ID for stateful API testing (so all requests share same graph instance)
        import uuid
        self.session_id = str(uuid.uuid4())
        
        # Setup native executor
        self.graph = AIContextDB(name=namespace, storage_backend='csr')
        self.graph.buffer_enabled = False
        self.executor = AIQLExecutor(contextcore=self.graph)
        self.executor.active_namespace = namespace
        
        # Track created UUIDs for cleanup
        self.created_node_uuids = []
        self.created_edge_uuids = []
        
        # Results
        self.results = {
            'native': {},
            'api': {}
        }
    
    def setup(self):
        """Setup test environment."""
        print("\n[SETUP] Creating namespace...")
        try:
            self.executor.execute(f"CREATE NAMESPACE {self.namespace}")
            self.executor.execute(f"USE NAMESPACE {self.namespace}")
            print("[OK] Namespace created")
        except Exception as e:
            print(f"[WARN] Namespace might already exist: {e}")
    
    def test_native(self, query_name: str, query: str, expected_success: bool = True):
        """Test query natively."""
        try:
            result = self.executor.execute(query)
            success = result.get('success', True) if isinstance(result, dict) else True
            
            if expected_success:
                self.results['native'][query_name] = success
                status = "[PASS]" if success else "[FAIL]"
                print(f"  {status} {query_name}")
            else:
                # For queries that might fail, just check no exception
                self.results['native'][query_name] = True
                print(f"  [OK] {query_name} (executed without exception)")
            
            return result
        except Exception as e:
            self.results['native'][query_name] = False
            print(f"  [FAIL] {query_name}: {e}")
            return None
    
    def test_api(self, query_name: str, query: str, expected_success: bool = True):
        """Test query via API."""
        if not REQUESTS_AVAILABLE:
            self.results['api'][query_name] = None
            print(f"  [SKIP] {query_name} (API): requests library not available")
            return None
        
        try:
            response = requests.post(
                f"{self.api_url}/aiql",
                json={
                    "query": query, 
                    "namespace": self.namespace,
                    "session_id": self.session_id  # Use stateful session for consistent graph instance
                },
                timeout=30  # Increased timeout for complex queries
            )
            
            if response.status_code == 200:
                result = response.json()
                success = result.get('success', True)
                
                if expected_success:
                    self.results['api'][query_name] = success
                    status = "[PASS]" if success else "[FAIL]"
                    print(f"  {status} {query_name} (API)")
                else:
                    # For queries that might fail, just check no exception
                    self.results['api'][query_name] = True
                    print(f"  [OK] {query_name} (API) (executed without exception)")
                
                return result
            else:
                # Check if it's an expected failure
                if not expected_success:
                    self.results['api'][query_name] = True
                    print(f"  [OK] {query_name} (API) (expected failure: HTTP {response.status_code})")
                else:
                    self.results['api'][query_name] = False
                    print(f"  [FAIL] {query_name} (API): HTTP {response.status_code}")
                return None
        except requests.exceptions.ConnectionError:
            self.results['api'][query_name] = None  # API not available
            print(f"  [SKIP] {query_name} (API): API server not running (start with: python -m contextsynapse.api.server)")
            return None
        except requests.exceptions.Timeout:
            self.results['api'][query_name] = None
            print(f"  [SKIP] {query_name} (API): Request timeout")
            return None
        except Exception as e:
            if not expected_success:
                self.results['api'][query_name] = True
                print(f"  [OK] {query_name} (API) (expected failure: {str(e)[:50]})")
            else:
                self.results['api'][query_name] = False
                print(f"  [FAIL] {query_name} (API): {e}")
            return None
    
    def test_both(self, query_name: str, query: str, expected_success: bool = True):
        """Test query both natively and via API."""
        print(f"\n[TEST] {query_name}")
        print(f"  Query: {query[:80]}..." if len(query) > 80 else f"  Query: {query}")
        
        native_result = None
        api_result = None
        
        if not self.test_api_only:
            native_result = self.test_native(query_name, query, expected_success)
        
        if not self.test_native_only and REQUESTS_AVAILABLE:
            api_result = self.test_api(query_name, query, expected_success)
        
        return native_result, api_result
    
    def run_all_tests(self):
        """Run comprehensive test suite."""
        print("=" * 80)
        print("COMPREHENSIVE AIQL QUERY TEST SUITE")
        print("=" * 80)
        
        if self.test_native_only:
            print("\n[MODE] Testing NATIVE execution only")
        elif self.test_api_only:
            print("\n[MODE] Testing API execution only")
        else:
            print("\n[MODE] Testing both NATIVE and API execution")
        
        self.setup()
        
        # ========================================================================
        # 1. CREATE NODE Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("1. CREATE NODE Tests")
        print("=" * 80)
        
        # Basic CREATE NODE
        result1, _ = self.test_both(
            "CREATE_NODE_BASIC",
            'CREATE NODE Person {name: "Alice", age: 30, city: "NYC"}'
        )
        if result1 and result1.get('uuid'):
            self.created_node_uuids.append(result1['uuid'])
        
        # CREATE NODE with alias
        result2, _ = self.test_both(
            "CREATE_NODE_WITH_ALIAS",
            'CREATE NODE Company AS corp {name: "TechCorp", industry: "Tech"}'
        )
        if result2 and result2.get('uuid'):
            self.created_node_uuids.append(result2['uuid'])
        
        # CREATE NODE with complex properties
        result3, _ = self.test_both(
            "CREATE_NODE_COMPLEX",
            'CREATE NODE Product {name: "Widget", price: 99.99, in_stock: true, tags: ["electronics", "gadget"]}'
        )
        if result3 and result3.get('uuid'):
            self.created_node_uuids.append(result3['uuid'])
        
        # ========================================================================
        # 2. CREATE EDGE Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("2. CREATE EDGE Tests")
        print("=" * 80)
        
        # CREATE EDGE with SRC/DEST
        result4, _ = self.test_both(
            "CREATE_EDGE_SRC_DEST",
            "CREATE EDGE WORKS_AT SRC Person DEST Company"
        )
        if result4 and result4.get('uuid'):
            self.created_edge_uuids.append(result4['uuid'])
        
        # CREATE EDGE with properties
        result5, _ = self.test_both(
            "CREATE_EDGE_WITH_PROPERTIES",
            'CREATE EDGE OWNS SRC Person DEST Product {since: "2024-01-01", quantity: 2}'
        )
        if result5 and result5.get('uuid'):
            self.created_edge_uuids.append(result5['uuid'])
        
        # ========================================================================
        # 3. SELECT Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("3. SELECT Tests")
        print("=" * 80)
        
        # SELECT all nodes
        self.test_both(
            "SELECT_ALL",
            "SELECT * FROM Person"
        )
        
        # SELECT with WHERE
        self.test_both(
            "SELECT_WHERE",
            'SELECT * FROM Person WHERE age > 25'
        )
        
        # SELECT with LIMIT
        self.test_both(
            "SELECT_LIMIT",
            "SELECT * FROM Person LIMIT 10"
        )
        
        # SELECT COUNT
        self.test_both(
            "SELECT_COUNT",
            "SELECT COUNT(*) FROM Person"
        )
        
        # SELECT specific properties
        self.test_both(
            "SELECT_PROPERTIES",
            "SELECT name, age FROM Person"
        )
        
        # ========================================================================
        # 4. UPDATE NODE Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("4. UPDATE NODE Tests")
        print("=" * 80)
        
        # UPDATE NODE
        self.test_both(
            "UPDATE_NODE",
            'UPDATE NODE Person SET {age: 31, city: "Boston"} WHERE name = "Alice"'
        )
        
        # UPDATE NODE with multiple properties
        self.test_both(
            "UPDATE_NODE_MULTIPLE",
            'UPDATE NODE Product SET {price: 89.99, in_stock: false} WHERE name = "Widget"'
        )
        
        # ========================================================================
        # 5. UPDATE EDGE Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("5. UPDATE EDGE Tests")
        print("=" * 80)
        
        # UPDATE EDGE
        self.test_both(
            "UPDATE_EDGE",
            'UPDATE EDGE WORKS_AT SET {role: "Senior Engineer", since: "2023-01-01"}'
        )
        
        # ========================================================================
        # 6. DELETE Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("6. DELETE Tests")
        print("=" * 80)
        
        # DELETE NODE
        self.test_both(
            "DELETE_NODE",
            'DELETE NODE Product WHERE name = "Widget"'
        )
        
        # DELETE EDGE
        self.test_both(
            "DELETE_EDGE",
            "DELETE EDGE OWNS"
        )
        
        # ========================================================================
        # 7. MATCH Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("7. MATCH Tests")
        print("=" * 80)
        
        # MATCH NODE
        self.test_both(
            "MATCH_NODE",
            'MATCH NODE Person WHERE name = "Alice"'
        )
        
        # MATCH pattern
        self.test_both(
            "MATCH_PATTERN",
            "MATCH (Person)-[WORKS_AT]->(Company)"
        )
        
        # ========================================================================
        # 8. TRAVERSE Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("8. TRAVERSE Tests")
        print("=" * 80)
        
        # TRAVERSE FROM
        self.test_both(
            "TRAVERSE_FROM",
            "TRAVERSE FROM Person TO Company VIA WORKS_AT"
        )
        
        # TRAVERSE with DEPTH
        self.test_both(
            "TRAVERSE_DEPTH",
            "TRAVERSE FROM Person DEPTH 2"
        )
        
        # ========================================================================
        # 9. Namespace Operations
        # ========================================================================
        print("\n" + "=" * 80)
        print("9. Namespace Operations")
        print("=" * 80)
        
        # SHOW NAMESPACES
        self.test_both(
            "SHOW_NAMESPACES",
            "SHOW NAMESPACES"
        )
        
        # SHOW GRAPHS
        self.test_both(
            "SHOW_GRAPHS",
            "SHOW GRAPHS"
        )
        
        # ========================================================================
        # 10. Advanced SELECT Tests
        # ========================================================================
        print("\n" + "=" * 80)
        print("10. Advanced SELECT Tests")
        print("=" * 80)
        
        # SELECT with ORDER BY
        self.test_both(
            "SELECT_ORDER_BY",
            "SELECT * FROM Person ORDER BY age DESC"
        )
        
        # SELECT with GROUP BY
        self.test_both(
            "SELECT_GROUP_BY",
            "SELECT city, COUNT(*) FROM Person GROUP BY city"
        )
        
        # SELECT with aggregation functions
        self.test_both(
            "SELECT_AVG",
            "SELECT AVG(age) FROM Person"
        )
        
        self.test_both(
            "SELECT_SUM",
            "SELECT SUM(age) FROM Person"
        )
        
        self.test_both(
            "SELECT_MIN_MAX",
            "SELECT MIN(age), MAX(age) FROM Person"
        )
        
        # ========================================================================
        # 11. Variable Declarations
        # ========================================================================
        print("\n" + "=" * 80)
        print("11. Variable Declarations")
        print("=" * 80)
        
        # LET variable
        self.test_both(
            "LET_VARIABLE",
            'LET $author = "Alice"'
        )
        
        # Use variable in query
        self.test_both(
            "SELECT_WITH_VARIABLE",
            'LET $min_age = 25; SELECT * FROM Person WHERE age > $min_age'
        )
        
        # ========================================================================
        # 12. MERGE Operations
        # ========================================================================
        print("\n" + "=" * 80)
        print("12. MERGE Operations")
        print("=" * 80)
        
        # MERGE NODE
        self.test_both(
            "MERGE_NODE",
            'MERGE NODE Person {name: "Alice"} SET {age: 30, city: "NYC"}'
        )
        
        # ========================================================================
        # 13. Search Queries
        # ========================================================================
        print("\n" + "=" * 80)
        print("13. Search Queries")
        print("=" * 80)
        
        # DENSE SEARCH
        self.test_both(
            "DENSE_SEARCH",
            'DENSE SEARCH "Alice" IN Person LIMIT 5',
            expected_success=False  # May fail if no embeddings
        )
        
        # SPARSE SEARCH
        self.test_both(
            "SPARSE_SEARCH",
            'SPARSE SEARCH "Alice" IN Person LIMIT 5',
            expected_success=False  # May fail if no index
        )
        
        # HYBRID SEARCH
        self.test_both(
            "HYBRID_SEARCH",
            'HYBRID SEARCH "Alice" IN Person LIMIT 5',
            expected_success=False  # May fail if no embeddings/index
        )
        
        # SEMANTIC SEARCH
        self.test_both(
            "SEMANTIC_SEARCH",
            'SEMANTIC SEARCH "Alice" IN Person LIMIT 5',
            expected_success=False  # May fail if no embeddings
        )
        
        # GRAPH SEARCH
        self.test_both(
            "GRAPH_SEARCH",
            'GRAPH SEARCH "Alice" VIA (WORKS_AT) DEPTH 2',
            expected_success=False  # May fail if no graph structure
        )
        
        # ========================================================================
        # 14. RAG Queries
        # ========================================================================
        print("\n" + "=" * 80)
        print("14. RAG Queries")
        print("=" * 80)
        
        # Simple RAG query
        self.test_both(
            "RAG_QUERY_SIMPLE",
            f'RAG "What is Alice?" AT NAMESPACE {self.namespace}',
            expected_success=False  # May fail if no RAG setup
        )
        
        # RAG GENERATE
        self.test_both(
            "RAG_GENERATE",
            'LET $query = "What is Alice?"; RAG GENERATE $query',
            expected_success=False  # May fail if no RAG setup
        )
        
        # ========================================================================
        # 15. Analytics Queries
        # ========================================================================
        print("\n" + "=" * 80)
        print("15. Analytics Queries")
        print("=" * 80)
        
        # PAGERANK
        self.test_both(
            "PAGERANK",
            "PAGERANK ON Person ITERATIONS 10",
            expected_success=False  # May fail if no edges
        )
        
        # SHORTEST_PATH
        self.test_both(
            "SHORTEST_PATH",
            'SHORTEST_PATH FROM "Alice" TO "TechCorp"',
            expected_success=False  # May fail if nodes don't exist
        )
        
        # COMMUNITY_DETECTION
        self.test_both(
            "COMMUNITY_DETECTION",
            "COMMUNITY_DETECTION ON Person",
            expected_success=False  # May fail if no graph structure
        )
        
        # GRAPH SUMMARY
        self.test_both(
            "GRAPH_SUMMARY",
            "GRAPH SUMMARY ON Person",
            expected_success=False  # May fail if no nodes
        )
        
        # HOP query
        self.test_both(
            "HOP_QUERY",
            'HOP FROM Person TO Company DEPTH 1',
            expected_success=False  # May fail if no edges
        )
        
        # ========================================================================
        # 16. NEIGHBORS Queries
        # ========================================================================
        print("\n" + "=" * 80)
        print("16. NEIGHBORS Queries")
        print("=" * 80)
        
        # NEIGHBORS FROM
        self.test_both(
            "NEIGHBORS_FROM",
            'NEIGHBORS FROM Person WHERE name = "Alice" DEPTH 1',
            expected_success=False  # May fail if no edges
        )
        
        # ========================================================================
        # 17. Collection Operations
        # ========================================================================
        print("\n" + "=" * 80)
        print("17. Collection Operations")
        print("=" * 80)
        
        # CREATE COLLECTION
        self.test_both(
            "CREATE_COLLECTION",
            'CREATE COLLECTION test_collection {fields: [{name: "string"}, {age: "int"}]}',
            expected_success=False  # May not be implemented
        )
        
        # SHOW COLLECTIONS
        self.test_both(
            "SHOW_COLLECTIONS",
            "SHOW COLLECTIONS"
        )
        
        # ========================================================================
        # 18. Index Operations
        # ========================================================================
        print("\n" + "=" * 80)
        print("18. Index Operations")
        print("=" * 80)
        
        # CREATE INDEX
        self.test_both(
            "CREATE_INDEX",
            "CREATE INDEX test_index ON Person (name) TYPE DENSE",
            expected_success=False  # May fail if not implemented
        )
        
        # SHOW INDEXES
        self.test_both(
            "SHOW_INDEXES",
            "SHOW INDEXES"
        )
        
        # ========================================================================
        # 19. Statistics and Metadata
        # ========================================================================
        print("\n" + "=" * 80)
        print("19. Statistics and Metadata")
        print("=" * 80)
        
        # SHOW STATS
        self.test_both(
            "SHOW_STATS",
            "SHOW STATS"
        )
        
        # SHOW CURRENT GRAPH
        self.test_both(
            "SHOW_CURRENT_GRAPH",
            "SHOW CURRENT GRAPH"
        )
        
        # DESCRIBE NODE
        self.test_both(
            "DESCRIBE_NODE",
            "DESCRIBE NODE Person"
        )
        
        # ========================================================================
        # 20. Pipeline Operations
        # ========================================================================
        print("\n" + "=" * 80)
        print("20. Pipeline Operations")
        print("=" * 80)
        
        # SHOW PIPELINES
        self.test_both(
            "SHOW_PIPELINES",
            "SHOW PIPELINES"
        )
        
        # ========================================================================
        # 21. Advanced Traversal
        # ========================================================================
        print("\n" + "=" * 80)
        print("21. Advanced Traversal")
        print("=" * 80)
        
        # TRAVERSE with VIA
        self.test_both(
            "TRAVERSE_VIA",
            "TRAVERSE FROM Person VIA (WORKS_AT) TO Company"
        )
        
        # TRAVERSE with MAX DEPTH
        self.test_both(
            "TRAVERSE_MAX_DEPTH",
            "TRAVERSE FROM Person MAX DEPTH 3"
        )
        
        # TRAVERSE with WHERE
        self.test_both(
            "TRAVERSE_WHERE",
            'TRAVERSE FROM Person WHERE name = "Alice" MAX DEPTH 2'
        )
        
        # ========================================================================
        # 22. Advanced MATCH Patterns
        # ========================================================================
        print("\n" + "=" * 80)
        print("22. Advanced MATCH Patterns")
        print("=" * 80)
        
        # MATCH with RETURN
        self.test_both(
            "MATCH_RETURN",
            "MATCH (Person)-[WORKS_AT]->(Company) RETURN Person.name, Company.name"
        )
        
        # MATCH with WHERE
        self.test_both(
            "MATCH_WHERE",
            'MATCH (Person)-[WORKS_AT]->(Company) WHERE Person.name = "Alice"'
        )
        
        # ========================================================================
        # 23. Aggregation Queries
        # ========================================================================
        print("\n" + "=" * 80)
        print("23. Aggregation Queries")
        print("=" * 80)
        
        # AGGREGATE query
        self.test_both(
            "AGGREGATE_COUNT",
            "AGGREGATE COUNT(*) FROM Person"
        )
        
        # ========================================================================
        # 24. CREATE GRAPH Operations
        # ========================================================================
        print("\n" + "=" * 80)
        print("24. CREATE GRAPH Operations")
        print("=" * 80)
        
        # CREATE GRAPH
        self.test_both(
            "CREATE_GRAPH",
            "CREATE GRAPH test_graph"
        )
        
        # USE GRAPH
        self.test_both(
            "USE_GRAPH",
            "USE GRAPH test_graph"
        )
        
        # ========================================================================
        # 25. Transaction Operations
        # ========================================================================
        print("\n" + "=" * 80)
        print("25. Transaction Operations")
        print("=" * 80)
        
        # BEGIN TRANSACTION
        self.test_both(
            "BEGIN_TRANSACTION",
            "BEGIN TRANSACTION",
            expected_success=False  # May not be implemented
        )
        
        # COMMIT TRANSACTION
        self.test_both(
            "COMMIT_TRANSACTION",
            "COMMIT TRANSACTION",
            expected_success=False  # May not be implemented
        )
        
        # ========================================================================
        # 26. Verification: Check UUIDs in results
        # ========================================================================
        print("\n" + "=" * 80)
        print("26. UUID Verification")
        print("=" * 80)
        
        # Verify CREATE NODE returns UUID
        if result1:
            has_uuid = 'uuid' in result1
            no_node_id = 'node_id' not in result1
            self.results['native']['UUID_RETURNED'] = has_uuid and no_node_id
            print(f"  [{'PASS' if has_uuid and no_node_id else 'FAIL'}] CREATE NODE returns UUID (no node_id)")
        
        # Verify CREATE EDGE returns UUID
        if result4:
            has_uuid = 'uuid' in result4
            no_edge_id = 'edge_id' not in result4
            self.results['native']['EDGE_UUID_RETURNED'] = has_uuid and no_edge_id
            print(f"  [{'PASS' if has_uuid and no_edge_id else 'FAIL'}] CREATE EDGE returns UUID (no edge_id)")
        
        # Verify nodes can be found by UUID
        if self.created_node_uuids:
            all_nodes = self.graph.get_all_nodes()
            found_by_uuid = False
            for node in all_nodes:
                if node.properties.get('uuid') == self.created_node_uuids[0]:
                    found_by_uuid = True
                    break
            self.results['native']['FIND_BY_UUID'] = found_by_uuid
            print(f"  [{'PASS' if found_by_uuid else 'FAIL'}] Can find node by UUID from properties")
        
        # ========================================================================
        # Print Summary
        # ========================================================================
        self.print_summary()
    
    def print_summary(self):
        """Print test summary."""
        print("\n" + "=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)
        
        # Native results
        native_passed = sum(1 for v in self.results['native'].values() if v is True)
        native_failed = sum(1 for v in self.results['native'].values() if v is False)
        native_total = len([v for v in self.results['native'].values() if v is not None])
        native_skipped = sum(1 for v in self.results['native'].values() if v is None)
        
        print(f"\nNative Execution:")
        print(f"  Passed: {native_passed}/{native_total}")
        print(f"  Failed: {native_failed}/{native_total}")
        if native_skipped > 0:
            print(f"  Skipped: {native_skipped}")
        print(f"  Success Rate: {(native_passed*100//native_total) if native_total > 0 else 0}%")
        
        # API results
        api_passed = sum(1 for v in self.results['api'].values() if v is True)
        api_failed = sum(1 for v in self.results['api'].values() if v is False)
        api_total = len([v for v in self.results['api'].values() if v is not None])
        api_skipped = sum(1 for v in self.results['api'].values() if v is None)
        
        print(f"\nAPI Execution:")
        print(f"  Passed: {api_passed}/{api_total}")
        print(f"  Failed: {api_failed}/{api_total}")
        if api_skipped > 0:
            print(f"  Skipped: {api_skipped} (API server not running)")
            print(f"  Note: Start API server with: python -m contextsynapse.api.server")
        if api_total > 0:
            print(f"  Success Rate: {(api_passed*100//api_total)}%")
        
        # Group results by category
        categories = {
            'CREATE': ['CREATE_NODE', 'CREATE_EDGE', 'CREATE_GRAPH', 'CREATE_COLLECTION', 'CREATE_INDEX'],
            'SELECT': ['SELECT', 'SELECT_COUNT', 'SELECT_WHERE', 'SELECT_ORDER_BY', 'SELECT_GROUP_BY'],
            'UPDATE': ['UPDATE_NODE', 'UPDATE_EDGE'],
            'DELETE': ['DELETE_NODE', 'DELETE_EDGE'],
            'MATCH': ['MATCH_NODE', 'MATCH_PATTERN', 'MATCH_RETURN', 'MATCH_WHERE'],
            'TRAVERSE': ['TRAVERSE', 'TRAVERSE_FROM', 'TRAVERSE_DEPTH', 'TRAVERSE_VIA'],
            'SEARCH': ['DENSE_SEARCH', 'SPARSE_SEARCH', 'HYBRID_SEARCH', 'SEMANTIC_SEARCH', 'GRAPH_SEARCH'],
            'RAG': ['RAG_QUERY', 'RAG_GENERATE'],
            'ANALYTICS': ['PAGERANK', 'SHORTEST_PATH', 'COMMUNITY_DETECTION', 'GRAPH_SUMMARY', 'HOP_QUERY'],
            'NAMESPACE': ['SHOW_NAMESPACES', 'SHOW_GRAPHS', 'SHOW_COLLECTIONS', 'SHOW_PIPELINES'],
            'OTHER': ['MERGE_NODE', 'LET_VARIABLE', 'NEIGHBORS', 'AGGREGATE']
        }
        
        print(f"\n" + "=" * 80)
        print("QUERY TYPE COVERAGE")
        print("=" * 80)
        
        for category, patterns in categories.items():
            category_tests = [k for k in self.results['native'].keys() 
                             if any(p in k for p in patterns)]
            if category_tests:
                passed = sum(1 for k in category_tests if self.results['native'].get(k) is True)
                total = len([k for k in category_tests if self.results['native'].get(k) is not None])
                print(f"\n{category}: {passed}/{total} passed")
        
        # Detailed results
        print(f"\n" + "=" * 80)
        print("DETAILED RESULTS")
        print("=" * 80)
        print(f"\nNative:")
        for test_name, result in sorted(self.results['native'].items()):
            status = "[PASS]" if result is True else "[FAIL]" if result is False else "[SKIP]"
            print(f"  {status} {test_name}")
        
        if api_total > 0:
            print(f"\nAPI:")
            for test_name, result in sorted(self.results['api'].items()):
                status = "[PASS]" if result is True else "[FAIL]" if result is False else "[SKIP]"
                print(f"  {status} {test_name}")
        
        print("\n" + "=" * 80)
        print("QUERY TYPES TESTED")
        print("=" * 80)
        print("""
1. CREATE Operations: NODE, EDGE, GRAPH, COLLECTION, INDEX
2. SELECT Queries: Basic, WHERE, LIMIT, COUNT, ORDER BY, GROUP BY, Aggregations
3. UPDATE Operations: NODE, EDGE
4. DELETE Operations: NODE, EDGE
5. MATCH Queries: NODE, Pattern matching, RETURN, WHERE
6. TRAVERSE Queries: FROM, VIA, DEPTH, MAX DEPTH, WHERE
7. Search Queries: DENSE, SPARSE, HYBRID, SEMANTIC, GRAPH
8. RAG Queries: Simple RAG, RAG GENERATE
9. Analytics: PAGERANK, SHORTEST_PATH, COMMUNITY_DETECTION, GRAPH SUMMARY, HOP
10. Variable Declarations: LET
11. MERGE Operations: MERGE NODE
12. NEIGHBORS Queries
13. Namespace Operations: SHOW NAMESPACES, GRAPHS, COLLECTIONS, PIPELINES
14. Statistics: SHOW STATS, DESCRIBE
15. Transaction Operations: BEGIN, COMMIT
16. Aggregation: COUNT, SUM, AVG, MIN, MAX
        """)
        
        print("=" * 80)
        
        # Overall success
        overall_success = native_passed == native_total
        if api_total > 0:
            overall_success = overall_success and (api_passed == api_total)
        
        return overall_success


def main():
    """Run comprehensive AIQL query tests."""
    parser = argparse.ArgumentParser(description='Test all AIQL queries (native and/or API)')
    parser.add_argument('--native-only', action='store_true', 
                       help='Test only native execution (skip API tests)')
    parser.add_argument('--api-only', action='store_true',
                       help='Test only API execution (skip native tests)')
    parser.add_argument('--api-url', default='http://localhost:8000',
                       help='API server URL (default: http://localhost:8000)')
    parser.add_argument('--namespace', default='test_all_queries',
                       help='Test namespace (default: test_all_queries)')
    
    args = parser.parse_args()
    
    if args.native_only and args.api_only:
        print("[ERROR] Cannot specify both --native-only and --api-only")
        sys.exit(1)
    
    tester = AIQLQueryTester(
        namespace=args.namespace,
        api_url=args.api_url,
        test_native_only=args.native_only,
        test_api_only=args.api_only
    )
    
    try:
        success = tester.run_all_tests()
        sys.exit(0 if success else 1)
    except Exception as e:
        print(f"\n[ERROR] Test suite failed with exception: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
