"""
Extraction Configuration Models

Defines configuration structures for extraction operations.
"""

from dataclasses import dataclass, field
from typing import List, Optional, Tuple
from enum import Enum
from pathlib import Path


class ExtractionMode(str, Enum):
    """Extraction scope modes."""
    FULL = "full"
    RANGE = "range"
    LIST = "list"


@dataclass
class ExtractionConfig:
    """
    Configuration for document extraction.
    
    Attributes:
        file_path: Path to the input file
        reader: Extractor name ("AUTO" or specific like "pdf_v1", "pdf_ocr_v2")
        detect: List of modalities to detect/extract (e.g., ["TEXT", "TABLES", "IMAGES"])
        pages_mode: Extraction scope mode (full, range, list)
        pages_range: Tuple of (start, end) for range mode (1-indexed, inclusive)
        pages_list: List of page numbers for list mode (1-indexed)
        parse_metadata: Whether to parse document metadata
        store_intermediate: Whether to store intermediate extraction artifacts
        output_dir: Base directory for output (default: "contextcore_data")
        document_id: Optional document ID (auto-generated if not provided)
    """
    file_path: str
    reader: str = "AUTO"
    detect: List[str] = field(default_factory=lambda: ["TEXT"])
    pages_mode: ExtractionMode = ExtractionMode.FULL
    pages_range: Optional[Tuple[int, int]] = None
    pages_list: Optional[List[int]] = None
    parse_metadata: bool = True
    store_intermediate: bool = True
    output_dir: str = "contextcore_data"
    namespace: Optional[str] = None  # Namespace for hybrid storage
    document_id: Optional[str] = None
    use_layout_parser: bool = False  # Enable Layout Parser for multi-column extraction
    layout_detection_method: str = "auto"  # "auto", "heuristic", "ml"
    post_process: bool = True  # Enable post-processing to clean normalized documents
    post_processing_config: Optional[dict] = None  # Post-processing configuration (dict or PostProcessingConfig)
    
    def __post_init__(self):
        """Validate configuration."""
        # Validate file exists (skip for URLs/websites)
        file_path_str = str(self.file_path).lower().strip()
        is_url = (
            file_path_str.startswith("http://") or
            file_path_str.startswith("https://") or
            file_path_str.startswith("www.")
        )
        if not is_url and not Path(self.file_path).exists():
            raise FileNotFoundError(f"File not found: {self.file_path}")
        
        # Validate pages_mode consistency
        if self.pages_mode == ExtractionMode.RANGE:
            if not self.pages_range:
                raise ValueError("pages_range must be provided for RANGE mode")
            start, end = self.pages_range
            if start < 1 or end < start:
                raise ValueError(f"Invalid pages_range: {self.pages_range}")
        
        elif self.pages_mode == ExtractionMode.LIST:
            if not self.pages_list:
                raise ValueError("pages_list must be provided for LIST mode")
            if any(p < 1 for p in self.pages_list):
                raise ValueError("Page numbers must be >= 1")
        
        # Normalize detect list
        self.detect = [d.upper() for d in self.detect]
    
    def to_dict(self) -> dict:
        """Convert to dictionary for serialization (e.g., for Ray)."""
        return {
            "file_path": self.file_path,
            "reader": self.reader,
            "detect": self.detect,
            "pages_mode": self.pages_mode.value,
            "pages_range": self.pages_range,
            "pages_list": self.pages_list,
            "parse_metadata": self.parse_metadata,
            "store_intermediate": self.store_intermediate,
            "output_dir": self.output_dir,
            "document_id": self.document_id,
            "use_layout_parser": self.use_layout_parser,
            "layout_detection_method": self.layout_detection_method,
            "post_process": self.post_process,
            "post_processing_config": self.post_processing_config,
        }
    
    @classmethod
    def from_dict(cls, data: dict) -> "ExtractionConfig":
        """Create from dictionary."""
        data = data.copy()
        if "pages_mode" in data and isinstance(data["pages_mode"], str):
            data["pages_mode"] = ExtractionMode(data["pages_mode"])
        return cls(**data)
