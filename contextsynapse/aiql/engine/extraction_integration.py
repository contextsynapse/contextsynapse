"""
Extraction Integration

Integrates the AIQL Extraction & Normalization Subsystem (Stage-1) with AIQL executor.
"""

from typing import Dict, Any, Optional, List
import logging
from pathlib import Path
import json

logger = logging.getLogger(__name__)


def integrate_extraction_subsystem(
    stage: Dict[str, Any],
    namespace: str,
    source_collection: str,
    target_collection: str,
    namespace_graph: Any
) -> Dict[str, Any]:
    """
    Integrate the new extraction subsystem with AIQL executor.
    
    This function bridges the new aiql_extraction package with the existing
    AIQL executor's _execute_extract_stage method.
    
    Args:
        stage: Stage configuration from pipeline
        namespace: Namespace name
        source_collection: Source collection name
        target_collection: Target collection name
        namespace_graph: AIContextDB graph instance
        
    Returns:
        Dictionary with extraction results and created nodes
    """
    try:
        from contextsynapse.extraction import (
            ExtractionConfig,
            ExtractionMode,
            run_extraction,
            FileManager,
            Normalizer
        )
    except ImportError:
        logger.warning("contextsynapse.extraction package not available, falling back to legacy extraction")
        return None
    
    # Parse stage configuration
    from_file = stage.get('from_file', '') or stage.get('input_file', '')
    if isinstance(from_file, str):
        from_file = from_file.strip('"').strip("'")
    
    if not from_file:
        logger.error("No file path specified in EXTRACT stage")
        return None
    
    # Resolve file path
    file_path = _resolve_file_path(from_file)
    if not Path(file_path).exists():
        logger.error(f"File not found: {file_path}")
        return None
    
    # Parse extraction mode
    pages_mode = ExtractionMode.FULL
    pages_range = None
    pages_list = None
    
    # Check for pages_range first (parsed from PAGES 1..3 syntax)
    if 'pages_range' in stage:
        pages_range_list = stage['pages_range']
        if isinstance(pages_range_list, (list, tuple)) and len(pages_range_list) >= 2:
            pages_mode = ExtractionMode.RANGE
            pages_range = tuple(pages_range_list[:2])  # Take first 2 elements
            logger.info(f"[EXTRACT] Parsed pages_range from stage: {pages_range}")
            print(f"[EXTRACT] Parsed pages_range: {pages_range}")
    
    # Check for pages_list (parsed from PAGES [1,3,5] syntax)
    if 'pages_list' in stage:
        pages_mode = ExtractionMode.LIST
        pages_list = stage['pages_list']
        logger.info(f"[EXTRACT] Parsed pages_list from stage: {pages_list}")
        print(f"[EXTRACT] Parsed pages_list: {pages_list}")
    
    # Check for mode and pages_range/pages_list (alternative syntax)
    if 'mode' in stage:
        mode_str = stage['mode'].upper()
        if mode_str == 'RANGE':
            pages_mode = ExtractionMode.RANGE
            if 'pages_range' in stage and not pages_range:
                pages_range_list = stage['pages_range']
                if isinstance(pages_range_list, (list, tuple)) and len(pages_range_list) >= 2:
                    pages_range = tuple(pages_range_list[:2])
        elif mode_str == 'LIST':
            pages_mode = ExtractionMode.LIST
            if 'pages_list' in stage and not pages_list:
                pages_list = stage['pages_list']
    
    # Fallback: Check for 'pages' key (legacy or alternative format)
    elif 'pages' in stage and not pages_range and not pages_list:
        # Support PAGES [1,3,5] or PAGES 1..10 syntax
        pages_spec = stage['pages']
        if isinstance(pages_spec, list):
            pages_mode = ExtractionMode.LIST
            pages_list = pages_spec
        elif isinstance(pages_spec, str) and '..' in pages_spec:
            pages_mode = ExtractionMode.RANGE
            start, end = pages_spec.split('..')
            pages_range = (int(start.strip()), int(end.strip()))
    
    # Parse reader
    reader = "AUTO"
    if 'using' in stage:
        using = stage['using']
        if isinstance(using, dict) and 'reader' in using:
            reader = using['reader']
        elif isinstance(using, str):
            reader = using
    
    # Parse detect modalities
    detect = ["TEXT"]
    if 'detect' in stage:
        detect_list = stage['detect']
        if isinstance(detect_list, list):
            detect = [d.upper() for d in detect_list]
        elif isinstance(detect_list, str):
            detect = [d.strip().upper() for d in detect_list.split(',')]
    
    # Parse metadata parsing
    parse_metadata = stage.get('parse_metadata', True)
    if isinstance(parse_metadata, str):
        parse_metadata = parse_metadata.upper() == 'TRUE'
    
    # Parse output directory
    # Use namespace-aware base directory (contextcore_data)
    output_dir = stage.get('output_dir', 'contextcore_data')
    if isinstance(output_dir, str):
        output_dir = output_dir.strip('"').strip("'")
    
    # Create extraction config
    config = ExtractionConfig(
        file_path=file_path,
        reader=reader,
        detect=detect,
        pages_mode=pages_mode,
        pages_range=pages_range,
        pages_list=pages_list,
        parse_metadata=parse_metadata,
        output_dir=output_dir,
        namespace=namespace,  # Pass namespace for hybrid storage
    )
    
    # Run extraction
    logger.info(f"Running extraction subsystem for: {file_path}")
    print(f"[EXTRACT] Running extraction subsystem for: {file_path}")
    try:
        document_id = run_extraction(config)
        print(f"[EXTRACT] Extraction completed, document_id: {document_id}")
        logger.info(f"Extraction completed, document_id: {document_id}")
    except Exception as e:
        logger.error(f"Extraction failed: {e}")
        print(f"[EXTRACT] ERROR: Extraction failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        print(traceback.format_exc())
        return None
    
    # Load normalized document (try hybrid storage first, fallback to file system)
    print(f"[EXTRACT] Loading normalized document for document_id: {document_id}")
    
    # Try hybrid storage first
    normalized_doc = None
    normalized_file = None
    
    try:
        from contextsynapse.extraction.normalized_store import NormalizedDocumentStore
        # NormalizedDocumentStore uses namespace-aware paths: base_dir/namespaces/{namespace}/documents
        # Ensure we use contextcore_data as base_dir for proper namespace structure
        base_dir = output_dir or 'contextcore_data'
        store = NormalizedDocumentStore(namespace=namespace, base_dir=base_dir)
        normalized_doc = store.get(document_id, version=None, load_full=True)
        if normalized_doc:
            print(f"[EXTRACT] Loaded from hybrid storage: {document_id}")
            # Get file path for backward compatibility - use namespace-aware path
            # FileManager should use namespace-aware structure: base_dir/namespaces/{namespace}/documents/{document_id}
            # Construct namespace-aware path first
            namespace_doc_path = Path(base_dir) / "namespaces" / namespace / "documents" / document_id
            file_manager = FileManager(document_id, base_dir, namespace=namespace)
            if namespace_doc_path.exists():
                # Try to find Parquet file first, then JSON
                normalized_dir = namespace_doc_path / "normalized"
                parquet_files = list(normalized_dir.glob("normalized_*.parquet")) if normalized_dir.exists() else []
                if parquet_files:
                    parquet_files.sort(reverse=True)
                    normalized_file = parquet_files[0]
                    print(f"[EXTRACT] Found Parquet file path from hybrid storage: {normalized_file}")
                else:
                    versions = file_manager.list_normalized_versions()
                    if versions:
                        latest_version = versions[-1]
                        normalized_file = namespace_doc_path / "normalized" / f"normalized_{latest_version}.json"
    except Exception as e:
        logger.warning(f"Hybrid storage load failed, falling back to file system: {e}")
    
    # Fallback to file system - use namespace-aware paths
    if normalized_doc is None:
        base_dir = output_dir or 'contextcore_data'
        # Try namespace-aware path first: base_dir/namespaces/{namespace}/documents/{document_id}/normalized
        namespace_doc_path = Path(base_dir) / "namespaces" / namespace / "documents" / document_id
        normalized_dir = namespace_doc_path / "normalized"
        
        if normalized_dir.exists():
            # Try Parquet files first (preferred format)
            parquet_files = list(normalized_dir.glob("normalized_*.parquet"))
            if parquet_files:
                # Use latest Parquet file
                parquet_files.sort(reverse=True)
                parquet_file = parquet_files[0]
                # Load metadata to get document info
                metadata_file = normalized_dir / f"{parquet_file.stem}.metadata.json"
                if metadata_file.exists():
                    with open(metadata_file, 'r', encoding='utf-8') as f:
                        metadata = json.load(f)
                    # Create minimal normalized_doc structure from metadata
                    normalized_doc = {
                        'document_id': metadata.get('document_id', document_id),
                        'version': metadata.get('version', 'v1'),
                        'content': {
                            'pages': [],  # Pages are in Parquet, will be loaded by chunking stage
                            'tables': [],
                            'images': []
                        },
                        'metadata': metadata.get('metadata', {}),
                        'parquet_file': str(parquet_file)
                    }
                    normalized_file = parquet_file  # Return Parquet file path
                    print(f"[EXTRACT] Found Parquet file (namespace-aware): {parquet_file}")
                    print(f"[EXTRACT] Parquet contains {metadata.get('page_count', 0)} pages")
                    print(f"[EXTRACT] DEBUG: normalized_doc set from Parquet, normalized_doc is not None: {normalized_doc is not None}")
                    # Parquet file found and normalized_doc set, skip JSON fallback
                else:
                    logger.warning(f"Parquet file found but metadata.json missing: {parquet_file}")
                    # Still use Parquet file, chunking stage can handle it
                    normalized_file = parquet_file
                    normalized_doc = {
                        'document_id': document_id,
                        'version': 'v1',
                        'content': {'pages': [], 'tables': [], 'images': []},
                        'parquet_file': str(parquet_file)
                    }
                    print(f"[EXTRACT] Found Parquet file (no metadata): {parquet_file}")
                    # Parquet file found and normalized_doc set, skip JSON fallback
            
            # Fallback to JSON files if Parquet not found
            print(f"[EXTRACT] DEBUG: Before JSON fallback check, normalized_doc is None: {normalized_doc is None}")
            if normalized_doc is None:
                # Fallback to JSON files (skip metadata.json files)
                versions_files = [f for f in normalized_dir.glob("normalized_*.json") 
                                 if not f.name.endswith('.metadata.json')]
                if versions_files:
                    # Use latest version file
                    versions_files.sort(reverse=True)
                    normalized_file = versions_files[0]
                    with open(normalized_file, 'r', encoding='utf-8') as f:
                        normalized_doc = json.load(f)
                    print(f"[EXTRACT] Loaded from file system (namespace-aware): {normalized_file}")
                else:
                    logger.error(f"No normalized versions found for document: {document_id}")
                    print(f"[EXTRACT] ERROR: No normalized versions found for document: {document_id}")
                    return None
        else:
            # Fallback to legacy FileManager structure (for backward compatibility)
            file_manager = FileManager(document_id, base_dir, namespace=namespace)
            versions = file_manager.list_normalized_versions()
            print(f"[EXTRACT] Found {len(versions)} normalized versions")
            if not versions:
                logger.error(f"No normalized versions found for document: {document_id}")
                print(f"[EXTRACT] ERROR: No normalized versions found for document: {document_id}")
                return None
            
            latest_version = versions[-1]
            normalized_file = file_manager.workspace / "normalized" / f"normalized_{latest_version}.json"
            
            with open(normalized_file, 'r', encoding='utf-8') as f:
                normalized_doc = json.load(f)
            print(f"[EXTRACT] Loaded from file system (legacy): {normalized_file}")
    
    # Stage-1 (EXTRACT): Only extract and normalize - DO NOT create graph nodes
    # Node creation will happen in a later stage (e.g., CHUNK, CONNECT, or dedicated NODE_CREATE stage)
    # 
    # The extraction subsystem's job is to:
    # 1. Extract content from files
    # 2. Normalize to universal JSON format
    # 3. Store assets (pages, tables, images) in their respective storage
    # 4. Return normalized document reference for later stages
    
    # Extract normalized version from document or file path
    normalized_version = normalized_doc.get('version', 'v1')
    # If version is in format "normalized_v1", extract just "v1"
    if normalized_version.startswith('normalized_'):
        normalized_version = normalized_version.replace('normalized_', '')
    
    # Also try to extract from file path if available (support both .json and .parquet)
    if normalized_file and normalized_version == 'v1':
        import re
        # Match both .json and .parquet files
        version_match = re.search(r'normalized_(\w+)\.(json|parquet)', str(normalized_file))
        if version_match:
            normalized_version = version_match.group(1)
            # Remove "normalized_" prefix if present
            if normalized_version.startswith('normalized_'):
                normalized_version = normalized_version.replace('normalized_', '')
    
    logger.info(f"Extraction complete. Document ID: {document_id}, Pages: {len(normalized_doc.get('content', {}).get('pages', []))}")
    print(f"[EXTRACT] Extraction complete. Document ID: {document_id}")
    print(f"[EXTRACT] Pages extracted: {len(normalized_doc.get('content', {}).get('pages', []))}")
    print(f"[EXTRACT] Tables extracted: {len(normalized_doc.get('content', {}).get('tables', []))}")
    print(f"[EXTRACT] Images extracted: {len(normalized_doc.get('content', {}).get('images', []))}")
    print(f"[EXTRACT] Normalized version: {normalized_version}")
    file_type = "Parquet" if str(normalized_file).endswith('.parquet') else "JSON"
    print(f"[EXTRACT] Normalized {file_type} stored at: {normalized_file}")
    print(f"[EXTRACT] NOTE: Graph nodes will be created in a later pipeline stage")
    
    return {
        "status": "success",
        "document_id": document_id,
        "normalized_version": normalized_version,
        "normalized_file": str(normalized_file),
        "normalized_doc": normalized_doc,
        "pages_count": len(normalized_doc.get('content', {}).get('pages', [])),
        "tables_count": len(normalized_doc.get('content', {}).get('tables', [])),
        "images_count": len(normalized_doc.get('content', {}).get('images', [])),
        # Do not return document_nodes, table_nodes, image_nodes, edges
        # These will be created in a later stage
    }


def _resolve_file_path(file_path: str) -> str:
    """Resolve file path (relative, absolute, or with default prefix)."""
    path = Path(file_path)
    
    if path.is_absolute() and path.exists():
        return str(path)
    
    # Try relative to current directory
    if path.exists():
        return str(path.absolute())
    
    # Try with input-doc/ prefix
    input_doc_path = Path("input-doc") / path.name
    if input_doc_path.exists():
        return str(input_doc_path.absolute())
    
    return file_path


def _create_document_node(page: Dict[str, Any], document_id: str, namespace: str, collection: str) -> Any:
    """Create a Document node from normalized page."""
    from ...core.graph_structures import GraphNode
    
    node_id = f"{document_id}_page_{page.get('page_no', 1)}"
    
    properties = {
        "type": "Document",
        "document_id": document_id,
        "page_no": page.get('page_no', 1),
        "text": page.get('flat_text', ''),
        "markdown": page.get('markdown', ''),
        "namespace": namespace,
        "collection": collection,
    }
    
    return GraphNode(
        id=node_id,
        label="Document",
        properties=properties
    )


def _create_table_node(table: Dict[str, Any], document_id: str, namespace: str, collection: str) -> Any:
    """Create a Table node from normalized table."""
    from ...core.graph_structures import GraphNode
    
    table_id = table.get('id', 'tbl_01')
    node_id = f"{document_id}_{table_id}"
    
    properties = {
        "type": "Table",
        "document_id": document_id,
        "table_id": table_id,
        "from_page": table.get('from_page', 1),
        "data": table.get('data', []),
        "markdown": table.get('markdown', ''),
        "namespace": namespace,
        "collection": collection,
    }
    
    return GraphNode(
        id=node_id,
        label="Table",
        properties=properties
    )


def _create_image_node(image: Dict[str, Any], document_id: str, namespace: str, collection: str) -> Any:
    """Create an Image node from normalized image."""
    from ...core.graph_structures import GraphNode
    
    image_id = image.get('id', 'img_01')
    node_id = f"{document_id}_{image_id}"
    
    properties = {
        "type": "Image",
        "document_id": document_id,
        "image_id": image_id,
        "page_no": image.get('page_no', 1),
        "path": image.get('path', ''),
        "ocr_text": image.get('ocr_text', ''),
        "namespace": namespace,
        "collection": collection,
    }
    
    return GraphNode(
        id=node_id,
        label="Image",
        properties=properties
    )


def _create_link_edge(source: Any, target: Any, edge_type: str) -> Any:
    """Create a link edge between nodes."""
    from ...core.graph_structures import GraphEdge
    
    return GraphEdge(
        id=f"{source.id}_to_{target.id}_{edge_type}",
        label=edge_type,
        source=source.id,
        target=target.id,
        properties={}
    )

