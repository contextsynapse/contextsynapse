"""
HTML Extractor

Extracts content from HTML files.
"""

from pathlib import Path
from typing import Dict, Any
import logging

from ..registry import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class HtmlExtractor(BaseExtractor):
    """
    HTML extractor using BeautifulSoup.
    """
    
    def __init__(self, config):
        """Initialize HTML extractor."""
        super().__init__(config)
        self.extractor_version = "html_v1"
    
    def supports_format(self, file_path: str) -> bool:
        """Check if file is HTML."""
        ext = Path(file_path).suffix.lower()
        return ext in [".html", ".htm"]
    
    def extract(self, file_path: str) -> ExtractionResult:
        """
        Extract content from HTML file.
        
        Args:
            file_path: Path to HTML file
            
        Returns:
            ExtractionResult
        """
        result = ExtractionResult(extractor_version=self.extractor_version)
        
        try:
            from bs4 import BeautifulSoup
            
            with open(file_path, 'r', encoding='utf-8') as f:
                html_content = f.read()
            
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extract text
            text = soup.get_text(separator='\n', strip=True)
            
            # Extract title
            title_tag = soup.find('title')
            title = title_tag.text if title_tag else Path(file_path).stem
            
            # Extract tables
            tables = soup.find_all('table')
            for table_idx, table in enumerate(tables, 1):
                table_data = []
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'th'])
                    row_data = [cell.get_text(strip=True) for cell in cells]
                    if row_data:
                        table_data.append(row_data)
                
                if table_data:
                    result.tables.append({
                        "id": f"tbl_{table_idx}",
                        "data": table_data,
                        "headers": table_data[0] if table_data else [],
                        "rows": table_data[1:] if len(table_data) > 1 else [],
                    })
            
            # Extract images
            images = soup.find_all('img')
            for img_idx, img in enumerate(images, 1):
                src = img.get('src', '')
                alt = img.get('alt', '')
                result.images.append({
                    "id": f"img_{img_idx}",
                    "path": src,
                    "description": alt,
                })
            
            result.pages.append({
                "page_no": 1,
                "text": text,
                "content": text,
            })
            
            result.metadata = {
                "title": title,
                "page_count": 1,
            }
            
            # Store HTML content in web field
            result.web.append({
                "url": f"file://{Path(file_path).absolute()}",
                "html": html_content,
                "clean_text": text,
            })
        
        except ImportError:
            error_msg = "beautifulsoup4 not available. Install with: pip install beautifulsoup4"
            logger.error(error_msg)
            result.errors.append(error_msg)
        except Exception as e:
            logger.error(f"HTML extraction failed: {e}")
            result.errors.append(str(e))
        
        return result
































