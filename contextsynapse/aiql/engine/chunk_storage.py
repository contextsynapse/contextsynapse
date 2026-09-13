"""
Chunk Storage with Pointers to Normalized JSON

Smart chunk storage that references normalized JSON instead of duplicating data.
"""

import logging
from typing import Dict, List, Optional, Any
from pathlib import Path
import json
import time
import traceback

logger = logging.getLogger(__name__)


def _log_chunking_operation(level: str, message: str, context: Optional[Dict[str, Any]] = None, exc_info: bool = False):
    """
    Structured logging helper for chunking operations.
    
    Args:
        level: Log level ('debug', 'info', 'warning', 'error')
        message: Log message
        context: Additional context dictionary (document_id, page_no, strategy, etc.)
        exc_info: Whether to include exception info
    """
    context_str = ""
    if context:
        context_parts = [f"{k}={v}" for k, v in context.items() if v is not None]
        if context_parts:
            context_str = f" | {' | '.join(context_parts)}"
    
    log_message = f"[CHUNKING]{context_str} | {message}"
    
    if level == 'debug':
        logger.debug(log_message, exc_info=exc_info)
    elif level == 'info':
        logger.info(log_message, exc_info=exc_info)
    elif level == 'warning':
        logger.warning(log_message, exc_info=exc_info)
    elif level == 'error':
        logger.error(log_message, exc_info=exc_info)
    else:
        logger.info(log_message, exc_info=exc_info)


class ChunkStorage:
    """
    Manages chunk storage with pointers to normalized JSON.
    
    Strategy:
    - Chunk content stored in graph nodes (for fast retrieval)
    - Chunk metadata includes pointer to normalized JSON
    - Large structures (tables, images) referenced, not duplicated
    """
    
    # Class-level cache for chunking config (avoids re-reading YAML on every call)
    _chunking_config_cache: Optional[Dict[str, Any]] = None
    _chunking_config_mtime: float = 0.0

    def __init__(self, namespace: str, base_dir: str = "contextcore_data"):
        """
        Initialize chunk storage.

        Args:
            namespace: Namespace name
            base_dir: Base directory for storage
        """
        self.namespace = namespace
        self.base_dir = Path(base_dir)
        self.documents_dir = self.base_dir / "namespaces" / namespace / "documents"
        # Cache for normalized documents to avoid repeated loads
        self._normalized_doc_cache = {}

    @classmethod
    def load_chunking_config(cls) -> Dict[str, Any]:
        """
        Load chunking strategies config from YAML.
        Returns dict of strategy_name -> {parameters: {...}, ...}.
        Caches the result and re-reads only if the file changed.
        """
        config_path = Path(__file__).resolve().parent.parent.parent.parent / "config" / "strategies" / "chunking.yaml"
        try:
            if not config_path.exists():
                return {}
            mtime = config_path.stat().st_mtime
            if cls._chunking_config_cache is not None and mtime == cls._chunking_config_mtime:
                return cls._chunking_config_cache
            import yaml
            with open(config_path, "r", encoding="utf-8") as f:
                data = yaml.safe_load(f) or {}
            cls._chunking_config_cache = data.get("strategies", {})
            cls._chunking_config_mtime = mtime
            return cls._chunking_config_cache
        except Exception as e:
            logger.warning(f"Failed to load chunking config: {e}")
            return {}

    @classmethod
    def get_strategy_params(cls, strategy_name: str) -> Dict[str, Any]:
        """
        Get configured parameters for a chunking strategy.
        Returns the parameters dict from the YAML config, or empty dict if not found.
        """
        config = cls.load_chunking_config()
        strategy = config.get(strategy_name, {})
        return dict(strategy.get("parameters", {}))
    
    def create_chunk_with_pointer(
        self,
        chunk_id: str,
        content: str,
        document_id: str,
        normalized_version: str,
        page_no: Optional[int] = None,
        start_char: Optional[int] = None,
        end_char: Optional[int] = None,
        chunk_index: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
        chunking_strategy: Optional[str] = None,
        strategy_version: Optional[str] = None,
        chunking_params: Optional[Dict[str, Any]] = None,
        document_node_id: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Create chunk node with pointer to normalized JSON.
        
        Args:
            chunk_id: Chunk identifier
            content: Chunk text content (for fast retrieval)
            document_id: Source document ID
            normalized_version: Version of normalized JSON
            page_no: Page number in normalized JSON
            start_char: Start character position in page
            end_char: End character position in page
            chunk_index: Chunk index within document
            metadata: Additional chunk metadata
            
        Returns:
            Chunk node properties dictionary
        """
        # Build source pointer
        source_pointer = {
            "document_id": document_id,
            "normalized_version": normalized_version,
            "normalized_json_path": f"documents/{document_id}/normalized/normalized_{normalized_version}.json",
            "hdf5_path": f"{document_id}/{normalized_version}",  # For hybrid storage
        }
        
        # Add chunking strategy metadata (for multiple strategies support)
        if chunking_strategy:
            source_pointer["chunking_strategy"] = chunking_strategy
        if strategy_version:
            source_pointer["strategy_version"] = strategy_version
        if chunking_params:
            source_pointer["chunking_params"] = chunking_params
        
        # Link to Document node (if created in CONNECT stage)
        if document_node_id:
            source_pointer["document_node_id"] = document_node_id
        
        if page_no is not None:
            source_pointer["page_no"] = page_no
        
        if start_char is not None:
            source_pointer["start_char"] = start_char
        
        if end_char is not None:
            source_pointer["end_char"] = end_char
        
        # CRITICAL: Ensure content doesn't contain image bytes
        # Images are reference-only and should never be loaded into chunk content
        if content:
            # Check for base64 image data patterns (common indicators of image bytes in text)
            if "data:image" in content and "base64," in content:
                logger.warning(f"[CHUNK] Chunk {chunk_id[:50]}... contains base64 image data - removing image data (images are reference-only)")
                # Remove base64 image data from content
                import re
                content = re.sub(r'data:image/[^;]+;base64,[A-Za-z0-9+/=]+', '[IMAGE_DATA_REMOVED]', content)
            
            # Check for suspiciously large content that might contain binary data
            # Normal text chunks should be reasonable size (e.g., < 1MB)
            if len(content) > 1024 * 1024:  # 1MB threshold
                logger.warning(f"[CHUNK] Chunk {chunk_id[:50]}... has unusually large content ({len(content):,} bytes) - may contain binary data")
                # Log first 200 chars for inspection
                preview = content[:200].replace('\n', ' ').replace('\r', ' ')
                logger.warning(f"[CHUNK] Content preview: {preview}...")
        
        # Build chunk properties
        chunk_properties = {
            "chunk_id": chunk_id,
            "content": content,  # Store content for fast retrieval (images are reference-only)
            "modality": "text",
            "order": chunk_index,
            "metadata": metadata or {},
            "source": source_pointer,  # Pointer to normalized JSON
        }
        
        return chunk_properties
    
    def get_source_from_normalized_json(
        self,
        document_id: str,
        normalized_version: str,
        page_no: Optional[int] = None,
        start_char: Optional[int] = None,
        end_char: Optional[int] = None
    ) -> Optional[Dict[str, Any]]:
        """
        Get source content from normalized JSON using pointer.
        
        Args:
            document_id: Document identifier
            normalized_version: Version of normalized JSON
            page_no: Page number (optional)
            start_char: Start character position (optional)
            end_char: End character position (optional)
            
        Returns:
            Source content dictionary or None
        """
        try:
            from contextsynapse.extraction.normalized_store import NormalizedDocumentStore
            from pathlib import Path
            import json
            
            # First try NormalizedDocumentStore (namespace-aware paths)
            store = NormalizedDocumentStore(namespace=self.namespace, base_dir=str(self.base_dir))
            normalized_doc = store.get(document_id, normalized_version, load_full=True)
            
            # If not found, try legacy path format (where extract actually stores files)
            if not normalized_doc:
                legacy_path = Path(self.base_dir) / document_id / "normalized" / f"normalized_{normalized_version}.json"
                if legacy_path.exists():
                    try:
                        with open(legacy_path, 'r', encoding='utf-8') as f:
                            normalized_doc = json.load(f)
                        logger.debug(f"Loaded normalized document from legacy path: {legacy_path}")
                    except Exception as e:
                        logger.warning(f"Failed to load from legacy path {legacy_path}: {e}")
                else:
                    # Try to find any normalized file in legacy path
                    legacy_dir = Path(self.base_dir) / document_id / "normalized"
                    if legacy_dir.exists():
                        normalized_files = list(legacy_dir.glob("normalized_*.json"))
                        if normalized_files:
                            normalized_files.sort(reverse=True)
                            latest_file = normalized_files[0]
                            try:
                                with open(latest_file, 'r', encoding='utf-8') as f:
                                    normalized_doc = json.load(f)
                                logger.debug(f"Loaded normalized document from legacy path (latest): {latest_file}")
                            except Exception as e:
                                logger.warning(f"Failed to load from legacy path {latest_file}: {e}")
            
            if not normalized_doc:
                logger.warning(f"Normalized document not found: {document_id}/{normalized_version}")
                return None
            
            # If page_no specified, get specific page
            if page_no is not None:
                pages = normalized_doc.get('content', {}).get('pages', [])
                for page in pages:
                    if page.get('page_no') == page_no:
                        page_content = page.get('flat_text', '')
                        
                        # If character range specified, extract substring
                        if start_char is not None and end_char is not None:
                            page_content = page_content[start_char:end_char]
                        
                        return {
                            "page_no": page_no,
                            "content": page_content,
                            "normalized_blocks": page.get('normalized_blocks', []),
                            "structure": page.get('hierarchy', {})
                        }
            
            # Return full document if no page specified
            return {
                "document_id": document_id,
                "version": normalized_version,
                "content": normalized_doc.get('content', {}),
                "metadata": normalized_doc.get('metadata', {})
            }
            
        except Exception as e:
            logger.error(f"Failed to load source from normalized JSON: {e}")
            return None
    
    def create_chunks_from_normalized_json(
        self,
        document_id: str,
        normalized_version: str,
        chunking_strategy: str = "semantic",
        strategy_version: str = "v1",
        chunk_size: int = 1000,
        overlap: int = 200,
        document_node_id: Optional[str] = None,
        normalized_doc: Optional[Dict[str, Any]] = None,
        chunk_callback: Optional[callable] = None,
        **kwargs
    ) -> List[Dict[str, Any]]:
        """
        Create chunks from normalized JSON.
        
        Args:
            document_id: Document identifier
            normalized_version: Version of normalized JSON
            chunking_strategy: Chunking strategy ("semantic", "fixed", "paragraph", "hierarchical", "recursive", "parent_child")
            chunk_size: Chunk size in tokens/characters
            overlap: Overlap between chunks
            **kwargs: Additional chunking parameters
            
        Returns:
            List of chunk properties dictionaries
        """
        # Merge YAML config defaults (YAML < caller kwargs < explicit params)
        yaml_params = self.get_strategy_params(chunking_strategy)
        if yaml_params:
            # YAML defaults for chunk_size/overlap only apply if caller used the function defaults
            if 'chunk_size' in yaml_params and chunk_size == 1000:
                chunk_size = yaml_params.pop('chunk_size')
            else:
                yaml_params.pop('chunk_size', None)
            if 'overlap' in yaml_params and overlap == 200:
                overlap = yaml_params.pop('overlap')
            else:
                yaml_params.pop('overlap', None)
            # Remaining YAML params become kwargs defaults (caller kwargs take precedence)
            merged_kwargs = {**yaml_params, **kwargs}
            kwargs = merged_kwargs

        operation_start = time.time()
        context = {
            'document_id': document_id,
            'normalized_version': normalized_version,
            'strategy': chunking_strategy,
            'strategy_version': strategy_version,
            'chunk_size': chunk_size,
            'overlap': overlap,
            'namespace': self.namespace
        }
        
        _log_chunking_operation('info', f"Starting chunk creation", context)
        _log_chunking_operation('debug', f"Additional kwargs: {list(kwargs.keys())}", context)
        
        try:
            # If normalized_doc is provided, use it directly (already loaded)
            if normalized_doc is not None:
                _log_chunking_operation('debug', f"Using provided normalized document", context)
                # Cache it for future use
                cache_key = f"{document_id}_{normalized_version}"
                if len(self._normalized_doc_cache) > 10:
                    oldest_key = next(iter(self._normalized_doc_cache))
                    del self._normalized_doc_cache[oldest_key]
                self._normalized_doc_cache[cache_key] = normalized_doc
            else:
                # Check cache first
                cache_key = f"{document_id}_{normalized_version}"
                load_start = time.time()
                if cache_key in self._normalized_doc_cache:
                    normalized_doc = self._normalized_doc_cache[cache_key]
                    _log_chunking_operation('debug', f"Using cached normalized document", context)
                else:
                    _log_chunking_operation('debug', f"Loading normalized document from store", context)
                    from contextsynapse.extraction.normalized_store import NormalizedDocumentStore
                    
                    store = NormalizedDocumentStore(namespace=self.namespace, base_dir=str(self.base_dir))
                    normalized_doc = store.get(document_id, normalized_version, load_full=True)
                    
                    if normalized_doc:
                        # Cache the document (limit cache size to avoid memory issues)
                        if len(self._normalized_doc_cache) > 10:
                            # Remove oldest entry (simple FIFO)
                            oldest_key = next(iter(self._normalized_doc_cache))
                            del self._normalized_doc_cache[oldest_key]
                            _log_chunking_operation('debug', f"Cache full, removed oldest entry: {oldest_key}", context)
                        self._normalized_doc_cache[cache_key] = normalized_doc
                        load_time = time.time() - load_start
                        _log_chunking_operation('info', f"Loaded and cached normalized document in {load_time:.2f}s", context)
            
            if not normalized_doc:
                _log_chunking_operation('error', f"Normalized document not found", context)
                return []
            
            # Log document structure info with detailed breakdown
            pages = normalized_doc.get('content', {}).get('pages', [])
            total_pages = len(pages)
            pages_with_text = sum(1 for p in pages if p.get('flat_text', '').strip())
            total_text_length = sum(len(p.get('flat_text', '')) for p in pages)
            total_text_mb = total_text_length / (1024 * 1024)
            
            # Log first few pages details
            print(f"[CHUNK_STORAGE] Document structure analysis:", flush=True)
            print(f"[CHUNK_STORAGE]   Total pages: {total_pages}", flush=True)
            print(f"[CHUNK_STORAGE]   Pages with text: {pages_with_text}", flush=True)
            print(f"[CHUNK_STORAGE]   Total text: {total_text_length:,} chars ({total_text_mb:.2f} MB)", flush=True)
            
            # Log first 3 pages details
            for i, page in enumerate(pages[:3]):
                page_no = page.get('page_no', i + 1)
                page_text = page.get('flat_text', '')
                page_text_len = len(page_text) if isinstance(page_text, str) else 0
                print(f"[CHUNK_STORAGE]   Page {page_no}: {page_text_len:,} chars", flush=True)
                if page_text_len > 0:
                    preview = page_text[:150].replace('\n', ' ').strip()
                    print(f"[CHUNK_STORAGE]     Preview: {preview}...", flush=True)
            
            _log_chunking_operation('info', 
                f"Document structure: {total_pages} pages, {pages_with_text} with text, {total_text_length:,} total chars ({total_text_mb:.2f} MB)",
                context)
            
            # Handle strategies that work on full document structure
            if chunking_strategy == "hierarchical":
                _log_chunking_operation('info', f"Using hierarchical chunking strategy", context)
                hierarchical_start = time.time()
                hierarchical_chunks = self._chunk_hierarchical(normalized_doc, chunk_size, overlap)
                hierarchical_time = time.time() - hierarchical_start
                _log_chunking_operation('info', f"Hierarchical chunking completed: {len(hierarchical_chunks)} chunks in {hierarchical_time:.2f}s", context)
                # Convert to chunk properties format
                chunks = []
                for chunk_data in hierarchical_chunks:
                    from contextsynapse.extraction.id_generator import IDGenerator
                    chunk_id = IDGenerator.generate_chunk_id(
                        document_id=document_id,
                        page_no=chunk_data["page_no"],
                        chunk_index=chunk_data["chunk_index"],
                        chunking_strategy=chunking_strategy,
                        strategy_version=strategy_version,
                        namespace=None
                    )
                    chunk_props = self.create_chunk_with_pointer(
                        chunk_id=chunk_id,
                        content=chunk_data["content"],
                        document_id=document_id,
                        normalized_version=normalized_version,
                        page_no=chunk_data["page_no"],
                        start_char=chunk_data.get("start_char"),
                        end_char=chunk_data.get("end_char"),
                        chunk_index=chunk_data["chunk_index"],
                        chunking_strategy=chunking_strategy,
                        strategy_version=strategy_version,
                        chunking_params={"chunk_size": chunk_size, "overlap": overlap},
                        document_node_id=document_node_id,
                        metadata={
                            "chunk_size": len(chunk_data["content"]),
                            "level": chunk_data.get("level", "chunk"),
                            "parent": chunk_data.get("parent"),
                            "section": chunk_data.get("section"),
                        }
                    )
                    # Write immediately if callback provided, otherwise accumulate
                    if chunk_callback:
                        chunk_callback(chunk_props)
                    else:
                        chunks.append(chunk_props)
                return chunks if not chunk_callback else []
            
            if chunking_strategy == "parent_child":
                _log_chunking_operation('info', f"Using parent_child chunking strategy", context)
                parent_child_start = time.time()
                parent_child_chunks = self._chunk_parent_child(normalized_doc, chunk_size, overlap)
                parent_child_time = time.time() - parent_child_start
                _log_chunking_operation('info', f"Parent-child chunking completed: {len(parent_child_chunks)} chunks in {parent_child_time:.2f}s", context)
                # Convert to chunk properties format
                chunks = []
                for chunk_data in parent_child_chunks:
                    from contextsynapse.extraction.id_generator import IDGenerator
                    chunk_id = IDGenerator.generate_chunk_id(
                        document_id=document_id,
                        page_no=chunk_data["page_no"],
                        chunk_index=chunk_data["chunk_index"],
                        chunking_strategy=chunking_strategy,
                        strategy_version=strategy_version,
                        namespace=None
                    )
                    chunk_props = self.create_chunk_with_pointer(
                        chunk_id=chunk_id,
                        content=chunk_data["content"],
                        document_id=document_id,
                        normalized_version=normalized_version,
                        page_no=chunk_data["page_no"],
                        start_char=chunk_data.get("start_char"),
                        end_char=chunk_data.get("end_char"),
                        chunk_index=chunk_data["chunk_index"],
                        chunking_strategy=chunking_strategy,
                        strategy_version=strategy_version,
                        chunking_params={"chunk_size": chunk_size, "overlap": overlap},
                        document_node_id=document_node_id,
                        metadata={
                            "chunk_size": len(chunk_data["content"]),
                            "level": chunk_data.get("level", "chunk"),
                            "parent": chunk_data.get("parent"),
                            "parent_chunk_index": chunk_data.get("parent_chunk_index"),
                            "children": chunk_data.get("children", []),
                        }
                    )
                    # Write immediately if callback provided, otherwise accumulate
                    if chunk_callback:
                        chunk_callback(chunk_props)
                    else:
                        chunks.append(chunk_props)
                return chunks if not chunk_callback else []
            
            # Page-by-page chunking strategies
            chunks = []
            pages = normalized_doc.get('content', {}).get('pages', [])
            
            _log_chunking_operation('info', f"Processing {len(pages)} pages with {chunking_strategy} strategy", context)
            
            # Special handling for cross_page strategy (needs all pages)
            if chunking_strategy == "cross_page":
                _log_chunking_operation('info', f"Using cross_page chunking strategy", context)
                context_pages = kwargs.get('context_pages', 1)
                context_size = kwargs.get('context_size', 200)
                for page_idx, page in enumerate(pages):
                    page_chunks = self._chunk_cross_page(pages, page_idx, chunk_size, overlap, context_pages, context_size)
                    page_no = page.get('page_no')
                    
                    # Create chunk nodes
                    for i, (chunk_text, start_char, end_char) in enumerate(page_chunks):
                        from contextsynapse.extraction.id_generator import IDGenerator
                        chunk_id = IDGenerator.generate_chunk_id(
                            document_id=document_id,
                            page_no=page_no,
                            chunk_index=i,
                            chunking_strategy=chunking_strategy,
                            strategy_version=strategy_version,
                            namespace=None
                        )
                        chunk_props = self.create_chunk_with_pointer(
                            chunk_id=chunk_id,
                            content=chunk_text,
                            document_id=document_id,
                            normalized_version=normalized_version,
                            page_no=page_no,
                            start_char=start_char,
                            end_char=end_char,
                            chunk_index=i,
                            chunking_strategy=chunking_strategy,
                            strategy_version=strategy_version,
                            chunking_params={"chunk_size": chunk_size, "overlap": overlap, **kwargs},
                            document_node_id=document_node_id,
                            metadata={"chunk_size": len(chunk_text), "cross_page_context": True}
                        )
                        # Write immediately if callback provided, otherwise accumulate
                        if chunk_callback:
                            chunk_callback(chunk_props)
                        else:
                            chunks.append(chunk_props)
                return chunks if not chunk_callback else []
            
            page_chunking_times = []
            for page_idx, page in enumerate(pages):
                page_no = page.get('page_no', page_idx + 1)
                page_text = page.get('flat_text', '')
                page_context = {**context, 'page_no': page_no, 'page_idx': page_idx}
                
                if not page_text:
                    _log_chunking_operation('debug', f"Page has no text, skipping", page_context)
                    continue
                
                page_text_length = len(page_text)
                _log_chunking_operation('debug', f"Chunking page with {page_text_length:,} characters", page_context)
                
                # Page monitoring hook - before processing (if available from executor)
                page_start_metrics = None
                page_monitor = getattr(self, 'page_monitor', None) or kwargs.get('page_monitor')
                if page_monitor:
                    try:
                        page_start_metrics = page_monitor.monitor_page_start(page_no)
                    except Exception:
                        pass  # Silently fail if monitor not available
                
                page_chunk_start = time.time()
                try:
                    # Chunk the page text
                    # Use LangChain internally if available for better chunking quality
                    if chunking_strategy == "semantic":
                        page_chunks = self._chunk_semantic_with_langchain(page_text, chunk_size, overlap, **kwargs)
                    elif chunking_strategy == "paragraph":
                        page_chunks = self._chunk_paragraph(page_text, overlap)
                    elif chunking_strategy == "recursive":
                        page_chunks = self._chunk_recursive_with_langchain(page_text, chunk_size, overlap, **kwargs)
                    elif chunking_strategy == "sliding_window":
                        page_chunks = self._chunk_sliding_window(page_text, chunk_size, overlap, **kwargs)
                    elif chunking_strategy == "section_based":
                        page_chunks = self._chunk_section_based(page, chunk_size, overlap, **kwargs)
                    elif chunking_strategy == "table_aware":
                        page_chunks = self._chunk_table_aware(page, normalized_doc, chunk_size, overlap, **kwargs)
                    elif chunking_strategy == "smart":
                        page_chunks = self._chunk_smart(page, normalized_doc, chunk_size, overlap, **kwargs)
                    elif chunking_strategy == "token_based":
                        max_tokens = kwargs.get('max_tokens', 512)
                        overlap_tokens = kwargs.get('overlap_tokens', 50)
                        tokenizer = kwargs.get('tokenizer', 'tiktoken')
                        model = kwargs.get('model', 'gpt-3.5-turbo')
                        page_chunks = self._chunk_token_based(page_text, max_tokens, overlap_tokens, tokenizer, model)
                    elif chunking_strategy == "semantic_similarity":
                        similarity_threshold = kwargs.get('similarity_threshold', 0.7)
                        max_chunk_size = kwargs.get('max_chunk_size', chunk_size)
                        min_chunk_size = kwargs.get('min_chunk_size', 200)
                        embedding_model = kwargs.get('embedding_model', 'sentence-transformers')
                        page_chunks = self._chunk_semantic_similarity(page_text, similarity_threshold, max_chunk_size, min_chunk_size, embedding_model)
                    elif chunking_strategy == "topic_aware":
                        num_topics = kwargs.get('num_topics', 10)
                        min_chunk_size = kwargs.get('min_chunk_size', 300)
                        topic_model = kwargs.get('topic_model', 'simple')
                        page_chunks = self._chunk_topic_aware(page_text, num_topics, min_chunk_size, topic_model)
                    elif chunking_strategy == "qa_aware":
                        question_length = kwargs.get('question_length', 50)
                        context_size = kwargs.get('context_size', 500)
                        answer_markers = kwargs.get('answer_markers', None)
                        page_chunks = self._chunk_qa_aware(page_text, question_length, context_size, answer_markers)
                    elif chunking_strategy == "code_aware":
                        preserve_code_blocks = kwargs.get('preserve_code_blocks', True)
                        code_context = kwargs.get('code_context', 200)
                        min_code_block_size = kwargs.get('min_code_block_size', 50)
                        page_chunks = self._chunk_code_aware(page, chunk_size, overlap, preserve_code_blocks, code_context, min_code_block_size)
                    elif chunking_strategy == "image_aware":
                        include_captions = kwargs.get('include_captions', True)
                        caption_context = kwargs.get('caption_context', 300)
                        image_chunk_size = kwargs.get('image_chunk_size', 200)
                        page_chunks = self._chunk_image_aware(page, normalized_doc, chunk_size, overlap, include_captions, caption_context, image_chunk_size)
                    elif chunking_strategy == "adaptive":
                        base_chunk_size = kwargs.get('base_chunk_size', chunk_size)
                        complexity_threshold = kwargs.get('complexity_threshold', 0.5)
                        density_factor = kwargs.get('density_factor', 1.2)
                        page_chunks = self._chunk_adaptive(page_text, base_chunk_size, overlap, complexity_threshold, density_factor)
                    elif chunking_strategy == "entity_aware":
                        entity_types = kwargs.get('entity_types', None)
                        page_chunks = self._chunk_entity_aware(page_text, chunk_size, overlap, entity_types)
                    elif chunking_strategy == "multilang":
                        per_language_chunking = kwargs.get('per_language_chunking', True)
                        page_chunks = self._chunk_multilang(page_text, chunk_size, overlap, per_language_chunking)
                    elif chunking_strategy == "citation_aware":
                        citation_patterns = kwargs.get('citation_patterns', None)
                        citation_context = kwargs.get('citation_context', 300)
                        page_chunks = self._chunk_citation_aware(page_text, chunk_size, overlap, citation_patterns, citation_context)
                    elif chunking_strategy == "dialogue_aware":
                        speaker_detection = kwargs.get('speaker_detection', True)
                        preserve_turns = kwargs.get('preserve_turns', True)
                        page_chunks = self._chunk_dialogue_aware(page_text, chunk_size, overlap, speaker_detection, preserve_turns)
                    elif chunking_strategy == "formula_aware":
                        formula_patterns = kwargs.get('formula_patterns', None)
                        formula_context = kwargs.get('formula_context', 200)
                        page_chunks = self._chunk_formula_aware(page_text, chunk_size, overlap, formula_patterns, formula_context)
                    elif chunking_strategy == "list_aware":
                        preserve_lists = kwargs.get('preserve_lists', True)
                        list_context = kwargs.get('list_context', 200)
                        page_chunks = self._chunk_list_aware(page, chunk_size, overlap, preserve_lists, list_context)
                    elif chunking_strategy == "cross_page":
                        # cross_page is handled before the loop (needs all pages)
                        # Skip this page in the loop
                        page_chunks = []
                        _log_chunking_operation('debug', 
                            f"Cross-page strategy: skipping page (handled before loop)", 
                            page_context)
                    else:  # fixed
                        page_chunks = self._chunk_fixed(page_text, chunk_size, overlap)
                    
                    page_chunk_time = time.time() - page_chunk_start
                    if page_chunks:  # Only log if we actually created chunks
                        page_chunking_times.append(page_chunk_time)
                        _log_chunking_operation('debug', 
                            f"Page chunking completed: {len(page_chunks)} chunks in {page_chunk_time:.3f}s", 
                            page_context)
                except Exception as page_err:
                    page_chunk_time = time.time() - page_chunk_start
                    _log_chunking_operation('error', 
                        f"Error chunking page: {str(page_err)}", 
                        page_context, exc_info=True)
                    logger.error(f"Full traceback for page {page_no}: {traceback.format_exc()}")
                    page_chunks = []  # Set to empty list on error
                
                # Create chunk nodes with pointers
                # Validate chunks don't exceed page boundaries
                # Skip if no chunks were created (cross_page strategy or error)
                if not page_chunks:
                    continue
                    
                page_text_length = len(page_text)
                
                chunks_created_for_page = 0
                chunks_skipped_for_page = 0
                for i, (chunk_text, start_char, end_char) in enumerate(page_chunks):
                    chunk_context = {**page_context, 'chunk_index': i}
                    
                    # Validate chunk boundaries are within page
                    if start_char < 0:
                        _log_chunking_operation('warning', 
                            f"Chunk has negative start_char ({start_char}), clamping to 0", 
                            chunk_context)
                        start_char = 0
                    
                    if end_char > page_text_length:
                        _log_chunking_operation('warning', 
                            f"Chunk exceeds page boundary (end_char={end_char}, page_length={page_text_length}), clamping", 
                            chunk_context)
                        end_char = page_text_length
                        # Truncate chunk text to match
                        chunk_text = chunk_text[:end_char - start_char]
                    
                    if start_char >= end_char:
                        _log_chunking_operation('warning', 
                            f"Chunk has invalid boundaries (start={start_char}, end={end_char}), skipping", 
                            chunk_context)
                        chunks_skipped_for_page += 1
                        continue
                    
                    # Verify chunk text matches the page text at these positions
                    expected_text = page_text[start_char:end_char]
                    if chunk_text != expected_text and len(chunk_text) > 0:
                        # Allow small differences (whitespace normalization), but log significant mismatches
                        if abs(len(chunk_text) - len(expected_text)) > 10:
                            _log_chunking_operation('warning', 
                                f"Text mismatch: chunk_len={len(chunk_text)}, expected_len={len(expected_text)}", 
                                chunk_context)
                    
                    # Use IDGenerator for consistent chunk ID generation
                    from contextsynapse.extraction.id_generator import IDGenerator
                    
                    chunk_id = IDGenerator.generate_chunk_id(
                        document_id=document_id,
                        page_no=page_no,
                        chunk_index=i,
                        chunking_strategy=chunking_strategy,
                        strategy_version=strategy_version,
                        namespace=None  # Will be set by executor
                    )
                    
                    chunk_props = self.create_chunk_with_pointer(
                        chunk_id=chunk_id,
                        content=chunk_text,
                        document_id=document_id,
                        normalized_version=normalized_version,
                        page_no=page_no,
                        start_char=start_char,
                        end_char=end_char,
                        chunk_index=i,
                        chunking_strategy=chunking_strategy,
                        strategy_version=strategy_version,
                        chunking_params={
                            "chunk_size": chunk_size,
                            "overlap": overlap
                        },
                        document_node_id=document_node_id,
                        metadata={
                            "chunk_size": len(chunk_text),
                            "token_count": len(chunk_text.split()),
                            "page_boundary_validated": True,  # Flag to indicate validation
                        }
                    )
                    
                    # Write immediately if callback provided, otherwise accumulate
                    if chunk_callback:
                        chunk_callback(chunk_props)
                    else:
                        chunks.append(chunk_props)
                    chunks_created_for_page += 1
                
                if chunks_created_for_page > 0:
                    _log_chunking_operation('debug', 
                        f"Page processing complete: {chunks_created_for_page} chunks created, {chunks_skipped_for_page} skipped", 
                        page_context)
                
                # Page monitoring hook - after processing (if available)
                page_monitor = getattr(self, 'page_monitor', None) or kwargs.get('page_monitor')
                if page_monitor and page_start_metrics:
                    try:
                        page_time = time.time() - page_chunk_start
                        page_monitor.monitor_page_end(page_no, page_start_metrics, 
                                                     chunks_created=chunks_created_for_page, 
                                                     processing_time=page_time)
                    except Exception:
                        pass  # Silently fail if monitor not available
            
            operation_time = time.time() - operation_start
            avg_page_time = sum(page_chunking_times) / len(page_chunking_times) if page_chunking_times else 0
            # If using callback, chunks list is empty, so we can't calculate stats from it
            # In that case, the callback should track stats
            total_chunks = len(chunks) if not chunk_callback else 0
            avg_chunk_size = sum(len(c.get('content', '')) for c in chunks) / total_chunks if total_chunks > 0 else 0
            
            _log_chunking_operation('info', 
                f"Chunk creation complete: {total_chunks if not chunk_callback else 'streamed'} chunks in {operation_time:.2f}s "
                f"(avg {avg_page_time:.3f}s/page, avg chunk size: {avg_chunk_size:.0f} chars)", 
                context)
            
            return chunks if not chunk_callback else []
            
        except Exception as e:
            operation_time = time.time() - operation_start
            _log_chunking_operation('error', 
                f"Failed to create chunks: {str(e)} (after {operation_time:.2f}s)", 
                context, exc_info=True)
            logger.error(f"Full traceback:\n{traceback.format_exc()}")
            return []
    
    def _chunk_semantic_with_langchain(self, text: str, chunk_size: int, overlap: int, **kwargs) -> List[tuple]:
        """
        Chunk text using semantic boundaries with LangChain if available.
        Falls back to built-in semantic chunking if LangChain is not available.
        """
        # Try to use LangChain RecursiveCharacterTextSplitter if available
        try:
            from langchain.text_splitter import RecursiveCharacterTextSplitter
            
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=overlap,
                separators=["\n\n", "\n", ". ", " ", ""],  # Sentence-aware separators
                length_function=len,
                is_separator_regex=False,
            )
            
            langchain_chunks = splitter.split_text(text)
            
            # Convert LangChain chunks to our format (chunk_text, start_char, end_char)
            chunks = []
            current_pos = 0
            for chunk_text in langchain_chunks:
                if not chunk_text.strip():
                    continue
                
                # Find the chunk in the original text
                start_char = text.find(chunk_text, current_pos)
                if start_char == -1:
                    start_char = current_pos
                
                end_char = start_char + len(chunk_text)
                chunks.append((chunk_text, start_char, end_char))
                current_pos = end_char - overlap  # Account for overlap
            
            logger.debug(f"LangChain recursive chunking created {len(chunks)} chunks")
            return chunks
            
        except ImportError:
            # Fall back to built-in semantic chunking
            logger.debug("LangChain not available, using built-in semantic chunking")
            return self._chunk_semantic(text, chunk_size, overlap)
        except Exception as e:
            logger.warning(f"LangChain chunking failed: {e}, falling back to built-in semantic chunking")
            return self._chunk_semantic(text, chunk_size, overlap)
    
    def _chunk_recursive_with_langchain(self, text: str, chunk_size: int, overlap: int, **kwargs) -> List[tuple]:
        """
        Chunk text using recursive splitting with LangChain if available.
        Falls back to built-in recursive chunking if LangChain is not available.
        """
        # Try to use LangChain RecursiveCharacterTextSplitter if available
        try:
            from langchain.text_splitter import RecursiveCharacterTextSplitter
            
            separators = kwargs.get('separators', ["\n\n", "\n", ". ", " ", ""])
            
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=chunk_size,
                chunk_overlap=overlap,
                separators=separators,
                length_function=len,
                is_separator_regex=False,
            )
            
            langchain_chunks = splitter.split_text(text)
            
            # Convert LangChain chunks to our format
            chunks = []
            current_pos = 0
            for chunk_text in langchain_chunks:
                if not chunk_text.strip():
                    continue
                
                start_char = text.find(chunk_text, current_pos)
                if start_char == -1:
                    start_char = current_pos
                
                end_char = start_char + len(chunk_text)
                chunks.append((chunk_text, start_char, end_char))
                current_pos = end_char - overlap
            
            logger.debug(f"LangChain recursive chunking created {len(chunks)} chunks")
            return chunks
            
        except ImportError:
            # Fall back to built-in recursive chunking
            logger.debug("LangChain not available, using built-in recursive chunking")
            return self._chunk_recursive(text, chunk_size, overlap)
        except Exception as e:
            logger.warning(f"LangChain chunking failed: {e}, falling back to built-in recursive chunking")
            return self._chunk_recursive(text, chunk_size, overlap)
    
    def _chunk_semantic(self, text: str, chunk_size: int, overlap: int) -> List[tuple]:
        """
        Chunk text using semantic boundaries (sentence-aware).
        
        Logs detailed information about chunking process for debugging.
        """
        """
        Semantic chunking (sentence-aware).
        
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        import re
        
        # Log input details
        text_length = len(text) if text else 0
        logger.debug(f"[_chunk_semantic] Starting: text_length={text_length:,}, chunk_size={chunk_size}, overlap={overlap}")
        
        if not text or not text.strip():
            logger.warning(f"[_chunk_semantic] Empty text provided, returning empty chunks")
            return []
        
        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)
        logger.debug(f"[_chunk_semantic] Split text into {len(sentences)} sentences")
        
        if len(sentences) == 0:
            logger.warning(f"[_chunk_semantic] No sentences found, returning single chunk")
            return [(text, 0, text_length)]
        
        chunks = []
        current_chunk = []
        current_size = 0
        start_char = 0
        chunk_count = 0
        
        for sentence_idx, sentence in enumerate(sentences):
            sentence_size = len(sentence)
            
            if current_size + sentence_size > chunk_size and current_chunk:
                # Save current chunk
                chunk_text = ' '.join(current_chunk)
                end_char = start_char + len(chunk_text)
                chunks.append((chunk_text, start_char, end_char))
                chunk_count += 1
                
                # Log chunk creation (first 3 and every 10th)
                if chunk_count <= 3 or chunk_count % 10 == 0:
                    chunk_preview = chunk_text[:100].replace('\n', ' ').strip()
                    logger.debug(f"[_chunk_semantic] Created chunk {chunk_count}: size={len(chunk_text):,} chars, start={start_char}, end={end_char}, preview={chunk_preview}...")
                
                # Start new chunk with overlap
                overlap_text = ' '.join(current_chunk[-overlap//50:]) if overlap > 0 else ''
                current_chunk = [overlap_text, sentence] if overlap_text else [sentence]
                current_size = len(overlap_text) + sentence_size if overlap_text else sentence_size
                start_char = end_char - len(overlap_text) if overlap_text else end_char
            else:
                current_chunk.append(sentence)
                current_size += sentence_size
        
        # Add final chunk
        if current_chunk:
            chunk_text = ' '.join(current_chunk)
            end_char = start_char + len(chunk_text)
            chunks.append((chunk_text, start_char, end_char))
            chunk_count += 1
            chunk_preview = chunk_text[:100].replace('\n', ' ').strip()
            logger.debug(f"[_chunk_semantic] Created final chunk {chunk_count}: size={len(chunk_text):,} chars, start={start_char}, end={end_char}, preview={chunk_preview}...")
        
        logger.info(f"[_chunk_semantic] Completed: {len(chunks)} chunks created from {text_length:,} chars")
        return chunks
    
    def _chunk_fixed(self, text: str, chunk_size: int, overlap: int) -> List[tuple]:
        """
        Fixed-size chunking with memory-efficient handling for large texts.
        
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        chunks = []
        start_char = 0
        text_length = len(text)
        
        logger.debug(f"[_chunk_fixed] Starting fixed chunking (text_length={text_length}, chunk_size={chunk_size}, overlap={overlap})")
        
        # For very large texts (>10MB), process in batches to avoid memory issues
        # This prevents accumulating all chunks in memory at once
        max_batch_size = 1000  # Process up to 1000 chunks at a time for very large texts
        is_very_large = text_length > 10_000_000  # >10MB
        
        if is_very_large:
            logger.warning(f"[_chunk_fixed] Very large text detected ({text_length:,} chars), using memory-efficient batch processing")
        
        chunk_count = 0
        while start_char < text_length:
            end_char = min(start_char + chunk_size, text_length)
            chunk_text = text[start_char:end_char]
            chunks.append((chunk_text, start_char, end_char))
            chunk_count += 1
            start_char = end_char - overlap if overlap > 0 else end_char
            
            # For very large texts, yield chunks in batches to avoid memory buildup
            # Note: This is a safeguard - in practice, chunks should be written immediately
            # by the caller, so this shouldn't be needed, but it provides extra safety
            if is_very_large and chunk_count % max_batch_size == 0:
                # Force garbage collection periodically for very large texts
                import gc
                gc.collect()
                logger.debug(f"[_chunk_fixed] Processed {chunk_count} chunks so far (memory safeguard)")
        
        logger.debug(f"[_chunk_fixed] Created {len(chunks)} chunks")
        return chunks
    
    def _chunk_paragraph(self, text: str, overlap: int) -> List[tuple]:
        """
        Paragraph-based chunking.
        
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        paragraphs = text.split('\n\n')
        chunks = []
        start_char = 0
        
        for para in paragraphs:
            if not para.strip():
                continue
            
            chunk_text = para.strip()
            end_char = start_char + len(chunk_text)
            chunks.append((chunk_text, start_char, end_char))
            start_char = end_char + 2  # +2 for \n\n
        
        return chunks
    
    def _chunk_hierarchical(self, normalized_doc: Dict[str, Any], chunk_size: int, overlap: int) -> List[Dict[str, Any]]:
        """
        Hierarchical chunking that preserves document structure.
        
        Uses normalized blocks to create chunks that respect document hierarchy:
        - Document -> Page -> Section/Heading -> Paragraph -> Chunk
        
        Returns:
            List of chunk dictionaries with hierarchical metadata
        """
        chunks = []
        document_id = normalized_doc.get("document_id", "unknown")
        pages = normalized_doc.get("content", {}).get("pages", [])
        
        logger.debug(f"[_chunk_hierarchical] Processing {len(pages)} pages for document {document_id}")
        
        for page in pages:
            page_no = page.get("page_no", 0)
            normalized_blocks = page.get("normalized_blocks", [])
            flat_text = page.get("flat_text", "")
            
            if not normalized_blocks:
                # Fallback to fixed chunking if no blocks
                logger.debug(f"[_chunk_hierarchical] Page {page_no} has no normalized_blocks, using fixed chunking fallback")
                page_chunks = self._chunk_fixed(flat_text, chunk_size, overlap)
                for i, (chunk_text, start, end) in enumerate(page_chunks):
                    chunks.append({
                        "content": chunk_text,
                        "page_no": page_no,
                        "chunk_index": len(chunks),
                        "level": "chunk",
                        "parent": f"page_{page_no}",
                        "start_char": start,
                        "end_char": end,
                    })
                continue
            
            # Use hierarchical structure from blocks
            current_section = None
            current_paragraph_group = []
            current_start_char = 0
            
            for block_idx, block in enumerate(normalized_blocks):
                block_type = block.get("type", "paragraph")
                block_text = block.get("text", "")
                block_id = block.get("id", f"block_{page_no}_{block_idx}")
                
                if block_type == "heading":
                    # Save previous paragraph group as chunk
                    if current_paragraph_group:
                        chunk_text = "\n\n".join(current_paragraph_group)
                        if len(chunk_text) > 50:  # Minimum chunk size
                            chunks.append({
                                "content": chunk_text,
                                "page_no": page_no,
                                "chunk_index": len(chunks),
                                "level": "paragraph_group",
                                "parent": current_section or f"page_{page_no}",
                                "section": current_section,
                                "start_char": current_start_char,
                                "end_char": current_start_char + len(chunk_text),
                            })
                        current_paragraph_group = []
                    
                    # Start new section
                    level = block.get("level", 1)
                    current_section = f"section_{page_no}_{block_idx}"
                    
                    # Heading itself can be a chunk if substantial
                    if len(block_text) > 50:
                        chunks.append({
                            "content": block_text,
                            "page_no": page_no,
                            "chunk_index": len(chunks),
                            "level": f"heading_{level}",
                            "parent": f"page_{page_no}",
                            "section": current_section,
                            "start_char": current_start_char,
                            "end_char": current_start_char + len(block_text),
                        })
                        current_start_char += len(block_text) + 2
                
                elif block_type == "paragraph":
                    # Add to current paragraph group
                    if block_text.strip():
                        current_paragraph_group.append(block_text)
                        
                        # If group is large enough, create chunk
                        group_text = "\n\n".join(current_paragraph_group)
                        if len(group_text) >= chunk_size:
                            chunks.append({
                                "content": group_text,
                                "page_no": page_no,
                                "chunk_index": len(chunks),
                                "level": "paragraph_group",
                                "parent": current_section or f"page_{page_no}",
                                "section": current_section,
                                "start_char": current_start_char,
                                "end_char": current_start_char + len(group_text),
                            })
                            current_start_char += len(group_text) + 2
                            current_paragraph_group = []
            
            # Save remaining paragraph group
            if current_paragraph_group:
                chunk_text = "\n\n".join(current_paragraph_group)
                if len(chunk_text) > 50:
                    chunks.append({
                        "content": chunk_text,
                        "page_no": page_no,
                        "chunk_index": len(chunks),
                        "level": "paragraph_group",
                        "parent": current_section or f"page_{page_no}",
                        "section": current_section,
                        "start_char": current_start_char,
                        "end_char": current_start_char + len(chunk_text),
                    })
        
        return chunks
    
    def _chunk_recursive(self, text: str, chunk_size: int, overlap: int, max_depth: int = 3) -> List[tuple]:
        """
        Recursive chunking - recursively splits text into smaller chunks.
        
        Strategy:
        1. Try to split at paragraph boundaries
        2. If still too large, split at sentence boundaries
        3. If still too large, split at word boundaries
        4. If still too large, use fixed-size chunking
        
        Optimized to avoid deep recursion and improve performance.
        
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        # Pre-compile regex for better performance
        import re
        sentence_pattern = re.compile(r'(?<=[.!?])\s+')
        
        def recursive_split(text_segment: str, start_pos: int, depth: int = 0) -> List[tuple]:
            # Base case: segment is small enough or max depth reached
            if depth >= max_depth or len(text_segment) <= chunk_size:
                return [(text_segment, start_pos, start_pos + len(text_segment))]
            
            # Try paragraph split first (most efficient)
            if '\n\n' in text_segment:
                paragraphs = text_segment.split('\n\n')
                # Filter out empty paragraphs early
                paragraphs = [p.strip() for p in paragraphs if p.strip()]
                if len(paragraphs) > 1:
                    result = []
                    current_pos = start_pos
                    for para in paragraphs:
                        if len(para) <= chunk_size:
                            # Paragraph fits in one chunk
                            result.append((para, current_pos, current_pos + len(para)))
                            current_pos += len(para) + 2  # +2 for \n\n
                        else:
                            # Recursively split large paragraph
                            para_chunks = recursive_split(para, current_pos, depth + 1)
                            result.extend(para_chunks)
                            if para_chunks:
                                current_pos = para_chunks[-1][2] + 2  # Update position
                    return result
            
            # Try sentence split (more granular)
            sentences = sentence_pattern.split(text_segment)
            if len(sentences) > 1:
                result = []
                current_pos = start_pos
                current_chunk = []
                current_size = 0
                
                for sentence in sentences:
                    if not sentence.strip():
                        continue
                    sentence_size = len(sentence)
                    
                    # If adding this sentence would exceed chunk_size, save current chunk
                    if current_size + sentence_size > chunk_size and current_chunk:
                        chunk_text = ' '.join(current_chunk)
                        result.append((chunk_text, current_pos, current_pos + len(chunk_text)))
                        current_pos = current_pos + len(chunk_text) + 1
                        current_chunk = [sentence]
                        current_size = sentence_size
                    else:
                        current_chunk.append(sentence)
                        current_size += sentence_size + 1
                
                # Add final chunk
                if current_chunk:
                    chunk_text = ' '.join(current_chunk)
                    result.append((chunk_text, current_pos, current_pos + len(chunk_text)))
                
                return result
            
            # Fallback to fixed-size chunking (fastest for very large segments)
            return self._chunk_fixed(text_segment, chunk_size, overlap)
        
        chunks = recursive_split(text, 0, 0)
        return chunks
    
    def _chunk_parent_child(self, normalized_doc: Dict[str, Any], chunk_size: int, overlap: int) -> List[Dict[str, Any]]:
        """
        Parent-child chunking that creates parent chunks and child chunks.
        
        Strategy:
        - Creates parent chunks at section/heading level
        - Creates child chunks at paragraph level
        - Maintains parent-child relationships
        
        Returns:
            List of chunk dictionaries with parent-child metadata
        """
        chunks = []
        document_id = normalized_doc.get("document_id", "unknown")
        pages = normalized_doc.get("content", {}).get("pages", [])
        
        for page in pages:
            page_no = page.get("page_no", 0)
            normalized_blocks = page.get("normalized_blocks", [])
            
            if not normalized_blocks:
                continue
            
            current_parent = None
            current_parent_content = []
            child_chunks = []
            
            for block_idx, block in enumerate(normalized_blocks):
                block_type = block.get("type", "paragraph")
                block_text = block.get("text", "")
                
                if block_type == "heading":
                    # Save previous parent chunk with its children
                    if current_parent and current_parent_content:
                        parent_content = "\n\n".join(current_parent_content)
                        if len(parent_content) > 50:
                            parent_chunk = {
                                "content": parent_content,
                                "page_no": page_no,
                                "chunk_index": len(chunks),
                                "level": "parent",
                                "parent": f"page_{page_no}",
                                "children": [c["chunk_index"] for c in child_chunks],
                                "start_char": 0,  # Will be calculated
                                "end_char": len(parent_content),
                            }
                            chunks.append(parent_chunk)
                            
                            # Add child chunks
                            for child in child_chunks:
                                child["parent_chunk_index"] = parent_chunk["chunk_index"]
                                chunks.append(child)
                        
                        child_chunks = []
                        current_parent_content = []
                    
                    # Start new parent
                    level = block.get("level", 1)
                    current_parent = {
                        "id": f"parent_{page_no}_{block_idx}",
                        "level": level,
                        "heading": block_text,
                    }
                    current_parent_content.append(block_text)
                
                elif block_type == "paragraph" and current_parent:
                    # Add to parent content
                    current_parent_content.append(block_text)
                    
                    # Create child chunk if paragraph is substantial
                    if len(block_text) > 100:
                        child_chunk = {
                            "content": block_text,
                            "page_no": page_no,
                            "chunk_index": len(chunks) + len(child_chunks),
                            "level": "child",
                            "parent": current_parent["id"],
                            "start_char": 0,  # Will be calculated
                            "end_char": len(block_text),
                        }
                        child_chunks.append(child_chunk)
            
            # Save final parent chunk
            if current_parent and current_parent_content:
                parent_content = "\n\n".join(current_parent_content)
                if len(parent_content) > 50:
                    parent_chunk = {
                        "content": parent_content,
                        "page_no": page_no,
                        "chunk_index": len(chunks),
                        "level": "parent",
                        "parent": f"page_{page_no}",
                        "children": [c["chunk_index"] for c in child_chunks],
                        "start_char": 0,
                        "end_char": len(parent_content),
                    }
                    chunks.append(parent_chunk)
                    
                    # Add child chunks
                    for child in child_chunks:
                        child["parent_chunk_index"] = parent_chunk["chunk_index"]
                        chunks.append(child)
        
        return chunks
    
    def _chunk_sliding_window(self, text: str, window_size: int, overlap: int, step_size: Optional[int] = None, **kwargs) -> List[tuple]:
        """
        Sliding window chunking with configurable step size.
        
        Args:
            text: Text to chunk
            window_size: Size of the sliding window
            overlap: Overlap between chunks (used if step_size not provided)
            step_size: Step size for sliding window (optional)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        if step_size is None:
            step_size = window_size - overlap
        
        chunks = []
        start_char = 0
        
        while start_char < len(text):
            end_char = min(start_char + window_size, len(text))
            chunk_text = text[start_char:end_char]
            chunks.append((chunk_text, start_char, end_char))
            start_char += step_size
        
        return chunks
    
    def _chunk_section_based(self, page: Dict[str, Any], chunk_size: int, overlap: int, 
                             section_markers: Optional[List[str]] = None, preserve_hierarchy: bool = True, **kwargs) -> List[tuple]:
        """
        Section-based chunking that splits at section boundaries.
        
        Args:
            page: Page dictionary with normalized_blocks
            chunk_size: Maximum chunk size
            overlap: Overlap between chunks
            section_markers: List of section markers (e.g., ["##", "###", "Chapter"])
            preserve_hierarchy: Whether to preserve hierarchy in chunks
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        if section_markers is None:
            section_markers = ["##", "###", "Chapter", "Section", "Part", "#"]
        
        normalized_blocks = page.get("normalized_blocks", [])
        flat_text = page.get("flat_text", "")
        
        if not normalized_blocks:
            # Fallback to fixed chunking
            return self._chunk_fixed(flat_text, chunk_size, overlap)
        
        chunks = []
        current_section = []
        current_size = 0
        start_char = 0
        
        for block in normalized_blocks:
            block_type = block.get("type", "paragraph")
            block_text = block.get("text", "")
            
            # Check if this is a section marker
            is_section = block_type == "heading" or any(
                block_text.strip().startswith(marker) for marker in section_markers
            )
            
            if is_section and current_section:
                # Save current section as chunk
                section_text = "\n\n".join(current_section)
                if len(section_text) > 50:
                    end_char = start_char + len(section_text)
                    chunks.append((section_text, start_char, end_char))
                    start_char = end_char - overlap if overlap > 0 else end_char
                
                current_section = [block_text]
                current_size = len(block_text)
            else:
                current_section.append(block_text)
                current_size += len(block_text) + 2  # +2 for \n\n
                
                # If section exceeds chunk_size, split it
                if current_size > chunk_size:
                    section_text = "\n\n".join(current_section)
                    # Split large section using semantic chunking
                    section_chunks = self._chunk_semantic(section_text, chunk_size, overlap)
                    for chunk_text, chunk_start, chunk_end in section_chunks:
                        chunks.append((chunk_text, start_char + chunk_start, start_char + chunk_end))
                    start_char += len(section_text)
                    current_section = []
                    current_size = 0
        
        # Save remaining section
        if current_section:
            section_text = "\n\n".join(current_section)
            if len(section_text) > 50:
                end_char = start_char + len(section_text)
                chunks.append((section_text, start_char, end_char))
        
        return chunks
    
    def _chunk_table_aware(self, page: Dict[str, Any], normalized_doc: Dict[str, Any], 
                          chunk_size: int, overlap: int, 
                          extract_tables: bool = True, preserve_table_context: bool = True,
                          table_chunk_size: int = 300, context_before: int = 200, context_after: int = 200, **kwargs) -> List[tuple]:
        """
        Table-aware chunking that preserves table context.
        
        Args:
            page: Page dictionary
            normalized_doc: Full normalized document
            chunk_size: Chunk size for text
            overlap: Overlap between chunks
            extract_tables: Whether to extract tables as separate chunks
            preserve_table_context: Whether to include context around tables
            table_chunk_size: Size for table chunks
            context_before: Context before table
            context_after: Context after table
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        chunks = []
        flat_text = page.get("flat_text", "")
        page_no = page.get("page_no", 0)
        
        # Get tables for this page
        tables = normalized_doc.get("content", {}).get("tables", [])
        page_tables = [t for t in tables if t.get("from_page", 0) == page_no]
        
        if not page_tables or not extract_tables:
            # No tables or table extraction disabled, use regular chunking
            return self._chunk_semantic(flat_text, chunk_size, overlap)
        
        # Build table positions
        table_positions = []
        for table in page_tables:
            # Try to find table position in text
            table_markdown = table.get("markdown", "")
            if table_markdown:
                pos = flat_text.find(table_markdown)
                if pos >= 0:
                    table_positions.append((pos, pos + len(table_markdown), table))
        
        # Sort by position
        table_positions.sort(key=lambda x: x[0])
        
        # Chunk text with table awareness
        current_pos = 0
        for table_start, table_end, table in table_positions:
            # Chunk text before table
            if table_start > current_pos:
                before_text = flat_text[current_pos:table_start]
                if preserve_table_context and len(before_text) > context_before:
                    # Include context
                    context_start = max(0, table_start - context_before)
                    before_text = flat_text[context_start:table_start]
                    before_chunks = self._chunk_semantic(before_text, chunk_size, overlap)
                    for chunk_text, chunk_start, chunk_end in before_chunks:
                        chunks.append((chunk_text, context_start + chunk_start, context_start + chunk_end))
                elif before_text.strip():
                    before_chunks = self._chunk_semantic(before_text, chunk_size, overlap)
                    for chunk_text, chunk_start, chunk_end in before_chunks:
                        chunks.append((chunk_text, current_pos + chunk_start, current_pos + chunk_end))
            
            # Add table as chunk
            if extract_tables:
                table_text = f"[TABLE]\n{table_markdown}\n[/TABLE]"
                chunks.append((table_text, table_start, table_end))
            
            current_pos = table_end
        
        # Chunk remaining text
        if current_pos < len(flat_text):
            remaining_text = flat_text[current_pos:]
            if preserve_table_context and len(remaining_text) > context_after:
                context_end = min(len(flat_text), current_pos + context_after)
                remaining_text = flat_text[current_pos:context_end]
            
            if remaining_text.strip():
                remaining_chunks = self._chunk_semantic(remaining_text, chunk_size, overlap)
                for chunk_text, chunk_start, chunk_end in remaining_chunks:
                    chunks.append((chunk_text, current_pos + chunk_start, current_pos + chunk_end))
        
        return chunks
    
    def _chunk_smart(self, page: Dict[str, Any], normalized_doc: Dict[str, Any], 
                     chunk_size: int, overlap: int, 
                     document_type_detection: bool = True, fallback_strategy: str = "fixed", **kwargs) -> List[tuple]:
        """
        Smart chunking that auto-detects document type and selects best strategy.
        
        Args:
            page: Page dictionary
            normalized_doc: Full normalized document
            chunk_size: Chunk size
            overlap: Overlap between chunks
            document_type_detection: Whether to detect document type
            fallback_strategy: Fallback strategy if detection fails
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        if not document_type_detection:
            # Use fallback strategy
            page_text = page.get("flat_text", "")
            if fallback_strategy == "semantic":
                return self._chunk_semantic(page_text, chunk_size, overlap)
            elif fallback_strategy == "paragraph":
                return self._chunk_paragraph(page_text, overlap)
            elif fallback_strategy == "recursive":
                return self._chunk_recursive(page_text, chunk_size, overlap)
            else:
                return self._chunk_fixed(page_text, chunk_size, overlap)
        
        # Detect document type
        page_text = page.get("flat_text", "")
        normalized_blocks = page.get("normalized_blocks", [])
        
        # Count different elements
        heading_count = sum(1 for b in normalized_blocks if b.get("type") == "heading")
        table_count = len([t for t in normalized_doc.get("content", {}).get("tables", []) 
                          if t.get("from_page", 0) == page.get("page_no", 0)])
        paragraph_count = sum(1 for b in normalized_blocks if b.get("type") == "paragraph")
        
        # Decision logic
        if table_count > 0:
            # Document has tables, use table-aware chunking
            return self._chunk_table_aware(page, normalized_doc, chunk_size, overlap, **kwargs)
        elif heading_count > 3:
            # Many headings, use section-based chunking
            return self._chunk_section_based(page, chunk_size, overlap, **kwargs)
        elif paragraph_count > 10:
            # Many paragraphs, use paragraph-based chunking
            return self._chunk_paragraph(page_text, overlap)
        else:
            # Default to semantic chunking
            return self._chunk_semantic(page_text, chunk_size, overlap)
    
    def _chunk_token_based(self, text: str, max_tokens: int, overlap_tokens: int = 50, 
                           tokenizer: str = "tiktoken", model: str = "gpt-3.5-turbo", **kwargs) -> List[tuple]:
        """
        Token-based chunking for LLM compatibility.
        
        Args:
            text: Text to chunk
            max_tokens: Maximum tokens per chunk
            overlap_tokens: Overlap in tokens
            tokenizer: Tokenizer to use ("tiktoken" or "transformers")
            model: Model name for tokenizer (default: "gpt-3.5-turbo")
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        try:
            if tokenizer == "tiktoken":
                try:
                    import tiktoken
                    enc = tiktoken.encoding_for_model(model)
                    tokens = enc.encode(text)
                except ImportError:
                    logger.warning("tiktoken not available, falling back to character-based estimation")
                    # Fallback: estimate tokens as ~4 chars per token
                    return self._chunk_fixed(text, max_tokens * 4, overlap_tokens * 4)
                except Exception as e:
                    logger.warning(f"tiktoken encoding failed: {e}, falling back to character-based")
                    return self._chunk_fixed(text, max_tokens * 4, overlap_tokens * 4)
            else:
                # Fallback to character-based estimation
                return self._chunk_fixed(text, max_tokens * 4, overlap_tokens * 4)
            
            chunks = []
            start_token = 0
            
            while start_token < len(tokens):
                end_token = min(start_token + max_tokens, len(tokens))
                chunk_tokens = tokens[start_token:end_token]
                
                # Decode tokens back to text
                chunk_text = enc.decode(chunk_tokens)
                
                # Find position in original text
                # Approximate: find the text that corresponds to these tokens
                if start_token == 0:
                    start_char = 0
                else:
                    # Estimate start position
                    start_char = int((start_token / len(tokens)) * len(text))
                
                if end_token >= len(tokens):
                    end_char = len(text)
                else:
                    # Estimate end position
                    end_char = int((end_token / len(tokens)) * len(text))
                
                # Refine positions by finding actual text
                if chunk_text in text:
                    actual_start = text.find(chunk_text, start_char)
                    if actual_start >= 0:
                        start_char = actual_start
                        end_char = actual_start + len(chunk_text)
                
                chunks.append((chunk_text, start_char, end_char))
                start_token = end_token - overlap_tokens if overlap_tokens > 0 else end_token
            
            return chunks
        except Exception as e:
            logger.error(f"Token-based chunking failed: {e}, falling back to fixed chunking")
            return self._chunk_fixed(text, max_tokens * 4, overlap_tokens * 4)
    
    def _chunk_semantic_similarity(self, text: str, similarity_threshold: float = 0.7,
                                   max_chunk_size: int = 1000, min_chunk_size: int = 200,
                                   embedding_model: str = "sentence-transformers", **kwargs) -> List[tuple]:
        """
        Semantic similarity chunking - groups similar sentences together.
        
        Args:
            text: Text to chunk
            similarity_threshold: Minimum similarity to group (default: 0.7)
            max_chunk_size: Maximum chunk size (default: 1000)
            min_chunk_size: Minimum chunk size (default: 200)
            embedding_model: Model for embeddings (default: "sentence-transformers")
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        try:
            # Try to use sentence transformers
            try:
                from sentence_transformers import SentenceTransformer
                model = SentenceTransformer('all-MiniLM-L6-v2')  # Lightweight model
                
                # Split into sentences
                import re
                sentences = re.split(r'(?<=[.!?])\s+', text)
                sentences = [s.strip() for s in sentences if s.strip()]
                
                if len(sentences) < 2:
                    # Not enough sentences, use semantic chunking
                    return self._chunk_semantic(text, max_chunk_size, 0)
                
                # Generate embeddings
                embeddings = model.encode(sentences)
                
                # Group similar sentences
                chunks = []
                current_chunk = [sentences[0]]
                current_size = len(sentences[0])
                current_start = 0
                current_embedding = embeddings[0]
                
                for i in range(1, len(sentences)):
                    sentence = sentences[i]
                    sentence_embedding = embeddings[i]
                    
                    # Calculate similarity (cosine similarity)
                    try:
                        import numpy as np
                        similarity = np.dot(current_embedding, sentence_embedding) / (
                            np.linalg.norm(current_embedding) * np.linalg.norm(sentence_embedding)
                        )
                    except ImportError:
                        # Fallback: simple dot product if numpy not available
                        similarity = sum(a * b for a, b in zip(current_embedding, sentence_embedding)) / (
                            (sum(a*a for a in current_embedding) ** 0.5) * (sum(b*b for b in sentence_embedding) ** 0.5)
                        )
                    
                    # Check if we should add to current chunk
                    if (similarity >= similarity_threshold and 
                        current_size + len(sentence) <= max_chunk_size):
                        current_chunk.append(sentence)
                        current_size += len(sentence) + 1
                        # Update embedding (average)
                        current_embedding = (current_embedding + sentence_embedding) / 2
                    else:
                        # Save current chunk
                        if current_size >= min_chunk_size:
                            chunk_text = ' '.join(current_chunk)
                            end_char = current_start + len(chunk_text)
                            chunks.append((chunk_text, current_start, end_char))
                            current_start = end_char
                        
                        # Start new chunk
                        current_chunk = [sentence]
                        current_size = len(sentence)
                        current_embedding = sentence_embedding
                
                # Add final chunk
                if current_chunk and current_size >= min_chunk_size:
                    chunk_text = ' '.join(current_chunk)
                    end_char = current_start + len(chunk_text)
                    chunks.append((chunk_text, current_start, end_char))
                
                return chunks if chunks else self._chunk_semantic(text, max_chunk_size, 0)
                
            except ImportError:
                logger.warning("sentence-transformers not available, falling back to semantic chunking")
                return self._chunk_semantic(text, max_chunk_size, 0)
        except Exception as e:
            logger.error(f"Semantic similarity chunking failed: {e}, falling back to semantic chunking")
            return self._chunk_semantic(text, max_chunk_size, 0)
    
    def _chunk_topic_aware(self, text: str, num_topics: int = 10, min_chunk_size: int = 300,
                          topic_model: str = "simple", **kwargs) -> List[tuple]:
        """
        Topic-aware chunking - splits at topic boundaries.
        
        Args:
            text: Text to chunk
            num_topics: Number of topics to detect (default: 10)
            min_chunk_size: Minimum chunk size (default: 300)
            topic_model: Topic modeling algorithm ("simple", "lda", "bertopic")
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        # Simple topic detection based on keyword frequency
        # For full LDA/BERTopic, would need additional dependencies
        import re
        
        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)
        sentences = [s.strip() for s in sentences if s.strip()]
        
        if len(sentences) < num_topics:
            # Not enough sentences, use semantic chunking
            return self._chunk_semantic(text, 1000, 200)
        
        # Simple topic detection: group sentences by common keywords
        chunks = []
        current_chunk = []
        current_size = 0
        start_char = 0
        
        # Extract keywords from sentences (simple approach)
        def extract_keywords(sentence, top_n=3):
            words = re.findall(r'\b\w{4,}\b', sentence.lower())
            # Count frequency
            from collections import Counter
            return [w for w, _ in Counter(words).most_common(top_n)]
        
        prev_keywords = set()
        
        for sentence in sentences:
            keywords = set(extract_keywords(sentence))
            
            # Check if sentence shares keywords with previous chunk
            if prev_keywords and keywords.intersection(prev_keywords):
                # Same topic, add to current chunk
                current_chunk.append(sentence)
                current_size += len(sentence) + 1
                prev_keywords.update(keywords)
            else:
                # New topic, save current chunk
                if current_chunk and current_size >= min_chunk_size:
                    chunk_text = ' '.join(current_chunk)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                
                # Start new chunk
                current_chunk = [sentence]
                current_size = len(sentence)
                prev_keywords = keywords
        
        # Add final chunk
        if current_chunk and current_size >= min_chunk_size:
            chunk_text = ' '.join(current_chunk)
            end_char = start_char + len(chunk_text)
            chunks.append((chunk_text, start_char, end_char))
        
        return chunks if chunks else self._chunk_semantic(text, 1000, 200)
    
    def _chunk_qa_aware(self, text: str, question_length: int = 50, context_size: int = 500,
                       answer_markers: Optional[List[str]] = None, **kwargs) -> List[tuple]:
        """
        Q&A-aware chunking - pairs questions with answers.
        
        Args:
            text: Text to chunk
            question_length: Expected question length (default: 50)
            context_size: Context around answers (default: 500)
            answer_markers: Markers for answers (default: ["Answer:", "A:", "Solution:"])
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        if answer_markers is None:
            answer_markers = ["Answer:", "A:", "Solution:", "Answer", "Solution"]
        
        import re
        
        chunks = []
        lines = text.split('\n')
        current_chunk = []
        current_size = 0
        start_char = 0
        in_qa_pair = False
        
        for line in lines:
            line_stripped = line.strip()
            
            # Check if line is a question (ends with ?)
            is_question = line_stripped.endswith('?')
            
            # Check if line is an answer (starts with answer marker)
            is_answer = any(line_stripped.startswith(marker) for marker in answer_markers)
            
            if is_question:
                # Save previous chunk if exists
                if current_chunk and current_size >= context_size:
                    chunk_text = '\n'.join(current_chunk)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_chunk = []
                    current_size = 0
                
                # Start new Q&A chunk
                current_chunk.append(line)
                current_size = len(line)
                in_qa_pair = True
            elif is_answer or in_qa_pair:
                # Add answer/context to current chunk
                current_chunk.append(line)
                current_size += len(line) + 1
                
                # If we've collected enough context, save chunk
                if current_size >= context_size:
                    chunk_text = '\n'.join(current_chunk)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_chunk = []
                    current_size = 0
                    in_qa_pair = False
            else:
                # Regular text
                if in_qa_pair:
                    # Add to current Q&A chunk
                    current_chunk.append(line)
                    current_size += len(line) + 1
                else:
                    # Start new chunk
                    if current_chunk:
                        chunk_text = '\n'.join(current_chunk)
                        end_char = start_char + len(chunk_text)
                        chunks.append((chunk_text, start_char, end_char))
                        start_char = end_char
                    current_chunk = [line]
                    current_size = len(line)
        
        # Add final chunk
        if current_chunk:
            chunk_text = '\n'.join(current_chunk)
            end_char = start_char + len(chunk_text)
            chunks.append((chunk_text, start_char, end_char))
        
        return chunks if chunks else self._chunk_semantic(text, 1000, 200)
    
    def _chunk_code_aware(self, page: Dict[str, Any], chunk_size: int, overlap: int,
                         preserve_code_blocks: bool = True, code_context: int = 200,
                         min_code_block_size: int = 50, **kwargs) -> List[tuple]:
        """
        Code-aware chunking - preserves code blocks.
        
        Args:
            page: Page dictionary with normalized blocks
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            preserve_code_blocks: Keep code blocks intact (default: True)
            code_context: Context around code (default: 200)
            min_code_block_size: Minimum code block size (default: 50)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        normalized_blocks = page.get("normalized_blocks", [])
        flat_text = page.get("flat_text", "")
        
        if not normalized_blocks:
            return self._chunk_semantic(flat_text, chunk_size, overlap)
        
        chunks = []
        current_chunk = []
        current_size = 0
        start_char = 0
        
        for block in normalized_blocks:
            block_type = block.get("type", "paragraph")
            block_text = block.get("text", "")
            
            # Detect code blocks (simple heuristic: lines starting with common code patterns)
            is_code = False
            if block_type in ["code", "pre"]:
                is_code = True
            elif block_text:
                # Check for code indicators
                code_indicators = ["def ", "class ", "import ", "function ", "{", "}", "=", "()", "[]"]
                code_line_count = sum(1 for line in block_text.split('\n') 
                                     if any(indicator in line for indicator in code_indicators))
                is_code = code_line_count > len(block_text.split('\n')) * 0.3
            
            if is_code and preserve_code_blocks:
                # Save previous chunk
                if current_chunk and current_size > 0:
                    chunk_text = '\n\n'.join(current_chunk)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_chunk = []
                    current_size = 0
                
                # Add code block as separate chunk with context
                if len(block_text) >= min_code_block_size:
                    # Add context before code
                    context_before = flat_text[max(0, start_char - code_context):start_char]
                    if context_before:
                        current_chunk.append(context_before)
                    
                    # Add code block
                    current_chunk.append(block_text)
                    current_size = len(block_text) + len(context_before)
                    
                    # Save code chunk
                    chunk_text = '\n\n'.join(current_chunk)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_chunk = []
                    current_size = 0
            else:
                # Regular text block
                current_chunk.append(block_text)
                current_size += len(block_text) + 2
                
                # If chunk is large enough, save it
                if current_size >= chunk_size:
                    chunk_text = '\n\n'.join(current_chunk)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_chunk = []
                    current_size = 0
        
        # Add final chunk
        if current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            end_char = start_char + len(chunk_text)
            chunks.append((chunk_text, start_char, end_char))
        
        return chunks if chunks else self._chunk_semantic(flat_text, chunk_size, overlap)
    
    def _chunk_image_aware(self, page: Dict[str, Any], normalized_doc: Dict[str, Any],
                          chunk_size: int, overlap: int, include_captions: bool = True,
                          caption_context: int = 300, image_chunk_size: int = 200, **kwargs) -> List[tuple]:
        """
        Image-aware chunking - includes image context and captions.
        
        Args:
            page: Page dictionary
            normalized_doc: Full normalized document
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            include_captions: Include image captions (default: True)
            caption_context: Context around images (default: 300)
            image_chunk_size: Size for image chunks (default: 200)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        flat_text = page.get("flat_text", "")
        page_no = page.get("page_no", 0)
        
        # Get images for this page
        # NOTE: We only use image metadata (caption, id, path) - NOT image bytes
        # This prevents loading large image data into memory during chunking
        images = normalized_doc.get("content", {}).get("images", [])
        page_images = [img for img in images if img.get("from_page", 0) == page_no]
        
        if not page_images:
            return self._chunk_semantic(flat_text, chunk_size, overlap)
        
        chunks = []
        current_pos = 0
        
        for image in page_images:
            # Get image metadata only (caption, id, path) - do NOT load image bytes
            # Image bytes are stored separately and should not be loaded into memory here
            # Images are for reference only - chunks contain only image placeholders and metadata
            caption = image.get("caption", "") or image.get("description", "")
            image_id = image.get("id", f"image_{page_no}_{len(chunks)}")
            image_path = image.get("path", "")
            
            # CRITICAL: Ensure we're not accidentally including image bytes
            # (image_bytes_base64 should not be present, but check to be safe)
            # If image bytes are found, exclude them and log a warning
            if "image_bytes" in image:
                logger.warning(f"[CHUNK] Image {image_id} contains 'image_bytes' - removing from chunking data (images are reference-only)")
                # Remove image bytes to prevent loading into memory
                image = {k: v for k, v in image.items() if k != "image_bytes"}
            if "image_bytes_base64" in image:
                logger.warning(f"[CHUNK] Image {image_id} contains 'image_bytes_base64' - removing from chunking data (images are reference-only)")
                # Remove base64 image data to prevent loading into memory
                image = {k: v for k, v in image.items() if k != "image_bytes_base64"}
            
            # Create image reference placeholder - images are NOT loaded into chunks
            # Only include image ID and caption (if available) as text reference
            image_text = f"[IMAGE: {image_id}]"
            if image_path:
                image_text += f" (path: {image_path})"
            if caption:
                image_text += f"\nCaption: {caption}"
            
            # Chunk text before image
            if current_pos < len(flat_text):
                before_text = flat_text[current_pos:]
                if include_captions:
                    # Include context before image
                    context_start = max(0, current_pos - caption_context)
                    before_text = flat_text[context_start:current_pos]
                
                if before_text.strip():
                    before_chunks = self._chunk_semantic(before_text, chunk_size, overlap)
                    for chunk_text, chunk_start, chunk_end in before_chunks:
                        chunks.append((chunk_text, context_start + chunk_start if include_captions else current_pos + chunk_start,
                                      context_start + chunk_end if include_captions else current_pos + chunk_end))
                    if before_chunks:
                        current_pos = context_start + before_chunks[-1][2] if include_captions else current_pos + before_chunks[-1][2]
            
            # Add image chunk
            if include_captions and caption:
                chunks.append((image_text, current_pos, current_pos + len(image_text)))
                current_pos += len(image_text)
        
        # Chunk remaining text
        if current_pos < len(flat_text):
            remaining_text = flat_text[current_pos:]
            if remaining_text.strip():
                remaining_chunks = self._chunk_semantic(remaining_text, chunk_size, overlap)
                for chunk_text, chunk_start, chunk_end in remaining_chunks:
                    chunks.append((chunk_text, current_pos + chunk_start, current_pos + chunk_end))
        
        return chunks if chunks else self._chunk_semantic(flat_text, chunk_size, overlap)
    
    def _chunk_adaptive(self, text: str, base_chunk_size: int = 1000, overlap: int = 200,
                       complexity_threshold: float = 0.5, density_factor: float = 1.2, **kwargs) -> List[tuple]:
        """
        Adaptive chunking - adjusts chunk size based on content complexity.
        
        Args:
            text: Text to chunk
            base_chunk_size: Base chunk size (default: 1000)
            overlap: Overlap (default: 200)
            complexity_threshold: Complexity threshold (default: 0.5)
            density_factor: Density adjustment factor (default: 1.2)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        import re
        
        # Calculate complexity metrics
        sentences = re.split(r'(?<=[.!?])\s+', text)
        avg_sentence_length = sum(len(s) for s in sentences) / len(sentences) if sentences else 0
        
        # Count complex words (long words, technical terms)
        words = re.findall(r'\b\w+\b', text)
        complex_words = sum(1 for w in words if len(w) > 6)
        complexity_ratio = complex_words / len(words) if words else 0
        
        # Adjust chunk size based on complexity
        if complexity_ratio > complexity_threshold or avg_sentence_length > 150:
            # High complexity - use smaller chunks
            adjusted_chunk_size = int(base_chunk_size / density_factor)
        elif complexity_ratio < 0.2 and avg_sentence_length < 50:
            # Low complexity - use larger chunks
            adjusted_chunk_size = int(base_chunk_size * density_factor)
        else:
            adjusted_chunk_size = base_chunk_size
        
        # Use semantic chunking with adjusted size
        return self._chunk_semantic(text, adjusted_chunk_size, overlap)
    
    def _chunk_cross_page(self, pages: List[Dict[str, Any]], page_index: int, chunk_size: int, overlap: int,
                         context_pages: int = 1, context_size: int = 200, **kwargs) -> List[tuple]:
        """
        Cross-page context chunking - includes context from adjacent pages.
        
        Args:
            pages: List of all pages
            page_index: Current page index
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            context_pages: Number of adjacent pages (default: 1)
            context_size: Size of context from adjacent pages (default: 200)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        if page_index >= len(pages):
            return []
        
        current_page = pages[page_index]
        page_text = current_page.get("flat_text", "")
        
        # Add context from previous page
        if page_index > 0 and context_pages > 0:
            prev_page = pages[page_index - 1]
            prev_text = prev_page.get("flat_text", "")
            context_from_prev = prev_text[-context_size:] if len(prev_text) > context_size else prev_text
            page_text = context_from_prev + "\n\n" + page_text
        
        # Add context from next page
        if page_index < len(pages) - 1 and context_pages > 0:
            next_page = pages[page_index + 1]
            next_text = next_page.get("flat_text", "")
            context_from_next = next_text[:context_size] if len(next_text) > context_size else next_text
            page_text = page_text + "\n\n" + context_from_next
        
        # Chunk with extended context
        return self._chunk_semantic(page_text, chunk_size, overlap)
    
    def _chunk_entity_aware(self, text: str, chunk_size: int, overlap: int,
                           entity_types: Optional[List[str]] = None, **kwargs) -> List[tuple]:
        """
        Entity-aware chunking - ensures entities stay in same chunk.
        
        Args:
            text: Text to chunk
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            entity_types: Types of entities to preserve (default: ["PERSON", "ORG", "LOC"])
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        if entity_types is None:
            entity_types = ["PERSON", "ORG", "LOC"]
        
        # Simple entity detection using patterns (for full NER, would need spaCy/NLTK)
        import re
        
        # Common entity patterns
        entity_patterns = {
            "PERSON": r'\b[A-Z][a-z]+ [A-Z][a-z]+\b',  # "John Smith"
            "ORG": r'\b[A-Z][A-Za-z]+ (Inc|Corp|LLC|Ltd|Company)\b',  # "Apple Inc"
            "LOC": r'\b[A-Z][a-z]+(?: [A-Z][a-z]+)*(?: City|State|Country)\b',  # "New York City"
        }
        
        # Find entities
        entities = []
        for entity_type in entity_types:
            if entity_type in entity_patterns:
                matches = re.finditer(entity_patterns[entity_type], text)
                for match in matches:
                    entities.append((match.start(), match.end(), entity_type))
        
        # Sort entities by position
        entities.sort(key=lambda x: x[0])
        
        if not entities:
            # No entities found, use semantic chunking
            return self._chunk_semantic(text, chunk_size, overlap)
        
        # Chunk while preserving entities
        chunks = []
        current_chunk = []
        current_size = 0
        start_char = 0
        entity_index = 0
        
        # Split into sentences
        sentences = re.split(r'(?<=[.!?])\s+', text)
        
        for sentence in sentences:
            sentence_start = text.find(sentence, start_char + current_size)
            sentence_end = sentence_start + len(sentence)
            
            # Check if sentence contains entities
            sentence_entities = [e for e in entities if sentence_start <= e[0] < sentence_end]
            
            # If adding this sentence would exceed chunk_size and it has entities, start new chunk
            if (current_size + len(sentence) > chunk_size and 
                sentence_entities and current_chunk):
                # Save current chunk
                chunk_text = ' '.join(current_chunk)
                end_char = start_char + len(chunk_text)
                chunks.append((chunk_text, start_char, end_char))
                start_char = end_char
                current_chunk = [sentence]
                current_size = len(sentence)
            else:
                current_chunk.append(sentence)
                current_size += len(sentence) + 1
        
        # Add final chunk
        if current_chunk:
            chunk_text = ' '.join(current_chunk)
            end_char = start_char + len(chunk_text)
            chunks.append((chunk_text, start_char, end_char))
        
        return chunks if chunks else self._chunk_semantic(text, chunk_size, overlap)
    
    def _chunk_multilang(self, text: str, chunk_size: int, overlap: int,
                        per_language_chunking: bool = True, **kwargs) -> List[tuple]:
        """
        Multi-language chunking - handles multiple languages.
        
        Args:
            text: Text to chunk
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            per_language_chunking: Chunk per language (default: True)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        try:
            # Try language detection
            try:
                from langdetect import detect_langs, DetectorFactory
                DetectorFactory.seed = 0  # For consistent results
                
                # Detect language of full text
                languages = detect_langs(text)
                primary_lang = languages[0].lang if languages else 'en'
                
                if per_language_chunking and len(languages) > 1:
                    # Multiple languages detected - chunk per language
                    # Simple approach: use semantic chunking (works for most languages)
                    return self._chunk_semantic(text, chunk_size, overlap)
                else:
                    # Single language or per-language disabled
                    return self._chunk_semantic(text, chunk_size, overlap)
            except ImportError:
                logger.warning("langdetect not available, using semantic chunking")
                return self._chunk_semantic(text, chunk_size, overlap)
        except Exception as e:
            logger.warning(f"Language detection failed: {e}, using semantic chunking")
            return self._chunk_semantic(text, chunk_size, overlap)
    
    def _chunk_citation_aware(self, text: str, chunk_size: int, overlap: int,
                             citation_patterns: Optional[List[str]] = None,
                             citation_context: int = 300, **kwargs) -> List[tuple]:
        """
        Citation-aware chunking - preserves citations with context.
        
        Args:
            text: Text to chunk
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            citation_patterns: Citation patterns (default: ["[1]", "(Smith, 2020)", "et al."])
            citation_context: Context around citations (default: 300)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        if citation_patterns is None:
            citation_patterns = [
                r'\[\d+\]',  # [1], [2], etc.
                r'\([A-Z][a-z]+,?\s+\d{4}\)',  # (Smith, 2020)
                r'et al\.',  # et al.
                r'\([A-Z][a-z]+ et al\.',  # (Smith et al.
            ]
        
        import re
        
        # Find all citations
        citations = []
        for pattern in citation_patterns:
            matches = re.finditer(pattern, text)
            for match in matches:
                citations.append((match.start(), match.end()))
        
        # Sort by position
        citations.sort(key=lambda x: x[0])
        
        if not citations:
            # No citations, use semantic chunking
            return self._chunk_semantic(text, chunk_size, overlap)
        
        # Chunk while preserving citation context
        chunks = []
        current_pos = 0
        
        for cit_start, cit_end in citations:
            # Chunk text before citation
            if cit_start > current_pos:
                before_text = text[current_pos:cit_start]
                if before_text.strip():
                    before_chunks = self._chunk_semantic(before_text, chunk_size, overlap)
                    for chunk_text, chunk_start, chunk_end in before_chunks:
                        chunks.append((chunk_text, current_pos + chunk_start, current_pos + chunk_end))
                    if before_chunks:
                        current_pos = current_pos + before_chunks[-1][2]
            
            # Include citation with context
            context_start = max(0, cit_start - citation_context)
            context_end = min(len(text), cit_end + citation_context)
            citation_chunk = text[context_start:context_end]
            
            if citation_chunk.strip():
                chunks.append((citation_chunk, context_start, context_end))
                current_pos = context_end
        
        # Chunk remaining text
        if current_pos < len(text):
            remaining_text = text[current_pos:]
            if remaining_text.strip():
                remaining_chunks = self._chunk_semantic(remaining_text, chunk_size, overlap)
                for chunk_text, chunk_start, chunk_end in remaining_chunks:
                    chunks.append((chunk_text, current_pos + chunk_start, current_pos + chunk_end))
        
        return chunks if chunks else self._chunk_semantic(text, chunk_size, overlap)
    
    def _chunk_dialogue_aware(self, text: str, chunk_size: int, overlap: int,
                              speaker_detection: bool = True, preserve_turns: bool = True, **kwargs) -> List[tuple]:
        """
        Dialogue-aware chunking - preserves dialogue structure.
        
        Args:
            text: Text to chunk
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            speaker_detection: Detect speakers (default: True)
            preserve_turns: Keep dialogue turns together (default: True)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        import re
        
        # Detect dialogue patterns
        # Common patterns: "Speaker: text", "Speaker said:", etc.
        dialogue_pattern = r'^([A-Z][A-Za-z\s]+?):\s+(.+)$'
        
        lines = text.split('\n')
        chunks = []
        current_dialogue = []
        current_size = 0
        start_char = 0
        
        for line in lines:
            line_match = re.match(dialogue_pattern, line)
            
            if line_match and preserve_turns:
                # This is a dialogue line
                if current_dialogue and current_size + len(line) > chunk_size:
                    # Save current dialogue chunk
                    chunk_text = '\n'.join(current_dialogue)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_dialogue = [line]
                    current_size = len(line)
                else:
                    current_dialogue.append(line)
                    current_size += len(line) + 1
            else:
                # Not dialogue, save current dialogue if exists
                if current_dialogue:
                    chunk_text = '\n'.join(current_dialogue)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_dialogue = []
                    current_size = 0
                
                # Add regular text
                if line.strip():
                    chunks.append((line, start_char, start_char + len(line)))
                    start_char += len(line) + 1
        
        # Add final dialogue chunk
        if current_dialogue:
            chunk_text = '\n'.join(current_dialogue)
            end_char = start_char + len(chunk_text)
            chunks.append((chunk_text, start_char, end_char))
        
        return chunks if chunks else self._chunk_semantic(text, chunk_size, overlap)
    
    def _chunk_formula_aware(self, text: str, chunk_size: int, overlap: int,
                            formula_patterns: Optional[List[str]] = None,
                            formula_context: int = 200, **kwargs) -> List[tuple]:
        """
        Formula-aware chunking - preserves mathematical formulas.
        
        Args:
            text: Text to chunk
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            formula_patterns: Formula patterns (default: LaTeX, MathML patterns)
            formula_context: Context around formulas (default: 200)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        if formula_patterns is None:
            # Common formula patterns
            formula_patterns = [
                r'\$[^$]+\$',  # LaTeX inline: $formula$
                r'\$\$[^$]+\$\$',  # LaTeX display: $$formula$$
                r'\\begin\{equation\}.*?\\end\{equation\}',  # LaTeX equation
                r'<math>.*?</math>',  # MathML
            ]
        
        import re
        
        # Find all formulas
        formulas = []
        for pattern in formula_patterns:
            matches = re.finditer(pattern, text, re.DOTALL)
            for match in matches:
                formulas.append((match.start(), match.end()))
        
        # Sort by position
        formulas.sort(key=lambda x: x[0])
        
        if not formulas:
            # No formulas, use semantic chunking
            return self._chunk_semantic(text, chunk_size, overlap)
        
        # Chunk while preserving formula context (similar to citation_aware)
        chunks = []
        current_pos = 0
        
        for form_start, form_end in formulas:
            # Chunk text before formula
            if form_start > current_pos:
                before_text = text[current_pos:form_start]
                if before_text.strip():
                    before_chunks = self._chunk_semantic(before_text, chunk_size, overlap)
                    for chunk_text, chunk_start, chunk_end in before_chunks:
                        chunks.append((chunk_text, current_pos + chunk_start, current_pos + chunk_end))
                    if before_chunks:
                        current_pos = current_pos + before_chunks[-1][2]
            
            # Include formula with context
            context_start = max(0, form_start - formula_context)
            context_end = min(len(text), form_end + formula_context)
            formula_chunk = text[context_start:context_end]
            
            if formula_chunk.strip():
                chunks.append((formula_chunk, context_start, context_end))
                current_pos = context_end
        
        # Chunk remaining text
        if current_pos < len(text):
            remaining_text = text[current_pos:]
            if remaining_text.strip():
                remaining_chunks = self._chunk_semantic(remaining_text, chunk_size, overlap)
                for chunk_text, chunk_start, chunk_end in remaining_chunks:
                    chunks.append((chunk_text, current_pos + chunk_start, current_pos + chunk_end))
        
        return chunks if chunks else self._chunk_semantic(text, chunk_size, overlap)
    
    def _chunk_list_aware(self, page: Dict[str, Any], chunk_size: int, overlap: int,
                         preserve_lists: bool = True, list_context: int = 200, **kwargs) -> List[tuple]:
        """
        List-aware chunking - preserves list structure.
        
        Args:
            page: Page dictionary with normalized blocks
            chunk_size: Chunk size (default: 1000)
            overlap: Overlap (default: 200)
            preserve_lists: Keep lists intact (default: True)
            list_context: Context around lists (default: 200)
            
        Returns:
            List of (chunk_text, start_char, end_char) tuples
        """
        normalized_blocks = page.get("normalized_blocks", [])
        flat_text = page.get("flat_text", "")
        
        if not normalized_blocks:
            return self._chunk_semantic(flat_text, chunk_size, overlap)
        
        chunks = []
        current_chunk = []
        current_size = 0
        start_char = 0
        in_list = False
        
        for block in normalized_blocks:
            block_type = block.get("type", "paragraph")
            block_text = block.get("text", "")
            
            # Detect lists
            is_list = block_type in ["list", "list_item"] or (
                block_text.strip().startswith(('-', '*', '•', '1.', '2.', '3.'))
            )
            
            if is_list and preserve_lists:
                # Save previous chunk
                if current_chunk and current_size > 0:
                    chunk_text = '\n\n'.join(current_chunk)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_chunk = []
                    current_size = 0
                
                # Add list as separate chunk with context
                # Add context before list
                context_before = flat_text[max(0, start_char - list_context):start_char]
                if context_before:
                    current_chunk.append(context_before)
                
                # Add list
                current_chunk.append(block_text)
                current_size = len(block_text) + len(context_before)
                
                # Save list chunk
                chunk_text = '\n\n'.join(current_chunk)
                end_char = start_char + len(chunk_text)
                chunks.append((chunk_text, start_char, end_char))
                start_char = end_char
                current_chunk = []
                current_size = 0
                in_list = True
            else:
                # Regular text block
                if in_list:
                    # Add context after list
                    context_after = block_text[:list_context]
                    if context_after:
                        current_chunk.append(context_after)
                        current_size += len(context_after)
                    in_list = False
                
                current_chunk.append(block_text)
                current_size += len(block_text) + 2
                
                # If chunk is large enough, save it
                if current_size >= chunk_size:
                    chunk_text = '\n\n'.join(current_chunk)
                    end_char = start_char + len(chunk_text)
                    chunks.append((chunk_text, start_char, end_char))
                    start_char = end_char
                    current_chunk = []
                    current_size = 0
        
        # Add final chunk
        if current_chunk:
            chunk_text = '\n\n'.join(current_chunk)
            end_char = start_char + len(chunk_text)
            chunks.append((chunk_text, start_char, end_char))
        
        return chunks if chunks else self._chunk_semantic(flat_text, chunk_size, overlap)

