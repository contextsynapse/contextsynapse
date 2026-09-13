"""
TXT Extractor

Extracts content from plain text files.
"""

from pathlib import Path
from typing import Dict, Any
import logging

from ..registry import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class TxtExtractor(BaseExtractor):
    """
    Plain text extractor.
    """
    
    def __init__(self, config):
        """Initialize TXT extractor."""
        super().__init__(config)
        self.extractor_version = "txt_v1"
    
    def supports_format(self, file_path: str) -> bool:
        """Check if file is a TXT."""
        return Path(file_path).suffix.lower() == ".txt"
    
    def extract(self, file_path: str) -> ExtractionResult:
        """
        Extract content from TXT file.
        
        Args:
            file_path: Path to TXT file
            
        Returns:
            ExtractionResult
        """
        result = ExtractionResult(extractor_version=self.extractor_version)
        
        try:
            # Try different encodings
            encodings = ['utf-8', 'latin-1', 'cp1252', 'iso-8859-1']
            content = None
            
            for encoding in encodings:
                try:
                    with open(file_path, 'r', encoding=encoding) as f:
                        content = f.read()
                    break
                except UnicodeDecodeError:
                    continue
            
            if content is None:
                raise ValueError(f"Could not decode file with any encoding: {encodings}")
            
            # Split into pages (by line breaks or size)
            lines = content.split('\n')
            page_size = 50  # lines per page (configurable)
            
            pages = []
            for i in range(0, len(lines), page_size):
                page_text = '\n'.join(lines[i:i+page_size])
                pages.append({
                    "page_no": len(pages) + 1,
                    "text": page_text,
                    "content": page_text,
                })
            
            if not pages:
                pages.append({
                    "page_no": 1,
                    "text": content,
                    "content": content,
                })
            
            result.pages = pages
            result.metadata = {
                "title": Path(file_path).stem,
                "page_count": len(pages),
            }
        
        except Exception as e:
            logger.error(f"TXT extraction failed: {e}")
            result.errors.append(str(e))
        
        return result
































