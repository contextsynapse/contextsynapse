"""
Ray Remote Functions for Pipeline Stages

Distributed execution of pipeline stages using Ray.
"""

import logging
from typing import Dict, List, Any, Optional
import json
from pathlib import Path
import time

logger = logging.getLogger(__name__)

# Try to import Ray
try:
    import ray
    RAY_AVAILABLE = True
except ImportError:
    RAY_AVAILABLE = False
    logger.warning("Ray not available. Install with: pip install ray")


def _init_ray_if_needed() -> bool:
    """
    Initialize Ray if not already initialized with optimized configuration.
    
    Uses configuration from ray.yaml if available, otherwise uses sensible defaults.
    """
    if not RAY_AVAILABLE:
        return False
    
    if not ray.is_initialized():
        try:
            # Try to load configuration
            ray_config = None
            try:
                from ...ray.ray_config import RayConfigLoader
                ray_config = RayConfigLoader.load_config()
            except:
                pass
            
            # Configure Ray with optimal settings for chunking
            init_kwargs = {
                "ignore_reinit_error": True,
                "num_cpus": None,  # Use all available CPUs
                "object_store_memory": 2 * 1024 * 1024 * 1024,  # 2GB object store
                "include_dashboard": False,  # Disable dashboard for performance
                "log_to_driver": False,  # Reduce logging overhead
            }
            
            # Override with config if available
            if ray_config:
                if hasattr(ray_config, 'cluster') and ray_config.cluster:
                    if ray_config.cluster.get('address') and ray_config.cluster['address'] != 'auto':
                        init_kwargs['address'] = ray_config.cluster['address']
                
                if hasattr(ray_config, 'resources') and ray_config.resources:
                    if ray_config.resources.get('object_store_memory_mb'):
                        init_kwargs['object_store_memory'] = ray_config.resources['object_store_memory_mb'] * 1024 * 1024
            
            ray.init(**init_kwargs)
            logger.info(f"Ray initialized for distributed pipeline execution (CPUs: {ray.cluster_resources().get('CPU', 'unknown')})")
            return True
        except Exception as e:
            logger.error(f"Failed to initialize Ray: {e}")
            return False
    
    return True


if RAY_AVAILABLE:
    @ray.remote(num_cpus=2, memory=2048 * 1024 * 1024)  # 2 CPUs, 2GB memory
    def remote_chunk_page(
        page_data: Dict[str, Any],
        document_id: str,
        normalized_version: str,
        chunking_strategy: str,
        chunk_size: int,
        overlap: int,
        namespace: str,
        base_dir: str,
        chunking_params: Optional[Dict[str, Any]] = None,
        normalized_doc_snapshot: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Remote function to chunk a single page with full strategy support.
        
        Args:
            page_data: Page data dictionary
            document_id: Document identifier
            normalized_version: Normalized document version
            chunking_strategy: Chunking strategy name
            chunk_size: Chunk size in characters
            overlap: Overlap between chunks
            namespace: Namespace name
            base_dir: Base directory for storage
            chunking_params: Additional chunking parameters (for advanced strategies)
            normalized_doc_snapshot: Snapshot of normalized document (for strategies that need it)
        """
        try:
            from ..engine.chunk_storage import ChunkStorage
            
            chunk_storage = ChunkStorage(namespace=namespace, base_dir=base_dir)
            page_no = page_data.get('page_no')
            page_text = page_data.get('flat_text', '')
            page = page_data  # Full page data for strategies that need it
            
            context = {
                'document_id': document_id,
                'page_no': page_no,
                'strategy': chunking_strategy,
                'namespace': namespace
            }
            
            logger.debug(f"[remote_chunk_page] Starting chunking for page {page_no} (text_length={len(page_text) if page_text else 0})")
            
            if not page_text:
                logger.warning(f"[remote_chunk_page] Page {page_no} has no text, returning empty chunks")
                return []
            
            chunking_params = chunking_params or {}
            
            chunk_start_time = time.time()
            try:
                # Chunk the page text - support all strategies
                if chunking_strategy == "semantic":
                    page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
                elif chunking_strategy == "paragraph":
                    page_chunks = chunk_storage._chunk_paragraph(page_text, overlap)
                elif chunking_strategy == "recursive":
                    page_chunks = chunk_storage._chunk_recursive(page_text, chunk_size, overlap)
                elif chunking_strategy == "sliding_window":
                    step_size = chunking_params.get('step_size', chunk_size - overlap)
                    page_chunks = chunk_storage._chunk_sliding_window(page_text, chunk_size, overlap, step_size=step_size)
                elif chunking_strategy == "section_based":
                    section_markers = chunking_params.get('section_markers', ["##", "###", "Chapter", "Section", "Part", "#"])
                    preserve_hierarchy = chunking_params.get('preserve_hierarchy', True)
                    page_chunks = chunk_storage._chunk_section_based(page, chunk_size, overlap,
                                                                    section_markers=section_markers,
                                                                    preserve_hierarchy=preserve_hierarchy)
                elif chunking_strategy == "table_aware":
                    # Need normalized doc for table positions
                    if normalized_doc_snapshot:
                        extract_tables = chunking_params.get('extract_tables', True)
                        preserve_table_context = chunking_params.get('preserve_table_context', True)
                        table_chunk_size = chunking_params.get('table_chunk_size', 300)
                        context_before = chunking_params.get('context_before', 200)
                        context_after = chunking_params.get('context_after', 200)
                        page_chunks = chunk_storage._chunk_table_aware(page, normalized_doc_snapshot, chunk_size, overlap,
                                                                      extract_tables=extract_tables,
                                                                      preserve_table_context=preserve_table_context,
                                                                      table_chunk_size=table_chunk_size,
                                                                      context_before=context_before,
                                                                      context_after=context_after)
                    else:
                        # Fallback to semantic if no normalized doc
                        page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
                elif chunking_strategy == "smart":
                    # Need normalized doc for smart detection
                    if normalized_doc_snapshot:
                        document_type_detection = chunking_params.get('document_type_detection', True)
                        fallback_strategy = chunking_params.get('fallback_strategy', 'fixed')
                        page_chunks = chunk_storage._chunk_smart(page, normalized_doc_snapshot, chunk_size, overlap,
                                                                document_type_detection=document_type_detection,
                                                                fallback_strategy=fallback_strategy)
                    else:
                        # Fallback to semantic if no normalized doc
                        page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
                elif chunking_strategy == "token_based":
                    max_tokens = chunking_params.get('max_tokens', 512)
                    overlap_tokens = chunking_params.get('overlap_tokens', 50)
                    tokenizer = chunking_params.get('tokenizer', 'tiktoken')
                    model = chunking_params.get('model', 'gpt-3.5-turbo')
                    page_chunks = chunk_storage._chunk_token_based(page_text, max_tokens, overlap_tokens, tokenizer, model)
                elif chunking_strategy == "semantic_similarity":
                    similarity_threshold = chunking_params.get('similarity_threshold', 0.7)
                    max_chunk_size = chunking_params.get('max_chunk_size', chunk_size)
                    min_chunk_size = chunking_params.get('min_chunk_size', 200)
                    embedding_model = chunking_params.get('embedding_model', 'sentence-transformers')
                    page_chunks = chunk_storage._chunk_semantic_similarity(page_text, similarity_threshold, max_chunk_size, min_chunk_size, embedding_model)
                elif chunking_strategy == "topic_aware":
                    num_topics = chunking_params.get('num_topics', 10)
                    min_chunk_size = chunking_params.get('min_chunk_size', 300)
                    topic_model = chunking_params.get('topic_model', 'simple')
                    page_chunks = chunk_storage._chunk_topic_aware(page_text, num_topics, min_chunk_size, topic_model)
                elif chunking_strategy == "qa_aware":
                    question_length = chunking_params.get('question_length', 50)
                    context_size = chunking_params.get('context_size', 500)
                    answer_markers = chunking_params.get('answer_markers', None)
                    page_chunks = chunk_storage._chunk_qa_aware(page_text, question_length, context_size, answer_markers)
                elif chunking_strategy == "code_aware":
                    preserve_code_blocks = chunking_params.get('preserve_code_blocks', True)
                    code_context = chunking_params.get('code_context', 200)
                    min_code_block_size = chunking_params.get('min_code_block_size', 50)
                    page_chunks = chunk_storage._chunk_code_aware(page, chunk_size, overlap, preserve_code_blocks, code_context, min_code_block_size)
                elif chunking_strategy == "image_aware":
                    if normalized_doc_snapshot:
                        include_captions = chunking_params.get('include_captions', True)
                        caption_context = chunking_params.get('caption_context', 300)
                        image_chunk_size = chunking_params.get('image_chunk_size', 200)
                        page_chunks = chunk_storage._chunk_image_aware(page, normalized_doc_snapshot, chunk_size, overlap, include_captions, caption_context, image_chunk_size)
                    else:
                        page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
                elif chunking_strategy == "adaptive":
                    base_chunk_size = chunking_params.get('base_chunk_size', chunk_size)
                    complexity_threshold = chunking_params.get('complexity_threshold', 0.5)
                    density_factor = chunking_params.get('density_factor', 1.2)
                    page_chunks = chunk_storage._chunk_adaptive(page_text, base_chunk_size, overlap, complexity_threshold, density_factor)
                elif chunking_strategy == "entity_aware":
                    entity_types = chunking_params.get('entity_types', None)
                    page_chunks = chunk_storage._chunk_entity_aware(page_text, chunk_size, overlap, entity_types)
                elif chunking_strategy == "multilang":
                    per_language_chunking = chunking_params.get('per_language_chunking', True)
                    page_chunks = chunk_storage._chunk_multilang(page_text, chunk_size, overlap, per_language_chunking)
                elif chunking_strategy == "citation_aware":
                    citation_patterns = chunking_params.get('citation_patterns', None)
                    citation_context = chunking_params.get('citation_context', 300)
                    page_chunks = chunk_storage._chunk_citation_aware(page_text, chunk_size, overlap, citation_patterns, citation_context)
                elif chunking_strategy == "dialogue_aware":
                    speaker_detection = chunking_params.get('speaker_detection', True)
                    preserve_turns = chunking_params.get('preserve_turns', True)
                    page_chunks = chunk_storage._chunk_dialogue_aware(page_text, chunk_size, overlap, speaker_detection, preserve_turns)
                elif chunking_strategy == "formula_aware":
                    formula_patterns = chunking_params.get('formula_patterns', None)
                    formula_context = chunking_params.get('formula_context', 200)
                    page_chunks = chunk_storage._chunk_formula_aware(page_text, chunk_size, overlap, formula_patterns, formula_context)
                elif chunking_strategy == "list_aware":
                    preserve_lists = chunking_params.get('preserve_lists', True)
                    list_context = chunking_params.get('list_context', 200)
                    page_chunks = chunk_storage._chunk_list_aware(page, chunk_size, overlap, preserve_lists, list_context)
                else:  # fixed (default)
                    page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
                
                chunk_time = time.time() - chunk_start_time
                logger.debug(f"[remote_chunk_page] Page {page_no}: Created {len(page_chunks)} chunks in {chunk_time:.3f}s using {chunking_strategy}")
            except Exception as chunk_err:
                chunk_time = time.time() - chunk_start_time
                logger.error(f"[remote_chunk_page] Error chunking page {page_no} with {chunking_strategy}: {chunk_err} (after {chunk_time:.3f}s)")
                import traceback
                logger.error(f"[remote_chunk_page] Traceback:\n{traceback.format_exc()}")
                return []
            
            # Create chunk properties
            chunks = []
            # Validate chunks don't exceed page boundaries
            page_text_length = len(page_text)
            validation_start = time.time()
            
            chunks_validated = 0
            chunks_skipped = 0
            for chunk_idx, (chunk_text, start, end) in enumerate(page_chunks):
                # Validate chunk boundaries are within page
                if start < 0:
                    logger.warning(f"[remote_chunk_page] Page {page_no}, chunk {chunk_idx}: negative start ({start}), clamping to 0")
                    start = 0
                
                if end > page_text_length:
                    logger.warning(f"[remote_chunk_page] Page {page_no}, chunk {chunk_idx}: exceeds boundary (end={end}, page_length={page_text_length}), clamping")
                    end = page_text_length
                    # Truncate chunk text to match
                    chunk_text = chunk_text[:end - start]
                
                if start >= end:
                    logger.warning(f"[remote_chunk_page] Page {page_no}, chunk {chunk_idx}: invalid boundaries (start={start}, end={end}), skipping")
                    chunks_skipped += 1
                    continue
                
                chunks_validated += 1
                
                from contextsynapse.extraction.id_generator import IDGenerator
                chunk_id = IDGenerator.generate_chunk_id(
                    document_id=document_id,
                    page_no=page_no,
                    chunk_index=chunk_idx,
                    chunking_strategy=chunking_strategy,
                    strategy_version="v1",
                    namespace=None
                )
                chunk_props = chunk_storage.create_chunk_with_pointer(
                    chunk_id=chunk_id,
                    content=chunk_text,
                    document_id=document_id,
                    normalized_version=normalized_version,
                    page_no=page_no,
                    start_char=start,
                    end_char=end,
                    chunk_index=chunk_idx,
                    chunking_strategy=chunking_strategy,
                    strategy_version="v1",
                    chunking_params={
                        "chunk_size": chunk_size,
                        "overlap": overlap,
                        **chunking_params
                    },
                    document_node_id=None,  # Will be set later
                    metadata={
                        "page_boundary_validated": True,  # Flag to indicate validation
                    }
                )
                chunks.append(chunk_props)
            
            validation_time = time.time() - validation_start
            total_time = time.time() - chunk_start_time
            logger.info(f"[remote_chunk_page] Page {page_no} complete: {chunks_validated} chunks created, {chunks_skipped} skipped "
                      f"(validation: {validation_time:.3f}s, total: {total_time:.3f}s)")
            
            return chunks
        except Exception as e:
            logger.error(f"[remote_chunk_page] Fatal error for page {page_no}: {e}")
            import traceback
            logger.error(f"[remote_chunk_page] Full traceback:\n{traceback.format_exc()}")
            return []

    @ray.remote(num_cpus=1, num_gpus=0.5, memory=4096 * 1024 * 1024)  # 1 CPU, 0.5 GPU, 4GB memory
    def remote_embed_chunk(
        chunk_id: str,
        content: str,
        model: str,
        dimensions: int,
        embedding_field: str
    ) -> Dict[str, Any]:
        """Remote function to generate embedding for a single chunk."""
        try:
            from ...llm.openai_embedding_service import OpenAIEmbeddingService
            
            embedding_service = OpenAIEmbeddingService()
            embedding = embedding_service.generate_embedding(content)
            
            return {
                "chunk_id": chunk_id,
                "embedding": embedding,
                "embedding_field": embedding_field
            }
        except Exception as e:
            logger.error(f"Error in remote_embed_chunk for {chunk_id}: {e}")
            return {
                "chunk_id": chunk_id,
                "embedding": None,
                "error": str(e)
            }

    @ray.remote(num_cpus=2, memory=2048 * 1024 * 1024)  # 2 CPUs, 2GB memory
    def remote_extract_entities_from_chunk(
        chunk_id: str,
        content: str,
        llm_model: str,
        prompt: str
    ) -> Dict[str, Any]:
        """Remote function to extract entities from a single chunk."""
        try:
            from ...llm.universal_llm import AIContextDBUniversalLLM
            
            llm_manager = AIContextDBUniversalLLM()
            
            # Prepare prompt
            full_prompt = f"{prompt}\n\nText:\n{content[:2000]}"  # Limit content size
            
            # Call LLM
            result = llm_manager.generate(
                model_name=llm_model,
                prompt=full_prompt,
                temperature=0.3,
                max_tokens=2000
            )
            
            if not result.get('success'):
                return {
                    "chunk_id": chunk_id,
                    "entities": [],
                    "error": result.get('error', 'Unknown error')
                }
            
            response_text = result.get('response', '')
            
            # Parse entities from response
            try:
                entities_data = json.loads(response_text)
                if not isinstance(entities_data, list):
                    entities_data = [entities_data]
            except:
                # Fallback: try to extract entities from text response
                import re
                entities_data = []
                patterns = [
                    r'(\w+):\s*([^(]+)\s*\(([^)]+)\)',
                    r'Name:\s*([^,]+),\s*Type:\s*([^,]+)',
                ]
                for pattern in patterns:
                    matches = re.findall(pattern, response_text)
                    for match in matches:
                        if len(match) >= 2:
                            entities_data.append({
                                'name': match[1] if len(match) > 1 else match[0],
                                'type': match[2] if len(match) > 2 else 'Entity'
                            })
            
            return {
                "chunk_id": chunk_id,
                "entities": entities_data,
                "error": None
            }
        except Exception as e:
            logger.error(f"Error in remote_extract_entities_from_chunk for {chunk_id}: {e}")
            return {
                "chunk_id": chunk_id,
                "entities": [],
                "error": str(e)
            }


def chunk_pages_parallel(
    pages: List[Dict[str, Any]],
    document_id: str,
    normalized_version: str,
    chunking_strategy: str,
    chunk_size: int,
    overlap: int,
    namespace: str,
    base_dir: str,
    use_ray: bool = True,
    chunking_params: Optional[Dict[str, Any]] = None,
    normalized_doc: Optional[Dict[str, Any]] = None,
    max_concurrent: Optional[int] = None
) -> List[Dict[str, Any]]:
    """
    Chunk multiple pages in parallel using Ray with optimized batching.
    
    Args:
        pages: List of page data dictionaries
        document_id: Document identifier
        normalized_version: Normalized document version
        chunking_strategy: Chunking strategy (all strategies supported)
        chunk_size: Chunk size in characters
        overlap: Overlap between chunks
        namespace: Namespace name
        base_dir: Base directory for storage
        use_ray: Whether to use Ray for parallel processing
        chunking_params: Additional chunking parameters for advanced strategies
        normalized_doc: Full normalized document (for strategies that need it)
        max_concurrent: Maximum concurrent Ray tasks (None = auto-detect)
        
    Returns:
        List of chunk property dictionaries
    """
    if not use_ray or not RAY_AVAILABLE or not _init_ray_if_needed():
        # Fallback to sequential processing
        from ..engine.chunk_storage import ChunkStorage
        chunk_storage = ChunkStorage(namespace=namespace, base_dir=base_dir)
        
        all_chunks = []
        for page in pages:
            page_no = page.get('page_no')
            page_text = page.get('flat_text', '')
            
            if not page_text:
                continue
            
            chunking_params = chunking_params or {}
            
            # Support all strategies in fallback mode
            if chunking_strategy == "semantic":
                page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
            elif chunking_strategy == "paragraph":
                page_chunks = chunk_storage._chunk_paragraph(page_text, overlap)
            elif chunking_strategy == "recursive":
                page_chunks = chunk_storage._chunk_recursive(page_text, chunk_size, overlap)
            elif chunking_strategy == "sliding_window":
                step_size = chunking_params.get('step_size', chunk_size - overlap)
                page_chunks = chunk_storage._chunk_sliding_window(page_text, chunk_size, overlap, step_size=step_size)
            elif chunking_strategy == "section_based":
                section_markers = chunking_params.get('section_markers', ["##", "###", "Chapter", "Section", "Part", "#"])
                preserve_hierarchy = chunking_params.get('preserve_hierarchy', True)
                page_chunks = chunk_storage._chunk_section_based(page, chunk_size, overlap,
                                                                section_markers=section_markers,
                                                                preserve_hierarchy=preserve_hierarchy)
            elif chunking_strategy == "table_aware" and normalized_doc:
                extract_tables = chunking_params.get('extract_tables', True)
                preserve_table_context = chunking_params.get('preserve_table_context', True)
                table_chunk_size = chunking_params.get('table_chunk_size', 300)
                context_before = chunking_params.get('context_before', 200)
                context_after = chunking_params.get('context_after', 200)
                page_chunks = chunk_storage._chunk_table_aware(page, normalized_doc, chunk_size, overlap,
                                                              extract_tables=extract_tables,
                                                              preserve_table_context=preserve_table_context,
                                                              table_chunk_size=table_chunk_size,
                                                              context_before=context_before,
                                                              context_after=context_after)
            elif chunking_strategy == "smart" and normalized_doc:
                document_type_detection = chunking_params.get('document_type_detection', True)
                fallback_strategy = chunking_params.get('fallback_strategy', 'fixed')
                page_chunks = chunk_storage._chunk_smart(page, normalized_doc, chunk_size, overlap,
                                                        document_type_detection=document_type_detection,
                                                        fallback_strategy=fallback_strategy)
            elif chunking_strategy == "token_based":
                max_tokens = chunking_params.get('max_tokens', 512)
                overlap_tokens = chunking_params.get('overlap_tokens', 50)
                tokenizer = chunking_params.get('tokenizer', 'tiktoken')
                model = chunking_params.get('model', 'gpt-3.5-turbo')
                page_chunks = chunk_storage._chunk_token_based(page_text, max_tokens, overlap_tokens, tokenizer, model)
            elif chunking_strategy == "semantic_similarity":
                similarity_threshold = chunking_params.get('similarity_threshold', 0.7)
                max_chunk_size = chunking_params.get('max_chunk_size', chunk_size)
                min_chunk_size = chunking_params.get('min_chunk_size', 200)
                embedding_model = chunking_params.get('embedding_model', 'sentence-transformers')
                page_chunks = chunk_storage._chunk_semantic_similarity(page_text, similarity_threshold, max_chunk_size, min_chunk_size, embedding_model)
            elif chunking_strategy == "topic_aware":
                num_topics = chunking_params.get('num_topics', 10)
                min_chunk_size = chunking_params.get('min_chunk_size', 300)
                topic_model = chunking_params.get('topic_model', 'simple')
                page_chunks = chunk_storage._chunk_topic_aware(page_text, num_topics, min_chunk_size, topic_model)
            elif chunking_strategy == "qa_aware":
                question_length = chunking_params.get('question_length', 50)
                context_size = chunking_params.get('context_size', 500)
                answer_markers = chunking_params.get('answer_markers', None)
                page_chunks = chunk_storage._chunk_qa_aware(page_text, question_length, context_size, answer_markers)
            elif chunking_strategy == "code_aware" and normalized_doc:
                preserve_code_blocks = chunking_params.get('preserve_code_blocks', True)
                code_context = chunking_params.get('code_context', 200)
                min_code_block_size = chunking_params.get('min_code_block_size', 50)
                page_chunks = chunk_storage._chunk_code_aware(page, chunk_size, overlap, preserve_code_blocks, code_context, min_code_block_size)
            elif chunking_strategy == "image_aware" and normalized_doc:
                include_captions = chunking_params.get('include_captions', True)
                caption_context = chunking_params.get('caption_context', 300)
                image_chunk_size = chunking_params.get('image_chunk_size', 200)
                page_chunks = chunk_storage._chunk_image_aware(page, normalized_doc, chunk_size, overlap, include_captions, caption_context, image_chunk_size)
            elif chunking_strategy == "adaptive":
                base_chunk_size = chunking_params.get('base_chunk_size', chunk_size)
                complexity_threshold = chunking_params.get('complexity_threshold', 0.5)
                density_factor = chunking_params.get('density_factor', 1.2)
                page_chunks = chunk_storage._chunk_adaptive(page_text, base_chunk_size, overlap, complexity_threshold, density_factor)
            elif chunking_strategy == "entity_aware":
                entity_types = chunking_params.get('entity_types', None)
                page_chunks = chunk_storage._chunk_entity_aware(page_text, chunk_size, overlap, entity_types)
            elif chunking_strategy == "multilang":
                per_language_chunking = chunking_params.get('per_language_chunking', True)
                page_chunks = chunk_storage._chunk_multilang(page_text, chunk_size, overlap, per_language_chunking)
            elif chunking_strategy == "citation_aware":
                citation_patterns = chunking_params.get('citation_patterns', None)
                citation_context = chunking_params.get('citation_context', 300)
                page_chunks = chunk_storage._chunk_citation_aware(page_text, chunk_size, overlap, citation_patterns, citation_context)
            elif chunking_strategy == "dialogue_aware":
                speaker_detection = chunking_params.get('speaker_detection', True)
                preserve_turns = chunking_params.get('preserve_turns', True)
                page_chunks = chunk_storage._chunk_dialogue_aware(page_text, chunk_size, overlap, speaker_detection, preserve_turns)
            elif chunking_strategy == "formula_aware":
                formula_patterns = chunking_params.get('formula_patterns', None)
                formula_context = chunking_params.get('formula_context', 200)
                page_chunks = chunk_storage._chunk_formula_aware(page_text, chunk_size, overlap, formula_patterns, formula_context)
            elif chunking_strategy == "list_aware" and normalized_doc:
                preserve_lists = chunking_params.get('preserve_lists', True)
                list_context = chunking_params.get('list_context', 200)
                page_chunks = chunk_storage._chunk_list_aware(page, chunk_size, overlap, preserve_lists, list_context)
            else:
                page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
            
            for chunk_idx, (chunk_text, start, end) in enumerate(page_chunks):
                from contextsynapse.extraction.id_generator import IDGenerator
                chunk_id = IDGenerator.generate_chunk_id(
                    document_id=document_id,
                    page_no=page_no,
                    chunk_index=chunk_idx,
                    chunking_strategy=chunking_strategy,
                    strategy_version="v1",
                    namespace=None
                )
                chunk_props = chunk_storage.create_chunk_with_pointer(
                    chunk_id=chunk_id,
                    content=chunk_text,
                    document_id=document_id,
                    normalized_version=normalized_version,
                    page_no=page_no,
                    start_char=start,
                    end_char=end,
                    chunk_index=chunk_idx,
                    chunking_strategy=chunking_strategy,
                    strategy_version="v1",
                    chunking_params={"chunk_size": chunk_size, "overlap": overlap, **(chunking_params or {})},
                    document_node_id=None
                )
                all_chunks.append(chunk_props)
        
        return all_chunks
    
    # Use Ray for parallel processing with optimized batching
    parallel_start = time.time()
    logger.info(f"[chunk_pages_parallel] Starting parallel chunking: {len(pages)} pages, strategy={chunking_strategy}, "
              f"chunk_size={chunk_size}, overlap={overlap}, namespace={namespace}")
    
    # Validate pages are unique to prevent duplicate processing
    page_numbers = [p.get('page_no') for p in pages if p.get('page_no') is not None]
    unique_pages = len(set(page_numbers))
    if unique_pages < len(page_numbers):
        logger.warning(f"[chunk_pages_parallel] Duplicate page numbers detected! {len(page_numbers)} pages but only {unique_pages} unique. Deduplicating...")
        # Deduplicate pages by page_no (keep first occurrence)
        seen_pages = set()
        deduplicated_pages = []
        for page in pages:
            page_no = page.get('page_no')
            if page_no is not None and page_no not in seen_pages:
                seen_pages.add(page_no)
                deduplicated_pages.append(page)
            elif page_no is None:
                # Keep pages without page_no (shouldn't happen, but be safe)
                deduplicated_pages.append(page)
        pages = deduplicated_pages
        logger.info(f"[chunk_pages_parallel] Deduplicated to {len(pages)} unique pages")
    
    # Determine if we need normalized doc snapshot
    needs_normalized_doc = chunking_strategy in ["table_aware", "smart", "image_aware", "code_aware", "list_aware"]
    normalized_doc_snapshot = None
    if needs_normalized_doc and normalized_doc:
        # Create a lightweight snapshot (only what's needed)
        # OPTIMIZATION: Only include tables/images metadata, not full page data
        content = normalized_doc.get("content", {})
        normalized_doc_snapshot = {
            "document_id": normalized_doc.get("document_id"),
            "content": {
                "tables": content.get("tables", []),
                "images": content.get("images", []) if chunking_strategy == "image_aware" else []
            }
        }
        logger.debug(f"[chunk_pages_parallel] Created optimized normalized_doc snapshot: "
                    f"{len(normalized_doc_snapshot['content'].get('tables', []))} tables, "
                    f"{len(normalized_doc_snapshot['content'].get('images', []))} images")
    
    # Optimize batch size based on strategy and available resources
    if max_concurrent is None:
        # Auto-detect based on available CPUs
        try:
            available_cpus = ray.cluster_resources().get("CPU", 4)
            # Use 80% of available CPUs, but cap at reasonable limit
            max_concurrent = min(int(available_cpus * 0.8), len(pages), 20)
        except:
            max_concurrent = min(len(pages), 10)  # Conservative default
    
    # OPTIMIZED: Process in batches with streaming results to prevent memory accumulation
    # Use smaller batches and process results as they arrive
    all_chunks = []
    chunk_id_set = set()  # Track chunk IDs to detect duplicates
    duplicate_count = 0
    batch_size = max_concurrent
    
    import time
    import gc
    parallel_start_time = time.time()
    total_batches = (len(pages) + batch_size - 1) // batch_size
    
    print(f"[CHUNK] Ray parallel processing: {len(pages)} pages, {max_concurrent} concurrent workers, {total_batches} batches", flush=True)
    print(f"[CHUNK] OPTIMIZED: Processing results incrementally to prevent memory buildup", flush=True)
    
    for batch_start in range(0, len(pages), batch_size):
        batch_end = min(batch_start + batch_size, len(pages))
        batch_pages = pages[batch_start:batch_end]
        batch_num = batch_start // batch_size + 1
        
        batch_start_time = time.time()
        logger.info(f"[chunk_pages_parallel] Batch {batch_num}/{total_batches}: Processing pages {batch_start+1}-{batch_end} of {len(pages)}")
        print(f"[CHUNK] Ray batch {batch_num}/{total_batches}: Processing pages {batch_start+1}-{batch_end} of {len(pages)}", flush=True)
        
        # Create futures for this batch
        futures = [
            remote_chunk_page.remote(
                page, document_id, normalized_version, chunking_strategy,
                chunk_size, overlap, namespace, base_dir,
                chunking_params=chunking_params,
                normalized_doc_snapshot=normalized_doc_snapshot
            )
            for page in batch_pages
        ]
        
        # OPTIMIZED: Process results as they complete using ray.wait() for streaming
        # This allows processing chunks incrementally instead of waiting for entire batch
        batch_results = []
        remaining_futures = futures.copy()
        
        while remaining_futures:
            # Wait for at least one future to complete (timeout=1s to avoid blocking too long)
            ready, remaining_futures = ray.wait(remaining_futures, num_returns=1, timeout=1.0)
            
            for future in ready:
                try:
                    page_chunks = ray.get(future)
                    batch_results.append(page_chunks)
                except Exception as page_err:
                    logger.warning(f"[chunk_pages_parallel] Page chunking failed: {page_err}")
                    batch_results.append([])  # Continue with empty result
        
        batch_time = time.time() - batch_start_time
        batch_chunks = sum(len(page_chunks) for page_chunks in batch_results)
        logger.info(f"[chunk_pages_parallel] Batch {batch_num} completed: {batch_chunks} chunks in {batch_time:.2f}s "
                   f"({batch_chunks/batch_time:.1f} chunks/sec)")
        
        # OPTIMIZED: Process chunks from batch immediately and clear from memory
        # This prevents accumulating all chunks in memory
        batch_chunks_processed = 0
        for page_chunks in batch_results:
            for chunk in page_chunks:
                chunk_id = chunk.get('chunk_id') or chunk.get('id')
                if not chunk_id:
                    # Try to extract from source if available
                    source = chunk.get('source', {})
                    if isinstance(source, dict):
                        chunk_id = source.get('chunk_id')
                    if not chunk_id:
                        chunk_id = f"chunk_{len(all_chunks) + batch_chunks_processed}"
                
                if chunk_id in chunk_id_set:
                    duplicate_count += 1
                    logger.warning(f"[chunk_pages_parallel] Duplicate chunk ID detected: {chunk_id}. Skipping duplicate.")
                    continue
                
                chunk_id_set.add(chunk_id)
                all_chunks.append(chunk)
                batch_chunks_processed += 1
        
        # Clear batch results from memory after processing
        del batch_results
        del futures
        gc.collect()  # Force garbage collection after each batch
        
        elapsed = time.time() - parallel_start_time
        remaining = len(pages) - batch_end
        estimated_remaining = (remaining / len(batch_pages)) * batch_time if len(batch_pages) > 0 else 0
        progress_pct = (batch_end / len(pages)) * 100
        
        print(f"[CHUNK] Ray batch {batch_num} complete: {batch_chunks} chunks in {batch_time:.2f}s | Total: {len(all_chunks)} chunks | Progress: {progress_pct:.1f}% | Est. remaining: {estimated_remaining:.1f}s", flush=True)
        
        logger.debug(f"[chunk_pages_parallel] Batch {batch_num} complete: {len(all_chunks)} unique chunks so far "
                    f"(skipped {duplicate_count} duplicates, progress: {progress_pct:.1f}%)")
    
    total_time = time.time() - parallel_start_time
    if duplicate_count > 0:
        logger.warning(f"[chunk_pages_parallel] Detected and skipped {duplicate_count} duplicate chunks during parallel processing")
    
    chunks_per_sec = len(all_chunks) / total_time if total_time > 0 else 0
    print(f"[CHUNK] Ray parallel processing complete: {len(all_chunks)} unique chunks in {total_time:.2f}s ({chunks_per_sec:.1f} chunks/sec)", flush=True)
    logger.info(f"[chunk_pages_parallel] Parallel processing complete: {len(all_chunks)} unique chunks in {total_time:.2f}s "
               f"({chunks_per_sec:.1f} chunks/sec, {len(pages)/total_time:.1f} pages/sec)")
    
    # Final cleanup
    gc.collect()
    return all_chunks


def embed_chunks_parallel(
    chunks: List[Dict[str, Any]],
    model: str = "text-embedding-3-large",
    dimensions: int = 3072,
    embedding_field: str = "embedding",
    use_ray: bool = True
) -> Dict[str, List[float]]:
    """
    Generate embeddings for multiple chunks in parallel using Ray.
    
    Args:
        chunks: List of chunk dictionaries with 'id' and 'content'
        model: Embedding model name
        dimensions: Embedding dimensions
        embedding_field: Field name to store embedding
        use_ray: Whether to use Ray for parallel processing
        
    Returns:
        Dictionary mapping chunk_id to embedding vector
    """
    if not chunks:
        return {}
    
    if not use_ray or not RAY_AVAILABLE or not _init_ray_if_needed():
        # Fallback to sequential processing
        try:
            from ...llm.openai_embedding_service import OpenAIEmbeddingService
            embedding_service = OpenAIEmbeddingService()
        except ImportError:
            logger.warning("OpenAI embedding service not available")
            return {}

        embeddings = {}
        for chunk in chunks:
            chunk_id = chunk.get("id", "")
            content = chunk.get("content", "")
            if content:
                try:
                    embedding = embedding_service.generate_embedding(content)
                    embeddings[chunk_id] = embedding
                except Exception as e:
                    logger.warning(f"Failed to embed chunk {chunk_id}: {e}")

        return embeddings
    
    # Use Ray for parallel processing
    logger.info(f"Generating embeddings for {len(chunks)} chunks in parallel using Ray")
    futures = [
        remote_embed_chunk.remote(
            chunk.get("id", ""),
            chunk.get("content", ""),
            model,
            dimensions,
            embedding_field
        )
        for chunk in chunks
    ]
    
    results = ray.get(futures)
    embeddings = {}
    for result in results:
        if result.get("embedding"):
            embeddings[result["chunk_id"]] = result["embedding"]
    
    logger.info(f"Generated {len(embeddings)} embeddings in parallel")
    return embeddings


def extract_entities_parallel(
    chunks: List[Dict[str, Any]],
    llm_model: str = "gpt-4",
    prompt: str = "Extract all named entities from the text.",
    use_ray: bool = True
) -> Dict[str, List[Dict[str, Any]]]:
    """
    Extract entities from multiple chunks in parallel using Ray.
    
    Args:
        chunks: List of chunk dictionaries with 'id' and 'content'
        llm_model: LLM model name
        prompt: Prompt for entity extraction
        use_ray: Whether to use Ray for parallel processing
        
    Returns:
        Dictionary mapping chunk_id to list of entities
    """
    if not chunks:
        return {}
    
    if not use_ray or not RAY_AVAILABLE or not _init_ray_if_needed():
        # Fallback to sequential processing
        try:
            from ...llm.universal_llm import AIContextDBUniversalLLM
            llm_manager = AIContextDBUniversalLLM()
        except ImportError:
            logger.warning("LLM module not available for entity extraction")
            return {}
        
        all_entities = {}
        for chunk in chunks:
            chunk_id = chunk.get("id", "")
            content = chunk.get("content", "")
            if not content:
                continue
            
            try:
                full_prompt = f"{prompt}\n\nText:\n{content[:2000]}"
                result = llm_manager.generate(
                    model_name=llm_model,
                    prompt=full_prompt,
                    temperature=0.3,
                    max_tokens=2000
                )
                
                if result.get('success'):
                    response_text = result.get('response', '')
                    try:
                        entities_data = json.loads(response_text)
                        if not isinstance(entities_data, list):
                            entities_data = [entities_data]
                    except:
                        entities_data = _parse_entities_from_text(response_text)
                    all_entities[chunk_id] = entities_data
            except Exception as e:
                logger.warning(f"Failed to extract entities from chunk {chunk_id}: {e}")
        
        return all_entities
    
    # Use Ray for parallel processing
    logger.info(f"Extracting entities from {len(chunks)} chunks in parallel using Ray")
    futures = [
        remote_extract_entities_from_chunk.remote(
            chunk.get("id", ""),
            chunk.get("content", ""),
            llm_model,
            prompt
        )
        for chunk in chunks
    ]
    
    results = ray.get(futures)
    all_entities = {}
    for result in results:
        if result.get("entities"):
            all_entities[result["chunk_id"]] = result["entities"]
    
    logger.info(f"Extracted entities from {len(all_entities)} chunks in parallel")
    return all_entities


def _parse_entities_from_text_fallback(text: str) -> List[Dict[str, Any]]:
    """Parse entities from text response (fallback method)."""
    entities = []
    import re
    patterns = [
        r'(\w+):\s*([^(]+)\s*\(([^)]+)\)',
        r'Name:\s*([^,]+),\s*Type:\s*([^,]+)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, text)
        for match in matches:
            if len(match) >= 2:
                entities.append({
                    'name': match[1] if len(match) > 1 else match[0],
                    'type': match[2] if len(match) > 2 else 'Entity'
                })
    return entities


def _parse_entities_from_text(text: str) -> List[Dict[str, Any]]:
    """Parse entities from text response (fallback method)."""
    return _parse_entities_from_text_fallback(text)

