#!/usr/bin/env python3
"""
Script to add priority, prerequisites, and requires_data fields to test cases.
"""
import os
import yaml
from pathlib import Path
from collections import OrderedDict

# Define metadata for each test category
TEST_METADATA = {
    # Level 0 - Infrastructure
    'test_namespace_switch.yaml': {
        'priority': 0,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': []
    },
    
    # Level 1 - Basic CRUD
    'test_basic_create_node.yaml': {
        'priority': 1,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': []
    },
    'test_basic_select.yaml': {
        'priority': 1,
        'prerequisites': ['CREATE NODE TestNode { name: "test1", value: 1 }'],
        'requires_data': False,
        'requires_files': []
    },
    
    # Level 2 - Relationships
    'test_create_edge.yaml': {
        'priority': 2,
        'prerequisites': [
            'CREATE NODE TestNode { name: "node1" } AS a',
            'CREATE NODE TestNode { name: "node2" } AS b'
        ],
        'requires_data': False,
        'requires_files': []
    },
    
    # Level 4 - Pipelines (need input files)
    'test_pipeline_cascaded.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_fixed_size_chunking.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_gpt4_chunking.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_hybrid_indexing.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_large_embedding.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_paragraph_chunking.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_recursive_chunking.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_run_pipeline.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_section_based_chunking.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_semantic_chunking.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_with_entity_extraction.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_pipeline_with_relationships.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    
    # Level 4b - TCS Ingestion (need input files)
    'test_tcs_ingestion_fixed_size.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_tcs_ingestion_recursive.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_tcs_ingestion_section.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    'test_tcs_ingestion_semantic.yaml': {
        'priority': 4,
        'prerequisites': [],
        'requires_data': False,
        'requires_files': ['input-doc/annual-report-2024-2025.pdf']
    },
    
    # Level 5 - RAG queries (need ingested data)
    'test_rag_evaluation.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_bfs_with_eval.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_centrality_eval.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_centrality.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_community_eval.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_community.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_dfs_with_eval.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_multi_hop_eval.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_path.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_search_basic.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_search_bfs.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_search_dfs.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_search_multi_hop.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_search_shortest_path.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_search_weighted.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_shortest_path_eval.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_traversal.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_graph_weighted_eval.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_hybrid_search.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_hybrid_with_prompt_embedding.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_keyword_search.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_multi_hop_graph.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_path_based.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_profile_based.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_vector_search.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_weighted_graph.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_rag_weighted_hybrid.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    
    # Level 5b - Evaluation
    'test_evaluation_mrr.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_evaluation_ndcg.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_evaluation_precision.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
    'test_evaluation_recall.yaml': {
        'priority': 5,
        'prerequisites': ['RUN_INGESTION_PIPELINE'],
        'requires_data': True,
        'requires_files': []
    },
}

# Default metadata for any test not explicitly listed (level 6 - skipped tests)
DEFAULT_METADATA = {
    'priority': 6,
    'prerequisites': [],
    'requires_data': False,
    'requires_files': []
}

def add_metadata():
    """Add metadata fields to test files."""
    cases_dir = Path(__file__).parent / 'cases'
    
    updated_count = 0
    
    for test_file in sorted(cases_dir.glob('test_*.yaml')):
        # Read the test file
        with open(test_file, 'r', encoding='utf-8') as f:
            content = f.read()
            
        # Parse YAML
        try:
            test_data = yaml.safe_load(content)
        except yaml.YAMLError as e:
            print(f"❌ Error parsing {test_file.name}: {e}")
            continue
        
        # Get metadata for this test
        metadata = TEST_METADATA.get(test_file.name, DEFAULT_METADATA)
        
        # Add metadata fields if not already present
        updated = False
        if 'priority' not in test_data:
            test_data['priority'] = metadata['priority']
            updated = True
        if 'prerequisites' not in test_data:
            test_data['prerequisites'] = metadata['prerequisites']
            updated = True
        if 'requires_data' not in test_data:
            test_data['requires_data'] = metadata['requires_data']
            updated = True
        if 'requires_files' not in test_data:
            test_data['requires_files'] = metadata['requires_files']
            updated = True
        
        if updated:
            # Write back to file
            with open(test_file, 'w', encoding='utf-8') as f:
                yaml.dump(test_data, f, default_flow_style=False, sort_keys=False, allow_unicode=True)
            
            print(f"✅ Updated {test_file.name} - priority {metadata['priority']}")
            updated_count += 1
        else:
            print(f"⏭️  {test_file.name} - already has metadata")
    
    print(f"\n📊 Summary:")
    print(f"  - Updated: {updated_count} files")

if __name__ == '__main__':
    add_metadata()

