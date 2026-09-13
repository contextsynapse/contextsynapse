"""
Extractor Registry

Pluggable extractor system with registry pattern for extensibility.
"""

from abc import ABC, abstractmethod
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path
import logging

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    """
    Result from an extractor.
    
    Attributes:
        metadata: Document metadata (title, author, page_count, etc.)
        pages: List of page content (text, blocks, etc.)
        tables: List of extracted tables
        images: List of image metadata and paths
        audio: List of audio metadata and paths
        video: List of video metadata and paths
        web: List of web content (if applicable)
        extractor_version: Version identifier of the extractor used
        errors: List of error messages (if any)
    """
    metadata: Dict[str, Any] = field(default_factory=dict)
    pages: List[Dict[str, Any]] = field(default_factory=list)
    tables: List[Dict[str, Any]] = field(default_factory=list)
    images: List[Dict[str, Any]] = field(default_factory=list)
    audio: List[Dict[str, Any]] = field(default_factory=list)
    video: List[Dict[str, Any]] = field(default_factory=list)
    web: List[Dict[str, Any]] = field(default_factory=list)
    extractor_version: str = "unknown"
    errors: List[str] = field(default_factory=list)
    
    def has_content(self) -> bool:
        """Check if extraction produced any content."""
        return (
            len(self.pages) > 0 or
            len(self.tables) > 0 or
            len(self.images) > 0 or
            len(self.audio) > 0 or
            len(self.video) > 0 or
            len(self.web) > 0
        )


class BaseExtractor(ABC):
    """
    Abstract base class for all extractors.
    
    All extractors must implement the extract() method.
    """
    
    def __init__(self, config: "ExtractionConfig"):
        """
        Initialize extractor.
        
        Args:
            config: Extraction configuration
        """
        self.config = config
        self.logger = logging.getLogger(f"{__name__}.{self.__class__.__name__}")
    
    @abstractmethod
    def extract(self, file_path: str) -> ExtractionResult:
        """
        Extract content from file.
        
        Args:
            file_path: Path to the file to extract
            
        Returns:
            ExtractionResult with extracted content
        """
        pass
    
    def supports_format(self, file_path: str) -> bool:
        """
        Check if this extractor supports the given file format.
        
        Args:
            file_path: Path to file
            
        Returns:
            True if supported, False otherwise
        """
        return False


class ExtractorRegistry:
    """
    Registry for extractors.
    
    Allows registration and retrieval of extractors by name.
    """
    
    _registry: Dict[str, type] = {}
    _auto_detectors: List[type] = []
    
    @classmethod
    def register(cls, name: str, extractor_class: type, auto_detect: bool = False) -> None:
        """
        Register an extractor.
        
        Args:
            name: Extractor name (e.g., "pdf_v1", "docx_v1")
            extractor_class: Extractor class (subclass of BaseExtractor)
            auto_detect: If True, include in AUTO detection chain
        """
        if not issubclass(extractor_class, BaseExtractor):
            raise TypeError(f"Extractor must subclass BaseExtractor: {extractor_class}")
        
        cls._registry[name] = extractor_class
        if auto_detect:
            cls._auto_detectors.append(extractor_class)
        
        logger.info(f"Registered extractor: {name} (auto_detect={auto_detect})")
    
    @classmethod
    def get(cls, name: str) -> Optional[type]:
        """
        Get extractor class by name.
        
        Args:
            name: Extractor name
            
        Returns:
            Extractor class or None if not found
        """
        return cls._registry.get(name)
    
    @classmethod
    def create(cls, name: str, config: "ExtractionConfig") -> Optional[BaseExtractor]:
        """
        Create an extractor instance.
        
        Args:
            name: Extractor name
            config: Extraction configuration
            
        Returns:
            Extractor instance or None if not found
        """
        extractor_class = cls.get(name)
        if extractor_class:
            return extractor_class(config)
        return None
    
    @classmethod
    def auto_detect(cls, file_path: str, config: "ExtractionConfig") -> Optional[BaseExtractor]:
        """
        Auto-detect and create appropriate extractor.
        
        Args:
            file_path: Path to file
            config: Extraction configuration
            
        Returns:
            Extractor instance or None if no suitable extractor found
        """
        # Try auto-detectors in order
        for extractor_class in cls._auto_detectors:
            extractor = extractor_class(config)
            if extractor.supports_format(file_path):
                logger.info(f"Auto-detected extractor: {extractor_class.__name__}")
                return extractor
        
        # Fallback: try by file extension
        ext = Path(file_path).suffix.lower()
        ext_map = {
            ".pdf": "pdf_v1",
            ".docx": "docx_v1",
            ".pptx": "pptx_v1",
            ".csv": "csv_v1",
            ".txt": "txt_v1",
            ".html": "html_v1",
            ".htm": "html_v1",
            ".png": "image_v1",
            ".jpg": "image_v1",
            ".jpeg": "image_v1",
            ".mp3": "audio_v1",
            ".wav": "audio_v1",
            ".mp4": "video_v1",
            ".avi": "video_v1",
        }
        
        extractor_name = ext_map.get(ext)
        if extractor_name:
            return cls.create(extractor_name, config)
        
        logger.warning(f"No extractor found for file: {file_path}")
        return None
    
    @classmethod
    def list_registered(cls) -> List[str]:
        """List all registered extractor names."""
        return list(cls._registry.keys())
