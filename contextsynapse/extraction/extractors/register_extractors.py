"""
Register Extractors

Registers all extractors with the ExtractorRegistry.
"""

from ..registry import ExtractorRegistry
from .pdf_extractor import PdfExtractor
from .docx_extractor import DocxExtractor
from .txt_extractor import TxtExtractor
from .csv_extractor import CsvExtractor
from .html_extractor import HtmlExtractor
from .website_extractor import WebsiteExtractor


def register_all_extractors():
    """Register all extractors."""
    # Register with auto-detect enabled
    ExtractorRegistry.register("pdf_v1", PdfExtractor, auto_detect=True)
    ExtractorRegistry.register("docx_v1", DocxExtractor, auto_detect=True)
    ExtractorRegistry.register("txt_v1", TxtExtractor, auto_detect=True)
    ExtractorRegistry.register("csv_v1", CsvExtractor, auto_detect=True)
    ExtractorRegistry.register("html_v1", HtmlExtractor, auto_detect=True)
    ExtractorRegistry.register("website_v1", WebsiteExtractor, auto_detect=True)
    
    # Also register as "AUTO" fallback (handled by auto_detect)
    # The AUTO logic in ExtractorRegistry will use auto_detect=True extractors


# Auto-register on import
register_all_extractors()
































