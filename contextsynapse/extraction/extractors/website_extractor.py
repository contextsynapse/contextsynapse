"""
Website/URL Extractor

Extracts content from websites using trafilatura.
"""

from pathlib import Path
from typing import Dict, Any
import logging
import json

from ..registry import BaseExtractor, ExtractionResult

logger = logging.getLogger(__name__)


class WebsiteExtractor(BaseExtractor):
    """
    Website extractor using trafilatura for clean text extraction.
    """
    
    def __init__(self, config):
        """Initialize website extractor."""
        super().__init__(config)
        self.extractor_version = "website_v1"
    
    def supports_format(self, file_path: str) -> bool:
        """Check if input is a URL or website."""
        path_str = str(file_path).lower().strip()
        return (
            path_str.startswith("http://") or
            path_str.startswith("https://") or
            path_str.startswith("www.")
        )
    
    def extract(self, file_path: str) -> ExtractionResult:
        """
        Extract content from website/URL.
        
        Args:
            file_path: URL or website address
            
        Returns:
            ExtractionResult
        """
        result = ExtractionResult(extractor_version=self.extractor_version)
        
        # #region agent log
        log_path = r"c:\qgraph\qgraph-app\.cursor\debug.log"
        try:
            with open(log_path, 'a', encoding='utf-8') as f:
                f.write(json.dumps({"sessionId":"debug-session","runId":"extraction","hypothesisId":"C","location":"website_extractor.py:40","message":"Starting website extraction","data":{"url":file_path},"timestamp":int(__import__('time').time()*1000)})+"\n")
        except: pass
        # #endregion
        
        try:
            import trafilatura
            import requests
            from urllib.parse import urlparse
            
            # Normalize URL
            url = str(file_path).strip()
            if not url.startswith(("http://", "https://")):
                if url.startswith("www."):
                    url = "https://" + url
                else:
                    url = "https://" + url
            
            # Fetch HTML content
            try:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36'
                }
                response = requests.get(url, headers=headers, timeout=30)
                response.raise_for_status()
                html_content = response.text
            except Exception as e:
                logger.error(f"Failed to fetch URL {url}: {e}")
                result.errors.append(f"Failed to fetch URL: {e}")
                return result
            
            # Extract main content using trafilatura
            # Extract text content (default format)
            extracted = trafilatura.extract(html_content, output_format='txt')
            
            # Extract XML format for structured output (optional)
            extracted_xml = None
            try:
                extracted_xml = trafilatura.extract(html_content, output_format='xml')
            except:
                pass
            
            if not extracted:
                # Fallback: use BeautifulSoup to extract text
                try:
                    from bs4 import BeautifulSoup
                    soup = BeautifulSoup(html_content, 'html.parser')
                    # Remove script and style elements
                    for script in soup(["script", "style"]):
                        script.decompose()
                    extracted = soup.get_text(separator='\n', strip=True)
                except:
                    extracted = ""
            
            # Extract metadata
            metadata_dict = None
            try:
                metadata_dict = trafilatura.extract_metadata(html_content)
            except:
                pass
            
            # Use extracted text as text_content
            text_content = extracted or ""
            
            # Extract tables from HTML
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_content, 'html.parser')
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
            except ImportError:
                logger.warning("BeautifulSoup not available for table extraction")
            except Exception as e:
                logger.warning(f"Table extraction failed: {e}")
            
            # Extract images
            try:
                from bs4 import BeautifulSoup
                soup = BeautifulSoup(html_content, 'html.parser')
                images = soup.find_all('img')
                
                for img_idx, img in enumerate(images, 1):
                    src = img.get('src', '')
                    alt = img.get('alt', '')
                    
                    # Resolve relative URLs
                    if src and not src.startswith(('http://', 'https://')):
                        parsed_url = urlparse(url)
                        base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"
                        if src.startswith('/'):
                            src = base_url + src
                        else:
                            src = base_url + '/' + src
                    
                    result.images.append({
                        "id": f"img_{img_idx}",
                        "path": src,
                        "description": alt,
                        "url": src,
                    })
            except ImportError:
                logger.warning("BeautifulSoup not available for image extraction")
            except Exception as e:
                logger.warning(f"Image extraction failed: {e}")
            
            # Build metadata
            result.metadata = {
                "title": metadata_dict.title if metadata_dict else urlparse(url).netloc,
                "author": metadata_dict.author if metadata_dict else "",
                "url": url,
                "domain": urlparse(url).netloc,
                "page_count": 1,
            }
            
            # Store as page
            result.pages.append({
                "page_no": 1,
                "text": text_content,
                "content": text_content,
            })
            
            # Store web content
            result.web.append({
                "url": url,
                "html": html_content,
                "clean_text": text_content,
                "extracted_xml": extracted_xml if extracted_xml else "",
            })
            
            # #region agent log
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"extraction","hypothesisId":"C","location":"website_extractor.py:155","message":"Website extraction complete","data":{"text_length":len(text_content),"tables_extracted":len(result.tables),"images_extracted":len(result.images)},"timestamp":int(__import__('time').time()*1000)})+"\n")
            except: pass
            # #endregion
            
        except ImportError:
            error_msg = "trafilatura or requests not available. Install with: pip install trafilatura requests beautifulsoup4"
            logger.error(error_msg)
            result.errors.append(error_msg)
        except Exception as e:
            logger.error(f"Website extraction failed: {e}")
            result.errors.append(str(e))
            # #region agent log
            try:
                with open(log_path, 'a', encoding='utf-8') as f:
                    f.write(json.dumps({"sessionId":"debug-session","runId":"extraction","hypothesisId":"C","location":"website_extractor.py:167","message":"Website extraction error","data":{"error":str(e)},"timestamp":int(__import__('time').time()*1000)})+"\n")
            except: pass
            # #endregion
        
        return result





























