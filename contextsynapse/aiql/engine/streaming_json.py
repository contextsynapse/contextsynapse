"""
Streaming JSON parser for normalized documents.

This module provides streaming JSON parsing to avoid loading entire documents
into memory. Critical for processing large documents (340+ pages) without OOM.
"""

import ijson
import json
from pathlib import Path
from typing import Iterator, Dict, Any, Optional, Generator
import gc


class StreamingNormalizedDocumentParser:
    """
    Streams normalized JSON documents page-by-page or section-by-section.
    
    Never loads more than one logical unit (page/section) into memory at a time.
    """
    
    def __init__(self, file_path: Path, strategy: str = "page"):
        """
        Initialize streaming parser.
        
        Args:
            file_path: Path to normalized JSON file
            strategy: "page" (stream pages) or "section" (stream sections)
        """
        self.file_path = Path(file_path)
        self.strategy = strategy
        self._document_metadata = None
        self._file_handle = None
    
    def __enter__(self):
        """Context manager entry."""
        self._file_handle = open(self.file_path, 'rb')
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        if self._file_handle:
            self._file_handle.close()
            self._file_handle = None
        gc.collect()
    
    def get_document_metadata(self) -> Dict[str, Any]:
        """
        Get document metadata (document_id, version, etc.) without loading full doc.
        
        Returns:
            Dictionary with document_id, version, and other top-level metadata
        """
        if self._document_metadata is not None:
            return self._document_metadata
        
        # Stream only top-level metadata fields
        metadata = {}
        with open(self.file_path, 'rb') as f:
            parser = ijson.parse(f)
            for prefix, event, value in parser:
                # Capture top-level metadata
                if prefix == 'document_id':
                    metadata['document_id'] = value
                elif prefix == 'version':
                    metadata['version'] = value
                elif prefix == 'aiql_schema_version':
                    metadata['aiql_schema_version'] = value
                elif prefix == 'source':
                    metadata['source'] = value
                elif prefix == 'metadata':
                    metadata['metadata'] = value
                
                # Stop after we have essential metadata
                if len(metadata) >= 5:
                    break
        
        self._document_metadata = metadata
        return metadata
    
    def get_document_pointer(self) -> Dict[str, Any]:
        """Alias for get_document_metadata for compatibility."""
        return self.get_document_metadata()
    
    def stream_pages(self) -> Generator[Dict[str, Any], None, None]:
        """
        Stream pages one at a time from normalized JSON.
        
        Yields:
            Page dictionary with page_no, flat_text, and other page fields
        """
        if not self._file_handle:
            raise RuntimeError("Must use as context manager: with StreamingNormalizedDocumentParser(...) as parser:")
        
        # Log file info
        file_size_mb = self.file_path.stat().st_size / (1024 * 1024)
        print(f"[STREAMING] Opening normalized file: {self.file_path}", flush=True)
        print(f"[STREAMING] File size: {file_size_mb:.2f} MB", flush=True)
        
        # Reset file handle to beginning
        self._file_handle.seek(0)
        
        # Stream pages array items
        pages = ijson.items(self._file_handle, 'content.pages.item')
        
        page_count = 0
        total_text_chars = 0
        
        for page in pages:
            page_count += 1
            page_no = page.get('page_no', page_count)
            page_text = page.get('flat_text', '')
            text_length = len(page_text) if isinstance(page_text, str) else 0
            total_text_chars += text_length
            
            # Log page details
            print(f"[STREAMING] Page {page_no}: {text_length:,} chars", flush=True)
            if text_length > 0:
                # Show preview of page content (first 100 chars)
                preview = page_text[:100].replace('\n', ' ').strip()
                print(f"[STREAMING]   Preview: {preview}...", flush=True)
            
            # Log page structure
            has_blocks = bool(page.get('normalized_blocks'))
            has_tables = bool(page.get('tables'))
            has_images = bool(page.get('images'))
            if has_blocks or has_tables or has_images:
                print(f"[STREAMING]   Structure: blocks={has_blocks}, tables={has_tables}, images={has_images}", flush=True)
            
            yield page
            
            # Force cleanup of page object
            del page
            gc.collect()
        
        print(f"[STREAMING] Completed streaming: {page_count} pages, {total_text_chars:,} total chars", flush=True)
    
    def stream_sections(self) -> Generator[Dict[str, Any], None, None]:
        """
        Stream sections from normalized_blocks (for hierarchical chunking).
        
        Yields:
            Section/block dictionaries from normalized_blocks
        """
        if not self._file_handle:
            raise RuntimeError("Must use as context manager")
        
        self._file_handle.seek(0)
        
        # Stream normalized_blocks from each page
        pages = ijson.items(self._file_handle, 'content.pages.item')
        
        for page in pages:
            page_no = page.get('page_no', 0)
            normalized_blocks = page.get('normalized_blocks', [])
            
            for block in normalized_blocks:
                # Add page context to block
                block_with_context = {
                    **block,
                    'page_no': page_no,
                    'page_context': {
                        'page_no': page_no,
                        'flat_text': page.get('flat_text', '')
                    }
                }
                yield block_with_context
                del block_with_context
            
            # Cleanup page
            del page
            gc.collect()
    
    def stream_structural_units(self) -> Generator[Dict[str, Any], None, None]:
        """
        Stream structural units (tables, figures, code blocks) for structural chunking.
        
        Yields:
            Structural unit dictionaries (tables, images, etc.)
        """
        if not self._file_handle:
            raise RuntimeError("Must use as context manager")
        
        self._file_handle.seek(0)
        
        # Stream tables
        try:
            self._file_handle.seek(0)
            tables = ijson.items(self._file_handle, 'content.tables.item')
            for table in tables:
                yield {'type': 'table', 'data': table}
                del table
                gc.collect()
        except Exception:
            pass  # No tables in document
        
        # Stream images
        try:
            self._file_handle.seek(0)
            images = ijson.items(self._file_handle, 'content.images.item')
            for image in images:
                yield {'type': 'image', 'data': image}
                del image
                gc.collect()
        except Exception:
            pass  # No images in document
        
        # Stream code blocks from normalized_blocks
        self._file_handle.seek(0)
        pages = ijson.items(self._file_handle, 'content.pages.item')
        for page in pages:
            page_no = page.get('page_no', 0)
            normalized_blocks = page.get('normalized_blocks', [])
            
            for block in normalized_blocks:
                block_type = block.get('type', '')
                if block_type in {'code', 'code_block', 'pre'}:
                    yield {
                        'type': 'code_block',
                        'page_no': page_no,
                        'data': block
                    }
                del block
            
            del page
            gc.collect()


def stream_normalized_document_pages(file_path: Path) -> Iterator[Dict[str, Any]]:
    """
    Convenience function to stream pages from normalized JSON.
    
    Args:
        file_path: Path to normalized JSON file
        
    Yields:
        Page dictionaries
    """
    with StreamingNormalizedDocumentParser(file_path, strategy="page") as parser:
        for page in parser.stream_pages():
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


def chunk_incrementally(
    text_items: Iterator[str],
    max_tokens: int = 1000,
    overlap_tokens: int = 200,
    token_estimator=None
) -> Generator[Dict[str, Any], None, None]:
    """
    Chunk text incrementally while walking through items.
    
    Never materializes full text - chunks as it goes.
    
    Args:
        text_items: Iterator of text strings (paragraphs, sentences, etc.)
        max_tokens: Maximum tokens per chunk
        overlap_tokens: Overlap tokens between chunks
        token_estimator: Function to estimate tokens (default: estimate_tokens)
        
    Yields:
        Chunk dictionaries with text, token_count, start_offset, end_offset
    """
    if token_estimator is None:
        token_estimator = estimate_tokens
    
    buffer = []
    token_count = 0
    start_offset = 0
    current_offset = 0
    
    for text_item in text_items:
        if not text_item or not text_item.strip():
            continue
        
        item_tokens = token_estimator(text_item)
        
        # If single item exceeds max, yield it as its own chunk
        if item_tokens > max_tokens:
            # Flush current buffer first
            if buffer:
                chunk_text = ' '.join(buffer)
                yield {
                    'text': chunk_text,
                    'token_count': token_count,
                    'start_offset': start_offset,
                    'end_offset': current_offset
                }
                buffer = []
                token_count = 0
                start_offset = current_offset + len(text_item)
            
            # Yield oversized item as its own chunk
            yield {
                'text': text_item,
                'token_count': item_tokens,
                'start_offset': current_offset,
                'end_offset': current_offset + len(text_item)
            }
            current_offset += len(text_item) + 1  # +1 for separator
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
                    'start_offset': start_offset,
                    'end_offset': current_offset
                }
                
                # Start new buffer with overlap
                # For overlap, keep last N tokens worth of text
                overlap_chars = int(overlap_tokens * 4.0)  # ~4 chars per token
                if len(chunk_text) > overlap_chars:
                    overlap_text = chunk_text[-overlap_chars:]
                    buffer = [overlap_text]
                    token_count = token_estimator(overlap_text)
                    start_offset = current_offset - overlap_chars
                else:
                    buffer = []
                    token_count = 0
                    start_offset = current_offset
        
        buffer.append(text_item)
        token_count += item_tokens
        current_offset += len(text_item) + 1  # +1 for separator
    
    # Flush remaining buffer
    if buffer:
        chunk_text = ' '.join(buffer)
        yield {
            'text': chunk_text,
            'token_count': token_count,
            'start_offset': start_offset,
            'end_offset': current_offset
        }











