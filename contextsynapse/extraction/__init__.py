"""
AIQL Extraction & Normalization Subsystem (Stage-1)

A comprehensive extraction engine that:
- Ingests input files (PDF, DOCX, PPTX, CSV, TXT, HTML, images, audio, video)
- Runs pluggable extractors
- Produces universal normalized JSON with hierarchical structure
- Stores extracted assets + manifest
- Scales with Ray for high-load parsing
- Extensible for new extraction libraries and modalities
"""

__version__ = "1.0.0"
__author__ = "AIContextDB Team"

from .config import ExtractionConfig, ExtractionMode
from .registry import ExtractorRegistry, BaseExtractor, ExtractionResult
from .extract_engine import run_extraction, ExtractionEngine
from .normalizer import Normalizer, NormalizedDocument
from .hierarchy import HierarchyBuilder
from .file_manager import FileManager, get_document_workspace
from .version_manager import VersionManager, compute_file_hash
from .ray_runner import extract_many_documents_with_ray, remote_extract_document
from .normalized_store import NormalizedDocumentStore
from .id_generator import IDGenerator, DeduplicationManager

# Register extractors
from .extractors import register_extractors

__all__ = [
    # Config
    "ExtractionConfig",
    "ExtractionMode",
    
    # Registry
    "ExtractorRegistry",
    "BaseExtractor",
    "ExtractionResult",
    
    # Engine
    "run_extraction",
    "ExtractionEngine",
    
    # Normalization
    "Normalizer",
    "NormalizedDocument",
    
    # Hierarchy
    "HierarchyBuilder",
    
    # File Management
    "FileManager",
    "get_document_workspace",
    
    # Versioning
    "VersionManager",
    "compute_file_hash",
    
    # Ray
    "extract_many_documents_with_ray",
    "remote_extract_document",
    
    # Storage
    "NormalizedDocumentStore",
    
    # ID Generation & Deduplication
    "IDGenerator",
    "DeduplicationManager",
]
