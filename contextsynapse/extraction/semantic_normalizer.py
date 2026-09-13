"""
Semantic Normalizer

Rebuilds paragraphs from fragmented blocks using semantic rules.
This is Stage 2 of normalization: Semantic Normalization (paragraph rebuild).

Pipeline:
1. Structural Normalization (blocks) - extracts blocks with structure
2. Semantic Normalization (paragraph rebuild) - merges blocks into coherent paragraphs
3. Markdown (optional view) - generates markdown representation
"""

from typing import Dict, List, Any, Optional
import re
import logging

logger = logging.getLogger(__name__)


def is_toc_or_index_entry(text: str) -> bool:
    """
    Detect if text is a table of contents or index entry.
    
    Args:
        text: Text to check
        
    Returns:
        True if likely TOC/index entry
    """
    if not text or len(text.strip()) < 3:
        return False
    
    text = text.strip()
    
    # Pattern 1: Ends with dots followed by page number
    if re.search(r'\.{2,}\s*\d+$', text):
        return True
    
    # Pattern 2: High digit density (likely page numbers)
    digit_ratio = sum(1 for c in text if c.isdigit()) / len(text) if text else 0
    if digit_ratio > 0.3 and len(text) < 100:
        return True
    
    # Pattern 3: Multiple titles separated by page numbers
    if re.search(r'\d+\s+\d+', text) and len(text) < 150:
        return True
    
    # Pattern 4: Very short + heading type (likely TOC entry)
    if len(text) < 50 and re.search(r'^\w+\s+\d+$', text):
        return True
    
    return False


def should_merge_blocks(prev_block: Dict[str, Any], current_block: Dict[str, Any]) -> bool:
    """
    Determine if two blocks should be merged into one paragraph.
    
    Paragraph Continuation Rules:
    - Same font name and size
    - Same block type = paragraph
    - Previous text does not end with [.?!]
    - Current text starts lowercase OR numeric OR bullet
    
    Args:
        prev_block: Previous block
        current_block: Current block
        
    Returns:
        True if blocks should be merged
    """
    prev_text = prev_block.get('text', '').strip()
    curr_text = current_block.get('text', '').strip()
    
    if not prev_text or not curr_text:
        return False
    
    # Rule 1: Previous text doesn't end with sentence ending
    if prev_text and prev_text[-1] not in '.!?':
        # Rule 2: Current text starts with lowercase, number, or bullet
        if curr_text and (curr_text[0].islower() or curr_text[0].isdigit() or curr_text.startswith(('•', '-', '*'))):
            # Rule 3: Same block type
            if prev_block.get('type') == current_block.get('type') == 'paragraph':
                # Rule 4: Same font (if available)
                prev_font = prev_block.get('font_name')
                curr_font = current_block.get('font_name')
                if prev_font and curr_font and prev_font == curr_font:
                    return True
                # Or if fonts not available, merge if continuation pattern matches
                elif not prev_font or not curr_font:
                    return True
    
    return False


class SemanticNormalizer:
    """
    Semantic Normalizer - Rebuilds paragraphs from fragmented blocks.
    
    This is Stage 2 of normalization, after structural normalization.
    """
    
    def __init__(self, enable_toc_detection: bool = True):
        """
        Initialize semantic normalizer.
        
        Args:
            enable_toc_detection: Whether to detect and skip TOC/index entries
        """
        self.enable_toc_detection = enable_toc_detection
    
    def rebuild_paragraphs(
        self,
        normalized_blocks: List[Dict[str, Any]],
        page_no: int
    ) -> List[Dict[str, Any]]:
        """
        Rebuild paragraphs from fragmented blocks.
        
        This merges blocks that are part of the same semantic paragraph.
        
        Args:
            normalized_blocks: List of structurally normalized blocks
            page_no: Page number for tracking
            
        Returns:
            List of rebuilt paragraphs with merged content
        """
        if not normalized_blocks:
            return []
        
        rebuilt = []
        current_para = None
        
        for block in normalized_blocks:
            block_type = block.get('type', 'paragraph')
            block_text = block.get('text', '').strip()
            
            if not block_text:
                continue
            
            # Skip TOC/index entries if enabled
            if self.enable_toc_detection and is_toc_or_index_entry(block_text):
                logger.debug(f"Page {page_no}: Skipping TOC/index entry: {block_text[:50]}...")
                continue
            
            # If we have a current paragraph and should merge
            if current_para and should_merge_blocks(current_para, block):
                # Merge: add space and continue
                current_para['text'] = current_para['text'] + ' ' + block_text
                # Update metadata
                if 'source_block_ids' in current_para:
                    current_para['source_block_ids'].append(block.get('id'))
                else:
                    current_para['source_block_ids'] = [current_para.get('id'), block.get('id')]
                # Update end position if available
                if 'end_char' in block:
                    current_para['end_char'] = block.get('end_char')
            else:
                # Save previous paragraph if exists
                if current_para:
                    rebuilt.append(current_para)
                
                # Start new paragraph
                current_para = {
                    'id': block.get('id', f"rebuilt_p{page_no}_{len(rebuilt) + 1}"),
                    'type': block_type,
                    'text': block_text,
                    'page_no': page_no,
                    'source_block_ids': [block.get('id')],
                    'level': block.get('level'),
                    'font_size': block.get('font_size'),
                    'font_name': block.get('font_name'),
                    'start_char': block.get('start_char', 0),
                    'end_char': block.get('end_char', len(block_text))
                }
        
        # Don't forget the last paragraph
        if current_para:
            rebuilt.append(current_para)
        
        logger.debug(f"Page {page_no}: Rebuilt {len(rebuilt)} paragraphs from {len(normalized_blocks)} blocks")
        
        return rebuilt
    
    def normalize_page(
        self,
        page_data: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply semantic normalization to a page.
        
        Takes a page with normalized_blocks and adds rebuilt_paragraphs.
        
        Args:
            page_data: Page dictionary with normalized_blocks
            
        Returns:
            Page dictionary with added rebuilt_paragraphs field
        """
        page_no = page_data.get('page_no', 0)
        normalized_blocks = page_data.get('normalized_blocks', [])
        
        # Rebuild paragraphs
        rebuilt_paragraphs = self.rebuild_paragraphs(normalized_blocks, page_no)
        
        # Add rebuilt paragraphs to page data
        page_data['rebuilt_paragraphs'] = rebuilt_paragraphs
        
        # Update flat_text if needed (optional - can keep original)
        if rebuilt_paragraphs and not page_data.get('flat_text'):
            page_data['flat_text'] = '\n\n'.join([p.get('text', '') for p in rebuilt_paragraphs])
        
        return page_data
    
    def normalize_document(
        self,
        normalized_doc: Dict[str, Any]
    ) -> Dict[str, Any]:
        """
        Apply semantic normalization to entire document.
        
        Args:
            normalized_doc: Normalized document with pages
            
        Returns:
            Document with rebuilt_paragraphs added to each page
        """
        pages = normalized_doc.get('content', {}).get('pages', [])
        
        for page in pages:
            self.normalize_page(page)
        
        # Update document metadata
        if 'metadata' not in normalized_doc:
            normalized_doc['metadata'] = {}
        
        normalized_doc['metadata']['semantic_normalization'] = {
            'enabled': True,
            'toc_detection': self.enable_toc_detection,
            'version': 'v1'
        }
        
        return normalized_doc

