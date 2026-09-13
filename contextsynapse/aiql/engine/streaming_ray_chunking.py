"""
Streaming Ray Chunking - Combines streaming JSON parsing with Ray parallel processing.

This module provides the best of both worlds:
- Memory efficiency: Pages streamed one at a time (no full doc load)
- Speed: Parallel processing across multiple CPU cores with Ray
"""

import ray
from typing import Dict, List, Any, Optional, Iterator, Generator
from pathlib import Path
import time
import gc
import logging

logger = logging.getLogger(__name__)

# Check if Ray is available
RAY_AVAILABLE = False
try:
    import ray
    RAY_AVAILABLE = ray.is_initialized() or True  # Will initialize if needed
except ImportError:
    RAY_AVAILABLE = False


def _init_ray_if_needed() -> bool:
    """Initialize Ray if not already initialized."""
    if not RAY_AVAILABLE:
        return False
    
    try:
        if not ray.is_initialized():
            ray.init(ignore_reinit_error=True, num_cpus=None)  # Use all available CPUs
        return True
    except Exception as e:
        logger.warning(f"Ray initialization failed: {e}")
        return False


if RAY_AVAILABLE:
    @ray.remote(num_cpus=1, memory=1024 * 1024 * 1024)  # 1 CPU, 1GB memory per worker
    def remote_chunk_page_streaming(
        page_data: Dict[str, Any],
        document_id: str,
        normalized_version: str,
        chunking_strategy: str,
        chunk_size: int,
        overlap: int,
        namespace: str,
        base_dir: str,
        chunking_params: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """
        Remote function to chunk a single page (streaming-compatible).
        
        This is called by Ray workers - only receives one page at a time.
        No full document needed - perfect for streaming!
        
        Args:
            page_data: Single page dictionary (from stream)
            document_id: Document identifier
            normalized_version: Normalized document version
            chunking_strategy: Chunking strategy name
            chunk_size: Chunk size in characters
            overlap: Overlap between chunks
            namespace: Namespace name
            base_dir: Base directory for storage
            chunking_params: Additional chunking parameters
            
        Returns:
            List of chunk property dictionaries for this page
        """
        try:
            from ..engine.chunk_storage import ChunkStorage
            from contextsynapse.extraction.id_generator import IDGenerator
            
            chunk_storage = ChunkStorage(namespace=namespace, base_dir=base_dir)
            page_no = page_data.get('page_no', 0)
            page_text = page_data.get('flat_text', '')
            
            if not page_text or not page_text.strip():
                return []
            
            chunking_params = chunking_params or {}
            
            # Chunk the page based on strategy
            page_chunks = []
            if chunking_strategy == "semantic":
                page_chunks = chunk_storage._chunk_semantic(page_text, chunk_size, overlap)
            elif chunking_strategy == "paragraph":
                page_chunks = chunk_storage._chunk_paragraph(page_text, overlap)
            elif chunking_strategy == "recursive":
                page_chunks = chunk_storage._chunk_recursive(page_text, chunk_size, overlap)
            elif chunking_strategy == "sliding_window":
                step_size = chunking_params.get('step_size', chunk_size - overlap)
                page_chunks = chunk_storage._chunk_sliding_window(page_text, chunk_size, overlap, step_size=step_size)
            else:
                # Fallback to fixed
                page_chunks = chunk_storage._chunk_fixed(page_text, chunk_size, overlap)
            
            # Convert to chunk properties format
            chunk_props_list = []
            for chunk_idx, chunk_data in enumerate(page_chunks):
                if not isinstance(chunk_data, (tuple, list)) or len(chunk_data) < 2:
                    continue
                
                chunk_text = chunk_data[0] if len(chunk_data) > 0 else ""
                start = chunk_data[1] if len(chunk_data) > 1 else 0
                end = chunk_data[2] if len(chunk_data) > 2 else len(chunk_text)
                
                if not chunk_text or not chunk_text.strip():
                    continue
                
                # Generate chunk ID
                chunk_id = IDGenerator.generate_chunk_id(
                    document_id=document_id,
                    page_no=page_no,
                    chunk_index=chunk_idx,
                    chunking_strategy=chunking_strategy,
                    strategy_version="v1",
                    namespace=None
                )
                
                if not chunk_id:
                    continue
                
                # Create chunk properties
                chunk_props = {
                    "id": chunk_id,
                    "content": chunk_text,
                    "source_pointer": {
                        "document_id": document_id,
                        "normalized_version": normalized_version,
                        "page_no": page_no,
                        "start_char": start,
                        "end_char": end,
                        "chunking_strategy": chunking_strategy,
                        "strategy_version": "v1",
                        "chunking_params": {
                            "chunk_size": chunk_size,
                            "overlap": overlap
                        }
                    },
                    "chunk_index": chunk_idx,
                    "metadata": {
                        "chunk_size": len(chunk_text),
                        "page_no": page_no
                    }
                }
                
                chunk_props_list.append(chunk_props)
            
            return chunk_props_list
            
        except Exception as e:
            logger.error(f"[streaming_ray] Error chunking page {page_data.get('page_no', 'unknown')}: {e}")
            return []


def chunk_with_streaming_ray(
    normalized_file_path: Path,
    document_id: str,
    normalized_version: str,
    chunking_strategy: str,
    chunk_size: int,
    overlap: int,
    namespace: str,
    base_dir: str,
    chunking_params: Optional[Dict[str, Any]] = None,
    max_concurrent: Optional[int] = None,
    write_callback: Optional[callable] = None
) -> Generator[Dict[str, Any], None, None]:
    """
    Stream pages from JSON and chunk them in parallel using Ray.
    
    This combines:
    - Streaming JSON parsing (memory efficient)
    - Ray parallel processing (fast)
    
    Args:
        normalized_file_path: Path to normalized JSON file
        document_id: Document identifier
        normalized_version: Normalized document version
        chunking_strategy: Chunking strategy
        chunk_size: Chunk size in characters
        overlap: Overlap between chunks
        namespace: Namespace name
        base_dir: Base directory
        chunking_params: Additional chunking parameters
        max_concurrent: Maximum concurrent Ray tasks (None = auto-detect)
        write_callback: Optional callback to write chunks immediately (chunk_props) -> None
        
    Yields:
        Chunk property dictionaries as they're processed
    """
    if not RAY_AVAILABLE or not _init_ray_if_needed():
        raise RuntimeError("Ray is not available for streaming parallel chunking")
    
    from .streaming_json import StreamingNormalizedDocumentParser
    
    # Auto-detect max concurrent tasks
    if max_concurrent is None:
        try:
            import os
            max_concurrent = min(os.cpu_count() or 4, 8)  # Cap at 8 to avoid overhead
        except:
            max_concurrent = 4
    
    print(f"[STREAMING_RAY] Initializing streaming Ray chunking", flush=True)
    print(f"[STREAMING_RAY] File: {normalized_file_path}", flush=True)
    file_size_mb = normalized_file_path.stat().st_size / (1024 * 1024) if normalized_file_path.exists() else 0
    print(f"[STREAMING_RAY] File size: {file_size_mb:.2f} MB", flush=True)
    print(f"[STREAMING_RAY] Max concurrent tasks: {max_concurrent}", flush=True)
    print(f"[STREAMING_RAY] Strategy: {chunking_strategy}", flush=True)
    print(f"[STREAMING_RAY] Chunk size: {chunk_size}, Overlap: {overlap}", flush=True)
    
    # Log initial memory
    try:
        import psutil
        import os
        process = psutil.Process(os.getpid())
        mem_mb = process.memory_info().rss / (1024 * 1024)
        print(f"[STREAMING_RAY] Initial memory: {mem_mb:.1f}MB", flush=True)
    except:
        pass
    
    active_futures = {}  # page_no -> future
    page_counter = 0
    chunks_written = 0
    
    try:
        with StreamingNormalizedDocumentParser(normalized_file_path, strategy="page") as stream_parser:
            # Stream pages and submit to Ray workers
            for page in stream_parser.stream_pages():
                page_no = page.get('page_no', page_counter + 1)
                page_counter += 1
                
                # Log page being submitted
                page_text = page.get('flat_text', '')
                page_text_len = len(page_text) if isinstance(page_text, str) else 0
                print(f"[STREAMING_RAY] Submitting page {page_no} to Ray worker: {page_text_len:,} chars", flush=True)
                
                # Submit page to Ray worker
                future = remote_chunk_page_streaming.remote(
                    page_data=page,
                    document_id=document_id,
                    normalized_version=normalized_version,
                    chunking_strategy=chunking_strategy,
                    chunk_size=chunk_size,
                    overlap=overlap,
                    namespace=namespace,
                    base_dir=base_dir,
                    chunking_params=chunking_params
                )
                
                active_futures[page_no] = future
                print(f"[STREAMING_RAY] Page {page_no} submitted, active futures: {len(active_futures)}", flush=True)
                
                # Process completed futures (streaming results)
                # Use ray.wait() to get results as they complete
                ready_futures, _ = ray.wait(list(active_futures.values()), num_returns=1, timeout=0.1)
                
                for ready_future in ready_futures:
                    # Find which page this future belongs to
                    completed_page_no = None
                    for pno, fut in active_futures.items():
                        if fut == ready_future:
                            completed_page_no = pno
                            break
                    
                    if completed_page_no is None:
                        continue
                    
                        try:
                            # Get chunks from Ray worker
                            page_chunks = ray.get(ready_future)
                            
                            # Log page processing details
                            print(f"[STREAMING_RAY] Page {completed_page_no}: Received {len(page_chunks)} chunks from Ray worker", flush=True)
                            
                            # Yield chunks immediately (streaming)
                            for chunk_idx, chunk_props in enumerate(page_chunks):
                                chunks_written += 1
                                
                                # Log chunk details (first 3 and every 10th)
                                if chunks_written <= 3 or chunks_written % 10 == 0:
                                    chunk_id = chunk_props.get("id", "unknown")
                                    chunk_content = chunk_props.get("content", "")
                                    chunk_size = len(chunk_content) if isinstance(chunk_content, str) else 0
                                    chunk_preview = chunk_content[:100].replace('\n', ' ').strip() if chunk_content else ""
                                    print(f"[STREAMING_RAY]   Chunk {chunks_written}: id={chunk_id[:50]}..., size={chunk_size:,} chars", flush=True)
                                    if chunk_preview:
                                        print(f"[STREAMING_RAY]     Preview: {chunk_preview}...", flush=True)
                                
                                # Write immediately if callback provided
                                if write_callback:
                                    try:
                                        write_callback(chunk_props)
                                    except Exception as write_err:
                                        logger.error(f"[STREAMING_RAY] Write callback failed for chunk {chunks_written}: {write_err}")
                                        import traceback
                                        logger.error(traceback.format_exc())
                                
                                yield chunk_props
                            
                            # Remove completed future
                            del active_futures[completed_page_no]
                            
                            # Log memory after processing page
                            try:
                                import psutil
                                import os
                                process = psutil.Process(os.getpid())
                                mem_mb = process.memory_info().rss / (1024 * 1024)
                                print(f"[STREAMING_RAY] Page {completed_page_no}: Memory: {mem_mb:.1f}MB", flush=True)
                            except:
                                pass
                            
                            print(f"[STREAMING_RAY] Page {completed_page_no}: {len(page_chunks)} chunks processed", flush=True)
                        
                    except Exception as page_err:
                        logger.error(f"[STREAMING_RAY] Error processing page {completed_page_no}: {page_err}")
                        if completed_page_no in active_futures:
                            del active_futures[completed_page_no]
                
                # Limit concurrent tasks to prevent memory buildup
                while len(active_futures) >= max_concurrent:
                    # Wait for at least one to complete
                    ready, _ = ray.wait(list(active_futures.values()), num_returns=1, timeout=1.0)
                    
                    for ready_future in ready:
                        completed_page_no = None
                        for pno, fut in active_futures.items():
                            if fut == ready_future:
                                completed_page_no = pno
                                break
                        
                        if completed_page_no is None:
                            continue
                        
                        try:
                            page_chunks = ray.get(ready_future)
                            for chunk_props in page_chunks:
                                chunks_written += 1
                                if write_callback:
                                    try:
                                        write_callback(chunk_props)
                                    except Exception as write_err:
                                        logger.error(f"[STREAMING_RAY] Write callback failed: {write_err}")
                                yield chunk_props
                            
                            del active_futures[completed_page_no]
                            print(f"[STREAMING_RAY] Page {completed_page_no}: {len(page_chunks)} chunks processed", flush=True)
                        except Exception as page_err:
                            logger.error(f"[STREAMING_RAY] Error processing page {completed_page_no}: {page_err}")
                            import traceback
                            error_trace = traceback.format_exc()
                            logger.error(error_trace)
                            print(f"[STREAMING_RAY] ⚠️  Error processing page {completed_page_no}: {page_err}", flush=True)
                            print(f"[STREAMING_RAY]   Traceback: {error_trace[:500]}...", flush=True)
                            if completed_page_no in active_futures:
                                del active_futures[completed_page_no]
                
                # Cleanup page from memory
                del page
                gc.collect()
            
            # Process remaining futures
            print(f"[STREAMING_RAY] Waiting for {len(active_futures)} remaining pages...", flush=True)
            while active_futures:
                ready, remaining = ray.wait(list(active_futures.values()), num_returns=1, timeout=1.0)
                
                for ready_future in ready:
                    completed_page_no = None
                    for pno, fut in active_futures.items():
                        if fut == ready_future:
                            completed_page_no = pno
                            break
                    
                    if completed_page_no is None:
                        continue
                    
                    try:
                        page_chunks = ray.get(ready_future)
                        for chunk_props in page_chunks:
                            chunks_written += 1
                            if write_callback:
                                try:
                                    write_callback(chunk_props)
                                except Exception as write_err:
                                    logger.error(f"[STREAMING_RAY] Write callback failed: {write_err}")
                            yield chunk_props
                        
                        del active_futures[completed_page_no]
                        print(f"[STREAMING_RAY] Page {completed_page_no}: {len(page_chunks)} chunks processed", flush=True)
                    except Exception as page_err:
                        logger.error(f"[STREAMING_RAY] Error processing page {completed_page_no}: {page_err}")
                        if completed_page_no in active_futures:
                            del active_futures[completed_page_no]
    
    except Exception as e:
        logger.error(f"[STREAMING_RAY] Streaming Ray chunking failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
        raise
    
    print(f"[STREAMING_RAY] Complete: {page_counter} pages, {chunks_written} chunks", flush=True)











