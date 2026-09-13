"""
Parquet Writer for Incremental Normalized Data Storage

Writes normalized document data incrementally to Parquet format.
This eliminates JSON storage and enables streaming chunking with bounded memory.
"""

import pyarrow as pa
import pyarrow.parquet as pq
from pathlib import Path
from typing import Dict, List, Any, Optional
import json
import logging
import gc

logger = logging.getLogger(__name__)


# Parquet schema for normalized document blocks
PARQUET_SCHEMA = pa.schema([
    ("doc_id", pa.string()),
    ("document_type", pa.string()),      # Document type: pdf, doc, excel, txt, web, scan, image, sound
    ("page", pa.int32()),
    ("block_id", pa.int32()),
    ("type", pa.string()),                # Block type: paragraph, heading, table, etc.
    ("text", pa.string()),
    ("meta", pa.string()),  # JSON string for additional metadata
])

# Enhanced Parquet schema with hierarchical relationships for hierarchical chunking
PARQUET_SCHEMA_HIERARCHICAL = pa.schema([
    ("doc_id", pa.string()),
    ("document_type", pa.string()),      # Document type: pdf, doc, excel, txt, web, scan, image, sound
    ("page", pa.int32()),
    ("block_id", pa.int32()),
    ("type", pa.string()),                # Block type: paragraph, heading, table, etc.
    ("text", pa.string()),
    ("meta", pa.string()),  # JSON string for additional metadata
    # Hierarchical fields for easier chunking
    ("hierarchy_level", pa.int32()),      # 0=document, 1=section, 2=paragraph, 3=fact
    ("parent_block_id", pa.string()),     # ID of parent block (null for document level)
    ("section_id", pa.string()),          # Section identifier this block belongs to
    ("is_section_header", pa.bool_()),     # True if this block starts a new section
    ("section_start_page", pa.int32()),   # Page where section starts
    ("heading_level", pa.int32()),        # Heading level (1-6, null for non-headings)
    ("sibling_order", pa.int32()),        # Order within parent (for traversal)
])


class IncrementalParquetWriter:
    """
    Writes normalized document data incrementally to Parquet.
    
    Writes rows in batches to avoid memory accumulation.
    """
    
    def __init__(
        self,
        output_path: Path,
        schema: pa.Schema = PARQUET_SCHEMA,
        compression: str = "zstd",
        batch_size: int = 500
    ):
        """
        Initialize Parquet writer.
        
        Args:
            output_path: Path to output Parquet file
            schema: PyArrow schema for the data
            compression: Compression codec (zstd, snappy, gzip, etc.)
            batch_size: Number of rows to accumulate before writing
        """
        self.output_path = Path(output_path)
        self.output_path.parent.mkdir(parents=True, exist_ok=True)
        self.schema = schema
        self.compression = compression
        self.batch_size = batch_size
        
        self.writer = None
        self.buffer = []
        self.total_rows = 0
        
    def __enter__(self):
        """Context manager entry."""
        self.writer = pq.ParquetWriter(
            self.output_path,
            schema=self.schema,
            compression=self.compression
        )
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - flush remaining data and close."""
        if self.buffer:
            self._write_batch()
        if self.writer:
            self.writer.close()
        gc.collect()
    
    def _write_batch(self):
        """Write accumulated batch to Parquet file."""
        if not self.buffer:
            return
        
        try:
            table = pa.Table.from_pylist(self.buffer, schema=self.schema)
            self.writer.write_table(table)
            self.total_rows += len(self.buffer)
            self.buffer.clear()
            gc.collect()
        except Exception as e:
            logger.error(f"Error writing Parquet batch: {e}")
            raise
    
    def write_block(
        self,
        doc_id: str,
        document_type: str,
        page: int,
        block_id: int,
        block_type: str,
        text: str,
        metadata: Optional[Dict[str, Any]] = None,
        hierarchical_info: Optional[Dict[str, Any]] = None
    ):
        """
        Write a single block to Parquet.
        
        Args:
            doc_id: Document identifier
            document_type: Document type (pdf, doc, excel, txt, web, scan, image, sound)
            page: Page number (1-indexed)
            block_id: Block identifier (unique within page)
            block_type: Type of block (paragraph, heading, etc.)
            text: Block text content
            metadata: Optional additional metadata (will be JSON-serialized)
            hierarchical_info: Optional hierarchical relationship info for hierarchical chunking
                {
                    "hierarchy_level": 0-3,  # 0=doc, 1=section, 2=paragraph, 3=fact
                    "parent_block_id": "b1_1",  # Parent block ID
                    "section_id": "section_1",  # Section identifier
                    "is_section_header": False,  # True if starts new section
                    "section_start_page": 1,  # Page where section starts
                    "heading_level": 1,  # Heading level (1-6)
                    "sibling_order": 0  # Order within parent
                }
        """
        meta_str = json.dumps(metadata) if metadata else "{}"
        
        row = {
            "doc_id": doc_id,
            "document_type": document_type or "unknown",  # Default to "unknown" if not provided
            "page": page,
            "block_id": block_id,
            "type": block_type,
            "text": text,
            "meta": meta_str,
        }
        
        # Add hierarchical fields if using hierarchical schema
        if hierarchical_info and self.schema == PARQUET_SCHEMA_HIERARCHICAL:
            row.update({
                "hierarchy_level": hierarchical_info.get("hierarchy_level", 2),  # Default: paragraph
                "parent_block_id": hierarchical_info.get("parent_block_id", ""),
                "section_id": hierarchical_info.get("section_id", ""),
                "is_section_header": hierarchical_info.get("is_section_header", False),
                "section_start_page": hierarchical_info.get("section_start_page", page),
                "heading_level": hierarchical_info.get("heading_level", 0),
                "sibling_order": hierarchical_info.get("sibling_order", 0),
            })
        
        self.buffer.append(row)
        
        # Write batch when buffer is full
        if len(self.buffer) >= self.batch_size:
            self._write_batch()
    
    def write_page_blocks(
        self,
        doc_id: str,
        document_type: str,
        page_no: int,
        page_data: Dict[str, Any],
        hierarchy_index: Optional[Dict[str, Dict[str, Any]]] = None,
        page_tables: Optional[List[Dict[str, Any]]] = None
    ):
        """
        Write all blocks from a page to Parquet with hierarchical relationships.
        
        Args:
            doc_id: Document identifier
            document_type: Document type (pdf, doc, excel, txt, web, scan, image, sound)
            page_no: Page number
            page_data: Page dictionary with normalized_blocks or flat_text
            hierarchy_index: Optional hierarchy index from HierarchyBuilder
                Format: {
                    "block_id": {
                        "level": 1,
                        "parent": "parent_block_id",
                        "children": ["child1", "child2"]
                    }
                }
            page_tables: Optional list of tables from normalized document for this page
        """
        # Extract flat text
        flat_text = page_data.get("flat_text", "")
        
        # Write flat text as a single block if no normalized_blocks
        normalized_blocks = page_data.get("normalized_blocks", [])
        
        if not normalized_blocks and flat_text:
            # Write entire page as single block
            self.write_block(
                doc_id=doc_id,
                document_type=document_type,
                page=page_no,
                block_id=0,
                block_type="page",
                text=flat_text,
                metadata={
                    "page_no": page_no,
                    "has_blocks": False,
                },
                hierarchical_info={
                    "hierarchy_level": 0,  # Document level
                    "parent_block_id": "",
                    "section_id": "",
                    "is_section_header": False,
                    "section_start_page": page_no,
                    "heading_level": 0,
                    "sibling_order": 0,
                }
            )
        else:
            # Track section state for hierarchical relationships
            current_section_id = None
            current_section_start_page = page_no
            section_paragraph_count = 0
            
            # Write each normalized block with hierarchical info
            for idx, block in enumerate(normalized_blocks):
                block_text = block.get("text", block.get("content", ""))
                if not block_text:
                    continue
                
                block_id = block.get("id", f"b{page_no}_{idx+1}")
                block_type = block.get("type", "paragraph")
                
                # Get hierarchy info from hierarchy_index if available
                hierarchy_info = hierarchy_index.get(block_id, {}) if hierarchy_index else {}
                parent_id = hierarchy_info.get("parent")
                level = hierarchy_info.get("level") or block.get("level")
                children = hierarchy_info.get("children", [])
                
                # Determine hierarchy level
                if block_type == "heading":
                    hierarchy_level = 1  # Section level
                    is_section_header = True
                    # Start new section
                    current_section_id = f"section_{page_no}_{idx}"
                    current_section_start_page = page_no
                    section_paragraph_count = 0
                elif block_type == "paragraph":
                    hierarchy_level = 2  # Paragraph level
                    is_section_header = False
                    section_paragraph_count += 1
                elif block_type == "table":
                    hierarchy_level = 2  # Table level (same as paragraph)
                    is_section_header = False
                    section_paragraph_count += 1
                else:
                    hierarchy_level = 2  # Default to paragraph level
                    is_section_header = False
                
                # Build hierarchical info
                hierarchical_info = {
                    "hierarchy_level": hierarchy_level,
                    "parent_block_id": parent_id or "",
                    "section_id": current_section_id or "",
                    "is_section_header": is_section_header,
                    "section_start_page": current_section_start_page,
                    "heading_level": level if block_type == "heading" else 0,
                    "sibling_order": section_paragraph_count if not is_section_header else 0,
                }
                
                # Build block metadata
                block_meta = {
                    "page_no": page_no,
                    "block_id": block_id,
                    "block_type": block_type,
                    "level": level,
                    "font_size": block.get("font_size"),
                    "font_name": block.get("font_name"),
                    "children": children,  # Store children IDs in metadata
                }
                
                # Add table-specific metadata if this is a table block
                if block_type == "table":
                    # Try to find matching table from page_tables
                    table_match = None
                    if page_tables:
                        # Try to match by ID or position
                        block_table_id = block.get("id", "")
                        for table in page_tables:
                            if table.get("id") == block_table_id or table.get("id", "").endswith(block_table_id):
                                table_match = table
                                break
                        # If no match by ID, try by index
                        if not table_match and idx < len(page_tables):
                            table_match = page_tables[idx]
                    
                    # Enhance block with table data if found
                    if table_match:
                        block = {**block, **table_match}
                    
                    block_meta.update(self._extract_table_metadata(block, page_no))
                
                self.write_block(
                    doc_id=doc_id,
                    document_type=document_type,
                    page=page_no,
                    block_id=idx,
                    block_type=block_type,
                    text=block_text,
                    metadata=block_meta,
                    hierarchical_info=hierarchical_info
                )
        
        # Also write tables that might not be in normalized_blocks
        # (e.g., tables extracted separately)
        if page_tables:
            table_block_index = len(normalized_blocks) if normalized_blocks else 0
            for table_idx, table in enumerate(page_tables):
                # Check if this table was already written as a block
                table_id = table.get("id", f"tbl_{page_no}_{table_idx}")
                already_written = any(
                    block.get("id") == table_id or block.get("type") == "table"
                    for block in (normalized_blocks or [])
                )
                
                if not already_written:
                    # Write table as separate block
                    table_data = table.get("data", [])
                    headers = table_data[0] if table_data else []
                    rows = table_data[1:] if len(table_data) > 1 else []
                    
                    # Generate markdown for table
                    table_markdown = self._table_to_markdown(headers, rows) if headers or rows else table.get("markdown", "")
                    
                    # Extract table metadata
                    table_meta = self._extract_table_metadata(table, page_no)
                    table_meta.update({
                        "page_no": page_no,
                        "block_id": table_id,
                        "block_type": "table",
                    })
                    
                    # Determine hierarchy
                    current_section_id = None  # Could track from previous blocks
                    hierarchical_info = {
                        "hierarchy_level": 2,  # Table level
                        "parent_block_id": "",
                        "section_id": current_section_id or "",
                        "is_section_header": False,
                        "section_start_page": page_no,
                        "heading_level": 0,
                        "sibling_order": table_block_index + table_idx,
                    }
                    
                    self.write_block(
                        doc_id=doc_id,
                        document_type=document_type,
                        page=page_no,
                        block_id=table_block_index + table_idx,
                        block_type="table",
                        text=table_markdown,
                        metadata=table_meta,
                        hierarchical_info=hierarchical_info
                    )
        
        # Flush if buffer is getting large
        if len(self.buffer) >= self.batch_size:
            self._write_batch()
    
    def flush(self):
        """Manually flush buffer to disk."""
        self._write_batch()
    
    def _extract_table_metadata(self, block: Dict[str, Any], page_no: int) -> Dict[str, Any]:
        """
        Extract table-specific metadata from a table block.
        
        Args:
            block: Table block dictionary
            page_no: Page number
            
        Returns:
            Dictionary with table metadata
        """
        table_meta = {}
        
        # Try to get table data from block
        table_data = block.get("data", [])
        if not table_data:
            # Try alternative field names
            table_data = block.get("rows", [])
        
        if table_data:
            headers = table_data[0] if table_data else []
            rows = table_data[1:] if len(table_data) > 1 else []
            
            # Calculate table size
            row_count = len(rows)
            column_count = len(headers) if headers else (len(rows[0]) if rows else 0)
            
            # Determine table size category
            if row_count < 20 and column_count < 5:
                table_size = "small"
            elif row_count < 100:
                table_size = "medium"
            else:
                table_size = "large"
            
            table_meta = {
                "table_id": block.get("id", f"tbl_{page_no}_{block.get('table_index', 0)}"),
                "table_index": block.get("table_index", 0),
                "row_count": row_count,
                "column_count": column_count,
                "has_headers": len(headers) > 0,
                "table_size": table_size,
            }
            
            # Store headers as JSON string if available
            if headers:
                table_meta["table_headers"] = json.dumps(headers)
        
        # Add caption if available
        if "caption" in block:
            table_meta["caption"] = block["caption"]
        
        return table_meta
    
    def _table_to_markdown(self, headers: List[str], rows: List[List[str]]) -> str:
        """
        Convert table data to markdown format.
        
        Args:
            headers: List of header strings
            rows: List of row data (list of lists)
            
        Returns:
            Markdown table string
        """
        if not headers and not rows:
            return ""
        
        if not headers and rows:
            headers = [f"Column {i+1}" for i in range(len(rows[0]))]
        
        lines = []
        # Header
        lines.append("| " + " | ".join(str(h) for h in headers) + " |")
        # Separator
        lines.append("| " + " | ".join("---" for _ in headers) + " |")
        # Rows
        for row in rows:
            # Ensure row has same number of columns as headers
            row_padded = row + [""] * (len(headers) - len(row))
            lines.append("| " + " | ".join(str(cell) for cell in row_padded[:len(headers)]) + " |")
        
        return "\n".join(lines)


def save_normalized_to_parquet(
    normalized_doc: Dict[str, Any],
    output_path: Path,
    version: str = "v1",
    compression: str = "zstd",
    batch_size: int = 500,
    use_hierarchical_schema: bool = True
) -> Path:
    """
    Save normalized document to Parquet format with hierarchical relationships.
    
    This function converts a normalized JSON document to Parquet by writing
    all pages and blocks incrementally. Also saves document metadata to a
    separate JSON file for quick access.
    
    Args:
        normalized_doc: Normalized document dictionary
        output_path: Path to output Parquet file
        version: Version string
        compression: Compression codec
        batch_size: Batch size for writing
        use_hierarchical_schema: Whether to use hierarchical schema with parent-child relationships
        
    Returns:
        Path to created Parquet file
    """
    doc_id = normalized_doc.get("document_id", "unknown")
    pages = normalized_doc.get("content", {}).get("pages", [])
    
    # Get document type from source metadata
    source = normalized_doc.get("source", {})
    document_type_raw = source.get("format", "unknown").lower()
    
    # Normalize document type values to canonical types
    document_type_map = {
        "pdf": "pdf",
        "docx": "doc",
        "doc": "doc",
        "xlsx": "excel",
        "xls": "excel",
        "csv": "excel",
        "txt": "txt",
        "text": "txt",
        "html": "web",
        "htm": "web",
        "png": "image",
        "jpg": "image",
        "jpeg": "image",
        "gif": "image",
        "bmp": "image",
        "tiff": "image",
        "mp3": "sound",
        "wav": "sound",
        "mp4": "video",
        "avi": "video",
        "mov": "video",
    }
    document_type = document_type_map.get(document_type_raw, document_type_raw)
    
    # If still unknown, try to infer from file path in source
    if document_type == "unknown" and "file_path" in source:
        file_path = source["file_path"]
        ext = Path(file_path).suffix.lower()
        document_type = document_type_map.get(ext.lstrip("."), "unknown")
    
    # Get hierarchy index if available (for hierarchical relationships)
    hierarchy_index = normalized_doc.get("hierarchy_index", {})
    
    # Choose schema based on flag
    schema = PARQUET_SCHEMA_HIERARCHICAL if use_hierarchical_schema else PARQUET_SCHEMA
    
    # Get tables from normalized document (for table block enhancement)
    tables = normalized_doc.get("content", {}).get("tables", [])
    # Create table lookup by page and index
    tables_by_page = {}
    for table in tables:
        table_page = table.get("from_page", 0)
        if table_page not in tables_by_page:
            tables_by_page[table_page] = []
        tables_by_page[table_page].append(table)
    
    # Write Parquet file incrementally
    with IncrementalParquetWriter(
        output_path=output_path,
        schema=schema,
        compression=compression,
        batch_size=batch_size
    ) as writer:
        for page_data in pages:
            page_no = page_data.get("page_no", 0)
            # Get tables for this page
            page_tables = tables_by_page.get(page_no, [])
            writer.write_page_blocks(
                doc_id, 
                document_type, 
                page_no, 
                page_data, 
                hierarchy_index,
                page_tables=page_tables if page_tables else None
            )
    
    # Save document metadata to separate JSON file for quick access
    metadata_path = output_path.with_suffix('.metadata.json')
    metadata = {
        "document_id": normalized_doc.get("document_id"),
        "version": normalized_doc.get("version", version),
        "aiql_schema_version": normalized_doc.get("aiql_schema_version"),
        "source": normalized_doc.get("source", {}),
        "metadata": normalized_doc.get("metadata", {}),
        "extraction_scope": normalized_doc.get("extraction_scope", {}),
        "status": normalized_doc.get("status", {}),
        "page_count": len(pages),
        "parquet_file": output_path.name,
    }
    
    with open(metadata_path, 'w', encoding='utf-8') as f:
        json.dump(metadata, f, indent=2, ensure_ascii=False)
    
    logger.info(f"Saved normalized document to Parquet: {output_path} ({len(pages)} pages)")
    logger.info(f"Saved metadata to: {metadata_path}")
    return output_path




