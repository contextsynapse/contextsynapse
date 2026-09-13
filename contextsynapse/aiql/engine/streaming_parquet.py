"""
Streaming Parquet Reader for Chunking

Reads normalized document data from Parquet format in streaming batches.
This enables chunking with constant memory usage - no OOM possible.
"""

import pyarrow.parquet as pq
from pathlib import Path
from typing import Iterator, Dict, Any, Generator, Optional
import json
import gc
import logging

logger = logging.getLogger(__name__)


class StreamingParquetReader:
    """
    Streams normalized document data from Parquet file.
    
    Reads in small batches to maintain constant memory usage.
    """
    
    def __init__(
        self,
        parquet_path: Path,
        batch_size: int = 256,
        columns: Optional[list] = None
    ):
        """
        Initialize Parquet reader.
        
        Args:
            parquet_path: Path to Parquet file
            batch_size: Number of rows to read per batch
            columns: Optional list of column names to read (None = all columns)
        """
        self.parquet_path = Path(parquet_path)
        self.batch_size = batch_size
        self.columns = columns or ["text", "page", "type", "meta", "block_id"]
        
        if not self.parquet_path.exists():
            raise FileNotFoundError(f"Parquet file not found: {parquet_path}")
        
        self.parquet_file = pq.ParquetFile(self.parquet_path)
        self._metadata = None
    
    def get_document_metadata(self) -> Dict[str, Any]:
        """
        Get document metadata from metadata JSON file or Parquet file.
        
        Returns:
            Dictionary with document metadata including document_type
        """
        if self._metadata is not None:
            return self._metadata
        
        # Try to load from metadata JSON file first (faster)
        metadata_path = self.parquet_path.with_suffix('.metadata.json')
        if metadata_path.exists():
            try:
                with open(metadata_path, 'r', encoding='utf-8') as f:
                    metadata = json.load(f)
                    self._metadata = metadata
                    return metadata
            except Exception as e:
                logger.warning(f"Failed to load metadata from {metadata_path}: {e}")
        
        # Fallback: extract from Parquet file or path
        metadata = {
            "document_id": self._extract_doc_id_from_path(),
            "version": "v1",  # Default
            "format": "parquet",
            "document_type": "unknown",
        }
        
        # Try to get doc_id and document_type from first row
        try:
            first_batch = next(self.iter_batches(batch_size=1))
            if len(first_batch) > 0:
                doc_id = first_batch["doc_id"][0].as_py()
                metadata["document_id"] = doc_id
                
                # Get document_type if available in schema
                if "document_type" in first_batch.schema.names:
                    document_type = first_batch["document_type"][0].as_py()
                    metadata["document_type"] = document_type
                    metadata["format"] = document_type  # Also set format for compatibility
        except:
            pass
        
        self._metadata = metadata
        return metadata
    
    def _extract_doc_id_from_path(self) -> str:
        """Extract document ID from file path."""
        # Try to extract from path structure
        # e.g., .../documents/{doc_id}/normalized/normalized_v1.parquet
        parts = self.parquet_path.parts
        if "documents" in parts:
            idx = parts.index("documents")
            if idx + 1 < len(parts):
                return parts[idx + 1]
        return "unknown"
    
    def iter_batches(
        self,
        batch_size: Optional[int] = None,
        columns: Optional[list] = None
    ) -> Generator[Any, None, None]:
        """
        Iterate over Parquet file in batches.
        
        Args:
            batch_size: Override default batch size
            columns: Override default columns
            
        Yields:
            PyArrow RecordBatch with columns: text, page, type, meta, block_id
        """
        batch_size = batch_size or self.batch_size
        columns = columns or self.columns
        
        for batch in self.parquet_file.iter_batches(
            batch_size=batch_size,
            columns=columns
        ):
            yield batch
            gc.collect()
    
    def stream_pages(self) -> Generator[Dict[str, Any], None, None]:
        """
        Stream pages one at a time from Parquet.
        
        Groups blocks by page and yields page dictionaries similar to
        normalized JSON format for compatibility.
        
        Yields:
            Page dictionary with page_no, flat_text, normalized_blocks
        """
        current_page = None
        current_page_no = None
        blocks = []
        flat_text_parts = []
        
        # Check if hierarchical schema is available
        has_hierarchical = False
        try:
            schema = self.parquet_file.schema_arrow
            has_hierarchical = "hierarchy_level" in schema.names
        except:
            pass
        
        for batch in self.iter_batches():
            # Convert batch to Python
            pages = batch["page"].to_pylist()
            texts = batch["text"].to_pylist()
            types = batch["type"].to_pylist()
            metas = batch["meta"].to_pylist()
            block_ids = batch["block_id"].to_pylist()
            
            # Get document_type if available
            document_types = []
            if "document_type" in batch.schema.names:
                document_types = batch["document_type"].to_pylist()
            
            # Get hierarchical fields if available
            hierarchy_levels = []
            parent_block_ids = []
            section_ids = []
            is_section_headers = []
            section_start_pages = []
            heading_levels = []
            sibling_orders = []
            
            if has_hierarchical:
                try:
                    hierarchy_levels = batch["hierarchy_level"].to_pylist()
                    parent_block_ids = batch["parent_block_id"].to_pylist()
                    section_ids = batch["section_id"].to_pylist()
                    is_section_headers = batch["is_section_header"].to_pylist()
                    section_start_pages = batch["section_start_page"].to_pylist()
                    heading_levels = batch["heading_level"].to_pylist()
                    sibling_orders = batch["sibling_order"].to_pylist()
                except:
                    has_hierarchical = False
            
            for idx, (page_no, text, block_type, meta_str, block_id) in enumerate(zip(
                pages, texts, types, metas, block_ids
            )):
                # Get document_type for this block
                document_type = document_types[idx] if document_types and idx < len(document_types) else None
                # Parse metadata
                try:
                    meta = json.loads(meta_str) if meta_str else {}
                except:
                    meta = {}
                
                # If new page, yield previous page
                if current_page_no is not None and page_no != current_page_no:
                    yield self._build_page_dict(
                        current_page_no,
                        flat_text_parts,
                        blocks
                    )
                    blocks = []
                    flat_text_parts = []
                
                # Accumulate current page data
                current_page_no = page_no
                flat_text_parts.append(text)
                
                # Build block
                block = {
                    "id": meta.get("block_id", f"b{page_no}_{block_id}"),
                    "type": block_type,
                    "text": text,
                }
                if document_type:
                    block["document_type"] = document_type
                if "level" in meta:
                    block["level"] = meta["level"]
                if "font_size" in meta:
                    block["font_size"] = meta["font_size"]
                if "font_name" in meta:
                    block["font_name"] = meta["font_name"]
                
                # Add hierarchical fields if available
                if has_hierarchical and idx < len(hierarchy_levels):
                    block["hierarchy_level"] = hierarchy_levels[idx]
                    block["parent_block_id"] = parent_block_ids[idx] if parent_block_ids[idx] else None
                    block["section_id"] = section_ids[idx] if section_ids[idx] else None
                    block["is_section_header"] = is_section_headers[idx] if idx < len(is_section_headers) else False
                    block["section_start_page"] = section_start_pages[idx] if idx < len(section_start_pages) else page_no
                    block["heading_level"] = heading_levels[idx] if idx < len(heading_levels) else 0
                    block["sibling_order"] = sibling_orders[idx] if idx < len(sibling_orders) else 0
                
                blocks.append(block)
                
                # Force cleanup
                del text, block_type, meta_str, block_id, meta
                gc.collect()
        
        # Yield last page
        if current_page_no is not None:
            yield self._build_page_dict(
                current_page_no,
                flat_text_parts,
                blocks
            )
    
    def _build_page_dict(
        self,
        page_no: int,
        flat_text_parts: list,
        blocks: list
    ) -> Dict[str, Any]:
        """Build page dictionary from accumulated data."""
        flat_text = "\n".join(flat_text_parts)
        
        return {
            "page_no": page_no,
            "flat_text": flat_text,
            "normalized_blocks": blocks,
            "raw_blocks": [],  # Not stored in Parquet
            "markdown": "",  # Not stored in Parquet
            "hierarchy": {},  # Not stored in Parquet
        }
    
    def stream_blocks(self) -> Generator[Dict[str, Any], None, None]:
        """
        Stream individual blocks (for fine-grained chunking).
        
        Yields:
            Block dictionaries with text, page_no, type, metadata, hierarchical info
        """
        # Check if hierarchical schema is available
        has_hierarchical = False
        try:
            schema = self.parquet_file.schema_arrow
            has_hierarchical = "hierarchy_level" in schema.names
        except:
            pass
        
        for batch in self.iter_batches():
            pages = batch["page"].to_pylist()
            texts = batch["text"].to_pylist()
            types = batch["type"].to_pylist()
            metas = batch["meta"].to_pylist()
            block_ids = batch["block_id"].to_pylist()
            
            # Get hierarchical fields if available
            hierarchy_levels = []
            parent_block_ids = []
            section_ids = []
            is_section_headers = []
            section_start_pages = []
            heading_levels = []
            sibling_orders = []
            
            if has_hierarchical:
                try:
                    hierarchy_levels = batch["hierarchy_level"].to_pylist()
                    parent_block_ids = batch["parent_block_id"].to_pylist()
                    section_ids = batch["section_id"].to_pylist()
                    is_section_headers = batch["is_section_header"].to_pylist()
                    section_start_pages = batch["section_start_page"].to_pylist()
                    heading_levels = batch["heading_level"].to_pylist()
                    sibling_orders = batch["sibling_order"].to_pylist()
                except:
                    has_hierarchical = False
            
            # Get document_type if available
            document_types = []
            if "document_type" in batch.schema.names:
                document_types = batch["document_type"].to_pylist()
            
            for idx, (page_no, text, block_type, meta_str, block_id) in enumerate(zip(
                pages, texts, types, metas, block_ids
            )):
                try:
                    meta = json.loads(meta_str) if meta_str else {}
                except:
                    meta = {}
                
                # Get document_type for this block
                document_type = document_types[idx] if document_types and idx < len(document_types) else None
                
                block_dict = {
                    "page_no": page_no,
                    "text": text,
                    "type": block_type,
                    "block_id": block_id,
                    "metadata": meta,
                }
                if document_type:
                    block_dict["document_type"] = document_type
                
                # Add hierarchical fields if available
                if has_hierarchical and idx < len(hierarchy_levels):
                    block_dict["hierarchy_level"] = hierarchy_levels[idx]
                    block_dict["parent_block_id"] = parent_block_ids[idx] if parent_block_ids[idx] else None
                    block_dict["section_id"] = section_ids[idx] if section_ids[idx] else None
                    block_dict["is_section_header"] = is_section_headers[idx] if idx < len(is_section_headers) else False
                    block_dict["section_start_page"] = section_start_pages[idx] if idx < len(section_start_pages) else page_no
                    block_dict["heading_level"] = heading_levels[idx] if idx < len(heading_levels) else 0
                    block_dict["sibling_order"] = sibling_orders[idx] if idx < len(sibling_orders) else 0
                
                yield block_dict
                
                # Cleanup
                del text, meta_str, meta
                gc.collect()
    
    def stream_sections(self) -> Generator[Dict[str, Any], None, None]:
        """
        Stream sections with their paragraphs (for hierarchical chunking).
        
        Uses hierarchical fields from Parquet to group blocks into sections.
        
        Yields:
            Section dictionaries with title, paragraphs, metadata
        """
        current_section = None
        section_paragraphs = []
        
        for block in self.stream_blocks():
            # Check if this is a section header
            if block.get("is_section_header", False):
                # Emit previous section
                if current_section:
                    yield {
                        "section_id": current_section["section_id"],
                        "title": current_section["text"],
                        "paragraphs": section_paragraphs,
                        "start_page": current_section["section_start_page"],
                        "end_page": section_paragraphs[-1]["page_no"] if section_paragraphs else current_section["page_no"],
                        "heading_level": current_section.get("heading_level", 1),
                    }
                
                # Start new section
                current_section = block
                section_paragraphs = []
            elif block.get("section_id") and current_section and block["section_id"] == current_section.get("section_id"):
                # Add paragraph to current section
                if block.get("hierarchy_level") == 2:  # Paragraph level
                    section_paragraphs.append(block)
        
        # Emit final section
        if current_section:
            yield {
                "section_id": current_section["section_id"],
                "title": current_section["text"],
                "paragraphs": section_paragraphs,
                "start_page": current_section["section_start_page"],
                "end_page": section_paragraphs[-1]["page_no"] if section_paragraphs else current_section["page_no"],
                "heading_level": current_section.get("heading_level", 1),
            }


def stream_parquet_pages(parquet_path: Path) -> Iterator[Dict[str, Any]]:
    """
    Convenience function to stream pages from Parquet.
    
    Args:
        parquet_path: Path to Parquet file
        
    Yields:
        Page dictionaries
    """
    reader = StreamingParquetReader(parquet_path)
    for page in reader.stream_pages():
        yield page


def estimate_tokens(text: str, chars_per_token: float = 4.0) -> int:
    """
    Estimate token count from character count.
    
    Args:
        text: Text to estimate
        chars_per_token: Average characters per token (default 4.0 for English)
        
    Returns:
        Estimated token count
    """
    return int(len(text) / chars_per_token)


def chunk_incrementally_from_parquet(
    parquet_path: Path,
    max_tokens: int = 1000,
    overlap_tokens: int = 200,
    batch_size: int = 256
) -> Generator[Dict[str, Any], None, None]:
    """
    Chunk text incrementally from Parquet file.
    
    This is the KEY function - it streams Parquet batches, accumulates text
    until token limit, then emits chunks. Constant memory usage.
    
    Args:
        parquet_path: Path to Parquet file
        max_tokens: Maximum tokens per chunk
        overlap_tokens: Overlap tokens between chunks
        batch_size: Parquet batch size
        
    Yields:
        Chunk dictionaries with text, token_count, page_no, start_offset, end_offset
    """
    reader = StreamingParquetReader(parquet_path, batch_size=batch_size)
    
    buffer = []
    token_count = 0
    start_offset = 0
    current_offset = 0
    current_page = None
    
    for batch in reader.iter_batches():
        texts = batch["text"].to_pylist()
        pages = batch["page"].to_pylist()
        
        for text, page in zip(texts, pages):
            if not text or not text.strip():
                continue
            
            # Track page changes
            if current_page is None:
                current_page = page
            elif page != current_page:
                # Page changed - flush buffer if needed
                if buffer:
                    chunk_text = ' '.join(buffer)
                    yield {
                        'text': chunk_text,
                        'token_count': token_count,
                        'page_no': current_page,
                        'start_offset': start_offset,
                        'end_offset': current_offset
                    }
                    buffer = []
                    token_count = 0
                    start_offset = 0
                    current_offset = 0
                current_page = page
            
            item_tokens = estimate_tokens(text)
            
            # If single item exceeds max, yield it as its own chunk
            if item_tokens > max_tokens:
                # Flush current buffer first
                if buffer:
                    chunk_text = ' '.join(buffer)
                    yield {
                        'text': chunk_text,
                        'token_count': token_count,
                        'page_no': current_page,
                        'start_offset': start_offset,
                        'end_offset': current_offset
                    }
                    buffer = []
                    token_count = 0
                    start_offset = current_offset + len(text)
                
                # Yield oversized item as its own chunk
                yield {
                    'text': text,
                    'token_count': item_tokens,
                    'page_no': current_page,
                    'start_offset': current_offset,
                    'end_offset': current_offset + len(text)
                }
                current_offset += len(text) + 1
                start_offset = current_offset
                continue
            
            # Check if adding this item would exceed max
            if token_count + item_tokens > max_tokens:
                # Flush current buffer
                if buffer:
                    chunk_text = ' '.join(buffer)
                    yield {
                        'text': chunk_text,
                        'token_count': token_count,
                        'page_no': current_page,
                        'start_offset': start_offset,
                        'end_offset': current_offset
                    }
                    
                    # Start new buffer with overlap
                    overlap_chars = int(overlap_tokens * 4.0)
                    if len(chunk_text) > overlap_chars:
                        overlap_text = chunk_text[-overlap_chars:]
                        buffer = [overlap_text]
                        token_count = estimate_tokens(overlap_text)
                        start_offset = current_offset - overlap_chars
                    else:
                        buffer = []
                        token_count = 0
                        start_offset = current_offset
            
            buffer.append(text)
            token_count += item_tokens
            current_offset += len(text) + 1
            
            # Force cleanup
            del text
            gc.collect()
        
        # Cleanup batch
        del batch, texts, pages
        gc.collect()
    
    # Flush remaining buffer
    if buffer:
        chunk_text = ' '.join(buffer)
        yield {
            'text': chunk_text,
            'token_count': token_count,
            'page_no': current_page,
            'start_offset': start_offset,
            'end_offset': current_offset
        }




