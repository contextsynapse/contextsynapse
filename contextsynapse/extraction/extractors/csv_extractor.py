"""
CSV Extractor

Extracts content from CSV files.
"""

from pathlib import Path
from typing import Dict, Any
import logging
import csv

from ..registry import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class CsvExtractor(BaseExtractor):
    """
    CSV extractor.
    """
    
    def __init__(self, config):
        """Initialize CSV extractor."""
        super().__init__(config)
        self.extractor_version = "csv_v1"
    
    def supports_format(self, file_path: str) -> bool:
        """Check if file is a CSV."""
        return Path(file_path).suffix.lower() == ".csv"
    
    def extract(self, file_path: str) -> ExtractionResult:
        """
        Extract content from CSV file.
        
        Args:
            file_path: Path to CSV file
            
        Returns:
            ExtractionResult
        """
        result = ExtractionResult(extractor_version=self.extractor_version)
        
        try:
            with open(file_path, 'r', encoding='utf-8', newline='') as f:
                # Try to detect delimiter
                sample = f.read(1024)
                f.seek(0)
                sniffer = csv.Sniffer()
                delimiter = sniffer.sniff(sample).delimiter
                
                reader = csv.reader(f, delimiter=delimiter)
                rows = list(reader)
            
            if rows:
                result.tables.append({
                    "id": "tbl_01",
                    "data": rows,
                    "headers": rows[0] if rows else [],
                    "rows": rows[1:] if len(rows) > 1 else [],
                })
                
                # Also create a text representation
                text_lines = []
                for row in rows:
                    text_lines.append(" | ".join(str(cell) for cell in row))
                
                result.pages.append({
                    "page_no": 1,
                    "text": "\n".join(text_lines),
                    "content": "\n".join(text_lines),
                })
            
            result.metadata = {
                "title": Path(file_path).stem,
                "page_count": 1,
                "row_count": len(rows),
            }
        
        except Exception as e:
            logger.error(f"CSV extraction failed: {e}")
            result.errors.append(str(e))
        
        return result
