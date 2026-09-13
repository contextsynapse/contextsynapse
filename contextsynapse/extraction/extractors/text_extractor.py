"""
Text Extractor

Extracts content from plain text files.
"""

from pathlib import Path
from typing import Dict, Any
import logging

from ..registry import BaseExtractor, ExtractionResult
from ..config import ExtractionConfig

logger = logging.getLogger(__name__)


class TextExtractor(BaseExtractor):
    """Text file extractor."""
    
    version = "txt_v1"
    
    def supports_format(self, file_path: str) -> bool:
        """Check if file is text."""
        ext = Path(file_path).suffix.lower()
        return ext in {".txt", ".md", ".log"}
    
    def extract(self, file_path: str) -> ExtractionResult:
        """Extract content from text file."""
        result = ExtractionResult(extractor_version=self.version)
        
        try:
            # Try UTF-8 first
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
            except UnicodeDecodeError:
                # Fallback to latin-1
                with open(file_path, 'r', encoding='latin-1') as f:
                    content = f.read()
            
            result.pages[1] = content
            result.metadata = {
                "page_count": 1,
                "file_path": str(file_path),
                "line_count": len(content.splitlines()),
            }
            
            # Create raw blocks
            result.raw_blocks[1] = [
                {
                    "id": f"raw_1_{i}",
                    "type": "paragraph",
                    "content": line,
                    "metadata": {}
                }
                for i, line in enumerate(content.splitlines(), 1)
                if line.strip()
            ]
            
        except Exception as e:
            logger.error(f"Text extraction failed: {e}")
            result.errors.append(str(e))
        
        return result

