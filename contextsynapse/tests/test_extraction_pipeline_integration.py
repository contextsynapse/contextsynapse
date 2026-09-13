"""
Test Extraction Subsystem Integration with AIQL Pipelines

Tests:
1. Integration with AIQL EXTRACT stage
2. Different extraction modes (FULL, RANGE, LIST)
3. Storage verification in graph
4. Data persistence
"""

import sys
import os
from pathlib import Path
import json
import time

# Add project root to path
project_root = Path(__file__).parent.parent
sys.path.insert(0, str(project_root))

from contextsynapse.core.hybrid_graph_storage import AIContextDB
from contextsynapse.aiql.engine.executor import AIQLExecutor


class ExtractionPipelineTester:
    """Test extraction subsystem with AIQL pipelines."""
    
    def __init__(self):
        self.namespace = "test_extraction_integration"
        self.pdf_file = "input-doc/OTC_TCS_2024.pdf"
        self.executor = None
        self.graph = None
        
    def setup(self):
        """Setup test environment."""
        print("=" * 80)
        print("SETUP: Creating namespace and executor")
        print("=" * 80)
        
        # Create namespace
        self.graph = AIContextDB(name=self.namespace, storage_backend='csr')
        self.executor = AIQLExecutor(contextcore=self.graph)
        self.executor.active_namespace = self.namespace
        
        # Create namespace via AIQL
        query = f"CREATE NAMESPACE {self.namespace};"
        result = self.executor.execute(query)
        print(f"[OK] Namespace created: {self.namespace}")
        
        # Use namespace
        query = f"USE NAMESPACE {self.namespace};"
        result = self.executor.execute(query)
        print(f"[OK] Using namespace: {self.namespace}")
        
    def test_mode_full(self):
        """Test FULL extraction mode (first 2 pages for speed and debugging)."""
        print("\n" + "=" * 80)
        print("TEST 1: FULL Mode (Pages 1-2)")
        print("=" * 80)
        
        pipeline_name = "test_extract_full"
        
        # Create pipeline with EXTRACT + CONNECT stages
        query = f"""
CREATE PIPELINE {pipeline_name}
IN NAMESPACE {self.namespace}
SOURCE COLLECTION raw_docs
TARGET COLLECTION processed_docs
DESCRIPTION "Test FULL extraction mode with CONNECT"
STAGES = [
STEP extract
    EXTRACT FROM FILE "{self.pdf_file}"
    PAGES 1..2
    USING READER "AUTO"
    DETECT (TEXT, TABLES, IMAGES)
    PARSE_METADATA TRUE
    STORE INTERMEDIATE IN COLLECTION raw_docs,

STEP connect
    CONNECT FROM NORMALIZED
    CREATE NODES (Document, Table, Image)
    LINK (Document TO Table, Document TO Image)
    STORE INTERMEDIATE IN COLLECTION raw_docs
]
"""
        
        print("\n[1] Creating pipeline...")
        try:
            result = self.executor.execute(query)
            print(f"[OK] Pipeline created: {pipeline_name}")
        except Exception as e:
            print(f"[ERROR] Pipeline creation failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Run full pipeline (both EXTRACT and CONNECT stages)
        print("\n[2] Running full pipeline (EXTRACT + CONNECT stages)...")
        run_query = f"""
USE NAMESPACE {self.namespace};
RUN PIPELINE {pipeline_name}
"""
        
        try:
            start_time = time.time()
            result = self.executor.execute(run_query)
            elapsed = time.time() - start_time
            print(f"[OK] Pipeline executed in {elapsed:.2f}s")
            
            # Debug: Check extraction results
            if result and isinstance(result, dict):
                extract_result = result.get('extract', {})
                if extract_result:
                    tables_count = extract_result.get('tables_count', 0)
                    pages_count = extract_result.get('pages_count', 0)
                    images_count = extract_result.get('images_count', 0)
                    print(f"[DEBUG] Pages extracted: {pages_count}")
                    print(f"[DEBUG] Tables extracted: {tables_count}")
                    print(f"[DEBUG] Images extracted: {images_count}")
                
                # Debug: Check node creation results
                connect_result = result.get('connect', {})
                if connect_result:
                    table_nodes = connect_result.get('table_nodes', [])
                    doc_nodes = connect_result.get('document_nodes', [])
                    image_nodes = connect_result.get('image_nodes', [])
                    print(f"[DEBUG] Document nodes created: {len(doc_nodes) if isinstance(doc_nodes, list) else doc_nodes}")
                    print(f"[DEBUG] Table nodes created: {len(table_nodes) if isinstance(table_nodes, list) else table_nodes}")
                    print(f"[DEBUG] Image nodes created: {len(image_nodes) if isinstance(image_nodes, list) else image_nodes}")
        except Exception as e:
            print(f"[ERROR] Pipeline execution failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Verify storage
        print("\n[3] Verifying storage...")
        return self._verify_storage("FULL mode")
    
    def test_mode_range(self):
        """Test RANGE extraction mode."""
        print("\n" + "=" * 80)
        print("TEST 2: RANGE Mode (Pages 1-2)")
        print("=" * 80)
        
        pipeline_name = "test_extract_range"
        
        # Create pipeline with RANGE mode
        query = f"""
CREATE PIPELINE {pipeline_name}
IN NAMESPACE {self.namespace}
SOURCE COLLECTION raw_docs
TARGET COLLECTION processed_docs
DESCRIPTION "Test RANGE extraction mode"
STAGES = [
STEP extract
    EXTRACT FROM FILE "{self.pdf_file}"
    PAGES 1..2
    USING READER "AUTO"
    DETECT (TEXT, TABLES)
    PARSE_METADATA TRUE
    STORE AS NODE TYPES (Document, Table)
    LINK (Document TO Table)
    STORE INTERMEDIATE IN COLLECTION raw_docs
]
"""
        
        print("\n[1] Creating pipeline...")
        try:
            result = self.executor.execute(query)
            print(f"[OK] Pipeline created: {pipeline_name}")
        except Exception as e:
            print(f"[ERROR] Pipeline creation failed: {e}")
            return False
        
        # Run pipeline
        print("\n[2] Running pipeline...")
        run_query = f"""
USE NAMESPACE {self.namespace};
RUN PIPELINE {pipeline_name}
FROM STEP extract
"""
        
        try:
            start_time = time.time()
            result = self.executor.execute(run_query)
            elapsed = time.time() - start_time
            print(f"[OK] Pipeline executed in {elapsed:.2f}s")
        except Exception as e:
            print(f"[ERROR] Pipeline execution failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Verify storage
        print("\n[3] Verifying storage...")
        return self._verify_storage("RANGE mode")
    
    def test_mode_list(self):
        """Test LIST extraction mode."""
        print("\n" + "=" * 80)
        print("TEST 3: LIST Mode (Pages 1, 2)")
        print("=" * 80)
        
        pipeline_name = "test_extract_list"
        
        # Create pipeline with LIST mode
        query = f"""
CREATE PIPELINE {pipeline_name}
IN NAMESPACE {self.namespace}
SOURCE COLLECTION raw_docs
TARGET COLLECTION processed_docs
DESCRIPTION "Test LIST extraction mode"
STAGES = [
STEP extract
    EXTRACT FROM FILE "{self.pdf_file}"
    PAGES [1, 2]
    USING READER "AUTO"
    DETECT (TEXT)
    PARSE_METADATA TRUE
    STORE AS NODE TYPES (Document)
    STORE INTERMEDIATE IN COLLECTION raw_docs
]
"""
        
        print("\n[1] Creating pipeline...")
        try:
            result = self.executor.execute(query)
            print(f"[OK] Pipeline created: {pipeline_name}")
        except Exception as e:
            print(f"[ERROR] Pipeline creation failed: {e}")
            return False
        
        # Run pipeline
        print("\n[2] Running pipeline...")
        run_query = f"""
USE NAMESPACE {self.namespace};
RUN PIPELINE {pipeline_name}
FROM STEP extract
"""
        
        try:
            start_time = time.time()
            result = self.executor.execute(run_query)
            elapsed = time.time() - start_time
            print(f"[OK] Pipeline executed in {elapsed:.2f}s")
        except Exception as e:
            print(f"[ERROR] Pipeline execution failed: {e}")
            import traceback
            traceback.print_exc()
            return False
        
        # Verify storage
        print("\n[3] Verifying storage...")
        return self._verify_storage("LIST mode")
    
    def _verify_storage(self, test_name: str):
        """Verify that nodes are stored in the graph."""
        print(f"\n[VERIFY] Checking storage for {test_name}...")
        
        # Check Document nodes
        query = f"""
USE NAMESPACE {self.namespace};
SELECT COUNT(*) FROM Document
"""
        try:
            result = self.executor.execute(query)
            print(f"  [DEBUG] Document query result: {result}")
            doc_count = self._extract_count(result)
            print(f"  [OK] Document nodes: {doc_count}")
        except Exception as e:
            print(f"  [ERROR] Error counting documents: {e}")
            import traceback
            traceback.print_exc()
            doc_count = 0
        
        # Check Table nodes
        query = f"""
USE NAMESPACE {self.namespace};
SELECT COUNT(*) FROM Table
"""
        try:
            result = self.executor.execute(query)
            print(f"  [DEBUG] Table query result: {result}")
            table_count = self._extract_count(result)
            print(f"  [OK] Table nodes: {table_count}")
        except Exception as e:
            print(f"  [ERROR] Error counting tables: {e}")
            import traceback
            traceback.print_exc()
            table_count = 0
        
        # Check Image nodes
        query = f"""
USE NAMESPACE {self.namespace};
SELECT COUNT(*) FROM Image
"""
        try:
            result = self.executor.execute(query)
            print(f"  [DEBUG] Image query result: {result}")
            image_count = self._extract_count(result)
            print(f"  [OK] Image nodes: {image_count}")
        except Exception as e:
            print(f"  [ERROR] Error counting images: {e}")
            import traceback
            traceback.print_exc()
            image_count = 0
        
        # Check graph storage directly
        print(f"\n[VERIFY] Direct graph storage check...")
        node_index_count = len(self.graph.node_index) if hasattr(self.graph, 'node_index') else 0
        
        # Try to query nodes directly from graph
        print(f"\n[VERIFY] Direct graph node query...")
        try:
            # Try to get nodes by type from node_index
            if hasattr(self.graph, 'node_index'):
                doc_nodes_direct = [nid for nid, node in self.graph.node_index.items() 
                                   if hasattr(node, 'node_type') and node.node_type == 'Document']
                table_nodes_direct = [nid for nid, node in self.graph.node_index.items() 
                                     if hasattr(node, 'node_type') and node.node_type == 'Table']
                image_nodes_direct = [nid for nid, node in self.graph.node_index.items() 
                                     if hasattr(node, 'node_type') and node.node_type == 'Image']
                print(f"  [DEBUG] Direct Document nodes: {len(doc_nodes_direct)}")
                print(f"  [DEBUG] Direct Table nodes: {len(table_nodes_direct)}")
                print(f"  [DEBUG] Direct Image nodes: {len(image_nodes_direct)}")
        except Exception as e:
            print(f"  [WARN] Could not query nodes directly: {e}")
        
        # Try different ways to get CSR count
        csr_count = 0
        if hasattr(self.graph, 'csr_adapter') and self.graph.csr_adapter:
            try:
                # Try different method names
                if hasattr(self.graph.csr_adapter, 'num_nodes'):
                    csr_count = self.graph.csr_adapter.num_nodes()
                elif hasattr(self.graph.csr_adapter, 'get_num_nodes'):
                    csr_count = self.graph.csr_adapter.get_num_nodes()
                elif hasattr(self.graph.csr_adapter, 'node_count'):
                    csr_count = self.graph.csr_adapter.node_count
                else:
                    # Fallback: count from CSR data structures
                    if hasattr(self.graph.csr_adapter, 'num_nodes_data'):
                        csr_count = self.graph.csr_adapter.num_nodes_data
            except Exception as e:
                print(f"  [WARN] Could not get CSR count: {e}")
        
        print(f"  [OK] Nodes in node_index: {node_index_count}")
        print(f"  [OK] Nodes in CSR storage: {csr_count}")
        
        # Check persistence by saving and reloading
        print(f"\n[VERIFY] Testing persistence...")
        try:
            # Save graph - construct file path from namespace
            from pathlib import Path
            storage_path = Path("contextcore_data") / "namespaces" / self.namespace
            storage_path.mkdir(parents=True, exist_ok=True)
            graph_file = storage_path / "graph.h5"
            self.graph.save(str(graph_file))
            print(f"  [OK] Graph saved to {graph_file}")
            
            # Reload graph
            reloaded_graph = AIContextDB(name=self.namespace, storage_backend='csr')
            # Try different ways to get CSR count
            reloaded_count = 0
            if hasattr(reloaded_graph, 'csr_adapter') and reloaded_graph.csr_adapter:
                try:
                    if hasattr(reloaded_graph.csr_adapter, 'num_nodes'):
                        reloaded_count = reloaded_graph.csr_adapter.num_nodes()
                    elif hasattr(reloaded_graph.csr_adapter, 'get_num_nodes'):
                        reloaded_count = reloaded_graph.csr_adapter.get_num_nodes()
                    elif hasattr(reloaded_graph.csr_adapter, 'node_count'):
                        reloaded_count = reloaded_graph.csr_adapter.node_count
                    elif hasattr(reloaded_graph.csr_adapter, 'num_nodes_data'):
                        reloaded_count = reloaded_graph.csr_adapter.num_nodes_data
                except:
                    pass
            print(f"  [OK] Graph reloaded: {reloaded_count} nodes")
            
            if reloaded_count == node_index_count:
                print(f"  [OK] Persistence verified: {reloaded_count} nodes persisted")
            else:
                print(f"  [WARN] Persistence mismatch: {node_index_count} saved vs {reloaded_count} loaded")
        except Exception as e:
            print(f"  [ERROR] Persistence test failed: {e}")
            import traceback
            traceback.print_exc()
        
        # Summary
        print(f"\n[SUMMARY] {test_name}:")
        print(f"  Documents: {doc_count}")
        print(f"  Tables: {table_count}")
        print(f"  Images: {image_count}")
        print(f"  Total in graph: {node_index_count}")
        
        return doc_count > 0 or table_count > 0 or image_count > 0
    
    def _extract_count(self, result):
        """Extract count from query result."""
        # Handle nested result structure: {'use_namespace': {...}, 'select': [...]}
        if isinstance(result, dict):
            # Check if result has nested 'select' key (from multi-statement queries)
            if 'select' in result:
                select_result = result['select']
                if isinstance(select_result, list) and len(select_result) > 0:
                    first_row = select_result[0]
                    if isinstance(first_row, dict):
                        # Try common count field names
                        for key in ['count', 'COUNT(*)', 'count(*)', 'total']:
                            if key in first_row:
                                return first_row[key]
                        # Return first numeric value
                        for value in first_row.values():
                            if isinstance(value, (int, float)):
                                return int(value)
            # Check for 'data' key
            elif 'data' in result:
                data = result['data']
                if isinstance(data, list) and len(data) > 0:
                    first_row = data[0]
                    if isinstance(first_row, dict):
                        # Try common count field names
                        for key in ['count', 'COUNT(*)', 'count(*)', 'total']:
                            if key in first_row:
                                return first_row[key]
                        # Return first numeric value
                        for value in first_row.values():
                            if isinstance(value, (int, float)):
                                return int(value)
            elif 'count' in result:
                return result['count']
        elif isinstance(result, (int, float)):
            return int(result)
        elif isinstance(result, list) and len(result) > 0:
            if isinstance(result[0], dict):
                # Try common count field names
                for key in ['count', 'COUNT(*)', 'count(*)', 'total']:
                    if key in result[0]:
                        return result[0][key]
                return result[0].get('count', 0)
            elif isinstance(result[0], (int, float)):
                return int(result[0])
        return 0
    
    def test_persistence(self):
        """Test that data persists after graph reload."""
        print("\n" + "=" * 80)
        print("TEST 4: Persistence Verification")
        print("=" * 80)
        
        # Get current node count using CSR storage (consistent with reload check)
        current_count = 0
        if hasattr(self.graph, 'csr_adapter') and self.graph.csr_adapter:
            try:
                if hasattr(self.graph.csr_adapter.csr_storage, 'get_node_count'):
                    current_count = self.graph.csr_adapter.csr_storage.get_node_count()
                elif hasattr(self.graph.csr_adapter, 'get_statistics'):
                    stats = self.graph.csr_adapter.get_statistics()
                    current_count = stats.get('node_count', 0)
                elif hasattr(self.graph.csr_adapter.csr_storage, 'nodes'):
                    current_count = len(self.graph.csr_adapter.csr_storage.nodes)
            except Exception as e:
                print(f"  [WARN] Could not get current count: {e}")
        
        # Also show node_index count for reference
        node_index_count = len(self.graph.node_index) if hasattr(self.graph, 'node_index') else 0
        print(f"\n[1] Current nodes in memory (CSR): {current_count}, (node_index): {node_index_count}")
        
        # Save graph
        print("\n[2] Saving graph...")
        try:
            from pathlib import Path
            storage_path = Path("contextcore_data") / "namespaces" / self.namespace
            storage_path.mkdir(parents=True, exist_ok=True)
            graph_file = storage_path / "graph.h5"
            self.graph.save(str(graph_file))
            print(f"[OK] Graph saved to {graph_file}")
        except Exception as e:
            print(f"[ERROR] Save failed: {e}")
            return False
        
        # Reload graph
        print("\n[3] Reloading graph...")
        try:
            reloaded_graph = AIContextDB(name=self.namespace, storage_backend='csr')
            # Try different ways to get CSR count (consistent with _verify_storage)
            reloaded_count = 0
            if hasattr(reloaded_graph, 'csr_adapter') and reloaded_graph.csr_adapter:
                try:
                    if hasattr(reloaded_graph.csr_adapter.csr_storage, 'get_node_count'):
                        reloaded_count = reloaded_graph.csr_adapter.csr_storage.get_node_count()
                    elif hasattr(reloaded_graph.csr_adapter, 'get_statistics'):
                        stats = reloaded_graph.csr_adapter.get_statistics()
                        reloaded_count = stats.get('node_count', 0)
                    elif hasattr(reloaded_graph.csr_adapter.csr_storage, 'nodes'):
                        reloaded_count = len(reloaded_graph.csr_adapter.csr_storage.nodes)
                except Exception as e:
                    print(f"  [WARN] Could not get reloaded count: {e}")
            
            print(f"[OK] Graph reloaded: {reloaded_count} nodes")
            
            if reloaded_count == current_count:
                print(f"[OK] Persistence verified: All {reloaded_count} nodes persisted")
                return True
            else:
                print(f"[WARN] Mismatch: {current_count} saved vs {reloaded_count} loaded")
                return False
        except Exception as e:
            print(f"[ERROR] Reload failed: {e}")
            import traceback
            traceback.print_exc()
            return False
    
    def run_simple_test(self):
        """Run a simple test with pages 1-2 to debug table extraction."""
        print("\n" + "=" * 80)
        print("SIMPLE EXTRACTION TEST (Pages 1-2)")
        print("=" * 80)
        
        # Setup
        self.setup()
        
        # Run only the full test with debugging
        result = self.test_mode_full()
        
        print("\n" + "=" * 80)
        if result:
            print("[SUCCESS] TEST PASSED")
        else:
            print("[FAILURE] TEST FAILED")
        print("=" * 80)
        
        return result
    
    def run_all_tests(self):
        """Run all tests."""
        print("\n" + "=" * 80)
        print("EXTRACTION SUBSYSTEM PIPELINE INTEGRATION TESTS")
        print("=" * 80)
        
        # Setup
        self.setup()
        
        # Run tests
        results = {}
        results['full'] = self.test_mode_full()
        results['range'] = self.test_mode_range()
        results['list'] = self.test_mode_list()
        results['persistence'] = self.test_persistence()
        
        # Summary
        print("\n" + "=" * 80)
        print("TEST SUMMARY")
        print("=" * 80)
        for test_name, passed in results.items():
            status = "[PASSED]" if passed else "[FAILED]"
            print(f"{test_name.upper()}: {status}")
        
        all_passed = all(results.values())
        print("\n" + "=" * 80)
        if all_passed:
            print("[SUCCESS] ALL TESTS PASSED")
        else:
            print("[FAILURE] SOME TESTS FAILED")
        print("=" * 80)
        
        return all_passed


if __name__ == "__main__":
    import sys
    tester = ExtractionPipelineTester()
    
    # Run simple test by default (faster, better for debugging)
    if len(sys.argv) > 1 and sys.argv[1] == "--all":
        success = tester.run_all_tests()
    else:
        success = tester.run_simple_test()
    
    sys.exit(0 if success else 1)

