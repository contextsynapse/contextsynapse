"""
Ray-Based Parallel Normalization

Uses Ray for distributed parallel processing of page normalization.
Consistent with chunking implementation using Ray.
"""

import logging
from typing import Dict, List, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# Check if Ray is available
RAY_AVAILABLE = False
try:
    import ray
    RAY_AVAILABLE = True
except ImportError:
    RAY_AVAILABLE = False
    logger.warning("Ray not available. Install with: pip install ray")


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
    @ray.remote(num_cpus=1, memory=512 * 1024 * 1024)  # 1 CPU, 512MB per worker
    def remote_build_page(
        page_data: Dict[str, Any],
        page_no: int
    ) -> Dict[str, Any]:
        """
        Remote function to build normalized page structure.
        
        This is called by Ray workers - processes one page at a time.
        No dependencies between pages - perfect for parallelization!
        
        Args:
            page_data: Raw page data from extraction
            page_no: Page number (1-indexed)
            
        Returns:
            Normalized page dictionary
        """
        try:
            from .normalizer import Normalizer
            
            # Create minimal normalizer instance
            # Note: file_manager not needed for page building
            normalizer = Normalizer(file_manager=None)
            return normalizer._build_page(page_data, page_no)
        except Exception as e:
            logger.error(f"Error normalizing page {page_no}: {e}")
            # Return empty page structure on error
            return {
                "page_no": page_no,
                "flat_text": "",
                "raw_blocks": [],
                "normalized_blocks": [],
                "markdown": "",
                "hierarchy": {},
            }
    
    @ray.remote(num_cpus=1, memory=256 * 1024 * 1024)  # 1 CPU, 256MB per worker
    def remote_build_table(
        table_data: Dict[str, Any],
        table_index: int
    ) -> Dict[str, Any]:
        """Remote function to build normalized table structure."""
        try:
            from .normalizer import Normalizer
            normalizer = Normalizer(file_manager=None)
            return normalizer._build_table(table_data, table_index)
        except Exception as e:
            logger.error(f"Error normalizing table {table_index}: {e}")
            return {}
    
    @ray.remote(num_cpus=1, memory=256 * 1024 * 1024)  # 1 CPU, 256MB per worker
    def remote_build_image(
        image_data: Dict[str, Any],
        image_index: int
    ) -> Dict[str, Any]:
        """Remote function to build normalized image structure."""
        try:
            from .normalizer import Normalizer
            normalizer = Normalizer(file_manager=None)
            return normalizer._build_image(image_data, image_index)
        except Exception as e:
            logger.error(f"Error normalizing image {image_index}: {e}")
            return {}


def normalize_pages_parallel_ray(
    extraction_result: "ExtractionResult",
    max_concurrent: Optional[int] = None,
    use_streaming: bool = True
) -> List[Dict[str, Any]]:
    """
    Normalize pages in parallel using Ray.
    
    Args:
        extraction_result: Extraction result with pages
        max_concurrent: Maximum concurrent Ray tasks (None = auto)
        use_streaming: If True, process results as they complete (better memory)
        
    Returns:
        List of normalized pages (in order)
    """
    if not _init_ray_if_needed():
        logger.warning("Ray not available, falling back to sequential processing")
        from .normalizer import Normalizer
        normalizer = Normalizer(file_manager=None)
        return [
            normalizer._build_page(page_data, i+1)
            for i, page_data in enumerate(extraction_result.pages)
        ]
    
    # Submit all pages to Ray
    futures = [
        remote_build_page.remote(page_data, i+1)
        for i, page_data in enumerate(extraction_result.pages)
    ]
    
    pages = []
    
    if use_streaming and max_concurrent:
        # Process results as they complete (streaming, better memory)
        remaining = list(futures)
        while remaining:
            ready, remaining = ray.wait(
                remaining,
                num_returns=min(max_concurrent, len(remaining)),
                timeout=1.0
            )
            for future in ready:
                try:
                    page = ray.get(future)
                    pages.append(page)
                except Exception as e:
                    logger.error(f"Error getting page result: {e}")
                    # Add empty page to maintain order
                    pages.append({
                        "page_no": len(pages) + 1,
                        "flat_text": "",
                        "raw_blocks": [],
                        "normalized_blocks": [],
                        "markdown": "",
                        "hierarchy": {},
                    })
    else:
        # Get all results at once
        pages = ray.get(futures)
    
    # Sort by page number to ensure order (Ray may complete out of order)
    pages.sort(key=lambda p: p.get("page_no", 0))
    
    return pages


def normalize_content_parallel_ray(
    extraction_result: "ExtractionResult",
    max_concurrent: Optional[int] = None,
    use_streaming: bool = True
) -> Dict[str, Any]:
    """
    Normalize all content (pages, tables, images) in parallel using Ray.
    
    Args:
        extraction_result: Extraction result
        max_concurrent: Maximum concurrent Ray tasks
        use_streaming: Process results as they complete
        
    Returns:
        Content dictionary with normalized pages, tables, images
    """
    if not _init_ray_if_needed():
        logger.warning("Ray not available, falling back to sequential processing")
        from .normalizer import Normalizer
        normalizer = Normalizer(file_manager=None)
        return normalizer._build_content(extraction_result, None)
    
    # Normalize pages in parallel
    pages = normalize_pages_parallel_ray(
        extraction_result,
        max_concurrent=max_concurrent,
        use_streaming=use_streaming
    )
    
    # Normalize tables in parallel (if any)
    if extraction_result.tables:
        table_futures = [
            remote_build_table.remote(table_data, i+1)
            for i, table_data in enumerate(extraction_result.tables)
        ]
        tables = ray.get(table_futures) if table_futures else []
    else:
        tables = []
    
    # Normalize images in parallel (if any)
    if extraction_result.images:
        image_futures = [
            remote_build_image.remote(image_data, i+1)
            for i, image_data in enumerate(extraction_result.images)
        ]
        images = ray.get(image_futures) if image_futures else []
    else:
        images = []
    
    return {
        "pages": pages,
        "tables": tables,
        "images": images,
        "web": [],  # Can be parallelized similarly
        "audio": [],
        "video": [],
        "extensions": {},
    }




