"""
PDF Extractor

Extracts content from PDF files.
"""

from pathlib import Path
from typing import Dict, Any, List
import logging

from ..registry import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)

# Suppress noisy pdfminer warnings (e.g. "CropBox missing from /Page, defaulting to MediaBox")
logging.getLogger("pdfminer").setLevel(logging.ERROR)

# Optional import for layout detection
try:
    from ..layout_detector import LayoutDetector
    LAYOUT_DETECTOR_AVAILABLE = True
except ImportError:
    LAYOUT_DETECTOR_AVAILABLE = False
    LayoutDetector = None

# Import column reorganizer for post-processing
try:
    from ..post_processors.column_reorganizer import ColumnReorganizer
    COLUMN_REORGANIZER_AVAILABLE = True
except ImportError:
    COLUMN_REORGANIZER_AVAILABLE = False
    ColumnReorganizer = None


class PdfExtractor(BaseExtractor):
    """
    PDF extractor using PyPDF2 or pdfplumber.
    """
    
    def __init__(self, config):
        """Initialize PDF extractor."""
        super().__init__(config)
        self.extractor_version = "pdf_v1"
        self.progress_callback = None  # Optional: (message, progress) -> None
        
        # Initialize layout detector if Layout Parser is enabled
        self.use_layout_parser = getattr(config, 'use_layout_parser', False)
        self.layout_detection_method = getattr(config, 'layout_detection_method', 'auto')
        
        if self.use_layout_parser or self.layout_detection_method == 'ml':
            try:
                from .pdf_extractor_layoutparser import PdfExtractorLayoutParser
                self.layout_parser_extractor = PdfExtractorLayoutParser(config)
                logger.info("Layout Parser extractor initialized")
            except Exception as e:
                logger.warning(f"Could not initialize Layout Parser extractor: {e}")
                self.layout_parser_extractor = None
        else:
            self.layout_parser_extractor = None
        
        # Initialize layout detector for adaptive extraction
        if LAYOUT_DETECTOR_AVAILABLE and LayoutDetector:
            if self.layout_detection_method == 'auto':
                self.layout_detector = LayoutDetector(use_ml=self.use_layout_parser)
            else:
                self.layout_detector = LayoutDetector(use_ml=(self.layout_detection_method == 'ml'))
        else:
            self.layout_detector = None
        
        # Initialize column reorganizer for post-processing
        if COLUMN_REORGANIZER_AVAILABLE and ColumnReorganizer:
            self.column_reorganizer = ColumnReorganizer()
        else:
            self.column_reorganizer = None
    
    def supports_format(self, file_path: str) -> bool:
        """Check if file is a PDF."""
        return Path(file_path).suffix.lower() == ".pdf"
    
    def extract(self, file_path: str) -> ExtractionResult:
        """
        Extract content from PDF with adaptive layout handling.
        
        Args:
            file_path: Path to PDF file
            
        Returns:
            ExtractionResult
        """
        result = ExtractionResult(extractor_version=self.extractor_version)
        
        try:
            # If Layout Parser is explicitly enabled, try it first
            if self.use_layout_parser and self.layout_parser_extractor:
                logger.info("Attempting Layout Parser extraction...")
                layout_result = self.layout_parser_extractor.extract(file_path)
                # If Layout Parser succeeded (has pages), use it
                if layout_result.pages:
                    logger.info("Layout Parser extraction successful")
                    return layout_result
                # Otherwise fall back to standard extraction
                logger.warning("Layout Parser extraction failed, falling back to standard extraction")
            
            # Adaptive extraction: detect layout first, then choose method
            if self.layout_detection_method == 'auto' and self.layout_detector and LAYOUT_DETECTOR_AVAILABLE:
                logger.info("Detecting layout complexity...")
                layout_info = self.layout_detector.detect_layout_complexity(file_path)
                result.metadata["layout_detection"] = layout_info
                
                # Use Layout Parser if complex layout detected and available
                if (layout_info.get("has_complex_layout", False) and 
                    self.layout_parser_extractor):
                    logger.info(f"Complex layout detected ({layout_info.get('layout_type')}), using Layout Parser")
                    layout_result = self.layout_parser_extractor.extract(file_path)
                    if layout_result.pages:
                        return layout_result
                    logger.warning("Layout Parser extraction failed, falling back to standard extraction")
            
            # Standard extraction (pdfplumber or PyPDF2)
            try:
                import pdfplumber
                result = self._extract_with_pdfplumber(file_path, result)
            except ImportError:
                logger.warning("pdfplumber not available, falling back to PyPDF2")
                result = self._extract_with_pypdf2(file_path, result)
        
        except Exception as e:
            logger.error(f"PDF extraction failed: {e}")
            result.errors.append(str(e))
        
        return result
    
    def _extract_with_pdfplumber(self, file_path: str, result: ExtractionResult) -> ExtractionResult:
        """Extract using pdfplumber."""
        import pdfplumber

        with pdfplumber.open(file_path) as pdf:
            # Metadata
            metadata = pdf.metadata or {}
            result.metadata = {
                "title": metadata.get("Title", ""),
                "author": metadata.get("Author", ""),
                "subject": metadata.get("Subject", ""),
                "creator": metadata.get("Creator", ""),
                "producer": metadata.get("Producer", ""),
                "page_count": len(pdf.pages),
            }
            
            # Extract pages
            requested_pages = self._get_requested_pages()

            total_pages = len(pdf.pages)
            for i, page in enumerate(pdf.pages, 1):
                if requested_pages and i not in requested_pages:
                    continue

                if self.progress_callback:
                    self.progress_callback(f"Extracting page {i} of {total_pages}", i / total_pages)

                # Extract text with layout-aware reading order
                text = self._extract_text_with_layout_awareness(page, i)
                
                # Post-process: Fix interleaved column text if needed
                if self.column_reorganizer:
                    text = self.column_reorganizer.reorganize(text)
                
                # Extract words with font size information
                words_with_font = page.extract_words(extra_attrs=["size", "fontname"])
                
                # Build blocks with font size information
                blocks = self._build_blocks_with_font_size(words_with_font, text)
                
                # Extract tables with better settings
                tables = page.extract_tables(table_settings={
                    "vertical_strategy": "lines",
                    "horizontal_strategy": "lines",
                    "intersection_tolerance": 15,
                    "min_words_vertical": 1,
                    "min_words_horizontal": 1,
                })
                
                result.pages.append({
                    "page_no": i,
                    "text": text,
                    "content": text,
                    "blocks": blocks,
                })
                
                # Process and validate tables
                for table_idx, table in enumerate(tables or [], 1):
                    if self._is_valid_table(table):
                        # Clean and normalize table data
                        cleaned_table = self._clean_table_data(table)
                        if cleaned_table:
                            result.tables.append({
                                "id": f"tbl_{i}_{table_idx}",
                                "page_number": i,
                                "data": cleaned_table,
                                "headers": cleaned_table[0] if cleaned_table else [],
                                "rows": cleaned_table[1:] if len(cleaned_table) > 1 else [],
                            })
                
                # Extract images using PyMuPDF (fitz) - non-LLM method
                # Note: We'll extract images after processing all pages to avoid opening the PDF multiple times
                pass
        
        if self.progress_callback:
            self.progress_callback("Extracting images...", None)
        try:
            import fitz  # PyMuPDF
            doc = fitz.open(file_path)
            for i, page in enumerate(doc, 1):
                if requested_pages and i not in requested_pages:
                    continue
                page_fitz = doc[i - 1]  # 0-indexed
                image_list = page_fitz.get_images()
                
                for img_idx, img in enumerate(image_list, 1):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        
                        # Save image to temporary location
                        from pathlib import Path
                        import tempfile
                        temp_dir = Path(tempfile.gettempdir()) / "aiql_extraction_images"
                        temp_dir.mkdir(exist_ok=True)
                        image_path = temp_dir / f"img_{i}_{img_idx}.{image_ext}"
                        with open(image_path, 'wb') as img_file:
                            img_file.write(image_bytes)
                        
                        result.images.append({
                            "id": f"img_{i}_{img_idx}",
                            "page_number": i,
                            "path": str(image_path),
                            "format": image_ext.upper(),
                            "size_bytes": len(image_bytes),
                        })
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_idx} from page {i}: {e}")
            doc.close()
        except ImportError:
            logger.warning("PyMuPDF (fitz) not available for image extraction. Install with: pip install PyMuPDF")
        except Exception as e:
            logger.warning(f"Image extraction failed: {e}")
        
        return result
    
    def _extract_with_pypdf2(self, file_path: str, result: ExtractionResult) -> ExtractionResult:
        """Extract using PyPDF2 (fallback)."""
        try:
            import PyPDF2
        except ImportError:
            raise ImportError("Neither pdfplumber nor PyPDF2 is available. Install one: pip install pdfplumber or pip install PyPDF2")
        
        with open(file_path, 'rb') as f:
            pdf = PyPDF2.PdfReader(f)
            
            # Metadata
            metadata = pdf.metadata or {}
            result.metadata = {
                "title": metadata.get("/Title", ""),
                "author": metadata.get("/Author", ""),
                "subject": metadata.get("/Subject", ""),
                "page_count": len(pdf.pages),
            }
            
            # Extract pages
            requested_pages = self._get_requested_pages()
            
            for i, page in enumerate(pdf.pages, 1):
                if requested_pages and i not in requested_pages:
                    continue
                
                text = page.extract_text() or ""
                
                result.pages.append({
                    "page_no": i,
                    "text": text,
                    "content": text,
                })
        
        return result
    
    def _extract_text_with_layout_awareness(self, page, page_num: int) -> str:
        """
        Extract text with layout awareness for multi-column documents.
        
        Uses heuristic detection to identify multi-column layouts and
        adjusts reading order accordingly.
        """
        try:
            import pdfplumber
        except ImportError:
            return page.extract_text() or ""
        
        # Get words with positions for layout analysis
        words = page.extract_words()
        
        if not words or len(words) < 10:
            # Too few words, use standard extraction
            return page.extract_text() or ""
        
        # Analyze layout: check if multi-column by finding column boundaries
        page_width = page.width
        x_starts = sorted([w['x0'] for w in words])
        
        if len(x_starts) < 20:
            # Too few words, use standard extraction
            return page.extract_text() or ""
        
        # Find column boundaries by looking for gaps in x-coordinates
        # Group x-coordinates into clusters (columns)
        x_gaps = []
        for i in range(len(x_starts) - 1):
            gap = x_starts[i + 1] - x_starts[i]
            x_gaps.append((x_starts[i], gap))
        
        # Find significant gaps (potential column separators)
        avg_gap = sum(g[1] for g in x_gaps) / len(x_gaps) if x_gaps else 0
        large_gaps = [g for g in x_gaps if g[1] > avg_gap * 3 and g[1] > 50]  # Gap > 3x average and > 50px
        
        # Alternative: Check if text is distributed in distinct x-regions
        # Divide page into left and right halves
        mid_x = page_width / 2
        left_words = [w for w in words if w['x0'] < mid_x]
        right_words = [w for w in words if w['x0'] >= mid_x]
        
        left_ratio = len(left_words) / len(words)
        right_ratio = len(right_words) / len(words)
        
        # Detect multi-column if:
        # 1. Significant gaps found (column separators), OR
        # 2. Text is roughly balanced between left and right halves (30-70% split)
        is_multi_column = (
            len(large_gaps) >= 2 or  # Multiple column separators found
            (0.25 <= left_ratio <= 0.75 and 0.25 <= right_ratio <= 0.75 and
             abs(left_ratio - right_ratio) < 0.4)  # Roughly balanced columns
        )
        
        if is_multi_column:
            logger.info(f"Multi-column layout detected on page {page_num} (left: {left_ratio:.1%}, right: {right_ratio:.1%}, gaps: {len(large_gaps)}), using column-aware extraction")
            # Extract text column by column
            return self._extract_text_column_by_column(page, words)
        else:
            # Single column or simple layout, use standard extraction
            logger.debug(f"Single-column layout on page {page_num} (left: {left_ratio:.1%}, right: {right_ratio:.1%})")
            return page.extract_text() or ""
    
    def _extract_text_column_by_column(self, page, words: List[Dict]) -> str:
        """
        Extract text column by column for multi-column layouts.
        
        Reads left column completely (top to bottom), then right column.
        """
        if not words:
            return ""
        
        page_width = page.width
        mid_x = page_width / 2
        
        # Separate words into left and right columns
        left_column_words = [w for w in words if w['x0'] < mid_x]
        right_column_words = [w for w in words if w['x0'] >= mid_x]
        
        # Sort each column by y-coordinate (top to bottom), then x (left to right)
        left_column_words.sort(key=lambda w: (w['top'], w['x0']))
        right_column_words.sort(key=lambda w: (w['top'], w['x0']))
        
        # Group words into lines within each column
        def group_into_lines(column_words, y_tolerance=5):
            """Group words into lines based on y-coordinate."""
            lines = []
            current_line = []
            current_y = None
            
            for word in column_words:
                word_y = word['top']
                
                if current_y is None or abs(word_y - current_y) <= y_tolerance:
                    current_line.append(word)
                    current_y = word_y if current_y is None else (current_y + word_y) / 2
                else:
                    if current_line:
                        current_line.sort(key=lambda w: w['x0'])
                        lines.append(' '.join([w['text'] for w in current_line]))
                    current_line = [word]
                    current_y = word_y
            
            if current_line:
                current_line.sort(key=lambda w: w['x0'])
                lines.append(' '.join([w['text'] for w in current_line]))
            
            return lines
        
        left_lines = group_into_lines(left_column_words)
        right_lines = group_into_lines(right_column_words)
        
        # Read left column completely (top to bottom), then right column
        result_lines = []
        result_lines.extend(left_lines)
        result_lines.extend(right_lines)
        
        return '\n'.join(result_lines)
    
    def _build_blocks_with_font_size(self, words_with_font: List[Dict], text: str) -> List[Dict[str, Any]]:
        """
        Build blocks with font size information from words.
        
        Groups words into blocks based on font size and position.
        """
        if not words_with_font:
            # Fallback: create simple blocks from text
            lines = text.split('\n')
            blocks = []
            for i, line in enumerate(lines):
                if line.strip():
                    blocks.append({
                        "id": f"b_{i+1}",
                        "type": "paragraph",
                        "text": line.strip(),
                        "font_size": None,
                    })
            return blocks
        
        # Group words into blocks by font size and position
        blocks = []
        current_block = None
        y_tolerance = 5  # Words on same line if y-difference < 5
        
        # Sort words by position (top to bottom, left to right)
        sorted_words = sorted(words_with_font, key=lambda w: (w.get('top', 0), w.get('x0', 0)))
        
        for word in sorted_words:
            word_text = word.get('text', '').strip()
            if not word_text:
                continue
            
            font_size = word.get('size')
            word_y = word.get('top', 0)
            
            # Check if we should start a new block
            if (current_block is None or 
                font_size != current_block.get('font_size') or
                abs(word_y - current_block.get('last_y', 0)) > y_tolerance):
                
                # Save previous block if exists
                if current_block and current_block.get('text'):
                    block_dict = {
                        "id": f"b_{len(blocks)+1}",
                        "type": current_block.get('type', 'paragraph'),
                        "text": current_block['text'].strip(),
                        "font_size": current_block.get('font_size'),
                        "font_name": current_block.get('font_name'),
                    }
                    if current_block.get('level') is not None:
                        block_dict["level"] = current_block.get('level')
                    blocks.append(block_dict)
                
                # Start new block
                # Determine block type and level based on font size (heuristic)
                block_type = "paragraph"
                level = None
                if font_size:
                    if font_size >= 16:
                        block_type = "heading"
                        level = 1
                    elif font_size >= 14:
                        block_type = "heading"
                        level = 2
                    elif font_size >= 12:
                        block_type = "heading"
                        level = 3
                
                current_block = {
                    "text": word_text,
                    "font_size": font_size,
                    "font_name": word.get('fontname'),
                    "type": block_type,
                    "level": level,
                    "last_y": word_y,
                }
            else:
                # Continue current block
                current_block['text'] += ' ' + word_text
                current_block['last_y'] = word_y
        
        # Add last block
        if current_block and current_block.get('text'):
            block_dict = {
                "id": f"b_{len(blocks)+1}",
                "type": current_block.get('type', 'paragraph'),
                "text": current_block['text'].strip(),
                "font_size": current_block.get('font_size'),
                "font_name": current_block.get('font_name'),
            }
            if current_block.get('level') is not None:
                block_dict["level"] = current_block.get('level')
            blocks.append(block_dict)
        
        return blocks
    
    def _is_valid_table(self, table: List[List[Any]]) -> bool:
        """
        Validate if a table is actually a valid table.
        
        Filters out false positives like:
        - Empty tables
        - Single cell tables
        - Tables with too few rows/columns
        - Tables with mostly empty cells
        - Tables that are just fragments or single characters
        """
        if not table or len(table) == 0:
            return False
        
        # Minimum requirements: at least 2 rows and 2 columns
        if len(table) < 2:
            return False
        
        # Check if all rows have at least 2 columns
        num_columns = len(table[0]) if table[0] else 0
        if num_columns < 2:
            return False
        
        # Check that all rows have at least 2 columns
        for row in table:
            if len(row) < 2:
                return False
        
        # Check for meaningful content (not all empty or single characters)
        total_cells = sum(len(row) for row in table)
        non_empty_cells = sum(
            1 for row in table 
            for cell in row 
            if cell and str(cell).strip() and len(str(cell).strip()) > 1
        )
        
        # At least 40% of cells should have meaningful content (increased from 30%)
        if total_cells == 0 or (non_empty_cells / total_cells) < 0.4:
            return False
        
        # Check if table rows are just single characters (like "M,M,M")
        single_char_rows = 0
        for row in table:
            if len(row) >= 2:
                # Check if all cells in row are single characters or empty
                all_single_chars = all(
                    not cell or (isinstance(cell, str) and len(cell.strip()) <= 1)
                    for cell in row
                )
                if all_single_chars:
                    single_char_rows += 1
        
        # If more than half the rows are just single characters, it's likely a false positive
        if single_char_rows > len(table) / 2:
            return False
        
        # For 2-row tables, require both rows to have substantial content
        # (to filter out fragments and false positives)
        if len(table) == 2:
            substantial_cells_row1 = sum(
                1 for cell in table[0] 
                if cell and str(cell).strip() and len(str(cell).strip()) > 2
            )
            substantial_cells_row2 = sum(
                1 for cell in table[1] 
                if cell and str(cell).strip() and len(str(cell).strip()) > 2
            )
            # Both rows should have at least 2 substantial cells
            if substantial_cells_row1 < 2 or substantial_cells_row2 < 2:
                return False
        else:
            # For tables with 3+ rows, require at least 2 rows with substantial content
            substantial_rows = sum(
                1 for row in table
                if sum(1 for cell in row if cell and str(cell).strip() and len(str(cell).strip()) > 3) >= 2
            )
            if substantial_rows < 2:
                return False
        
        return True
    
    def _clean_table_data(self, table: List[List[Any]]) -> List[List[str]]:
        """
        Clean and normalize table data.
        
        - Removes empty rows
        - Trims whitespace from cells
        - Converts all cells to strings
        - Removes rows that are completely empty
        """
        if not table:
            return []
        
        cleaned = []
        for row in table:
            if not row:
                continue
            
            # Clean cells
            cleaned_row = []
            for cell in row:
                if cell is None:
                    cleaned_row.append("")
                else:
                    cell_str = str(cell).strip()
                    cleaned_row.append(cell_str)
            
            # Only add row if it has at least one non-empty cell
            if any(cell for cell in cleaned_row):
                cleaned.append(cleaned_row)
        
        # Ensure all rows have the same number of columns
        if cleaned:
            max_cols = max(len(row) for row in cleaned)
            for row in cleaned:
                while len(row) < max_cols:
                    row.append("")
        
        return cleaned if len(cleaned) >= 2 else []
    
    def _get_requested_pages(self) -> List[int]:
        """Get list of requested page numbers."""
        if self.config.pages_mode.value == "full":
            return []
        elif self.config.pages_mode.value == "range":
            if self.config.pages_range:
                start, end = self.config.pages_range
                return list(range(start, end + 1))
        elif self.config.pages_mode.value == "list":
            return self.config.pages_list or []
        return []
