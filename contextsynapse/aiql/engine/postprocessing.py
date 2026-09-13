"""
Post-processing Module for Normalized Documents

This module provides configurable post-processing to clean and optimize
normalized documents before storage, reducing noise and ensuring optimal
format for chunking operations.
"""

import re
import logging
from typing import Dict, List, Optional, Any, Callable
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class PostProcessingConfig:
    """Configuration for post-processing operations."""
    
    def __init__(
        self,
        remove_whitespace_noise: bool = True,
        remove_control_chars: bool = True,
        normalize_unicode: bool = True,
        remove_repeated_chars: bool = True,
        min_text_length: int = 3,
        remove_empty_blocks: bool = True,
        deduplicate_blocks: bool = False,
        normalize_line_breaks: bool = True,
        remove_headers_footers: bool = False,
        clean_table_data: bool = True,
        remove_special_noise: bool = True,
        custom_cleaners: Optional[List[Callable]] = None
    ):
        """
        Initialize post-processing configuration.
        
        Args:
            remove_whitespace_noise: Remove excessive whitespace
            remove_control_chars: Remove control characters
            normalize_unicode: Normalize unicode characters
            remove_repeated_chars: Remove excessive character repetition
            min_text_length: Minimum text length to keep
            remove_empty_blocks: Remove empty text blocks
            deduplicate_blocks: Remove duplicate blocks
            normalize_line_breaks: Normalize line break characters
            remove_headers_footers: Attempt to remove headers/footers
            clean_table_data: Clean table cell data
            remove_special_noise: Remove special noise patterns
            custom_cleaners: List of custom cleaning functions
        """
        self.remove_whitespace_noise = remove_whitespace_noise
        self.remove_control_chars = remove_control_chars
        self.normalize_unicode = normalize_unicode
        self.remove_repeated_chars = remove_repeated_chars
        self.min_text_length = min_text_length
        self.remove_empty_blocks = remove_empty_blocks
        self.deduplicate_blocks = deduplicate_blocks
        self.normalize_line_breaks = normalize_line_breaks
        self.remove_headers_footers = remove_headers_footers
        self.clean_table_data = clean_table_data
        self.remove_special_noise = remove_special_noise
        self.custom_cleaners = custom_cleaners or []
    
    @classmethod
    def from_dict(cls, config_dict: Dict[str, Any]) -> "PostProcessingConfig":
        """Create config from dictionary."""
        return cls(**{k: v for k, v in config_dict.items() if k != "custom_cleaners"})
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary."""
        return {
            "remove_whitespace_noise": self.remove_whitespace_noise,
            "remove_control_chars": self.remove_control_chars,
            "normalize_unicode": self.normalize_unicode,
            "remove_repeated_chars": self.remove_repeated_chars,
            "min_text_length": self.min_text_length,
            "remove_empty_blocks": self.remove_empty_blocks,
            "deduplicate_blocks": self.deduplicate_blocks,
            "normalize_line_breaks": self.normalize_line_breaks,
            "remove_headers_footers": self.remove_headers_footers,
            "clean_table_data": self.clean_table_data,
            "remove_special_noise": self.remove_special_noise,
        }


class DocumentPostProcessor:
    """
    Post-processor for cleaning and optimizing normalized documents.
    
    This processor removes noise, normalizes text, and optimizes the
    document structure to ensure clean data for chunking operations.
    """
    
    def __init__(self, config: Optional[PostProcessingConfig] = None):
        """
        Initialize post-processor.
        
        Args:
            config: Post-processing configuration
        """
        self.config = config or PostProcessingConfig()
    
    def process(self, normalized_doc: Dict[str, Any]) -> Dict[str, Any]:
        """
        Process and clean normalized document.
        
        Args:
            normalized_doc: Normalized document dictionary
            
        Returns:
            Cleaned normalized document
        """
        logger.info(f"Post-processing document: {normalized_doc.get('document_id', 'unknown')}")
        
        # Create a copy to avoid modifying original
        cleaned_doc = normalized_doc.copy()
        
        # Process content section
        if "content" in cleaned_doc:
            cleaned_doc["content"] = self._process_content(cleaned_doc["content"])
        
        # Process global text cache
        if "global_text_cache" in cleaned_doc:
            cleaned_doc["global_text_cache"] = self._clean_text(cleaned_doc["global_text_cache"])
        
        # Update status
        if "status" in cleaned_doc:
            cleaned_doc["status"]["post_processed"] = True
            cleaned_doc["status"]["post_processed_at"] = self._get_timestamp()
        
        logger.info(f"Post-processing complete for document: {cleaned_doc.get('document_id', 'unknown')}")
        return cleaned_doc
    
    def _process_content(self, content: Dict[str, Any]) -> Dict[str, Any]:
        """Process content section."""
        processed_content = content.copy()
        
        # Process pages
        if "pages" in processed_content:
            processed_content["pages"] = [
                self._process_page(page) for page in processed_content["pages"]
            ]
        
        # Process tables
        if "tables" in processed_content and self.config.clean_table_data:
            processed_content["tables"] = [
                self._process_table(table) for table in processed_content["tables"]
            ]
        
        return processed_content
    
    def _process_page(self, page: Dict[str, Any]) -> Dict[str, Any]:
        """Process a single page."""
        processed_page = page.copy()
        
        # Clean flat_text
        if "flat_text" in processed_page:
            processed_page["flat_text"] = self._clean_text(processed_page["flat_text"])
        
        # Process normalized_blocks
        if "normalized_blocks" in processed_page:
            processed_page["normalized_blocks"] = self._process_blocks(
                processed_page["normalized_blocks"]
            )
        
        # Process raw_blocks
        if "raw_blocks" in processed_page:
            processed_page["raw_blocks"] = self._process_blocks(
                processed_page["raw_blocks"]
            )
        
        # Clean markdown
        if "markdown" in processed_page:
            processed_page["markdown"] = self._clean_text(processed_page["markdown"])
        
        return processed_page
    
    def _process_blocks(self, blocks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Process text blocks."""
        processed_blocks = []
        seen_texts = set()  # For deduplication
        
        for block in blocks:
            processed_block = block.copy()
            
            # Clean text content
            if "text" in processed_block:
                text = self._clean_text(processed_block["text"])
                
                # Check minimum length
                if len(text.strip()) < self.config.min_text_length:
                    if self.config.remove_empty_blocks:
                        continue
                    else:
                        processed_block["text"] = text
                else:
                    processed_block["text"] = text
                    
                    # Deduplicate if enabled
                    if self.config.deduplicate_blocks:
                        text_key = text.strip().lower()
                        if text_key in seen_texts:
                            continue
                        seen_texts.add(text_key)
            
            # Clean content field (for raw_blocks)
            if "content" in processed_block:
                content = self._clean_text(processed_block["content"])
                if len(content.strip()) < self.config.min_text_length:
                    if self.config.remove_empty_blocks:
                        continue
                processed_block["content"] = content
            
            processed_blocks.append(processed_block)
        
        return processed_blocks
    
    def _process_table(self, table: Dict[str, Any]) -> Dict[str, Any]:
        """Process a table."""
        processed_table = table.copy()
        
        # Clean table data
        if "data" in processed_table:
            cleaned_data = []
            for row in processed_table["data"]:
                cleaned_row = [
                    self._clean_text(str(cell)) if cell else ""
                    for cell in row
                ]
                cleaned_data.append(cleaned_row)
            processed_table["data"] = cleaned_data
        
        # Clean markdown
        if "markdown" in processed_table:
            processed_table["markdown"] = self._clean_text(processed_table["markdown"])
        
        return processed_table
    
    def _clean_text(self, text: str) -> str:
        """
        Clean text using configured rules.
        
        Args:
            text: Text to clean
            
        Returns:
            Cleaned text
        """
        if not isinstance(text, str):
            return text
        
        cleaned = text
        
        # Normalize unicode
        if self.config.normalize_unicode:
            try:
                import unicodedata
                cleaned = unicodedata.normalize("NFKC", cleaned)
            except ImportError:
                logger.warning("unicodedata not available, skipping unicode normalization")
        
        # Remove control characters
        if self.config.remove_control_chars:
            # Keep common whitespace (space, tab, newline, carriage return)
            cleaned = re.sub(r'[\x00-\x08\x0B-\x0C\x0E-\x1F\x7F-\x9F]', '', cleaned)
        
        # Normalize line breaks
        if self.config.normalize_line_breaks:
            # Convert all line break variations to \n
            cleaned = re.sub(r'\r\n|\r', '\n', cleaned)
            # Remove excessive line breaks (more than 2 consecutive)
            cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        
        # Remove whitespace noise
        if self.config.remove_whitespace_noise:
            # Remove excessive spaces (more than 2 consecutive)
            cleaned = re.sub(r' {3,}', ' ', cleaned)
            # Remove spaces at start/end of lines
            cleaned = re.sub(r'^ +| +$', '', cleaned, flags=re.MULTILINE)
            # Remove trailing whitespace
            cleaned = cleaned.rstrip()
        
        # Remove repeated characters
        if self.config.remove_repeated_chars:
            # Remove excessive character repetition (more than 3)
            cleaned = re.sub(r'(.)\1{3,}', r'\1\1\1', cleaned)
        
        # Remove special noise patterns
        if self.config.remove_special_noise:
            # Remove common noise patterns
            noise_patterns = [
                r'^[\s\-_=]{3,}$',  # Lines with only separators
                r'^Page \d+$',  # Standalone page numbers
                r'^\d+$',  # Standalone numbers (if short)
            ]
            lines = cleaned.split('\n')
            cleaned_lines = []
            for line in lines:
                is_noise = False
                for pattern in noise_patterns:
                    if re.match(pattern, line.strip()):
                        is_noise = True
                        break
                if not is_noise:
                    cleaned_lines.append(line)
            cleaned = '\n'.join(cleaned_lines)
        
        # Apply custom cleaners
        for cleaner in self.config.custom_cleaners:
            try:
                cleaned = cleaner(cleaned)
            except Exception as e:
                logger.warning(f"Custom cleaner failed: {e}")
        
        return cleaned
    
    def _get_timestamp(self) -> str:
        """Get current timestamp in ISO format."""
        from datetime import datetime
        return datetime.utcnow().isoformat() + "Z"


def create_default_postprocessor() -> DocumentPostProcessor:
    """Create default post-processor with standard configuration."""
    config = PostProcessingConfig()
    return DocumentPostProcessor(config)


def create_aggressive_postprocessor() -> DocumentPostProcessor:
    """Create aggressive post-processor with maximum cleaning."""
    config = PostProcessingConfig(
        remove_whitespace_noise=True,
        remove_control_chars=True,
        normalize_unicode=True,
        remove_repeated_chars=True,
        min_text_length=5,
        remove_empty_blocks=True,
        deduplicate_blocks=True,
        normalize_line_breaks=True,
        remove_headers_footers=True,
        clean_table_data=True,
        remove_special_noise=True,
    )
    return DocumentPostProcessor(config)


def create_minimal_postprocessor() -> DocumentPostProcessor:
    """Create minimal post-processor with basic cleaning only."""
    config = PostProcessingConfig(
        remove_whitespace_noise=True,
        remove_control_chars=True,
        normalize_unicode=False,
        remove_repeated_chars=False,
        min_text_length=1,
        remove_empty_blocks=False,
        deduplicate_blocks=False,
        normalize_line_breaks=True,
        remove_headers_footers=False,
        clean_table_data=False,
        remove_special_noise=False,
    )
    return DocumentPostProcessor(config)

