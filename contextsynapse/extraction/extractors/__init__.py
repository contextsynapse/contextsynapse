"""
Extractors Module

Contains implementations of various extractors.
"""

from .pdf_extractor import PdfExtractor
from .docx_extractor import DocxExtractor
from .txt_extractor import TxtExtractor
from .csv_extractor import CsvExtractor
from .html_extractor import HtmlExtractor
from .website_extractor import WebsiteExtractor
from .excel_extractor import ExcelExtractor

# Optional: Layout Parser extractor (only imported if available)
try:
    from .pdf_extractor_layoutparser import PdfExtractorLayoutParser
    __all__ = [
        "PdfExtractor",
        "PdfExtractorLayoutParser",
        "DocxExtractor",
        "TxtExtractor",
        "CsvExtractor",
        "HtmlExtractor",
        "WebsiteExtractor",
        "ExcelExtractor",
    ]
except ImportError:
    __all__ = [
        "PdfExtractor",
        "DocxExtractor",
        "TxtExtractor",
        "CsvExtractor",
        "HtmlExtractor",
        "WebsiteExtractor",
        "ExcelExtractor",
    ]
