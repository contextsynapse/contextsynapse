"""
LLM-Based Document Extraction Module

This module provides intelligent document extraction using LLMs to:
- Extract text with proper reading order (handles multi-column layouts)
- Extract tables with structure preservation
- Extract images with metadata
- Extract named entities (NER)
- Generate semantic chunks
- Provide structured JSON output

This is more expensive than heuristic approaches but provides superior quality.
"""

import json
import logging
import base64
from pathlib import Path
from typing import Dict, List, Any, Optional, Union
from dataclasses import dataclass, asdict

logger = logging.getLogger(__name__)


@dataclass
class ExtractedTable:
    """Represents an extracted table with structure."""
    table_index: int
    page_number: int
    headers: List[str]
    rows: List[List[str]]
    title: Optional[str] = None
    caption: Optional[str] = None
    spatial_info: Optional[Dict[str, float]] = None  # x, y, width, height


@dataclass
class ExtractedImage:
    """Represents an extracted image with metadata."""
    image_index: int
    page_number: int
    filename: str
    format: str  # PNG, JPEG, etc.
    width: int
    height: int
    file_size: int
    spatial_info: Optional[Dict[str, float]] = None  # x, y, width, height
    description: Optional[str] = None  # LLM-generated description


@dataclass
class ExtractedEntity:
    """Represents a named entity extracted from the document."""
    entity_type: str  # PERSON, ORGANIZATION, LOCATION, etc.
    entity_name: str
    canonical_name: Optional[str] = None
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    confidence: Optional[float] = None
    context: Optional[str] = None  # Surrounding text


@dataclass
class DocumentChunk:
    """Represents a semantic chunk of the document."""
    chunk_index: int
    content: str
    chunk_type: str  # "paragraph", "section", "table_reference", etc.
    start_char: Optional[int] = None
    end_char: Optional[int] = None
    page_number: Optional[int] = None
    entities: Optional[List[ExtractedEntity]] = None
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class LLMExtractionResult:
    """Complete extraction result from LLM."""
    text_content: str
    tables: List[ExtractedTable]
    images: List[ExtractedImage]
    entities: List[ExtractedEntity]
    chunks: List[DocumentChunk]
    metadata: Dict[str, Any]
    reading_order_preserved: bool = True


class LLMDocumentExtractor:
    """LLM-based document extractor for high-quality extraction."""
    
    def __init__(self, llm_provider=None, llm_model: str = "gpt-3.5-turbo", temperature: float = 0.0):
        """
        Initialize LLM extractor.
        
        Args:
            llm_provider: LLM provider instance (AIContextDBUniversalLLM)
            llm_model: Model name (e.g., "gpt-4", "gpt-4-turbo", "claude-3-opus")
            temperature: Temperature for LLM generation (0.0 for deterministic)
        """
        self.llm_provider = llm_provider
        self.llm_model = llm_model
        self.temperature = temperature
        
        if llm_provider is None:
            try:
                from ..llm.universal_llm import AIContextDBUniversalLLM
                self.llm_provider = AIContextDBUniversalLLM()
            except Exception as e:
                logger.error(f"Failed to initialize LLM provider: {e}")
                raise
    
    def extract_from_pdf(self, pdf_path: Union[str, Path], 
                        max_pages: Optional[int] = None,
                        extract_tables: bool = True,
                        extract_images: bool = True,
                        extract_entities: bool = True,
                        generate_chunks: bool = True,
                        chunk_strategy: str = "semantic") -> LLMExtractionResult:
        """
        Extract structured information from PDF using LLM.
        
        Args:
            pdf_path: Path to PDF file
            max_pages: Maximum number of pages to process (None for all)
            extract_tables: Whether to extract tables
            extract_images: Whether to extract images
            extract_entities: Whether to extract named entities
            generate_chunks: Whether to generate semantic chunks
            chunk_strategy: Chunking strategy ("semantic", "paragraph", "sentence")
        
        Returns:
            LLMExtractionResult with all extracted information
        """
        pdf_path = Path(pdf_path)
        if not pdf_path.exists():
            raise FileNotFoundError(f"PDF file not found: {pdf_path}")
        
        logger.info(f"Starting LLM-based extraction from: {pdf_path}")
        logger.info(f"Settings: tables={extract_tables}, images={extract_images}, "
                   f"entities={extract_entities}, chunks={generate_chunks}")
        
        # Step 1: Extract raw content from PDF (text, tables, images with coordinates)
        raw_content = self._extract_raw_content(pdf_path, max_pages)
        
        # Step 2: Use LLM to process and structure the content
        structured_result = self._llm_process_content(
            raw_content,
            extract_tables=extract_tables,
            extract_images=extract_images,
            extract_entities=extract_entities,
            generate_chunks=generate_chunks,
            chunk_strategy=chunk_strategy
        )
        
        return structured_result
    
    def _extract_raw_content(self, pdf_path: Path, max_pages: Optional[int]) -> Dict[str, Any]:
        """Extract raw content from PDF (text, tables, images) for LLM processing."""
        try:
            import fitz  # PyMuPDF
            import pdfplumber
            from PIL import Image as PILImage
            import io
        except ImportError as e:
            logger.error(f"Required libraries not installed: {e}")
            logger.error("Install: pip install pymupdf pdfplumber pillow")
            raise
        
        raw_content = {
            'pages': [],
            'metadata': {
                'file_path': str(pdf_path),
                'file_name': pdf_path.name,
                'total_pages': 0
            }
        }
        
        try:
            # Open PDF with PyMuPDF
            doc = fitz.open(pdf_path)
            total_pages = len(doc)
            pages_to_process = min(max_pages or total_pages, total_pages)
            raw_content['metadata']['total_pages'] = total_pages
            raw_content['metadata']['pages_processed'] = pages_to_process
            
            # Open with pdfplumber for table extraction
            pdf_plumber = pdfplumber.open(pdf_path)
            
            for page_idx in range(pages_to_process):
                page_data = {
                    'page_number': page_idx + 1,
                    'text_blocks': [],
                    'tables': [],
                    'images': []
                }
                
                # Extract text with spatial information
                page_fitz = doc[page_idx]
                page_plumber = pdf_plumber.pages[page_idx]
                
                # Get text blocks with coordinates
                text_blocks = page_fitz.get_text("blocks")
                for block in text_blocks:
                    if len(block) >= 5:
                        block_text = block[4].strip()
                        if block_text:
                            page_data['text_blocks'].append({
                                'text': block_text,
                                'x': block[0],
                                'y': block[1],
                                'width': block[2] - block[0],
                                'height': block[3] - block[1]
                            })
                
                # Extract tables
                tables = page_plumber.extract_tables()
                for table_idx, table_data in enumerate(tables):
                    if table_data and len(table_data) > 0:
                        headers = [str(cell).strip() if cell else "" for cell in table_data[0]] if table_data else []
                        rows = []
                        for row in table_data[1:]:
                            if row and any(cell for cell in row if cell):
                                rows.append([str(cell).strip() if cell else "" for cell in row])
                        
                        if headers or rows:
                            # Get table bounding box
                            table_bbox = None
                            try:
                                page_tables = page_plumber.find_tables()
                                if table_idx < len(page_tables):
                                    table_obj = page_tables[table_idx]
                                    table_bbox = {
                                        'x': table_obj.bbox[0],
                                        'y': table_obj.bbox[1],
                                        'width': table_obj.bbox[2] - table_obj.bbox[0],
                                        'height': table_obj.bbox[3] - table_obj.bbox[1]
                                    }
                            except:
                                pass
                            
                            page_data['tables'].append({
                                'table_index': table_idx + 1,
                                'headers': headers,
                                'rows': rows,
                                'spatial_info': table_bbox
                            })
                
                # Extract images
                image_list = page_fitz.get_images(full=True)
                for img_idx, img in enumerate(image_list):
                    try:
                        xref = img[0]
                        base_image = doc.extract_image(xref)
                        image_bytes = base_image["image"]
                        image_ext = base_image["ext"]
                        
                        # Get image dimensions
                        image_obj = PILImage.open(io.BytesIO(image_bytes))
                        width, height = image_obj.size
                        
                        page_data['images'].append({
                            'image_index': img_idx + 1,
                            'format': image_ext.upper(),
                            'width': width,
                            'height': height,
                            'size_bytes': len(image_bytes),
                            'image_bytes_base64': base64.b64encode(image_bytes).decode('utf-8')
                        })
                    except Exception as e:
                        logger.warning(f"Failed to extract image {img_idx} from page {page_idx + 1}: {e}")
                
                raw_content['pages'].append(page_data)
            
            doc.close()
            pdf_plumber.close()
            
        except Exception as e:
            logger.error(f"Error extracting raw content from PDF: {e}")
            import traceback
            logger.error(traceback.format_exc())
            raise
        
        return raw_content
    
    def _llm_process_content(self, raw_content: Dict[str, Any],
                           extract_tables: bool,
                           extract_images: bool,
                           extract_entities: bool,
                           generate_chunks: bool,
                           chunk_strategy: str) -> LLMExtractionResult:
        """
        Use LLM to process raw content and extract structured information.
        
        This sends the raw extracted content to LLM with a carefully crafted prompt
        to extract tables, images, entities, and generate chunks while preserving
        reading order.
        """
        # Build the prompt for LLM
        prompt = self._build_extraction_prompt(
            raw_content,
            extract_tables=extract_tables,
            extract_images=extract_images,
            extract_entities=extract_entities,
            generate_chunks=generate_chunks,
            chunk_strategy=chunk_strategy
        )
        
        logger.info("=" * 80)
        logger.info("Sending extraction request to LLM...")
        logger.info(f"Prompt length: {len(prompt)} characters")
        logger.info(f"Pages to process: {len(raw_content['pages'])}")
        total_tables = sum(len(page.get('tables', [])) for page in raw_content['pages'])
        total_images = sum(len(page.get('images', [])) for page in raw_content['pages'])
        logger.info(f"[EMOJI] Raw extraction found: {total_tables} tables, {total_images} images")
        logger.info(f"[EMOJI] Extract tables: {extract_tables}, Extract images: {extract_images}")
        logger.info(f"[EMOJI] Processing mode: ALL PAGES IN ONE REQUEST (not page-by-page)")
        logger.info(f"Prompt preview (first 2000 chars):\n{prompt[:2000]}")
        print(f"[LLM EXTRACTOR] Processing {len(raw_content['pages'])} pages in ONE request")
        print(f"[LLM EXTRACTOR] Raw tables found: {total_tables}, Raw images found: {total_images}")
        print(f"[LLM EXTRACTOR] Extract tables={extract_tables}, Extract images={extract_images}")
        
        # Debug: Show table/image details per page
        for page_data in raw_content['pages']:
            page_num = page_data['page_number']
            page_tables = len(page_data.get('tables', []))
            page_images = len(page_data.get('images', []))
            if page_tables > 0 or page_images > 0:
                print(f"[LLM EXTRACTOR] Page {page_num}: {page_tables} tables, {page_images} images")
                if page_tables > 0:
                    for idx, table in enumerate(page_data.get('tables', [])):
                        headers = table.get('headers', [])
                        rows_count = len(table.get('rows', []))
                        print(f"  Table {idx+1}: {len(headers)} columns, {rows_count} rows, headers={headers[:3]}")
                if page_images > 0:
                    for idx, img in enumerate(page_data.get('images', [])):
                        print(f"  Image {idx+1}: {img.get('format', '?')} {img.get('width', 0)}x{img.get('height', 0)}")
        
        logger.info("=" * 80)
        
            # Call LLM
        try:
            print(f"[LLM EXTRACTOR] Calling LLM with model={self.llm_model}, temperature={self.temperature}")
            print(f"[LLM EXTRACTOR] Prompt length: {len(prompt)} chars")
            llm_response = self.llm_provider.generate(
                prompt=prompt,
                model=self.llm_model,
                temperature=self.temperature,
                max_tokens=16000  # Large response needed for structured data
            )
            
            # Extract response text from dict if needed
            if isinstance(llm_response, dict):
                response = llm_response.get('response', llm_response.get('text', llm_response.get('content', '')))
            else:
                response = str(llm_response) if llm_response else ''
            
            if not response or not response.strip():
                raise ValueError("Empty response from LLM")
            
            print(f"[LLM EXTRACTOR] LLM response received: {len(response)} chars")
            print(f"[LLM EXTRACTOR] Response preview (first 500 chars):\n{response[:500]}")
            
            # Parse LLM response (should be JSON)
            result = self._parse_llm_response(response)
            
            print(f"[LLM EXTRACTOR] Parsed result: {len(result.tables)} tables, {len(result.images)} images")
            if len(result.tables) > 0:
                print(f"[LLM EXTRACTOR] First table: {result.tables[0].headers}")
            if len(result.images) > 0:
                print(f"[LLM EXTRACTOR] First image: {result.images[0].description}")
            
            return result
            
        except Exception as e:
            logger.error(f"LLM processing failed: {e}")
            import traceback
            logger.error(traceback.format_exc())
            # Fallback to basic extraction
            return self._fallback_extraction(raw_content)
    
    def _build_extraction_prompt(self, raw_content: Dict[str, Any],
                                extract_tables: bool,
                                extract_images: bool,
                                extract_entities: bool,
                                generate_chunks: bool,
                                chunk_strategy: str) -> str:
        """Build comprehensive prompt for LLM extraction."""
        
        # Summarize raw content for prompt (to avoid token limits)
        content_summary = []
        for page_data in raw_content['pages']:
            page_text = "\n".join([block['text'] for block in page_data.get('text_blocks', [])])
            content_summary.append({
                'page': page_data['page_number'],
                'text_preview': page_text[:500] + "..." if len(page_text) > 500 else page_text,
                'text_length': len(page_text),
                'table_count': len(page_data.get('tables', [])),
                'image_count': len(page_data.get('images', []))
            })
        
        prompt = f"""You are an expert document analysis AI. Analyze the following PDF document content and extract structured information.

# DOCUMENT CONTENT:

"""
        
        # Add text content (first 5000 chars per page to avoid token limits)
        for page_data in raw_content['pages']:
            page_text = "\n".join([block['text'] for block in page_data.get('text_blocks', [])])
            # Truncate if too long
            if len(page_text) > 5000:
                page_text = page_text[:5000] + "... [truncated]"
            
            prompt += f"""
## PAGE {page_data['page_number']}:

{page_text}

"""
        
        # Add table extraction instructions - LLM should extract tables directly from text
        if extract_tables:
            prompt += """
# TABLE EXTRACTION:

IMPORTANT: You must identify and extract ALL tables from the document text above.

Look for:
- Tabular data with rows and columns
- Headers followed by data rows
- Financial tables, data tables, comparison tables
- Any structured data in table format

For each table you find:
1. Identify the headers (column names)
2. Extract ALL data rows
3. Preserve the exact structure and data
4. Include the page number where the table appears

IMPORTANT: Extract tables directly from the text content provided above. Do not rely on any pre-extracted table data.
Even if the text appears unstructured, identify tabular patterns and extract them as tables.

"""
        
        # Add image information
        if extract_images:
            prompt += """
# IMAGES IN DOCUMENT:

IMPORTANT: Extract ALL images found below. For each image, provide a description based on the document context.

"""
            image_count = 0
            for page_data in raw_content['pages']:
                for image in page_data.get('images', []):
                    image_count += 1
                    prompt += f"""
### Image {image['image_index']} (Page {page_data['page_number']}):

Format: {image.get('format', 'UNKNOWN')}
Width: {image.get('width', 0)} pixels
Height: {image.get('height', 0)} pixels
Size: {image.get('size_bytes', 0)} bytes
"""
                    # Note: Image base64 data is available but too large for prompt
                    # LLM should extract from context or describe what it sees in text
                    prompt += "Note: Image data is available. Extract description from document context.\n\n"
            
            if image_count == 0:
                prompt += "No images found in raw extraction.\n\n"
            else:
                prompt += f"\nTOTAL: {image_count} images found. Extract ALL of them in your JSON response.\n\n"
        
        prompt += f"""

# EXTRACTION TASK:

Extract and structure the following information from the document:

1. **Text Content**: Preserve natural reading order (left-to-right, top-to-bottom, column-by-column)
2. **Tables**: CRITICAL - Identify and extract ALL tables directly from the text. Look for tabular patterns, rows/columns, headers and data. Extract complete table structures with all rows and columns. Even if text appears unstructured, identify and extract any tabular data patterns.
3. **Images**: Extract image metadata (for images, I'll provide base64 data if needed)
4. **Named Entities**: Extract all entities (PERSON, ORGANIZATION, LOCATION, DATE, MONEY, etc.)
5. **Semantic Chunks**: Create meaningful chunks using {chunk_strategy} strategy

# OUTPUT FORMAT:

Return a JSON object with this exact structure:

{{
  "text_content": "Full document text in proper reading order",
  "tables": [
    {{
      "table_index": 1,
      "page_number": 1,
      "headers": ["Column1", "Column2"],
      "rows": [["Row1Col1", "Row1Col2"], ["Row2Col1", "Row2Col2"]],
      "title": "Table Title (if any)",
      "caption": "Table Caption (if any)"
    }}
  ],
  "images": [
    {{
      "image_index": 1,
      "page_number": 1,
      "description": "Description of what the image shows",
      "width": 800,
      "height": 600,
      "format": "PNG"
    }}
  ],
  "entities": [
    {{
      "entity_type": "ORGANIZATION",
      "entity_name": "TCS",
      "canonical_name": "Tata Consultancy Services",
      "context": "Surrounding text where entity appears"
    }}
  ],
  "chunks": [
    {{
      "chunk_index": 1,
      "content": "Chunk text content",
      "chunk_type": "paragraph",
      "page_number": 1,
      "entities": ["entity_name1", "entity_name2"]
    }}
  ],
  "metadata": {{
    "reading_order_preserved": true,
    "extraction_method": "llm",
    "model": "{self.llm_model}"
  }}
}}

# IMPORTANT INSTRUCTIONS:

1. **Reading Order**: When reconstructing text, read column-by-column (left column completely, then right column)
2. **Tables**: Preserve exact table structure - don't modify data
3. **Entities**: Extract all proper nouns, organizations, locations, dates, financial figures
4. **Chunks**: Create semantic chunks that preserve topic boundaries - each chunk should be a complete thought
5. **Quality**: Ensure accuracy - this data will be used for search and knowledge graph construction

Now extract the information and return ONLY valid JSON (no markdown, no code blocks):
"""
        
        return prompt
    
    def _parse_llm_response(self, response: str) -> LLMExtractionResult:
        """Parse LLM JSON response into structured result."""
        try:
            # Clean response - remove markdown code blocks if present
            cleaned_response = response.strip()
            if cleaned_response.startswith("```json"):
                cleaned_response = cleaned_response[7:]
            if cleaned_response.startswith("```"):
                cleaned_response = cleaned_response[3:]
            if cleaned_response.endswith("```"):
                cleaned_response = cleaned_response[:-3]
            cleaned_response = cleaned_response.strip()
            
            # Parse JSON
            data = json.loads(cleaned_response)
            
            # Convert to structured objects
            tables = [ExtractedTable(**t) for t in data.get('tables', [])]
            images = [ExtractedImage(**img) for img in data.get('images', [])]
            entities = [ExtractedEntity(**e) for e in data.get('entities', [])]
            chunks = [DocumentChunk(**c) for c in data.get('chunks', [])]
            
            result = LLMExtractionResult(
                text_content=data.get('text_content', ''),
                tables=tables,
                images=images,
                entities=entities,
                chunks=chunks,
                metadata=data.get('metadata', {}),
                reading_order_preserved=data.get('metadata', {}).get('reading_order_preserved', True)
            )
            
            return result
            
        except json.JSONDecodeError as e:
            logger.error(f"Failed to parse LLM JSON response: {e}")
            logger.error(f"Response preview: {response[:500]}")
            raise ValueError(f"Invalid JSON response from LLM: {e}")
        except Exception as e:
            logger.error(f"Error parsing LLM response: {e}")
            raise
    
    def _fallback_extraction(self, raw_content: Dict[str, Any]) -> LLMExtractionResult:
        """Fallback extraction if LLM fails - basic text extraction."""
        logger.warning("Using fallback extraction (LLM failed)")
        
        text_content = []
        for page_data in raw_content['pages']:
            page_text = "\n".join([block['text'] for block in page_data.get('text_blocks', [])])
            text_content.append(f"Page {page_data['page_number']}:\n{page_text}")
        
        return LLMExtractionResult(
            text_content="\n\n".join(text_content),
            tables=[],
            images=[],
            entities=[],
            chunks=[],
            metadata={'extraction_method': 'fallback', 'error': 'LLM extraction failed'},
            reading_order_preserved=False
        )


